"""PreviewAdapter — three real modes, never a fake preview.

LIVE_LOCAL starts the repo's own dev server as a background process inside the task workspace and
health-checks the port. SCREENSHOT_ONLY builds/renders and hands off to BrowserTestingService for a
visual capture when a live port can't be exposed to the founder's browser (the common case for this
server-side agent). EXTERNAL_URL just records a URL the founder attached themselves.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import time
from dataclasses import dataclass
from typing import List, Optional

from .proc_util import resolve_argv

_processes: dict = {}  # task_id -> asyncio subprocess


def _kill_process_group(proc: "asyncio.subprocess.Process") -> None:
    """`npm run dev` spawns a shell, which spawns the actual dev server (vite/webpack/...) as a
    grandchild — proc.kill() only signals the immediate `npm` process, leaving the real dev server
    running and the port held open forever (confirmed live: `stop_live_local` was leaving an
    orphaned `vite` process on every single call, not just occasionally). The process is started
    in its own session (see start_new_session=True below) specifically so its pid doubles as its
    process-group id, letting us kill the whole tree with one signal."""
    # os.killpg doesn't exist at all on Windows (AttributeError, not OSError) — the desktop app
    # runs this backend directly on the founder's Windows machine, not just a Linux server, so this
    # can't be a POSIX-only best-effort call.
    if not hasattr(os, "killpg"):
        proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()  # process group already gone — best effort


@dataclass
class PreviewState:
    mode: str
    status: str          # "starting" | "running" | "stopped" | "unavailable" | "attached"
    url: Optional[str] = None
    detail: Optional[str] = None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _resolve_dev_command(cwd: str, port: int) -> Optional[List[str]]:
    """Picks the actual runnable dev-server command from this directory's real package.json —
    never assumes `npm start` exists. Vite (this app's own frontend, and the modern default for
    new React/Vue/etc. projects) defines a "dev" script, not "start", so a hardcoded `npm start`
    fails outright with "Missing script: start" on any Vite-scaffolded project, this repo's own
    frontend included — caught by actually reading this app's own frontend/package.json, not
    assumed. CRA/webpack-dev-server projects use "start" instead and read the `PORT` env var."""
    try:
        with open(os.path.join(cwd, "package.json")) as fh:
            scripts = json.load(fh).get("scripts", {})
    except (OSError, json.JSONDecodeError):
        return None
    if "dev" in scripts:
        # Vite and most modern dev servers accept --port directly; --strictPort makes a taken
        # port a hard failure instead of silently binding a different one than the URL we report.
        return ["npm", "run", "dev", "--", "--port", str(port), "--strictPort"]
    if "start" in scripts:
        return ["npm", "start"]
    return None


async def _wait_for_port(port: int, proc: "asyncio.subprocess.Process", timeout: float) -> bool:
    """Real health check: repeatedly attempts an actual TCP connection to the port, not a fixed
    sleep-then-assume. Returns False immediately if the process has already exited (no point
    waiting out the full timeout on a process that's already dead)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.returncode is not None:
            return False
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1.0)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return True
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.5)
    return False


async def start_live_local(task_id: str, workspace_path: str,
                             subdir: str = "frontend", timeout: float = 25.0) -> PreviewState:
    cwd = os.path.join(workspace_path, subdir) if subdir else workspace_path
    if not os.path.isdir(cwd):
        return PreviewState(mode="LIVE_LOCAL", status="unavailable",
                             detail=f"{cwd} does not exist in this workspace")
    port = _free_port()
    args = _resolve_dev_command(cwd, port)
    if args is None:
        return PreviewState(mode="LIVE_LOCAL", status="unavailable",
                             detail=f"No 'dev' or 'start' script found in {cwd}/package.json")
    try:
        proc = await asyncio.create_subprocess_exec(
            *resolve_argv(args), cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "PORT": str(port), "BROWSER": "none"},
            start_new_session=True,  # own process group — see _kill_process_group
        )
    except OSError as e:
        return PreviewState(mode="LIVE_LOCAL", status="unavailable", detail=str(e))
    _processes[task_id] = proc

    healthy = await _wait_for_port(port, proc, timeout=timeout)
    if not healthy:
        if proc.returncode is None:
            _kill_process_group(proc)
            with contextlib.suppress(ProcessLookupError):
                await proc.wait()
            detail = f"Dev server did not start listening on port {port} within {timeout:.0f}s"
        else:
            detail = f"Dev server process exited with code {proc.returncode}"
        _processes.pop(task_id, None)
        return PreviewState(mode="LIVE_LOCAL", status="unavailable", detail=detail)

    return PreviewState(mode="LIVE_LOCAL", status="running", url=f"http://127.0.0.1:{port}")


async def stop_live_local(task_id: str) -> bool:
    proc = _processes.pop(task_id, None)
    if not proc:
        return False
    _kill_process_group(proc)
    return True


def attach_external_url(url: str) -> PreviewState:
    return PreviewState(mode="EXTERNAL_URL", status="attached", url=url)


# Relative dirs (root first) checked for a self-contained static site — an index.html that can be
# served and iframed directly, no dev server or build step. This is the preview path that actually
# works behind this app's reverse proxy (LIVE_LOCAL binds a random localhost port the founder's
# browser can't reach through the ingress).
_STATIC_ROOT_CANDIDATES = ["", "public", "dist", "build", "frontend", "site", "www", "src"]


def find_static_root(workspace_path: str) -> Optional[str]:
    for rel in _STATIC_ROOT_CANDIDATES:
        candidate = os.path.join(workspace_path, rel) if rel else workspace_path
        if os.path.isfile(os.path.join(candidate, "index.html")):
            return rel
    return None


def screenshot_only_state(screenshot_path: Optional[str]) -> PreviewState:
    if screenshot_path:
        return PreviewState(mode="SCREENSHOT_ONLY", status="running", url=None, detail=screenshot_path)
    return PreviewState(mode="SCREENSHOT_ONLY", status="unavailable",
                         detail="No screenshot captured yet — run browser QA first")
