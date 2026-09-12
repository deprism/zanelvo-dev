"""ModelRegistry — the one place that knows which LLMProvider classes exist and how to build them.

Deliberately provider-independent: adding a new vendor means adding one entry to `_PROVIDER_CLASSES`
and one `ModelInfo` list; nothing in agents/ or services/ changes.

Preset design (ECONOMICAL/BALANCED/MAX_QUALITY): coding-heavy roles (analyst, planner, frontend,
backend, integration, supervisor) stay on Anthropic across all three tiers — Claude has the most
consistently-reported edge on agentic coding/tool-use benchmarks, so using anything else there
would be diversity for its own sake, not a real improvement. Two roles genuinely diverge:

- Design uses Gemini: it works with screenshots, reference images, and design language — Gemini's
  multimodal + very large context window (1M tokens) is a real, task-specific advantage there, not
  a general capability claim over Anthropic.
- Reviewer uses OpenAI as primary (Anthropic as fallback): an independent second opinion from a
  different lab than the implementer reduces correlated blind spots — the same reason a human
  reviewer shouldn't be the PR's own author. This is an architectural choice, not a claim that
  OpenAI reviews code better than Claude does.

Within Anthropic, MAX_QUALITY further splits Fable 5.1 vs Opus 5 by role rather than picking one
model for everything:

- Supervisor, Repository Analyst, Planner, and Frontend use Fable 5.1 — it's Anthropic's newest
  flagship, built for long-running agentic problem-solving and self-recovery, and beats Opus 5 on
  most benchmarks while using far fewer output tokens. Its weak spot is lower single-shot (pass@1)
  accuracy than Opus, which matters less for orchestration/analysis/planning/UI work, where a
  wrong first attempt gets caught and retried rather than shipped.
- Backend and Integration stay on Opus 5. Fable carries safety guardrails around cybersecurity/
  biology content that can trigger on legitimate security-adjacent code (auth flows, crypto,
  sandboxing, permission checks) — exactly what these two roles write routinely — and silently
  falls back to a weaker model (Opus 4.8) when it does. Opus 5 has no such block risk and the
  highest pass@1 of the lineup, which is worth more than Fable's efficiency gains for code that
  guards tenant boundaries and secrets.
- Design and Reviewer are unaffected by this split — they were already on Gemini/OpenAI.

Model names/prices were checked against each vendor's own current pricing docs (Sept 2026) rather
than assumed — see the commit that added the OpenAI/Gemini providers.

Credential paths (Settings > Secrets, or the equivalent env vars): native per-vendor API keys
(anthropic/openai/gemini — one key each), Amazon Bedrock (an AWS access key/secret pair + region,
or the default AWS credential chain if no key pair is stored — see bedrock_provider.py), Google's
Gemini Enterprise Agent Platform (a GCP project/location + optional service account JSON, or
Application Default Credentials if none is stored — see gemini_enterprise_provider.py), and the
Emergent Universal Key (one key for GPT/Claude/Gemini via Emergent's proxy — real implementation,
see emergent_provider.py; its optional dependency lives in requirements-emergent.txt, not
requirements-devstudio.txt, due to a pinned-openai-version conflict documented there). None of the
three presets
route through Bedrock, Gemini Enterprise, or Emergent by default; all three are available as an
explicit per-role override (Settings > Agents > pick a role > choose provider) for deployments
that need them, without changing what ships to everyone else.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Type

from .anthropic_provider import AnthropicProvider
from .base import LLMProvider, ModelInfo
from .bedrock_provider import BedrockProvider
from .emergent_provider import EmergentUniversalKeyProvider
from .gemini_enterprise_provider import GeminiEnterpriseProvider
from .gemini_provider import GeminiProvider
from .openai_provider import OpenAIProvider

_PROVIDER_CLASSES: Dict[str, Type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "gemini_enterprise": GeminiEnterpriseProvider,
    "bedrock": BedrockProvider,
    "emergent": EmergentUniversalKeyProvider,
}

_ANTHROPIC_ECONOMICAL = {"primary_provider": "anthropic", "primary_model": "claude-haiku-4-5",
                          "fallback_provider": "anthropic", "fallback_model": "claude-haiku-4-5"}
_ANTHROPIC_BALANCED = {"primary_provider": "anthropic", "primary_model": "claude-sonnet-5",
                        "fallback_provider": "anthropic", "fallback_model": "claude-haiku-4-5"}
_ANTHROPIC_MAX = {"primary_provider": "anthropic", "primary_model": "claude-opus-5",
                   "fallback_provider": "anthropic", "fallback_model": "claude-sonnet-5"}
# MAX_QUALITY only, for roles where Fable 5.1's agentic strength outweighs its lower pass@1 and
# there's no legitimate-code safety-block risk (see module docstring). Falls back to Opus 5, not
# Fable's own internal Opus-4.8 fallback, so a block still lands on this tier's best model.
_ANTHROPIC_MAX_FABLE = {"primary_provider": "anthropic", "primary_model": "claude-fable-5-1",
                         "fallback_provider": "anthropic", "fallback_model": "claude-opus-5"}

# Global presets. Each maps agent role -> (primary_provider, primary_model, fallback_provider,
# fallback_model). Roles not listed fall back to the tier's "default" entry.
MODEL_PRESETS: Dict[str, Dict[str, Dict[str, Optional[str]]]] = {
    "ECONOMICAL": {
        "default": _ANTHROPIC_ECONOMICAL,
        "supervisor": _ANTHROPIC_BALANCED,  # orchestration judgment is worth one tier up even here
        "design": {"primary_provider": "gemini", "primary_model": "gemini-3.1-flash-lite",
                   "fallback_provider": "anthropic", "fallback_model": "claude-haiku-4-5"},
        "reviewer": _ANTHROPIC_ECONOMICAL,  # cross-provider review is a quality spend, not a
                                              # cost-tier default — kept same-provider here
    },
    "BALANCED": {
        "default": _ANTHROPIC_BALANCED,
        "design": {"primary_provider": "gemini", "primary_model": "gemini-3.1-pro-preview",
                   "fallback_provider": "anthropic", "fallback_model": "claude-sonnet-5"},
        "reviewer": {"primary_provider": "openai", "primary_model": "gpt-5.6-terra",
                     "fallback_provider": "anthropic", "fallback_model": "claude-sonnet-5"},
    },
    "MAX_QUALITY": {
        "default": _ANTHROPIC_MAX,  # backend, integration, qa, git land here — Opus 5
        "supervisor": _ANTHROPIC_MAX_FABLE,
        "repository_analyst": _ANTHROPIC_MAX_FABLE,
        "planner": _ANTHROPIC_MAX_FABLE,
        "frontend": _ANTHROPIC_MAX_FABLE,
        "design": {"primary_provider": "gemini", "primary_model": "gemini-3.1-pro-preview",
                   "fallback_provider": "anthropic", "fallback_model": "claude-opus-5"},
        "reviewer": {"primary_provider": "openai", "primary_model": "gpt-6-astra",
                     "fallback_provider": "anthropic", "fallback_model": "claude-opus-5"},
    },
}


def preset_for_role(preset: str, role: str) -> Dict[str, Optional[str]]:
    p = MODEL_PRESETS.get(preset, MODEL_PRESETS["BALANCED"])
    return p.get(role, p["default"])


# A reasonable, real, currently-served model for each provider — used by auto_attempts() below for
# any configured provider that isn't already the role's own curated primary/fallback choice (which
# is used instead when it applies, so curation from MODEL_PRESETS is never discarded, just extended
# to "and also try whatever else you've configured a key for").
_DEFAULT_MODEL_BY_PROVIDER: Dict[str, str] = {
    "anthropic": "claude-sonnet-5",
    "gemini": "gemini-3.1-pro-preview",
    "openai": "gpt-5.6-terra",
    "bedrock": "anthropic.claude-sonnet-5-v1:0",
    "gemini_enterprise": "gemini-3.1-pro-preview",
    "emergent": "claude-sonnet-5",
}

# Global cascade order for providers the role's own preset doesn't mention — native API-key
# providers first (the common case), cloud-account providers and Emergent last, since those need
# extra setup (AWS/GCP credentials, a separate Universal Key) a founder is less likely to have
# configured incidentally.
_AUTO_FALLBACK_ORDER = ["anthropic", "gemini", "openai", "bedrock", "gemini_enterprise", "emergent"]


def auto_attempts(role: str, registry: "ModelRegistry", preset: str = "BALANCED") -> List[Tuple[str, str]]:
    """Builds a primary->fallback->...  attempt list purely from which providers actually have a
    real credential configured (registry.is_configured), for AgentConfiguration.auto_provider=True.

    Order: the role's own BALANCED-preset provider(s) first if configured (preserving this
    project's existing per-role curation — e.g. Design still prefers Gemini, Reviewer still prefers
    OpenAI, when those keys are set), then every other configured provider in a fixed general
    order. Returns [] if literally no provider has a credential configured at all — the caller
    turns that into a clear "requires_credentials" AgentStepFailed rather than trying anything."""
    role_preset = preset_for_role(preset, role)
    ordered_candidates: List[str] = []
    for key in ("primary_provider", "fallback_provider"):
        name = role_preset.get(key)
        if name and name not in ordered_candidates:
            ordered_candidates.append(name)
    for name in _AUTO_FALLBACK_ORDER:
        if name not in ordered_candidates:
            ordered_candidates.append(name)

    attempts: List[Tuple[str, str]] = []
    for name in ordered_candidates:
        if not registry.is_configured(name):
            continue
        model = None
        if role_preset.get("primary_provider") == name:
            model = role_preset.get("primary_model")
        elif role_preset.get("fallback_provider") == name:
            model = role_preset.get("fallback_model")
        attempts.append((name, model or _DEFAULT_MODEL_BY_PROVIDER.get(name, "")))
    return attempts


class ModelRegistry:
    """Live registry of instantiated providers for one request/task run. Instantiate once per
    orchestration run (providers are cheap; API keys are resolved once via SettingsService).

    `api_keys` values are usually a single API key string, except "bedrock" — Bedrock uses the
    AWS credential model (access key/secret pair + region), so its value is a small dict instead;
    see BedrockProvider.__init__."""

    def __init__(self, api_keys: Dict[str, Any]):
        self._api_keys = api_keys
        self._instances: Dict[str, LLMProvider] = {}

    def get(self, provider_name: str) -> LLMProvider:
        if provider_name not in self._instances:
            cls = _PROVIDER_CLASSES.get(provider_name)
            if cls is None:
                raise ValueError(f"Unknown provider: {provider_name}")
            self._instances[provider_name] = cls(self._api_keys.get(provider_name))
        return self._instances[provider_name]

    def is_configured(self, provider_name: str) -> bool:
        """True only if this provider has a real credential set — never a network call, just the
        same presence check routes.py's /capabilities endpoint already makes per provider. Used by
        auto_attempts() to build a cascade purely from what's actually usable."""
        value = self._api_keys.get(provider_name)
        if provider_name == "bedrock":
            # Key pair is optional (the default AWS credential chain can cover it) — region is the
            # one thing BedrockProvider can't fall back on, so it's the real readiness signal.
            return bool((value or {}).get("region"))
        if provider_name == "gemini_enterprise":
            # Service account JSON is optional (Application Default Credentials can cover it) —
            # project id is the one thing GeminiEnterpriseProvider can't fall back on.
            return bool((value or {}).get("project_id"))
        return bool(value)

    def list_all_models(self) -> List[ModelInfo]:
        out: List[ModelInfo] = []
        for name in _PROVIDER_CLASSES:
            try:
                out.extend(self.get(name).list_models())
            except Exception:  # noqa: BLE001 — a misconfigured provider just contributes no models
                continue
        return out


def known_provider_names() -> List[str]:
    return list(_PROVIDER_CLASSES)
