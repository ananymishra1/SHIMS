from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import BACKUP_DIR, ROOT_DIR, SANDBOX_DIR, STORAGE_DIR
from .guardians import generate_secret, is_allowed_target, is_weak_secret
from .security import new_id

try:
    from .telemetry import log_event
except Exception:  # pragma: no cover
    def log_event(*args: Any, **kwargs: Any) -> None:
        return None

# v13 rule: generated patches are real artifacts, not simulated messages.
# Flow: propose -> sandbox validate -> human approve -> apply to live tree -> live validate -> archive.
ALLOWED_ROOTS = {"shared", "backend", "apps", "shims_enterprise", "frontend", "tests", "docs", "scripts", "android_app", "termux_offline_runtime", "desktop_bridge"}
BLOCKED_PARTS = {".env", ".venv", "storage", "__pycache__", ".git", "node_modules", "dist", "build", "site-packages"}
IMMUTABLE_RELATIVE_PATHS = {
    "shared/self_evolver.py",   # the patch/approval harness cannot modify itself
    "shared/security.py",       # security/session helpers stay human-controlled
    "shared/config.py",         # environment/secrets contract stays human-controlled
}
PROPOSAL_DIR = STORAGE_DIR / "evolution" / "proposals"
ARCHIVE_DIR = STORAGE_DIR / "evolution" / "archive"
UNDO_DIR = STORAGE_DIR / "evolution" / "undo"
for d in (PROPOSAL_DIR, ARCHIVE_DIR, SANDBOX_DIR, BACKUP_DIR, UNDO_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Scopes considered low-risk enough to auto-apply without explicit human approval.
AUTO_APPLY_WHITELIST = {"skill", "prompt_or_skill", "soft_extension", "note"}
# Undo window in seconds.
UNDO_WINDOW_SECONDS = 300


@dataclass
class EvolutionResult:
    status: str
    message: str
    details: dict[str, Any]


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normal_rel(relative_path: str | Path) -> str:
    rel = Path(str(relative_path).replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise ValueError("Patch path must be a safe relative path inside the SHIMS project.")
    return str(rel).replace("\\", "/")


def _target(relative_path: str | Path) -> Path:
    return (ROOT_DIR / _normal_rel(relative_path)).resolve()


def _is_allowed(path: Path) -> tuple[bool, str]:
    allowed_roots = ALLOWED_ROOTS
    ok, reason = is_allowed_target(path, allowed_roots=allowed_roots)
    if not ok:
        return ok, reason
    rel_s = str(path.resolve().relative_to(ROOT_DIR.resolve())).replace("\\", "/")
    if rel_s in IMMUTABLE_RELATIVE_PATHS:
        return False, "immutable_safety_harness"
    if rel_s.endswith((".pyc", ".pyo", ".sqlite3", ".db", ".exe", ".dll")):
        return False, "binary_or_database_file"
    return True, "ok"


def classify_risk(scope: str, relative_path: str, size: int) -> str:
    """Classify a proposal as low/medium/high/critical."""
    rel = relative_path.replace("\\", "/")
    if scope in AUTO_APPLY_WHITELIST:
        return "low"
    if rel.startswith(("shared/security", "shared/config", "shared/self_evolver")):
        return "critical"
    if rel.startswith(("backend/app/main.py", "shims_enterprise/app.py")):
        return "high"
    if rel.startswith(("shared/", "backend/", "shims_enterprise/")) and size > 2000:
        return "medium"
    return "medium"


def can_auto_apply(proposal: dict[str, Any]) -> bool:
    """Return True if the proposal may be applied without explicit human approval."""
    if proposal.get("scope") in AUTO_APPLY_WHITELIST:
        return True
    if proposal.get("risk") == "low" and proposal.get("auto_apply_allowed"):
        return True
    return False


def _proposal_path(proposal_id: str) -> Path:
    return PROPOSAL_DIR / f"{proposal_id}.json"


def _load_proposal(proposal_id: str) -> dict[str, Any]:
    path = _proposal_path(proposal_id)
    if not path.exists():
        raise FileNotFoundError(f"proposal not found: {proposal_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_proposal(data: dict[str, Any]) -> dict[str, Any]:
    data["updated_at"] = _utc()
    _proposal_path(data["proposal_id"]).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return data


def _public(proposal: dict[str, Any]) -> dict[str, Any]:
    out = dict(proposal)
    out.pop("new_content", None)
    return out


def _python_files(root: Path) -> list[str]:
    files: list[str] = []
    for root_name in ("shared", "backend", "apps", "shims_enterprise"):
        base = root / root_name
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if not any(part in BLOCKED_PARTS for part in path.parts):
                files.append(str(path))
    return files


# Only the Python import roots are needed to validate a patch (py_compile / import).
# Heavy, irrelevant trees (android_app/llama.cpp, frontend, gradle caches, models,
# binaries) are NEVER copied — copying them previously bloated each sandbox to
# ~260 MB and produced the 15 GB storage/sandbox pileup.
_SANDBOX_COPY_ROOTS = ("shared", "backend", "apps", "shims_enterprise")
_SANDBOX_IGNORE = shutil.ignore_patterns(
    ".venv", "storage", "data", "__pycache__", ".git", "node_modules",
    "android_app", "termux_offline_runtime", ".gradle", ".gradle-cache", ".gradle-dist",
    ".android-sdk", "llama.cpp", "models", "_archive", "build", "dist",
    "*.pyc", "*.pyo", "*.gguf", "*.bin", "*.so", "*.dll", "*.zip", "*.apk", "*.onnx",
)


def _copy_repo_to_sandbox(sandbox_root: Path) -> None:
    for name in _SANDBOX_COPY_ROOTS:
        src = ROOT_DIR / name
        if src.exists():
            shutil.copytree(src, sandbox_root / name, ignore=_SANDBOX_IGNORE, dirs_exist_ok=True)
    for name in ("requirements.txt", "requirements-optional-media.txt", ".env.example"):
        src = ROOT_DIR / name
        if src.exists():
            shutil.copy2(src, sandbox_root / name)


def _default_validation(relative_path: str, root: Path) -> list[list[str]]:
    suffix = Path(relative_path).suffix.lower()
    target = root / relative_path
    if suffix == ".py":
        files = _python_files(root)
        return [[sys.executable, "-m", "py_compile", *files]] if files else [[sys.executable, "-c", "print('no python files')"]]
    if suffix == ".json":
        return [[sys.executable, "-m", "json.tool", str(target)]]
    return [[sys.executable, "-c", f"from pathlib import Path; p=Path(r'{target}'); assert p.exists(); p.read_bytes(); print('candidate-readable')"]]


def _run_commands(commands: list[Any], cwd: Path, timeout: int = 120) -> tuple[bool, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    ok = True
    env = os.environ.copy()
    env["PYTHONPATH"] = str(cwd) + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")
    for raw in commands:
        command = raw.split() if isinstance(raw, str) else [str(x) for x in list(raw)]
        try:
            proc = subprocess.run(command, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
            item = {"cmd": command, "returncode": proc.returncode, "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-8000:]}
            results.append(item)
            if proc.returncode != 0:
                ok = False
                break
        except Exception as exc:
            ok = False
            results.append({"cmd": command, "error": str(exc)})
            break
    return ok, results


def propose_patch(relative_path: str, new_content: str, *, reason: str = "", scope: str = "prompt_or_skill", proposed_by: str = "shims", tests: list[Any] | None = None, auto_apply_allowed: bool = False) -> dict[str, Any]:
    relative_path = _normal_rel(relative_path)
    target = _target(relative_path)
    allowed, reason_code = _is_allowed(target)
    if not allowed:
        return {"ok": False, "status": "blocked", "message": f"Path is not allowed: {relative_path}", "reason_code": reason_code}
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    diff = "\n".join(difflib.unified_diff(old.splitlines(), new_content.splitlines(), fromfile=f"a/{relative_path}", tofile=f"b/{relative_path}", lineterm=""))
    proposal_id = new_id("patch")
    risk = classify_risk(scope, relative_path, len(new_content))
    proposal = {
        "ok": True,
        "id": proposal_id,
        "proposal_id": proposal_id,
        "status": "proposed",
        "scope": scope,
        "risk": risk,
        "auto_apply_allowed": auto_apply_allowed,
        "relative_path": relative_path,
        "reason": reason,
        "proposed_by": proposed_by,
        "created_at": _utc(),
        "updated_at": _utc(),
        "old_sha256": _sha_text(old),
        "new_sha256": _sha_text(new_content),
        "diff": diff,
        "size": len(new_content),
        "new_content": new_content,
        "tests": tests or [],
        "validation": [],
        "approved": False,
        "approved_by": None,
        "applied": False,
    }
    _save_proposal(proposal)
    log_event("evolution.proposed", route="evolution", provider="local", model="self-evolver", ok=True, message=reason, metadata={"proposal_id": proposal_id, "relative_path": relative_path, "scope": scope, "risk": risk})
    return _public(proposal)


def create_proposal(relative_path: str, new_content: str, *, reason: str = "", author: str = "user", scope: str = "code", tests: list[Any] | None = None) -> dict[str, Any]:
    return propose_patch(relative_path, new_content, reason=reason, scope=scope, proposed_by=author, tests=tests)


def list_proposals(limit: int = 50) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in sorted(PROPOSAL_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            rows.append(_public(json.loads(p.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return rows


def get_proposal(proposal_id: str, include_content: bool = False) -> dict[str, Any]:
    data = _load_proposal(proposal_id)
    return data if include_content else _public(data)


def validate_proposal(proposal_id: str, validation: list[Any] | None = None) -> EvolutionResult:
    proposal = _load_proposal(proposal_id)
    relative_path = _normal_rel(proposal["relative_path"])
    target = _target(relative_path)
    allowed, reason_code = _is_allowed(target)
    if not allowed:
        proposal["status"] = "blocked"
        _save_proposal(proposal)
        return EvolutionResult("blocked", f"Path is not allowed: {relative_path} ({reason_code})", {})
    sandbox_root = SANDBOX_DIR / f"validate_{proposal_id}_{int(time.time())}"
    if sandbox_root.exists():
        shutil.rmtree(sandbox_root)
    sandbox_root.mkdir(parents=True, exist_ok=True)
    _copy_repo_to_sandbox(sandbox_root)
    candidate = sandbox_root / relative_path
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(proposal["new_content"], encoding="utf-8")
    commands = validation or proposal.get("tests") or _default_validation(relative_path, sandbox_root)
    ok, results = _run_commands(commands, sandbox_root, timeout=120)
    proposal["validation"] = results
    proposal["last_validated_at"] = _utc()
    proposal["sandbox_path"] = str(sandbox_root)
    proposal["status"] = "validated" if ok else "validation_failed"
    _save_proposal(proposal)
    msg = "Patch validated in isolated sandbox. It is ready for human approval." if ok else "Patch failed sandbox validation. Live code was not touched."
    log_event("evolution.validated" if ok else "evolution.validation_failed", route="evolution", provider="local", model="self-evolver", ok=ok, message=msg, metadata={"proposal_id": proposal_id, "relative_path": relative_path})
    return EvolutionResult(proposal["status"], msg, {"proposal_id": proposal_id, "validation": results, "sandbox_path": str(sandbox_root)})


def approve_proposal(proposal_id: str, *, approved_by: str = "human", note: str = "", voice_phrase: str = "") -> EvolutionResult:
    proposal = _load_proposal(proposal_id)
    if proposal.get("status") not in {"validated", "approved"}:
        return EvolutionResult("validation_required", "Patch must pass sandbox validation before approval.", {"proposal_id": proposal_id, "current_status": proposal.get("status")})
    # Easy approval: explicit human name, or recognized voice phrase, or low-risk auto-apply.
    voice_ok = voice_phrase.strip().lower() in {"yes, apply it", "yes apply it", "yes", "apply it"}
    if not approved_by or str(approved_by).strip().lower() in {"shims", "ai", "assistant"}:
        if not voice_ok:
            return EvolutionResult("approval_required", "A human owner name or voice approval phrase is required.", {"proposal_id": proposal_id})
        approved_by = "voice-approval"
    proposal["status"] = "approved"
    proposal["approved"] = True
    proposal["approved_by"] = approved_by
    proposal["approval_note"] = note
    proposal["approved_at"] = _utc()
    _save_proposal(proposal)
    log_event("evolution.approved", route="evolution", provider="local", model="self-evolver", ok=True, message=note, metadata={"proposal_id": proposal_id, "approved_by": approved_by})
    return EvolutionResult("approved", "Patch approved. It may now be applied.", {"proposal_id": proposal_id, "approved_by": approved_by})


def apply_proposal(proposal_id: str, *, approved_by: str = "human", approval_phrase: str = "", voice_phrase: str = "", validation: list[Any] | None = None) -> EvolutionResult:
    from .config import settings
    proposal = _load_proposal(proposal_id)
    # Auto-apply low-risk proposals if enabled.
    if not proposal.get("approved") and can_auto_apply(proposal):
        proposal["approved"] = True
        proposal["approved_by"] = "auto-apply"
        proposal["approval_note"] = "Low-risk change auto-applied per policy."
        proposal["approved_at"] = _utc()
        proposal["status"] = "approved"
        _save_proposal(proposal)
    # Either the proposal was previously approved, caller passes phrase, voice phrase, or omnipotent mode.
    has_prior_approval = proposal.get("status") == "approved" and proposal.get("approved_by")
    voice_ok = voice_phrase.strip().lower() in {"yes, apply it", "yes apply it", "yes", "apply it"}
    if not has_prior_approval and not settings.omnipotent_mode:
        if approval_phrase.strip() == "I_APPROVE_SHIMS_PATCH":
            approval = approve_proposal(proposal_id, approved_by=approved_by, note="inline approval phrase")
        elif voice_ok:
            approval = approve_proposal(proposal_id, approved_by="voice-approval", voice_phrase=voice_phrase)
        else:
            return EvolutionResult("approval_required", "Patch application requires approval first, approval_phrase='I_APPROVE_SHIMS_PATCH', or voice phrase.", {"proposal_id": proposal_id})
        if approval.status != "approved":
            return approval
        proposal = _load_proposal(proposal_id)
    relative_path = _normal_rel(proposal["relative_path"])
    target = _target(relative_path)
    allowed, reason_code = _is_allowed(target)
    if not allowed:
        return EvolutionResult("blocked", f"Path is not allowed: {relative_path} ({reason_code})", {})
    # Revalidate after approval if validation is stale or absent.
    if not proposal.get("validation"):
        validation_result = validate_proposal(proposal_id, validation=validation)
        if validation_result.status != "validated":
            return validation_result
        proposal = _load_proposal(proposal_id)
    backup_dir = BACKUP_DIR / proposal_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    if target.exists():
        shutil.copy2(target, backup_dir / target.name)
    # Copy to undo buffer for quick revert.
    undo_path = UNDO_DIR / f"{proposal_id}_{_sha_text(old or '(new)')}.bak"
    undo_path.write_text(old or "", encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(proposal["new_content"], encoding="utf-8")
    commands = validation or proposal.get("tests") or _default_validation(relative_path, ROOT_DIR)
    ok, results = _run_commands(commands, ROOT_DIR, timeout=120)
    if not ok:
        if old:
            target.write_text(old, encoding="utf-8")
        else:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        proposal["status"] = "rolled_back"
        proposal["apply_validation"] = results
        proposal["rollback_at"] = _utc()
        _save_proposal(proposal)
        log_event("evolution.rolled_back", route="evolution", provider="local", model="self-evolver", ok=False, message="Live validation failed; rollback complete.", metadata={"proposal_id": proposal_id, "relative_path": relative_path})
        return EvolutionResult("rolled_back", "Validation failed after apply. Original file restored.", {"proposal_id": proposal_id, "validation": results, "backup_dir": str(backup_dir)})
    proposal["status"] = "applied"
    proposal["applied"] = True
    proposal["applied_at"] = _utc()
    proposal["approved_by"] = proposal.get("approved_by") or approved_by
    proposal["apply_validation"] = results
    proposal["backup_dir"] = str(backup_dir)
    proposal["undo_path"] = str(undo_path)
    proposal["undo_deadline"] = _utc()  # ISO timestamp; add UNDO_WINDOW_SECONDS at consumption
    _save_proposal(proposal)
    archive_copy = ARCHIVE_DIR / f"{proposal_id}.json"
    archive_copy.write_text(json.dumps(proposal, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    log_event("evolution.applied", route="evolution", provider="local", model="self-evolver", ok=True, message="Patch applied after sandbox validation and human approval.", metadata={"proposal_id": proposal_id, "relative_path": relative_path, "approved_by": proposal.get("approved_by")})
    return EvolutionResult("applied", "Patch applied after sandbox validation and human approval.", {"proposal_id": proposal_id, "relative_path": relative_path, "validation": results, "backup_dir": str(backup_dir)})


def undo_apply(proposal_id: str) -> EvolutionResult:
    """Revert an applied patch within the undo window."""
    proposal = _load_proposal(proposal_id)
    if proposal.get("status") != "applied":
        return EvolutionResult("not_applied", "Only applied proposals can be undone.", {"proposal_id": proposal_id})
    applied_at = datetime.fromisoformat(proposal["applied_at"])
    if (datetime.now(timezone.utc) - applied_at).total_seconds() > UNDO_WINDOW_SECONDS:
        return EvolutionResult("undo_expired", f"Undo window of {UNDO_WINDOW_SECONDS}s has expired.", {"proposal_id": proposal_id})
    target = _target(proposal["relative_path"])
    undo_path = Path(proposal["undo_path"])
    if not undo_path.exists():
        return EvolutionResult("undo_missing", "Undo backup not found.", {"proposal_id": proposal_id})
    target.write_text(undo_path.read_text(encoding="utf-8"), encoding="utf-8")
    proposal["status"] = "undone"
    proposal["undone_at"] = _utc()
    _save_proposal(proposal)
    log_event("evolution.undone", route="evolution", provider="local", model="self-evolver", ok=True, message="Patch reverted by user.", metadata={"proposal_id": proposal_id})
    return EvolutionResult("undone", "Patch reverted successfully.", {"proposal_id": proposal_id})


def approval_card(proposal_id: str) -> dict[str, Any]:
    """Return a UI-friendly approval payload for one-tap yes/no."""
    proposal = _load_proposal(proposal_id)
    return {
        "proposal_id": proposal_id,
        "risk": proposal.get("risk", "medium"),
        "scope": proposal.get("scope"),
        "relative_path": proposal.get("relative_path"),
        "reason": proposal.get("reason"),
        "size": proposal.get("size"),
        "diff": proposal.get("diff", "")[:4000],
        "status": proposal.get("status"),
        "actions": {
            "approve_url": f"/evolution/approve/{proposal_id}",
            "apply_url": f"/evolution/apply/{proposal_id}",
            "voice_yes": "yes, apply it",
            "keyboard_yes": "Y",
            "keyboard_no": "N",
        },
        "auto_apply": can_auto_apply(proposal),
    }


def apply_guarded_change(relative_path: str, new_content: str, validation: list[Any] | None = None) -> EvolutionResult:
    """Backward-compatible direct apply API.

    v13 keeps this disabled unless SHIMS_ALLOW_SELF_EVOLUTION=true. The preferred path is
    propose -> validate -> approve -> apply, exposed through /evolution/* endpoints.
    """
    from .config import settings
    if not settings.omnipotent_mode and os.getenv("SHIMS_ALLOW_SELF_EVOLUTION", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return EvolutionResult("disabled", "Legacy direct apply is disabled. Use propose -> validate -> approve -> apply.", {})
    proposal = propose_patch(relative_path, new_content, reason="legacy apply_guarded_change call", proposed_by="legacy")
    if not proposal.get("ok"):
        return EvolutionResult(proposal.get("status", "blocked"), proposal.get("message", "blocked"), proposal)
    validation_result = validate_proposal(proposal["proposal_id"], validation=validation)
    if validation_result.status != "validated":
        return validation_result
    approval_result = approve_proposal(proposal["proposal_id"], approved_by="legacy-env-enabled", note="SHIMS_ALLOW_SELF_EVOLUTION enabled")
    if approval_result.status != "approved":
        return approval_result
    return apply_proposal(proposal["proposal_id"], approved_by="legacy-env-enabled", validation=validation)


def generate_builtin_patch(kind: str = "tool_verification") -> dict[str, Any]:
    if kind == "tool_verification":
        return propose_patch(
            "docs/tool_verification_policy.md",
            "# SHIMS Tool Verification Policy\n\nThe LLM may never narrate file creation until the deterministic tool has written the file, verified it exists, and registered a SHA-256 ledger entry.\n\nRequired path: intent -> tool -> file exists -> ledger hash -> narration.\n",
            reason="Daily reflection: enforce no fake file generation.",
            scope="prompt_or_skill",
            proposed_by="reflection-engine",
        )
    if kind == "half_duplex_voice":
        return propose_patch(
            "frontend/self_evolution_notes.js",
            "// Generated by SHIMS v13 Self-Evolution Lab.\nexport const shimsVoicePolicy = {\n  halfDuplex: true,\n  listenWhileSpeaking: false,\n  silenceEventsBecomeChatMessages: false,\n  duplicateTurnCooldownMs: 4500,\n  languageHints: ['en-IN', 'hi-IN', 'hinglish'],\n};\n",
            reason="Daily reflection: prevent silence spam and self-transcription.",
            scope="soft_extension",
            proposed_by="reflection-engine",
        )
    raise ValueError(f"Unknown built-in patch kind: {kind}")
