"""Zanelvo Dev Studio — standalone FastAPI app entrypoint.

A private, single-user AI software-development environment. It has no customer/tenant concept —
see the app-level CLAUDE.md and README for what this app is and isn't.
"""
import logging
from pathlib import Path

from bson.errors import InvalidId
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from app import auth as auth_routes  # noqa: E402
from app import config  # noqa: E402
from app.devstudio import routes as devstudio_routes  # noqa: E402
from app.devstudio.db_indexes import ensure_indexes  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("devstudio")

app = FastAPI(
    title="Zanelvo Dev Studio",
    description="A private AI software-development environment.",
    version="0.1.0",
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(devstudio_routes.router)


# --- Global error mapping ---------------------------------------------------------------------
# Route handlers deliberately don't wrap every call in try/except: the domain exceptions raised by
# app/devstudio/services/* are specific and self-describing, so one place maps each to the right
# HTTP status instead of that boilerplate being repeated (or, worse, forgotten) per route. Anything
# not in this map still becomes a clean JSON 500 — FastAPI's default unhandled-exception response
# is plain text, which would break every frontend call site that reads `error.response.data.detail`.
def _status_for(exc: Exception) -> int:
    from app.devstudio.providers.base import ProviderError, ProviderNotConfigured, ProviderNotImplemented
    from app.devstudio.services.checkpoint_service import RestoreNotConfirmed
    from app.devstudio.services.execution_service import CommandBlocked
    from app.devstudio.services.file_service import PathEscapeError, StalePatchError
    from app.devstudio.services.git_service import GitError
    from app.devstudio.services.github_provider import GitHubError, GitHubNotConfigured
    from app.devstudio.services.upload_service import UploadRejected
    from app.devstudio.state_machine import InvalidTransition

    if isinstance(exc, InvalidId):
        return 400
    if isinstance(exc, PathEscapeError):
        return 400
    if isinstance(exc, StalePatchError):
        return 409
    if isinstance(exc, RestoreNotConfirmed):
        return 409
    if isinstance(exc, InvalidTransition):
        return 400
    if isinstance(exc, (CommandBlocked, UploadRejected)):
        return 400
    if isinstance(exc, (ProviderNotConfigured, ProviderNotImplemented, GitHubNotConfigured)):
        return 424  # Failed Dependency — a configuration gap, not a server bug
    if isinstance(exc, ProviderError):
        return 502  # a normalized upstream provider failure (rate limit, timeout, etc.)
    if isinstance(exc, (GitError, GitHubError)):
        return 502  # the upstream git/GitHub operation failed
    if isinstance(exc, (ValueError, FileNotFoundError, FileExistsError)):
        return 400
    return 500


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    status = _status_for(exc)
    if status == 500:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    return JSONResponse({"detail": str(exc)[:500]}, status_code=status)


@app.get("/api/health")
async def health():
    from app.db import get_db
    out = {"ok": True, "env": config.APP_ENV}
    try:
        await get_db().command("ping")
        out["db"] = {"ok": True}
    except Exception as e:  # noqa: BLE001
        out["ok"] = False
        out["db"] = {"ok": False, "error": str(e)[:200]}
    return out


@app.on_event("startup")
async def _on_startup():
    if config.is_production() and len(config.JWT_SECRET) < 32:
        raise RuntimeError("JWT_SECRET must be a strong (>=32 char) secret in production")
    try:
        await ensure_indexes()
    except Exception as e:  # noqa: BLE001
        logger.warning("Index creation failed (will retry on next call that needs them): %s", e)


@app.on_event("shutdown")
async def _on_shutdown():
    from app.db import get_client
    get_client().close()
