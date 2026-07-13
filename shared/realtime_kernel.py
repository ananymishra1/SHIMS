"""SHIMS v14 realtime frame kernel.

This is not a full replacement for Pipecat. It is the SHIMS-owned frame layer that
lets the product run without optional dependencies while staying compatible with
Pipecat-style transport/STT/LLM/TTS processors when pipecat-ai is installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable
import importlib.util
import time


@dataclass(slots=True)
class ShimsFrame:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat(timespec="milliseconds") + "Z")


@dataclass(slots=True)
class RealtimeMetrics:
    stt_ms: float = 0
    router_ms: float = 0
    first_token_ms: float = 0
    tts_ms: float = 0
    total_ms: float = 0
    interrupted: bool = False


class HalfDuplexState:
    def __init__(self) -> None:
        self.listening = False
        self.speaking = False
        self.last_user_audio_at = 0.0
        self.last_assistant_audio_at = 0.0

    def can_listen(self) -> bool:
        return not self.speaking

    def start_speaking(self) -> None:
        self.speaking = True
        self.listening = False
        self.last_assistant_audio_at = time.time()

    def stop_speaking(self) -> None:
        self.speaking = False

    def start_listening(self) -> bool:
        if not self.can_listen():
            return False
        self.listening = True
        self.last_user_audio_at = time.time()
        return True


class FramePipeline:
    """Minimal ordered frame processor used by tests and fallback runtime."""

    def __init__(self) -> None:
        self.processors: list[Callable[[ShimsFrame], ShimsFrame | Iterable[ShimsFrame] | None]] = []

    def add(self, processor: Callable[[ShimsFrame], ShimsFrame | Iterable[ShimsFrame] | None]) -> "FramePipeline":
        self.processors.append(processor)
        return self

    def run(self, frame: ShimsFrame) -> list[ShimsFrame]:
        frames = [frame]
        for proc in self.processors:
            next_frames: list[ShimsFrame] = []
            for item in frames:
                out = proc(item)
                if out is None:
                    continue
                if isinstance(out, ShimsFrame):
                    next_frames.append(out)
                else:
                    next_frames.extend(list(out))
            frames = next_frames
        return frames


def pipecat_available() -> bool:
    return importlib.util.find_spec("pipecat") is not None


def manifest() -> dict[str, Any]:
    return {
        "kernel": "shims-v14-frame-kernel",
        "pipecat_available": pipecat_available(),
        "frames": ["audio", "stt_final", "turn", "tool_call", "tool_result", "llm_delta", "tts_audio", "interrupt", "metrics"],
        "guarantees": ["half_duplex", "one_answer_per_turn", "deterministic_tool_first", "verified_artifacts"],
    }
