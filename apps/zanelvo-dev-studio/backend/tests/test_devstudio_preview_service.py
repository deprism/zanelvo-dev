"""Deterministic tests for PreviewService's dev-command resolution and port health-check —
covers a real, previously-unverified bug: LIVE_LOCAL always ran `npm start`, which fails outright
on any Vite-scaffolded project (a "dev" script, not "start") including this app's own frontend
(confirmed by reading apps/zanelvo-dev-studio/frontend/package.json, which has no "start" script
at all), and the injected PORT env var alone doesn't make Vite listen there (it hardcodes its port
in vite.config.ts unless given --port). Also covers a second real bug found while verifying the
first one live: killing the `npm run dev` process alone left its `vite` grandchild running forever,
still holding the port — every `stop_live_local` call was leaking an orphaned dev server, not just
occasionally. No DB. `_wait_for_port` and `_kill_process_group` are tested against real loopback
sockets / a real (tiny, fast) process tree rather than a live npm process — no external network.
"""
import asyncio
import json
import os

from app.devstudio.services.preview_service import (
    _free_port,
    _kill_process_group,
    _resolve_dev_command,
    _wait_for_port,
    start_live_local,
)


def _write_pkg(path, scripts):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"scripts": scripts}, fh)


def test_resolve_dev_command_prefers_vite_style_dev_script(tmp_path):
    _write_pkg(tmp_path / "package.json", {"dev": "vite", "start": "should-not-be-used"})
    args = _resolve_dev_command(str(tmp_path), 54321)
    assert args == ["npm", "run", "dev", "--", "--port", "54321", "--strictPort"]


def test_resolve_dev_command_falls_back_to_cra_style_start_script(tmp_path):
    _write_pkg(tmp_path / "package.json", {"start": "react-scripts start"})
    assert _resolve_dev_command(str(tmp_path), 54321) == ["npm", "start"]


def test_resolve_dev_command_none_when_neither_script_exists(tmp_path):
    _write_pkg(tmp_path / "package.json", {"build": "vite build"})
    assert _resolve_dev_command(str(tmp_path), 54321) is None


def test_resolve_dev_command_none_when_no_package_json(tmp_path):
    assert _resolve_dev_command(str(tmp_path), 54321) is None


def test_this_apps_own_frontend_uses_the_vite_dev_script_not_start():
    # The exact bug this round fixed: the old hardcoded `npm start` default would have failed
    # immediately against this repo's own frontend, which has no "start" script.
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../backend
    frontend_dir = os.path.join(os.path.dirname(backend_dir), "frontend")      # .../frontend
    args = _resolve_dev_command(frontend_dir, 54321)
    assert args is not None and args[:3] == ["npm", "run", "dev"]


def test_start_live_local_reports_a_clear_reason_when_no_dev_script_exists(tmp_path):
    (tmp_path / "frontend").mkdir()
    _write_pkg(tmp_path / "frontend" / "package.json", {"build": "vite build"})
    state = asyncio.run(start_live_local("t1", str(tmp_path), subdir="frontend"))
    assert state.status == "unavailable"
    assert "dev" in state.detail and "start" in state.detail


def test_start_live_local_reports_unavailable_when_subdir_missing(tmp_path):
    state = asyncio.run(start_live_local("t1", str(tmp_path), subdir="frontend"))
    assert state.status == "unavailable"
    assert "does not exist" in state.detail


class _FakeProc:
    returncode = None


def test_wait_for_port_detects_a_real_listening_server():
    async def scenario():
        port = _free_port()
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", port)
        try:
            healthy = await _wait_for_port(port, _FakeProc(), timeout=3.0)
            assert healthy is True
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_wait_for_port_times_out_when_nothing_is_listening():
    async def scenario():
        port = _free_port()  # bound-and-released; nothing listens on it
        healthy = await _wait_for_port(port, _FakeProc(), timeout=1.0)
        assert healthy is False

    asyncio.run(scenario())


def test_wait_for_port_returns_false_immediately_if_the_process_already_exited():
    class _DeadProc:
        returncode = 1

    async def scenario():
        port = _free_port()
        t0 = asyncio.get_event_loop().time()
        healthy = await _wait_for_port(port, _DeadProc(), timeout=10.0)
        elapsed = asyncio.get_event_loop().time() - t0
        assert healthy is False
        assert elapsed < 1.0, "should not wait out the full timeout on an already-dead process"

    asyncio.run(scenario())


def test_free_port_returns_a_usable_port_number():
    for _ in range(3):
        assert 1024 < _free_port() < 65536


def test_kill_process_group_kills_grandchildren_too(tmp_path):
    # Simulates the real npm -> shell -> vite chain: a process that itself spawns a detached
    # background grandchild. Killing only the top-level process (the old behavior) would leave
    # the grandchild running — exactly the orphaned-dev-server bug found live this round.
    pid_file = tmp_path / "child.pid"

    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", f"sleep 30 & echo $! > {pid_file}; wait",
            start_new_session=True)
        for _ in range(20):
            if pid_file.exists() and pid_file.read_text().strip():
                break
            await asyncio.sleep(0.1)
        grandchild_pid = int(pid_file.read_text().strip())
        assert os.path.exists(f"/proc/{grandchild_pid}"), "grandchild should be alive before the kill"

        _kill_process_group(proc)
        await asyncio.wait_for(proc.wait(), timeout=3)
        for _ in range(20):
            if not os.path.exists(f"/proc/{grandchild_pid}"):
                break
            await asyncio.sleep(0.1)
        assert not os.path.exists(f"/proc/{grandchild_pid}"), \
            "grandchild should be dead too — not just the direct child"

    asyncio.run(scenario())


def test_kill_process_group_falls_back_on_windows_where_killpg_does_not_exist(monkeypatch):
    # Regression test: os.killpg raises AttributeError on Windows (it doesn't exist there at all,
    # unlike the ProcessLookupError/PermissionError/OSError this function already handled) — the
    # desktop app runs this backend directly on a founder's Windows machine, so an uncaught
    # AttributeError here would crash stop_live_local outright instead of falling back to
    # proc.kill().
    monkeypatch.delattr(os, "killpg", raising=False)
    killed = {"called": False}

    class _FakeProc:
        pid = 12345
        def kill(self):
            killed["called"] = True

    _kill_process_group(_FakeProc())
    assert killed["called"], "expected proc.kill() fallback when os.killpg is unavailable"
