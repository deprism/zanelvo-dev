"""Deterministic tests for server.py::_status_for — the single place that maps a domain exception
to an HTTP status for every Dev Studio route (see server.py's "Global error mapping" comment).

Regression test: a real live-browser click-through (Playwright, real backend, no GitHub token
configured) found that connecting a repository surfaced a 502 "Bad Gateway" — implying GitHub's
API itself had failed — when the real cause was purely local: no token had been configured yet.
GitHubNotConfigured is a subclass of GitHubError, so it fell through into the same `GitHubError ->
502` bucket as a genuine upstream GitHub failure, instead of the `ProviderNotConfigured -> 424`
bucket the identical "not configured yet" case already gets for every other provider. No test
covered this mapping function at all before this file.
"""
import os

# server.py imports app.config at module load, which reads these eagerly (fail-loud, no default —
# see app/config.py) — no other test file imports server.py directly, so nothing else sets these.
os.environ.setdefault("JWT_SECRET", "test-secret-1234567890")
os.environ.setdefault("ADMIN_PASSWORD", "testpass123")

from app.devstudio.providers.base import ProviderError, ProviderNotConfigured, ProviderNotImplemented  # noqa: E402
from app.devstudio.services.git_service import GitError  # noqa: E402
from app.devstudio.services.github_provider import GitHubError, GitHubNotConfigured  # noqa: E402
from server import _status_for  # noqa: E402


def test_github_not_configured_is_a_config_gap_not_an_upstream_failure():
    assert _status_for(GitHubNotConfigured("no token")) == 424


def test_genuine_github_api_failure_stays_502():
    assert _status_for(GitHubError("GitHub API /repos/x/y -> 500: ...")) == 502


def test_genuine_git_failure_stays_502():
    assert _status_for(GitError("git clone failed")) == 502


def test_provider_not_configured_is_424():
    assert _status_for(ProviderNotConfigured("no anthropic key")) == 424


def test_provider_not_implemented_is_424():
    assert _status_for(ProviderNotImplemented("generate_with_tools not implemented")) == 424


def test_generic_provider_error_stays_502():
    assert _status_for(ProviderError("RATE_LIMIT", "rate limited")) == 502


def test_unmapped_exception_falls_back_to_500():
    assert _status_for(RuntimeError("something truly unexpected")) == 500
