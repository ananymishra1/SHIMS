import json

from fastapi.testclient import TestClient

import backend.app.main as omni
from backend.app.main import app as omni_app
from shims_enterprise.app import app as enterprise_app
from shared.database import db


def _stream_chunks(client: TestClient, payload: dict) -> list[dict]:
    chunks: list[dict] = []
    with client.stream("POST", "/brain/turn", json=payload) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line:
                chunks.append(json.loads(line))
    return chunks


def _enterprise_login(client: TestClient) -> None:
    resp = client.post("/login", data={"username": "admin", "password": "SHIMS2025!"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_omni_conversation_mode_persists_history_and_stateless_mode_does_not():
    omni._sessions.clear()
    with TestClient(omni_app) as client:
        chunks = _stream_chunks(client, {"message": "hi", "conversation_mode": True})
        done = [c for c in chunks if c.get("type") == "done"][-1]
        session_id = done["session_id"]

        detail = client.get(f"/sessions/{session_id}").json()
        assert detail["ok"] is True
        assert detail["message_count"] == 2
        assert detail["messages"][0]["role"] == "user"
        assert detail["messages"][1]["role"] == "assistant"

        stateless_chunks = _stream_chunks(client, {"message": "hello there", "conversation_mode": False})
        stateless_done = [c for c in stateless_chunks if c.get("type") == "done"][-1]
        assert stateless_done["session_id"] not in {s["id"] for s in client.get("/sessions").json()}


def test_omni_session_create_detail_and_agent_roster():
    with TestClient(omni_app) as client:
        created = client.post("/sessions/new").json()
        assert created["ok"] is True
        detail = client.get(f"/sessions/{created['session_id']}").json()
        assert detail["messages"] == []
        assert detail["title"] == "New chat"

        agents = client.get("/agents/list").json()["agents"]
        ids = {a["id"] for a in agents}
        assert {"voice", "search", "rd", "enterprise_bridge"}.issubset(ids)
        assert len(agents) >= 10


def test_omni_static_ui_loads_sessions_and_agents():
    js = (omni.ROOT / "frontend" / "js" / "shims_omni.js").read_text(encoding="utf-8")
    assert "function loadAgents" in js
    assert "function loadAgentsPane" in js
    assert "/agents/list" in js
    assert "function loadSession" in js
    assert "/sessions/new" in js
    assert "conversation_mode:state.converseMode" in js
    html = (omni.ROOT / "frontend" / "shims_omni.html").read_text(encoding="utf-8")
    assert 'data-view="agents"' in html
    assert 'id="agents-body"' in html


def test_enterprise_warehouse_task_screen_and_summary_api():
    with TestClient(enterprise_app) as client:
        _enterprise_login(client)
        page = client.get("/warehouse/stock")
        assert page.status_code == 200
        assert "Receive / Issue" in page.text
        assert "Release Queue" in page.text
        assert "Raise Request" in page.text
        assert "Warehouse Assistant" in page.text

        summary = client.get("/api/warehouse/summary").json()
        assert summary["ok"] is True
        assert "total_materials" in summary["counts"]
        assert summary["recommendations"]


def test_enterprise_warehouse_can_raise_procurement_request():
    with TestClient(enterprise_app) as client:
        _enterprise_login(client)
        item = db.one("SELECT * FROM inventory_items ORDER BY id LIMIT 1")
        assert item
        resp = client.post(
            "/api/warehouse/request",
            data={"item_id": item["id"], "quantity": 1, "required_by": "2026-06-10", "notes": "test request"},
            follow_redirects=False,
        )
        assert resp.status_code in (200, 302, 303)
        req = db.one("SELECT * FROM procurement_requests WHERE linked_item_id=? ORDER BY id DESC LIMIT 1", (item["id"],))
        assert req
        assert req["material_name"] == item["material_name"]


def test_enterprise_warehouse_static_half_screen_markers():
    template = (omni.ROOT / "shims_enterprise" / "templates" / "warehouse_stock.html").read_text(encoding="utf-8")
    css = (omni.ROOT / "shims_enterprise" / "static" / "style.css").read_text(encoding="utf-8")
    assert "warehouse-grid" in template
    assert "wh-tabs" in template
    assert "@media (max-width: 1024px)" in css
    assert ".warehouse-grid { grid-template-columns: 1fr; }" in css
