"""GeminiSearchService — real Gemini API call using native Google Search grounding
(`types.Tool(google_search=types.GoogleSearch())`), the free/AI-provider-key alternative to
Perplexity for the `web_search` built-in agent tool (see agents/runner.py). Reuses the same
`google-genai` SDK `GeminiProvider` already depends on (requirements-devstudio.txt) — no new
dependency.

Why this exists: `web_search`'s only prior non-Anthropic fallback required a separate paid
Perplexity key — neither free nor one of this app's model-provider keys. A Gemini API key has a
genuine free tier (ai.google.dev) and is already one of the five model-provider keys (Anthropic/
OpenAI/Gemini/Bedrock/Emergent) a founder may already be setting for agent roles, so it satisfies
"free or already-an-AI-key" directly instead of adding a sixth, search-only credential.

Classification note (CLAUDE.md evidence rules): "Live but requires external credentials" until
GEMINI_API_KEY (or the stored `gemini_api_key` secret) is configured — calling it without one
raises GeminiSearchNotConfigured rather than returning fake output.
"""
from __future__ import annotations

from typing import Optional

_DEFAULT_MODEL = "gemini-3.6-flash"


class GeminiSearchNotConfigured(Exception):
    pass


class GeminiSearchError(Exception):
    pass


def _sdk():
    try:
        from google import genai
        from google.genai import types
        return genai, types
    except ImportError as e:  # noqa: BLE001
        raise GeminiSearchNotConfigured(
            "The 'google-genai' package is not installed in this environment. Install it via: "
            "pip install -r requirements-devstudio.txt"
        ) from e


async def research(query: str, api_key: Optional[str], model: str = _DEFAULT_MODEL) -> str:
    if not api_key:
        raise GeminiSearchNotConfigured(
            "Gemini web search requires GEMINI_API_KEY (env) or the stored gemini_api_key "
            "secret — neither is configured."
        )
    genai, types = _sdk()
    client = genai.Client(api_key=api_key)
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
    except Exception as e:  # noqa: BLE001 - real SDK/network error, surfaced rather than swallowed
        message = str(e)
        if "API_KEY_INVALID" in message or "401" in message or "403" in message:
            raise GeminiSearchNotConfigured(
                f"Gemini rejected the configured API key: {type(e).__name__}: {e}"
            ) from e
        raise GeminiSearchError(f"Gemini search request failed: {type(e).__name__}: {e}") from e

    text = resp.text or ""
    sources = []
    candidates = getattr(resp, "candidates", None) or []
    grounding = getattr(candidates[0], "grounding_metadata", None) if candidates else None
    for chunk in (getattr(grounding, "grounding_chunks", None) or []):
        web = getattr(chunk, "web", None)
        uri = getattr(web, "uri", None) if web else None
        if uri:
            title = getattr(web, "title", None) or uri
            sources.append(f"- {title}: {uri}")
    if sources:
        text += "\n\nSources:\n" + "\n".join(sources)
    return text
