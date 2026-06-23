"""Smart conversation context management for long agent sessions.

Automatically summarizes older turns so the context window stays bounded
without losing track of the conversation thread.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Approximate token count: 1 token ≈ 4 chars for English text
def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class Turn:
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ContextManager:
    """Manages conversation context size.

    Keeps recent turns in raw form and summarizes older ones.
    The agent always sees: [original request] + [summaries] + [recent raw turns]
    """

    def __init__(self, max_tokens: int = 12000, summary_trigger: int = 8000):
        self.max_tokens = max_tokens
        self.summary_trigger = summary_trigger
        self.turns: list[Turn] = []
        self.summaries: list[str] = []
        self.original_request: str = ""

    def set_original_request(self, text: str) -> None:
        self.original_request = text

    def add_turn(self, role: str, content: str, **metadata: Any) -> None:
        """Add a turn. If context exceeds trigger, summarize oldest turns."""
        self.turns.append(Turn(role=role, content=content, metadata=metadata))
        self._maybe_summarize()

    def to_messages(self, system_text: str = "") -> list[dict[str, str]]:
        """Build messages array for LLM consumption.

        Order: system → original request → summaries → recent raw turns
        """
        messages: list[dict[str, str]] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})

        # Original request is always preserved
        if self.original_request:
            messages.append({
                "role": "user",
                "content": f"ORIGINAL REQUEST (never forget this): {self.original_request}",
            })

        # Summaries of older conversation
        for summary in self.summaries:
            messages.append({"role": "system", "content": f"[Earlier conversation summary]\n{summary}"})

        # Recent raw turns
        for turn in self.turns:
            messages.append({"role": turn.role, "content": turn.content})

        return messages

    def get_token_count(self) -> int:
        """Approximate token count of current context."""
        total = 0
        for turn in self.turns:
            total += _approx_tokens(turn.content)
        for summary in self.summaries:
            total += _approx_tokens(summary)
        total += _approx_tokens(self.original_request)
        return total

    def _maybe_summarize(self) -> None:
        """If context is too large, summarize oldest turns into one paragraph."""
        while self.get_token_count() > self.summary_trigger and len(self.turns) > 6:
            # Take oldest 4-6 turns and collapse them into a summary
            batch_size = min(6, max(4, len(self.turns) // 3))
            batch = self.turns[:batch_size]
            self.turns = self.turns[batch_size:]

            summary_lines = []
            for turn in batch:
                prefix = "User" if turn.role == "user" else "Assistant"
                text = turn.content[:300].replace("\n", " ")
                summary_lines.append(f"{prefix}: {text}")

            summary = " | ".join(summary_lines)
            self.summaries.append(summary)

            # Keep only last 3 summaries to prevent unbounded growth
            while len(self.summaries) > 3:
                self.summaries.pop(0)

    def last_user_message(self) -> str:
        """Get the most recent user message."""
        for turn in reversed(self.turns):
            if turn.role == "user":
                return turn.content
        return self.original_request

    def export(self) -> dict[str, Any]:
        """Export state for persistence."""
        return {
            "original_request": self.original_request,
            "summaries": self.summaries,
            "turns": [{"role": t.role, "content": t.content} for t in self.turns],
            "token_count": self.get_token_count(),
        }
