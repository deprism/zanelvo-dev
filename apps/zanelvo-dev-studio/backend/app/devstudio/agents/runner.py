"""Shared LLM-call plumbing for every role agent: primary -> fallback, AgentRun + LLMInvocation
bookkeeping, and best-effort JSON parsing for structured steps. This is the one place agent code
touches ModelRegistry/AgentConfiguration, so role modules stay short and declarative.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ...db import get_db
from ..models import AgentConfiguration, AgentRole
from ..providers.base import LLMResult, ProviderNotConfigured, ProviderNotImplemented
from ..providers.registry import auto_attempts, ModelRegistry
from ..services import activity_service, provider_health, usage_tracker


class AgentStepFailed(Exception):
    def __init__(self, message: str, classification: str = "error"):
        super().__init__(message)
        self.classification = classification  # "requires_credentials" | "not_implemented" | "error"


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", re.DOTALL)


def extract_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = _JSON_FENCE.search(raw)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    first, last = raw.find("{"), raw.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(raw[first:last + 1])
        except json.JSONDecodeError:
            pass
    first, last = raw.find("["), raw.rfind("]")
    if first != -1 and last > first:
        try:
            return json.loads(raw[first:last + 1])
        except json.JSONDecodeError:
            pass
    raise AgentStepFailed("Model did not return parseable JSON")


async def _start_run(task_id: str, role: AgentRole, provider: str, model: str, action: str,
                      plan_item_id: Optional[str] = None) -> str:
    doc = {"task_id": task_id, "plan_item_id": plan_item_id, "role": role, "attempt": 1,
            "provider": provider, "model": model, "status": "running", "action": action,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()}
    res = await get_db().ds_agent_runs.insert_one(doc)
    await activity_service.emit(task_id, "agent_started",
                                  {"role": role, "provider": provider, "model": model, "action": action})
    return str(res.inserted_id)


async def _finish_run(run_id: str, task_id: str, role: AgentRole, status: str, summary: str,
                       duration_ms: int) -> None:
    await get_db().ds_agent_runs.update_one({"_id": __oid(run_id)}, {"$set": {
        "status": status, "result_summary": summary[:500], "duration_ms": duration_ms,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }})
    await activity_service.emit(task_id, "agent_finished",
                                  {"role": role, "status": status, "summary": summary[:300]})


def __oid(id_str: str):
    from bson import ObjectId
    return ObjectId(id_str)


def _attempts_for(config: AgentConfiguration, registry: ModelRegistry) -> List[Tuple[str, str]]:
    """The ordered (provider, model) attempts a call should try, for either mode:

    - auto_provider=True: cascade through every provider that actually has a real credential
      configured (see providers/registry.py::auto_attempts) — no provider/model ever needs to be
      picked by hand; a role stays runnable as long as ANY key is set.
    - auto_provider=False (default): the existing explicit primary -> fallback behavior, unchanged.
    """
    if config.auto_provider:
        attempts = auto_attempts(config.role, registry)
        if not attempts:
            raise AgentStepFailed(
                f"{config.role}: automatic provider selection is on, but no provider has a "
                "credential configured at all — set at least one API key in Settings > Secrets.",
                classification="requires_credentials")
        return attempts
    attempts = [(config.primary_provider, config.primary_model)]
    if config.automatic_fallback and config.fallback_provider and config.fallback_model:
        attempts.append((config.fallback_provider, config.fallback_model))
    return attempts


async def call_structured(registry: ModelRegistry, config: AgentConfiguration, task_id: str,
                           role: AgentRole, system: str, prompt: str, action: str,
                           plan_item_id: Optional[str] = None,
                           max_tokens: int = 4096,
                           images_b64: Optional[list] = None) -> Dict[str, Any]:
    """Runs the primary model; on ANY failure (including not-configured), falls back once if
    automatic_fallback is set. Every attempt is logged; the final failure raises AgentStepFailed
    with a classification the Supervisor / capability report can use.

    If `images_b64` is supplied (task attachments flagged for vision) the call is sent as a
    vision request to any attempt whose model supports vision; models that don't get the normal
    text-only structured call, so a non-vision fallback still runs rather than hard-failing.

    If this role has any MCP servers or built-in tools configured (AgentConfiguration.mcp_servers /
    .tools_enabled) — and no images_b64, since combining vision with a tool loop isn't supported —
    this transparently delegates to call_with_tools() instead. Every existing call site (roles.py)
    is unchanged either way; tool access is purely a per-role Settings/Agents-panel toggle."""
    if not images_b64 and (config.mcp_servers or config.tools_enabled):
        tools = await _tools_for_config(config)
        if tools:
            return await call_with_tools(registry, config, task_id, role, system, prompt, action,
                                          tools, plan_item_id=plan_item_id, max_tokens=max_tokens)
    attempts = _attempts_for(config, registry)

    last_err: Optional[Exception] = None
    classification = "error"
    for provider_name, model in attempts:
        run_id = await _start_run(task_id, role, provider_name, model, action, plan_item_id)
        t0 = time.monotonic()
        try:
            provider = registry.get(provider_name)
            if images_b64 and provider.supports_vision(model):
                strict = (system + "\n\nRespond with ONLY a single valid JSON object/array. No "
                          "prose, no markdown code fences.")
                result: LLMResult = await provider.generate_with_vision(
                    system=strict, prompt=prompt, model=model, images_b64=images_b64,
                    max_tokens=max_tokens)
                kind = "generate_with_vision"
                await activity_service.emit(task_id, "vision_used",
                                            {"role": role, "provider": provider_name,
                                             "model": model, "image_count": len(images_b64)})
            else:
                result = await provider.generate_structured(
                    system=system, prompt=prompt, model=model, max_tokens=max_tokens)
                kind = "generate_structured"
            duration_ms = int((time.monotonic() - t0) * 1000)
            await usage_tracker.record_invocation(task_id, role, result, agent_run_id=run_id,
                                                    kind=kind)
            await provider_health.record(provider_name, "ok")
            parsed = extract_json(result.text)
            _summary = parsed.get("summary") if isinstance(parsed, dict) else None
            await _finish_run(run_id, task_id, role, "succeeded", str(_summary or "ok"), duration_ms)
            return parsed
        except ProviderNotConfigured as e:
            classification = "requires_credentials"
            last_err = e
            await provider_health.record_exception(provider_name, e)
            await _finish_run(run_id, task_id, role, "failed", str(e), int((time.monotonic() - t0) * 1000))
        except ProviderNotImplemented as e:
            classification = "not_implemented"
            last_err = e
            await _finish_run(run_id, task_id, role, "failed", str(e), int((time.monotonic() - t0) * 1000))
        except Exception as e:  # noqa: BLE001
            classification = "error"
            last_err = e
            await provider_health.record_exception(provider_name, e)
            await _finish_run(run_id, task_id, role, "failed", f"{type(e).__name__}: {e}",
                                int((time.monotonic() - t0) * 1000))
    raise AgentStepFailed(f"{role} step '{action}' failed on all configured models: {last_err}",
                           classification=classification)


# --- Tool-calling loop (MCP + built-in tools) -----------------------------------------------

class ToolExecutionError(Exception):
    """A tool ran but failed. Caught by _execute_tool_call and reported back to the model as a
    tool_result, never raised up to crash the agent step — the model gets to see and adapt to
    the failure, same as a human using a broken tool would."""


BUILTIN_TOOLS: Dict[str, Dict[str, Any]] = {
    "ask_human": {
        "name": "ask_human",
        "description": "Pause this task and ask the founder a question. Use only when truly "
                        "blocked — an ambiguous requirement, a destructive/irreversible choice, "
                        "or missing credentials only they can provide. The task pauses (status "
                        "BLOCKED) until they reply in the chat; you will not get an answer in "
                        "this same turn, so end your response right after calling this.",
        "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}},
                          "required": ["question"]},
    },
    "finish": {
        "name": "finish",
        "description": "Signal that this step is genuinely complete and its acceptance criteria "
                        "are met. Call this instead of just stopping — it's how the orchestrator "
                        "tells a deliberate finish apart from a stall.",
        "inputSchema": {"type": "object", "properties": {"summary": {"type": "string"}},
                          "required": ["summary"]},
    },
    "web_search": {
        "name": "web_search",
        "description": "Search the public web for current information. When this role's primary "
                        "model is Anthropic, this routes to Claude's own server-hosted web search "
                        "directly. For any other provider whose tool-calling loop is supported "
                        "(currently Emergent), it uses Gemini's native Google Search grounding if "
                        "a Gemini API key is configured (free tier, and already one of this app's "
                        "model-provider keys), otherwise a configured Perplexity API key — never "
                        "requires an Anthropic key specifically, and never requires a search-only "
                        "credential when a free/AI-provider key is already set.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}},
                          "required": ["query"]},
    },
    "screenshot": {
        "name": "screenshot",
        "description": "Take a real screenshot of a URL using the same headless-browser (Playwright) "
                        "infrastructure already used for browser QA.",
        "inputSchema": {"type": "object", "properties": {
            "url": {"type": "string"}, "label": {"type": "string"}}, "required": ["url"]},
    },
    "perplexity_research": {
        "name": "perplexity_research",
        "description": "Ask Perplexity's research API a question requiring deep, cited web "
                        "research (competitor/market/pricing/standards). Requires a configured "
                        "Perplexity API key — fails with a clear message if absent, never fakes "
                        "an answer.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}},
                          "required": ["query"]},
    },
    "get_assets": {
        "name": "get_assets",
        "description": "NOT IMPLEMENTED. No real stock-image/asset provider is configured for "
                        "this app — calling this always returns an explanatory error rather than "
                        "a fabricated image or URL.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}},
                          "required": ["query"]},
    },
    "view_file": {
        "name": "view_file",
        "description": "Read a real file from this task's workspace, mid-reasoning — e.g. to "
                        "check something before deciding what to change, without waiting for the "
                        "next structured implementation step. Path is relative to the repo root.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                          "required": ["path"]},
    },
    "search_files": {
        "name": "search_files",
        "description": "Search this task's workspace for a literal/regex text match across real "
                        "files (like grep), optionally scoped by a glob (e.g. '*.py'). Returns "
                        "matching file paths with the matching lines.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"}, "glob": {"type": "string"}}, "required": ["query"]},
    },
    "execute_bash": {
        "name": "execute_bash",
        "description": "Run a real, allow-listed shell command inside this task's workspace (e.g. "
                        "npm test, git status, mvn -B package) and see its actual output — the "
                        "same allowlist/deny-list every other command in this app goes through "
                        "(services/command_policy.py). A command not on the allowlist, or matching "
                        "a destructive pattern, is refused with the reason rather than run.",
        "inputSchema": {"type": "object", "properties": {
            "command": {"type": "string"}, "cwd_subdir": {"type": "string"}},
            "required": ["command"]},
    },
    "view_logs": {
        "name": "view_logs",
        "description": "See this task's own real recent activity: agent runs (role, provider/"
                        "model, status, action) and repeated-failure fingerprints (from the "
                        "anti-loop engine) — useful for checking what's already been tried before "
                        "proposing another approach, instead of repeating it blind.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []},
    },
    "deployment_debugger": {
        "name": "deployment_debugger",
        "description": "Real GitHub Actions status for this project's connected repo (requires a "
                        "configured GitHub token). Omit run_id to list recent workflow runs; pass "
                        "one to see that run's jobs and each job's step-by-step status/conclusion "
                        "— usually enough to see exactly which step broke a CI/deployment run. "
                        "Does not fetch raw log text (GitHub serves that as a binary download, out "
                        "of scope here) — step status is what's actually returned.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "integer"}}, "required": []},
    },
    "analyze_image": {
        "name": "analyze_image",
        "description": "Real vision-model analysis of an actual screenshot already captured for "
                        "this task (via the screenshot tool or browser QA) — e.g. 'does the button "
                        "look centered?' or 'describe what's visually broken here'. Requires this "
                        "role's own model to support vision; fails with a clear reason if it "
                        "doesn't rather than guessing from the filename.",
        "inputSchema": {"type": "object", "properties": {
            "question": {"type": "string"}, "screenshot_id": {"type": "string"}},
            "required": ["question"]},
    },
}


async def _run_builtin_tool(name: str, arguments: Dict[str, Any], *, task_id: str, role: AgentRole,
                              registry: Optional[ModelRegistry] = None,
                              config: Optional[AgentConfiguration] = None) -> str:
    if name == "ask_human":
        from ..services import task_manager
        question = str(arguments.get("question", "")).strip() or "The agent needs your input."
        await task_manager.set_status(task_id, "BLOCKED", note=f"{role} is asking: {question}")
        await task_manager.update_task(task_id, blocked_reason=question)
        return ("Task paused; the founder has been asked and will reply via chat. You will not "
                "receive their answer in this turn — stop here.")
    if name == "finish":
        return f"Acknowledged: {arguments.get('summary', 'done')}"
    if name == "web_search":
        # Real Anthropic-hosted search is wired directly into AnthropicProvider.generate_with_tools
        # (mapped to Claude's own server-hosted search tool there, resolved within that one API
        # call — never routed through this executor) when the calling role's model is Anthropic.
        # This executor is only ever reached for a NON-Anthropic provider (Emergent is the only
        # other one that implements the tool-calling loop today). Backend preference order here is
        # deliberate: Gemini's native Google Search grounding first (free tier, and already one of
        # this app's model-provider keys — never a search-only credential), Perplexity only as a
        # secondary option for founders who already pay for it.
        from ..services import gemini_search_service, perplexity_service, settings_service
        query = str(arguments.get("query", ""))
        gemini_key = await settings_service.get_secret("gemini_api_key")
        if gemini_key:
            try:
                return await gemini_search_service.research(query, gemini_key)
            except gemini_search_service.GeminiSearchNotConfigured:
                pass  # fall through to Perplexity below rather than fail outright
        perplexity_key = await settings_service.get_secret("perplexity_api_key")
        if perplexity_key:
            return await perplexity_service.research(query, perplexity_key)
        raise ToolExecutionError(
            "web_search has no search backend available for this role's provider: it isn't "
            "Anthropic (server-hosted search), no Gemini API key is configured (free tier — "
            "the recommended option, ai.google.dev), and no Perplexity API key is configured "
            "either. Add one in Settings > Secrets (Tools group), or configure a real search "
            "MCP server instead."
        )
    if name == "screenshot":
        from ..services import browser_service
        url = str(arguments.get("url", "")).strip()
        if not url:
            raise ToolExecutionError("screenshot requires a 'url'")
        label = str(arguments.get("label") or "agent_screenshot")
        run = await browser_service.run_scenario(task_id, url, label)
        if run.status == "unavailable":
            raise ToolExecutionError(run.notes or "Browser automation is unavailable.")
        return f"Screenshot captured (browser run {run.id}, status={run.status})."
    if name == "perplexity_research":
        from ..services import perplexity_service, settings_service
        api_key = await settings_service.get_secret("perplexity_api_key")
        return await perplexity_service.research(str(arguments.get("query", "")), api_key)
    if name == "get_assets":
        raise ToolExecutionError(
            "get_assets has no real asset provider configured in this build — not implemented "
            "rather than faked. Wire a real image/stock-asset API here if you need this."
        )
    if name == "view_file":
        from ..services.file_service import FileService, PathEscapeError
        path = str(arguments.get("path", "")).strip()
        if not path:
            raise ToolExecutionError("view_file requires a 'path'")
        workspace_path = await _workspace_path_for_task(task_id)
        try:
            return FileService(workspace_path).read_file(path)
        except FileNotFoundError as e:
            raise ToolExecutionError(f"File not found: {e}") from e
        except PathEscapeError as e:
            raise ToolExecutionError(str(e)) from e
    if name == "search_files":
        from ..services.file_service import FileService
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ToolExecutionError("search_files requires a 'query'")
        workspace_path = await _workspace_path_for_task(task_id)
        results = FileService(workspace_path).search_repo(query, glob=arguments.get("glob"))
        return json.dumps(results) if results else "No matches found."
    if name == "execute_bash":
        from ..services import execution_service
        command = str(arguments.get("command", "")).strip()
        if not command:
            raise ToolExecutionError("execute_bash requires a 'command'")
        workspace_path = await _workspace_path_for_task(task_id)
        try:
            run = await execution_service.run_command(
                workspace_path, task_id, command, "agent_tool",
                cwd_subdir=arguments.get("cwd_subdir"))
        except execution_service.CommandBlocked as e:
            raise ToolExecutionError(str(e)) from e
        return f"exit_code={run.exit_code}\nstdout:\n{run.stdout_tail}\nstderr:\n{run.stderr_tail}"
    if name == "view_logs":
        from ..services import anti_loop
        limit = int(arguments.get("limit") or 20)
        runs = [r async for r in get_db().ds_agent_runs.find({"task_id": task_id})
                 .sort("created_at", -1).limit(limit)]
        failures = await anti_loop.failure_history(task_id, limit=limit)
        lines = ["Recent agent runs (most recent first):"]
        for r in runs:
            summary = f" — {r['result_summary']}" if r.get("result_summary") else ""
            lines.append(f"- {r.get('role')} via {r.get('provider')}/{r.get('model')}: "
                          f"{r.get('status')} ({r.get('action', '')}){summary}")
        if not runs:
            lines.append("(none yet)")
        if failures:
            lines.append("\nRepeated-failure fingerprints:")
            for f in failures:
                lines.append(f"- [{f['failure_class']}] {f['command']}: {f['occurrences']}x — "
                              f"{f['normalized_error'][:200]}")
        return "\n".join(lines)
    if name == "deployment_debugger":
        from ..services import github_provider
        project = await _project_for_task(task_id)
        run_id = arguments.get("run_id")
        try:
            if run_id:
                jobs = await github_provider.get_workflow_run_jobs(
                    project.github_owner, project.github_repo, int(run_id))
                if not jobs:
                    return f"No jobs found for run {run_id}."
                lines = [f"Run {run_id} — jobs and step status:"]
                for j in jobs:
                    lines.append(f"- {j['name']}: {j['status']}/{j['conclusion']}")
                    for s in j["steps"]:
                        lines.append(f"    · {s['name']}: {s['status']}/{s['conclusion']}")
                return "\n".join(lines)
            runs = await github_provider.list_workflow_runs(project.github_owner, project.github_repo)
            if not runs:
                return "No GitHub Actions workflow runs found for this repository."
            lines = ["Recent workflow runs:"]
            for r in runs:
                lines.append(f"- run {r['id']}: {r['name']} ({r['head_branch']}@{r['head_sha']}) "
                              f"— {r['status']}/{r['conclusion']} — {r['html_url']}")
            return "\n".join(lines)
        except github_provider.GitHubError as e:
            raise ToolExecutionError(str(e)) from e
    if name == "analyze_image":
        from ..services import browser_service
        question = str(arguments.get("question", "")).strip()
        if not question:
            raise ToolExecutionError("analyze_image requires a 'question'")
        if registry is None or config is None:
            raise ToolExecutionError("analyze_image isn't available in this call context.")
        screenshots = await browser_service.list_screenshots(task_id)
        if not screenshots:
            raise ToolExecutionError("No screenshots exist for this task yet — call the screenshot "
                                       "tool first.")
        provider = registry.get(config.primary_provider)
        if not provider.supports_vision(config.primary_model):
            raise ToolExecutionError(
                f"{config.primary_provider}/{config.primary_model} does not support vision.")
        screenshot_id = arguments.get("screenshot_id")
        shot = next((s for s in screenshots if s.id == screenshot_id), screenshots[0]) \
            if screenshot_id else screenshots[0]
        if not os.path.isfile(shot.path):
            raise ToolExecutionError(f"Screenshot file missing on disk: {shot.path}")
        with open(shot.path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
        result = await provider.generate_with_vision(
            system="You are analyzing a real screenshot captured by this app's own browser QA. "
                    "Describe only what you can actually see — never guess at content outside the "
                    "image.",
            prompt=question, model=config.primary_model, images_b64=[image_b64])
        return result.text
    raise ToolExecutionError(f"Unknown built-in tool '{name}'")


async def _project_for_task(task_id: str):
    from ..services import repository_service, task_manager
    task = await task_manager.get_task(task_id)
    if not task:
        raise ToolExecutionError("Task not found.")
    project = await repository_service.get_project(task.project_id)
    if not project:
        raise ToolExecutionError("Project not found for this task.")
    return project


async def _workspace_path_for_task(task_id: str) -> str:
    from ..services import workspace_manager
    ws = await workspace_manager.get_workspace_for_task(task_id)
    if not ws:
        raise ToolExecutionError("This task has no workspace yet — the repository hasn't been provisioned.")
    return ws.local_path


async def _tools_for_config(config: AgentConfiguration) -> List[Dict[str, Any]]:
    from ..services import mcp_service
    tools = await mcp_service.tools_for_servers(config.mcp_servers)
    for name in config.tools_enabled:
        if name in BUILTIN_TOOLS:
            tools.append(BUILTIN_TOOLS[name])
    return tools


async def _execute_tool_call(call: Dict[str, Any], tools_by_name: Dict[str, Dict[str, Any]], *,
                              task_id: str, role: AgentRole,
                              registry: Optional[ModelRegistry] = None,
                              config: Optional[AgentConfiguration] = None) -> Dict[str, Any]:
    name = call["name"]
    tool = tools_by_name.get(name)
    try:
        if tool and "_mcp_server" in tool:
            from ..services import mcp_service
            server = await mcp_service.get_server_by_name(tool["_mcp_server"])
            if not server:
                raise ToolExecutionError(f"MCP server '{tool['_mcp_server']}' is no longer configured")
            content = await mcp_service.call_tool(server, name, call.get("arguments") or {})
        elif name in BUILTIN_TOOLS:
            content = await _run_builtin_tool(name, call.get("arguments") or {}, task_id=task_id, role=role,
                                                registry=registry, config=config)
        else:
            content = f"ERROR: unknown tool '{name}'"
        return {"id": call["id"], "content": content}
    except Exception as e:  # noqa: BLE001 — a failed tool call becomes tool_result content for the
        # model to see and adapt to, not a crash of the whole agent step (see ToolExecutionError).
        return {"id": call["id"], "content": f"ERROR: {type(e).__name__}: {e}"}


async def call_with_tools(registry: ModelRegistry, config: AgentConfiguration, task_id: str,
                           role: AgentRole, system: str, prompt: str, action: str,
                           tools: List[Dict[str, Any]], plan_item_id: Optional[str] = None,
                           max_tokens: int = 4096, max_iterations: int = 6) -> Dict[str, Any]:
    """Like call_structured, but drives a bounded tool-calling loop: the model can call any of
    `tools` (MCP-sourced + built-in), see the results, and call more, before returning its final
    JSON answer. Same primary -> fallback behavior as call_structured; a provider that doesn't
    implement generate_with_tools() raises ProviderNotConfigured from the base class default,
    which IS the fallback trigger — a role misconfigured onto a non-tool-capable model degrades
    the same way an unconfigured-credentials one does, never hangs."""
    attempts = _attempts_for(config, registry)
    tools_by_name = {t["name"]: t for t in tools}
    # A tool-enabled role still owes the orchestrator a single structured JSON answer at the end.
    # Left to its own devices a model often narrates ("I used web_search and found…") instead of
    # emitting JSON, so extract_json fails and the whole step errors — exactly what enabling tools
    # on every role at MAX_QUALITY surfaced on the read-only Analyst. Make the contract explicit.
    strict_system = system + (
        "\n\nIMPORTANT: Use tools as needed, but your FINAL message (once you stop calling tools) "
        "must be ONLY the single valid JSON object/array specified above — no prose, no markdown "
        "code fences, nothing before or after it.")

    last_err: Optional[Exception] = None
    classification = "error"
    for provider_name, model in attempts:
        run_id = await _start_run(task_id, role, provider_name, model, action, plan_item_id)
        t0 = time.monotonic()
        try:
            provider = registry.get(provider_name)
            history: Optional[Any] = None
            tool_results: Optional[List[Dict[str, Any]]] = None
            cur_prompt: Optional[str] = prompt
            result: Optional[LLMResult] = None
            for _ in range(max_iterations):
                result = await provider.generate_with_tools(
                    system=strict_system, model=model, tools=tools, prompt=cur_prompt,
                    history=history, tool_results=tool_results, max_tokens=max_tokens)
                await usage_tracker.record_invocation(task_id, role, result, agent_run_id=run_id,
                                                        kind="generate_structured")
                if not result.tool_calls:
                    break
                cur_prompt = None
                history = result.tool_loop_history
                executed = []
                for call in result.tool_calls:
                    call_result = await _execute_tool_call(call, tools_by_name, task_id=task_id, role=role,
                                                              registry=registry, config=config)
                    executed.append(call_result)
                    args = call.get("arguments") or {}
                    await activity_service.emit(task_id, "tool_call", {
                        "role": role, "tool": call["name"],
                        "server": (tools_by_name.get(call["name"]) or {}).get("_mcp_server"),
                        "args": {k: str(v)[:200] for k, v in args.items()} if isinstance(args, dict) else {},
                        "result": (str(call_result.get("content") or ""))[:600],
                    })
                tool_results = executed
            else:
                # Hit the tool-call budget without the model volunteering a final answer. Rather
                # than fail the whole step, force a closing no-tool structured call so it still
                # produces the required JSON instead of looping forever (this is what left QA/
                # testing items — which love to keep calling screenshot/execute_bash — stuck).
                result = await provider.generate_structured(
                    system=strict_system,
                    prompt=("You have used enough tools. Now output ONLY the required JSON "
                            "object/array specified above, based on what you have gathered so far."),
                    model=model, max_tokens=max_tokens)
                await usage_tracker.record_invocation(task_id, role, result, agent_run_id=run_id,
                                                        kind="generate_structured")
            duration_ms = int((time.monotonic() - t0) * 1000)
            await provider_health.record(provider_name, "ok")
            try:
                parsed = extract_json(result.text) if result is not None else {}
            except AgentStepFailed:
                # Tool-capable models sometimes end the loop with prose instead of the required
                # JSON. Rather than fail the whole step, recover with one plain (no-tool) structured
                # call that reformats their own final answer into the JSON contract.
                recovery = await provider.generate_structured(
                    system=strict_system,
                    prompt=("Your previous answer was not valid JSON. Reply with ONLY the required "
                            "JSON object/array specified above and nothing else. Previous answer:\n\n"
                            + ((result.text if result is not None else "") or "")),
                    model=model, max_tokens=max_tokens)
                await usage_tracker.record_invocation(task_id, role, recovery, agent_run_id=run_id,
                                                        kind="generate_structured")
                parsed = extract_json(recovery.text)
            _summary = parsed.get("summary") if isinstance(parsed, dict) else None
            await _finish_run(run_id, task_id, role, "succeeded", str(_summary or "ok"), duration_ms)
            return parsed
        except ProviderNotConfigured as e:
            classification = "requires_credentials"
            last_err = e
            await provider_health.record_exception(provider_name, e)
            await _finish_run(run_id, task_id, role, "failed", str(e), int((time.monotonic() - t0) * 1000))
        except ProviderNotImplemented as e:
            classification = "not_implemented"
            last_err = e
            await _finish_run(run_id, task_id, role, "failed", str(e), int((time.monotonic() - t0) * 1000))
        except AgentStepFailed as e:
            classification = e.classification
            last_err = e
            await _finish_run(run_id, task_id, role, "failed", str(e), int((time.monotonic() - t0) * 1000))
        except Exception as e:  # noqa: BLE001
            classification = "error"
            last_err = e
            await provider_health.record_exception(provider_name, e)
            await _finish_run(run_id, task_id, role, "failed", f"{type(e).__name__}: {e}",
                                int((time.monotonic() - t0) * 1000))
    raise AgentStepFailed(f"{role} step '{action}' failed on all configured models: {last_err}",
                           classification=classification)
