"""Phantom-approval guard tests.

A bare "yes" in chat must never execute a pending action that was not
actually surfaced to the user in the SAME session within the approval TTL.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import backend.app.main as main


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _pending_dir(tmp_path, monkeypatch):
    """Point the pending-actions store at a tmp dir and stub the action ledger."""
    pending_dir = tmp_path / "pending_actions"
    pending_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "PENDING_ACTION_DIR", pending_dir)
    monkeypatch.setattr(
        main,
        "record_action",
        lambda *a, **k: {"action_id": "ledger-test", "ledger_hash": "hash-test", "action": {}},
    )
    return pending_dir


def _make_pending(session_id: str, *, surfaced: bool = False, age_s: float = 0.0) -> dict:
    action = main._create_pending_action(
        action_type="agent_tool",
        title="Run desktop.bridge ping",
        summary="Run desktop.bridge ping on the paired desktop",
        payload={"tool": "desktop.bridge", "args": {"action": "ping"}},
        session_id=session_id,
        risk="desktop_access",
    )
    if surfaced:
        action = main._surface_pending_action(action, session_id)
    if age_s:
        action["created_at"] = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
        if surfaced:
            action["surfaced_at"] = action["created_at"]
        action = main._save_pending_action(action)
    return action


def _collect(stream) -> list[dict]:
    async def _gather() -> list[dict]:
        return [json.loads(chunk) async for chunk in stream]

    return _run(_gather())


def test_stale_cross_session_pending_not_executed(monkeypatch):
    """An 11-day-old pending action surfaced in ANOTHER session must not run
    when this session says "yes" — the reply falls through to the model."""
    _make_pending("other-session", surfaced=True, age_s=11 * 86400)

    # No global fallback: this session sees nothing.
    assert main._latest_pending_action("this-session") is None
    assert main._executable_pending_action("this-session") is None

    # The yes/no text still parses — but the router gate rejects routing, so
    # _approval_decision_stream is never entered from the chat lanes. Even if
    # it were, it must refuse to execute.
    assert main._approval_decision_from_text("yes") is True
    executed: list[dict] = []

    async def _fake_execute(action, approved_by="human-operator"):
        executed.append(action)
        return {"ok": True, "status": "completed", "message": "ran"}

    monkeypatch.setattr(main, "_execute_pending_action", _fake_execute)
    req = main.ChatRequest(message="yes", session_id="this-session")
    events = _collect(main._approval_decision_stream(req, "this-session", True))
    assert executed == []
    routes = {e.get("route") for e in events if e.get("type") == "done"}
    assert "approval:no-pending" in routes
    assert "approval:executed" not in routes


def test_unsurfaced_same_session_pending_not_executed():
    """A fresh pending action in this session that was never shown to the user
    (no surfaced_at stamp) is not executable by a bare yes."""
    _make_pending("this-session", surfaced=False)
    assert main._latest_pending_action("this-session") is not None
    assert main._executable_pending_action("this-session") is None


def test_surfaced_in_session_approval_executes(monkeypatch):
    """A pending action surfaced in this session within the TTL runs on "yes"."""
    action = _make_pending("this-session", surfaced=True)
    assert main._executable_pending_action("this-session") is not None

    executed: list[dict] = []

    async def _fake_execute(act, approved_by="human-operator"):
        executed.append(act)
        return {"ok": True, "status": "completed", "message": "Ping ok."}

    monkeypatch.setattr(main, "_execute_pending_action", _fake_execute)
    req = main.ChatRequest(message="yes", session_id="this-session")
    events = _collect(main._approval_decision_stream(req, "this-session", True))
    assert len(executed) == 1
    assert executed[0]["approval_id"] == action["approval_id"]
    routes = {e.get("route") for e in events if e.get("type") == "done"}
    assert "approval:executed" in routes


def test_expired_approval_not_executed():
    """A surfaced in-session approval older than SHIMS_APPROVAL_TTL_S is dead."""
    _make_pending("this-session", surfaced=True, age_s=main._approval_ttl_s() + 60)
    assert main._latest_pending_action("this-session") is None
    assert main._executable_pending_action("this-session") is None


def test_startup_sweep_marks_stale_pending_expired():
    """The lifespan hygiene sweep expires old files without deleting them."""
    action = _make_pending("this-session", surfaced=True, age_s=main._approval_ttl_s() + 60)
    fresh = _make_pending("this-session", surfaced=True)

    expired = main._expire_stale_pending_actions()
    assert expired == 1

    reloaded = main._load_pending_action(action["approval_id"])
    assert reloaded["status"] == "expired"
    assert main._load_pending_action(fresh["approval_id"])["status"] == "pending"


def test_pending_action_record_authorizes_coder_app(monkeypatch, tmp_path):
    """3e: the recorded pending action (not a hardcoded phrase) authorizes the
    scaffold apply path; unrelated callers still need the phrase."""
    action = _make_pending("this-session", surfaced=True)
    assert main._pending_action_authorizes(action) is True
    assert main._pending_action_authorizes({"approval_id": "appr_nonexistent"}) is False
    assert main._pending_action_authorizes(None) is False
