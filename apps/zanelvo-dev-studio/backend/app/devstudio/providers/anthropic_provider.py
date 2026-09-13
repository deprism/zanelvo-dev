"""Real AnthropicProvider. `anthropic` is an OPTIONAL runtime dependency (see
backend/requirements-devstudio.txt) — imported lazily so the app boots (and its deterministic
tests pass) without it installed.

Classification note (CLAUDE.md evidence rules): this provider is "Live but requires external
credentials" until ANTHROPIC_API_KEY (or a stored Dev Studio secret) is configured — calling it
without one raises ProviderNotConfigured rather than returning fake output.
"""
from __future__ import annotations

import time
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import LLMProvider, LLMResult, LLMUsage, ModelInfo, ProviderNotConfigured

# Known Anthropic models Dev Studio can target. Kept as a static registry (per the spec's
# ModelRegistry requirement) rather than a live discovery call, since Anthropic's Python SDK has
# no `models.list` guaranteed-stable endpoint across all deployments; this list is easy to extend.
_MODELS = [
    ModelInfo(id="claude-fable-5-1", provider="anthropic", label="Claude Fable 5.1",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=True,
              context_window=200_000,
              notes="Newest flagship, tuned for long-running agentic tasks and self-recovery; "
                    "beats Opus 5 on most benchmarks but has lower single-shot (pass@1) accuracy "
                    "and carries cybersecurity/biology safety guardrails that can block on "
                    "legitimate security-adjacent code (auth, crypto, sandboxing) — kept off "
                    "Backend/Integration for that reason, see registry.py."),
    ModelInfo(id="claude-opus-5", provider="anthropic", label="Claude Opus 5",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=True,
              context_window=200_000),
    ModelInfo(id="claude-sonnet-5", provider="anthropic", label="Claude Sonnet 5",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=True,
              context_window=200_000),
    ModelInfo(id="claude-haiku-4-5", provider="anthropic", label="Claude Haiku 4.5",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=False,
              context_window=200_000),
]


def _sdk():
    try:
        import anthropic  # noqa: F401
        return anthropic
    except ImportError as e:  # noqa: BLE001
        raise ProviderNotConfigured(
            "The 'anthropic' package is not installed in this environment. Install it via: "
            "pip install -r requirements-devstudio.txt"
        ) from e


_CACHE_CONTROL = {"type": "ephemeral"}


def _cached_system(system: str) -> List[Dict[str, Any]]:
    """Every call marks its system prompt as a cache breakpoint. Anthropic silently skips caching
    a block below its per-model minimum (1024/2048 tokens) rather than erroring, so this is a safe
    no-op for a short role prompt and a real saving for a long one (a founder's custom system
    prompt, or the implementer JSON contract appended in generate_structured) — never something
    that has to be sized correctly by hand."""
    return [{"type": "text", "text": system, "cache_control": dict(_CACHE_CONTROL)}]


def _strip_cache_control(messages: List[Dict[str, Any]]) -> None:
    """Removes any cache_control left over from a previous turn's marking (see
    _mark_last_message_cacheable) before re-marking a new one — keeps exactly one active
    breakpoint in the message history at a time so a long tool-calling loop never exceeds
    Anthropic's 4-breakpoints-per-request cap (system + tools + this = 3, always)."""
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block.pop("cache_control", None)


def _mark_last_message_cacheable(messages: List[Dict[str, Any]]) -> None:
    """Marks the last message's last content block as a cache breakpoint, so everything up to and
    including it is a cache HIT on the next tool-loop iteration (runner.py's call_with_tools calls
    generate_with_tools again with this exact history plus new content appended) — only the newly
    appended tail is billed as fresh input. This is where the real token savings are: the tool loop
    resends its FULL growing history (system + tools + every prior tool_use/tool_result) on every
    one of its up-to-6 iterations, uncached."""
    if not messages:
        return
    content = messages[-1]["content"]
    if isinstance(content, str):
        messages[-1]["content"] = [{"type": "text", "text": content, "cache_control": dict(_CACHE_CONTROL)}]
    elif isinstance(content, list) and content:
        content[-1] = {**content[-1], "cache_control": dict(_CACHE_CONTROL)}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: Optional[str]):
        self._api_key = api_key

    def _client(self):
        if not self._api_key:
            raise ProviderNotConfigured(
                "ANTHROPIC_API_KEY is not configured. Set it in the environment or store it via "
                "Dev Studio Settings > Secrets (POST /api/devstudio/settings/secrets)."
            )
        anthropic = _sdk()
        return anthropic.AsyncAnthropic(api_key=self._api_key)

    def list_models(self) -> List[ModelInfo]:
        return list(_MODELS)

    def _usage_from_response(self, resp, duration_ms: int) -> LLMUsage:
        u = getattr(resp, "usage", None)
        return LLMUsage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_tokens=(getattr(u, "cache_read_input_tokens", 0) or 0)
            + (getattr(u, "cache_creation_input_tokens", 0) or 0),
            cost_usd=None,  # Anthropic responses don't include $ cost; UsageTracker leaves it null
            duration_ms=duration_ms,
        )

    async def generate(self, *, system: str, prompt: str, model: str,
                        max_tokens: int = 4096, temperature: float = 0.2,
                        reasoning_level: Optional[str] = None) -> LLMResult:
        client = self._client()
        t0 = time.monotonic()
        kwargs: Dict[str, Any] = dict(
            model=model, max_tokens=max_tokens, temperature=temperature,
            system=_cached_system(system), messages=[{"role": "user", "content": prompt}],
        )
        if reasoning_level and self.supports_reasoning_levels(model):
            budget = {"low": 1024, "medium": 4096, "high": 12000}.get(reasoning_level, 4096)
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
        resp = await client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        dur = int((time.monotonic() - t0) * 1000)
        return LLMResult(text=text, usage=self._usage_from_response(resp, dur),
                          model=model, provider=self.name, raw=None)

    async def generate_structured(self, *, system: str, prompt: str, model: str,
                                   json_schema: Optional[Dict[str, Any]] = None,
                                   max_tokens: int = 4096) -> LLMResult:
        strict_system = (
            system
            + "\n\nRespond with ONLY a single valid JSON object/array. No prose, no markdown "
              "code fences, no explanation before or after the JSON."
        )
        if json_schema:
            strict_system += f"\n\nThe JSON MUST conform to this schema:\n{json_schema}"
        return await self.generate(system=strict_system, prompt=prompt, model=model,
                                    max_tokens=max_tokens, temperature=0.0)

    async def generate_with_vision(self, *, system: str, prompt: str, model: str,
                                    images_b64: List[str], max_tokens: int = 4096) -> LLMResult:
        if not self.supports_vision(model):
            raise ProviderNotConfigured(f"Model {model} does not support vision input")
        client = self._client()
        t0 = time.monotonic()
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images_b64:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": img},
            })
        resp = await client.messages.create(
            model=model, max_tokens=max_tokens, system=_cached_system(system),
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        dur = int((time.monotonic() - t0) * 1000)
        return LLMResult(text=text, usage=self._usage_from_response(resp, dur),
                          model=model, provider=self.name)

    async def stream(self, *, system: str, prompt: str, model: str,
                      max_tokens: int = 4096) -> AsyncIterator[str]:
        client = self._client()
        async with client.messages.stream(
            model=model, max_tokens=max_tokens, system=_cached_system(system),
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def generate_with_tools(self, *, system: str, model: str, tools: List[Dict[str, Any]],
                                   prompt: Optional[str] = None, history: Optional[Any] = None,
                                   tool_results: Optional[List[Dict[str, Any]]] = None,
                                   max_tokens: int = 4096) -> LLMResult:
        client = self._client()
        messages: List[Dict[str, Any]] = list(history) if history is not None else []
        if tool_results:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
                    for r in tool_results
                ],
            })
        elif prompt is not None:
            messages.append({"role": "user", "content": prompt})
        # "web_search" (a runner.py built-in tool) maps onto Claude's own server-hosted search
        # instead of a JSON-schema tool this app would have to execute itself — Anthropic resolves
        # it within this same API call and the result never comes back as a tool_use block (see
        # the type=="tool_use" filter below), so it needs no entry in runner.py's tool executor.
        # NOTE: not independently verified against a live call in this build (no API key
        # available here) — if Anthropic's current tool-type string has moved on, this specific
        # tool will error and the agent step falls back the same as any other provider error,
        # rather than silently doing nothing.
        anthropic_tools = [
            {"type": "web_search_20250305", "name": "web_search"} if t["name"] == "web_search"
            else {"name": t["name"], "description": t.get("description", ""), "input_schema": t["inputSchema"]}
            for t in tools
        ]
        # cache_control on the LAST tool definition caches the whole tools array (per Anthropic's
        # own semantics) — real savings across this call's own up-to-6-iteration loop, since the
        # full tool schema is resent unchanged on every one of them.
        if anthropic_tools:
            anthropic_tools[-1] = {**anthropic_tools[-1], "cache_control": dict(_CACHE_CONTROL)}
        _strip_cache_control(messages)
        _mark_last_message_cacheable(messages)
        t0 = time.monotonic()
        resp = await client.messages.create(
            model=model, max_tokens=max_tokens, system=_cached_system(system), messages=messages,
            tools=anthropic_tools,
        )
        dur = int((time.monotonic() - t0) * 1000)
        messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        tool_calls = [
            {"id": b.id, "name": b.name, "arguments": b.input}
            for b in resp.content if getattr(b, "type", None) == "tool_use"
        ]
        return LLMResult(
            text=text, usage=self._usage_from_response(resp, dur), model=model, provider=self.name,
            tool_calls=tool_calls or None, tool_loop_history=messages,
        )
