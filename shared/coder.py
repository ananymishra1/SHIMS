"""Coder workspace — SHIMS's separate coding agent ("codex").

A persistent, multi-file project workspace with an LLM-driven
**plan → write → run → read errors → fix** loop, kept separate from the main
chat. Each project lives under ``storage/coder/<id>/`` and is run in isolation
with a timeout. The LLM (via the shared provider router) returns file changes as
JSON; SHIMS applies them, runs the entrypoint, and can auto-fix on failure for a
bounded number of steps.

This supersedes the old toy ``code_sandbox.run_python_code`` (still used as the
low-level Python runner primitive).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import re

from .ai import ask_ai, extract_json_maybe
from .config import STORAGE_DIR, settings
from .security import new_id

_CODER_SETTINGS_PATH = STORAGE_DIR / "coder_settings.json"

def _get_coder_base_dir() -> Path:
    if _CODER_SETTINGS_PATH.exists():
        try:
            data = json.loads(_CODER_SETTINGS_PATH.read_text(encoding="utf-8"))
            custom = data.get("base_dir")
            if custom:
                p = Path(custom).resolve()
                p.mkdir(parents=True, exist_ok=True)
                return p
        except Exception:
            pass
    d = STORAGE_DIR / "coder"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_coder_settings() -> dict[str, Any]:
    default = str(STORAGE_DIR / "coder")
    if _CODER_SETTINGS_PATH.exists():
        try:
            data = json.loads(_CODER_SETTINGS_PATH.read_text(encoding="utf-8"))
            if not data.get("base_dir"):
                data["base_dir"] = default
            return data
        except Exception:
            pass
    return {"base_dir": default}


def set_coder_settings(data: dict[str, Any]) -> dict[str, Any]:
    current = get_coder_settings()
    current.update(data)
    _CODER_SETTINGS_PATH.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return current


_SYSTEM = (
    "You are SHIMS Coder, a precise software engineer. You are given a project goal, "
    "the current files, and an instruction. Respond with STRICT JSON only, no prose:\n"
    '{"explanation": "...", "run": "main.py", "files": {"path/name.py": "FULL FILE CONTENT", ...}}\n'
    "Always return COMPLETE file contents (not diffs). Keep it minimal and runnable. "
    "Prefer the Python standard library."
)


def _proj_dir(project_id: str) -> Path:
    base = _get_coder_base_dir()
    d = (base / project_id).resolve()
    if base.resolve() not in d.parents:
        raise ValueError("invalid project id")
    return d


def _meta_path(project_id: str) -> Path:
    return _proj_dir(project_id) / "_project.json"


def _load_meta(project_id: str) -> dict[str, Any]:
    p = _meta_path(project_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_meta(meta: dict[str, Any]) -> None:
    _meta_path(meta["id"]).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_file(project_id: str, relpath: str) -> Path:
    base = _proj_dir(project_id)
    target = (base / relpath).resolve()
    if target != base and base not in target.parents:
        raise ValueError("path escapes project")
    if target.suffix.lower() in {".exe", ".dll", ".so", ".bin"}:
        raise ValueError("disallowed file type")
    return target


def create_project(name: str, goal: str = "") -> dict[str, Any]:
    pid = new_id("proj")
    _proj_dir(pid).mkdir(parents=True, exist_ok=True)
    meta = {"id": pid, "name": (name or "project").strip(), "goal": goal.strip(),
            "created_at": time.time(), "updated_at": time.time(), "entry": "main.py", "history": []}
    _save_meta(meta)
    return meta


def list_projects() -> list[dict[str, Any]]:
    out = []
    for d in _get_coder_base_dir().iterdir():
        if d.is_dir() and (d / "_project.json").exists():
            m = _load_meta(d.name)
            out.append({k: m.get(k) for k in ("id", "name", "goal", "entry", "updated_at")})
    return sorted(out, key=lambda m: m.get("updated_at", 0), reverse=True)


def list_files(project_id: str) -> list[dict[str, Any]]:
    base = _proj_dir(project_id)
    files = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.name != "_project.json":
            files.append({"path": str(p.relative_to(base)).replace("\\", "/"), "size": p.stat().st_size})
    return files


def read_file(project_id: str, relpath: str) -> dict[str, Any]:
    target = _safe_file(project_id, relpath)
    if not target.is_file():
        return {"ok": False, "error": "not found"}
    return {"ok": True, "path": relpath, "content": target.read_text(encoding="utf-8", errors="replace")}


def write_file(project_id: str, relpath: str, content: str) -> dict[str, Any]:
    target = _safe_file(project_id, relpath)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Sanitize common JSON-in-Python mistakes for .py files
    if target.suffix.lower() == ".py":
        content = _sanitize_python(content)
    target.write_text(content, encoding="utf-8")
    return {"ok": True, "path": relpath, "size": len(content)}


def _sanitize_python(content: str) -> str:
    """Replace JSON literals (null, true, false) with Python equivalents.
    Skips occurrences inside string literals."""
    import re
    result = []
    i = 0
    in_str = None  # None, "'", or '"'
    while i < len(content):
        ch = content[i]
        if in_str is None:
            if ch in ("'", '"'):
                in_str = ch
                result.append(ch)
            elif ch == '#':
                # Rest of line is a comment — skip to newline
                nl = content.find('\n', i)
                if nl == -1:
                    result.append(content[i:])
                    break
                result.append(content[i:nl])
                i = nl
                continue
            else:
                # Check for bare word at this position
                m = re.match(r'\b(null|true|false)\b', content[i:])
                if m:
                    w = m.group(0)
                    result.append({"null": "None", "true": "True", "false": "False"}.get(w, w))
                    i += len(w)
                    continue
                result.append(ch)
        else:
            result.append(ch)
            if ch == '\\' and i + 1 < len(content):
                result.append(content[i + 1])
                i += 1
            elif ch == in_str:
                in_str = None
        i += 1
    return ''.join(result)


def get_project(project_id: str) -> dict[str, Any]:
    meta = _load_meta(project_id)
    if not meta:
        return {"ok": False, "error": "project not found"}
    return {"ok": True, "project": meta, "files": list_files(project_id)}


def _install_requirements(project_id: str) -> dict[str, Any]:
    """Install requirements.txt if it exists. Returns install log."""
    req_file = _proj_dir(project_id) / "requirements.txt"
    if not req_file.is_file():
        return {"installed": False, "reason": "no requirements.txt"}
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_proj_dir(project_id))
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file), "--quiet", "--disable-pip-version-check"],
            cwd=str(_proj_dir(project_id)), capture_output=True, text=True,
            timeout=120, env=env
        )
        return {
            "installed": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-2000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {"installed": False, "reason": "pip install timed out", "stderr": (exc.stderr or "")[-1000:]}
    except Exception as e:
        return {"installed": False, "reason": str(e)}


def run_project(project_id: str, entry: str | None = None) -> dict[str, Any]:
    """Run the project's entry script in its own directory, with a timeout.
    Automatically installs requirements.txt before running."""
    meta = _load_meta(project_id)
    if not meta:
        return {"ok": False, "error": "project not found"}
    entry = entry or meta.get("entry") or "main.py"
    target = _safe_file(project_id, entry)
    if not target.is_file():
        return {"ok": False, "error": f"entry '{entry}' not found"}

    # Auto-install dependencies
    req_result = _install_requirements(project_id)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(_proj_dir(project_id))
    try:
        proc = subprocess.run([sys.executable, str(target)], cwd=str(_proj_dir(project_id)),
                              capture_output=True, text=True, timeout=settings.code_timeout_seconds, env=env)
        result = {"ok": proc.returncode == 0, "returncode": proc.returncode,
                  "stdout": proc.stdout[-6000:], "stderr": proc.stderr[-6000:], "entry": entry}
        if not req_result.get("installed") and req_result.get("stderr"):
            result["stderr"] = "[pip install output]\n" + req_result["stderr"] + "\n\n[run output]\n" + result["stderr"]
        return result
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "returncode": -1, "stdout": (exc.stdout or "")[-3000:],
                "stderr": "Timed out.", "entry": entry}
    except Exception as exc:
        return {"ok": False, "returncode": -1, "stdout": "",
                "stderr": f"Runtime error: {str(exc)[:500]}", "entry": entry,
                "crashed": True, "crash_type": "run_error", "retryable": True}


def _prefer_coder_model(provider: str | None, model: str | None) -> str | None:
    """If using local Ollama with no explicit model, prefer an installed coding model."""
    if model:
        return model
    if provider not in (None, "", "ollama"):
        return model
    try:
        import os
        import httpx
        base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        with httpx.Client(timeout=8) as c:
            names = [m.get("name", "") for m in c.get(f"{base}/api/tags").json().get("models", [])]
        for pref in ("qwen2.5-coder", "coder", "deepseek-coder", "codellama", "qwen2.5"):
            hit = next((n for n in names if pref in n.lower()), None)
            if hit:
                return hit
    except Exception:
        pass
    return model


def _parse_spec(text: str) -> dict[str, Any]:
    """Robustly extract {explanation, run, files{}} from imperfect LLM output.

    Handles strict JSON, ```json fenced JSON, and a final fallback that treats a
    fenced ```python/```py block as main.py — so even small local models work.
    """
    spec = extract_json_maybe(text)
    if isinstance(spec, dict) and spec.get("files"):
        return spec
    # ```json fenced
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            spec = json.loads(m.group(1))
            if isinstance(spec, dict) and spec.get("files"):
                return spec
        except Exception:
            pass
    # Fallback: first fenced code block becomes main.py
    code = re.search(r"```(?:python|py)?\s*(.*?)```", text, re.S)
    if code and code.group(1).strip():
        return {"explanation": "Recovered code block from model output.",
                "run": "main.py", "files": {"main.py": code.group(1).strip() + "\n"}}
    return {"explanation": text[:300], "files": {}}


def _project_context(project_id: str, max_chars: int = 12000) -> str:
    parts = []
    used = 0
    for f in list_files(project_id):
        r = read_file(project_id, f["path"])
        body = r.get("content", "")
        snippet = body[: max(0, max_chars - used)]
        parts.append(f"### FILE: {f['path']}\n```\n{snippet}\n```")
        used += len(snippet)
        if used >= max_chars:
            break
    return "\n\n".join(parts) if parts else "(empty project)"


async def iterate(project_id: str, instruction: str, *, provider: str | None = None,
                  model: str | None = None, max_steps: int = 2, auto_run: bool = True,
                  on_step: Any = None) -> dict[str, Any]:
    """One LLM coding step (with optional auto-fix retries on run failure).

    If ``on_step`` is given it is called with each completed step dict — used by
    the background coder job to stream live progress into the chat.
    """
    meta = _load_meta(project_id)
    if not meta:
        return {"ok": False, "error": "project not found"}
    model = _prefer_coder_model(provider, model)
    steps: list[dict[str, Any]] = []
    feedback = ""
    last_run: dict[str, Any] | None = None
    for step in range(1, max(1, min(max_steps, 4)) + 1):
        prompt = (
            f"PROJECT GOAL:\n{meta.get('goal') or meta.get('name')}\n\n"
            f"CURRENT FILES:\n{_project_context(project_id)}\n\n"
            f"INSTRUCTION:\n{instruction}\n"
        )
        if feedback:
            prompt += f"\nThe previous run FAILED. Fix it. Run output:\n{feedback}\n"
        result = await ask_ai(prompt, system=_SYSTEM, provider=provider, model=model)
        spec = _parse_spec(result.text)
        files = spec.get("files") or {}
        changed = []
        for path, content in files.items():
            if isinstance(content, str):
                try:
                    write_file(project_id, path, content)
                    changed.append(path)
                except ValueError:
                    continue
        if spec.get("run"):
            meta["entry"] = str(spec["run"])
        run_result = run_project(project_id) if (auto_run and changed) else None
        last_run = run_result or last_run
        step_record = {"step": step, "explanation": spec.get("explanation", "")[:500],
                       "files_changed": changed, "run": run_result,
                       "llm_provider": result.provider}
        steps.append(step_record)
        if callable(on_step):
            try:
                on_step(step_record)
            except Exception:
                pass
        if not run_result or run_result.get("ok") or not auto_run:
            break
        feedback = (run_result.get("stderr") or run_result.get("stdout") or "")[-2500:]
    meta["updated_at"] = time.time()
    meta.setdefault("history", []).append({"at": time.time(), "instruction": instruction[:300], "steps": len(steps)})
    _save_meta(meta)
    crashed = last_run and last_run.get("crashed")
    return {"ok": True, "project_id": project_id, "steps": steps,
            "files": list_files(project_id), "final_run": last_run, "entry": meta.get("entry"),
            "crashed": crashed, "retryable": crashed,
            "crash_context": {"project_id": project_id, "instruction": instruction, "provider": provider, "model": model} if crashed else None}
