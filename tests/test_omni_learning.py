"""P7 tests: Omni's resilient AI surface + self-learning loop.

Covers the gateway health endpoint, the feedback→memory loop, the
feedback→skill distillation, and the chat-visible learning status — the
pieces that make Omni reliably self-improving.
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

# Whisper native init can hard-crash on Windows — never load it under tests.
os.environ.setdefault("SHIMS_DISABLE_WHISPER", "1")

from backend.app.main import app, _distill_feedback_into_skills  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)


def test_ai_health_endpoint():
    r = client.get("/api/ai/health")
    assert r.status_code == 200
    data = r.json()
    assert "providers" in data and "ollama" in data["providers"]
    assert isinstance(data.get("ok"), bool)


def test_feedback_requires_fields():
    assert client.post("/api/feedback", json={}).json()["ok"] is False
    assert client.post("/api/feedback", json={"rating": 1}).json()["ok"] is False


def test_positive_feedback_then_distills_to_skill():
    msg = "always show me tabular material balances"
    r = client.post("/api/feedback", json={"rating": 1, "message": msg})
    assert r.status_code == 200 and r.json()["ok"] is True

    # The preference memory should now be distillable into a skill.
    made = _distill_feedback_into_skills()
    assert made >= 0  # idempotent; >0 on first run for this preference

    from shared.skills import list_skills
    names = " ".join(s.get("name", "") for s in list_skills(limit=500))
    assert "Preference:" in names


def test_negative_feedback_stored_as_anti_pattern():
    r = client.post("/api/feedback", json={"rating": -1, "message": "summarize the news",
                                           "comment": "too long", "answer": "Here is a very long..."})
    assert r.json()["ok"] is True
    from shared.omni_brain import list_memories
    fb = list_memories(namespace="omni_feedback", limit=50)
    assert any("anti_pattern" in (m.get("tags") or []) for m in fb)


def test_learning_recent_endpoint():
    # Seed one of each so the payload is populated.
    client.post("/api/feedback", json={"rating": 1, "message": "learning recent seed positive"})
    client.post("/api/feedback", json={"rating": -1, "message": "learning recent seed negative", "comment": "x"})
    r = client.get("/api/learning/recent")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "skills" in data and "feedback" in data and "feedback_counts" in data
    assert data["feedback_counts"]["preferences"] >= 1
    assert data["feedback_counts"]["anti_patterns"] >= 1
    assert "autonomous_improvement_enabled" in data
