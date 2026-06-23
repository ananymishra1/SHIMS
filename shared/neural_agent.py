"""Neural Agent — unified self-evolution dashboard backend.

Unifies both proposal systems:
  1. Governor evolution proposals (SQLite: governor_evolution.sqlite3)
  2. Self-evolver proposals (JSON files: storage/evolution/proposals/)

Provides:
  - Test → Accept → Reject → Apply workflow
  - Reflection/proposal generation via local model
  - Model status and configuration
  - Permission queue integration
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import ROOT_DIR, STORAGE_DIR, settings
from .security import new_id

# Import both proposal systems
from .neural_governor import evolution as gov_evolution
from . import self_evolver


EVOLUTION_PROPOSALS_DIR = STORAGE_DIR / "evolution" / "proposals"


@dataclass
class ModelStatus:
    model: str
    provider: str = "ollama"
    available: bool = False
    size_gb: float = 0.0
    vram_gb: float = 0.0
    ram_gb: float = 0.0
    gpu_name: str = ""


def get_model_status() -> dict[str, Any]:
    """Get status of the configured self-evolution model."""
    model = settings.self_evolution_model
    from .neural_governor.hardware_profiler import quick_profile
    hw = quick_profile()

    # Check if model is available in Ollama
    available = False
    try:
        import httpx
        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=8)
        if r.status_code == 200:
            models = r.json().get("models", [])
            available = any(m.get("name", "").startswith(model.split(":")[0]) for m in models)
            if not available:
                # Check exact match
                available = any(m.get("name", "") == model for m in models)
    except Exception:
        pass

    return {
        "ok": True,
        "model": model,
        "provider": "ollama",
        "available": available,
        "hardware": {
            "vram_gb": round(hw.get("vram_gb", 0), 1),
            "ram_gb": round(hw.get("total_ram_gb", 0), 1),
            "cuda": hw.get("cuda_available", False),
            "gpu_name": "NVIDIA CUDA" if hw.get("cuda_available") else "CPU only",
        },
        "note": "Download gemma-4 from HuggingFace and run scripts/setup_gemma4_ollama.py to switch." if not available else "Model ready",
    }


def _load_self_evolver_proposals(limit: int = 50) -> list[dict[str, Any]]:
    """Load proposals from the self-evolver JSON store."""
    proposals = []
    for p in sorted(EVOLUTION_PROPOSALS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # Normalize to common schema
            proposals.append({
                "id": data.get("proposal_id", p.stem),
                "uuid": data.get("proposal_id", p.stem),
                "source": "self_evolver",
                "title": data.get("reason", "Untitled patch"),
                "description": data.get("reason", ""),
                "intent": data.get("reason", ""),
                "thought": "Self-evolver generated patch for " + data.get("relative_path", "unknown"),
                "patch_type": data.get("scope", "code"),
                "patch_content": data.get("new_content", ""),
                "diff": data.get("diff", ""),
                "affected_files": [data.get("relative_path", "")] if data.get("relative_path") else [],
                "status": data.get("status", "unknown"),
                "proposed_by": data.get("proposed_by", "system"),
                "reviewed_by": data.get("approved_by", None),
                "review_notes": data.get("approval_note", ""),
                "baseline_score": 0.0,
                "sandbox_score": 1.0 if data.get("status") == "validated" else 0.0,
                "improvement_delta": 0.0,
                "test_results": data.get("validation", []),
                "created_at": data.get("created_at", _utc()),
                "reviewed_at": data.get("approved_at", None),
                "deployed_at": data.get("applied_at", None),
            })
        except Exception:
            continue
    return proposals


def _load_governor_proposals(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Load proposals from the Governor SQLite store."""
    rows = gov_evolution.list_proposals(status=status, limit=limit)
    proposals = []
    for r in rows:
        proposals.append({
            "id": r["proposal_uuid"],
            "uuid": r["proposal_uuid"],
            "source": "governor",
            "title": r["title"],
            "description": r["description"] or "",
            "intent": r["title"],
            "thought": r["description"] or "Governor-detected pattern improvement",
            "patch_type": r["patch_type"] or "code",
            "patch_content": r["patch_content"] or "",
            "diff": r["patch_content"] or "",
            "affected_files": r.get("affected_files", []),
            "status": r["status"],
            "proposed_by": r["proposed_by"],
            "reviewed_by": r.get("reviewed_by"),
            "review_notes": r.get("review_notes", ""),
            "baseline_score": r.get("baseline_score", 0.0),
            "sandbox_score": r.get("sandbox_score", 0.0),
            "improvement_delta": r.get("improvement_delta", 0.0),
            "test_results": r.get("test_results", {}),
            "created_at": datetime.fromtimestamp(r["created_at"], tz=timezone.utc).isoformat() if isinstance(r["created_at"], (int, float)) else r["created_at"],
            "reviewed_at": datetime.fromtimestamp(r["reviewed_at"], tz=timezone.utc).isoformat() if r.get("reviewed_at") else None,
            "deployed_at": datetime.fromtimestamp(r["deployed_at"], tz=timezone.utc).isoformat() if r.get("deployed_at") else None,
        })
    return proposals


def list_all_proposals(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Unified proposal list from both stores, sorted by created_at desc."""
    gov = _load_governor_proposals(status=status, limit=limit)
    se = _load_self_evolver_proposals(limit=limit)
    all_proposals = gov + se
    # Sort by created_at desc
    all_proposals.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return all_proposals[:limit]


def get_proposal_full(proposal_id: str) -> dict[str, Any] | None:
    """Get full proposal details from either store."""
    # Try governor first
    gov = gov_evolution.get_proposal(proposal_id)
    if gov:
        return {
            "id": gov["proposal_uuid"],
            "uuid": gov["proposal_uuid"],
            "source": "governor",
            "title": gov["title"],
            "description": gov["description"] or "",
            "intent": gov["title"],
            "thought": gov["description"] or "",
            "patch_type": gov["patch_type"] or "code",
            "patch_content": gov["patch_content"] or "",
            "diff": gov["patch_content"] or "",
            "affected_files": gov.get("affected_files", []),
            "status": gov["status"],
            "proposed_by": gov["proposed_by"],
            "reviewed_by": gov.get("reviewed_by"),
            "review_notes": gov.get("review_notes", ""),
            "baseline_score": gov.get("baseline_score", 0.0),
            "sandbox_score": gov.get("sandbox_score", 0.0),
            "improvement_delta": gov.get("improvement_delta", 0.0),
            "test_results": gov.get("test_results", {}),
            "created_at": gov["created_at"],
            "reviewed_at": gov.get("reviewed_at"),
            "deployed_at": gov.get("deployed_at"),
        }
    # Try self-evolver
    try:
        data = self_evolver.get_proposal(proposal_id, include_content=True)
        return {
            "id": data.get("proposal_id", proposal_id),
            "uuid": data.get("proposal_id", proposal_id),
            "source": "self_evolver",
            "title": data.get("reason", "Untitled patch"),
            "description": data.get("reason", ""),
            "intent": data.get("reason", ""),
            "thought": "Self-evolver generated patch",
            "patch_type": data.get("scope", "code"),
            "patch_content": data.get("new_content", ""),
            "diff": data.get("diff", ""),
            "affected_files": [data.get("relative_path", "")] if data.get("relative_path") else [],
            "status": data.get("status", "unknown"),
            "proposed_by": data.get("proposed_by", "system"),
            "reviewed_by": data.get("approved_by"),
            "review_notes": data.get("approval_note", ""),
            "baseline_score": 0.0,
            "sandbox_score": 1.0 if data.get("status") == "validated" else 0.0,
            "improvement_delta": 0.0,
            "test_results": data.get("validation", []),
            "created_at": data.get("created_at", _utc()),
            "reviewed_at": data.get("approved_at"),
            "deployed_at": data.get("applied_at"),
        }
    except Exception:
        pass
    return None


def test_proposal(proposal_id: str) -> dict[str, Any]:
    """Run sandbox test on a proposal. Returns test results."""
    # Try self-evolver first
    try:
        result = self_evolver.validate_proposal(proposal_id)
        return {
            "ok": result.status == "validated",
            "status": result.status,
            "message": result.message,
            "details": result.details,
        }
    except Exception as exc:
        pass

    # Try governor proposals
    gov = gov_evolution.get_proposal(proposal_id)
    if gov:
        try:
            result = gov_evolution.run_sandbox_test(
                gov.get("patch_content", ""),
                gov.get("affected_files", []),
            )
            # Update the proposal with test results
            gov_evolution.review_proposal(proposal_id, 0, True, f"Auto-tested: {result}")
            return {
                "ok": result.get("ok", False),
                "status": "tested",
                "message": str(result.get("details", "")),
                "details": result,
            }
        except Exception as exc:
            return {"ok": False, "status": "error", "message": str(exc)}

    return {"ok": False, "status": "not_found", "message": "Proposal not found in either store"}


def accept_proposal(proposal_id: str, reviewer: str = "user", notes: str = "") -> dict[str, Any]:
    """Accept/approve a proposal."""
    # Try governor first
    gov = gov_evolution.get_proposal(proposal_id)
    if gov:
        result = gov_evolution.review_proposal(proposal_id, 1, True, notes)
        return {"ok": True, "status": "approved", "source": "governor", **result}

    # Try self-evolver
    try:
        result = self_evolver.approve_proposal(proposal_id, approved_by=reviewer, note=notes)
        return {"ok": result.status == "approved", "status": result.status, "message": result.message, "source": "self_evolver"}
    except Exception as exc:
        return {"ok": False, "status": "error", "message": str(exc)}


def reject_proposal(proposal_id: str, reviewer: str = "user", notes: str = "") -> dict[str, Any]:
    """Reject a proposal."""
    # Try governor first
    gov = gov_evolution.get_proposal(proposal_id)
    if gov:
        result = gov_evolution.review_proposal(proposal_id, 1, False, notes)
        return {"ok": True, "status": "rejected", "source": "governor", **result}

    # Try self-evolver - self_evolver doesn't have a direct reject, so we just note it
    try:
        data = self_evolver._load_proposal(proposal_id)
        data["status"] = "rejected"
        data["rejected_by"] = reviewer
        data["rejection_note"] = notes
        data["rejected_at"] = _utc()
        self_evolver._save_proposal(data)
        return {"ok": True, "status": "rejected", "source": "self_evolver"}
    except Exception as exc:
        return {"ok": False, "status": "error", "message": str(exc)}


def apply_proposal(proposal_id: str, approved_by: str = "user", approval_phrase: str = "") -> dict[str, Any]:
    """Apply a proposal to live code."""
    from .config import settings
    # Try self-evolver first (it has the actual file-writing logic)
    try:
        result = self_evolver.apply_proposal(proposal_id, approved_by=approved_by, approval_phrase=approval_phrase if not settings.omnipotent_mode else "I_APPROVE_SHIMS_PATCH")
        if result.status == "applied":
            # Also mark governor proposal as deployed if it exists
            gov = gov_evolution.get_proposal(proposal_id)
            if gov:
                gov_evolution.deploy_proposal(proposal_id)
            return {"ok": True, "status": "applied", "message": result.message, "details": result.details, "source": "self_evolver"}
        return {"ok": False, "status": result.status, "message": result.message, "details": result.details}
    except Exception as exc:
        pass

    # Governor proposals don't have direct apply - they need to be converted to self-evolver proposals first
    gov = gov_evolution.get_proposal(proposal_id)
    if gov and gov.get("patch_content"):
        # Create a self-evolver proposal from the governor proposal
        files = gov.get("affected_files", [])
        if files:
            se_prop = self_evolver.propose_patch(
                files[0],
                gov["patch_content"],
                reason=gov["title"] + ": " + (gov.get("description", "")),
                proposed_by="neural_agent",
            )
            if se_prop.get("ok"):
                # Validate and apply
                val = self_evolver.validate_proposal(se_prop["proposal_id"])
                if val.status == "validated":
                    apr = self_evolver.approve_proposal(se_prop["proposal_id"], approved_by=approved_by, note="Approved via Neural Agent")
                    if apr.status == "approved":
                        app = self_evolver.apply_proposal(se_prop["proposal_id"], approved_by=approved_by, approval_phrase=approval_phrase if not settings.omnipotent_mode else "I_APPROVE_SHIMS_PATCH")
                        if app.status == "applied":
                            gov_evolution.deploy_proposal(proposal_id)
                            return {"ok": True, "status": "applied", "message": app.message, "new_proposal_id": se_prop["proposal_id"]}
                return {"ok": False, "status": val.status, "message": val.message}
        return {"ok": False, "status": "no_files", "message": "Governor proposal has no affected files to apply"}

    return {"ok": False, "status": "not_found", "message": "Proposal not found"}


def generate_proposal(intent: str, file_path: str = "", instructions: str = "") -> dict[str, Any]:
    """Generate a new patch proposal using the local self-evolution model.

    The model analyzes the intent, reads the current file, and proposes a change.
    Returns a proposal ready for sandbox testing.
    """
    from .ai import ask_ai
    from .agent_tools import _run_sync
    from .coder import _prefer_coder_model, _parse_spec

    model = settings.self_evolution_model or _prefer_coder_model("ollama", None)

    # If no file path given, ask the model to suggest one
    if not file_path:
        system = "You are SHIMS Neural Agent. Return STRICT JSON: {\"file_path\": \"relative/path.py\", \"reasoning\": \"why this file\"}."
        prompt = f"INTENT: {intent}\n\nWhich file in the SHIMS codebase should be modified? Suggest a relative path."
        try:
            result = _run_sync(ask_ai(prompt, system=system, provider="ollama", model=model))
            spec = _parse_spec(result.text)
            file_path = spec.get("file_path", "")
        except Exception:
            file_path = "shared/neural_governor/governor.py"

    # Read current file content
    target = Path(ROOT_DIR) / file_path
    current = ""
    if target.exists():
        try:
            current = target.read_text(encoding="utf-8", errors="replace")[:24000]
        except Exception:
            pass

    # Generate the patch
    system = (
        "You are SHIMS modifying its own source. Return STRICT JSON only: "
        '{"intent": "what this change achieves", "thought": "reasoning behind the change", '
        '"files": {"' + file_path + '": "FULL NEW FILE CONTENT"}}. '
        "Return the COMPLETE file, preserving everything that should stay. No prose outside JSON."
    )
    prompt = f"INTENT: {intent}\n\nFILE: {file_path}\n\nCURRENT CONTENT:\n```\n{current}\n```\n\nINSTRUCTIONS: {instructions}\n"

    try:
        result = _run_sync(ask_ai(prompt, system=system, provider="ollama", model=model))
        spec = _parse_spec(result.text)
        files = spec.get("files", {})
        intent_text = spec.get("intent", intent)
        thought_text = spec.get("thought", "Generated by Neural Agent")

        for fp, content in files.items():
            if isinstance(content, str) and content.strip():
                # Create self-evolver proposal
                se_prop = self_evolver.propose_patch(
                    fp,
                    content,
                    reason=intent_text,
                    proposed_by="neural_agent",
                )
                if se_prop.get("ok"):
                    # Also create a governor proposal for the unified view
                    gov_prop = gov_evolution.propose_patch(
                        title=intent_text,
                        description=thought_text,
                        patch_type="code",
                        patch_content=content,
                        affected_files=[fp],
                    )
                    return {
                        "ok": True,
                        "proposal_id": se_prop["proposal_id"],
                        "governor_uuid": gov_prop.get("proposal_uuid"),
                        "title": intent_text,
                        "intent": intent_text,
                        "thought": thought_text,
                        "file_path": fp,
                        "diff": se_prop.get("diff", ""),
                        "model_used": model,
                    }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": False, "error": "Model did not return valid patch content"}


def run_reflection() -> dict[str, Any]:
    """Trigger a reflection cycle: analyze codebase gaps and generate proposals."""
    from .self_awareness import build_self_model, _derive_gaps

    try:
        model = build_self_model()
        gaps = model.get("gaps", [])

        proposals_generated = 0
        for gap in gaps[:3]:  # Top 3 gaps
            gap_text = str(gap)
            result = generate_proposal(
                intent=f"Address gap: {gap_text[:120]}",
                instructions=f"Improve this area of the codebase: {gap_text}",
            )
            if result.get("ok"):
                proposals_generated += 1

        return {
            "ok": True,
            "gaps_found": len(gaps),
            "proposals_generated": proposals_generated,
            "gaps": [{"name": str(g)[:80], "suggestion": str(g)[:200]} for g in gaps[:5]],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()
