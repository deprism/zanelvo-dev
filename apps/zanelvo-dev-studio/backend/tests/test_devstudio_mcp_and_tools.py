"""Deterministic tests for MCP presets and the built-in agent tool-calling plumbing. No DB, no
network, no subprocess — the live end-to-end pieces (an actual MCP server round trip, an actual
Anthropic/Emergent tool-calling API call) were verified manually this session (see the session
notes / commit message) since they need real credentials or npx/node this test environment can't
assume every CI runner has; what's covered here is everything that doesn't.
"""
import asyncio

import pytest

from app.devstudio.agents import runner
from app.devstudio.agents.runner import BUILTIN_TOOLS, ToolExecutionError, _execute_tool_call, _run_builtin_tool
from app.devstudio.services.custom_agent_service import SEED_ROLES
from app.devstudio.services.mcp_service import PRESETS


def _fake_workspace(monkeypatch, path: str) -> None:
    """Bypasses the real (DB-backed) workspace lookup — these tests exercise the file/command
    logic for real against a tmp_path, not the DB plumbing that resolves task_id -> workspace."""
    async def fake(task_id):
        return path
    monkeypatch.setattr(runner, "_workspace_path_for_task", fake)


def test_mcp_presets_are_well_formed():
    assert set(PRESETS) == {"memory", "notion"}
    for name, preset in PRESETS.items():
        assert preset["transport"] in ("stdio", "http")
        if preset["transport"] == "stdio":
            assert preset["command"]
            assert isinstance(preset["args"], list) and preset["args"]
        assert isinstance(preset["env_keys"], list)
        assert preset["description"]


def test_notion_preset_declares_its_required_token():
    assert PRESETS["notion"]["env_keys"] == ["NOTION_TOKEN"]


def test_memory_preset_needs_no_credentials():
    assert PRESETS["memory"]["env_keys"] == []


def test_builtin_tools_all_have_a_valid_json_schema_shape():
    for name, tool in BUILTIN_TOOLS.items():
        assert tool["name"] == name
        assert tool["description"]
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties"), dict)
        assert isinstance(schema.get("required", []), list)


def test_finish_tool_acknowledges_the_summary():
    result = asyncio.run(_run_builtin_tool("finish", {"summary": "done here"}, task_id="t1", role="qa"))
    assert "done here" in result


def test_get_assets_tool_is_honestly_not_implemented():
    with pytest.raises(ToolExecutionError, match="NOT|not implemented|no real"):
        asyncio.run(_run_builtin_tool("get_assets", {"query": "logo"}, task_id="t1", role="design"))


def test_web_search_tool_explains_it_needs_an_anthropic_model():
    # Only reached for non-Anthropic providers — AnthropicProvider intercepts "web_search" before
    # it would ever become a tool call needing this executor (see anthropic_provider.py).
    with pytest.raises(ToolExecutionError, match="Anthropic"):
        asyncio.run(_run_builtin_tool("web_search", {"query": "x"}, task_id="t1", role="planner"))


def test_unknown_builtin_tool_raises():
    with pytest.raises(ToolExecutionError, match="Unknown"):
        asyncio.run(_run_builtin_tool("not_a_real_tool", {}, task_id="t1", role="qa"))


def test_execute_tool_call_never_raises_reports_error_as_content_instead():
    # A failed tool call must become tool_result content the model can see and adapt to, not an
    # exception that crashes the whole agent step (see call_with_tools' design note).
    result = asyncio.run(_execute_tool_call(
        {"id": "call_1", "name": "get_assets", "arguments": {"query": "x"}}, {}, task_id="t1", role="design"))
    assert result["id"] == "call_1"
    assert result["content"].startswith("ERROR:")


def test_execute_tool_call_unknown_tool_name_reports_error_as_content():
    result = asyncio.run(_execute_tool_call(
        {"id": "call_2", "name": "does_not_exist", "arguments": {}}, {}, task_id="t1", role="design"))
    assert "ERROR" in result["content"]


def test_view_file_reads_a_real_file(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Main.java").write_text("class Main {}")
    _fake_workspace(monkeypatch, str(tmp_path))
    result = asyncio.run(_run_builtin_tool("view_file", {"path": "src/Main.java"}, task_id="t1", role="troubleshoot"))
    assert result == "class Main {}"


def test_view_file_missing_path_raises():
    with pytest.raises(ToolExecutionError, match="path"):
        asyncio.run(_run_builtin_tool("view_file", {}, task_id="t1", role="troubleshoot"))


def test_view_file_not_found_raises_clear_error(tmp_path, monkeypatch):
    _fake_workspace(monkeypatch, str(tmp_path))
    with pytest.raises(ToolExecutionError, match="not found"):
        asyncio.run(_run_builtin_tool("view_file", {"path": "nope.txt"}, task_id="t1", role="troubleshoot"))


def test_view_file_rejects_escaping_the_workspace(tmp_path, monkeypatch):
    _fake_workspace(monkeypatch, str(tmp_path))
    with pytest.raises(ToolExecutionError):
        asyncio.run(_run_builtin_tool("view_file", {"path": "../../../../etc/passwd"},
                                        task_id="t1", role="troubleshoot"))


def test_search_files_finds_a_real_match(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("def broken_function():\n    raise ValueError('boom')\n")
    _fake_workspace(monkeypatch, str(tmp_path))
    result = asyncio.run(_run_builtin_tool("search_files", {"query": "broken_function"},
                                             task_id="t1", role="troubleshoot"))
    assert "app.py" in result


def test_search_files_no_matches_says_so_plainly(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("print('hi')\n")
    _fake_workspace(monkeypatch, str(tmp_path))
    result = asyncio.run(_run_builtin_tool("search_files", {"query": "nonexistent_symbol_xyz"},
                                             task_id="t1", role="troubleshoot"))
    assert "No matches" in result


def test_search_files_missing_query_raises():
    with pytest.raises(ToolExecutionError, match="query"):
        asyncio.run(_run_builtin_tool("search_files", {}, task_id="t1", role="troubleshoot"))


def test_execute_bash_formats_a_successful_run(tmp_path, monkeypatch):
    # execution_service.run_command itself needs a real DB (it persists a TestRun) — covered live
    # in the manual smoke test alongside the route-level checks; this proves execute_bash's own
    # glue (calling run_command with the right args, formatting the result) without needing DB.
    from types import SimpleNamespace

    from app.devstudio.services import execution_service

    captured = {}

    async def fake_run_command(workspace_path, task_id, command, test_type, cwd_subdir=None, **kw):
        captured.update(workspace_path=workspace_path, command=command, cwd_subdir=cwd_subdir)
        return SimpleNamespace(exit_code=0, stdout_tail="On branch main", stderr_tail="")

    monkeypatch.setattr(execution_service, "run_command", fake_run_command)
    _fake_workspace(monkeypatch, str(tmp_path))

    result = asyncio.run(_run_builtin_tool("execute_bash", {"command": "git status", "cwd_subdir": "backend"},
                                             task_id="t1", role="troubleshoot"))
    assert captured == {"workspace_path": str(tmp_path), "command": "git status", "cwd_subdir": "backend"}
    assert "exit_code=0" in result and "On branch main" in result


def test_execute_bash_uses_a_valid_test_run_type():
    # Same bug class as testing_service's "java_unit" gap: execute_bash passes "agent_tool" as
    # TestRun.test_type (models.py) — this fails loudly if that string is ever removed from the
    # Literal without updating this call site.
    from app.devstudio.models import TestRun
    allowed_types = TestRun.model_fields["test_type"].annotation.__args__
    assert "agent_tool" in allowed_types


def test_execute_bash_refuses_a_command_not_on_the_allowlist(tmp_path, monkeypatch):
    _fake_workspace(monkeypatch, str(tmp_path))
    with pytest.raises(ToolExecutionError, match="allowlist|not allowed|denied|blocked"):
        asyncio.run(_run_builtin_tool("execute_bash", {"command": "curl http://example.com"},
                                        task_id="t1", role="troubleshoot"))


def test_execute_bash_missing_command_raises():
    with pytest.raises(ToolExecutionError, match="command"):
        asyncio.run(_run_builtin_tool("execute_bash", {}, task_id="t1", role="troubleshoot"))


def test_seed_roles_cover_the_six_seeded_specialists_with_unique_slugs():
    # "Integration" — the 7th Emergent-style specialist in the original ask — is already a
    # built-in implementer role (models.BUILTIN_AGENT_ROLES), so it isn't re-seeded here.
    slugs = [r["role"] for r in SEED_ROLES]
    assert len(slugs) == len(set(slugs)), "duplicate role slugs in SEED_ROLES"
    assert set(slugs) == {
        "vision", "frontend_testing", "backend_testing", "fullstack_testing",
        "troubleshoot", "deployment",
    }
    for spec in SEED_ROLES:
        assert spec["label"]
        assert spec["system_prompt"] and len(spec["system_prompt"]) > 20
        assert spec["icon"]
