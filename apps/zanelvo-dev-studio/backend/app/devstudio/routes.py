"""Zanelvo Dev Studio API — mounted at /api/devstudio. Every route requires the single admin
session (`require_devstudio_access` / `require_devstudio_write`, both backed by `app.deps.require_auth`
— see CLAUDE.md for why this app has no roles or accounts system)."""
from __future__ import annotations

import os
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.responses import Response, StreamingResponse

from .agents import git_agent, orchestrator, qa_agent, runner
from .agents import registry as agent_registry
from .models import (AgentRole, CreateProjectRequest, CreateRepoAndProjectRequest, CreateTaskRequest,
                       MemoryCategory, ModelPreset, TaskMessageRequest, TaskUpdateRequest)
from .providers import registry as provider_registry
from .security import require_devstudio_access, require_devstudio_write
from .services import (activity_service, browser_service, checkpoint_service, custom_agent_service,
                         execution_service, github_provider, indexer, mcp_service, memory_service,
                         preview_service, provider_health, repository_service, settings_service,
                         task_manager, upload_service, usage_tracker, workspace_manager)
from .services.diff_service import get_diff_summary
from .services.file_service import FileService

router = APIRouter(prefix="/api/devstudio", tags=["devstudio"])


async def _require_project(project_id: str):
    project = await repository_service.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


async def _require_task(task_id: str):
    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


async def _require_workspace(task_id: str):
    ws = await workspace_manager.get_workspace_for_task(task_id)
    if not ws:
        raise HTTPException(400, "Task has no workspace yet — run it first")
    return ws


# --- Capabilities (Phase 0) --------------------------------------------------------------

@router.get("/capabilities")
async def capabilities(user: str = Depends(require_devstudio_access)):
    import shutil as _shutil
    out = []
    out.append({"name": "git_cli", "available": bool(_shutil.which("git")), "detail": None})
    workspaces_root = workspace_manager.WORKSPACES_ROOT
    try:
        os.makedirs(workspaces_root, exist_ok=True)
        testfile = os.path.join(workspaces_root, ".write_test")
        with open(testfile, "w") as fh:
            fh.write("ok")
        os.remove(testfile)
        out.append({"name": "writable_workspace", "available": True, "detail": workspaces_root})
    except OSError as e:
        out.append({"name": "writable_workspace", "available": False, "detail": str(e)})
    out.append({"name": "child_processes", "available": True, "detail": "asyncio.create_subprocess_exec"})
    try:
        import playwright  # noqa: F401
        out.append({"name": "playwright", "available": True, "detail": None})
    except ImportError:
        out.append({"name": "playwright", "available": False,
                     "detail": "pip install -r requirements-devstudio.txt"})
    gh = await github_provider.connectivity_check()
    out.append({"name": "github", "available": gh["available"], "detail": gh["detail"]})
    try:
        from ..db import get_db
        await get_db().command("ping")
        out.append({"name": "database", "available": True, "detail": None})
    except Exception as e:  # noqa: BLE001
        out.append({"name": "database", "available": False, "detail": str(e)})
    out.append({"name": "sse", "available": True, "detail": "EventSourceResponse"})
    secrets = await settings_service.secrets_status()
    out.append({"name": "anthropic_provider", "available": secrets["anthropic_api_key"],
                 "detail": None if secrets["anthropic_api_key"] else "ANTHROPIC_API_KEY not configured"})
    out.append({"name": "openai_provider", "available": secrets["openai_api_key"],
                 "detail": None if secrets["openai_api_key"] else "OPENAI_API_KEY not configured"})
    out.append({"name": "gemini_provider", "available": secrets["gemini_api_key"],
                 "detail": None if secrets["gemini_api_key"] else "GEMINI_API_KEY not configured"})
    bedrock_ready = bool(secrets["aws_region"])  # key pair optional — default AWS chain can cover it
    out.append({"name": "bedrock_provider", "available": bedrock_ready,
                 "detail": None if bedrock_ready else "aws_region not configured (AWS_REGION also works)"})
    gemini_ent_ready = bool(secrets["gcp_project_id"])  # service account json optional — ADC can cover it
    out.append({"name": "gemini_enterprise_provider", "available": gemini_ent_ready,
                 "detail": None if gemini_ent_ready else "gcp_project_id not configured (GOOGLE_CLOUD_PROJECT also works)"})
    out.append({"name": "emergent_provider", "available": secrets["emergent_universal_key"],
                 "detail": None if secrets["emergent_universal_key"] else "EMERGENT_UNIVERSAL_KEY not configured"})
    return {"capabilities": out}


@router.get("/providers/models")
async def list_provider_models(user: str = Depends(require_devstudio_access)):
    """Full model catalog across every provider, grouped by provider — powers the per-role
    provider/model pickers in Settings > Agents. Static per-provider lists (no live credentials
    required to see them; `capabilities` above is what tells you which ones will actually run)."""
    from dataclasses import asdict

    registry = await settings_service.build_model_registry()
    by_provider: dict = {}
    for m in registry.list_all_models():
        by_provider.setdefault(m.provider, []).append(asdict(m))
    return {"providers": provider_registry.known_provider_names(), "models": by_provider}


class ProviderTestRequest(BaseModel):
    provider: str
    model: str


@router.post("/providers/test")
async def test_provider_model(body: ProviderTestRequest, user: str = Depends(require_devstudio_write)):
    """Make one real, minimal generate() call against a specific provider+model using whatever
    credentials are currently saved (never the unsaved contents of a settings form) — this is the
    only honest way to answer "does this actually work", per CLAUDE.md's evidence rules. Always
    returns 200 with a structured ok/false result (mirrors github/whoami's style) rather than an
    HTTP error, so the frontend can render every outcome — not configured, not implemented (the
    an unimplemented provider, if one exists), or a real failure (bad model id, auth error,
    network) — without exception
    handling on every call site."""
    import time

    from .providers.base import ProviderNotConfigured, ProviderNotImplemented

    registry = await settings_service.build_model_registry()
    if body.provider not in provider_registry.known_provider_names():
        raise HTTPException(404, f"Unknown provider: {body.provider}")

    t0 = time.monotonic()
    try:
        provider = registry.get(body.provider)
        result = await provider.generate(
            system="You are a connectivity test for a developer tool. Reply with exactly one word.",
            prompt="Reply with exactly the word: OK",
            model=body.model,
            max_tokens=8,
            temperature=0.0,
        )
        await provider_health.record(body.provider, "ok")
        return {
            "ok": True,
            "provider": body.provider,
            "model": body.model,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "response_text": (result.text or "").strip()[:200],
            "usage": {"input_tokens": result.usage.input_tokens, "output_tokens": result.usage.output_tokens},
        }
    except ProviderNotConfigured as e:
        await provider_health.record_exception(body.provider, e)
        return {"ok": False, "provider": body.provider, "model": body.model,
                 "error_type": "not_configured", "detail": str(e)}
    except ProviderNotImplemented as e:
        return {"ok": False, "provider": body.provider, "model": body.model,
                 "error_type": "not_implemented", "detail": str(e)}
    except Exception as e:  # noqa: BLE001 — a failed test call is a result to display, not a 500
        await provider_health.record_exception(body.provider, e)
        return {"ok": False, "provider": body.provider, "model": body.model,
                 "error_type": "error", "detail": str(e)[:500],
                 "latency_ms": int((time.monotonic() - t0) * 1000)}


@router.get("/providers/{provider}/health")
async def provider_health_status(provider: str, user: str = Depends(require_devstudio_access)):
    """Last-observed runtime status of a provider from REAL calls (used by a Universal Key
    balance-style banner). `configured` reflects whether a credential/key is currently set."""
    secret_map = {"emergent": "emergent_universal_key", "anthropic": "anthropic_api_key",
                  "openai": "openai_api_key", "gemini": "gemini_api_key"}
    configured = False
    if provider in secret_map:
        configured = bool(await settings_service.get_secret(secret_map[provider]))
    health = await provider_health.get(provider)
    return {"provider": provider, "configured": configured,
            "status": (health or {}).get("status"),
            "detail": (health or {}).get("detail", ""),
            "checked_at": (health or {}).get("checked_at")}


# --- Settings / secrets --------------------------------------------------------------------

class SecretBody(BaseModel):
    name: str
    value: str


class SettingsBody(BaseModel):
    model_preset: Optional[ModelPreset] = None
    default_orchestration_budget: Optional[int] = None
    preview_mode: Optional[str] = None


@router.get("/settings")
async def get_settings(user: str = Depends(require_devstudio_access)):
    settings = await settings_service.get_settings()
    secrets = await settings_service.secrets_status()
    return {"settings": settings.model_dump(), "secrets_configured": secrets}


@router.put("/settings")
async def update_settings(body: SettingsBody, user: str = Depends(require_devstudio_write)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    settings = await settings_service.update_settings(**fields)
    return settings.model_dump()


@router.post("/settings/secrets")
async def set_secret(body: SecretBody, user: str = Depends(require_devstudio_write)):
    await settings_service.set_secret(body.name, body.value)
    return {"ok": True}


# --- GitHub ------------------------------------------------------------------------------

@router.get("/github/whoami")
async def github_whoami(user: str = Depends(require_devstudio_access)):
    return await github_provider.connectivity_check()


@router.get("/github/repos")
async def github_repos(user: str = Depends(require_devstudio_access)):
    return {"repos": await github_provider.list_repos()}


@router.get("/github/{owner}/{repo}/branches")
async def github_branches(owner: str, repo: str, user: str = Depends(require_devstudio_access)):
    return {"branches": await github_provider.list_branches(owner, repo)}


# --- Projects ----------------------------------------------------------------------------

@router.post("/projects")
async def create_project(body: CreateProjectRequest, user: str = Depends(require_devstudio_write)):
    project = await repository_service.create_project(body)
    return project.model_dump()


@router.post("/projects/new-repo")
async def create_new_repo_and_project(body: CreateRepoAndProjectRequest,
                                        user: str = Depends(require_devstudio_write)):
    """Creates a brand-new GitHub repository (under the authenticated user's own account) and
    turns it into a Dev Studio Project in one step — no separate trip to github.com first."""
    project = await repository_service.create_new_repo_and_project(body)
    return project.model_dump()


@router.get("/projects")
async def list_projects(user: str = Depends(require_devstudio_access)):
    return {"projects": [p.model_dump() for p in await repository_service.list_projects()]}


@router.get("/projects/{project_id}")
async def get_project(project_id: str, user: str = Depends(require_devstudio_access)):
    return (await _require_project(project_id)).model_dump()


@router.get("/projects/{project_id}/branches")
async def project_branches(project_id: str, user: str = Depends(require_devstudio_access)):
    project = await _require_project(project_id)
    return {"branches": await repository_service.list_branches(project)}


@router.post("/projects/{project_id}/sync")
async def sync_project(project_id: str, branch: str, user: str = Depends(require_devstudio_write)):
    project = await _require_project(project_id)
    snapshot = await indexer.index_project_branch(project, branch)
    return snapshot.model_dump()


@router.get("/projects/{project_id}/tree")
async def project_tree(project_id: str, branch: str, subdir: str = "",
                        user: str = Depends(require_devstudio_access)):
    project = await _require_project(project_id)
    entries = await repository_service.browse_tree(project, branch, subdir)
    return {"entries": [e.__dict__ for e in entries]}


@router.get("/projects/{project_id}/file")
async def project_file(project_id: str, branch: str, path: str,
                        user: str = Depends(require_devstudio_access)):
    project = await _require_project(project_id)
    try:
        content = await repository_service.browse_read_file(project, branch, path)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e))
    return {"path": path, "content": content}


@router.get("/projects/{project_id}/search")
async def project_search(project_id: str, branch: str, q: str,
                          user: str = Depends(require_devstudio_access)):
    project = await _require_project(project_id)
    return {"results": await repository_service.browse_search(project, branch, q)}


# --- Project memory ------------------------------------------------------------------------

class MemoryBody(BaseModel):
    category: MemoryCategory
    title: str
    content: str
    branch: Optional[str] = None
    confidence: str = "medium"


@router.get("/projects/{project_id}/memory")
async def list_memory(project_id: str, category: Optional[str] = None,
                       user: str = Depends(require_devstudio_access)):
    items = await memory_service.list_memory(project_id, category=category)
    return {"memory": [m.model_dump() for m in items]}


@router.post("/projects/{project_id}/memory")
async def add_memory(project_id: str, body: MemoryBody, user: str = Depends(require_devstudio_write)):
    await _require_project(project_id)
    mem = await memory_service.add_memory(project_id, body.category, body.title, body.content,
                                            branch=body.branch, confidence=body.confidence)
    return mem.model_dump()


@router.patch("/memory/{memory_id}")
async def update_memory(memory_id: str, content: str = Body(..., embed=True),
                          reason: str = Body("founder edit", embed=True),
                          user: str = Depends(require_devstudio_write)):
    mem = await memory_service.update_memory(memory_id, content, reason)
    return mem.model_dump()


@router.post("/memory/{memory_id}/stale")
async def mark_memory_stale(memory_id: str, user: str = Depends(require_devstudio_write)):
    await memory_service.mark_stale(memory_id)
    return {"ok": True}


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: str, user: str = Depends(require_devstudio_write)):
    await memory_service.delete_memory(memory_id)
    return {"ok": True}


# --- Agent configuration -------------------------------------------------------------------

@router.get("/agents/config")
async def get_agent_configs(user: str = Depends(require_devstudio_access)):
    configs = await agent_registry.list_configs()
    return {"agents": {role: cfg.model_dump() for role, cfg in configs.items()}}


class AgentConfigBody(BaseModel):
    enabled: Optional[bool] = None
    auto_provider: Optional[bool] = None
    primary_provider: Optional[str] = None
    primary_model: Optional[str] = None
    fallback_provider: Optional[str] = None
    fallback_model: Optional[str] = None
    reasoning_level: Optional[str] = None
    max_attempts: Optional[int] = None
    automatic_fallback: Optional[bool] = None
    mcp_servers: Optional[List[str]] = None
    tools_enabled: Optional[List[str]] = None


@router.put("/agents/config/{role}")
async def update_agent_config(role: AgentRole, body: AgentConfigBody,
                                user: str = Depends(require_devstudio_write)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    cfg = await agent_registry.update_config(role, **fields)
    return cfg.model_dump()


@router.post("/agents/preset/{preset}")
async def apply_preset(preset: ModelPreset, user: str = Depends(require_devstudio_write)):
    configs = await agent_registry.apply_preset(preset)
    return {"agents": {role: cfg.model_dump() for role, cfg in configs.items()}}


@router.get("/tools/builtin")
async def list_builtin_tools(user: str = Depends(require_devstudio_access)):
    """The fixed built-in tool toggles (Ask Human, Finish, Web Search, Screenshot, Perplexity
    Research, Get Assets) any agent role can enable — see agents/runner.py::BUILTIN_TOOLS for
    what each one actually does (and which are genuinely implemented vs. documented-not-faked)."""
    return {"tools": [{"name": t["name"], "description": t["description"]}
                        for t in runner.BUILTIN_TOOLS.values()]}


# --- Custom agent roles ---------------------------------------------------------------------

class CustomAgentRoleBody(BaseModel):
    role: str
    label: str
    system_prompt: str
    category: str = "implementer"
    icon: Optional[str] = None


class CustomAgentRoleUpdateBody(BaseModel):
    label: Optional[str] = None
    system_prompt: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None


@router.get("/agents/roles")
async def list_agent_roles(user: str = Depends(require_devstudio_access)):
    from .models import BUILTIN_AGENT_ROLES

    custom = await custom_agent_service.list_roles()
    return {
        "builtin_roles": list(BUILTIN_AGENT_ROLES),
        "custom_roles": [r.model_dump() for r in custom],
    }


@router.post("/agents/roles")
async def create_agent_role(body: CustomAgentRoleBody, user: str = Depends(require_devstudio_write)):
    try:
        role = await custom_agent_service.create_role(**body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return role.model_dump()


@router.put("/agents/roles/{role}")
async def update_agent_role(role: str, body: CustomAgentRoleUpdateBody,
                              user: str = Depends(require_devstudio_write)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        updated = await custom_agent_service.update_role(role, **fields)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return updated.model_dump()


@router.delete("/agents/roles/{role}")
async def delete_agent_role(role: str, user: str = Depends(require_devstudio_write)):
    try:
        await custom_agent_service.delete_role(role)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


# --- MCP servers -----------------------------------------------------------------------------

class MCPServerBody(BaseModel):
    name: str
    transport: str = "stdio"
    command: Optional[str] = None
    args: List[str] = []
    url: Optional[str] = None
    env: dict = {}
    preset: Optional[str] = None
    enabled: bool = True


class MCPServerUpdateBody(BaseModel):
    name: Optional[str] = None
    transport: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    url: Optional[str] = None
    env: Optional[dict] = None
    enabled: Optional[bool] = None


@router.get("/mcp/presets")
async def list_mcp_presets(user: str = Depends(require_devstudio_access)):
    return {"presets": mcp_service.PRESETS}


@router.get("/mcp/servers")
async def list_mcp_servers(user: str = Depends(require_devstudio_access)):
    servers = await mcp_service.list_servers()
    # env values are never returned — only which keys are set — same "configured: bool" pattern
    # as every other secret in this app (see settings_service.secrets_status).
    out = []
    for s in servers:
        d = s.model_dump()
        d["env_keys"] = list(d.pop("env", {}).keys())
        out.append(d)
    return {"servers": out}


@router.post("/mcp/servers")
async def create_mcp_server(body: MCPServerBody, user: str = Depends(require_devstudio_write)):
    if await mcp_service.get_server_by_name(body.name):
        raise HTTPException(400, f"An MCP server named '{body.name}' already exists")
    server = await mcp_service.create_server(**body.model_dump())
    d = server.model_dump()
    d["env_keys"] = list(d.pop("env", {}).keys())
    return d


@router.put("/mcp/servers/{server_id}")
async def update_mcp_server(server_id: str, body: MCPServerUpdateBody,
                              user: str = Depends(require_devstudio_write)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    server = await mcp_service.update_server(server_id, **fields)
    d = server.model_dump()
    d["env_keys"] = list(d.pop("env", {}).keys())
    return d


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str, user: str = Depends(require_devstudio_write)):
    await mcp_service.delete_server(server_id)
    return {"ok": True}


@router.post("/mcp/servers/{server_id}/test")
async def test_mcp_server(server_id: str, user: str = Depends(require_devstudio_write)):
    """Connects to the server for real and lists its tools — never a fabricated 'ok'."""
    return await mcp_service.test_server(server_id)


# --- Tasks -------------------------------------------------------------------------------

@router.post("/tasks")
async def create_task(body: CreateTaskRequest, user: str = Depends(require_devstudio_write)):
    await _require_project(body.project_id)
    task = await task_manager.create_task(body, created_by=user)
    return task.model_dump()


@router.get("/tasks")
async def list_tasks(project_id: Optional[str] = None, user: str = Depends(require_devstudio_access)):
    return {"tasks": [t.model_dump() for t in await task_manager.list_tasks(project_id)]}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user: str = Depends(require_devstudio_access)):
    return (await _require_task(task_id)).model_dump()


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskUpdateRequest, user: str = Depends(require_devstudio_write)):
    await _require_task(task_id)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        return (await task_manager.get_task(task_id)).model_dump()
    return (await task_manager.update_task(task_id, **fields)).model_dump()


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, user: str = Depends(require_devstudio_write)):
    """Soft-delete: the chat is archived (filtered out of list_tasks), never hard-deleted — its
    plan/diff/test/checkpoint history stays intact and recoverable server-side."""
    await _require_task(task_id)
    await task_manager.archive_task(task_id)
    return {"ok": True}


@router.get("/tasks/{task_id}/messages")
async def get_task_messages(task_id: str, user: str = Depends(require_devstudio_access)):
    await _require_task(task_id)
    return {"messages": await task_manager.list_messages(task_id)}


@router.post("/tasks/{task_id}/messages")
async def post_task_message(task_id: str, body: TaskMessageRequest, background_tasks: BackgroundTasks,
                              user: str = Depends(require_devstudio_write)):
    task = await _require_task(task_id)
    # First real message on a blank "New Chat" (created with no request_text) is the effective
    # kickoff: backfill request_text (every downstream prompt reads it, not the message log) and
    # auto-title from it if the title is still the placeholder — ChatGPT/Claude-style naming.
    if not task.request_text.strip() and body.text.strip():
        update_fields = {"request_text": body.text}
        if task.title == task_manager.DEFAULT_CHAT_TITLE:
            update_fields["title"] = task_manager.generate_title_from_text(body.text)
        task = await task_manager.update_task(task_id, **update_fields)
    await task_manager.add_message_from_request(task_id, body)
    # A follow-up message on a resting task resumes orchestration automatically (per spec: natural
    # follow-ups like "Continue." / "Fix it." should just work without a separate Run click).
    if task.status not in ("IMPLEMENTING", "TESTING", "ANALYZING_REPOSITORY", "PLANNING"):
        background_tasks.add_task(orchestrator.run_task, task_id)
    return {"ok": True, "task": task.model_dump()}


@router.post("/tasks/{task_id}/run")
async def run_task(task_id: str, background_tasks: BackgroundTasks,
                     user: str = Depends(require_devstudio_write)):
    task = await _require_task(task_id)
    if task.status in ("COMPLETED",):
        raise HTTPException(400, f"Task is {task.status}; nothing to run")
    await task_manager.clear_stop(task_id)
    background_tasks.add_task(orchestrator.run_task, task_id)
    return {"ok": True, "status": task.status}


@router.post("/tasks/{task_id}/stop")
async def stop_task(task_id: str, user: str = Depends(require_devstudio_write)):
    await _require_task(task_id)
    task = await task_manager.request_stop(task_id)
    return task.model_dump()


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, user: str = Depends(require_devstudio_access)):
    await _require_task(task_id)
    return StreamingResponse(activity_service.stream(task_id), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/tasks/{task_id}/plan")
async def get_plan(task_id: str, user: str = Depends(require_devstudio_access)):
    await _require_task(task_id)
    return {"plan": [p.model_dump() for p in await task_manager.list_plan_items(task_id)]}


@router.get("/tasks/{task_id}/diff")
async def get_diff(task_id: str, user: str = Depends(require_devstudio_access)):
    await _require_task(task_id)
    ws = await workspace_manager.get_workspace_for_task(task_id)
    if not ws:
        return {"files": [], "total_additions": 0, "total_deletions": 0, "changed_file_count": 0}
    summary = await get_diff_summary(ws.local_path, ws.base_commit_sha)
    return summary.to_dict()


@router.get("/tasks/{task_id}/files/tree")
async def task_files_tree(task_id: str, subdir: str = "", user: str = Depends(require_devstudio_access)):
    ws = await _require_workspace(task_id)
    entries = FileService(ws.local_path).list_tree(subdir)
    return {"entries": [e.__dict__ for e in entries]}


@router.get("/tasks/{task_id}/files/read")
async def task_file_read(task_id: str, path: str, user: str = Depends(require_devstudio_access)):
    ws = await _require_workspace(task_id)
    try:
        return {"path": path, "content": FileService(ws.local_path).read_file(path)}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e))


@router.get("/tasks/{task_id}/tests")
async def get_tests(task_id: str, user: str = Depends(require_devstudio_access)):
    await _require_task(task_id)
    return {"runs": [r.model_dump() for r in await execution_service.get_test_runs(task_id)]}


@router.post("/tasks/{task_id}/tests/run")
async def run_tests(task_id: str, user: str = Depends(require_devstudio_write)):
    ws = await _require_workspace(task_id)
    runs = await qa_agent.run_risk_based_tests(task_id, ws)
    return {"runs": [r.model_dump() for r in runs]}


@router.get("/tasks/{task_id}/screenshots")
async def get_screenshots(task_id: str, user: str = Depends(require_devstudio_access)):
    await _require_task(task_id)
    return {"screenshots": [s.model_dump() for s in await browser_service.list_screenshots(task_id)]}


class BrowserRunBody(BaseModel):
    base_url: str
    scenario: str = "smoke"
    actions: List[dict] = []
    viewport: str = "1280x800"


@router.post("/tasks/{task_id}/browser/run")
async def run_browser(task_id: str, body: BrowserRunBody, user: str = Depends(require_devstudio_write)):
    await _require_task(task_id)
    run = await browser_service.run_scenario(task_id, body.base_url, body.scenario, body.actions,
                                                body.viewport)
    return run.model_dump()


# --- Preview (LIVE_LOCAL / SCREENSHOT_ONLY / EXTERNAL_URL) ---------------------------------

class PreviewLiveLocalBody(BaseModel):
    subdir: str = "frontend"  # relative to the workspace root; not a raw shell command — the
                                # actual dev-server command is derived from this directory's own
                                # package.json ("dev" for Vite, "start" for CRA/webpack-dev-server —
                                # see preview_service._resolve_dev_command), never accepted from the
                                # caller, so this can never become an arbitrary shell-command
                                # execution endpoint.


@router.post("/tasks/{task_id}/preview/live-local")
async def preview_live_local(task_id: str, body: PreviewLiveLocalBody = PreviewLiveLocalBody(),
                               user: str = Depends(require_devstudio_write)):
    ws = await _require_workspace(task_id)
    state = await preview_service.start_live_local(task_id, ws.local_path, subdir=body.subdir)
    return state.__dict__


@router.post("/tasks/{task_id}/preview/stop")
async def preview_stop(task_id: str, user: str = Depends(require_devstudio_write)):
    stopped = await preview_service.stop_live_local(task_id)
    return {"stopped": stopped}


class PreviewExternalBody(BaseModel):
    url: str


@router.post("/tasks/{task_id}/preview/external")
async def preview_external(task_id: str, body: PreviewExternalBody,
                             user: str = Depends(require_devstudio_write)):
    await _require_task(task_id)
    state = preview_service.attach_external_url(body.url)
    return state.__dict__


@router.get("/tasks/{task_id}/preview/screenshot")
async def preview_screenshot(task_id: str, user: str = Depends(require_devstudio_access)):
    await _require_task(task_id)
    screenshots = await browser_service.list_screenshots(task_id)
    latest = screenshots[0].path if screenshots else None
    state = preview_service.screenshot_only_state(latest)
    return state.__dict__


@router.get("/tasks/{task_id}/checkpoints")
async def list_checkpoints(task_id: str, user: str = Depends(require_devstudio_access)):
    await _require_task(task_id)
    return {"checkpoints": [c.model_dump() for c in await checkpoint_service.list_checkpoints(task_id)]}


@router.post("/tasks/{task_id}/checkpoints")
async def create_checkpoint(task_id: str, label: str = Body(..., embed=True),
                              reason: Optional[str] = Body(None, embed=True),
                              user: str = Depends(require_devstudio_write)):
    ws = await _require_workspace(task_id)
    cp = await checkpoint_service.create_checkpoint(task_id, ws, label, reason)
    return cp.model_dump()


@router.post("/tasks/{task_id}/checkpoints/{checkpoint_id}/restore")
async def restore_checkpoint(task_id: str, checkpoint_id: str, confirm: bool = Body(False, embed=True),
                               user: str = Depends(require_devstudio_write)):
    ws = await _require_workspace(task_id)
    try:
        cp = await checkpoint_service.restore_checkpoint(task_id, ws, checkpoint_id, confirm=confirm)
    except checkpoint_service.RestoreNotConfirmed as e:
        raise HTTPException(409, str(e))
    return cp.model_dump()


@router.post("/tasks/{task_id}/commit")
async def commit_task(task_id: str, message: str = Body(..., embed=True),
                        user: str = Depends(require_devstudio_write)):
    task = await _require_task(task_id)
    ws = await _require_workspace(task_id)
    try:
        task = await task_manager.set_status(task_id, "COMMITTING")
    except Exception as e:  # noqa: BLE001 — state_machine.InvalidTransition
        raise HTTPException(400, str(e))
    sha = await git_agent.commit(task, ws, message)
    return {"commit_sha": sha, "status": task.status}


class PushBody(BaseModel):
    force_after_drift: bool = False


@router.post("/tasks/{task_id}/push")
async def push_task(task_id: str, body: PushBody = PushBody(), user: str = Depends(require_devstudio_write)):
    task = await _require_task(task_id)
    ws = await _require_workspace(task_id)
    project = await _require_project(task.project_id)
    drift = await workspace_manager.detect_remote_drift(project, ws)
    if drift and not body.force_after_drift:
        raise HTTPException(409, f"Base branch '{ws.base_branch}' has moved on GitHub since this "
                                   "workspace was created. Re-run the task to refresh, or pass "
                                   "force_after_drift=true to push anyway.")
    try:
        task = await task_manager.set_status(task_id, "PUSHING")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    await git_agent.push(task, ws)
    task = await task_manager.set_status(task_id, "COMPLETED")
    await task_manager.update_task(task_id, completion_note="Pushed to GitHub")
    return {"status": task.status, "branch": ws.working_branch}


class PullRequestBody(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None


@router.post("/tasks/{task_id}/pr")
async def open_pr(task_id: str, req: PullRequestBody = PullRequestBody(),
                    user: str = Depends(require_devstudio_write)):
    task = await _require_task(task_id)
    ws = await _require_workspace(task_id)
    project = await _require_project(task.project_id)
    pr = await git_agent.open_pull_request(task, project, ws, req.title or task.title,
                                             req.body or task.request_text)
    return pr


@router.get("/tasks/{task_id}/usage")
async def get_usage(task_id: str, user: str = Depends(require_devstudio_access)):
    await _require_task(task_id)
    return await usage_tracker.task_usage_summary(task_id)


@router.post("/tasks/{task_id}/uploads")
async def upload_file(task_id: str, file: UploadFile, user: str = Depends(require_devstudio_write)):
    await _require_task(task_id)
    data = await file.read()
    try:
        up = await upload_service.save_upload(file.filename, file.content_type, data, task_id=task_id)
    except upload_service.UploadRejected as e:
        raise HTTPException(400, str(e))
    return up.model_dump()


@router.get("/tasks/{task_id}/uploads")
async def list_uploads(task_id: str, user: str = Depends(require_devstudio_access)):
    await _require_task(task_id)
    return {"uploads": [u.model_dump() for u in await upload_service.list_uploads(task_id)]}


@router.get("/uploads/{upload_id}/download")
async def download_upload(upload_id: str, user: str = Depends(require_devstudio_access)):
    up = await upload_service.get_upload(upload_id)
    if not up:
        raise HTTPException(404, "Upload not found")
    data = await upload_service.read_upload_bytes(up)
    return Response(content=data, media_type=up.content_type,
                     headers={"Content-Disposition": f'inline; filename="{up.filename}"'})


class VisionAttachBody(BaseModel):
    attach: bool


@router.put("/uploads/{upload_id}/vision")
async def set_upload_vision(upload_id: str, body: VisionAttachBody,
                             user: str = Depends(require_devstudio_write)):
    """Flag/unflag an image upload for delivery to a vision-capable agent (e.g. Design), on
    request only — never auto-injected into every context."""
    try:
        up = await upload_service.set_vision_attachment(upload_id, body.attach)
    except upload_service.UploadRejected as e:
        raise HTTPException(400, str(e))
    if up is None:
        raise HTTPException(404, "Upload not found")
    return up.model_dump()
