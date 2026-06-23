"""Voice entry service using faster-whisper."""
from __future__ import annotations

import os
import tempfile
import wave
from pathlib import Path
from typing import Any

from shared.stt_corrector import correct_transcript
from ..config import VOICE_MODEL


def _whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _convert_webm_to_wav(webm_bytes: bytes) -> bytes:
    """Use ffmpeg if available; otherwise attempt raw header guess."""
    import subprocess
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fin:
        fin.write(webm_bytes)
        in_path = fin.name
    out_path = in_path.replace(".webm", ".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", in_path, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", out_path],
            check=True, capture_output=True, timeout=60,
        )
        with open(out_path, "rb") as f:
            return f.read()
    except Exception as exc:
        raise RuntimeError(f"ffmpeg conversion failed: {exc}") from exc
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except Exception:
                pass


async def transcribe_voice(audio_bytes: bytes, content_type: str = "audio/webm") -> dict[str, Any]:
    if not _whisper_available():
        return {"ok": False, "error": "faster-whisper not installed. Install with: pip install faster-whisper"}
    from faster_whisper import WhisperModel

    try:
        wav_bytes = _convert_webm_to_wav(audio_bytes)
    except Exception as exc:
        return {"ok": False, "error": f"Audio conversion failed: {exc}"}

    model = WhisperModel(VOICE_MODEL, device="cpu", compute_type="int8")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        wav_path = f.name
    try:
        segments, info = model.transcribe(wav_path, language=None, task="transcribe")
        text = " ".join([seg.text for seg in segments]).strip()
        lang = info.language if info else "unknown"
        # Run through STT corrector
        corrected = await correct_transcript(text, language=lang)
        return {
            "ok": True,
            "language": lang,
            "raw": text,
            "corrected": corrected.get("corrected", text),
            "changed": corrected.get("changed", False),
            "confidence": corrected.get("confidence", 0.0),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            os.unlink(wav_path)
        except Exception:
            pass
