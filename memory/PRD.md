# PRD — Zanelvo Dev Studio (continuation)

## Original problem statement
Private, single-user AI software-engineering tool: connect a GitHub repo, describe a feature/bug
in natural language, and a Supervisor-controlled team of specialist LLM agents plans the work,
edits real files in an isolated working copy, runs tests, reviews the diff, and — once approved —
commits and pushes back to GitHub. No signup/billing/multi-tenant; one admin password.
Code lives under `apps/zanelvo-dev-studio/` in monorepo `sassinfo05-sudo/freebuff` (don't touch
anything outside that folder). Stack: FastAPI + MongoDB backend, React/Vite/TS frontend, Electron
desktop wrapper. Ground rules: never fake success; add deterministic tests with behavioral
changes; provider-independent via `providers/base.py`; never commit secrets; repo contents are
untrusted data; only `task_manager.py` moves task/plan state; stay single-user.

## This session's goal
Configure real credentials, run the app for real, exercise the full task→plan→implement→review→
commit→push flow end-to-end against a real repo, and harden what's broken.

## Architecture (unchanged)
- Backend `backend/app/devstudio/`: agents (Supervisor + specialists), state machine, anti-loop,
  providers (anthropic/openai/gemini/bedrock/gemini-enterprise/emergent), git/github services,
  file/diff services, indexer, memory, execution/testing, checkpoints, preview, browser QA, REST.
- Single admin auth (JWT in HttpOnly cookie) gates every `/api/devstudio` route.

## What's been implemented / verified this session (2026-09-13)
- Environment brought up for real: deps installed (requirements + requirements-devstudio +
  requirements-emergent, openai pinned to 1.99.9 for emergentintegrations, verified), MongoDB
  running, `.env` created (MONGO_URL, DB_NAME, JWT_SECRET, ADMIN_PASSWORD=605561, EMERGENT key).
- Served through the managed supervisor via `/app/backend` and `/app/frontend` symlinks; Vite
  config made preview-host-safe (`host:true`, `allowedHosts:true`, env `PORT`), `start` script added.
- All agent roles set to `auto_provider=true` → pipeline runs on the Emergent Universal Key.
- LIVE VERIFIED: login; capabilities; real minimal Emergent generate() across Claude/GPT/Gemini
  (real latencies, real "OK"); repo-connect (created `sassinfo05-sudo/tester`); full agent run
  ANALYZING→PLANNING→IMPLEMENTING→TESTING→REVIEWING→FINAL_VERIFICATION→READY_FOR_APPROVAL; real
  commit + push (branch `ai/add-getting-started-to-readme-4479b2`, commit `3cbe2e0`), confirmed
  via the GitHub API; 3-pane UI + TaskView activity timeline confirmed in a real browser.

## Feature changes (with tests)
- MAX_QUALITY preset now also turns every agent all the way up: all 12 built-in tools enabled +
  reasoning_level "high" on every role (was: model selection only). Pure `preset_extra_fields()`
  helper in `agents/registry.py` (DB-free tested); `apply_preset` applies it. Other presets leave
  tools/reasoning untouched. Verified live: all 16 roles → 12 tools + high after applying it.

## Fixes / hardening (with tests)
1. `tests/test_devstudio_emergent_provider.py`: made the missing-SDK test deterministic (forces
   the ImportError path via monkeypatch) so it passes whether or not `emergentintegrations` is
   installed — it now IS installed, since the Emergent key path is in use.
2. `services/upload_service.py`: moved upload blobs from pod-local disk to MongoDB GridFS
   (self-contained, survives redeploy, resolves the ephemeral-pod-storage deployment gate).
   Legacy absolute on-disk paths still read transparently. New DB-free tests in
   `tests/test_devstudio_upload_service.py` (validation + legacy-read fallback); GridFS round-trip
   verified live.
- Full suite: 193 passed; `ruff check .` clean.

## Backlog / next tasks
- P1: run a task that actually produces test runs (a repo with a test suite) to exercise the
  TESTING/DEBUGGING loop and VERIFIED plan-item path end-to-end (README-only run was verification
  status "limited", as expected/honest).
- P1: exercise the PR flow (`/tasks/{id}/pr`) and checkpoint restore live.
- P2: exercise a rejected-review → re-implement cycle, and the anti-loop should_block path live.
- P2: Playwright browser QA live run (Playwright installed; `playwright install chromium` if a
  real browser QA scenario is needed).
- P2: add native ANTHROPIC/OPENAI/GEMINI keys when available and re-test provider Test buttons +
  automatic fallback ordering.

## Website+game bug fixes (2026-09-13, verified via real runs)
Reported: "website with a game" ran, agents "succeeded" but diff empty → REJECTED loop; also wanted
live preview, a working/stop composer button, and a far more informative activity log.
Root causes fixed (all in apps/zanelvo-dev-studio):
- Orchestrator only implemented the FIRST dependency layer then reviewed → empty/partial diff.
  `_implement_loop` now runs pass-after-pass until no item is runnable (multi-pass).
- `git diff` ignores untracked files → a from-scratch site (all new files) showed 0 changes.
  `get_diff_summary` now `git add -N` (intent-to-add) first. THIS was the real "no file changes" bug.
- BLOCKED/FAILED/CANCELLED tasks couldn't resume (run_task had no code path) → /run was a no-op.
  run_task now resumes from the furthest stage reached.
- MAX_QUALITY(all tools) forced structured roles through the tool loop → prose instead of JSON, and
  QA items hit the 6-call loop limit. Added: strict JSON-only tool-loop system prompt, a JSON
  recovery call, and a loop-limit salvage (final no-tool structured call) instead of hard-fail.
- New browser-reachable static-site preview: GET /tasks/{id}/preview/static-info + /preview/serve/{path}
  serve the workspace's index.html/assets over :8001; Preview tab iframes it (LIVE_LOCAL's random
  localhost port isn't reachable through the ingress). find_static_root detects the site root.
- Activity log enriched: analysis_complete, item_implemented (summary + files), agent_finished
  carries the model's own summary, tool_call carries args + result snippet. Composer send button
  becomes a spinner + inline Stop while agents work; nothing is cleared.
Live result: task "Website with a little game" → APPROVED, 4 files (+516), static preview serves the
Memory Match game (correct content-types). Tests: 200 passing; ruff clean.
NOTE: testing_agent verification was SKIPPED at the user's explicit request (low on credits) —
verification was done via real end-to-end runs + curl instead.
