"""ExecutionService — runs allow-listed commands inside a task workspace with timeouts,
cancellation, and full stdout/stderr/exit-code capture, persisted as TestRun records.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import time
from typing import Dict, Optional

from ...db import get_db
from ..models import TestRun
from . import command_policy
from .proc_util import resolve_argv

_running: Dict[str, asyncio.subprocess.Process] = {}  # test_run_id -> process, for cancellation


class CommandBlocked(Exception):
    pass


def _tail(s: str, n: int = 4000) -> str:
    return s[-n:] if s else s


def _resolve_cwd(workspace_path: str, subdir: Optional[str]) -> str:
    """Never let a subdirectory (even one this app generated itself, e.g. from TestingProfile)
    resolve outside the workspace — same discipline as FileService's path-escape protection."""
    root = os.path.realpath(workspace_path)
    if not subdir:
        return root
    candidate = os.path.realpath(os.path.join(root, subdir))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise CommandBlocked(f"Refusing to run outside the workspace: {subdir!r}")
    return candidate


async def run_command(workspace_path: str, task_id: str, command: str, test_type: str,
                       cwd_subdir: Optional[str] = None, timeout: int = 300) -> TestRun:
    """`command` must be a plain, directly-executable command — no shell operators (`&&`, `|`,
    `;`) — since it runs via `create_subprocess_exec`, never a shell. Use `cwd_subdir` (relative to
    `workspace_path`) to run it in a subdirectory instead of baking `cd` into the command string,
    which would neither pass CommandPolicy nor actually execute."""
    decision = command_policy.evaluate(command)
    if not decision.allowed:
        raise CommandBlocked(decision.reason)
    cwd = _resolve_cwd(workspace_path, cwd_subdir)

    db = get_db()
    run = TestRun(task_id=task_id, test_type=test_type, command=command, status="running")
    res = await db.ds_test_runs.insert_one(run.to_mongo())
    run.id = str(res.inserted_id)

    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *resolve_argv(shlex.split(command)), cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CI": "true"},
        )
        _running[run.id] = proc
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            exit_code = proc.returncode
            status = "passed" if exit_code == 0 else "failed"
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            out, err = b"", f"Command timed out after {timeout}s".encode()
            exit_code = -1
            status = "error"
    finally:
        _running.pop(run.id, None)

    duration_ms = int((time.monotonic() - t0) * 1000)
    stdout_tail = _tail(out.decode(errors="replace") if isinstance(out, bytes) else out)
    stderr_tail = _tail(err.decode(errors="replace") if isinstance(err, bytes) else err)

    await db.ds_test_runs.update_one({"_id": res.inserted_id}, {"$set": {
        "status": status, "duration_ms": duration_ms, "exit_code": exit_code,
        "stdout_tail": stdout_tail, "stderr_tail": stderr_tail,
    }})
    run.status = status
    run.duration_ms = duration_ms
    run.exit_code = exit_code
    run.stdout_tail = stdout_tail
    run.stderr_tail = stderr_tail
    return run


async def cancel(test_run_id: str) -> bool:
    proc = _running.get(test_run_id)
    if not proc:
        return False
    proc.kill()
    return True


async def get_test_runs(task_id: str):
    docs = get_db().ds_test_runs.find({"task_id": task_id}).sort("created_at", -1)
    return [TestRun.from_mongo(d) async for d in docs]
