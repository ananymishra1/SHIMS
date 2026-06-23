from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEFAULT_CHANNELS = ["email", "linkedin", "whatsapp", "landing_page"]


def _clean(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def plan_campaign(
    *,
    objective: str,
    audience: str = "",
    offer: str = "",
    channels: list[str] | None = None,
    tone: str = "clear, useful, credible",
    due_date: str = "",
) -> dict[str, Any]:
    objective = _clean(objective or "Grow qualified business leads", 300)
    audience = _clean(audience or "current customers and qualified prospects", 220)
    offer = _clean(offer or "a practical SHIMS-powered solution briefing", 220)
    channels = [str(c).strip().lower().replace(" ", "_") for c in (channels or DEFAULT_CHANNELS) if str(c).strip()]
    channels = channels or list(DEFAULT_CHANNELS)
    tone = _clean(tone or "clear, useful, credible", 180)

    brief = {
        "objective": objective,
        "audience": audience,
        "offer": offer,
        "tone": tone,
        "due_date": due_date,
        "positioning": f"{offer} for {audience}, framed around measurable daily usefulness and verification-first AI.",
        "success_metrics": ["qualified replies", "booked demos", "saved operator time", "verified task completion"],
    }
    drafts = {
        "email_subjects": [
            f"Can SHIMS remove one daily bottleneck for {audience}?",
            "A verification-first AI operator for daily business work",
            f"{offer}: draft, verify, and follow up from one workspace",
        ],
        "email_body": (
            f"Hi,\n\nWe are preparing {offer} for {audience}. SHIMS focuses on useful daily execution: "
            "capturing work, drafting documents, planning follow-ups, and showing evidence for what it knows or creates.\n\n"
            "Would a short walkthrough be useful this week?\n\nRegards,\nSHIMS"
        ),
        "linkedin_post": (
            f"We are shaping SHIMS around a simple standard: useful AI must show its work. "
            f"For {audience}, that means mailbox capture, task follow-up, document/media drafting, and action proof."
        ),
        "whatsapp_note": f"Sharing a quick SHIMS update: {offer}. It drafts, captures, verifies, and keeps follow-ups visible.",
        "landing_page_outline": [
            "Problem: daily work is scattered across inboxes, files, calls, and tools.",
            "Promise: SHIMS drafts and organizes work while showing evidence and action proof.",
            "Proof: trust badges, action ledger, mailbox/capture memory, local-first controls.",
            "CTA: request a workflow demo.",
        ],
    }
    asset_checklist = [
        "One-page product PDF",
        "Short demo video storyboard",
        "Three proof screenshots: mailbox, trust badge, action ledger",
        "Approved email list or opt-in audience",
        "Human approval before external send/post",
    ]
    tasks = [
        {"title": "Review audience list and exclusions", "requires_confirmation": True},
        {"title": "Generate demo PDF and screenshots", "requires_confirmation": False},
        {"title": "Approve email/WhatsApp copy before sending", "requires_confirmation": True},
        {"title": "Track replies and schedule demos", "requires_confirmation": False},
    ]
    return {
        "ok": True,
        "version": "campaign-planner-v16",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "draft_only_external_actions_require_approval",
        "brief": brief,
        "channels": channels,
        "drafts": drafts,
        "asset_checklist": asset_checklist,
        "tasks": tasks,
        "policy": "Campaign content is drafted locally. External send/post/publish actions require explicit user confirmation.",
    }
