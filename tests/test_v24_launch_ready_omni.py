from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def _stream_events(payload: dict) -> list[dict]:
    events: list[dict] = []
    with client.stream("POST", "/brain/turn", json=payload) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if not line:
                continue
            events.append(json.loads(line))
    return events


def test_chat_action_request_asks_yes_no_and_can_cancel():
    # Risky actions (desktop bridge access) ask for approval before running.
    events = _stream_events({"message": "take a screenshot of my desktop", "provider": "ollama"})
    approval_events = [e for e in events if e.get("type") == "approval_request"]
    assert approval_events
    approval = approval_events[0]["approval"]
    assert approval["status"] == "pending"
    assert approval["action_type"] == "agent_tool"

    # meta (with session_id) now arrives after the leading "thinking" thought events
    session_id = next(e["session_id"] for e in events if e.get("session_id"))
    cancel_events = _stream_events({"message": "no", "session_id": session_id, "provider": "ollama"})
    assert any(e.get("type") == "approval" and e["approval"]["status"] == "cancelled" for e in cancel_events)


def test_chat_scaffold_action_auto_executes_without_approval():
    # Safe read-only/scaffold actions skip the approval round-trip and run
    # immediately, so SHIMS does the work instead of asking and doing nothing.
    events = _stream_events({"message": "create app called pytest launch approval board", "provider": "ollama"})
    done = [e for e in events if e.get("type") == "done"][-1]
    assert done.get("route") == "auto:coder_app_scaffold"
    answer = "".join(e.get("content", "") for e in events if e.get("type") == "token")
    assert "Created and applied" in answer


def test_media_request_not_hijacked_by_scaffold_router():
    # "Create a PDF about tool calling" contains create+tool — the scaffold
    # detector must not swallow it; the model's media.create tool owns it.
    from backend.app.main import _detect_chat_action_request
    assert _detect_chat_action_request(
        "Create a one-page PDF titled 'Status' summarizing that the engine supports tool calling.", "t"
    ) is None
    assert _detect_chat_action_request("make an image of a sunset over code", "t") is None
    # Real app requests still route to the scaffolder.
    assert _detect_chat_action_request("create app called launch board", "t") is not None
    assert _detect_chat_action_request("build me a dashboard for sales", "t") is not None


def test_coder_status_and_launch_readiness_surfaces():
    coder = client.get("/coder/playground/status").json()
    assert coder["ok"] is True
    assert "frontend" in coder["roots"]
    readiness = client.get("/launch/readiness").json()
    assert readiness["status"] in {"ready", "needs_attention"}
    assert any(c["id"] == "approvals" and c["ok"] for c in readiness["checks"])
    assert any(c["id"] == "coder_playground" and c["ok"] for c in readiness["checks"])


def test_smarter_image_generation_still_returns_real_file():
    response = client.post(
        "/media/generate",
        json={"kind": "image", "prompt": "clean product render of SHIMS Omni launch dashboard", "provider": "local", "quality": "high"},
    )
    data = response.json()
    assert data["ok"] is True
    assert data["provider"] == "local-fallback"
    assert data["file_url"].endswith(".png")
    assert data.get("enhanced_prompt")
