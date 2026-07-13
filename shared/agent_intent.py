"""Intent classifier for the SHIMS agent graph.

Determines whether a user turn should be handled as:
  - conversation (direct chat)
  - research (web search, fetch, summarize)
  - automation (run code, shell, plan, file edits)
  - hybrid (research + automation)

It uses fast keyword heuristics first, then a lightweight local LLM call if the
keywords are ambiguous. This keeps latency low while still being robust.
"""
from __future__ import annotations

import re
from typing import Any


INTENT_LABELS = {"conversation", "research", "automation", "hybrid"}


# Fast keyword maps. Lower-case tokens.
_RESEARCH_KEYWORDS = {
    "search", "find", "look up", "lookup", "research", "patent", "paper", "article",
    "summarize", "summary", "what is", "what are", "who is", "who are", "explain",
    "compare", "pros and cons", "latest", "news", "documentation", "docs", "wiki",
    "wikipedia", "google", "website", "web", "online", "url", "fetch", "read this",
    "analyze this", "extract", "information about", "tell me about",
}

_AUTOMATION_KEYWORDS = {
    "run", "execute", "script", "code", "python", "shell", "command", "terminal",
    "build", "compile", "install", "fix", "patch", "edit", "write", "create",
    "generate", "make", "convert", "download", "upload", "copy", "move", "delete",
    "git", "commit", "push", "pull", "test", "pytest", "debug", "refactor",
    "set up", "configure", "deploy", "docker", "compose", "schedule", "plan",
    "automation", "automate", "workflow", "pipeline", "batch",
}

_HYBRID_INDICATORS = {
    "and then", "after that", "then", "and also", "plus", "combine", "using the research",
    "based on the research", "find and", "search and", "research and then", "build a",
    "create a script that", "write code to", "fetch and analyze", "analyze and report",
}


_RESEARCH_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in _RESEARCH_KEYWORDS) + r")\b")
_AUTOMATION_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in _AUTOMATION_KEYWORDS) + r")\b")
_HYBRID_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in _HYBRID_INDICATORS) + r")\b")


def classify_keywords(query: str) -> str:
    """Return intent using only keyword heuristics."""
    text = query.lower()
    has_research = bool(_RESEARCH_RE.search(text))
    has_automation = bool(_AUTOMATION_RE.search(text))
    has_hybrid = bool(_HYBRID_RE.search(text))

    if has_hybrid or (has_research and has_automation):
        return "hybrid"
    if has_automation:
        return "automation"
    if has_research:
        return "research"
    return "conversation"


_INTENT_PROMPT = """You are an intent classifier. Read the user message and choose ONE intent:
- conversation: casual chat, greetings, opinions, or questions that need no tools.
- research: needs web search, reading URLs/papers, summarizing external info.
- automation: needs running code/shell, editing files, building, configuring, scheduling.
- hybrid: needs both research AND automation in the same turn.

Respond with exactly one word from the list above. No explanation.

User message: {query}
Intent:"""


async def classify_llm(query: str, chat_fn: Any) -> str:
    """Classify intent using a small LLM call.

    `chat_fn(messages)` should return a dict with a "content" string.
    """
    messages = [
        {"role": "system", "content": "You classify user intent into one word."},
        {"role": "user", "content": _INTENT_PROMPT.format(query=query)},
    ]
    try:
        result = await chat_fn(messages)
        text = (result.get("content") if isinstance(result, dict) else str(result)).strip().lower()
        # Strip punctuation and markdown
        text = re.sub(r"[^a-z]+", "", text)
        if text in INTENT_LABELS:
            return text
    except Exception:
        pass
    return "conversation"


async def classify_intent(
    query: str,
    *,
    chat_fn: Any | None = None,
    use_llm: bool = True,
) -> str:
    """Classify user intent.

    Args:
        query: raw user message.
        chat_fn: optional async callable(messages) -> {content: str} for LLM fallback.
        use_llm: if True and keyword classification is ambiguous, ask a small LLM.

    Returns:
        One of "conversation", "research", "automation", "hybrid".
    """
    keyword_intent = classify_keywords(query)

    # Short / clearly one category → trust keywords
    if keyword_intent in {"automation", "hybrid"}:
        return keyword_intent

    # Research can overlap with conversation ("what is AI?"). Use LLM for long queries.
    if keyword_intent == "research" and len(query) < 60:
        return keyword_intent

    if not use_llm or chat_fn is None:
        return keyword_intent

    # Ambiguous case: ask the LLM
    return await classify_llm(query, chat_fn)
