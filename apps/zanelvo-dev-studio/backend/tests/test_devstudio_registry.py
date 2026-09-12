"""Deterministic tests for the multi-provider ModelRegistry, plus BedrockProvider's and
GeminiEnterpriseProvider's pure/sync config-validation paths. No DB/network — mirrors the pattern
in test_devstudio_execution_service.py.
"""
from app.devstudio.providers.base import ProviderNotConfigured
from app.devstudio.providers.bedrock_provider import BedrockProvider
from app.devstudio.providers.gemini_enterprise_provider import GeminiEnterpriseProvider
from app.devstudio.providers.registry import MODEL_PRESETS, known_provider_names, preset_for_role

_CLOUD_ROUTED_PROVIDERS = ("bedrock", "gemini_enterprise", "emergent")


def test_known_provider_names_includes_every_provider():
    names = known_provider_names()
    assert set(names) == {"anthropic", "openai", "gemini", "gemini_enterprise", "bedrock", "emergent"}


def test_cloud_routed_providers_not_used_by_any_preset_by_default():
    # Bedrock/Gemini Enterprise/Emergent are opt-in per-role overrides (Settings > Agents), not
    # preset defaults — nothing in MODEL_PRESETS should silently route through them.
    for preset in MODEL_PRESETS.values():
        for cfg in preset.values():
            assert cfg["primary_provider"] not in _CLOUD_ROUTED_PROVIDERS
            assert cfg["fallback_provider"] not in _CLOUD_ROUTED_PROVIDERS


def test_preset_for_role_unaffected_by_bedrock_addition():
    p = preset_for_role("MAX_QUALITY", "backend")
    assert p["primary_provider"] == "anthropic"
    assert p["primary_model"] == "claude-opus-5"


def test_bedrock_list_models_works_without_any_credentials():
    models = BedrockProvider(None).list_models()
    assert models, "expected a static model list even with no credentials configured"
    assert all(m.provider == "bedrock" for m in models)
    assert all(m.id.startswith("anthropic.") for m in models)


def test_bedrock_does_not_list_fable_without_verified_availability():
    ids = {m.id for m in BedrockProvider(None).list_models()}
    assert not any("fable" in i for i in ids)


def test_bedrock_client_requires_a_region():
    provider = BedrockProvider({"access_key_id": "AKIA_FAKE", "secret_access_key": "fake"})
    try:
        provider._client()
        assert False, "expected ProviderNotConfigured when no region is configured"
    except ProviderNotConfigured as e:
        assert "region" in str(e).lower()


def test_bedrock_client_requires_region_even_with_empty_credentials_dict():
    provider = BedrockProvider({})
    try:
        provider._client()
        assert False, "expected ProviderNotConfigured when no region is configured"
    except ProviderNotConfigured:
        pass


def test_gemini_enterprise_list_models_works_without_any_credentials():
    models = GeminiEnterpriseProvider(None).list_models()
    assert models, "expected a static model list even with no credentials configured"
    assert all(m.provider == "gemini_enterprise" for m in models)
    # Both real model families this provider serves: Gemini (via google-genai) and Claude (via
    # AnthropicVertex) — see gemini_enterprise_provider.py's module docstring for why Claude models
    # are genuinely available on the same GCP-hosted Agent Platform.
    assert any(m.id.startswith("gemini-") for m in models)
    assert any(m.id.startswith("claude-") for m in models)


def test_gemini_enterprise_claude_models_are_correctly_flagged():
    models = GeminiEnterpriseProvider(None).list_models()
    claude_models = [m for m in models if m.id.startswith("claude-")]
    assert {m.id for m in claude_models} == {
        "claude-fable-5-1", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5@20251001",
    }
    assert all(m.supports_tools and m.supports_vision for m in claude_models)


def test_gemini_enterprise_client_requires_a_project():
    provider = GeminiEnterpriseProvider({"location": "us-central1"})
    try:
        provider._client()
        assert False, "expected ProviderNotConfigured when no GCP project is configured"
    except ProviderNotConfigured as e:
        assert "project" in str(e).lower()


def test_gemini_enterprise_client_requires_project_even_with_empty_credentials_dict():
    provider = GeminiEnterpriseProvider({})
    try:
        provider._client()
        assert False, "expected ProviderNotConfigured when no GCP project is configured"
    except ProviderNotConfigured:
        pass


def test_gemini_enterprise_rejects_invalid_service_account_json():
    provider = GeminiEnterpriseProvider({"project_id": "demo-project", "service_account_json": "not json"})
    try:
        provider._client()
        assert False, "expected ProviderNotConfigured for invalid service account JSON"
    except ProviderNotConfigured as e:
        assert "json" in str(e).lower()


def test_gemini_enterprise_claude_vertex_client_requires_a_project():
    provider = GeminiEnterpriseProvider({})
    try:
        provider._claude_vertex_client()
        assert False, "expected ProviderNotConfigured when no GCP project is configured"
    except ProviderNotConfigured as e:
        assert "project" in str(e).lower()


def test_gemini_enterprise_claude_vertex_client_rejects_invalid_service_account_json():
    provider = GeminiEnterpriseProvider({"project_id": "demo-project", "service_account_json": "not json"})
    try:
        provider._claude_vertex_client()
        assert False, "expected ProviderNotConfigured for invalid service account JSON"
    except ProviderNotConfigured as e:
        assert "json" in str(e).lower()


def test_gemini_enterprise_claude_vertex_client_defaults_region_to_global():
    provider = GeminiEnterpriseProvider({"project_id": "demo-project"})
    client = provider._claude_vertex_client()
    assert client.region == "global"
    assert client.project_id == "demo-project"


def test_gemini_enterprise_claude_vertex_client_honors_a_configured_region():
    provider = GeminiEnterpriseProvider({"project_id": "demo-project", "claude_region": "us-east5"})
    client = provider._claude_vertex_client()
    assert client.region == "us-east5"


def test_gemini_enterprise_dispatches_claude_models_to_the_vertex_adapter(monkeypatch):
    # generate()/generate_structured()/generate_with_vision()/stream()/generate_with_tools() must
    # all route a Claude model id to the AnthropicVertex-backed adapter, never the Gemini path —
    # this is the actual "make gemini enterprise have claude models" fix.
    import asyncio

    provider = GeminiEnterpriseProvider({"project_id": "demo-project"})
    captured = {}

    class _FakeAdapter:
        async def generate(self, **kwargs):
            captured["generate"] = kwargs
            return "claude-generate-result"

        async def generate_structured(self, **kwargs):
            captured["generate_structured"] = kwargs
            return "claude-structured-result"

        async def generate_with_vision(self, **kwargs):
            captured["generate_with_vision"] = kwargs
            return "claude-vision-result"

        async def stream(self, **kwargs):
            captured["stream"] = kwargs
            yield "chunk-1"
            yield "chunk-2"

        async def generate_with_tools(self, **kwargs):
            captured["generate_with_tools"] = kwargs
            return "claude-tools-result"

    monkeypatch.setattr(provider, "_claude_adapter", lambda: _FakeAdapter())

    result = asyncio.run(provider.generate(system="s", prompt="p", model="claude-sonnet-5"))
    assert result == "claude-generate-result"
    assert captured["generate"]["model"] == "claude-sonnet-5"

    result = asyncio.run(provider.generate_structured(system="s", prompt="p", model="claude-opus-5"))
    assert result == "claude-structured-result"

    result = asyncio.run(provider.generate_with_vision(
        system="s", prompt="p", model="claude-fable-5-1", images_b64=["abc"]))
    assert result == "claude-vision-result"

    async def _collect_stream():
        return [c async for c in provider.stream(system="s", prompt="p", model="claude-sonnet-5")]

    assert asyncio.run(_collect_stream()) == ["chunk-1", "chunk-2"]

    result = asyncio.run(provider.generate_with_tools(
        system="s", model="claude-haiku-4-5@20251001", tools=[]))
    assert result == "claude-tools-result"


def test_gemini_enterprise_does_not_route_gemini_models_through_the_claude_adapter(monkeypatch):
    provider = GeminiEnterpriseProvider({"project_id": "demo-project"})

    def _fail_if_called():
        raise AssertionError("Gemini models must not be routed through the Claude adapter")

    monkeypatch.setattr(provider, "_claude_adapter", _fail_if_called)
    try:
        import asyncio
        asyncio.run(provider.generate_with_tools(system="s", model="gemini-3.6-flash", tools=[]))
        assert False, "expected the base class's ProviderNotConfigured for a Gemini model"
    except ProviderNotConfigured as e:
        assert "gemini_enterprise" in str(e)
