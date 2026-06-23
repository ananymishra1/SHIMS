from __future__ import annotations

import re
from dataclasses import dataclass

CASUAL_PATTERNS = [
    r"^\s*hi\s*$",
    r"^\s*hello\s*$",
    r"^\s*hey\s*$",
    r"^\s*hey\s+shims\s*$",
    r"^\s*how are you\s*\??\s*$",
    r"^\s*sun rahe ho\s*\??\s*$",
    r"^\s*haan\s*$",
    r"^\s*yes\s*$",
    r"^\s*ok\s*$",
]

SEARCH_TRIGGERS = [
    "search", "internet", "web", "look up", "lookup", "latest", "current", "recent",
    "today", "news", "verify online", "online", "patent", "cas number", "cas no",
    "regulation", "rule", "law", "price", "market", "source", "citation", "cite", "browse",
]


@dataclass
class SearchDecision:
    should_search: bool
    reason: str
    confidence: float


def decide_search(user_text: str, web_mode: bool = False, force_search: bool = False) -> SearchDecision:
    text = (user_text or "").strip().lower()
    if not text:
        return SearchDecision(False, "empty_input", 1.0)
    if force_search:
        return SearchDecision(True, "force_search", 1.0)
    for pattern in CASUAL_PATTERNS:
        if re.match(pattern, text, flags=re.I):
            return SearchDecision(False, "casual_greeting", 1.0)
    if any(trigger in text for trigger in SEARCH_TRIGGERS):
        return SearchDecision(True, "explicit_or_freshness_trigger", 0.92)
    if web_mode:
        freshness_words = ["current", "latest", "today", "now", "recent", "2026", "new", "updated"]
        if any(word in text for word in freshness_words):
            return SearchDecision(True, "web_mode_plus_freshness", 0.8)
        return SearchDecision(False, "web_mode_no_search_needed", 0.75)
    return SearchDecision(False, "local_chat_sufficient", 0.85)
