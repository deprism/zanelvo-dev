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


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for d in self._docs:
            yield d


def test_view_logs_summarizes_real_runs_and_failures(monkeypatch):
    class _FakeCollection:
        def find(self, *a, **kw):
            return _FakeCursor([
                {"role": "backend", "provider": "anthropic", "model": "claude-sonnet-5",
                 "status": "failed", "action": "Implementing: fix bug", "result_summary": "TypeError"},
            ])

    class _FakeDB:
        ds_agent_runs = _FakeCollection()

    monkeypatch.setattr(runner, "get_db", lambda: _FakeDB())

    async def fake_failure_history(task_id, limit=20):
        return [{"failure_class": "type_error", "command": "pytest", "occurrences": 2,
                  "normalized_error": "TypeError: NoneType is not callable"}]

    monkeypatch.setattr("app.devstudio.services.anti_loop.failure_history", fake_failure_history)

    result = asyncio.run(_run_builtin_tool("view_logs", {}, task_id="t1", role="troubleshoot"))
    assert "backend via anthropic/claude-sonnet-5: failed" in result
    assert "TypeError" in result
    assert "type_error" in result and "2x" in result


def test_deployment_debugger_lists_recent_runs(monkeypatch):
    from app.devstudio.services import github_provider

    class _FakeProject:
        github_owner = "founder"
        github_repo = "my-plugin"

    async def fake_project(task_id):
        return _FakeProject()

    async def fake_list_runs(owner, repo, limit=10):
        assert (owner, repo) == ("founder", "my-plugin")
        return [{"id": 42, "name": "CI", "status": "completed", "conclusion": "failure",
                  "head_branch": "main", "head_sha": "abc1234", "html_url": "https://x", "created_at": "now"}]

    monkeypatch.setattr(runner, "_project_for_task", fake_project)
    monkeypatch.setattr(github_provider, "list_workflow_runs", fake_list_runs)

    result = asyncio.run(_run_builtin_tool("deployment_debugger", {}, task_id="t1", role="deployment"))
    assert "run 42" in result and "failure" in result


def test_deployment_debugger_inspects_a_specific_run(monkeypatch):
    from app.devstudio.services import github_provider

    class _FakeProject:
        github_owner = "founder"
        github_repo = "my-plugin"

    async def fake_project(task_id):
        return _FakeProject()

    async def fake_jobs(owner, repo, run_id):
        assert run_id == 42
        return [{"id": 1, "name": "build", "status": "completed", "conclusion": "failure",
                  "steps": [{"name": "Run tests", "status": "completed", "conclusion": "failure"}]}]

    monkeypatch.setattr(runner, "_project_for_task", fake_project)
    monkeypatch.setattr(github_provider, "get_workflow_run_jobs", fake_jobs)

    result = asyncio.run(_run_builtin_tool("deployment_debugger", {"run_id": 42},
                                             task_id="t1", role="deployment"))
    assert "Run tests: completed/failure" in result


def test_deployment_debugger_wraps_github_errors(monkeypatch):
    from app.devstudio.services import github_provider

    class _FakeProject:
        github_owner = "founder"
        github_repo = "my-plugin"

    async def fake_project(task_id):
        return _FakeProject()

    async def fake_list_runs(owner, repo, limit=10):
        raise github_provider.GitHubError("GitHub token rejected (401)")

    monkeypatch.setattr(runner, "_project_for_task", fake_project)
    monkeypatch.setattr(github_provider, "list_workflow_runs", fake_list_runs)

    with pytest.raises(ToolExecutionError, match="401"):
        asyncio.run(_run_builtin_tool("deployment_debugger", {}, task_id="t1", role="deployment"))


def test_analyze_image_requires_a_question():
    with pytest.raises(ToolExecutionError, match="question"):
        asyncio.run(_run_builtin_tool("analyze_image", {}, task_id="t1", role="vision"))


def test_analyze_image_requires_registry_and_config_in_context():
    with pytest.raises(ToolExecutionError, match="context"):
        asyncio.run(_run_builtin_tool("analyze_image", {"question": "well?"}, task_id="t1", role="vision"))


def test_analyze_image_requires_an_existing_screenshot(monkeypatch):
    from app.devstudio.services import browser_service

    async def fake_list_screenshots(task_id):
        return []

    monkeypatch.setattr(browser_service, "list_screenshots", fake_list_screenshots)

    class _FakeRegistry:
        def get(self, name):
            raise AssertionError("should never reach provider lookup with no screenshots")

    with pytest.raises(ToolExecutionError, match="screenshot"):
        asyncio.run(_run_builtin_tool("analyze_image", {"question": "well?"}, task_id="t1", role="vision",
                                        registry=_FakeRegistry(), config=object()))


def test_analyze_image_analyzes_the_real_captured_screenshot(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.devstudio.services import browser_service

    shot_path = tmp_path / "shot.png"
    shot_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")

    async def fake_list_screenshots(task_id):
        return [SimpleNamespace(id="s1", path=str(shot_path))]

    monkeypatch.setattr(browser_service, "list_screenshots", fake_list_screenshots)

    captured = {}

    class _FakeProvider:
        def supports_vision(self, model):
            return True

        async def generate_with_vision(self, *, system, prompt, model, images_b64):
            captured.update(prompt=prompt, model=model, image_count=len(images_b64))
            return SimpleNamespace(text="The button looks centered.")

    class _FakeRegistry:
        def get(self, name):
            assert name == "anthropic"
            return _FakeProvider()

    config = SimpleNamespace(primary_provider="anthropic", primary_model="claude-sonnet-5")
    result = asyncio.run(_run_builtin_tool(
        "analyze_image", {"question": "Is the button centered?"}, task_id="t1", role="vision",
        registry=_FakeRegistry(), config=config))

    assert result == "The button looks centered."
    assert captured["prompt"] == "Is the button centered?"
    assert captured["image_count"] == 1


def test_analyze_image_refuses_a_non_vision_model(monkeypatch):
    from types import SimpleNamespace

    from app.devstudio.services import browser_service

    async def fake_list_screenshots(task_id):
        return [SimpleNamespace(id="s1", path="/tmp/does-not-need-to-exist-for-this-check.png")]

    monkeypatch.setattr(browser_service, "list_screenshots", fake_list_screenshots)

    class _FakeProvider:
        def supports_vision(self, model):
            return False

    class _FakeRegistry:
        def get(self, name):
            return _FakeProvider()

    config = SimpleNamespace(primary_provider="openai", primary_model="gpt-5.4-mini")
    with pytest.raises(ToolExecutionError, match="vision"):
        asyncio.run(_run_builtin_tool("analyze_image", {"question": "well?"}, task_id="t1", role="vision",
                                        registry=_FakeRegistry(), config=config))


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
