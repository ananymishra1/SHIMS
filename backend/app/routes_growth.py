"""SHIMS growth & product API — behavior learning, licensing, marketplace, cortex.

Self-contained APIRouter so it can be included by ``main.py`` with one guarded
line. Everything here is additive: if a dependency is missing the endpoint
returns a clean error rather than breaking app boot.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["growth"])


# --------------------------------------------------------------------------- #
# Behavior engine
# --------------------------------------------------------------------------- #

class BehaviorRecordRequest(BaseModel):
    action: str
    context: str = ""
    user_id: str = "default"


class BehaviorFeedbackRequest(BaseModel):
    action: str
    positive: bool = True
    user_id: str = "default"


@router.get("/behavior/suggestions")
async def behavior_suggestions(user_id: str = "default") -> dict[str, Any]:
    from shared.behavior_engine import get_behavior_engine
    eng = get_behavior_engine(user_id)
    return {"ok": True, "stats": eng.stats(),
            "suggestion": (s.to_dict() if (s := eng.suggest()) else None),
            "context_block": eng.to_context()}


@router.post("/behavior/record")
async def behavior_record(req: BehaviorRecordRequest) -> dict[str, Any]:
    from shared.behavior_engine import get_behavior_engine
    get_behavior_engine(req.user_id).record(req.action, context=req.context)
    return {"ok": True}


@router.post("/behavior/feedback")
async def behavior_feedback(req: BehaviorFeedbackRequest) -> dict[str, Any]:
    from shared.behavior_engine import get_behavior_engine
    get_behavior_engine(req.user_id).reinforce(req.action, req.positive)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Licensing / entitlements
# --------------------------------------------------------------------------- #

class LicenseActivateRequest(BaseModel):
    key: str


@router.get("/license")
async def license_status() -> dict[str, Any]:
    from shared.licensing import current_entitlements
    return current_entitlements()


@router.post("/license/activate")
async def license_activate(req: LicenseActivateRequest) -> dict[str, Any]:
    import os
    from shared.licensing import verify_license, current_entitlements, save_license_key
    lic = verify_license(req.key.strip())
    if not lic or not lic.valid:
        return {"ok": False, "error": "invalid_or_expired_license"}
    os.environ["SHIMS_LICENSE_KEY"] = req.key.strip()
    save_license_key(req.key.strip())  # durable across restarts
    return {"ok": True, "entitlements": current_entitlements()}


# --------------------------------------------------------------------------- #
# Skill marketplace
# --------------------------------------------------------------------------- #

class MarketInstallRequest(BaseModel):
    slug: str


class PackImportRequest(BaseModel):
    pack: dict[str, Any]
    overwrite: bool = False


@router.get("/marketplace/skills")
async def marketplace_skills(category: Optional[str] = None,
                             query: Optional[str] = None) -> dict[str, Any]:
    from shared import skill_marketplace as mk
    return {"ok": True, "categories": mk.categories(),
            "skills": mk.list_catalog(category=category, query=query)}


@router.post("/marketplace/install")
async def marketplace_install(req: MarketInstallRequest) -> dict[str, Any]:
    from shared import skill_marketplace as mk
    return mk.install(req.slug)


@router.get("/marketplace/export")
async def marketplace_export() -> dict[str, Any]:
    from shared import skill_marketplace as mk
    return {"ok": True, "pack": mk.export_pack()}


@router.post("/marketplace/import")
async def marketplace_import(req: PackImportRequest) -> dict[str, Any]:
    from shared import skill_marketplace as mk
    return mk.import_pack(req.pack, overwrite=req.overwrite)


# --------------------------------------------------------------------------- #
# Cortex / self-evolution status
# --------------------------------------------------------------------------- #

@router.get("/cortex/status")
async def cortex_status() -> dict[str, Any]:
    from shared import cortex
    return cortex.status()


class PromptOverlayRequest(BaseModel):
    text: str
    reason: str = ""


@router.post("/cortex/prompt-overlay")
async def cortex_prompt_overlay(req: PromptOverlayRequest) -> dict[str, Any]:
    from shared import cortex
    return cortex.set_prompt_overlay(req.text, reason=req.reason)


# --------------------------------------------------------------------------- #
# Teams (Pro/Enterprise workspaces)
# --------------------------------------------------------------------------- #

class TeamCreateRequest(BaseModel):
    name: str
    owner_email: str


class TeamMemberRequest(BaseModel):
    team_id: str
    email: str
    role: str = "member"


class InviteAcceptRequest(BaseModel):
    team_id: str
    token: str


@router.get("/teams")
async def teams_list() -> dict[str, Any]:
    from shared import teams
    return {"ok": True, "teams": teams.list_teams()}


@router.post("/teams")
async def teams_create(req: TeamCreateRequest) -> dict[str, Any]:
    from shared import teams, licensing
    if not licensing.is_entitled("team_skill_library"):
        ok, payload = licensing.require("team_skill_library")
        return payload
    t = teams.create_team(req.name, req.owner_email)
    return {"ok": True, "team": t.to_dict()}


@router.get("/teams/{team_id}")
async def teams_get(team_id: str) -> dict[str, Any]:
    from shared import teams
    t = teams.get_team(team_id)
    return {"ok": True, "team": t.to_dict()} if t else {"ok": False, "error": "not_found"}


@router.post("/teams/invite")
async def teams_invite(req: TeamMemberRequest) -> dict[str, Any]:
    from shared import teams
    t = teams.get_team(req.team_id)
    if not t:
        return {"ok": False, "error": "not_found"}
    try:
        inv = t.invite(req.email, req.role)
        return {"ok": True, "invite": inv.to_dict()}
    except teams.TeamError as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/teams/accept")
async def teams_accept(req: InviteAcceptRequest) -> dict[str, Any]:
    from shared import teams
    t = teams.get_team(req.team_id)
    if not t:
        return {"ok": False, "error": "not_found"}
    try:
        m = t.accept_invite(req.token)
        return {"ok": True, "member": m.to_dict()}
    except teams.TeamError as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/teams/role")
async def teams_role(req: TeamMemberRequest) -> dict[str, Any]:
    from shared import teams
    t = teams.get_team(req.team_id)
    if not t:
        return {"ok": False, "error": "not_found"}
    try:
        m = t.set_role(req.email, req.role)
        return {"ok": True, "member": m.to_dict()}
    except teams.TeamError as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/teams/remove")
async def teams_remove(req: TeamMemberRequest) -> dict[str, Any]:
    from shared import teams
    t = teams.get_team(req.team_id)
    if not t:
        return {"ok": False, "error": "not_found"}
    try:
        return {"ok": t.remove_member(req.email)}
    except teams.TeamError as exc:
        return {"ok": False, "error": str(exc)}


# --------------------------------------------------------------------------- #
# SSO (enterprise OIDC)
# --------------------------------------------------------------------------- #

@router.get("/auth/sso/status")
async def sso_status() -> dict[str, Any]:
    from shared import sso
    return sso.status()


@router.get("/auth/sso/login")
async def sso_login() -> dict[str, Any]:
    from shared import sso
    return sso.begin_login()


class SSOCallbackRequest(BaseModel):
    code: str
    state: str


@router.post("/auth/sso/callback")
async def sso_callback(req: SSOCallbackRequest) -> dict[str, Any]:
    from shared import sso
    return await sso.complete_login(req.code, req.state)


# --------------------------------------------------------------------------- #
# Skill registry (hosted marketplace layer — this instance can BE a registry)
# --------------------------------------------------------------------------- #

class RegistryPublishRequest(BaseModel):
    name: str
    summary: str = ""
    body: str = ""
    tags: list[str] = []


@router.get("/registry/status")
async def registry_status() -> dict[str, Any]:
    from shared import skill_registry
    return skill_registry.status()


@router.get("/registry/skills")
async def registry_skills(query: Optional[str] = None) -> dict[str, Any]:
    """Serve this instance's published skills (so it can act as a registry hub)."""
    from shared import skill_registry
    items = skill_registry.published_catalog()
    if query:
        q = query.lower()
        items = [e for e in items if q in e.get("name", "").lower() or q in e.get("summary", "").lower()]
    return {"ok": True, "skills": items}


@router.post("/registry/publish")
async def registry_publish(req: RegistryPublishRequest) -> dict[str, Any]:
    from shared import skill_registry
    return skill_registry.publish_local(req.name, req.summary, body=req.body, tags=req.tags)
