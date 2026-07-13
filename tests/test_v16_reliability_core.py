import json

from fastapi.testclient import TestClient

import backend.app.main as main
import shared.action_ledger as al
import shared.calendar_planner as cp
import shared.mailbox as mb
import shared.omni_brain as ob
from backend.app.main import app
from shared.campaign_planner import plan_campaign
from shared.eval_harness import run_reliability_evals
from shared.search_query_planner import plan_search_query
from shared.trust_contract import build_trust, evidence_from_search


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "ACTION_DB", tmp_path / "actions.sqlite3")
    monkeypatch.setattr(mb, "MAILBOX_DB", tmp_path / "mailbox.sqlite3")
    monkeypatch.setattr(ob, "BRAIN_DB", tmp_path / "brain.sqlite3")
    monkeypatch.setattr(cp, "CALENDAR_DIR", tmp_path)
    return TestClient(app)


def test_trust_contract_classifies_sourced_answers():
    result = {
        "ok": True,
        "query": "fluconazole API India price",
        "provider": "fixture",
        "results": [{"title": "Price source", "url": "https://example.test/price", "snippet": "Fixture source"}],
    }

    trust = build_trust(route="tool:web_search", evidence=evidence_from_search(result), query_plan={"primary_query": result["query"]})

    assert trust["trust_level"] == "sourced"
    assert trust["evidence_count"] == 1
    assert trust["confidence"]["score"] >= 0.7


def test_action_ledger_records_verifies_and_gates_external_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "ACTION_DB", tmp_path / "actions.sqlite3")

    local = al.record_action("operator_digest", "Build digest", payload={"x": 1}, requested_level="L3")
    assert local["action"]["status"] == "completed"
    assert al.verify_action(local["action_id"])["ok"] is True

    external = al.record_action("gmail_send", "Send email", payload={"to": "buyer@example.com"}, requested_level="L3")
    assert external["action"]["status"] == "requires_confirmation"
    assert external["action"]["requires_confirmation"] is True
    assert external["action"]["autonomy"]["allowed"] is False


def test_search_query_planner_avoids_raw_chat_prompt():
    raw = "hey shims can you please search the internet for what is the latest GST e invoice rule in India today"
    plan = plan_search_query(raw, web_mode=True)

    assert plan.should_search is True
    assert "hey" not in plan.primary_query.lower()
    assert "please" not in plan.primary_query.lower()
    assert "GST" in plan.primary_query or "gst" in plan.primary_query.lower()


def test_brain_stream_search_done_contains_trust_and_action(monkeypatch, tmp_path):
    c = _client(tmp_path, monkeypatch)

    async def fake_search(query: str, max_results: int = 6, provider: str | None = None):
        return {
            "ok": True,
            "query": "fluconazole API India price",
            "original_query": query,
            "provider": "fixture",
            "results": [{"title": "Source", "url": "https://example.test/source", "snippet": "Fixture"}],
            "query_plan": {"primary_query": "fluconazole API India price", "variants": ["fluconazole API India price"]},
        }

    monkeypatch.setattr(main, "_web_search", fake_search)
    chunks = []
    with c.stream("POST", "/brain/turn", json={"message": "search web for latest fluconazole API price India", "web_mode": True}) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line:
                chunks.append(json.loads(line))

    done = [x for x in chunks if x.get("type") == "done"][-1]
    assert done["trust"]["trust_level"] in {"sourced", "verified"}
    assert done["evidence"]
    assert done["action_id"].startswith("act_")
    assert done["ledger_hash"]
    assert done["query_plan"]["primary_query"] == "fluconazole API India price"


def test_operator_campaign_calendar_and_eval_endpoints(monkeypatch, tmp_path):
    c = _client(tmp_path, monkeypatch)

    cap = c.post("/capture/share", json={"title": "RFQ capture", "text": "Urgent quote needed", "source": "test"}).json()
    assert cap["ok"] is True
    assert cap["trust"]["trust_level"] in {"memory-backed", "verified"}

    digest = c.get("/operator/digest").json()
    assert digest["ok"] is True
    assert digest["trust"]["trust_level"] in {"memory-backed", "sourced", "draft", "verified"}
    assert digest["recommendations"]

    campaign = c.post("/campaigns/plan", json={"objective": "Sell SHIMS", "audience": "factory owners", "offer": "AI operator demo"}).json()
    assert campaign["ok"] is True
    assert campaign["mode"] == "draft_only_external_actions_require_approval"
    assert campaign["action_id"].startswith("act_")
    assert any(task["requires_confirmation"] for task in campaign["tasks"])

    event = c.post("/calendar/ics", json={"title": "SHIMS demo", "start": "2026-06-01T10:00:00+00:00", "duration_minutes": 30}).json()
    assert event["ok"] is True
    assert "BEGIN:VCALENDAR" in event["ics"]
    assert event["sync"] == "none"
    assert event["action_id"].startswith("act_")

    evals = c.post("/evals/run").json()
    assert evals["ok"] is True
    assert evals["passed"] == evals["total"]


def test_campaign_planner_is_draft_only():
    plan = plan_campaign(objective="Run launch", audience="SMB teams", offer="workflow demo")
    assert plan["mode"] == "draft_only_external_actions_require_approval"
    assert "email_body" in plan["drafts"]
    assert any(task["requires_confirmation"] for task in plan["tasks"])


def test_eval_harness_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "ACTION_DB", tmp_path / "eval_actions.sqlite3")
    result = run_reliability_evals()
    assert result["ok"] is True
    assert result["total"] >= 6
