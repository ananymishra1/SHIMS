from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app


ROOT = Path(__file__).resolve().parents[1]


def test_capability_check_refuses_live_apply_without_approval_phrase():
    client = TestClient(app)
    data = client.post(
        "/evolution/capability-check",
        json={"apply": True, "revision": "pytest-refuse", "targets": ["feature"]},
    ).json()

    assert data["ok"] is False
    assert data["status"] == "approval_required"


def test_capability_check_applies_backend_frontend_and_updates_feature():
    client = TestClient(app)
    rev1 = "pytest-capability-v1"
    data = client.post(
        "/evolution/capability-check",
        json={
            "apply": True,
            "approval_phrase": "I_APPROVE_SHIMS_PATCH",
            "approved_by": "pytest-human",
            "revision": rev1,
            "targets": ["backend", "frontend", "feature"],
        },
    ).json()

    assert data["ok"] is True
    assert data["applied"] is True
    assert {item["target"] for item in data["targets"]} == {"backend", "frontend", "feature"}
    assert all(item["status"] == "applied" for item in data["targets"])

    backend_file = ROOT / "backend" / "generated_features" / "omni_backend_probe.py"
    frontend_file = ROOT / "frontend" / "self_evolution_probe.js"
    feature_file = ROOT / "shared" / "generated_skills" / "omni_feature_probe.py"
    for path in (backend_file, frontend_file, feature_file):
        assert path.exists(), path
        assert rev1 in path.read_text(encoding="utf-8")

    rev2 = "pytest-capability-v2"
    update = client.post(
        "/evolution/capability-check",
        json={
            "apply": True,
            "approval_phrase": "I_APPROVE_SHIMS_PATCH",
            "approved_by": "pytest-human",
            "revision": rev2,
            "targets": ["feature"],
        },
    ).json()

    assert update["ok"] is True
    assert update["targets"][0]["status"] == "applied"
    text = feature_file.read_text(encoding="utf-8")
    assert rev2 in text
    assert "self-evolution can create and update feature modules" in text
