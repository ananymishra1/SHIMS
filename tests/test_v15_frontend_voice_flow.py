from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "js" / "shims_omni.js"


def _script() -> str:
    return FRONTEND.read_text(encoding="utf-8", errors="replace")


def test_voice_reply_playback_is_awaited_before_idle_reset():
    js = _script()

    assert "await speakText(answer.replace" in js


def test_server_stt_resumes_after_tts_reply():
    js = _script()

    assert "state.serverVoiceShouldResume = false" in js
    assert "state.serverVoiceShouldResume = Boolean(state.voiceOn && !state.recognition && state.serverVoiceLoop)" in js
    assert "if(!state.recognition && state.serverVoiceShouldResume)" in js
    assert "setTimeout(()=>startServerVoiceFallback(), 800)" in js


def test_wake_detection_opens_command_latch():
    js = _script()

    assert "state.wakeLatchUntil = 0" in js
    assert "function armWakeLatch" in js
    assert "function wakeLatchActive()" in js
    assert "if(!hasWake && !wakeLatchActive())" in js
    assert "state.wakeLatchUntil = 0;" in js


def test_wake_only_phrase_gets_spoken_acknowledgement():
    js = _script()

    assert "function speakWakeAck()" in js
    assert "if(!command){ speakWakeAck(); return; }" in js
    assert "speakText('Yes, I am listening.')" in js
    assert "queueWakeAck(1400)" in js


def test_tts_paths_have_watchdogs_and_server_contract_handling():
    js = _script()

    assert "function estimateSpeechMs" in js
    assert "browser speech timed out" in js
    assert "server audio playback timed out" in js
    assert "if(!r.ok || (data && data.ok === false))" in js
    assert "data.spoken === true" in js


def test_server_stt_starts_even_when_wake_engine_is_running_without_browser_stt():
    js = _script()

    assert "if(!r){" in js
    assert "await startServerVoiceFallback();" in js


def test_wake_engine_uses_standard_audio_worklet_constructor_with_fallback():
    js = _script()

    assert "new AudioWorkletNode(this.ctx, 'ww-processor'" in js
    assert "createAudioWorkletNode" not in js
    assert "createScriptProcessor(4096, 1, 1)" in js
    assert "AudioWorklet unavailable, using ScriptProcessor fallback" in js
