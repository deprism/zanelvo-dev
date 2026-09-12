"""Deterministic tests for automatic provider selection (AgentConfiguration.auto_provider) — the
fix for "I shouldn't have to set provider/fallback, just use whatever API key I've set until an
error." No DB/network: ModelRegistry.is_configured/auto_attempts are pure, and _attempts_for is
exercised directly without any real provider call.
"""
import pytest

from app.devstudio.agents.registry import _default_config
from app.devstudio.agents.runner import _attempts_for, AgentStepFailed
from app.devstudio.models import AgentConfiguration
from app.devstudio.providers.registry import auto_attempts, ModelRegistry

# Note: PUT /agents/config/{role}'s request schema (routes.AgentConfigBody) actually silently
# dropped auto_provider on the very first live smoke-test run — its own Pydantic model didn't list
# the field, even though AgentConfiguration/_attempts_for/auto_attempts all worked perfectly in
# isolation. routes.py can't be imported from this DB/env-free pytest suite (it pulls in the full
# app.config/security chain, which requires real env vars) — that route-level wiring is covered by
# the live smoke test instead (mirrors this repo's established pattern for DB/env-dependent code).


# --- ModelRegistry.is_configured -----------------------------------------------------------

def test_is_configured_true_for_a_plain_string_key():
    registry = ModelRegistry({"anthropic": "sk-ant-real", "openai": None, "gemini": ""})
    assert registry.is_configured("anthropic") is True
    assert registry.is_configured("openai") is False
    assert registry.is_configured("gemini") is False


def test_is_configured_for_bedrock_requires_region_not_the_key_pair():
    registry = ModelRegistry({"bedrock": {"access_key_id": None, "secret_access_key": None, "region": "us-east-1"}})
    assert registry.is_configured("bedrock") is True
    registry2 = ModelRegistry({"bedrock": {"access_key_id": "AKIA...", "secret_access_key": "x", "region": None}})
    assert registry2.is_configured("bedrock") is False
    registry3 = ModelRegistry({"bedrock": None})
    assert registry3.is_configured("bedrock") is False


def test_is_configured_for_gemini_enterprise_requires_project_id_not_service_account():
    registry = ModelRegistry({"gemini_enterprise": {"project_id": "demo", "service_account_json": None}})
    assert registry.is_configured("gemini_enterprise") is True
    registry2 = ModelRegistry({"gemini_enterprise": {"project_id": None, "service_account_json": "{}"}})
    assert registry2.is_configured("gemini_enterprise") is False


# --- auto_attempts --------------------------------------------------------------------------

def test_auto_attempts_returns_empty_when_nothing_is_configured():
    registry = ModelRegistry({})
    assert auto_attempts("backend", registry) == []


def test_auto_attempts_prefers_the_roles_own_curated_provider_when_configured():
    # "design" is curated onto Gemini in every preset — auto mode must not override that just
    # because Anthropic also happens to be configured; it goes first, matching the preset order.
    registry = ModelRegistry({"gemini": "AIza-real", "anthropic": "sk-ant-real"})
    attempts = auto_attempts("design", registry, preset="BALANCED")
    assert attempts[0] == ("gemini", "gemini-3.1-pro-preview")
    assert ("anthropic", "claude-sonnet-5") in attempts


def test_auto_attempts_falls_through_to_global_order_for_providers_outside_the_preset():
    # "backend" only ever uses Anthropic in every preset — with only an OpenAI key configured,
    # auto mode must still find and use it via the global fallback order, with a real default model.
    registry = ModelRegistry({"openai": "sk-real"})
    attempts = auto_attempts("backend", registry, preset="BALANCED")
    assert attempts == [("openai", "gpt-5.6-terra")]


def test_auto_attempts_skips_unconfigured_providers_entirely():
    registry = ModelRegistry({"anthropic": None, "gemini": "AIza-real", "openai": None,
                                "bedrock": None, "gemini_enterprise": None, "emergent": None})
    attempts = auto_attempts("backend", registry, preset="BALANCED")
    assert attempts == [("gemini", "gemini-3.1-pro-preview")]


def test_auto_attempts_never_duplicates_a_provider():
    # "reviewer" is curated onto OpenAI primary / Anthropic fallback — both also appear in the
    # global order, so the dedup logic must keep each provider exactly once.
    registry = ModelRegistry({"openai": "sk-real", "anthropic": "sk-ant-real"})
    attempts = auto_attempts("reviewer", registry, preset="BALANCED")
    providers = [p for p, _m in attempts]
    assert len(providers) == len(set(providers))
    assert providers[0] == "openai"


# --- runner._attempts_for -------------------------------------------------------------------

def test_attempts_for_explicit_mode_is_unchanged():
    config = _default_config("backend")
    config.auto_provider = False
    registry = ModelRegistry({})  # not consulted at all in explicit mode
    attempts = _attempts_for(config, registry)
    assert attempts == [(config.primary_provider, config.primary_model),
                          (config.fallback_provider, config.fallback_model)]


def test_attempts_for_auto_mode_cascades_over_configured_providers():
    config = AgentConfiguration(role="backend", auto_provider=True)
    registry = ModelRegistry({"gemini": "AIza-real"})
    attempts = _attempts_for(config, registry)
    assert attempts == [("gemini", "gemini-3.1-pro-preview")]


def test_attempts_for_auto_mode_raises_a_clear_error_when_nothing_is_configured():
    config = AgentConfiguration(role="backend", auto_provider=True)
    registry = ModelRegistry({})
    with pytest.raises(AgentStepFailed) as exc_info:
        _attempts_for(config, registry)
    assert exc_info.value.classification == "requires_credentials"
    assert "no provider has a credential configured" in str(exc_info.value)
