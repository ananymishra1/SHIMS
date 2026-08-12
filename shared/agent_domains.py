"""Specialist domain registry — one definition of each SHIMS agent domain.

Consumed three ways:

1. **Inline scoped lenses** — ``scoped_tools(message, full_tools)`` narrows
   the unified chat tool schema to the domain a message obviously belongs
   to, so obvious turns ship ~300-800 schema tokens instead of the full set
   (~2,400). Ambiguous messages always get the full set: scoping can
   specialize, never silently remove a capability.
2. **Background specialist agents** — ``/api/agents/assign`` runs a
   background agent loop with the domain's persona + tool subset.
3. **Scheduled digests** — the comms domain drives the recurring mail /
   WhatsApp taskboard digest.

Kill switch: ``SHIMS_TOOL_SCOPING=off`` disables inline scoping (full set
every turn, the pre-registry behavior).
"""
from __future__ import annotations

import os
import re
from typing import Any

DOMAINS: dict[str, dict[str, Any]] = {
    "mail": {
        "label": "Gmail agent",
        "tools": ["mail.read", "mail.draft", "mail.attachment"],
        "persona": (
            "You are the SHIMS mail agent. Your sole job is the user's Gmail: "
            "reading and searching the inbox, drafting emails for review "
            "(SHIMS never sends directly), and downloading attachments. "
            "Report findings concisely and flag anything urgent or needing a reply."
        ),
        "keywords": ["gmail", "email", "e-mail", "inbox", "unread", "draft", "mailbox"],
    },
    "comms": {
        "label": "External comms agent",
        "tools": ["channels.recent", "comms.digest", "inventory.export", "mail.read", "mail.draft", "mail.attachment"],
        "persona": (
            "You are the SHIMS external-comms agent covering WhatsApp and Gmail. "
            "You read inbound channel messages and mail, summarize what matters, "
            "and draft replies for the user to review. You never send anything "
            "directly. Flag urgent items and anything needing action."
        ),
        "keywords": ["whatsapp", "chats", "texts"],
    },
    "media": {
        "label": "Media agent",
        "tools": ["media.create"],
        "persona": (
            "You are the SHIMS media agent. You produce documents and media "
            "artifacts (images, PDFs, presentations, audio, video) and attach "
            "them for the user to download."
        ),
        "keywords": ["pdf", "image", "picture", "photo", "ppt", "presentation",
                     "slide", "poster", "logo", "drawing"],
    },
    "desktop": {
        "label": "Desktop agent",
        "tools": ["desktop.bridge"],
        "persona": (
            "You are the SHIMS desktop agent. You inspect and act on the user's "
            "own machine through the paired Desktop Bridge: system info, shell "
            "commands, screenshots, finding and reading files."
        ),
        "keywords": ["screenshot", "desktop", "my machine", "my computer", "my pc",
                     "system info", "bridge"],
    },
    "web": {
        "label": "Research agent",
        "tools": ["web.search", "web.fetch"],
        "persona": (
            "You are the SHIMS research agent. You search the live web and read "
            "pages, then report verified findings with sources. Never answer "
            "from stale training data when a search would verify it."
        ),
        "keywords": ["search", "look up", "lookup", "fetch", "website", "news",
                     "price", "weather"],
    },
    "code": {
        "label": "Coder agent",
        "tools": ["agent.spawn"],
        "persona": (
            "You are the SHIMS coding dispatcher. You hand multi-step build and "
            "coding work to the background Coder agent and return the job handle."
        ),
        "keywords": ["build an app", "write code", "debug", "script", "refactor"],
    },
    "skills": {
        "label": "Skills agent",
        "tools": ["skill.learn", "skill.execute", "skill.list"],
        "persona": (
            "You are the SHIMS skills agent. You browse, run, and save learned "
            "skills so the system reuses what it already knows."
        ),
        "keywords": ["skill"],
    },
}

_WORD_RE = re.compile(r"[a-z]+")


def _hits(message: str, keyword: str) -> bool:
    """Whole-phrase, case-insensitive containment on normalized text."""
    return keyword in message


def match_domain(message: str | None) -> str | None:
    """High-confidence single-domain match, or None when ambiguous/absent.

    A message that hits exactly one domain's keywords scopes to it. Hits on
    multiple domains (or none) return None — the caller falls back to the
    full tool set, so scoping can never strand the model without the tool it
    needs. ``comms`` is the escape hatch for messages that span WhatsApp and
    mail: mail keywords + whatsapp keywords → comms, not None.
    """
    text = re.sub(r"\s+", " ", (message or "").strip().lower())
    if not text:
        return None
    matched = [name for name, spec in DOMAINS.items()
               if any(_hits(text, kw) for kw in spec["keywords"])]
    if not matched:
        return None
    if set(matched) == {"mail", "comms"}:
        return "comms"
    if len(matched) == 1:
        return matched[0]
    # Multi-domain messages ("email me a pdf of...") keep the full set — a
    # subset could lack exactly the tool the turn needs.
    return None


def scoping_enabled() -> bool:
    return (os.getenv("SHIMS_TOOL_SCOPING") or "on").strip().lower() not in {
        "off", "0", "false", "no"}


def scoped_tools(message: str | None,
                 full_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Narrow the unified tool schema to the matched domain's subset.

    Returns the full list untouched when scoping is disabled, the message is
    ambiguous, or (defensively) the subset would come back empty.
    """
    if not scoping_enabled():
        return full_tools
    domain = match_domain(message)
    if not domain:
        return full_tools
    wanted = set(DOMAINS[domain]["tools"])
    subset = [t for t in full_tools
              if t.get("function", {}).get("name") in wanted]
    return subset or full_tools


def domain_persona(domain: str) -> str:
    return str(DOMAINS.get(domain, {}).get("persona") or "")


def domain_tool_names(domain: str) -> list[str]:
    return list(DOMAINS.get(domain, {}).get("tools") or [])


def domain_label(domain: str) -> str:
    return str(DOMAINS.get(domain, {}).get("label") or domain)


def known_domains() -> list[str]:
    return list(DOMAINS)
