"""AgentOrchestrator (Supervisor). Deterministic control flow drives the TaskStateMachine; LLM
role-agents only ever PROPOSE (analysis, plan, file edits, verdicts) — every state transition and
every file write happens in plain Python here, never as a side effect of parsing model output.

Only this module calls task_manager.set_status to move a task toward COMPLETED, and only after the
completion gate (verified plan items + passing tests + review approval) is satisfied — see
`_finalize_verification`. Committing/pushing to GitHub is never automatic: the founder triggers it
explicitly via the commit/push API once a task reaches READY_FOR_APPROVAL.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from ..models import AgentConfiguration, PlanItem, Project, Task, Workspace
from ..providers.registry import ModelRegistry
from ..services import (activity_service, anti_loop, execution_service, indexer, memory_service,
                          repository_service, settings_service, task_manager, workspace_manager)
from ..services.file_service import FileService, PathEscapeError, StalePatchError
from . import git_agent, qa_agent, registry as agent_registry, roles
from .runner import AgentStepFailed

logger = logging.getLogger("zanelvo.devstudio")


async def _block(task_id: str, reason: str) -> None:
    await task_manager.update_task(task_id, blocked_reason=reason)
    await task_manager.set_status(task_id, "BLOCKED", note=reason)
    await task_manager.add_message(task_id, "supervisor", f"Blocked: {reason}")


async def _cancel(task_id: str) -> None:
    await task_manager.set_status(task_id, "CANCELLED", note="stop requested")
    await task_manager.clear_stop(task_id)
    await task_manager.add_message(task_id, "supervisor", "Stopped. Workspace preserved — send another "
                                     "message (e.g. 'Continue.') to resume.")


async def run_task(task_id: str) -> None:
    task = await task_manager.get_task(task_id)
    if task is None:
        return
    project = await repository_service.get_project(task.project_id)
    if project is None:
        await _block(task_id, "Project no longer exists")
        return
    registry = await settings_service.build_model_registry()
    configs = await agent_registry.list_configs()
    loop_detector = anti_loop.LoopDetector()

    async def stopped() -> bool:
        return await task_manager.is_stop_requested(task_id)

    try:
        # Resume a recoverable task (BLOCKED/FAILED/CANCELLED). run_task's per-state blocks below
        # only fire for in-flight states, so without this a blocked task's /run or "Continue."
        # message was a silent no-op — it never restarted. Resume from the furthest stage already
        # reached: an existing plan → re-enter implementation; a provisioned workspace but no plan
        # → re-analyze; otherwise start from scratch. The state machine already permits these.
        if task.status in ("BLOCKED", "FAILED", "CANCELLED"):
            existing_plan = await task_manager.list_plan_items(task_id)
            existing_ws = await workspace_manager.get_workspace_for_task(task_id)
            resume_to = ("IMPLEMENTING" if existing_plan
                         else "ANALYZING_REPOSITORY" if existing_ws else "UNDERSTANDING")
            if task.blocked_reason:
                await task_manager.update_task(task_id, blocked_reason=None)
            await task_manager.clear_stop(task_id)
            task = await task_manager.set_status(task_id, resume_to, note=f"Resuming from {task.status}")

        if task.status == "CREATED":
            await activity_service.emit(task_id, "supervisor_note", {"note": "Understanding request"})
            task = await task_manager.set_status(task_id, "UNDERSTANDING")

        if task.status in ("UNDERSTANDING", "CANCELLED", "FAILED"):
            workspace = await workspace_manager.get_workspace_for_task(task_id)
            if workspace is None:
                await activity_service.emit(task_id, "supervisor_note", {"note": "Provisioning workspace"})
                workspace = await workspace_manager.provision_task_workspace(
                    project, task.branch, task_id, task.title)
                await task_manager.update_task(task_id, workspace_id=workspace.id,
                                                 base_commit_sha=workspace.base_commit_sha)
            task = await task_manager.set_status(task_id, "ANALYZING_REPOSITORY")
        if await stopped():
            return await _cancel(task_id)

        analysis: Optional[dict] = None
        if task.status == "ANALYZING_REPOSITORY":
            snapshot = await indexer.get_snapshot(project.id, task.branch)
            if snapshot is None or not snapshot.indexed:
                await activity_service.emit(task_id, "supervisor_note", {"note": "Indexing repository"})
                await indexer.index_project_branch(project, task.branch)
            try:
                analysis = await roles.analyze_repository(task, project.id, task.branch, registry,
                                                             configs["repository_analyst"])
            except AgentStepFailed as e:
                return await _block(task_id, str(e))
            await activity_service.emit(task_id, "analysis_complete", {
                "summary": str(analysis.get("summary", ""))[:800],
                "relevant_files": (analysis.get("relevant_files") or [])[:20]})
            await memory_service.add_memory(
                project.id, "OUTSTANDING_WORK", f"Analysis for: {task.title}",
                str(analysis.get("summary", analysis))[:2000], branch=task.branch,
                source_task_id=task_id, confidence="medium")
            task = await task_manager.set_status(task_id, "PLANNING")
        if await stopped():
            return await _cancel(task_id)

        if task.status == "PLANNING":
            if analysis is None:
                try:
                    analysis = await roles.analyze_repository(task, project.id, task.branch, registry,
                                                                 configs["repository_analyst"])
                except AgentStepFailed as e:
                    return await _block(task_id, str(e))
            try:
                items = await roles.create_plan(task, analysis, project.id, registry, configs["planner"])
            except AgentStepFailed as e:
                return await _block(task_id, str(e))
            await task_manager.set_plan(task_id, items)
            task = await task_manager.set_status(task_id, "READY")
        if await stopped():
            return await _cancel(task_id)

        if task.status == "READY":
            task = await task_manager.set_status(task_id, "IMPLEMENTING")

        workspace = await workspace_manager.get_workspace_for_task(task_id)
        if workspace is None:
            return await _block(task_id, "Workspace missing — cannot implement")

        if task.status == "IMPLEMENTING":
            completed_ok = await _implement_loop(task, project, workspace, registry, configs, loop_detector)
            if not completed_ok:
                return  # _implement_loop already set BLOCKED/left state for retry
            task = await task_manager.get_task(task_id)
            # Every plan item ran but nothing was actually written — don't waste a review round on
            # an empty diff (which the reviewer rightly rejects, then loops). Block with a clear,
            # actionable reason instead.
            if task.status == "IMPLEMENTING":
                diff_now = await git_agent.diff_against_base(workspace)
                if diff_now.get("changed_file_count", 0) == 0:
                    return await _block(
                        task_id,
                        "The plan completed but produced no file changes at all — the items were "
                        "likely conceptual rather than concrete edits. Refine the request (name the "
                        "files/behavior you want built) and run again.")
        if await stopped():
            return await _cancel(task_id)

        if task.status == "IMPLEMENTING":
            task = await task_manager.set_status(task_id, "TESTING")
        if task.status == "TESTING":
            runs = await qa_agent.run_risk_based_tests(task_id, workspace)
            if runs and not qa_agent.all_passed(runs):
                failing = next((r for r in runs if r.status != "passed"), runs[0])
                signal = await anti_loop.record_failure(
                    task_id, (failing.stdout_tail or "") + (failing.stderr_tail or ""),
                    command=failing.command, subsystem="tests")
                await task_manager.add_message(task_id, "supervisor",
                                                 f"Tests failed ({failing.command}). {signal.reason}")
                if signal.should_block:
                    return await _block(task_id, "Same test failure repeated — needs a strategy change")
                task = await task_manager.set_status(task_id, "DEBUGGING")
                task = await task_manager.set_status(task_id, "IMPLEMENTING")
                await task_manager.update_task(task_id, iterations_used=task.iterations_used + 1)
                return
            if runs:
                await _verify_implemented_items(task_id, runs)
            task = await task_manager.set_status(task_id, "REVIEWING")
        if await stopped():
            return await _cancel(task_id)

        if task.status == "REVIEWING":
            diff = await git_agent.diff_against_base(workspace)
            test_runs = await execution_service.get_test_runs(task_id)
            test_evidence = [{"type": r.test_type, "command": r.command, "status": r.status}
                               for r in test_runs]
            failures = await anti_loop.failure_history(task_id)
            try:
                verdict = await roles.review_diff(task, diff, test_evidence, failures, registry,
                                                    configs["reviewer"])
            except AgentStepFailed as e:
                return await _block(task_id, str(e))
            await task_manager.update_task(task_id, review_verdict=verdict.get("verdict"),
                                             current_diff_summary=diff)
            await task_manager.add_message(task_id, "supervisor",
                                             f"Review verdict: {verdict.get('verdict')} — "
                                             f"{verdict.get('reasoning', '')[:300]}")
            if verdict.get("verdict") == "REJECTED":
                task = await task_manager.set_status(task_id, "IMPLEMENTING")
                return
            task = await task_manager.set_status(task_id, "FINAL_VERIFICATION")

        if task.status == "FINAL_VERIFICATION":
            await _finalize_verification(task_id)
            task = await task_manager.set_status(task_id, "READY_FOR_APPROVAL")
            await task_manager.add_message(
                task_id, "supervisor",
                "Ready for your approval. Review the diff and tests, then POST "
                "/api/devstudio/tasks/{id}/commit and /push when you're satisfied.")
    except Exception as e:  # noqa: BLE001 — orchestrator must never crash silently; always BLOCKED
        logger.exception("Dev Studio orchestrator error on task %s", task_id)
        await _block(task_id, f"Unexpected orchestrator error: {type(e).__name__}: {e}")


async def _verify_implemented_items(task_id: str, passing_test_runs) -> None:
    """A plan item cannot become VERIFIED without evidence — here that evidence is the passing
    TestRun(s) that covered this implementation pass."""
    evidence = {"kind": "test_runs", "test_run_ids": [r.id for r in passing_test_runs],
                 "commands": [r.command for r in passing_test_runs]}
    for item in await task_manager.list_plan_items(task_id):
        if item.status == "IMPLEMENTED":
            await task_manager.set_plan_item_status(item.id, "VERIFYING")
            await task_manager.set_plan_item_status(item.id, "VERIFIED", evidence=evidence)


async def _finalize_verification(task_id: str) -> None:
    all_verified = await task_manager.all_required_items_verified(task_id)
    test_runs = await execution_service.get_test_runs(task_id)
    tests_ok = qa_agent.all_passed(test_runs) if test_runs else False
    if all_verified and tests_ok:
        await task_manager.update_task(task_id, verification_status="verified")
    elif tests_ok:
        await task_manager.update_task(task_id, verification_status="partial")
    else:
        await task_manager.update_task(task_id, verification_status="limited")


_MAX_PARALLEL_IMPLEMENTERS = 3  # bounds concurrent LLM calls within one implement pass


def _select_runnable_items(items: List[PlanItem]) -> List[PlanItem]:
    """Pure: which of `items` are eligible to attempt right now — not already done, and every
    dependency already satisfied. Dependency readiness is evaluated against the snapshot in
    `items` only (a dependency finishing mid-pass doesn't unlock others until the next pass) —
    this matches the original single-pass orchestrator behavior exactly."""
    by_title = {i.title: i for i in items}
    id_index = {i.id: i for i in items}
    runnable = []
    for item in items:
        if item.status in ("VERIFIED", "SKIPPED", "IMPLEMENTED", "VERIFYING"):
            continue
        deps = [id_index.get(d) or by_title.get(d) for d in item.depends_on]
        if any(d and d.status not in ("VERIFIED", "IMPLEMENTED", "SKIPPED") for d in deps):
            continue  # dependency not ready yet; leave PENDING
        runnable.append(item)
    return runnable


async def _escalate_strategy(task_id: str, item: PlanItem, configs: Dict[str, AgentConfiguration]) -> None:
    """Called when anti_loop flags the SAME failure repeating on `item` (LoopSignal.
    should_change_strategy) — hands the item to the Troubleshoot specialist for its next attempt
    instead of blindly retrying the same role a third time. This is what actually triggers the
    behavior the seeded Troubleshoot role's own system prompt already promises ('invoked when the
    same plan item has failed more than once') — nothing used to call it. A no-op if the item is
    already assigned to Troubleshoot (a further repeat there heads to should_block instead) or no
    enabled Troubleshoot configuration exists."""
    if item.assigned_agent == "troubleshoot":
        return
    troubleshoot_cfg = configs.get("troubleshoot")
    if not troubleshoot_cfg or not troubleshoot_cfg.enabled:
        return
    await task_manager.reassign_plan_item(item.id, "troubleshoot", f"Repeated failure on '{item.title}'")
    await task_manager.add_message(
        task_id, "supervisor",
        f"{item.title}: same failure repeated — reassigning to the Troubleshoot Agent for a fresh approach.")


async def _implement_loop(task: Task, project: Project, workspace: Workspace, registry: ModelRegistry,
                           configs: Dict[str, AgentConfiguration], loop_detector: anti_loop.LoopDetector) -> bool:
    """Implements every plan item that's runnable this pass (dependencies already satisfied),
    running their LLM calls CONCURRENTLY in bounded batches — the file operations each call
    proposes are only applied afterward, sequentially, so no filesystem write ever races another.
    An item whose proposed patch turns out to be stale against another item's write in the same
    batch (only possible if the Planner declared them independent but they actually touch the same
    file) fails cleanly via the existing StalePatchError path and is retried next pass, once it can
    see the other item's change — it never corrupts anything. Returns True if the pass reached a
    stopping point cleanly; False if it already transitioned the task to a state the caller should
    not continue from (BLOCKED, or returned for the founder to retry)."""
    fs = FileService(workspace.local_path)

    # Keep implementing pass after pass until no plan item is runnable. One pass only unlocks the
    # items whose dependencies were already satisfied, so a dependency CHAIN (B depends on A, C on
    # B, ...) needs several passes to fully implement. Doing a single pass — the old behavior —
    # sent multi-item plans to review with only their first layer done, so the reviewer correctly
    # saw an (almost) empty diff and rejected, and the remaining items never ran. A pass that
    # implements nothing new breaks the loop so a stuck/failed item can't spin forever (its failure
    # was already recorded by the anti-loop engine, which blocks on genuine repeats).
    while True:
        if await task_manager.is_stop_requested(task.id):
            await _cancel(task.id)
            return False
        task = await task_manager.get_task(task.id)
        if anti_loop.budget_exhausted(task.iterations_used, task.iteration_budget):
            await _block(task.id, f"Orchestration budget exhausted ({task.iteration_budget} iterations)")
            return False

        items = await task_manager.list_plan_items(task.id)
        runnable = _select_runnable_items(items)
        if not runnable:
            break
        implemented_this_pass = 0

        for batch_start in range(0, len(runnable), _MAX_PARALLEL_IMPLEMENTERS):
            batch = runnable[batch_start:batch_start + _MAX_PARALLEL_IMPLEMENTERS]

            if await task_manager.is_stop_requested(task.id):
                await _cancel(task.id)
                return False
            task = await task_manager.get_task(task.id)
            if anti_loop.budget_exhausted(task.iterations_used, task.iteration_budget):
                await _block(task.id, f"Orchestration budget exhausted ({task.iteration_budget} iterations)")
                return False

            prepared = []
            for item in batch:
                await task_manager.set_plan_item_status(item.id, "READY")
                await task_manager.set_plan_item_status(item.id, "RUNNING")
                config = configs.get(item.assigned_agent, configs["backend"])
                file_contents: Dict[str, str] = {}
                expected_hashes: Dict[str, Optional[str]] = {}
                for path in item.relevant_files[:12]:
                    try:
                        file_contents[path] = fs.read_file(path)
                        expected_hashes[path] = fs.read_hash(path)
                    except (FileNotFoundError, PathEscapeError, ValueError):
                        continue
                prepared.append((item, config, file_contents, expected_hashes))

            # No filesystem writes happen until every call in this batch returns, so running the LLM
            # calls concurrently is safe regardless of whether items touch overlapping files — only
            # applying their results (below) needs to stay sequential.
            results = await asyncio.gather(
                *(roles.implement_plan_item(task, item, project.id, task.branch, file_contents, registry, config)
                  for item, config, file_contents, expected_hashes in prepared),
                return_exceptions=True,
            )

            for (item, config, file_contents, expected_hashes), plan_result in zip(prepared, results):
                if isinstance(plan_result, AgentStepFailed):
                    await task_manager.set_plan_item_status(item.id, "FAILED")
                    signal = await anti_loop.record_failure(task.id, str(plan_result), command="implement",
                                                              subsystem=item.assigned_agent, plan_item_id=item.id)
                    await task_manager.add_message(task.id, "supervisor",
                                                     f"{item.title}: implementation failed — {plan_result}")
                    if signal.should_block:
                        await _block(task.id, f"Repeated implementation failure on '{item.title}': {plan_result}")
                        return False
                    if signal.should_change_strategy:
                        await _escalate_strategy(task.id, item, configs)
                    continue
                if isinstance(plan_result, BaseException):
                    raise plan_result  # unexpected — surfaced by run_task's own catch-all as BLOCKED

                ops = plan_result.get("file_operations", []) if isinstance(plan_result, dict) else []
                summary = plan_result.get("summary", "") if isinstance(plan_result, dict) else ""
                diff_before = await git_agent.diff_against_base(workspace)
                try:
                    _apply_file_operations(fs, ops, expected_hashes)
                except (StalePatchError, PathEscapeError, ValueError, FileNotFoundError, FileExistsError) as e:
                    await task_manager.set_plan_item_status(item.id, "FAILED")
                    signal = await anti_loop.record_failure(task.id, str(e), command="apply_patch",
                                                              subsystem=item.assigned_agent, plan_item_id=item.id)
                    await task_manager.add_message(task.id, "supervisor", f"{item.title}: could not apply changes — {e}")
                    if signal.should_block:
                        await _block(task.id, f"Repeated patch failure on '{item.title}': {e}")
                        return False
                    if signal.should_change_strategy:
                        await _escalate_strategy(task.id, item, configs)
                    continue

                diff_after = await git_agent.diff_against_base(workspace)
                no_progress = loop_detector.record_diff(str(diff_after))
                changed_paths = [op.get("path") for op in ops if isinstance(op, dict) and op.get("path")]
                # Surface exactly what this agent did — the summary it wrote and the files it
                # touched — instead of a bare "succeeded", so the founder can see real progress.
                await activity_service.emit(task.id, "item_implemented", {
                    "role": item.assigned_agent, "title": item.title,
                    "summary": (summary or "")[:600], "files": changed_paths[:20],
                    "op_count": len(ops)})
                if not ops:
                    await task_manager.add_message(task.id, "supervisor",
                                                     f"{item.title}: {summary or 'completed with no file changes'}")
                elif no_progress and diff_before == diff_after:
                    await task_manager.add_message(task.id, "supervisor",
                                                     f"{item.title}: no diff change after implementation attempt")

                await task_manager.set_plan_item_status(item.id, "IMPLEMENTED",
                                                           evidence={"kind": "file_operations", "count": len(ops)})
                task = await task_manager.update_task(task.id, iterations_used=task.iterations_used + 1,
                                                         current_diff_summary=diff_after)
                implemented_this_pass += 1

        if implemented_this_pass == 0:
            break

    return True


def _apply_file_operations(fs: FileService, ops: List[dict], expected_hashes: Dict[str, Optional[str]]) -> None:
    for op in ops:
        action = op.get("action")
        path = op.get("path")
        if not path:
            raise ValueError("file_operation missing 'path'")
        if action == "create":
            fs.create_file(path, op.get("content", ""))
        elif action == "replace":
            fs.replace_file(path, op.get("content", ""), expected_hash=expected_hashes.get(path))
        elif action == "patch":
            fs.patch_file(path, op.get("find", ""), op.get("replace", ""),
                           expected_hash=expected_hashes.get(path))
        elif action == "delete":
            fs.delete_file(path)
        elif action == "move":
            fs.move_file(path, op.get("new_path", ""))
        else:
            raise ValueError(f"Unknown file operation action: {action!r}")
