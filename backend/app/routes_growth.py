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
    from shared.licensing import verify_license, current_entitlements
    lic = verify_license(req.key.strip())
    if not lic or not lic.valid:
        return {"ok": False, "error": "invalid_or_expired_license"}
    os.environ["SHIMS_LICENSE_KEY"] = req.key.strip()
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
