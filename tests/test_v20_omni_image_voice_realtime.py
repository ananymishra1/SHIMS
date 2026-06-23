from fastapi.testclient import TestClient

from backend.app import main as omni


def test_diffusers_sdxl_cpu_guard_returns_fast_fallback(monkeypatch):
    old_media = dict(omni._settings["media"])
    monkeypatch.setenv("SHIMS_DIFFUSERS_ALLOW_SLOW_CPU", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        omni._settings["media"].update(
            {
                "image_backend": "diffusers",
                "diffusers_enabled": True,
                "diffusers_model": "stabilityai/stable-diffusion-xl-base-1.0",
                "stable_diffusion_url": "",
            }
        )
        client = TestClient(omni.app)
        response = client.post("/media/generate", json={"kind": "image", "prompt": "test panda relaxing"})

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["provider"] == "local-fallback"
        assert "SDXL on CPU is too slow" in data["note"]
        assert data["verified"] is True
    finally:
        omni._settings["media"].clear()
        omni._settings["media"].update(old_media)


def test_image_provider_local_forces_local_fallback(monkeypatch):
    old_media = dict(omni._settings["media"])
    try:
        omni._settings["media"].update({"image_backend": "diffusers", "diffusers_enabled": True})
        client = TestClient(omni.app)
        response = client.post(
            "/media/generate",
            json={"kind": "image", "prompt": "local fallback smoke", "provider": "local"},
        )

        data = response.json()
        assert response.status_code == 200
        assert data["provider"] == "local-fallback"
        assert data["file_url"].startswith("/media/files/images/")
    finally:
        omni._settings["media"].clear()
        omni._settings["media"].update(old_media)


def test_frontend_voice_defaults_to_continuous_conversation():
    js = (omni.ROOT / "frontend" / "js" / "shims_omni.js").read_text(encoding="utf-8", errors="replace")

    assert "wakeArmed: localStorage.shimsWakeArmed === 'true'" in js
    assert "if(state.wakeArmed){" in js
    assert "Voice conversation ready." in js
    assert "SERVER_STT_CHUNK_MS = 2200" in js
    assert "state.serverSttBackoffUntil = Date.now() + 5000" in js
    assert "r.status === 429" in js
