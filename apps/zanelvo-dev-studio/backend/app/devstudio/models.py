"""Zanelvo Dev Studio — data model.

Private, single-user AI software-development environment. Collections are prefixed `ds_` so this
app's data is easy to spot in Mongo (this is the whole app, so the prefix isn't for isolation from
anything else — it's just a clear namespace). Every document extends `BaseDocument` (Mongo `_id`
<-> `id`, created_at/updated_at).

There is no tenant/org concept — Dev Studio has exactly one user (see CLAUDE.md: no
signup/billing/team administration). Every route is gated by `require_auth` (see
`devstudio/security.py` / `app/deps.py`).
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ..db import BaseDocument

# --- Task state machine -----------------------------------------------------------------

TaskStatus = Literal[
    "CREATED", "UNDERSTANDING", "ANALYZING_REPOSITORY", "PLANNING", "READY",
    "IMPLEMENTING", "TESTING", "DEBUGGING", "REVIEWING", "FINAL_VERIFICATION",
    "READY_FOR_APPROVAL", "COMMITTING", "PUSHING", "COMPLETED", "BLOCKED",
    "FAILED", "CANCELLED",
]

PlanItemStatus = Literal[
    "PENDING", "READY", "RUNNING", "IMPLEMENTED", "VERIFYING", "VERIFIED",
    "BLOCKED", "FAILED", "SKIPPED",
]

TaskMode = Literal["feature", "bugfix", "refactor", "chore", "investigation"]

# Plain str, not a Literal: founders can define custom agent roles at runtime (see
# CustomAgentRoleConfig below) whose names Pydantic can't know about statically. The set of
# actually-valid roles is enforced at the registry/DB layer (agents/registry.py + roles.py), not
# via static typing — the same tradeoff CreateTaskRequest.mode etc. don't need since those really
# are closed sets.
AgentRole = str

# The fixed, always-present roles — every other AgentRole value is a custom one from
# CustomAgentRoleConfig (which includes the seeded Vision/Testing/Integration/Troubleshoot/
# Deployment specialists — "built-in" in the product sense, but stored as data, not code, so
# they're editable like any other custom role).
BUILTIN_AGENT_ROLES = (
    "supervisor", "repository_analyst", "planner", "design", "frontend", "backend",
    "integration", "qa", "reviewer", "git",
)

ModelPreset = Literal["ECONOMICAL", "BALANCED", "MAX_QUALITY", "CUSTOM"]

PreviewMode = Literal["LIVE_LOCAL", "SCREENSHOT_ONLY", "EXTERNAL_URL"]

MemoryCategory = Literal[
    "PROJECT_OVERVIEW", "ARCHITECTURE", "FRONTEND", "BACKEND", "DATABASE", "AUTHENTICATION",
    "AUTHORIZATION", "INTEGRATIONS", "DESIGN_SYSTEM", "CODING_CONVENTIONS",
    "ENVIRONMENT_VARIABLES", "IMPORTANT_FILES", "MAJOR_DECISIONS", "COMPLETED_FEATURES",
    "KNOWN_BUGS", "OUTSTANDING_WORK", "FAILED_APPROACHES", "TESTING", "DEPLOYMENT_NOTES",
]

CapabilityClass = Literal[
    "live_verified", "live_requires_credentials", "demo_only", "structural_not_connected",
    "blocked", "removed", "missing",
]


# --- Core project / repository / workspace ----------------------------------------------

class Project(BaseDocument):
    """One tracked GitHub repository the founder is developing with Dev Studio."""
    name: str
    github_owner: str
    github_repo: str
    default_branch: str = "main"
    description: Optional[str] = None
    last_synced_commit_sha: Optional[str] = None
    last_indexed_commit_sha: Optional[str] = None
    archived: bool = False


class RepositorySnapshot(BaseDocument):
    project_id: str
    branch: str
    commit_sha: str
    tree_file_count: int = 0
    languages: Dict[str, int] = Field(default_factory=dict)   # language -> file count
    package_manifests: List[str] = Field(default_factory=list)
    frontend_root: Optional[str] = None
    backend_root: Optional[str] = None
    indexed: bool = False
    indexed_at: Optional[str] = None


class Workspace(BaseDocument):
    project_id: str
    task_id: Optional[str] = None
    base_branch: str
    base_commit_sha: str
    working_branch: str
    local_path: str  # absolute path on server disk, under backend/var/devstudio/workspaces
    remote_head_sha: Optional[str] = None   # last known remote HEAD of base_branch, for drift detection
    status: Literal["provisioning", "ready", "stale", "discarded"] = "provisioning"


# --- Tasks ---------------------------------------------------------------------------------

class Task(BaseDocument):
    project_id: str
    workspace_id: Optional[str] = None
    title: str
    request_text: str
    mode: TaskMode = "feature"
    status: TaskStatus = "CREATED"
    branch: Optional[str] = None
    base_commit_sha: Optional[str] = None
    iteration_budget: int = 8           # BALANCED default; see anti_loop.ORCHESTRATION_BUDGETS
    iterations_used: int = 0
    model_preset: ModelPreset = "BALANCED"
    verification_status: Literal["unverified", "partial", "verified", "limited"] = "unverified"
    current_diff_summary: Optional[Dict[str, Any]] = None
    review_verdict: Optional[Literal["APPROVED", "APPROVED_WITH_WARNINGS", "REJECTED"]] = None
    completion_note: Optional[str] = None
    stop_requested: bool = False
    blocked_reason: Optional[str] = None
    created_by: Optional[str] = None    # staff user id
    archived: bool = False               # soft-delete — see DELETE /tasks/{id}, list_tasks() filters it out


class TaskMessage(BaseDocument):
    task_id: str
    role: Literal["user", "supervisor", "system"]
    text: str
    attachments: List[str] = Field(default_factory=list)  # Upload ids


class PlanItem(BaseDocument):
    task_id: str
    order: int = 0
    title: str
    description: str
    assigned_agent: AgentRole = "backend"
    relevant_files: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    verification_method: str = ""
    depends_on: List[str] = Field(default_factory=list)  # other PlanItem ids
    status: PlanItemStatus = "PENDING"
    evidence: List[Dict[str, Any]] = Field(default_factory=list)  # test/build ids proving VERIFIED


# --- Agents ----------------------------------------------------------------------------

class AgentConfiguration(BaseDocument):
    role: AgentRole
    enabled: bool = True
    primary_provider: str = "anthropic"
    primary_model: str = "claude-sonnet-5"
    fallback_provider: Optional[str] = "anthropic"
    fallback_model: Optional[str] = "claude-haiku-4-5"
    reasoning_level: Optional[Literal["low", "medium", "high"]] = None
    max_attempts: int = 2
    automatic_fallback: bool = True
    # Non-empty in either field switches this role's calls onto the tool-calling loop (see
    # agents/runner.py::call_with_tools) instead of a plain one-shot structured call — the model
    # can then call any tool from these MCP servers plus these built-in tools before answering.
    mcp_servers: List[str] = Field(default_factory=list)          # MCPServerConfig.name values
    tools_enabled: List[str] = Field(default_factory=list)        # BUILTIN_TOOLS keys (runner.py)


class CustomAgentRoleConfig(BaseDocument):
    """A founder-defined agent role — either a brand-new one, or one of the seeded specialist
    roles (Vision/Frontend Testing/Backend Testing/Fullstack Testing/Troubleshoot/Deployment —
    "integration" already existed as a built-in implementer role, see BUILTIN_AGENT_ROLES). Stored
    as data rather than code so they're all equally editable; `built_in=True` just means "don't
    let this get deleted" for the ones the product ships with by default."""
    role: str                                    # unique slug; used as AgentConfiguration.role
    label: str                                   # display name, e.g. "Vision Agent"
    system_prompt: str
    category: Literal["implementer", "specialist"] = "specialist"
    # implementer roles are selectable by the Planner as a plan item's assigned_agent (like the
    # built-in design/frontend/backend/integration); specialist roles are invoked by name for a
    # specific step (like qa/reviewer/git) and never appear in the Planner's own menu.
    built_in: bool = False
    icon: Optional[str] = None                    # lucide-react icon name, frontend-only concern


class MCPServerConfig(BaseDocument):
    """A connected MCP (Model Context Protocol) server — either a preset (Notion, Memory) or a
    fully custom one. See services/mcp_service.py for the real client that connects to these."""
    name: str                                      # unique; also the tool-routing key
    transport: Literal["stdio", "http"] = "stdio"
    command: Optional[str] = None                  # stdio: e.g. "npx"
    args: List[str] = Field(default_factory=list)   # stdio: e.g. ["-y", "@notionhq/notion-mcp-server"]
    url: Optional[str] = None                       # http: streamable-HTTP endpoint
    env: Dict[str, str] = Field(default_factory=dict)   # values stored ENCRYPTED — see mcp_service.py
    enabled: bool = True
    preset: Optional[str] = None                    # "notion" | "memory" | None (fully custom)
    last_tool_count: Optional[int] = None            # from the last successful test_server() call
    last_checked_at: Optional[str] = None
    last_error: Optional[str] = None                 # from the last FAILED test_server() call


class AgentRun(BaseDocument):
    task_id: str
    plan_item_id: Optional[str] = None
    role: AgentRole
    attempt: int = 1
    provider: str
    model: str
    status: Literal["running", "succeeded", "failed"] = "running"
    action: str = ""            # short human-readable current action
    result_summary: Optional[str] = None
    duration_ms: Optional[int] = None
    started_at: str
    finished_at: Optional[str] = None


class AgentHandoff(BaseDocument):
    task_id: str
    from_role: AgentRole
    to_role: AgentRole
    objective: str
    attempts: int
    files: List[str] = Field(default_factory=list)
    diff_summary: Optional[str] = None
    failure: Optional[str] = None
    fingerprint: Optional[str] = None
    tests: List[str] = Field(default_factory=list)
    what_did_not_work: Optional[str] = None
    next_hypothesis: Optional[str] = None


class ToolExecution(BaseDocument):
    task_id: str
    agent_run_id: Optional[str] = None
    tool: str
    args_summary: Optional[str] = None
    ok: bool = True
    error: Optional[str] = None
    duration_ms: Optional[int] = None


class LLMInvocation(BaseDocument):
    task_id: str
    agent_run_id: Optional[str] = None
    role: AgentRole
    provider: str
    model: str
    kind: Literal["generate", "generate_structured", "generate_with_vision", "stream"] = "generate"
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    ok: bool = True
    error: Optional[str] = None


# --- Memory ------------------------------------------------------------------------------

class ProjectMemory(BaseDocument):
    project_id: str
    branch: Optional[str] = None
    category: MemoryCategory
    title: str
    content: str
    commit_sha: Optional[str] = None
    source_task_id: Optional[str] = None
    confidence: Literal["low", "medium", "high"] = "medium"
    stale: bool = False


class MemoryRevision(BaseDocument):
    memory_id: str
    previous_content: str
    reason: str


# --- Failures / anti-loop -----------------------------------------------------------------

class FailureRecord(BaseDocument):
    task_id: str
    plan_item_id: Optional[str] = None
    fingerprint: str
    command: Optional[str] = None
    failure_class: str
    normalized_error: str
    subsystem: Optional[str] = None
    occurrences: int = 1
    first_seen_at: str
    last_seen_at: str


# --- Testing / browser -----------------------------------------------------------------

class TestRun(BaseDocument):
    task_id: str
    test_type: Literal["setup", "frontend_unit", "backend_unit", "lint", "typecheck", "build",
                        "integration", "e2e", "java_unit", "agent_tool"]
    command: str
    status: Literal["running", "passed", "failed", "error", "skipped"] = "running"
    duration_ms: Optional[int] = None
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    stdout_tail: Optional[str] = None
    stderr_tail: Optional[str] = None
    exit_code: Optional[int] = None


class TestResult(BaseDocument):
    test_run_id: str
    name: str
    status: Literal["passed", "failed", "skipped"]
    duration_ms: Optional[int] = None
    failure_output: Optional[str] = None


class BrowserRun(BaseDocument):
    task_id: str
    scenario: str
    status: Literal["running", "passed", "failed", "unavailable"] = "running"
    console_errors: List[str] = Field(default_factory=list)
    failed_requests: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class Screenshot(BaseDocument):
    task_id: str
    browser_run_id: Optional[str] = None
    label: str
    path: str            # server-disk path under backend/var/devstudio
    viewport: Optional[str] = None


# --- Checkpoints / git ---------------------------------------------------------------------

class Checkpoint(BaseDocument):
    task_id: str
    label: str
    commit_sha: str
    branch: str
    reason: Optional[str] = None


class GitOperation(BaseDocument):
    task_id: str
    op: Literal["clone", "fetch", "branch", "checkout", "commit", "push", "pr", "diff", "status", "reset"]
    ok: bool = True
    detail: Optional[str] = None
    sha_before: Optional[str] = None
    sha_after: Optional[str] = None


# --- Uploads / activity / settings -----------------------------------------------------------

class Upload(BaseDocument):
    task_id: Optional[str] = None
    project_id: Optional[str] = None
    filename: str
    content_type: str
    size_bytes: int
    path: str
    is_image: bool = False
    attach_to_vision: bool = False


class ActivityEvent(BaseDocument):
    task_id: str
    kind: str          # e.g. "state_change", "agent_started", "tool_call", "test_result", "message"
    payload: Dict[str, Any] = Field(default_factory=dict)


class ApplicationSettings(BaseDocument):
    key: str = "singleton"
    model_preset: ModelPreset = "BALANCED"
    default_orchestration_budget: int = 8
    github_username: Optional[str] = None
    preview_mode: PreviewMode = "SCREENSHOT_ONLY"


# --- Repository index (RepositoryIndexer output; not in the spec's named list but required by
# "For each indexed file record: path/language/hash/size/summary/symbols/relationships/commit") ---

class IndexedFile(BaseDocument):
    project_id: str
    branch: str
    commit_sha: str
    path: str
    language: Optional[str] = None
    hash: str
    size_bytes: int
    summary: Optional[str] = None
    symbols: List[str] = Field(default_factory=list)
    imports: List[str] = Field(default_factory=list)


# --- API-facing request bodies (not persisted) ------------------------------------------------

class CreateProjectRequest(BaseModel):
    github_owner: str
    github_repo: str
    default_branch: str = "main"
    name: Optional[str] = None
    description: Optional[str] = None


class CreateRepoAndProjectRequest(BaseModel):
    """A brand-new GitHub repository, not a connection to an existing one (see
    CreateProjectRequest for that) — repo_name is the actual github.com repo name (letters,
    digits, ., -, _), which name (the Dev Studio Project display name) may differ from."""
    repo_name: str
    private: bool = True
    description: Optional[str] = None
    name: Optional[str] = None


class CreateTaskRequest(BaseModel):
    project_id: str
    branch: str
    # Both optional so a "New Chat" click can create a blank chat immediately, ChatGPT/Claude-style
    # — no upfront form. request_text (and title, if left default) is backfilled from the first
    # real message; see post_task_message in routes.py.
    title: Optional[str] = None
    request_text: str = ""
    mode: TaskMode = "feature"
    model_preset: ModelPreset = "BALANCED"


class TaskUpdateRequest(BaseModel):
    title: Optional[str] = None


class TaskMessageRequest(BaseModel):
    text: str
    attachments: List[str] = Field(default_factory=list)


class CapabilityReport(BaseModel):
    name: str
    available: bool
    detail: Optional[str] = None
