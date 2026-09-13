"""GitService — the only place that shells out to the `git` CLI.

Every mutating call is scoped to a workspace directory that Dev Studio itself created under
`backend/var/devstudio/workspaces/` (never the running application's own working tree — this repo's
`.git` is never touched by Dev Studio). Never force-pushes by default (see `push()`).
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import List, Optional

from ...db import get_db
from ..models import GitOperation
from .proc_util import resolve_argv


class GitError(Exception):
    pass


@dataclass
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


def _is_within(base: str, target: str) -> bool:
    base = os.path.realpath(base)
    target = os.path.realpath(target)
    return target == base or target.startswith(base + os.sep)


async def _run(args: List[str], cwd: Optional[str], timeout: int = 120, env: Optional[dict] = None) -> CommandResult:
    proc = await asyncio.create_subprocess_exec(
        *resolve_argv(args), cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **(env or {})},
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return CommandResult(False, "", f"git command timed out after {timeout}s: {' '.join(args)}", -1)
    return CommandResult(proc.returncode == 0, out.decode(errors="replace"),
                          err.decode(errors="replace"), proc.returncode)


_SAFE_REF = re.compile(r"^[A-Za-z0-9._/\-]+$")


def assert_safe_ref(ref: str) -> None:
    """Refuse anything that isn't a plain branch/tag/sha-like token — never interpolate
    arbitrary strings into a git command (shell is never invoked, but a malicious ref like
    `--upload-pack=...` could still abuse git's own flag parsing)."""
    if not ref or not _SAFE_REF.match(ref) or ref.startswith("-"):
        raise GitError(f"Refusing unsafe git ref: {ref!r}")


class GitService:
    def __init__(self, task_id: Optional[str] = None):
        self.task_id = task_id

    async def _record(self, op: str, ok: bool, detail: str = "",
                       sha_before: Optional[str] = None, sha_after: Optional[str] = None) -> None:
        if not self.task_id:
            return
        try:
            doc = GitOperation(task_id=self.task_id, op=op, ok=ok, detail=detail[:2000],
                                sha_before=sha_before, sha_after=sha_after)
            await get_db().ds_git_operations.insert_one(doc.to_mongo())
        except Exception:  # noqa: BLE001 — logging a git op must never break the git op itself
            pass

    async def clone_mirror(self, remote_url: str, dest: str) -> CommandResult:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        res = await _run(["git", "clone", "--mirror", remote_url, dest], cwd=os.path.dirname(dest), timeout=600)
        await self._record("clone", res.ok, res.stderr[-500:])
        if not res.ok:
            raise GitError(f"git clone --mirror failed: {res.stderr}")
        return res

    async def update_mirror(self, mirror_path: str) -> CommandResult:
        res = await _run(["git", "remote", "update", "--prune"], cwd=mirror_path, timeout=300)
        await self._record("fetch", res.ok, res.stderr[-500:])
        return res

    async def clone_from_mirror(self, mirror_path: str, dest: str, branch: str, push_url: str) -> CommandResult:
        assert_safe_ref(branch)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        res = await _run(["git", "clone", "--branch", branch, "--origin", "origin", mirror_path, dest],
                          cwd=os.path.dirname(dest), timeout=300)
        if not res.ok:
            await self._record("clone", False, res.stderr[-500:])
            raise GitError(f"git clone (from mirror) failed: {res.stderr}")
        # Repoint origin at the real GitHub remote (authenticated) so push/fetch talk to GitHub,
        # not the local mirror — the mirror is purely a fast local cache.
        set_url = await _run(["git", "remote", "set-url", "origin", push_url], cwd=dest, timeout=30)
        await self._record("clone", set_url.ok, "cloned from local mirror; origin repointed to GitHub")
        return res

    async def create_and_checkout_branch(self, dest: str, branch: str, base_ref: str = "HEAD") -> CommandResult:
        assert_safe_ref(branch)
        assert_safe_ref(base_ref) if base_ref != "HEAD" else None
        res = await _run(["git", "checkout", "-b", branch, base_ref], cwd=dest, timeout=30)
        await self._record("branch", res.ok, f"{branch} from {base_ref}: {res.stderr[-300:]}")
        if not res.ok:
            raise GitError(f"git checkout -b failed: {res.stderr}")
        return res

    async def checkout(self, dest: str, ref: str) -> CommandResult:
        assert_safe_ref(ref)
        res = await _run(["git", "checkout", ref], cwd=dest, timeout=30)
        await self._record("checkout", res.ok, res.stderr[-300:])
        return res

    async def current_sha(self, dest: str) -> str:
        res = await _run(["git", "rev-parse", "HEAD"], cwd=dest, timeout=15)
        if not res.ok:
            raise GitError(f"git rev-parse failed: {res.stderr}")
        return res.stdout.strip()

    async def status(self, dest: str) -> str:
        res = await _run(["git", "status", "--porcelain=v1"], cwd=dest, timeout=15)
        return res.stdout

    async def changed_files(self, dest: str, base_ref: Optional[str] = None) -> List[str]:
        args = ["git", "diff", "--name-only"]
        if base_ref:
            assert_safe_ref(base_ref)
            args.append(base_ref)
        res = await _run(args, cwd=dest, timeout=20)
        files = [f for f in res.stdout.splitlines() if f.strip()]
        # include untracked (new) files too
        untracked = await _run(["git", "ls-files", "--others", "--exclude-standard"], cwd=dest, timeout=20)
        files += [f for f in untracked.stdout.splitlines() if f.strip()]
        return sorted(set(files))

    async def diff(self, dest: str, base_ref: Optional[str] = None, path: Optional[str] = None) -> str:
        args = ["git", "diff", "--no-color"]
        if base_ref:
            assert_safe_ref(base_ref)
            args.append(base_ref)
        args.append("--")
        if path:
            args.append(path)
        res = await _run(args, cwd=dest, timeout=30)
        return res.stdout

    async def add_all(self, dest: str) -> CommandResult:
        return await _run(["git", "add", "-A"], cwd=dest, timeout=30)

    async def add_intent(self, dest: str) -> CommandResult:
        """`git add -N .` — records new (untracked) files as "intent to add" so they show up in
        `git diff` as additions. Without this, a task that CREATES files (the whole-new-website
        case) produces an empty diff, because plain `git diff` only reports changes to already-
        tracked files. Non-destructive: it stages intent only, not content, so a later
        add_all + commit is unaffected."""
        return await _run(["git", "add", "-N", "."], cwd=dest, timeout=30)

    async def commit(self, dest: str, message: str, allow_empty: bool = False,
                       author: Optional[tuple] = None) -> CommandResult:
        await self.add_all(dest)
        args = ["git"]
        if author:
            # -c overrides apply to this invocation only (never touches the repo's or the
            # machine's git config) — a fresh machine that has never run `git config --global
            # user.name/email` would otherwise fail every commit with "Please tell me who you
            # are", which is exactly what this exists to prevent (see git_agent.py's caller).
            name, email = author
            args += ["-c", f"user.name={name}", "-c", f"user.email={email}"]
        args += ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        before = None
        try:
            before = await self.current_sha(dest)
        except GitError:
            pass
        res = await _run(args, cwd=dest, timeout=30)
        after = None
        if res.ok:
            after = await self.current_sha(dest)
        await self._record("commit", res.ok, res.stdout[-300:] or res.stderr[-300:], before, after)
        if not res.ok and "nothing to commit" not in res.stdout.lower():
            raise GitError(f"git commit failed: {res.stderr or res.stdout}")
        return res

    async def push(self, dest: str, branch: str, force: bool = False) -> CommandResult:
        """Never force-pushes unless explicitly requested AND never to a protected default
        branch — callers must never pass force=True for anything but a Dev Studio working
        branch the founder is iterating on."""
        assert_safe_ref(branch)
        if force:
            raise GitError("Force push is disabled in Dev Studio. Never force-push by default.")
        res = await _run(["git", "push", "-u", "origin", branch], cwd=dest, timeout=180)
        await self._record("push", res.ok, (res.stdout + res.stderr)[-500:])
        if not res.ok:
            raise GitError(f"git push failed: {res.stderr}")
        return res

    async def remote_head_sha(self, remote_or_path: str, branch: str) -> Optional[str]:
        assert_safe_ref(branch)
        # `git ls-remote` needs no particular working directory (every argument here is already an
        # absolute path/URL) — cwd=None (inherit the running process's cwd) instead of a hardcoded
        # "/tmp" avoids assuming a Unix-style temp directory exists, which it does not on the
        # Windows desktop build (NotADirectoryError: [WinError 267] the first time any task tried
        # to provision a workspace, since this is called from provision_task_workspace).
        res = await _run(["git", "ls-remote", remote_or_path, f"refs/heads/{branch}"], cwd=None, timeout=30)
        if not res.ok or not res.stdout.strip():
            return None
        return res.stdout.split()[0]

    async def log(self, dest: str, n: int = 20) -> List[dict]:
        res = await _run(["git", "log", f"-{n}", "--pretty=format:%H|%an|%ad|%s", "--date=iso"],
                          cwd=dest, timeout=20)
        out = []
        for line in res.stdout.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                out.append({"sha": parts[0], "author": parts[1], "date": parts[2], "message": parts[3]})
        return out

    async def reset_hard(self, dest: str, sha: str) -> CommandResult:
        """DESTRUCTIVE within the isolated task workspace only (never the app's own repo, never
        GitHub). Callers (checkpoint_service.restore) must require explicit confirmation first."""
        assert_safe_ref(sha)
        res = await _run(["git", "reset", "--hard", sha], cwd=dest, timeout=30)
        await self._record("reset", res.ok, f"reset --hard {sha}: {res.stderr[-200:]}")
        if not res.ok:
            raise GitError(f"git reset --hard failed: {res.stderr}")
        return res

    async def tag_checkpoint(self, dest: str, tag: str) -> CommandResult:
        assert_safe_ref(tag)
        res = await _run(["git", "tag", "-f", tag], cwd=dest, timeout=15)
        return res
