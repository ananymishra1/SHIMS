"""Half-duplex voice runtime state for SHIMS Personal AI."""
from __future__ import annotations

import time
from typing import Optional

_last_voice_text: str = ""
_last_voice_at: float = 0.0
_speaking: bool = False
_cooldown_until: float = 0.0


def can_accept_voice(text: str) -> tuple[bool, Optional[str]]:
    """Return (ok, reason). Rejects empty, duplicates, cooldowns."""
    global _last_voice_text, _last_voice_at
    text = text.strip()
    if not text:
        return False, "empty_or_silence"
    now = time.time()
    if now < _cooldown_until:
        return False, "cooldown"
    if _speaking:
        return False, "assistant_speaking"
    if text.lower() == _last_voice_text.lower() and (now - _last_voice_at) < 3.0:
        return False, "silence_or_duplicate"
    _last_voice_text = text
    _last_voice_at = now
    return True, None


def mark_speaking() -> None:
    global _speaking
    _speaking = True


def mark_speech_done(cooldown_seconds: float = 0.75) -> None:
    global _speaking, _cooldown_until
    _speaking = False
    _cooldown_until = time.time() + cooldown_seconds
