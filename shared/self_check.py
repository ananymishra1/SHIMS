"""SHIMS real self-check: inspect code, run tests/lint, and propose patches.

This module performs actual analysis of the SHIMS source tree and creates a
guarded patch proposal. It never applies patches on its own.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from . import self_evolver
from .ai import ask_ai, extract_json_maybe
from .config import ROOT_DIR


def _extract_path_from_compile_error(error_text: str) -> str | None:
    for line in error_text.splitlines():
        for match in re.finditer(r"[\w/\\.-]+\.(py|js|json|html|css|md)", line):
            candidate = match.group(0).replace("\\", "/")
            if "/" in candidate:
                return candidate
    return None


def _guess_source_from_test_error(error_text: str, test_path: str) -> str | None:
    counts: dict[str, int] = {}
    for match in re.finditer(r"([\w/\\.-]+)\.(py|js|json|html|css|md)", error_text):
        candidate = match.group(1) + "." + match.group(2)
        candidate = candidate.replace("\\", "/")
        if candidate.startswith(("tests/", "test_")):
            continue
        if candidate.endswith(".py"):
            counts[candidate] = counts.get(candidate, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return None


def _collect_python_files() -> list[str]:
    files: list[str] = []
    for root_name in self_evolver.ALLOWED_ROOTS:
        base = ROOT_DIR / root_name
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if any(part in self_evolver.BLOCKED_PARTS for part in path.parts):
                continue
            files.append(str(path.relative_to(ROOT_DIR)).replace("\\", "/"))
    return files


async def run_self_check(
    scope: str = "tests",
    relative_path: str | None = None,
    goal: str | None = None,
    test_path: str | None = None,
) -> dict[str, Any]:
    """Inspect SHIMS code and return a validated patch proposal without applying it."""
    scope = (scope or "tests").strip().lower()
    findings: list[dict[str, Any]] = []
    target_path: str | None = None
    old_content = ""
    error_context = ""
    test_commands: list[list[str]] = []

    if scope == "lint":
        py_files = _collect_python_files()
        if not py_files:
            return {"ok": False, "scope": scope, "findings": findings, "error": "No Python files found to lint."}
        cmd: list[str] = [sys.executable, "-m", "py_compile"] + py_files[:200]
        ok, results = self_evolver._run_commands([cmd], ROOT_DIR, timeout=120)
        findings.append({"check": "py_compile", "ok": ok, "results": results})
        if ok:
            return {"ok": True, "scope": scope, "findings": findings, "proposal": None, "message": "No syntax errors found in allowed source roots."}
        error_text = results[0].get("stderr", "") if results else ""
        target_path = _extract_path_from_compile_error(error_text)
        if not target_path and py_files:
            target_path = py_files[0]
        old_content = (ROOT_DIR / target_path).read_text(encoding="utf-8") if target_path and (ROOT_DIR / target_path).exists() else ""
        error_context = error_text[:4000]
        test_commands = [[sys.executable, "-m", "py_compile", target_path]]

    elif scope == "tests":
        test_path = (test_path or "tests/test_omni_chat.py").strip().replace("\\", "/")
        target = ROOT_DIR / test_path
        allowed, reason = self_evolver._is_allowed(target.resolve())
        if not allowed:
            return {"ok": False, "scope": scope, "findings": findings, "error": f"Test path not allowed: {test_path} ({reason})"}
        cmd = [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"]
        ok, results = self_evolver._run_commands([cmd], ROOT_DIR, timeout=180)
        findings.append({"check": "pytest", "ok": ok, "results": results})
        if ok:
            return {"ok": True, "scope": scope, "findings": findings, "proposal": None, "message": f"{test_path} passed. No patch needed."}
        error_text = ""
        if results:
            error_text = (results[0].get("stdout", "") + "\n" + results[0].get("stderr", "")).strip()
        target_path = _guess_source_from_test_error(error_text, test_path)
        if not target_path:
            target_path = test_path
        old_content = (ROOT_DIR / target_path).read_text(encoding="utf-8") if (ROOT_DIR / target_path).exists() else ""
        error_context = error_text[:5000]
        test_commands = [[sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"]]

    elif scope == "file":
        target_path = (relative_path or "").strip().replace("\\", "/")
        goal = (goal or "Review and improve this SHIMS module. Keep behavior identical, fix obvious issues, and improve clarity.").strip()
        if not target_path:
            return {"ok": False, "scope": scope, "findings": findings, "error": "relative_path is required for scope=file"}
        target = ROOT_DIR / target_path
        allowed, reason = self_evolver._is_allowed(target.resolve())
        if not allowed:
            return {"ok": False, "scope": scope, "findings": findings, "error": f"File not allowed: {target_path} ({reason})"}
        old_content = target.read_text(encoding="utf-8")
        error_context = goal
        test_commands = self_evolver._default_validation(target_path, ROOT_DIR)

    else:
        return {"ok": False, "scope": scope, "findings": findings, "error": f"Unknown scope: {scope}. Use tests, lint, or file."}

    if not target_path or not old_content:
        return {"ok": False, "scope": scope, "findings": findings, "error": "Could not identify a source file to patch."}

    system = (
        "You are SHIMS self-evolution. Analyze the provided source file and the context/error/goal, "
        "then propose a minimal patch. Return ONLY valid JSON with these keys:\n"
        "- reason (string): what you changed and why\n"
        "- relative_path (string): the same relative path you received\n"
        "- new_content (string): the complete new file content\n"
        "- tests (list of shell-command lists to validate the change)\n"
        "Do not include markdown code fences or any explanation outside the JSON."
    )
    prompt = (
        f"Scope: {scope}\n"
        f"Target file: {target_path}\n\n"
        f"Current file content (truncated if long):\n```\n{old_content[:12000]}\n```\n\n"
        f"Context / error / goal:\n{error_context}\n\n"
        "Produce the JSON patch now."
    )
    llm_output = ""
    try:
        result = await ask_ai(prompt, system=system, feature="self-evolution")
        llm_output = result.text or ""
        parsed = extract_json_maybe(llm_output) or json.loads(llm_output.strip())
        if not isinstance(parsed, dict):
            raise ValueError("LLM response is not a JSON object")
        new_content = parsed.get("new_content", "")
        new_relative_path = parsed.get("relative_path", target_path).replace("\\", "/")
        reason = parsed.get("reason", "LLM-generated patch from self-check")
        tests = parsed.get("tests") or test_commands
    except Exception as exc:
        return {
            "ok": False,
            "scope": scope,
            "findings": findings,
            "error": f"LLM patch generation failed: {exc}",
            "llm_output": llm_output[:2000],
        }

    if not new_content:
        return {"ok": False, "scope": scope, "findings": findings, "error": "LLM returned empty new_content."}

    proposal = self_evolver.create_proposal(
        new_relative_path,
        new_content,
        reason=reason,
        author="shims-self-check",
        scope="code",
        tests=tests,
    )
    if not proposal.get("ok"):
        return {"ok": False, "scope": scope, "findings": findings, "proposal_error": proposal.get("message"), "proposal": proposal}

    proposal_id = proposal["proposal_id"]
    validation = self_evolver.validate_proposal(proposal_id, validation=tests)
    return {
        "ok": validation.status == "validated",
        "scope": scope,
        "findings": findings,
        "proposal": self_evolver.get_proposal(proposal_id),
        "validation_status": validation.status,
        "validation_message": validation.message,
        "message": (
            "Self-check created a validated patch proposal. Review and approve it in the Self-Evolution pane."
            if validation.status == "validated"
            else "Self-check created a proposal, but sandbox validation failed. Review the validation output."
        ),
    }
