"""Regression coverage for the Phase-1 desktop-cowork core:
sandbox slimming, skills, background task worker, FileOps, OCR, Coder.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


# ── 1A: sandbox stays slim and excludes android_app/llama.cpp ───────────────
def test_sandbox_excludes_heavy_trees(tmp_path):
    from shared import self_evolver as se
    p = se.propose_patch("docs/_cowork_test.md", "# t\nhi\n", reason="size test")
    r = se.validate_proposal(p["proposal_id"])
    sb = Path(r.details["sandbox_path"])
    try:
        assert r.status == "validated"
        assert not (sb / "android_app").exists()
        total = sum(f.stat().st_size for f in sb.rglob("*") if f.is_file())
        assert total < 30 * 1024 * 1024  # well under 30 MB (was ~260 MB)
    finally:
        import shutil
        shutil.rmtree(sb, ignore_errors=True)


# ── 1B: skills save / retrieve / forget ─────────────────────────────────────
def test_skills_roundtrip():
    from shared import skills as sk
    s = sk.save_skill("Test Skill", "Always do X for Y.", tags=["test"], pinned=True)
    assert s["id"]
    assert any(x["id"] == s["id"] for x in sk.list_skills())
    assert sk.relevant_skills("do x for y")  # pinned/relevant
    assert sk.forget_skill(s["id"]) is True


def test_skills_endpoint_shapes():
    d = client.get("/skills").json()
    assert d["ok"] and "builtin" in d and "learned" in d
    # legacy shape preserved for the existing pane
    assert isinstance(d["skills"], list) and d["skills"]


# ── 1C: background task worker drains real handlers ─────────────────────────
def test_task_enqueue_and_drain():
    enq = client.post("/brain/tasks", json={"task_type": "memory_consolidation", "title": "t"}).json()
    assert enq["ok"]
    rep = client.post("/brain/tasks/run", params={"max_tasks": 5}).json()
    assert rep["ok"] and (rep["done"] + rep["skipped"] + rep["failed"]) >= 1


# ── 1D: FileOps confined, dry-run + undo ────────────────────────────────────
def test_fileops_confined_and_reversible():
    ws = Path(tempfile.mkdtemp(prefix="shims_test_ws_"))
    (ws / "a.txt").write_text("hello")
    (ws / "pic.png").write_bytes(b"x")
    try:
        assert client.post("/files/workspace", json={"path": str(ws)}).json()["ok"]
        # escape attempt is blocked
        assert client.get("/files/read", params={"relpath": "../../secret"}).status_code == 400
        plan = client.post("/files/organize/plan").json()
        assert plan["count"] >= 2
        ap = client.post("/files/organize/apply", json={"moves": plan["moves"]}).json()
        assert ap["applied"] >= 2 and ap["undo_id"]
        un = client.post("/files/organize/undo", json={"undo_id": ap["undo_id"]}).json()
        assert un["restored"] >= 2
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)


# ── 1E: OCR endpoint (skips cleanly if engine absent) ───────────────────────
def test_ocr_health_and_extract():
    h = client.get("/ocr/health").json()
    assert "available" in h
    if not h["available"]:
        return  # engine not installed in this env — endpoint still safe
    from PIL import Image, ImageDraw
    import io
    img = Image.new("RGB", (260, 70), "white")
    ImageDraw.Draw(img).text((10, 22), "SHIMS 42", fill="black")
    buf = io.BytesIO(); img.save(buf, "PNG")
    r = client.post("/ocr", files={"file": ("t.png", buf.getvalue(), "image/png")})
    assert r.status_code == 200 and r.json()["ok"]


# ── 1F: Coder workspace create + write + run (no LLM needed) ─────────────────
def test_coder_create_write_run():
    pid = client.post("/coder/project", json={"name": "t", "goal": "g"}).json()["project"]["id"]
    client.post("/coder/write", json={"project_id": pid, "path": "main.py", "content": "print(6*7)"})
    run = client.post("/coder/run", json={"project_id": pid}).json()
    assert run["ok"] and run["stdout"].strip() == "42"
    # escape blocked
    assert client.post("/coder/write", json={"project_id": pid, "path": "../evil.py", "content": "x"}).status_code == 400
