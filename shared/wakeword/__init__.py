"""SHIMS wake word detection.

Audio wakeword support depends on heavier scientific/audio packages. Those
packages are optional for the core Enterprise + ingestion stack, so this module
must fail soft when they are not installed.
"""
from __future__ import annotations

try:
    from .detector import WakeWordDetector, get_detector
    from .trainer import WakeWordTrainer
except Exception as _wakeword_import_error:
    class WakeWordDetector:
        def detect(self, audio_bytes: bytes, transcript: str | None = None) -> dict | None:
            if transcript and "shims" in transcript.lower():
                return {
                    "label": "shims",
                    "score": 1.0,
                    "confidence": 1.0,
                    "backend": "text",
                }
            return None

        def status(self) -> dict:
            return {
                "available": False,
                "backend": "text-fallback",
                "error": str(_wakeword_import_error),
                "text_wake_words": ["shims"],
            }

        def list_wake_words(self) -> list[str]:
            return ["shims"]

    class WakeWordTrainer:
        def enroll_sample(self, label: str, audio_bytes: bytes) -> dict:
            return {
                "ok": False,
                "error": f"Wakeword audio dependencies are unavailable: {_wakeword_import_error}",
            }

        def delete_wake_word(self, label: str) -> dict:
            return {
                "ok": False,
                "label": label,
                "error": f"Wakeword audio dependencies are unavailable: {_wakeword_import_error}",
            }

        def list_wake_words(self) -> list[dict]:
            return []

    _DETECTOR = WakeWordDetector()

    def get_detector() -> WakeWordDetector:
        return _DETECTOR

__all__ = ["WakeWordDetector", "get_detector", "WakeWordTrainer"]
