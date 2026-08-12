"""Agent-domain registry: matching, scoped tool subsets, and the
background-specialist assign path."""
from __future__ import annotations

from shared.agent_domains import (
    DOMAINS, domain_tool_names, match_domain, scoped_tools,
)


def _spec(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


FULL = [_spec(n) for n in (
    "web.search", "web.fetch", "media.create", "agent.spawn", "agent.assign",
    "desktop.bridge", "mail.read", "mail.draft", "mail.attachment",
    "channels.recent", "comms.digest", "skill.list")]


def _names(tools):
    return {t["function"]["name"] for t in tools}


def test_gmail_message_scopes_to_mail_tools():
    assert match_domain("Check my Gmail inbox for unread messages") == "mail"
    out = scoped_tools("Check my Gmail inbox for unread messages", FULL)
    assert _names(out) == set(domain_tool_names("mail"))


def test_whatsapp_message_scopes_to_comms():
    assert match_domain("Show me my recent WhatsApp messages") == "comms"
    out = scoped_tools("Show me my recent WhatsApp messages", FULL)
    assert "channels.recent" in _names(out)


def test_pdf_message_scopes_to_media():
    assert match_domain("Create a one-page PDF about tool calling") == "media"
    out = scoped_tools("Create a one-page PDF about tool calling", FULL)
    assert _names(out) == {"media.create"}


def test_ambiguous_message_keeps_full_set():
    assert match_domain("what do you think about local AI?") is None
    out = scoped_tools("what do you think about local AI?", FULL)
    assert _names(out) == _names(FULL)


def test_multi_domain_message_keeps_full_set():
    # "email me a pdf" needs both mail and media — no subset is safe.
    assert match_domain("email me a pdf of the report") is None
    out = scoped_tools("email me a pdf of the report", FULL)
    assert _names(out) == _names(FULL)


def test_mail_plus_whatsapp_is_comms():
    assert match_domain("check my whatsapp and gmail") == "comms"


def test_scoping_kill_switch(monkeypatch):
    monkeypatch.setenv("SHIMS_TOOL_SCOPING", "off")
    out = scoped_tools("Check my Gmail inbox", FULL)
    assert _names(out) == _names(FULL)


def test_all_domain_tools_exist_in_registry():
    for name, spec in DOMAINS.items():
        assert spec["tools"], name
        assert spec["persona"], name
        assert spec["keywords"], name


def test_assign_endpoint_validates_domain():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    client = TestClient(app)
    bad = client.post("/api/agents/assign", json={"domain": "nope", "goal": "x"})
    assert bad.json()["ok"] is False
    assert "known_domains" in bad.json()
    missing = client.post("/api/agents/assign", json={"domain": "mail"})
    assert missing.json()["ok"] is False


def test_assign_endpoint_queues_specialist_job():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from shared import omni_brain
    client = TestClient(app)
    res = client.post("/api/agents/assign",
                      json={"domain": "mail", "goal": "Triage my inbox", "name": "test triage"})
    body = res.json()
    assert body["ok"] is True
    assert body["domain"] == "mail"
    assert body["job_id"]
    task = omni_brain.get_task(body["job_id"])
    assert task is not None
    assert task["task_type"] == "specialist_agent"
    assert "Gmail agent" in task["title"]
    # Cleanup: cancel so the drain never runs a real LLM turn for this test.
    omni_brain.cancel_task(body["job_id"])
