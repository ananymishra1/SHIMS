"""Omni Builder — let SHIMS Omni develop the codebase at scale via its own
Anthropic key, safely.

The user wants Omni (not Claude Code) to do the heavy Enterprise deep-build,
paying with the Anthropic key configured in `.env`. This module is the engine:

    detailed task prompt
        → ask_ai(provider='anthropic')           (high-quality code gen)
        → parse {files: {path: full_content}}
        → self_evolver.propose_patch / validate   (sandbox py_compile gate)
        → self_evolver.apply_proposal             (backup + live re-validate + rollback)
        → git commit                              (per successful build)

Every safety property of `shared/self_evolver.py` is preserved: allowed-roots,
immutable harness files, compile gating, automatic rollback, per-file backups.
Nothing is applied unless it compiles. Git gives a clean revert if a build
turns out wrong.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .ai import ask_ai, AnthropicProvider, AIResult
from .config import ROOT_DIR
from . import self_evolver as se

_BUILDER_SYSTEM = (
    "You are SHIMS Builder, a senior engineer working INSIDE the SHIMS pharma-OS "
    "monorepo (FastAPI + Jinja + a shared/ engine). You are given a task, the FULL "
    "current contents of the files you may change, and read-only context files.\n\n"
    "Rules:\n"
    "1. Respond with STRICT JSON only — no prose, no markdown fences:\n"
    '   {"explanation": "...", "files": {"relative/path.py": "FULL NEW FILE CONTENT", ...}}\n'
    "2. Return COMPLETE file contents (never diffs/snippets). Only include files you actually change.\n"
    "3. Match the existing code style, imports and patterns exactly. Reuse existing helpers.\n"
    "4. Every Python file must compile (no placeholders, no '...'). Keep changes minimal and correct.\n"
    "5. Never touch shared/self_evolver.py, shared/security.py, shared/config.py.\n"
    "6. Prefer adding routers/modules over rewriting large monoliths."
)

# Roots a build may read for grounding without blowing the prompt budget.
_MAX_FILE_CHARS = 18000


def _read(path: str) -> str:
    p = (ROOT_DIR / path).resolve()
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _parse_spec(text: str) -> dict[str, Any]:
    """Robustly extract {explanation, files{}} from the model output."""
    text = (text or "").strip()
    # direct JSON
    try:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            spec = json.loads(text[start:end + 1])
            if isinstance(spec, dict) and isinstance(spec.get("files"), dict):
                return spec
    except Exception:
        pass
    # ```json fenced
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        try:
            spec = json.loads(m.group(1))
            if isinstance(spec, dict) and isinstance(spec.get("files"), dict):
                return spec
        except Exception:
            pass
    return {"explanation": text[:400], "files": {}}


async def _ask_anthropic_direct(prompt: str, system: str, model: str | None = None, max_retries: int = 3) -> AIResult:
    """Call Anthropic directly, bypassing the LLM gateway and its provider fallbacks.

    Retries on rate-limit (429) / server errors with exponential backoff so the
    Builder survives Anthropic's request-throttling during multi-step builds.
    """
    provider = AnthropicProvider()
    last_error = ""
    for attempt in range(max_retries):
        result = await provider.complete(prompt=prompt, system=system, model=model)
        if result.ok:
            return result
        err = str(getattr(result, 'error', '') or result.text or '')
        last_error = err
        if '429' in err or 'rate' in err.lower() or 'too many requests' in err.lower():
            wait = 2 ** attempt + 1
            await asyncio.sleep(wait)
            continue
        # Non-retryable auth/key error — stop immediately.
        break
    return AIResult(text=last_error, provider='anthropic', model=model or '', ok=False, error=last_error)


def _git_commit(message: str) -> dict[str, Any]:
    try:
        if not (ROOT_DIR / ".git").exists():
            return {"committed": False, "reason": "no git repo"}
        subprocess.run(["git", "add", "-A"], cwd=str(ROOT_DIR), capture_output=True, text=True, timeout=60)
        proc = subprocess.run(["git", "commit", "-q", "-m", message], cwd=str(ROOT_DIR),
                              capture_output=True, text=True, timeout=60)
        return {"committed": proc.returncode == 0, "stderr": proc.stderr[-300:]}
    except Exception as exc:
        return {"committed": False, "reason": str(exc)[:200]}


async def build_task(
    instruction: str,
    *,
    targets: list[str] | None = None,
    context: list[str] | None = None,
    provider: str = "anthropic",
    model: str | None = None,
    apply: bool = False,
    approved_by: str = "omni-builder",
    commit: bool = True,
) -> dict[str, Any]:
    """Run one Anthropic-driven build step.

    targets:  relative paths the model may create/rewrite (current contents are shown).
    context:  read-only relative paths shown for grounding (not editable).
    apply:    if True, compiled-clean files are applied to the live repo + committed.
    """
    targets = targets or []
    context = context or []
    if not instruction.strip():
        return {"ok": False, "error": "empty instruction"}

    prompt_parts = [f"TASK:\n{instruction}\n"]
    if targets:
        prompt_parts.append("FILES YOU MAY CHANGE (full current contents; '' means new file):")
        for t in targets:
            body = _read(t)[:_MAX_FILE_CHARS]
            prompt_parts.append(f"\n### TARGET: {t}\n```\n{body}\n```")
    if context:
        prompt_parts.append("\nREAD-ONLY CONTEXT (do not rewrite these; for reference):")
        for c in context:
            body = _read(c)[:_MAX_FILE_CHARS]
            prompt_parts.append(f"\n### CONTEXT: {c}\n```\n{body}\n```")
    prompt_parts.append("\nReturn the STRICT JSON object now.")
    prompt = "\n".join(prompt_parts)

    try:
        if provider == "anthropic":
            result = await _ask_anthropic_direct(prompt, _BUILDER_SYSTEM, model=model)
        else:
            result = await ask_ai(prompt, system=_BUILDER_SYSTEM, provider=provider, model=model)
    except Exception as exc:
        return {"ok": False, "error": f"LLM call failed: {exc}", "provider": provider}

    spec = _parse_spec(result.text)
    files = spec.get("files") or {}
    if not files:
        return {"ok": False, "error": "model returned no files", "raw": (result.text or "")[:500], "provider": result.provider}

    report: list[dict[str, Any]] = []
    applied_any = False
    for path, content in files.items():
        if not isinstance(content, str):
            report.append({"path": path, "status": "skipped_non_string"})
            continue
        prop = se.propose_patch(path, content, reason=instruction[:140], scope="code", proposed_by="omni-builder")
        if not prop.get("ok"):
            report.append({"path": path, "status": "blocked", "reason": prop.get("reason_code") or prop.get("message")})
            continue
        pid = prop["proposal_id"]
        val = se.validate_proposal(pid)
        item = {"path": path, "status": val.status, "proposal_id": pid}
        if apply and val.status == "validated":
            ap = se.apply_proposal(pid, approved_by=approved_by, approval_phrase="I_APPROVE_SHIMS_PATCH")
            item["apply_status"] = ap.status
            applied_any = applied_any or ap.status == "applied"
        elif val.status != "validated":
            item["validation"] = (val.details or {}).get("validation", [])[-1:]
        report.append(item)

    commit_info = None
    if apply and applied_any and commit:
        commit_info = _git_commit(f"omni-builder: {instruction[:80]}")

    return {
        "ok": True,
        "summary": spec.get("explanation", "")[:600],
        "provider": result.provider,
        "files": report,
        "applied": applied_any,
        "commit": commit_info,
        "note": "Files are sandbox-compiled before apply; failures auto-rollback. Use apply=false to preview.",
    }
