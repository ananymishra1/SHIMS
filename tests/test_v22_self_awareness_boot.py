from fastapi.testclient import TestClient

import shared.self_awareness as sa
from backend.app.main import app


def _redirect_self_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(sa, "SELF_DIR", tmp_path)
    monkeypatch.setattr(sa, "LATEST_JSON", tmp_path / "latest.json")
    monkeypatch.setattr(sa, "LATEST_MD", tmp_path / "latest.md")


def test_boot_self_audit_writes_safe_operational_self_model(monkeypatch, tmp_path):
    _redirect_self_paths(monkeypatch, tmp_path)

    result = sa.run_boot_self_audit(app_name="SHIMS Test", app_version="v-test", max_files=160, persist_brain=False)

    assert result["ok"] is True
    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "latest.md").exists()
    model = result["model"]
    assert model["identity"]["claim"] == "SHIMS has an operational self-model, not human consciousness."
    assert model["workspace"]["scanned_files"] > 0
    assert model["routes"]["backend_count"] > 0
    assert model["tests"]["count"] > 0
    manifest_paths = [f["path"] for f in model.get("files", {}).get("manifest_sample", [])]
    assert ".env" not in manifest_paths
    assert "proposal" in model["identity"]["safety_gate"].lower()


def test_self_prompt_addendum_and_status_endpoint(monkeypatch, tmp_path):
    _redirect_self_paths(monkeypatch, tmp_path)
    result = sa.run_boot_self_audit(app_name="SHIMS Test", app_version="v-test", max_files=160, persist_brain=False)

    addendum = sa.self_prompt_addendum()
    assert result["boot_id"] in addendum
    assert "operational self-awareness, not human consciousness" in addendum
    assert "sandbox validation and human approval" in addendum

    client = TestClient(app)
    data = client.get("/self/status").json()
    assert data["ok"] is True
    assert data["boot_id"] == result["boot_id"]
    notes = client.get("/self/notes").json()
    assert notes["ok"] is True
    assert "SHIMS Boot Self-Awareness Note" in notes["notes"]
