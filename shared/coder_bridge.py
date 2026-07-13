"""Coder Bridge — fold a background Coder project back into the SHIMS main tree.

Background Coder projects live in ``storage/coder/<project_id>/``. This module
proposes patches so their files can migrate into ``shared/``, ``backend/``,
``frontend/``, etc. via the same sandbox-validate → approve → apply pipeline as
``self.patch``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .coder_v2 import _project_path
from .config import ROOT_DIR
from .self_evolver import create_proposal, validate_proposal, apply_proposal


ALLOWED_TARGET_ROOTS = {"shared", "backend", "frontend", "apps", "shims_enterprise", "shims_omni", "shims_personal", "tests", "docs", "scripts"}
META_FILE = "_project.json"


def _safe_target_path(target_dir: str, rel_path: str) -> Path:
    """Resolve a relative path under a target directory inside the repo."""
    target_dir = target_dir.strip("/\\")
    if not target_dir or target_dir.split("/")[0].split("\\")[0] not in ALLOWED_TARGET_ROOTS:
        raise ValueError(f"target_dir root not allowed: {target_dir}")
    if ".." in Path(rel_path).parts:
        raise ValueError(f"relative path contains ..: {rel_path}")
    return (ROOT_DIR / target_dir / rel_path).resolve()


def list_project_files(project_id: str) -> list[dict[str, Any]]:
    """List all files in a coder project, excluding the metadata file."""
    proj_dir = _project_path(project_id)
    if not proj_dir.exists():
        return []
    files: list[dict[str, Any]] = []
    for p in sorted(proj_dir.rglob("*")):
        if p.is_file() and p.name != META_FILE:
            rel = str(p.relative_to(proj_dir)).replace("\\", "/")
            files.append({"rel": rel, "size": p.stat().st_size})
    return files


def read_project_file(project_id: str, rel_path: str) -> str:
    """Read a single file from a coder project."""
    proj_dir = _project_path(project_id)
    target = (proj_dir / rel_path).resolve()
    if not str(target).startswith(str(proj_dir.resolve())):
        raise ValueError("path escapes project directory")
    return target.read_text(encoding="utf-8", errors="replace")


def fold_project(project_id: str, target_dir: str, *, auto_apply: bool = False,
                 approved_by: str = "coder-bridge") -> dict[str, Any]:
    """Fold all files from a Coder project into the main SHIMS tree as proposals.

    Args:
        project_id: Coder project id.
        target_dir: Target directory inside the SHIMS repo (e.g. ``shared/generated_skills``).
        auto_apply: If True and omnipotent mode is on, apply proposals immediately.
        approved_by: Name recorded as approver when auto-applying.
    """
    from .config import settings

    proj_dir = _project_path(project_id)
    if not proj_dir.exists():
        return {"ok": False, "error": f"project not found: {project_id}"}

    files = list_project_files(project_id)
    if not files:
        return {"ok": False, "error": "project is empty"}

    proposals: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for f in files:
        rel = f["rel"]
        try:
            content = read_project_file(project_id, rel)
            stripped = target_dir.rstrip('/\\')
            dest_rel = (stripped + "/" + rel).replace("\\", "/")
            dest = _safe_target_path(target_dir, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)

            proposal = create_proposal(
                dest_rel,
                content,
                reason=f"Folded from Coder project {project_id}",
                author="coder-bridge",
                scope="code",
            )
            if not proposal.get("ok"):
                errors.append({"file": rel, "error": proposal.get("message", "blocked")})
                continue

            pid = proposal["proposal_id"]
            validation = validate_proposal(pid)
            if validation.status != "validated":
                errors.append({"file": rel, "error": validation.message, "proposal_id": pid})
                continue

            proposals.append({"file": rel, "proposal_id": pid, "dest": dest_rel})

            if auto_apply and settings.omnipotent_mode:
                result = apply_proposal(pid, approved_by=approved_by, approval_phrase="I_APPROVE_SHIMS_PATCH")
                applied.append({"file": rel, "proposal_id": pid, "status": result.status})
        except Exception as exc:
            errors.append({"file": rel, "error": str(exc)[:200]})

    return {
        "ok": len(errors) == 0 or len(proposals) > 0,
        "project_id": project_id,
        "target_dir": target_dir,
        "proposals": proposals,
        "applied": applied,
        "errors": errors,
        "auto_apply": auto_apply and settings.omnipotent_mode,
    }
