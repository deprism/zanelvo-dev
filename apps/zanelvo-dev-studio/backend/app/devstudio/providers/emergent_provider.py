"""EmergentUniversalKeyProvider — real LLMProvider backed by Emergent's Universal Key.

One Universal Key (`EMERGENT_UNIVERSAL_KEY`, or the encrypted `emergent_universal_key` secret)
gives access to GPT / Claude / Gemini model families through Emergent's integration proxy. This
class implements the full LLMProvider surface so it is a drop-in `ModelRegistry` entry — nothing in
agents/ or orchestration changes.

Transport: the `emergentintegrations` package (Emergent's own CDN wheel, not PyPI). It is an
OPTIONAL runtime dependency kept in its OWN file, backend/requirements-emergent.txt — deliberately
NOT part of requirements-devstudio.txt, since every published emergentintegrations version hard-pins
openai==1.99.9, conflicting with requirements-devstudio.txt's openai==1.109.1 pin for the native
OpenAI provider (see that file's own header for the two ways to combine them). Imported lazily via
`_sdk()`, exactly like `anthropic`/`openai` elsewhere, so the app boots and its deterministic tests
pass without it installed.

Model discovery: `emergentintegrations` exposes no live model-list endpoint, so `list_models()`
returns a curated static catalog of the model IDs the Universal Key actually serves (per Emergent's
published supported-model list) — matching the same static-registry pattern the Anthropic/OpenAI
providers use. Capability flags are only set where they are genuinely true for the family; nothing
is fabricated.

See docs/EMERGENT_INTEGRATION_HANDOFF.md for the integration boundary this file satisfies.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import (
    LLMProvider,
    LLMResult,
    LLMUsage,
    ModelInfo,
    ProviderError,
    ProviderNotConfigured,
)

# Curated catalog of Universal-Key-served models across the three families. IDs are the exact
# strings the proxy routes on. `family` is the litellm provider used for `.with_model(family, id)`;
# it is not surfaced as ModelInfo.provider (that stays "emergent" so these appear under the Emergent
# selector). Capability flags mirror each vendor's own current catalog (the same source the sibling
# providers used) — set only where genuinely supported.
_CATALOG: List[Dict[str, Any]] = [
    # --- OpenAI family ---
    {"id": "gpt-6-astra", "family": "openai", "label": "GPT-6 Astra (Universal Key)",
     "tools": True, "vision": True, "reasoning": True, "ctx": 400_000,
     "notes": "OpenAI flagship via Emergent Universal Key."},
    {"id": "gpt-5.6-terra", "family": "openai", "label": "GPT-5.6 Terra (Universal Key)",
     "tools": True, "vision": True, "reasoning": True, "ctx": 256_000,
     "notes": "OpenAI balanced tier via Emergent Universal Key."},
    {"id": "gpt-5.4", "family": "openai", "label": "GPT-5.4 (Universal Key)",
     "tools": True, "vision": True, "reasoning": True, "ctx": 400_000,
     "notes": "OpenAI general-purpose (recommended) via Emergent Universal Key."},
    {"id": "gpt-5.4-mini", "family": "openai", "label": "GPT-5.4 Mini (Universal Key)",
     "tools": True, "vision": True, "reasoning": False, "ctx": 128_000,
     "notes": "OpenAI budget tier via Emergent Universal Key."},
    # --- Anthropic family ---
    {"id": "claude-fable-5-1", "family": "anthropic", "label": "Claude Fable 5.1 (Universal Key)",
     "tools": True, "vision": True, "reasoning": True, "ctx": 200_000,
     "notes": "Anthropic agentic flagship via Emergent Universal Key."},
    {"id": "claude-opus-5", "family": "anthropic", "label": "Claude Opus 5 (Universal Key)",
     "tools": True, "vision": True, "reasoning": True, "ctx": 200_000,
     "notes": "Anthropic highest-accuracy tier via Emergent Universal Key."},
    {"id": "claude-sonnet-5", "family": "anthropic", "label": "Claude Sonnet 5 (Universal Key)",
     "tools": True, "vision": True, "reasoning": True, "ctx": 200_000,
     "notes": "Anthropic balanced tier via Emergent Universal Key."},
    {"id": "claude-sonnet-4-6", "family": "anthropic", "label": "Claude Sonnet 4.6 (Universal Key)",
     "tools": True, "vision": True, "reasoning": True, "ctx": 200_000,
     "notes": "Anthropic (recommended) via Emergent Universal Key."},
    # --- Gemini family ---
    {"id": "gemini-3.1-pro-preview", "family": "gemini", "label": "Gemini 3.1 Pro (Universal Key)",
     "tools": True, "vision": True, "reasoning": True, "ctx": 1_000_000,
     "notes": "Gemini long-context multimodal (recommended) via Emergent Universal Key."},
    {"id": "gemini-3-flash-preview", "family": "gemini", "label": "Gemini 3 Flash (Universal Key)",
     "tools": True, "vision": True, "reasoning": False, "ctx": 1_000_000,
     "notes": "Gemini fast multimodal via Emergent Universal Key."},
    {"id": "gemini-2.5-pro", "family": "gemini", "label": "Gemini 2.5 Pro (Universal Key)",
     "tools": True, "vision": True, "reasoning": True, "ctx": 1_000_000,
     "notes": "Gemini 2.5 Pro via Emergent Universal Key."},
    {"id": "gemini-2.5-flash", "family": "gemini", "label": "Gemini 2.5 Flash (Universal Key)",
     "tools": True, "vision": True, "reasoning": False, "ctx": 1_000_000,
     "notes": "Gemini 2.5 Flash via Emergent Universal Key."},
]

_FAMILY: Dict[str, str] = {m["id"]: m["family"] for m in _CATALOG}


def _sdk():
    """Lazy import — the app must boot without emergentintegrations installed."""
    try:
        import litellm  # emergentintegrations' transport; bundled with it

        # Different Universal-Key model families accept different param sets (e.g. GPT-5 models
        # only allow temperature=1, no reasoning_effort on some tiers). Let litellm silently drop
        # params a given model doesn't support instead of erroring — scoped to this path only,
        # since the native anthropic/openai/gemini providers use their own SDKs, not litellm.
        litellm.drop_params = True
        from emergentintegrations.llm.chat import (  # noqa: F401
            ChatError,
            ImageContent,
            LlmChat,
            UserMessage,
        )
        return LlmChat, UserMessage, ImageContent, ChatError
    except ImportError as e:  # noqa: BLE001
        raise ProviderNotConfigured(
            "The 'emergentintegrations' package is not installed in this environment. It is NOT "
            "part of requirements-devstudio.txt (that would conflict with the native OpenAI "
            "provider's pinned version) — install it via: pip install -r requirements-emergent.txt "
            "(see that file's header for how to combine it with the native OpenAI provider if you "
            "need both)."
        ) from e


def _normalize_error(exc: Exception) -> Exception:
    """Map a raw provider/transport failure onto the app's provider error taxonomy without ever
    leaking the Universal Key or other secret material. Returns the exception to raise."""
    msg = str(exc)
    low = msg.lower()
    # Auth/credential problems are classified as 'requires_credentials' by the runner.
    if any(k in low for k in ("api key", "api_key", "unauthorized", "authentication",
                              "invalid key", "forbidden", "401", "403")):
        return ProviderNotConfigured(
            "Emergent Universal Key was rejected (authentication failed). Verify EMERGENT_UNIVERSAL_KEY "
            "or the stored emergent_universal_key secret is valid and has runtime balance."
        )
    if any(k in low for k in ("insufficient", "no balance", "out of credit", "credit", "budget",
                              "payment required", "402", "quota", "billing")):
        return ProviderError(
            "INSUFFICIENT_CREDIT",
            "Emergent Universal Key has no runtime balance. Add credit (Profile → Manage plan → "
            "Universal Key → Add Balance) and retry.",
        )
    if any(k in low for k in ("rate limit", "rate_limit", "429", "too many requests")):
        code = "RATE_LIMIT"
    elif any(k in low for k in ("context length", "context_length", "maximum context",
                                "too many tokens", "context window", "413")):
        code = "CONTEXT_TOO_LARGE"
    elif any(k in low for k in ("timeout", "timed out")):
        code = "PROVIDER_TIMEOUT"
    elif any(k in low for k in ("not found", "does not exist", "unknown model", "no such model",
                                "404")):
        code = "MODEL_UNAVAILABLE"
    elif any(k in low for k in ("invalid request", "bad request", "400", "unprocessable")):
        code = "INVALID_REQUEST"
    else:
        code = "PROVIDER_ERROR"
    # Strip anything that could echo a key back; keep only a short, safe classification.
    return ProviderError(code, f"Emergent provider call failed ({code}).")


def _unsupported_param(msg: str, params: Dict[str, Any]) -> Optional[str]:
    """If an error says a specific param isn't supported by this model, return which param in
    `params` to drop for a retry (or None). Newer Universal-Key models reject params older ones
    accept; this lets one provider serve them all without hardcoding per-model param tables."""
    low = msg.lower()
    if "unsupported" not in low and "not supported" not in low and "only temperature" not in low:
        return None
    # Order matters: temperature phrasing ("only temperature=1 is supported") also contains the
    # word "temperature"; check each candidate that's actually present in the request.
    for name in ("temperature", "max_tokens", "reasoning_effort"):
        if name in params and name in low:
            return name
    return None


class EmergentUniversalKeyProvider(LLMProvider):
    name = "emergent"

    def __init__(self, universal_key: Optional[str] = None):
        self._universal_key = universal_key

    # --- discovery / capabilities -------------------------------------------------------------
    def list_models(self) -> List[ModelInfo]:
        return [
            ModelInfo(
                id=m["id"], provider=self.name, label=m["label"],
                supports_tools=m["tools"], supports_vision=m["vision"],
                supports_reasoning_levels=m["reasoning"], context_window=m["ctx"],
                notes=m["notes"],
            )
            for m in _CATALOG
        ]

    # --- internals ----------------------------------------------------------------------------
    def _require_key(self) -> str:
        if not self._universal_key:
            raise ProviderNotConfigured(
                "EMERGENT_UNIVERSAL_KEY is not configured. Set it in the environment or store it via "
                "Dev Studio Settings > Secrets (POST /api/devstudio/settings/secrets "
                '{"name": "emergent_universal_key", "value": "<key>"}).'
            )
        return self._universal_key

    def _family(self, model: str) -> str:
        fam = _FAMILY.get(model)
        if fam is None:
            raise ProviderError(
                "MODEL_UNAVAILABLE",
                f"Model '{model}' is not served by the Emergent Universal Key catalog.",
            )
        return fam

    def _chat(self, *, system: str, model: str):
        key = self._require_key()
        family = self._family(model)  # pure catalog check first — fails fast without the SDK
        LlmChat, _UserMessage, _ImageContent, _ChatError = _sdk()
        session_id = f"devstudio-{uuid.uuid4().hex}"
        return LlmChat(api_key=key, session_id=session_id, system_message=system).with_model(
            family, model
        )

    @staticmethod
    def _usage(resp_usage, duration_ms: int) -> LLMUsage:
        # emergentintegrations returns a Usage(input_tokens, output_tokens, total_tokens).
        # No dollar cost and no cache-token breakdown are exposed → leave those unset (never faked).
        return LLMUsage(
            input_tokens=getattr(resp_usage, "input_tokens", 0) or 0,
            output_tokens=getattr(resp_usage, "output_tokens", 0) or 0,
            cache_tokens=0,
            cost_usd=None,
            duration_ms=duration_ms,
        )

    async def _send(self, *, system: str, model: str, user_message, params: Dict[str, Any]):
        """Send one request, adapting to what THIS model actually accepts. Newer families reject
        certain params (e.g. Claude-5/GPT-5 only allow the default temperature; GPT-6 rejects
        `max_tokens`) and litellm's own drop_params doesn't catch them through the Universal Key
        proxy — so when the model reports an unsupported param, drop that one and retry (bounded)."""
        _LlmChat, _UserMessage, _ImageContent, ChatError = _sdk()
        params = dict(params)
        last: Optional[Exception] = None
        for _ in range(len(params) + 1):
            chat = self._chat(system=system, model=model)
            if params:
                chat = chat.with_params(**params)
            try:
                return await chat.send_message_with_tools(user_message)
            except ChatError as e:
                last = e
                drop = _unsupported_param(str(e), params)
                if drop is None:
                    raise _normalize_error(e) from e
                params.pop(drop, None)
        raise _normalize_error(last) from last  # pragma: no cover

    def _floor_tokens(self, max_tokens: int) -> int:
        # A tiny output budget makes reasoning models spend it all on hidden thinking and return
        # empty/incomplete output; enforce a small floor across all families.
        return max(max_tokens, 64)

    # --- generation ---------------------------------------------------------------------------
    async def generate(self, *, system: str, prompt: str, model: str,
                        max_tokens: int = 4096, temperature: float = 0.2,
                        reasoning_level: Optional[str] = None) -> LLMResult:
        family = self._family(model)  # pure catalog check first — fails fast without the SDK
        _LlmChat, UserMessage, _ImageContent, _ChatError = _sdk()
        # NOTE: temperature is deliberately NOT sent — several Universal-Key model families only
        # accept their default temperature, and determinism for structured output is enforced by
        # strict-JSON prompting, not sampling temperature.
        params: Dict[str, Any] = {"max_tokens": self._floor_tokens(max_tokens)}
        if reasoning_level and self.supports_reasoning_levels(model) and family == "openai":
            params["reasoning_effort"] = {"low": "low", "medium": "medium",
                                          "high": "high"}.get(reasoning_level, "medium")
        t0 = time.monotonic()
        resp = await self._send(system=system, model=model,
                                user_message=UserMessage(text=prompt), params=params)
        dur = int((time.monotonic() - t0) * 1000)
        return LLMResult(text=resp.content or "", usage=self._usage(resp.usage, dur),
                         model=model, provider=self.name)

    async def generate_structured(self, *, system: str, prompt: str, model: str,
                                   json_schema: Optional[Dict[str, Any]] = None,
                                   max_tokens: int = 4096) -> LLMResult:
        # Strict-JSON prompting + the runner's extract_json repair — provider-agnostic and robust
        # across all three families behind the Universal Key (matches AnthropicProvider's approach).
        strict_system = (
            system
            + "\n\nRespond with ONLY a single valid JSON object/array. No prose, no markdown code "
              "fences, no explanation before or after the JSON."
        )
        if json_schema:
            strict_system += f"\n\nThe JSON MUST conform to this schema:\n{json_schema}"
        return await self.generate(system=strict_system, prompt=prompt, model=model,
                                   max_tokens=max_tokens)

    async def generate_with_vision(self, *, system: str, prompt: str, model: str,
                                    images_b64: List[str], max_tokens: int = 4096) -> LLMResult:
        if not self.supports_vision(model):
            raise ProviderError("INVALID_REQUEST",
                                f"Model '{model}' does not support vision input.")
        _LlmChat, UserMessage, ImageContent, _ChatError = _sdk()
        user_msg = UserMessage(
            text=prompt,
            file_contents=[ImageContent(image_base64=img) for img in images_b64],
        )
        t0 = time.monotonic()
        resp = await self._send(system=system, model=model, user_message=user_msg,
                                params={"max_tokens": self._floor_tokens(max_tokens)})
        dur = int((time.monotonic() - t0) * 1000)
        return LLMResult(text=resp.content or "", usage=self._usage(resp.usage, dur),
                         model=model, provider=self.name)

    async def generate_with_tools(self, *, system: str, model: str, tools: List[Dict[str, Any]],
                                   prompt: Optional[str] = None, history: Optional[Any] = None,
                                   tool_results: Optional[List[Dict[str, Any]]] = None,
                                   max_tokens: int = 4096) -> LLMResult:
        # Verified this session against the REAL emergentintegrations SDK (installed from
        # Emergent's own CDN index — see requirements-emergent.txt), not just read against its
        # docstrings: LlmChat/UserMessage/ToolCall/Usage's actual constructor signatures and
        # attribute names, and — critically — that add_tool_result()'s pending-call bookkeeping
        # (populated internally by a REAL send_message_with_tools() response, not a shallow mock
        # of that method) accepts the id this provider passes back on the following turn. Also
        # verified end to end through runner.call_with_tools() with a role's tools_enabled default
        # (e.g. the Planner's ask_human), including ask_human's real BLOCKED-transition side
        # effect. Only the network boundary (litellm.acompletion) was mocked — no funded Universal
        # Key was available in this environment. `emergentintegrations` cannot be installed
        # alongside requirements-devstudio.txt's openai==1.109.1 pin (see requirements-emergent.txt
        # for why), so this verification was done in a separate venv and isn't part of the default
        # pytest run — see the commit that added this note for the exact repro commands.
        #
        # `history`, when set, IS the same stateful LlmChat instance from the previous turn (the
        # SDK's own with_tools()/add_tool_result()/send_message_with_tools() already track
        # conversation state internally) — round-tripping the object itself is simpler and more
        # faithful than re-deriving a messages array, and this provider is only ever called
        # in-process, never (de)serialized, so an opaque object is safe here.
        _LlmChat, UserMessage, _ImageContent, ChatError = _sdk()
        chat = history if history is not None else self._chat(system=system, model=model)
        emergent_tools = [
            {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                               "parameters": t["inputSchema"]}}
            for t in tools
        ]
        chat = chat.with_tools(emergent_tools).with_params(max_tokens=self._floor_tokens(max_tokens))
        t0 = time.monotonic()
        try:
            if tool_results:
                for r in tool_results:
                    chat.add_tool_result(r["id"], r["content"])
                resp = await chat.send_message_with_tools()
            else:
                resp = await chat.send_message_with_tools(UserMessage(text=prompt))
        except ChatError as e:
            raise _normalize_error(e) from e
        dur = int((time.monotonic() - t0) * 1000)
        tool_calls = (
            [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in resp.tool_calls]
            if resp.tool_calls else None
        )
        return LLMResult(text=resp.content or "", usage=self._usage(resp.usage, dur),
                         model=model, provider=self.name, tool_calls=tool_calls, tool_loop_history=chat)

    async def stream(self, *, system: str, prompt: str, model: str,
                     max_tokens: int = 4096) -> AsyncIterator[str]:
        from emergentintegrations.llm.chat import TextDelta  # local — keeps module import lazy
        _LlmChat, UserMessage, _ImageContent, ChatError = _sdk()
        chat = self._chat(system=system, model=model).with_params(max_tokens=max_tokens)
        try:
            async for event in chat.stream_message(UserMessage(text=prompt)):
                if isinstance(event, TextDelta) and event.content:
                    yield event.content
        except ChatError as e:
            raise _normalize_error(e) from e
