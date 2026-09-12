"""Deterministic tests for git_service — no real subprocess/network, mirrors the pattern of
test_devstudio_command_policy.py (mock the actual OS/subprocess boundary, run the real logic
around it).
"""
import asyncio

from app.devstudio.services import git_service


def test_remote_head_sha_does_not_hardcode_a_unix_temp_directory(monkeypatch):
    # Regression test: this used to hardcode cwd="/tmp", which does not exist on the Windows
    # desktop build (this backend runs directly on a founder's machine, not just a Linux server) —
    # reproduced live as NotADirectoryError: [WinError 267] on the very first task, since
    # provision_task_workspace calls this. `git ls-remote` needs no particular cwd at all (every
    # argument is already an absolute path/URL), so cwd=None (inherit) is the correct fix, not a
    # different hardcoded directory.
    captured = {}

    async def fake_run(args, cwd, timeout=120, env=None):
        captured["args"] = args
        captured["cwd"] = cwd
        return git_service.CommandResult(ok=True, stdout="abc123 refs/heads/main\n", stderr="", returncode=0)

    monkeypatch.setattr(git_service, "_run", fake_run)

    service = git_service.GitService()
    sha = asyncio.run(service.remote_head_sha("/some/mirror.git", "main"))

    assert sha == "abc123"
    assert captured["cwd"] is None
    assert captured["args"] == ["git", "ls-remote", "/some/mirror.git", "refs/heads/main"]


def test_remote_head_sha_returns_none_when_ref_not_found(monkeypatch):
    async def fake_run(args, cwd, timeout=120, env=None):
        return git_service.CommandResult(ok=True, stdout="", stderr="", returncode=0)

    monkeypatch.setattr(git_service, "_run", fake_run)
    service = git_service.GitService()
    sha = asyncio.run(service.remote_head_sha("/some/mirror.git", "does-not-exist"))
    assert sha is None
