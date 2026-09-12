"""SettingsService + SecretsService.

Secrets (GitHub PAT, provider API keys) are stored encrypted-at-rest in Mongo using the app's
existing `services/secretbox.py` (Fernet), never in plaintext, never logged, never returned to the
client in `read()` responses (only `configured: true/false`).
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from ...db import get_db, utc_now_iso
from ...services import secretbox
from ..models import ApplicationSettings
from ..providers.registry import ModelRegistry

_SECRET_KEYS = ("github_pat", "anthropic_api_key", "openai_api_key", "gemini_api_key",
                 "emergent_universal_key", "aws_access_key_id", "aws_secret_access_key",
                 "aws_session_token", "aws_region", "gcp_project_id", "gcp_location",
                 "gcp_claude_region", "gcp_service_account_json", "perplexity_api_key")


async def get_settings() -> ApplicationSettings:
    db = get_db()
    doc = await db.ds_settings.find_one({"key": "singleton"})
    if not doc:
        s = ApplicationSettings()
        await db.ds_settings.insert_one(s.to_mongo())
        return s
    return ApplicationSettings.from_mongo(doc)


async def update_settings(**fields: Any) -> ApplicationSettings:
    db = get_db()
    fields["updated_at"] = utc_now_iso()
    await db.ds_settings.update_one({"key": "singleton"}, {"$set": fields}, upsert=True)
    return await get_settings()


async def set_secret(name: str, value: str) -> None:
    if name not in _SECRET_KEYS:
        raise ValueError(f"Unknown secret: {name}")
    db = get_db()
    await db.ds_secrets.update_one(
        {"key": "singleton"},
        {"$set": {f"{name}_enc": secretbox.encrypt(value), "updated_at": utc_now_iso()}},
        upsert=True,
    )


async def get_secret(name: str) -> Optional[str]:
    """Resolution order: environment variable (deploy-time override) then encrypted DB value."""
    env_map = {
        "github_pat": "DEVSTUDIO_GITHUB_TOKEN",
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "openai_api_key": "OPENAI_API_KEY",
        "gemini_api_key": "GEMINI_API_KEY",
        "emergent_universal_key": "EMERGENT_UNIVERSAL_KEY",
        "aws_access_key_id": "AWS_ACCESS_KEY_ID",
        "aws_secret_access_key": "AWS_SECRET_ACCESS_KEY",
        "aws_session_token": "AWS_SESSION_TOKEN",
        "aws_region": "AWS_REGION",
        "gcp_project_id": "GOOGLE_CLOUD_PROJECT",
        "gcp_location": "GOOGLE_CLOUD_LOCATION",
        # Claude-on-Vertex's own region namespace ("global"/"us"/"eu"/a specific region) — distinct
        # from gcp_location/GOOGLE_CLOUD_LOCATION, which is Gemini's. See gemini_enterprise_provider.
        "gcp_claude_region": "ANTHROPIC_VERTEX_REGION",
        # Not the real GOOGLE_APPLICATION_CREDENTIALS (a file path, already read automatically by
        # google-auth's ADC chain) — this is Dev Studio's own name for the raw JSON key contents,
        # for parity with how every other secret here is set via an env var override.
        "gcp_service_account_json": "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "perplexity_api_key": "PERPLEXITY_API_KEY",
    }
    env_val = os.environ.get(env_map.get(name, ""), "")
    if env_val:
        return env_val
    db = get_db()
    doc = await db.ds_secrets.find_one({"key": "singleton"})
    if not doc:
        return None
    enc = doc.get(f"{name}_enc")
    return secretbox.decrypt(enc) if enc else None


async def secrets_status() -> Dict[str, bool]:
    return {name: bool(await get_secret(name)) for name in _SECRET_KEYS}


async def build_model_registry() -> ModelRegistry:
    """Assemble a ModelRegistry with whatever provider credentials are currently configured.

    Most providers take a single API key string. Bedrock and Gemini Enterprise Agent Platform use
    their cloud vendor's own multi-field credential model instead, so their registry values are
    small dicts rather than a string — BedrockProvider and GeminiEnterpriseProvider know how to
    consume those shapes, and both fall back to their vendor's own default credential chain when
    the explicit fields aren't set (see the two provider modules).
    """
    keys: Dict[str, Any] = {
        "anthropic": await get_secret("anthropic_api_key"),
        "openai": await get_secret("openai_api_key"),
        "gemini": await get_secret("gemini_api_key"),
        "emergent": await get_secret("emergent_universal_key"),
        "bedrock": {
            "access_key_id": await get_secret("aws_access_key_id"),
            "secret_access_key": await get_secret("aws_secret_access_key"),
            "session_token": await get_secret("aws_session_token"),
            "region": await get_secret("aws_region"),
        },
        "gemini_enterprise": {
            "project_id": await get_secret("gcp_project_id"),
            "location": await get_secret("gcp_location"),
            "claude_region": await get_secret("gcp_claude_region"),
            "service_account_json": await get_secret("gcp_service_account_json"),
        },
    }
    return ModelRegistry(keys)
