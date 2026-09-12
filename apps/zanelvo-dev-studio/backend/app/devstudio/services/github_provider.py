"""GitHubProvider — thin wrapper over the GitHub REST API (httpx), authenticated with a
founder-supplied Personal Access Token stored via SettingsService/SecretsService.

This is separate from — and does not read — any GitHub credential belonging to the outer Claude
Code session/container. Dev Studio only trusts a token the founder explicitly configured through
its own Settings screen, matching "Secrets remain server-side" / no implicit credential reuse.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from . import settings_service

_API = "https://api.github.com"


class GitHubError(Exception):
    pass


class GitHubNotConfigured(GitHubError):
    pass


async def _token() -> str:
    tok = await settings_service.get_secret("github_pat")
    if not tok:
        raise GitHubNotConfigured(
            "No GitHub token configured. Set one via POST /api/devstudio/settings/secrets "
            "{\"name\": \"github_pat\", \"value\": \"<token>\"}."
        )
    return tok


async def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {await _token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "zanelvo-devstudio",
    }


async def _get(path: str, params: Optional[dict] = None) -> Any:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{_API}{path}", headers=await _headers(), params=params)
    if r.status_code == 401:
        raise GitHubError("GitHub token rejected (401) — it may be expired or lack scope.")
    if r.status_code >= 400:
        raise GitHubError(f"GitHub API {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


async def _post(path: str, json_body: dict) -> Any:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{_API}{path}", headers=await _headers(), json=json_body)
    if r.status_code >= 400:
        raise GitHubError(f"GitHub API POST {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


async def whoami() -> Dict[str, Any]:
    return await _get("/user")


async def list_repos(limit: int = 50) -> List[Dict[str, Any]]:
    data = await _get("/user/repos", params={"per_page": min(limit, 100), "sort": "updated"})
    return [{"owner": r["owner"]["login"], "repo": r["name"], "full_name": r["full_name"],
              "private": r["private"], "default_branch": r["default_branch"],
              "updated_at": r["updated_at"]} for r in data]


async def create_repo(name: str, *, private: bool = True, description: Optional[str] = None,
                       auto_init: bool = True) -> Dict[str, Any]:
    """Creates a brand-new repository under the authenticated user's own account (POST /user/repos
    — org-owned creation would need a different endpoint/scope, out of scope here). `auto_init`
    defaults to True: an uninitialized repo has no commits/branches at all, which every other
    piece of this app (default_branch resolution, cloning, indexing) assumes exists — a repo
    that's usable the instant it's created, not an empty shell the founder has to fix first."""
    body: Dict[str, Any] = {"name": name, "private": private, "auto_init": auto_init}
    if description:
        body["description"] = description
    data = await _post("/user/repos", body)
    return {"owner": data["owner"]["login"], "repo": data["name"], "full_name": data["full_name"],
             "private": data["private"], "default_branch": data.get("default_branch") or "main",
             "html_url": data["html_url"]}


async def get_repo(owner: str, repo: str) -> Dict[str, Any]:
    r = await _get(f"/repos/{owner}/{repo}")
    return {"owner": owner, "repo": repo, "default_branch": r["default_branch"],
             "private": r["private"], "html_url": r["html_url"]}


async def list_branches(owner: str, repo: str) -> List[Dict[str, Any]]:
    data = await _get(f"/repos/{owner}/{repo}/branches", params={"per_page": 100})
    return [{"name": b["name"], "sha": b["commit"]["sha"], "protected": b.get("protected", False)}
            for b in data]


async def compare(owner: str, repo: str, base: str, head: str) -> Dict[str, Any]:
    data = await _get(f"/repos/{owner}/{repo}/compare/{base}...{head}")
    return {"ahead_by": data["ahead_by"], "behind_by": data["behind_by"], "status": data["status"]}


async def create_pull_request(owner: str, repo: str, title: str, body: str,
                               head: str, base: str) -> Dict[str, Any]:
    data = await _post(f"/repos/{owner}/{repo}/pulls",
                        {"title": title, "body": body, "head": head, "base": base})
    return {"number": data["number"], "html_url": data["html_url"], "state": data["state"]}


async def list_workflow_runs(owner: str, repo: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Recent GitHub Actions runs for this repo — real CI/deployment status, for the
    deployment_debugger agent tool (agents/runner.py)."""
    data = await _get(f"/repos/{owner}/{repo}/actions/runs", params={"per_page": min(limit, 100)})
    return [{"id": r["id"], "name": r["name"], "status": r["status"], "conclusion": r["conclusion"],
              "head_branch": r["head_branch"], "head_sha": r["head_sha"][:7],
              "html_url": r["html_url"], "created_at": r["created_at"]}
             for r in data.get("workflow_runs", [])]


async def get_workflow_run_jobs(owner: str, repo: str, run_id: int) -> List[Dict[str, Any]]:
    """Per-job, per-step status for one workflow run — real, structured, and usually enough to see
    exactly which step failed without needing the raw log text (which GitHub serves as a binary
    blob download via a separate endpoint, out of scope here)."""
    data = await _get(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")
    return [{"id": j["id"], "name": j["name"], "status": j["status"], "conclusion": j["conclusion"],
              "steps": [{"name": s["name"], "status": s["status"], "conclusion": s["conclusion"]}
                         for s in j.get("steps", [])]}
             for j in data.get("jobs", [])]


async def authenticated_clone_url(owner: str, repo: str) -> str:
    tok = await _token()
    return f"https://x-access-token:{tok}@github.com/{owner}/{repo}.git"


async def connectivity_check() -> Dict[str, Any]:
    try:
        me = await whoami()
        return {"available": True, "detail": f"authenticated as {me.get('login')}"}
    except GitHubNotConfigured as e:
        return {"available": False, "detail": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "detail": f"{type(e).__name__}: {e}"}
