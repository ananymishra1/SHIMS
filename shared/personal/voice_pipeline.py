"""Voice pipeline for SHIMS Personal AI.

STT -> LLM -> TTS with half-duplex guarding.
Can use:
- faster-whisper (local, best quality)
- Android native SpeechRecognizer (via bridge)
- Browser Web Speech API (fallback)
"""
from __future__ import annotations

import io
import tempfile
import time
from typing import Any, Optional

from shared.personal.voice_state import can_accept_voice, mark_speaking, mark_speech_done


def transcribe_audio_local(audio_bytes: bytes, lang: str = "en") -> str:
    """Transcribe audio using faster-whisper if available."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
        model = WhisperModel("base", device="cpu", compute_type="int8")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        segments, _ = model.transcribe(tmp, language=lang, beam_size=5)
        return " ".join(s.text for s in segments).strip()
    except Exception:
        return ""


def speak_text(text: str, lang: str = "en-IN") -> dict[str, Any]:
    """Generate TTS audio. Returns file path or error."""
    try:
        from shared.ai import _create_audio  # type: ignore
        result = _create_audio(text[:500])
        return {"ok": True, "file_url": result.get("file_url"), "engine": result.get("engine", "tone-fallback")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def voice_turn(
    audio_bytes: bytes,
    llm_generate_fn: Any,
    stt_lang: str = "en",
    tts_lang: str = "en-IN",
) -> dict[str, Any]:
    """Full voice pipeline: STT -> LLM -> TTS with guards."""
    text = transcribe_audio_local(audio_bytes, stt_lang)
    if not text:
        return {"ok": False, "error": "Could not transcribe audio", "speak": False}

    ok, reason = can_accept_voice(text)
    if not ok:
        return {"ok": True, "ignored": True, "reason": reason, "text": text, "speak": False}

    mark_speaking()
    try:
        answer = llm_generate_fn(text)
        tts = speak_text(answer, tts_lang)
        return {
            "ok": True,
            "text": text,
            "answer": answer,
            "speak": True,
            "tts_file": tts.get("file_url"),
            "tts_engine": tts.get("engine"),
        }
    finally:
        mark_speech_done()
