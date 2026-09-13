"""CustomAgentService — founder-defined agent roles (CustomAgentRoleConfig): both fully custom
roles created from Settings, and the seven specialist sub-agents this product ships with by
default (Vision, Frontend/Backend/Fullstack Testing, Troubleshoot, Deployment — "Integration" was
already a built-in implementer role, see models.BUILTIN_AGENT_ROLES, so it isn't re-seeded here).

All of them are stored as data, not code, so every role — shipped or founder-added — is equally
inspectable/editable from Settings; `built_in=True` only means "can't be deleted", not "can't be
edited". They're all `category="implementer"`, meaning the Planner can assign a plan item directly
to any of them (see agents/roles.py::_planner_system) — a Frontend Testing Agent plan item writing
a new test file, or a Troubleshoot Agent one proposing a genuinely different fix, are both natural
fits for the same file_operations contract every other implementer role already uses.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...db import get_db, utc_now_iso
from ..models import CustomAgentRoleConfig

SEED_ROLES: List[Dict[str, Any]] = [
    {
        "role": "vision", "label": "Vision Agent", "icon": "Eye",
        "system_prompt": (
            "You are the Vision Agent for Zanelvo Dev Studio: you look at real screenshots (from "
            "browser QA) and judge whether the UI actually matches the intended design/acceptance "
            "criteria — layout, spacing, responsive behavior, visual regressions, obviously broken "
            "rendering. You are evidence-based: only report what you can actually see in an "
            "attached screenshot, never guess at what one 'probably' shows. Propose concrete file "
            "edits (CSS/layout fixes) for anything genuinely wrong."
        ),
    },
    {
        "role": "frontend_testing", "label": "Frontend Testing Agent", "icon": "TestTube2",
        "system_prompt": (
            "You are the Frontend Testing Agent: you write frontend tests (component/unit tests, "
            "browser QA scenarios) for the plan items assigned to Design/Frontend. Never claim a "
            "test passed without it actually running — cite real TestRun evidence, never assume."
        ),
    },
    {
        "role": "backend_testing", "label": "Backend Testing Agent", "icon": "TestTube2",
        "system_prompt": (
            "You are the Backend Testing Agent: you write backend tests (API/unit/integration) "
            "for the plan items assigned to Backend/Integration. Never claim a test passed "
            "without it actually running — cite real TestRun evidence, never assume."
        ),
    },
    {
        "role": "fullstack_testing", "label": "Fullstack Testing Agent", "icon": "TestTube2",
        "system_prompt": (
            "You are the Fullstack Testing Agent: you reason about end-to-end coverage across an "
            "entire task's diff — frontend and backend together — catching gaps a per-layer test "
            "pass would miss (a frontend form that doesn't actually match its backend's "
            "validation, a race between two plan items). Never claim a test passed without it "
            "actually running."
        ),
    },
    {
        "role": "troubleshoot", "label": "Troubleshoot Agent", "icon": "Wrench",
        "system_prompt": (
            "You are the Troubleshoot Agent, invoked when the same plan item has failed more than "
            "once. You are given the failure-fingerprint history and every prior attempt's "
            "diff/error — your job is to identify the ACTUAL root cause (never just retry the "
            "same fix) and propose a genuinely different approach as concrete file edits."
        ),
    },
    {
        "role": "deployment", "label": "Deployment Agent", "icon": "Rocket",
        "system_prompt": (
            "You are the Deployment Agent: given a reviewed, approved diff, you write the commit "
            "message and PR description (what changed and why, test evidence, any migration/config "
            "steps a human needs to take before/after deploying) as a file edit (e.g. a PR "
            "description or CHANGELOG entry). Never claim something was tested or deployed unless "
            "the evidence you were given actually shows it."
        ),
    },
]


async def ensure_seed_roles() -> None:
    db = get_db()
    for spec in SEED_ROLES:
        # Reproduced live: list_roles() runs this on every call, and the frontend's first page
        # load after a fresh DB fires several role-dependent requests concurrently — two calls
        # racing past a find_one-then-insert_one check both see "not seeded yet" and both insert,
        # and the second hits ds_custom_agent_roles' unique index on `role` with an unhandled
        # DuplicateKeyError (a real 500 on first login). An upsert with $setOnInsert is atomic at
        # the document level, so a duplicate simply does nothing instead of raising.
        cfg = CustomAgentRoleConfig(category="implementer", built_in=True, **spec)
        await db.ds_custom_agent_roles.update_one(
            {"role": spec["role"]}, {"$setOnInsert": cfg.to_mongo()}, upsert=True)


async def list_roles() -> List[CustomAgentRoleConfig]:
    await ensure_seed_roles()
    docs = get_db().ds_custom_agent_roles.find().sort("created_at", 1)
    return [CustomAgentRoleConfig.from_mongo(d) async for d in docs]


async def get_role(role: str) -> Optional[CustomAgentRoleConfig]:
    doc = await get_db().ds_custom_agent_roles.find_one({"role": role})
    return CustomAgentRoleConfig.from_mongo(doc) if doc else None


async def create_role(*, role: str, label: str, system_prompt: str,
                       category: str = "implementer", icon: Optional[str] = None
                       ) -> CustomAgentRoleConfig:
    from ..models import BUILTIN_AGENT_ROLES

    if role in BUILTIN_AGENT_ROLES:
        raise ValueError(f"'{role}' is already a built-in role name")
    if await get_role(role):
        raise ValueError(f"Agent role '{role}' already exists")
    cfg = CustomAgentRoleConfig(role=role, label=label, system_prompt=system_prompt,
                                 category=category, icon=icon, built_in=False)
    res = await get_db().ds_custom_agent_roles.insert_one(cfg.to_mongo())
    cfg.id = str(res.inserted_id)
    return cfg


async def update_role(role: str, **fields: Any) -> CustomAgentRoleConfig:
    fields.pop("role", None)  # the role slug itself is immutable — it's the AgentConfiguration key
    fields["updated_at"] = utc_now_iso()
    await get_db().ds_custom_agent_roles.update_one({"role": role}, {"$set": fields})
    updated = await get_role(role)
    if not updated:
        raise ValueError(f"Agent role '{role}' not found")
    return updated


async def delete_role(role: str) -> None:
    existing = await get_role(role)
    if not existing:
        raise ValueError(f"Agent role '{role}' not found")
    if existing.built_in:
        raise ValueError(f"'{existing.label}' ships with Dev Studio and can't be deleted — "
                          "disable its Agent Configuration instead if you don't want it used.")
    await get_db().ds_custom_agent_roles.delete_one({"role": role})
