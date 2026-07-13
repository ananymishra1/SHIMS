"""ChemDFM bridge for SHIMS chemistry R&D.

ChemDFM is a chemistry foundation model. This bridge lets SHIMS query it
for molecular insights, reactions, and property predictions, while keeping
a training journal for iterative learning.

Usage:
    from shared.chemdfm_bridge import chemdfm_query, chemdfm_train, get_journal_summary

The bridge tries (in order):
1. Local Ollama model tagged "chemdfm"
2. HTTP API endpoint from env CHEMDFM_API_URL
3. Fallback to rule-based chemistry (shims_chem_api)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .config import ROOT_DIR
import logging

logger = logging.getLogger("chemdfm")

_JOURNAL_PATH = Path(ROOT_DIR) / "data" / "state" / "chemdfm_journal.jsonl"
_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)

_CHEMDFM_API_URL = os.getenv("CHEMDFM_API_URL", "").strip()
_CHEMDFM_OLLAMA_TAG = os.getenv("CHEMDFM_OLLAMA_TAG", "chemdfm").strip()


def _journal_append(entry: dict[str, Any]) -> None:
    try:
        with open(_JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.warning(f"ChemDFM journal write failed: {exc}")


def get_journal_summary(limit: int = 100) -> dict[str, Any]:
    """Return statistics about the training journal."""
    entries: list[dict[str, Any]] = []
    try:
        if _JOURNAL_PATH.exists():
            with open(_JOURNAL_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            pass
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}

    recent = entries[-limit:] if len(entries) > limit else entries
    topics: dict[str, int] = {}
    for e in recent:
        t = e.get("topic", "general")
        topics[t] = topics.get(t, 0) + 1

    return {
        "ok": True,
        "total_entries": len(entries),
        "recent_entries": len(recent),
        "topics": topics,
        "last_entry": recent[-1] if recent else None,
    }


async def _ollama_chemdfm(prompt: str, timeout: float = 60.0) -> dict[str, Any] | None:
    """Try local Ollama chemdfm model."""
    from .config import settings
    base = settings.ollama_base_url.rstrip("/")
    payload = {
        "model": _CHEMDFM_OLLAMA_TAG,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.3},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
            msg = data.get("message") or {}
            return {"content": msg.get("content", ""), "source": "ollama"}
    except Exception as exc:
        logger.debug(f"Ollama ChemDFM unavailable: {exc}")
        return None


async def _api_chemdfm(prompt: str, timeout: float = 60.0) -> dict[str, Any] | None:
    """Try remote ChemDFM API."""
    if not _CHEMDFM_API_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                _CHEMDFM_API_URL,
                json={"prompt": prompt, "temperature": 0.3},
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            return {"content": data.get("text") or data.get("content") or data.get("response", ""), "source": "api"}
    except Exception as exc:
        logger.debug(f"ChemDFM API unavailable: {exc}")
        return None


async def chemdfm_query(query: str, topic: str = "general", record: bool = True) -> dict[str, Any]:
    """Ask ChemDFM a chemistry question. Falls back to rule-based chemistry if unavailable.

    Args:
        query: Chemistry question / SMILES / reaction string.
        topic: Category for journal (synthesis, property, safety, retrosynthesis, etc.)
        record: Whether to append to training journal.
    """
    if not query.strip():
        return {"ok": False, "error": "query required"}

    # Try ChemDFM sources
    result = await _ollama_chemdfm(query)
    if not result:
        result = await _api_chemdfm(query)

    if result:
        answer = result["content"].strip()
        if record:
            _journal_append({
                "t": time.time(),
                "topic": topic,
                "query": query[:500],
                "answer": answer[:2000],
                "source": result["source"],
            })
        return {
            "ok": True,
            "answer": answer,
            "source": result["source"],
            "model": "chemdfm",
            "disclaimer": "ChemDFM suggestion (unverified). Validate with rule-based chemistry or experimental data before use.",
        }

    # Fallback to rule-based chemistry
    try:
        from .shims_chem_api import plan_retro, verify_smiles
        # Simple heuristic: if query looks like SMILES, verify it; otherwise give a generic note
        if any(c in query for c in "=()[]@#"):
            rb = verify_smiles(query)
            answer = rb.get("note") or rb.get("summary") or str(rb)
        else:
            answer = "Rule-based fallback: ChemDFM model is not loaded. Start it via Admin AI Settings (Ollama or local server), then retry."
        return {
            "ok": True,
            "answer": answer,
            "source": "rule-based-fallback",
            "model": "shims_chem",
            "disclaimer": "Rule-based chemistry fallback. ChemDFM was unavailable.",
        }
    except Exception as exc:
        return {"ok": False, "error": f"ChemDFM unavailable and rule-based fallback failed: {exc}"}


def chemdfm_train(fact: str, topic: str = "general", validated_by: str = "human") -> dict[str, Any]:
    """Feed a validated chemistry fact into the training journal for iterative learning.

    Args:
        fact: Validated chemistry statement / reaction / property.
        topic: Category.
        validated_by: Who validated it (human, experiment, rule-based).
    """
    if not fact.strip():
        return {"ok": False, "error": "fact required"}
    entry = {
        "t": time.time(),
        "topic": topic,
        "fact": fact[:2000],
        "validated_by": validated_by,
        "type": "training",
    }
    _journal_append(entry)
    return {"ok": True, "journal_path": str(_JOURNAL_PATH), "note": "Fact recorded for iterative learning."}


def chemdfm_iterative_learn() -> dict[str, Any]:
    """Analyze the journal and propose patterns / gaps for systematic improvement.

    Returns a summary of what ChemDFM has learned and what gaps remain.
    """
    summary = get_journal_summary(limit=500)
    if not summary["ok"]:
        return summary

    topics = summary.get("topics", {})
    gaps = []
    if topics.get("synthesis", 0) < 5:
        gaps.append("synthesis")
    if topics.get("property", 0) < 5:
        gaps.append("property")
    if topics.get("safety", 0) < 5:
        gaps.append("safety")
    if topics.get("retrosynthesis", 0) < 5:
        gaps.append("retrosynthesis")

    return {
        "ok": True,
        "total_entries": summary["total_entries"],
        "topic_distribution": topics,
        "gaps": gaps,
        "recommendation": f"Train more on: {', '.join(gaps)}" if gaps else "Coverage looks balanced. Continue validating facts.",
        "next_action": "Use chem.chemdfm_train to add validated facts in weak topic areas.",
    }
