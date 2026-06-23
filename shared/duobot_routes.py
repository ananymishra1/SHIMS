"""REST routes for the Omni DuoBot chat interface.

Mounted on Instance A (primary) at /api/duobot and /omni-duobot.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from . import omni_duobot, duobot_tasks

router = APIRouter(prefix="/api/duobot", tags=["duobot"])


class CreateConversationRequest(BaseModel):
    topic: str = ""
    mode: str = "free"


class MessageRequest(BaseModel):
    content: str


class VoteRequest(BaseModel):
    action: str  # approve | reject


class ModeRequest(BaseModel):
    mode: str  # free | improvement | council


class AISettingsRequest(BaseModel):
    primary_provider: str | None = None
    primary_model: str | None = None
    local_model: str | None = None
    local_temperature: float | None = None
    council_auto_execute: bool | None = None
    council_members: list[str] | None = None
    council_chair: str | None = None
    council_rag_enabled: bool | None = None
    council_rag_limit: int | None = None
    council_personas: dict | None = None


class CouncilApprovalRequest(BaseModel):
    approval_id: str


class RethinkRequest(BaseModel):
    feedback: str = ""


class CreateTaskRequest(BaseModel):
    conv_id: str
    title: str
    description: str


@router.get("/settings/ai")
async def get_ai_settings() -> dict[str, Any]:
    return {"ok": True, "settings": omni_duobot.load_settings()}


@router.post("/settings/ai")
async def set_ai_settings(req: AISettingsRequest) -> dict[str, Any]:
    updates = req.model_dump(exclude_unset=True)
    return {"ok": True, "settings": omni_duobot.save_settings(updates)}


@router.get("/settings/ollama-models")
async def list_ollama_models() -> dict[str, Any]:
    import httpx
    from .config import settings
    host = str(settings.ollama_base_url).rstrip("/")
    try:
        r = httpx.get(f"{host}/api/tags", timeout=10)
        r.raise_for_status()
        models = [m.get("name", "") for m in r.json().get("models", [])]
        return {"ok": True, "models": models}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


@router.post("/conversations")
async def create_conv(req: CreateConversationRequest) -> dict[str, Any]:
    return omni_duobot.create_conversation(topic=req.topic, mode=req.mode)


@router.get("/conversations")
async def list_conversations(limit: int = 20) -> dict[str, Any]:
    return {"ok": True, "conversations": omni_duobot.list_conversations(limit=limit)}


@router.get("/capabilities")
async def check_capabilities() -> dict[str, Any]:
    return await omni_duobot.check_capabilities()


@router.get("/conversations/{conv_id}")
async def get_conv(conv_id: str) -> dict[str, Any]:
    conv = omni_duobot.get_conversation(conv_id)
    if not conv:
        return {"ok": False, "error": "conversation not found"}
    votes = omni_duobot.get_votes()
    return {"ok": True, "conversation": conv, "votes": votes}


@router.post("/conversations/{conv_id}/message")
async def add_message(conv_id: str, req: MessageRequest) -> dict[str, Any]:
    return omni_duobot.add_message(conv_id, "user", req.content)


@router.post("/conversations/{conv_id}/turn")
async def run_turn(conv_id: str) -> dict[str, Any]:
    return await omni_duobot.run_turn(conv_id)


@router.post("/conversations/{conv_id}/mode")
async def set_mode(conv_id: str, req: ModeRequest) -> dict[str, Any]:
    return omni_duobot.set_mode(conv_id, req.mode)


@router.post("/conversations/{conv_id}/capabilities")
async def refresh_capabilities(conv_id: str) -> dict[str, Any]:
    return await omni_duobot.refresh_conversation_capabilities(conv_id, force=True)


@router.post("/conversations/{conv_id}/finalize")
async def finalize(conv_id: str) -> dict[str, Any]:
    return await omni_duobot.finalize_conversation(conv_id)


@router.post("/conversations/{conv_id}/council/approve")
async def council_approve(conv_id: str, req: CouncilApprovalRequest) -> dict[str, Any]:
    return omni_duobot.approve_council_action(conv_id, req.approval_id)


@router.post("/conversations/{conv_id}/council/reject")
async def council_reject(conv_id: str, req: CouncilApprovalRequest) -> dict[str, Any]:
    return omni_duobot.reject_council_action(conv_id, req.approval_id)


@router.get("/proposals")
async def proposals_list(limit: int = 50) -> dict[str, Any]:
    return {"ok": True, "proposals": omni_duobot.get_pending_proposals(limit=limit), "votes": omni_duobot.get_votes()}


@router.post("/proposals/{proposal_id}/vote")
async def proposal_vote(proposal_id: str, req: VoteRequest) -> dict[str, Any]:
    return omni_duobot.record_vote(proposal_id, req.action)


@router.post("/proposals/{proposal_id}/apply")
async def proposal_apply(proposal_id: str) -> dict[str, Any]:
    return omni_duobot.apply_approved_proposal(proposal_id)


@router.post("/proposals/{proposal_id}/delete")
async def proposal_delete(proposal_id: str) -> dict[str, Any]:
    return omni_duobot.delete_proposal(proposal_id)


@router.post("/proposals/{proposal_id}/rethink")
async def proposal_rethink(proposal_id: str, req: RethinkRequest) -> dict[str, Any]:
    return omni_duobot.rethink_proposal(proposal_id, req.feedback)


@router.post("/tasks")
async def create_task(req: CreateTaskRequest) -> dict[str, Any]:
    return duobot_tasks.create_task(req.conv_id, req.title, req.description)


@router.get("/tasks")
async def list_tasks(conv_id: str | None = None, limit: int = 20) -> dict[str, Any]:
    return {"ok": True, "tasks": duobot_tasks.list_tasks(conv_id=conv_id, limit=limit)}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    task = duobot_tasks.get_task(task_id)
    if not task:
        return {"ok": False, "error": "task not found"}
    return {"ok": True, "task": task}


@router.post("/tasks/{task_id}/round")
async def task_round(task_id: str) -> dict[str, Any]:
    return await duobot_tasks.run_collaboration_round(task_id)


@router.post("/tasks/{task_id}/run")
async def task_run(task_id: str, max_rounds: int = 10) -> dict[str, Any]:
    return await duobot_tasks.run_collaboration_loop(task_id, max_rounds=max_rounds)
