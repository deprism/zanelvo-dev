"""Deterministic tests for this round's agent upgrades: per-role default tools, the pure
dependency-readiness selector used by the (now-concurrent) implement pass, and the anti-loop
strategy-escalation guard clauses. No DB/network — the DB-touching escalation path itself (actually
reassigning a plan item) was verified live this session (see the session notes / commit message),
matching this repo's established pattern of keeping pytest DB-free and verifying DB-touching code
paths with a live mongomock smoke test instead.
"""
import asyncio

from app.devstudio.agents.orchestrator import _escalate_strategy, _select_runnable_items
from app.devstudio.agents.registry import _DEFAULT_TOOLS_BY_ROLE, _default_config
from app.devstudio.models import AgentConfiguration, PlanItem


def _item(id_, title, status="PENDING", depends_on=None, assigned_agent="backend"):
    return PlanItem(id=id_, task_id="t1", title=title, description="d", status=status,
                     depends_on=depends_on or [], assigned_agent=assigned_agent)


# --- default tools per role --------------------------------------------------------------------

def test_default_tools_only_cover_anthropic_primary_roles():
    # design/reviewer default to Gemini/OpenAI primary in at least one preset (see
    # providers/registry.py MODEL_PRESETS) — neither implements generate_with_tools() today, so
    # giving them default tools would silently degrade every call to a fallback-model round trip.
    assert "design" not in _DEFAULT_TOOLS_BY_ROLE
    assert "reviewer" not in _DEFAULT_TOOLS_BY_ROLE


def test_default_tools_wired_into_default_config():
    planner_cfg = _default_config("planner")
    assert set(planner_cfg.tools_enabled) == {"ask_human", "web_search", "perplexity_research"}
    vision_cfg = _default_config("vision")
    assert vision_cfg.tools_enabled == ["screenshot"]
    troubleshoot_cfg = _default_config("troubleshoot")
    assert troubleshoot_cfg.tools_enabled == ["ask_human", "view_file", "search_files", "execute_bash"]


def test_default_config_for_unlisted_role_has_no_tools():
    cfg = _default_config("backend")
    assert cfg.tools_enabled == []
    assert cfg.mcp_servers == []


def test_default_tools_are_all_real_builtin_tool_names():
    from app.devstudio.agents.runner import BUILTIN_TOOLS
    for tools in _DEFAULT_TOOLS_BY_ROLE.values():
        for name in tools:
            assert name in BUILTIN_TOOLS, f"{name!r} is not a real built-in tool"


# --- _select_runnable_items (pure dependency-readiness selector) ------------------------------

def test_select_runnable_items_skips_terminal_statuses():
    items = [_item("1", "a", status="VERIFIED"), _item("2", "b", status="SKIPPED"),
              _item("3", "c", status="IMPLEMENTED"), _item("4", "d", status="VERIFYING"),
              _item("5", "e", status="PENDING")]
    runnable = _select_runnable_items(items)
    assert [i.id for i in runnable] == ["5"]


def test_select_runnable_items_holds_back_unsatisfied_dependencies():
    a = _item("1", "Build API", status="PENDING")
    b = _item("2", "Build UI", status="PENDING", depends_on=["Build API"])  # by title
    runnable = _select_runnable_items([a, b])
    assert [i.id for i in runnable] == ["1"]  # b waits


def test_select_runnable_items_releases_once_dependency_satisfied():
    a = _item("1", "Build API", status="VERIFIED")
    b = _item("2", "Build UI", status="PENDING", depends_on=["1"])  # by id
    runnable = _select_runnable_items([a, b])
    assert [i.id for i in runnable] == ["2"]


def test_select_runnable_items_batches_independent_items_together():
    # Independent items (no depends_on edge between them) are all runnable in the same pass —
    # this is exactly the set the orchestrator now sends through concurrently, in batches of
    # _MAX_PARALLEL_IMPLEMENTERS.
    items = [_item(str(i), f"item {i}") for i in range(5)]
    runnable = _select_runnable_items(items)
    assert {i.id for i in runnable} == {"0", "1", "2", "3", "4"}


def test_select_runnable_items_does_not_chain_unlock_within_one_pass():
    # A -> B -> C: only A is runnable this pass, even though B's dependency (A) hasn't actually
    # run yet — this matches the original single-pass orchestrator semantics exactly (a same-pass
    # dependent never sees another item's mid-pass status change).
    a = _item("1", "A", status="PENDING")
    b = _item("2", "B", status="PENDING", depends_on=["A"])
    c = _item("3", "C", status="PENDING", depends_on=["B"])
    runnable = _select_runnable_items([a, b, c])
    assert [i.id for i in runnable] == ["1"]


# --- _escalate_strategy guard clauses (no DB touch on these paths) -----------------------------

def test_escalate_strategy_noop_when_already_on_troubleshoot():
    # Reaching this assertion without a MONGO_URL/db configured in this test process IS the proof
    # that the guard returns before ever touching the database or `configs`.
    item = _item("1", "x", assigned_agent="troubleshoot")
    asyncio.run(_escalate_strategy("t1", item, {}))


def test_escalate_strategy_noop_when_no_troubleshoot_config_exists():
    item = _item("1", "x", assigned_agent="backend")
    asyncio.run(_escalate_strategy("t1", item, {}))


def test_escalate_strategy_noop_when_troubleshoot_disabled():
    item = _item("1", "x", assigned_agent="backend")
    cfg = AgentConfiguration(role="troubleshoot", enabled=False)
    asyncio.run(_escalate_strategy("t1", item, {"troubleshoot": cfg}))
