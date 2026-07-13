from fastapi.testclient import TestClient

from backend.app import main as omni


def test_voice_speak_never_crashes_when_server_tts_fails(monkeypatch):
    def failed_tts(*_args, **_kwargs):
        return {"ok": False, "engine": "pyttsx3", "spoken": False, "error": "simulated tts failure"}

    monkeypatch.setattr(omni, "_synthesize_pyttsx3_file", failed_tts)
    client = TestClient(omni.app)

    response = client.post("/voice/speak", json={"text": "SHIMS voice test", "lang": "en-IN", "rate": 172})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["engine"] == "local-tone-fallback"
    assert data["spoken"] is False
    assert data["tts_error"] == "simulated tts failure"
    assert data["file_url"].startswith("/media/files/audio/")


def test_voice_brain_turn_still_returns_a_reply_for_wake_greeting():
    client = TestClient(omni.app)

    with client.stream("POST", "/brain/turn", json={"message": "hey shims", "source": "voice", "provider": "ollama"}) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"route": "greeting"' in body
    assert "I'm listening" in body
