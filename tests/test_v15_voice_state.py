from shared.voice_state import can_accept_voice, mark_speaking, mark_speech_done, STATE


def reset_state():
    STATE.listening = False
    STATE.speaking = False
    STATE.last_user_text = ""
    STATE.last_user_at = 0.0
    STATE.last_assistant_at = 0.0
    STATE.cooldown_until = 0.0


def test_duplicate_voice_ignored():
    reset_state()
    ok, _ = can_accept_voice("hello shims")
    assert ok is True
    ok, reason = can_accept_voice("hello shims")
    assert ok is False
    assert reason == "duplicate_voice_turn"


def test_speaking_blocks_voice():
    reset_state()
    mark_speaking()
    ok, reason = can_accept_voice("hello")
    assert ok is False
    assert reason == "assistant_speaking"
    mark_speech_done(0)
