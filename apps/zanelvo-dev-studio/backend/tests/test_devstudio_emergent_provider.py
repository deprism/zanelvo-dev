"""Deterministic, network-free tests for EmergentUniversalKeyProvider — mirrors the pattern of
test_devstudio_registry.py (no DB, no live provider calls). Live round-trips are covered separately
by tests/manual_emergent_live.py, which needs a real Universal Key + network."""
import asyncio
import builtins

import pytest

from app.devstudio.providers.base import ProviderError, ProviderNotConfigured
from app.devstudio.providers.emergent_provider import (
    _CATALOG,
    _normalize_error,
    _sdk,
    EmergentUniversalKeyProvider,
)

_ALLOWED_FAMILIES = {"openai", "anthropic", "gemini"}


def test_list_models_non_empty_and_all_emergent():
    models = EmergentUniversalKeyProvider(None).list_models()
    assert models, "expected a static Universal Key catalog even before a key is configured"
    assert all(m.provider == "emergent" for m in models)
    # No duplicate model IDs (no fabricated/repeated entries).
    ids = [m.id for m in models]
    assert len(ids) == len(set(ids))


def test_catalog_families_are_real_providers():
    assert all(m["family"] in _ALLOWED_FAMILIES for m in _CATALOG)


def test_capabilities_are_reported_from_catalog_not_faked():
    p = EmergentUniversalKeyProvider(None)
    # A known vision model and a known non-reasoning model, straight from the catalog.
    assert p.supports_vision("gpt-6-astra") is True
    assert p.supports_reasoning_levels("gpt-5.4-mini") is False
    assert p.supports_reasoning_levels("claude-opus-5") is True
    # An unknown model reports no capabilities (never fabricates a True).
    assert p.supports_vision("not-a-real-model") is False


def test_missing_sdk_error_points_to_the_correct_requirements_file(monkeypatch):
    # Regression test: this error message used to (wrongly) tell founders to
    # `pip install -r requirements-devstudio.txt`, which deliberately does NOT include
    # emergentintegrations (see that file's own header — it conflicts with the native OpenAI
    # provider's pinned version) and so could never actually fix the problem.
    #
    # This must assert the same message whether or not emergentintegrations happens to be installed
    # in the runtime executing the tests (it IS installed when the Emergent Universal Key path is
    # actually in use). So force the ImportError path deterministically rather than depending on the
    # ambient install state.
    real_import = builtins.__import__

    def _fail_for_emergent(name, *args, **kwargs):
        if name == "litellm" or name.startswith("emergentintegrations"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_for_emergent)

    with pytest.raises(ProviderNotConfigured) as exc_info:
        _sdk()
    message = str(exc_info.value)
    assert "pip install -r requirements-emergent.txt" in message
    assert "pip install -r requirements-devstudio.txt" not in message


def test_generate_without_key_raises_not_configured():
    p = EmergentUniversalKeyProvider(None)
    with pytest.raises(ProviderNotConfigured):
        asyncio.run(p.generate(system="s", prompt="p", model="gpt-5.4"))


def test_unknown_model_maps_to_model_unavailable():
    # A key is present so we get past the credential check; the unknown-model guard fires before
    # any network call.
    p = EmergentUniversalKeyProvider(universal_key="sk-emergent-test-not-real")
    with pytest.raises(ProviderError) as ei:
        asyncio.run(p.generate(system="s", prompt="p", model="totally-unknown-model"))
    assert ei.value.code == "MODEL_UNAVAILABLE"


def test_vision_on_non_vision_model_is_invalid_request():
    p = EmergentUniversalKeyProvider(universal_key="sk-emergent-test-not-real")
    # gpt-5.4-mini supports vision in our catalog, so use an unknown id to force the guard:
    # supports_vision(unknown) is False → INVALID_REQUEST before any network call.
    with pytest.raises(ProviderError) as ei:
        asyncio.run(p.generate_with_vision(system="s", prompt="p", model="unknown-x",
                                           images_b64=["abc"]))
    assert ei.value.code == "INVALID_REQUEST"


def test_error_normalization_maps_known_categories():
    assert isinstance(_normalize_error(Exception("Invalid API key provided")), ProviderNotConfigured)
    assert isinstance(_normalize_error(Exception("401 Unauthorized")), ProviderNotConfigured)
    assert _normalize_error(Exception("429 rate limit exceeded")).code == "RATE_LIMIT"
    assert _normalize_error(Exception("maximum context length is 8000 tokens")).code == "CONTEXT_TOO_LARGE"
    assert _normalize_error(Exception("request timed out")).code == "PROVIDER_TIMEOUT"
    assert _normalize_error(Exception("model not found")).code == "MODEL_UNAVAILABLE"
    assert _normalize_error(Exception("some weird upstream blip")).code == "PROVIDER_ERROR"


def test_error_normalization_gives_the_real_fix_for_free_tier_external_access():
    # Regression test — reproduced live against Emergent's actual API with a real founder key that
    # WAS valid and DID have balance: a free-tier Universal Key is rejected for any use outside
    # Emergent's own platform (Emergent's own error code: FREE_USER_EXTERNAL_ACCESS_DENIED). The
    # generic "verify your key" message is actively misleading here, so this case must be detected
    # before the generic auth-rejected bucket and given its own, accurate message.
    real_error_text = (
        "litellm.APIError: APIError: OpenAIException - Error code: 403 - {'error': 'access_denied', "
        "'message': 'Free users can only use Universal Key from within Emergent platform. Please "
        "upgrade to paid plan for external access.', 'code': 'FREE_USER_EXTERNAL_ACCESS_DENIED', "
        "'upgrade_url': 'https://app.emergentagent.com/settings/billing'}"
    )
    err = _normalize_error(Exception(real_error_text))
    assert isinstance(err, ProviderNotConfigured)
    assert "plan-tier" in str(err)
    assert "billing" in str(err)
    assert "not an invalid key" in str(err)


def test_error_normalization_detects_insufficient_credit():
    for msg in ("insufficient balance", "402 Payment Required", "out of credit",
                "storage_quota_exceeded budget", "billing issue"):
        err = _normalize_error(Exception(msg))
        assert isinstance(err, ProviderError) and err.code == "INSUFFICIENT_CREDIT", msg


def test_provider_health_classify_exception():
    from app.devstudio.services.provider_health import classify_exception

    credit = ProviderError("INSUFFICIENT_CREDIT", "no balance")
    assert classify_exception(credit)[0] == "insufficient_credit"
    assert classify_exception(ProviderNotConfigured("bad key"))[0] == "auth_error"
    assert classify_exception(ProviderError("RATE_LIMIT", "slow down"))[0] == "error"
    assert classify_exception(ValueError("boom"))[0] == "error"


def test_normalized_errors_never_leak_the_key():
    # The raw text carries a secret-looking token; the normalized message must not echo it.
    fake_secret = "sk-emergent-FAKE0000deadbeef0000"
    err = _normalize_error(Exception(f"boom {fake_secret} internal detail"))
    assert fake_secret not in str(err)
    assert "internal detail" not in str(err)
