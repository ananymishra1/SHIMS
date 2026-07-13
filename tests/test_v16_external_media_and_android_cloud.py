import os
from pathlib import Path

from fastapi.testclient import TestClient

import backend.app.main as omni


ROOT = Path(__file__).resolve().parents[1]


def _restore_env(snapshot: dict[str, str | None]) -> None:
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_v16_external_audio_video_settings_contract(monkeypatch):
    old_media = dict(omni._settings["media"])
    env_keys = ["SHIMS_AUDIO_API_KEY", "SHIMS_VIDEO_API_KEY", "SHIMS_AUDIO_API_URL", "SHIMS_VIDEO_API_URL"]
    old_env = {key: os.environ.get(key) for key in env_keys}
    writes: dict[str, str] = {}

    def fake_set_env(key: str, value: str | None) -> None:
        writes[key] = "" if value is None else str(value)
        os.environ[key] = writes[key]

    monkeypatch.setattr(omni, "_set_env_persistent", fake_set_env)
    try:
        client = TestClient(omni.app)
        response = client.post(
            "/media/settings",
            json={
                "audio_backend": "openai",
                "video_backend": "generic",
                "openai_tts_model": "gpt-4o-mini-tts",
                "openai_tts_voice": "verse",
                "openai_video_model": "sora-2",
                "openai_video_size": "1280x720",
                "openai_video_seconds": 6,
                "audio_api_url": "https://api.example.test/audio",
                "audio_api_key": "audio-secret-token",
                "video_api_url": "https://api.example.test/video",
                "video_api_key": "video-secret-token",
            },
        )

        assert response.status_code == 200
        data = response.json()
        settings = data["settings"]
        assert data["providers"]["audio"] == ["auto", "openai", "generic", "local"]
        assert data["providers"]["video"] == ["auto", "openai", "generic", "local"]
        assert settings["audio_backend"] == "openai"
        assert settings["video_backend"] == "generic"
        assert settings["openai_tts_voice"] == "verse"
        assert settings["openai_video_seconds"] == 6
        assert settings["audio_api_key"] != "audio-secret-token"
        assert settings["video_api_key"] != "video-secret-token"
        assert writes["SHIMS_AUDIO_API_KEY"] == "audio-secret-token"
        assert writes["SHIMS_VIDEO_API_KEY"] == "video-secret-token"
    finally:
        omni._settings["media"].clear()
        omni._settings["media"].update(old_media)
        _restore_env(old_env)


def test_v16_media_generate_can_route_audio_to_openai_provider(monkeypatch):
    old_media = dict(omni._settings["media"])

    async def fake_openai_audio(prompt: str):
        return {
            "ok": True,
            "provider": "openai-tts",
            "type": "audio",
            "kind": "audio",
            "title": prompt,
            "file_url": "/media/files/audio/fake.mp3",
            "url": "/media/files/audio/fake.mp3",
        }

    monkeypatch.setattr(omni, "_openai_audio", fake_openai_audio)
    try:
        client = TestClient(omni.app)
        response = client.post(
            "/media/generate",
            json={"kind": "audio", "prompt": "say hello for the mobile app", "provider": "openai"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["provider"] == "openai-tts"
        assert data["kind"] == "audio"
        assert "trust" in data
        assert data["action_id"]
    finally:
        omni._settings["media"].clear()
        omni._settings["media"].update(old_media)


def test_v16_omni_and_android_cloud_controls_are_wired():
    omni_html = (ROOT / "frontend" / "shims_omni.html").read_text(encoding="utf-8")
    omni_js = (ROOT / "frontend" / "js" / "shims_omni.js").read_text(encoding="utf-8")
    android_html = (ROOT / "android_app" / "app" / "src" / "main" / "assets" / "shims_personal" / "index.html").read_text(encoding="utf-8")
    android_js = (ROOT / "android_app" / "app" / "src" / "main" / "assets" / "shims_personal" / "js" / "app.js").read_text(encoding="utf-8")
    android_java = (ROOT / "android_app" / "app" / "src" / "main" / "java" / "com" / "jklifecare" / "shimsmobile" / "MainActivity.java").read_text(encoding="utf-8")

    for token in ["set-audio-backend", "set-video-backend", "mf-provider", "openai-video-seconds"]:
        assert token in omni_html
    for token in ["audio_backend", "video_backend", "openai_video_seconds", "provider"]:
        assert token in omni_js

    for token in ["chatProvider", "openaiApiKey", "geminiApiKey", "audioPrompt", "mediaProvider"]:
        assert token in android_html
    for token in ["cloudChatAsync", "saveCloudKey", "provider: state.backendProvider", "generateAudio"]:
        assert token in android_js
    for token in ["saveCloudKey", "cloudKeyStatus", "cloudChatAsync", "api.openai.com/v1/responses", "generativelanguage.googleapis.com"]:
        assert token in android_java


def test_v16_brain_stream_exceptions_end_as_ndjson_error(monkeypatch):
    async def broken_stream(req):
        yield omni._jsonl({"type": "meta", "session_id": "stream-test"})
        raise RuntimeError("simulated stream break")

    monkeypatch.setattr(omni, "_brain_stream", broken_stream)
    # Disable the fast direct-LLM lane so the request actually exercises the
    # (broken) full brain stream and its error handling.
    monkeypatch.setattr(omni, "_fast_chat_eligible", lambda req: False)
    client = TestClient(omni.app)

    with client.stream("POST", "/brain/turn", json={"message": "hello"}) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"type": "meta"' in body
    assert '"type": "error"' in body
    assert '"route": "stream-error"' in body
    assert '"type": "done"' in body


def test_v16_frontends_do_not_hard_abort_brain_streams_after_one_minute():
    omni_js = (ROOT / "frontend" / "js" / "shims_omni.js").read_text(encoding="utf-8")
    android_js = (ROOT / "android_app" / "app" / "src" / "main" / "assets" / "shims_personal" / "js" / "app.js").read_text(encoding="utf-8")

    assert "IDLE_TIMEOUT_MS = 5 * 60 * 1000" in omni_js
    assert "BodyStreamBuffer" in omni_js
    assert "5 * 60 * 1000" in android_js
    assert "BodyStreamBuffer" in android_js
