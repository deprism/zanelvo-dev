"""Deterministic tests for gemini_search_service — the free/AI-provider-key web_search backend.
No network: the google-genai SDK layer (client + response shape) is faked, but the service's own
logic (key-presence check, error classification, citation extraction) runs for real.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.devstudio.services import gemini_search_service


def test_research_raises_not_configured_without_an_api_key():
    with pytest.raises(gemini_search_service.GeminiSearchNotConfigured, match="GEMINI_API_KEY"):
        asyncio.run(gemini_search_service.research("query", None))


def _fake_sdk(response, *, raise_error=None):
    class FakeGoogleSearch:
        pass

    class FakeTool:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_types = SimpleNamespace(GoogleSearch=FakeGoogleSearch, Tool=FakeTool,
                                   GenerateContentConfig=FakeConfig)

    captured = {}

    class FakeModels:
        async def generate_content(self, *, model, contents, config):
            captured.update(model=model, contents=contents, config=config)
            if raise_error:
                raise raise_error
            return response

    class FakeAio:
        models = FakeModels()

    class FakeClient:
        def __init__(self, api_key):
            captured["api_key"] = api_key
            self.aio = FakeAio()

    fake_genai = SimpleNamespace(Client=FakeClient)
    return fake_genai, fake_types, captured


def test_research_returns_grounded_text_with_sources(monkeypatch):
    web_chunk = SimpleNamespace(web=SimpleNamespace(uri="https://papermc.io/docs", title="PaperMC Docs"))
    candidate = SimpleNamespace(grounding_metadata=SimpleNamespace(grounding_chunks=[web_chunk]))
    response = SimpleNamespace(text="Paper 1.21 is the latest stable release.", candidates=[candidate])

    fake_genai, fake_types, captured = _fake_sdk(response)
    monkeypatch.setattr(gemini_search_service, "_sdk", lambda: (fake_genai, fake_types))

    result = asyncio.run(gemini_search_service.research("current Paper API version", "gm-real-key"))

    assert result == ("Paper 1.21 is the latest stable release.\n\nSources:\n"
                       "- PaperMC Docs: https://papermc.io/docs")
    assert captured["api_key"] == "gm-real-key"
    assert captured["contents"] == "current Paper API version"
    assert captured["model"] == gemini_search_service._DEFAULT_MODEL


def test_research_returns_text_only_when_no_grounding_chunks_present(monkeypatch):
    response = SimpleNamespace(text="No sources needed for this one.", candidates=[])
    fake_genai, fake_types, _captured = _fake_sdk(response)
    monkeypatch.setattr(gemini_search_service, "_sdk", lambda: (fake_genai, fake_types))

    result = asyncio.run(gemini_search_service.research("query", "gm-real-key"))
    assert result == "No sources needed for this one."


def test_research_classifies_an_invalid_key_as_not_configured(monkeypatch):
    fake_genai, fake_types, _captured = _fake_sdk(
        None, raise_error=Exception("400 API_KEY_INVALID. Bad key."))
    monkeypatch.setattr(gemini_search_service, "_sdk", lambda: (fake_genai, fake_types))

    with pytest.raises(gemini_search_service.GeminiSearchNotConfigured, match="rejected"):
        asyncio.run(gemini_search_service.research("query", "gm-bad-key"))


def test_research_raises_a_real_error_for_a_genuine_failure(monkeypatch):
    fake_genai, fake_types, _captured = _fake_sdk(
        None, raise_error=Exception("503 Service temporarily unavailable"))
    monkeypatch.setattr(gemini_search_service, "_sdk", lambda: (fake_genai, fake_types))

    with pytest.raises(gemini_search_service.GeminiSearchError, match="request failed"):
        asyncio.run(gemini_search_service.research("query", "gm-real-key"))
