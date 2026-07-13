from __future__ import annotations

from typing import Any

from .action_ledger import list_actions, record_action
from .mailbox import mailbox_digest
from .omni_brain import list_tasks
from .telemetry import build_daily_lessons, recent_events
from .trust_contract import build_trust, evidence_from_action, evidence_from_mailbox_digest, merge_evidence


def _clean(value: Any, limit: int = 600) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def build_operator_digest(*, limit: int = 20, record: bool = False) -> dict[str, Any]:
    mailbox = mailbox_digest(limit=limit)
    tasks = list_tasks(limit=limit)
    actions = list_actions(limit=limit)
    failed_actions = [a for a in actions if a.get("status") in {"failed", "requires_confirmation"}]
    events = recent_events(limit)
    errors = [e for e in events if not bool(e.get("ok", True))]
    lessons = build_daily_lessons(limit=250)

    recommendations: list[dict[str, Any]] = []
    for item in (mailbox.get("action_candidates") or [])[:6]:
        recommendations.append(
            {
                "type": "mailbox_follow_up",
                "title": _clean(item.get("title") or "Review mailbox item", 180),
                "reason": "Mailbox/capture item looks actionable.",
                "source_id": item.get("id"),
                "requires_confirmation": False,
            }
        )
    for task in tasks[:6]:
        recommendations.append(
            {
                "type": task.get("task_type") or "task",
                "title": _clean(task.get("title") or "Review task", 180),
                "reason": f"Queued brain task priority {task.get('priority')}.",
                "source_id": task.get("id"),
                "requires_confirmation": False,
            }
        )
    for action in failed_actions[:4]:
        recommendations.append(
            {
                "type": "action_review",
                "title": _clean(action.get("title") or action.get("action_type") or "Review action", 180),
                "reason": action.get("status") or "Action needs review.",
                "source_id": action.get("id"),
                "requires_confirmation": bool(action.get("requires_confirmation")),
            }
        )
    if errors:
        recommendations.append(
            {
                "type": "reliability_review",
                "title": "Review recent reliability errors",
                "reason": f"{len(errors)} telemetry errors found in recent events.",
                "source_id": "",
                "requires_confirmation": False,
            }
        )

    blockers = [
        {
            "type": a.get("action_type"),
            "title": a.get("title"),
            "status": a.get("status"),
            "action_id": a.get("id"),
            "reason": (a.get("autonomy") or {}).get("reason") or a.get("summary"),
        }
        for a in failed_actions[:8]
    ]

    evidence = merge_evidence(
        evidence_from_mailbox_digest(mailbox, limit=8),
        *[evidence_from_action(a) for a in actions[:4]],
        limit=12,
    )
    trust = build_trust(
        route="operator:digest",
        evidence=evidence,
        missing_evidence=[] if mailbox.get("counts", {}).get("messages") or mailbox.get("counts", {}).get("captures") else ["No mailbox or capture items are available yet."],
        requested_level="draft",
    )

    digest = {
        "ok": True,
        "version": "operator-digest-v16",
        "mailbox": mailbox,
        "tasks": tasks[:limit],
        "actions": actions[:limit],
        "blockers": blockers,
        "recommendations": recommendations[:12],
        "telemetry": {
            "error_count": len(errors),
            "recent_errors": errors[:8],
            "lessons": lessons,
        },
        "trust": trust,
        "evidence": trust["evidence"],
        "confidence": trust["confidence"],
    }

    if record:
        action = record_action(
            "operator_digest",
            "Build operator digest",
            payload={"limit": limit},
            result={"recommendations": len(recommendations), "blockers": len(blockers)},
            evidence=evidence,
            requested_level="L3",
            summary="Generated local operator digest from mailbox, tasks, action ledger, and telemetry.",
        )
        digest["action"] = action["action"]
        digest["action_id"] = action["action_id"]
        digest["ledger_hash"] = action["ledger_hash"]
        digest["trust"] = build_trust(
            route="operator:digest",
            evidence=merge_evidence(evidence, evidence_from_action(action["action"])),
            action_id=action["action_id"],
            ledger_hash=action["ledger_hash"],
            requested_level="draft",
        )
        digest["evidence"] = digest["trust"]["evidence"]
        digest["confidence"] = digest["trust"]["confidence"]

    return digest
