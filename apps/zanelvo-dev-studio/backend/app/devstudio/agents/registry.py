"""AgentRegistry — per-role AgentConfiguration (enabled, primary/fallback provider+model,
reasoning level, max attempts, automatic fallback), seeded with defaults and overridable per role
or wholesale via a global preset (ECONOMICAL/BALANCED/MAX_QUALITY)."""
from __future__ import annotations

from typing import Dict, List

from ...db import get_db, utc_now_iso
from ..models import AgentConfiguration, AgentRole, BUILTIN_AGENT_ROLES
from ..providers.registry import preset_for_role
from ..services import custom_agent_service

ROLES: List[AgentRole] = list(BUILTIN_AGENT_ROLES)

# Default tools_enabled per role. Deliberately conservative: only roles whose primary provider is
# Anthropic across EVERY preset (see providers/registry.py MODEL_PRESETS) get tools by default,
# because only Anthropic/Emergent implement generate_with_tools() today — giving a Gemini-primary
# role (design) or OpenAI-primary role (reviewer) default tools would silently make every one of
# their calls fail over to the Anthropic fallback model first, a real cost/latency regression, not
# a capability upgrade. Founders can still opt those roles in manually in Settings > Agents.
_DEFAULT_TOOLS_BY_ROLE: Dict[str, List[str]] = {
    "planner": ["ask_human", "web_search", "perplexity_research"],
    # analyze_image: Vision's whole job (see custom_agent_service.SEED_ROLES) is judging whether a
    # real screenshot matches the intended design — a real vision-model call on that exact image,
    # not the plain "screenshot" capture tool alone.
    "vision": ["screenshot", "analyze_image"],
    # view_file/search_files/execute_bash/view_logs: Troubleshoot's whole job is root-causing a
    # repeated failure — being able to actually look around the workspace, re-run a command, and
    # see what's already been tried mid-reasoning, not just react to whatever context it was
    # handed upfront, is exactly what that job needs.
    "troubleshoot": ["ask_human", "view_file", "search_files", "execute_bash", "view_logs"],
    # deployment_debugger: Deployment's job is writing the commit/PR description for a reviewed
    # diff — being able to check real CI status first means it can honestly report it instead of
    # assuming everything passed.
    "deployment": ["deployment_debugger"],
}


async def _all_roles() -> List[AgentRole]:
    """Built-in roles plus every founder-defined/seeded custom role (see
    services/custom_agent_service.py) — the full set that needs an AgentConfiguration."""
    custom = [r.role for r in await custom_agent_service.list_roles()]
    return ROLES + custom


def _default_config(role: AgentRole, preset: str = "BALANCED") -> AgentConfiguration:
    p = preset_for_role(preset, role)
    return AgentConfiguration(
        role=role, enabled=True,
        primary_provider=p["primary_provider"], primary_model=p["primary_model"],
        fallback_provider=p["fallback_provider"], fallback_model=p["fallback_model"],
        reasoning_level="medium" if role in ("planner", "reviewer") else None,
        max_attempts=2, automatic_fallback=True,
        tools_enabled=list(_DEFAULT_TOOLS_BY_ROLE.get(role, [])),
    )


async def ensure_defaults(preset: str = "BALANCED") -> None:
    db = get_db()
    for role in await _all_roles():
        existing = await db.ds_agent_configs.find_one({"role": role})
        if not existing:
            await db.ds_agent_configs.insert_one(_default_config(role, preset).to_mongo())


async def get_config(role: AgentRole) -> AgentConfiguration:
    doc = await get_db().ds_agent_configs.find_one({"role": role})
    if not doc:
        cfg = _default_config(role)
        res = await get_db().ds_agent_configs.insert_one(cfg.to_mongo())
        cfg.id = str(res.inserted_id)
        return cfg
    return AgentConfiguration.from_mongo(doc)


async def list_configs() -> Dict[str, AgentConfiguration]:
    await ensure_defaults()
    docs = get_db().ds_agent_configs.find({})
    out = {}
    async for d in docs:
        cfg = AgentConfiguration.from_mongo(d)
        out[cfg.role] = cfg
    return out


async def update_config(role: AgentRole, **fields) -> AgentConfiguration:
    fields["updated_at"] = utc_now_iso()
    await get_db().ds_agent_configs.update_one({"role": role}, {"$set": fields}, upsert=True)
    return await get_config(role)


async def apply_preset(preset: str) -> Dict[str, AgentConfiguration]:
    for role in await _all_roles():
        p = preset_for_role(preset, role)
        await update_config(role, primary_provider=p["primary_provider"], primary_model=p["primary_model"],
                             fallback_provider=p["fallback_provider"], fallback_model=p["fallback_model"])
    return await list_configs()
