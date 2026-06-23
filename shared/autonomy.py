from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

AUTONOMY_LEVELS = {
    "L0": "Shadow only: AI observes and logs; no user-visible action.",
    "L1": "Suggest: AI recommends; human decides.",
    "L2": "Pre-fill + confirm: AI prepares action; human confirms commit.",
    "L3": "Auto-execute with notification: allowed only for low-risk reversible workflows.",
    "L4": "Lights-out: allowed only for validated, low-risk, reversible non-GxP workflows.",
}

NEVER_AUTONOMOUS_ACTIONS = {
    "batch_release",
    "batch_certification",
    "oos_final_disposition",
    "deviation_closure",
    "capa_closure",
    "change_control_approval",
    "supplier_qualification_approval",
    "material_release_from_quarantine",
    "regulatory_submission_signoff",
    "deficiency_response_signoff",
    "electronic_signature_approval",
    "critical_process_parameter_change",
    "master_batch_record_approval",
    "customer_kyc_approval",
    "payment_release_high_value",
}

LOW_RISK_L3_ACTIONS = {
    "dashboard_refresh",
    "document_review_reminder",
    "low_stock_notification",
    "cycle_count_schedule",
    "gst_draft_prepare",
    "coa_draft_prepare",
    "sop_draft_prepare",
    "daily_lessons_build",
    "action_ledger_record",
    "artifact_generate",
    "calendar_ics_create",
    "campaign_draft",
    "capture_save",
    "mailbox_import",
    "memory_ingest",
    "operator_digest",
    "web_search",
}

@dataclass(frozen=True)
class AutonomyDecision:
    allowed: bool
    requested_level: str
    effective_level: str
    action: str
    reason: str
    requires_human_approval: bool


def normalize_level(level: str | None) -> str:
    level = (level or "L1").strip().upper()
    return level if level in AUTONOMY_LEVELS else "L1"


def check_autonomy(action: str, requested_level: str | None = "L1") -> dict[str, Any]:
    action = (action or "unknown").strip().lower().replace(" ", "_")
    level = normalize_level(requested_level)
    if action in NEVER_AUTONOMOUS_ACTIONS:
        return asdict(AutonomyDecision(False, level, "L1", action, "Hard GxP gate: AI may draft/recommend only; named human approval is required.", True))
    if level in {"L3", "L4"} and action not in LOW_RISK_L3_ACTIONS:
        return asdict(AutonomyDecision(False, level, "L2", action, "Requested autonomy is above the validated risk tier for this workflow.", True))
    return asdict(AutonomyDecision(True, level, level, action, "Allowed by v13 autonomy policy.", level in {"L1", "L2"}))


def policy() -> dict[str, Any]:
    return {
        "ok": True,
        "levels": AUTONOMY_LEVELS,
        "never_autonomous_actions": sorted(NEVER_AUTONOMOUS_ACTIONS),
        "low_risk_l3_actions": sorted(LOW_RISK_L3_ACTIONS),
        "default_enterprise_level": "L1",
        "default_omni_level": "L2",
        "principle": "AI drafts, checks and recommends. Humans approve regulated GxP decisions.",
    }
