from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .action_ledger import record_action, verify_action
from .calendar_planner import build_ics
from .campaign_planner import plan_campaign
from .search_query_planner import plan_search_query
from .trust_contract import build_trust, evidence_from_search


@dataclass(frozen=True)
class EvalCase:
    name: str
    check: Callable[[], tuple[bool, str, dict[str, Any]]]


def _case_search_query_rewrite() -> tuple[bool, str, dict[str, Any]]:
    raw = "hey shims please search the internet for what is the latest price of fluconazole API in India today"
    plan = plan_search_query(raw, web_mode=True)
    ok = plan.should_search and "hey" not in plan.primary_query.lower() and "fluconazole" in plan.primary_query.lower()
    return ok, "search planner rewrites chatty prompt into compact query", plan.to_dict()


def _case_trust_sources() -> tuple[bool, str, dict[str, Any]]:
    result = {
        "ok": True,
        "query": "fluconazole API India price",
        "provider": "fixture",
        "results": [{"title": "Market note", "url": "https://example.test/price", "snippet": "Fixture source"}],
    }
    evidence = evidence_from_search(result)
    trust = build_trust(route="tool:web_search", evidence=evidence, query_plan={"primary_query": result["query"]})
    ok = trust["trust_level"] == "sourced" and trust["evidence_count"] == 1 and trust["confidence"]["score"] >= 0.7
    return ok, "web answers get sourced trust envelopes", trust


def _case_action_ledger() -> tuple[bool, str, dict[str, Any]]:
    action = record_action(
        "operator_digest",
        "Reliability eval digest",
        payload={"eval": True},
        result={"ok": True},
        requested_level="L3",
        summary="Eval wrote a low-risk local action record.",
    )
    verified = verify_action(action["action_id"])
    return bool(verified.get("ok")), "action ledger records and verifies local actions", verified


def _case_external_requires_confirmation() -> tuple[bool, str, dict[str, Any]]:
    action = record_action("gmail_send", "Send external email", payload={"to": "user@example.com"}, requested_level="L3")
    ok = action["action"]["status"] == "requires_confirmation" and action["action"]["requires_confirmation"] is True
    return ok, "external actions require explicit confirmation", action["action"]


def _case_calendar_ics() -> tuple[bool, str, dict[str, Any]]:
    event = build_ics(title="SHIMS eval meeting", start="2026-06-01T10:00:00+00:00", duration_minutes=30)
    ok = "BEGIN:VCALENDAR" in event["ics"] and "BEGIN:VEVENT" in event["ics"] and event["sync"] == "none"
    return ok, "calendar creates local ICS without Google sync", {"uid": event["uid"], "sync": event["sync"], "sample": event["ics"][:120]}


def _case_campaign_draft_only() -> tuple[bool, str, dict[str, Any]]:
    plan = plan_campaign(objective="Sell SHIMS", audience="SMB founders", offer="AI operator demo")
    ok = plan["mode"] == "draft_only_external_actions_require_approval" and any(t["requires_confirmation"] for t in plan["tasks"])
    return ok, "campaign planner drafts content and gates external actions", {"mode": plan["mode"], "tasks": plan["tasks"]}


def run_reliability_evals() -> dict[str, Any]:
    cases = [
        EvalCase("search_query_rewrite", _case_search_query_rewrite),
        EvalCase("trust_sources", _case_trust_sources),
        EvalCase("action_ledger", _case_action_ledger),
        EvalCase("external_requires_confirmation", _case_external_requires_confirmation),
        EvalCase("calendar_ics", _case_calendar_ics),
        EvalCase("campaign_draft_only", _case_campaign_draft_only),
    ]
    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            ok, message, details = case.check()
        except Exception as exc:
            ok, message, details = False, f"{case.name} raised {exc.__class__.__name__}", {"error": str(exc)[:400]}
        results.append({"name": case.name, "ok": bool(ok), "message": message, "details": details})
    passed = sum(1 for r in results if r["ok"])
    return {"ok": passed == len(results), "version": "reliability-evals-v16", "passed": passed, "total": len(results), "results": results}
