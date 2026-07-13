from __future__ import annotations

import time
from dataclasses import dataclass, asdict


@dataclass
class VoiceRuntimeState:
    listening: bool = False
    speaking: bool = False
    last_user_text: str = ""
    last_user_at: float = 0.0
    last_assistant_at: float = 0.0
    cooldown_until: float = 0.0

    def to_dict(self):
        return asdict(self)


STATE = VoiceRuntimeState()


def can_accept_voice(text: str) -> tuple[bool, str]:
    now = time.time()
    clean = (text or "").strip().lower()
    if not clean:
        return False, "empty_voice_ignored"
    if STATE.speaking:
        return False, "assistant_speaking"
    if now < STATE.cooldown_until:
        return False, "cooldown"
    if clean == STATE.last_user_text and now - STATE.last_user_at < 3.0:
        return False, "duplicate_voice_turn"
    STATE.last_user_text = clean
    STATE.last_user_at = now
    return True, "accepted"


def mark_speaking():
    STATE.speaking = True
    STATE.listening = False
    STATE.last_assistant_at = time.time()


def mark_speech_done(cooldown_seconds: float = 0.75):
    STATE.speaking = False
    STATE.cooldown_until = time.time() + cooldown_seconds


def get_voice_state():
    return STATE.to_dict()
