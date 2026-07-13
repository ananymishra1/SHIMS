"""SHIMS Cortex — the mutable, hot-reloadable layer of a self-evolving agent.

The packaging problem: how can a shipped desktop app keep improving itself
without rebuilding the binary? The answer is a **Kernel vs Cortex** split:

  • Kernel (frozen in the app): the runtime, security guards, the self-evolver
    engine itself. It never self-modifies. Lives in shims_core / backend.
  • Cortex (mutable, hot-reloadable): skills, prompts, tool definitions and
    small behavior patches. This is what evolves at runtime — no rebuild.

This module is a thin orchestration layer that ties together pieces that already
exist in SHIMS (``shims_core.self_evolution`` for the guarded apply/rollback
pipeline, ``shared.skills`` for procedural memory, ``shared.behavior_engine`` for
learned signals) into one coherent, confidence-gated workflow:

    propose → validate (sandbox) → approve (human / confidence) → apply → rollback?

Code changes always require human approval. Cortex *content* (skills, prompt
overlays) can auto-apply when confidence is high, because it is reversible and
cannot escalate privilege.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from . import skills as skill_store
from .config import ROOT_DIR

# The cortex content store (hot-reloadable, non-code).
_CORTEX_DIR = Path(ROOT_DIR) / "data" / "state" / "cortex"
_CORTEX_DIR.mkdir(parents=True, exist_ok=True)
_PROMPT_OVERLAY = _CORTEX_DIR / "prompt_overlay.json"

# Confidence gate for auto-applying reversible cortex changes.
AUTO_APPLY_CONFIDENCE = 0.85
SUGGEST_CONFIDENCE = 0.70

# Paths that are KERNEL — cortex must never auto-modify these (code changes here
# go through the human-approved self_evolution pipeline only).
KERNEL_PREFIXES = ("shims_core/", "backend/", "shared/guardians.py", "shared/config.py")


def is_kernel_path(rel: str) -> bool:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    return any(rel == p or rel.startswith(p) for p in KERNEL_PREFIXES)


# --------------------------------------------------------------------------- #
# Prompt overlay (hot-reloadable behavior without code changes)
# --------------------------------------------------------------------------- #

def get_prompt_overlay() -> str:
    """Return the current additive prompt overlay text (may be empty)."""
    if not _PROMPT_OVERLAY.exists():
        return ""
    try:
        data = json.loads(_PROMPT_OVERLAY.read_text(encoding="utf-8"))
        return str(data.get("text", ""))
    except Exception:
        return ""


def set_prompt_overlay(text: str, *, reason: str = "") -> dict[str, Any]:
    """Hot-update the system-prompt overlay. Takes effect on the next turn."""
    payload = {"text": text or "", "reason": reason, "updated_at": time.time()}
    _PROMPT_OVERLAY.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, **payload}


# --------------------------------------------------------------------------- #
# Self-evolution pipeline (delegates to the guarded kernel engine)
# --------------------------------------------------------------------------- #

def _engine():
    """Import the kernel self-evolution engine lazily (keeps cortex importable
    even if the backend package isn't on the path in a given context)."""
    try:
        from shims_core import self_evolution  # type: ignore
        return self_evolution
    except Exception:
        return None


def propose_change(goal: str) -> dict[str, Any]:
    """Stage a proposal for a code change (kernel-guarded)."""
    eng = _engine()
    if eng is None:
        return {"ok": False, "error": "self_evolution engine unavailable"}
    try:
        return {"ok": True, "proposal": eng.propose(goal)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "error": str(exc)[:200]}


def apply_change(files: list[dict[str, str]], *, approved: bool = False,
                 confidence: float = 0.0) -> dict[str, Any]:
    """Apply a code change through the guarded pipeline.

    Code changes ALWAYS require explicit human approval; confidence alone never
    unlocks kernel edits. Reversible apply with automatic rollback on validation
    failure is provided by the underlying engine.
    """
    blocked = [f.get("path", "") for f in files if is_kernel_path(f.get("path", ""))]
    if blocked and not approved:
        return {"ok": False, "error": "kernel_paths_require_approval", "paths": blocked}
    if not approved:
        return {"ok": False, "error": "approval_required",
                "hint": "Code changes require explicit human approval (4-phase pipeline).",
                "confidence": confidence}
    eng = _engine()
    if eng is None:
        return {"ok": False, "error": "self_evolution engine unavailable"}
    try:
        # apply=True performs backup → write → validate → rollback-on-failure.
        return eng.apply_changes(files, apply=True)
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "error": str(exc)[:200]}


# --------------------------------------------------------------------------- #
# Confidence-gated cortex content evolution (reversible, may auto-apply)
# --------------------------------------------------------------------------- #

def evolve_skill(name: str, summary: str, body: str = "", *, confidence: float = 0.0,
                 tags: Optional[list[str]] = None) -> dict[str, Any]:
    """Add/refine a skill. Auto-applies above the confidence gate, else suggests.

    Skills are reversible content (not code), so high-confidence auto-apply is
    safe — the user can always 'forget' them.
    """
    decision = "auto" if confidence >= AUTO_APPLY_CONFIDENCE else (
        "suggest" if confidence >= SUGGEST_CONFIDENCE else "hold")
    if decision != "auto":
        return {"ok": True, "decision": decision, "confidence": confidence,
                "preview": {"name": name, "summary": summary, "body": body}}
    saved = skill_store.save_skill(
        name=name, summary=summary, body=body,
        tags=(tags or []) + ["cortex"], source="cortex-evolution",
    )
    return {"ok": True, "decision": "applied", "confidence": confidence, "skill": saved}


def status() -> dict[str, Any]:
    """Snapshot of the cortex/kernel layout for the UI."""
    eng = _engine()
    return {
        "ok": True,
        "architecture": "kernel/cortex",
        "kernel": {
            "frozen": True,
            "protected_prefixes": list(KERNEL_PREFIXES),
            "engine_available": eng is not None,
        },
        "cortex": {
            "hot_reloadable": ["skills", "prompt_overlay", "tool_definitions", "behavior_patches"],
            "prompt_overlay_active": bool(get_prompt_overlay()),
            "skill_count": len(skill_store.list_skills(limit=1000)),
        },
        "gates": {
            "code_changes": "human_approval_required",
            "cortex_auto_apply_confidence": AUTO_APPLY_CONFIDENCE,
            "cortex_suggest_confidence": SUGGEST_CONFIDENCE,
        },
    }
