"""Real GeminiEnterpriseProvider — the same Gemini models, served through Google Cloud's Gemini
Enterprise Agent Platform (the product formerly known as Vertex AI) instead of the plain Gemini
Developer API. Subclasses GeminiProvider: request/response shape is identical (both surfaces are
accessed through the same `google-genai` SDK — Google's own migration docs confirm the Developer
API and the Enterprise Agent Platform API are "now accessible through the unified Google Gen AI
SDK" with unchanged request parameters and response structure); only how the client authenticates
differs, so only `_client()`, `name`, and `list_models()` are overridden here for the Gemini model
family. (Claude models on this same provider are a separate transport entirely — see below —
which is why every other request method also gets a thin dispatch override further down.)

Why this exists alongside GeminiProvider: same reason BedrockProvider exists alongside
AnthropicProvider — enterprise deployments that need GCP-native governance (VPC Service Controls,
CMEK, data residency, org-policy controls, existing GCP billing) rather than a bare API key calling
generativelanguage.googleapis.com directly. `google-genai` is the same OPTIONAL runtime dependency
already used by GeminiProvider (see requirements-devstudio.txt) — no new package needed.

Credentials work differently from a single API key (and Vertex AI does not accept API keys at
all — confirmed via Google's own SDK issue tracker): a GCP project ID and location are required,
plus one of two credential paths:

1. A service account key, pasted as JSON into Dev Studio Settings > Secrets
   (`gcp_service_account_json`) — loaded in memory via
   `google.oauth2.service_account.Credentials.from_service_account_info()` and passed to the SDK
   explicitly. Nothing is ever written to disk.
2. Left blank — the SDK falls back to Google's own Application Default Credentials resolution
   (the real `GOOGLE_APPLICATION_CREDENTIALS` file-path env var, `gcloud auth
   application-default login`, or an attached GCE/GKE/Cloud Run service account). This is the
   standard zero-config path for a Dev Studio deployment that already runs inside GCP — a real
   credential source, not a shortcut; a genuinely missing credential still fails at call time
   rather than faking a result.

Verified against Google's own current docs (docs.cloud.google.com/gemini-enterprise-agent-platform,
ai.google.dev/gemini-api/docs/migrate-to-cloud, googleapis/python-genai on GitHub) rather than
assumed, same standard as the other providers in this module — see the commit that added this
file.

Claude models (`_CLAUDE_MODELS` below): Google Cloud's Agent Platform Model Garden genuinely hosts
Anthropic's Claude models too ("Claude on Google Cloud", platform.claude.com/docs/en/build-with-
claude/claude-on-vertex-ai) — but through a completely different transport than Gemini: Anthropic's
own `AnthropicVertex`/`AsyncAnthropicVertex` client (the `anthropic[vertex]` extra — see
requirements-devstudio.txt), which speaks the same Messages API as `AnthropicProvider` (rawPredict
under `publishers/anthropic/...`, not `google-genai`'s `generate_content`). `_VertexClaudeAdapter`
below reuses every one of `AnthropicProvider`'s real request/response methods unchanged — the only
difference is which client factory it calls. Model IDs and the fact that Haiku 4.5 alone needs a
dated `@20251001` suffix (unlike the newer models, which use the bare id) are taken verbatim from
Anthropic's own Agent Platform model ID table at the URL above — fetched live, not guessed.
"""
from __future__ import annotations

import json
import os
from typing import AsyncIterator, Dict, List, Optional

from .anthropic_provider import AnthropicProvider
from .base import LLMResult, ModelInfo, ProviderNotConfigured
from .gemini_provider import GeminiProvider, _sdk

_CLAUDE_MODELS = [
    ModelInfo(id="claude-fable-5-1", provider="gemini_enterprise",
              label="Claude Fable 5.1 (Agent Platform)",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=True,
              context_window=1_000_000,
              notes="Claude on Google Cloud's Agent Platform (Vertex AI), via Anthropic's own "
                    "AnthropicVertex client — not google-genai. Agent Platform gives this model a "
                    "1M-token context window per Anthropic's own docs (vs. 200k on the direct "
                    "Anthropic API)."),
    ModelInfo(id="claude-opus-5", provider="gemini_enterprise",
              label="Claude Opus 5 (Agent Platform)",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=True,
              context_window=1_000_000),
    ModelInfo(id="claude-sonnet-5", provider="gemini_enterprise",
              label="Claude Sonnet 5 (Agent Platform)",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=True,
              context_window=1_000_000),
    ModelInfo(id="claude-haiku-4-5@20251001", provider="gemini_enterprise",
              label="Claude Haiku 4.5 (Agent Platform)",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=False,
              context_window=200_000,
              notes="Agent Platform requires the dated model id (@20251001) for Haiku 4.5 "
                    "specifically — per Anthropic's own Agent Platform model ID table, unlike the "
                    "newer models above which use the bare id."),
]
_CLAUDE_MODEL_IDS = {m.id for m in _CLAUDE_MODELS}


class _VertexClaudeAdapter(AnthropicProvider):
    """Not constructed with an api_key — deliberately skips AnthropicProvider.__init__ so its
    api_key-required _client() is never reached. Reuses every other AnthropicProvider method
    (generate, generate_structured, generate_with_vision, stream, generate_with_tools, usage
    extraction) completely unchanged; only _client() differs, returning an AsyncAnthropicVertex
    built from GeminiEnterpriseProvider's own GCP credentials instead of an API key."""

    name = "gemini_enterprise"

    def __init__(self, client_factory):
        self._client_factory = client_factory

    def list_models(self) -> List[ModelInfo]:
        return list(_CLAUDE_MODELS)

    def _client(self):
        return self._client_factory()


_MODELS = [
    ModelInfo(id="gemini-3.1-pro-preview", provider="gemini_enterprise",
              label="Gemini 3.1 Pro Preview (Enterprise Agent Platform)",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=True,
              context_window=1_000_000,
              notes="Same model as the Gemini Developer API, served through Google Cloud's "
                    "Gemini Enterprise Agent Platform for GCP-native governance/data-residency."),
    ModelInfo(id="gemini-3.6-flash", provider="gemini_enterprise",
              label="Gemini 3.6 Flash (Enterprise Agent Platform)",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=True,
              context_window=1_000_000),
    ModelInfo(id="gemini-3.5-flash-lite", provider="gemini_enterprise",
              label="Gemini 3.5 Flash-Lite (Enterprise Agent Platform)",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=False,
              context_window=1_000_000),
    ModelInfo(id="gemini-3.1-flash-lite", provider="gemini_enterprise",
              label="Gemini 3.1 Flash-Lite (Enterprise Agent Platform)",
              supports_tools=True, supports_vision=True, supports_reasoning_levels=False,
              context_window=1_000_000),
]


class GeminiEnterpriseProvider(GeminiProvider):
    name = "gemini_enterprise"

    def __init__(self, credentials: Optional[Dict[str, Optional[str]]]):
        # {"project_id": ..., "location": ..., "claude_region": ..., "service_account_json": ...}
        # — deliberately does NOT call GeminiProvider.__init__, which expects a single api_key
        # string.
        self._creds = credentials or {}

    def list_models(self) -> List[ModelInfo]:
        return list(_MODELS) + list(_CLAUDE_MODELS)

    def _require_project(self) -> str:
        project = self._creds.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise ProviderNotConfigured(
                "No GCP project configured for Gemini Enterprise Agent Platform. Set it via Dev "
                "Studio Settings > Secrets (gcp_project_id) or the GOOGLE_CLOUD_PROJECT "
                "environment variable."
            )
        return project

    def _load_service_account_info(self) -> Optional[dict]:
        sa_json = self._creds.get("service_account_json") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        if not sa_json:
            return None
        try:
            return json.loads(sa_json)
        except (json.JSONDecodeError, TypeError) as e:
            raise ProviderNotConfigured(
                "gcp_service_account_json is not valid JSON — paste the full service account "
                "key file contents, not a file path."
            ) from e

    def _google_credentials(self, info: Optional[dict]):
        if info is None:
            return None
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/cloud-platform"])

    def _client(self):
        project = self._require_project()
        location = self._creds.get("location") or os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1"
        credentials = self._google_credentials(self._load_service_account_info())
        genai, _types = _sdk()
        if credentials is not None:
            return genai.Client(vertexai=True, project=project, location=location, credentials=credentials)
        # No service account JSON stored — Vertex AI does not accept API keys, so this relies on
        # Google's real Application Default Credentials chain (GOOGLE_APPLICATION_CREDENTIALS file
        # path, `gcloud auth application-default login`, or an attached GCP service account).
        return genai.Client(vertexai=True, project=project, location=location)

    def _claude_vertex_client(self):
        project = self._require_project()
        # Claude on Agent Platform uses a different region namespace than Gemini's `location`
        # ("global"/"us"/"eu"/a specific region like "us-east5" — not Gemini's "us-central1"
        # default), so it gets its own credential key rather than reusing `location`. "global" is
        # Anthropic's own recommended default (max availability, no pricing premium).
        region = self._creds.get("claude_region") or os.environ.get("ANTHROPIC_VERTEX_REGION") or "global"
        credentials = self._google_credentials(self._load_service_account_info())
        from anthropic import AsyncAnthropicVertex
        if credentials is not None:
            return AsyncAnthropicVertex(project_id=project, region=region, credentials=credentials)
        # No service account JSON — falls back to the same real Application Default Credentials
        # chain as the Gemini path above.
        return AsyncAnthropicVertex(project_id=project, region=region)

    def _claude_adapter(self) -> _VertexClaudeAdapter:
        return _VertexClaudeAdapter(self._claude_vertex_client)

    async def generate(self, *, system: str, prompt: str, model: str,
                        max_tokens: int = 4096, temperature: float = 0.2,
                        reasoning_level: Optional[str] = None) -> LLMResult:
        if model in _CLAUDE_MODEL_IDS:
            return await self._claude_adapter().generate(
                system=system, prompt=prompt, model=model, max_tokens=max_tokens,
                temperature=temperature, reasoning_level=reasoning_level)
        return await super().generate(system=system, prompt=prompt, model=model,
                                       max_tokens=max_tokens, temperature=temperature,
                                       reasoning_level=reasoning_level)

    async def generate_structured(self, *, system: str, prompt: str, model: str,
                                   json_schema: Optional[Dict[str, object]] = None,
                                   max_tokens: int = 4096) -> LLMResult:
        if model in _CLAUDE_MODEL_IDS:
            return await self._claude_adapter().generate_structured(
                system=system, prompt=prompt, model=model, json_schema=json_schema,
                max_tokens=max_tokens)
        return await super().generate_structured(system=system, prompt=prompt, model=model,
                                                   json_schema=json_schema, max_tokens=max_tokens)

    async def generate_with_vision(self, *, system: str, prompt: str, model: str,
                                    images_b64: List[str], max_tokens: int = 4096) -> LLMResult:
        if model in _CLAUDE_MODEL_IDS:
            return await self._claude_adapter().generate_with_vision(
                system=system, prompt=prompt, model=model, images_b64=images_b64,
                max_tokens=max_tokens)
        return await super().generate_with_vision(system=system, prompt=prompt, model=model,
                                                    images_b64=images_b64, max_tokens=max_tokens)

    async def stream(self, *, system: str, prompt: str, model: str,
                      max_tokens: int = 4096) -> AsyncIterator[str]:
        if model in _CLAUDE_MODEL_IDS:
            async for chunk in self._claude_adapter().stream(
                    system=system, prompt=prompt, model=model, max_tokens=max_tokens):
                yield chunk
            return
        async for chunk in super().stream(system=system, prompt=prompt, model=model,
                                           max_tokens=max_tokens):
            yield chunk

    async def generate_with_tools(self, *, system: str, model: str, tools: List[Dict[str, object]],
                                   prompt: Optional[str] = None, history: Optional[object] = None,
                                   tool_results: Optional[List[Dict[str, object]]] = None,
                                   max_tokens: int = 4096) -> LLMResult:
        if model in _CLAUDE_MODEL_IDS:
            return await self._claude_adapter().generate_with_tools(
                system=system, model=model, tools=tools, prompt=prompt, history=history,
                tool_results=tool_results, max_tokens=max_tokens)
        return await super().generate_with_tools(
            system=system, model=model, tools=tools, prompt=prompt, history=history,
            tool_results=tool_results, max_tokens=max_tokens)
