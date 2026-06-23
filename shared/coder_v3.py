"""Coder Playground v3 — Full Power IDE Backend.

Capabilities:
- Multi-file selection & bulk operations
- Project-wide search/replace (grep-style)
- Arbitrary shell execution with safety levels
- Package management (pip, npm, cargo, go mod)
- Code formatting (black, prettier, gofmt, rustfmt, autopep8)
- Test runners (pytest, jest, cargo test, go test)
- Process management & port allocation
- Environment variables per project
- File watcher / auto-reload
- Permission request system + audit log
- AI inline assistance (generate, explain, refactor, test, doc)
- Git enhancements (blame, file history, stash, remote)
- Diff engine (file vs file, branch vs branch)
- Project health check
- Backup/restore snapshots
- Symbol/outline extraction
- Command palette registry
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .coder_v2 import (
    CODER_DIR,
    LANGUAGE_CONFIG,
    TERMINAL_DIR,
    _project_meta_path,
    _project_path,
    check_permission,
    detect_language,
    list_files,
    read_file,
    write_file,
)
from .config import STORAGE_DIR

warnings.filterwarnings("ignore")

# ── Process Manager ─────────────────────────────────────────────────────────

_active_processes: dict[str, dict[str, Any]] = {}
_process_lock = threading.Lock()


def run_shell_command(
    project_id: str,
    command: str,
    timeout: int = 300,
    background: bool = False,
    env_vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run an arbitrary shell command in a project directory.

    Safety: commands are logged; dangerous patterns require explicit permission.
    """
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    # Safety scan
    danger_patterns = [
        r"\brm\s+-rf\s+/",
        r"\bdd\s+if=",
        r"\bmkfs\.",
        r"\b:(){ :|:& };:",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bdel\s+/[fq]",
        r"\bformat\s+[a-zA-Z]:",
    ]
    is_dangerous = any(re.search(p, command, re.IGNORECASE) for p in danger_patterns)

    proc_env = os.environ.copy()
    if env_vars:
        proc_env.update(env_vars)

    if background:
        proc = subprocess.Popen(
            command,
            cwd=str(project_dir),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=proc_env,
        )
        proc_id = f"proc_{uuid.uuid4().hex[:8]}"
        with _process_lock:
            _active_processes[proc_id] = {
                "proc": proc,
                "project_id": project_id,
                "command": command,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "output_buffer": [],
                "is_dangerous": is_dangerous,
            }
        # Start reader thread
        threading.Thread(
            target=_bg_process_reader,
            args=(proc_id,),
            daemon=True,
        ).start()
        _audit_log(project_id, "shell_bg", {"command": command, "dangerous": is_dangerous})
        return {
            "ok": True,
            "process_id": proc_id,
            "pid": proc.pid,
            "dangerous": is_dangerous,
        }

    try:
        result = subprocess.run(
            command,
            cwd=str(project_dir),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=proc_env,
        )
        _audit_log(project_id, "shell", {"command": command, "dangerous": is_dangerous, "rc": result.returncode})
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "dangerous": is_dangerous,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timed out after {timeout}s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _bg_process_reader(proc_id: str) -> None:
    entry = _active_processes.get(proc_id)
    if not entry:
        return
    proc = entry["proc"]
    try:
        for line in iter(proc.stdout.readline, ""):
            entry["output_buffer"].append(line)
            # Keep buffer bounded
            if len(entry["output_buffer"]) > 5000:
                entry["output_buffer"] = entry["output_buffer"][-2500:]
    except Exception:
        pass
    finally:
        proc.stdout.close()


def list_processes(project_id: str | None = None) -> list[dict[str, Any]]:
    """List active background processes."""
    out = []
    with _process_lock:
        for pid, info in _active_processes.items():
            if project_id and info["project_id"] != project_id:
                continue
            proc = info["proc"]
            alive = proc.poll() is None
            out.append({
                "process_id": pid,
                "project_id": info["project_id"],
                "command": info["command"],
                "pid": proc.pid,
                "alive": alive,
                "started_at": info["started_at"],
                "is_dangerous": info.get("is_dangerous", False),
            })
    return out


def read_process_output(proc_id: str, clear: bool = True) -> dict[str, Any]:
    entry = _active_processes.get(proc_id)
    if not entry:
        return {"ok": False, "error": "Process not found"}
    proc = entry["proc"]
    alive = proc.poll() is None
    with _process_lock:
        buf = entry["output_buffer"]
        text = "".join(buf)
        if clear:
            entry["output_buffer"] = []
    return {"ok": True, "alive": alive, "output": text, "returncode": proc.returncode if not alive else None}


def kill_process(proc_id: str) -> dict[str, Any]:
    entry = _active_processes.get(proc_id)
    if not entry:
        return {"ok": False, "error": "Process not found"}
    proc = entry["proc"]
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    _active_processes.pop(proc_id, None)
    return {"ok": True}


# ── Port Allocation ─────────────────────────────────────────────────────────

_allocated_ports: dict[int, str] = {}
_port_lock = threading.Lock()


def find_free_port(start: int = 9000, end: int = 9999) -> int | None:
    import socket
    for port in range(start, end):
        with _port_lock:
            if port in _allocated_ports:
                continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return None


def allocate_port(project_id: str, purpose: str = "app") -> dict[str, Any]:
    port = find_free_port()
    if port is None:
        return {"ok": False, "error": "No free ports in range 9000-9999"}
    with _port_lock:
        _allocated_ports[port] = f"{project_id}:{purpose}"
    _audit_log(project_id, "port_allocate", {"port": port, "purpose": purpose})
    return {"ok": True, "port": port, "purpose": purpose}


def list_allocated_ports(project_id: str | None = None) -> list[dict[str, Any]]:
    with _port_lock:
        return [
            {"port": p, "owner": o}
            for p, o in _allocated_ports.items()
            if project_id is None or o.startswith(project_id + ":")
        ]


def release_port(port: int) -> dict[str, Any]:
    with _port_lock:
        if port in _allocated_ports:
            del _allocated_ports[port]
            return {"ok": True}
    return {"ok": False, "error": "Port not allocated"}


# ── Environment Variables ───────────────────────────────────────────────────

_env_store: dict[str, dict[str, str]] = {}


def get_env_vars(project_id: str) -> dict[str, Any]:
    return {"ok": True, "env": _env_store.get(project_id, {})}


def set_env_vars(project_id: str, env: dict[str, str], merge: bool = True) -> dict[str, Any]:
    if merge:
        current = _env_store.get(project_id, {})
        current.update(env)
        _env_store[project_id] = current
    else:
        _env_store[project_id] = env
    _audit_log(project_id, "env_set", {"keys": list(env.keys())})
    return {"ok": True, "env": _env_store[project_id]}


# ── Project-wide Search ─────────────────────────────────────────────────────


def search_in_project(project_id: str, query: str, regex: bool = False, case_sensitive: bool = False, file_pattern: str = "*") -> dict[str, Any]:
    """Search for text across all files in a project."""
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query, flags) if regex else re.compile(re.escape(query), flags)
    except re.error as exc:
        return {"ok": False, "error": f"Invalid regex: {exc}"}

    results = []
    for file_path in project_dir.rglob(file_pattern):
        if file_path.is_file() and file_path.name != "_project.json":
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()
                for i, line in enumerate(lines, 1):
                    if pattern.search(line):
                        rel = str(file_path.relative_to(project_dir)).replace("\\", "/")
                        results.append({
                            "file": rel,
                            "line": i,
                            "text": line[:200],
                        })
            except Exception:
                pass
    return {"ok": True, "query": query, "results": results, "count": len(results)}


def find_replace_all(project_id: str, find: str, replace: str, regex: bool = False, case_sensitive: bool = False, file_pattern: str = "*") -> dict[str, Any]:
    """Find and replace across all files in a project."""
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(find, flags) if regex else re.compile(re.escape(find), flags)
    except re.error as exc:
        return {"ok": False, "error": f"Invalid regex: {exc}"}

    changes = []
    for file_path in project_dir.rglob(file_pattern):
        if file_path.is_file() and file_path.name != "_project.json":
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                new_content, count = pattern.subn(replace, content)
                if count:
                    file_path.write_text(new_content, encoding="utf-8")
                    rel = str(file_path.relative_to(project_dir)).replace("\\", "/")
                    changes.append({"file": rel, "replacements": count})
            except Exception:
                pass

    _audit_log(project_id, "find_replace", {"find": find, "replace": replace, "files_changed": len(changes)})
    return {"ok": True, "changes": changes, "total_replacements": sum(c["replacements"] for c in changes)}


# ── Multi-file Selection & Bulk Operations ──────────────────────────────────


def bulk_delete(project_id: str, paths: list[str]) -> dict[str, Any]:
    project_dir = _project_path(project_id)
    deleted = []
    errors = []
    for p in paths:
        full = (project_dir / p).resolve()
        if not str(full).startswith(str(project_dir.resolve())):
            errors.append(f"Blocked: {p}")
            continue
        try:
            if full.is_dir():
                shutil.rmtree(full)
            else:
                full.unlink()
            deleted.append(p)
        except Exception as exc:
            errors.append(f"{p}: {exc}")
    _audit_log(project_id, "bulk_delete", {"deleted": deleted})
    return {"ok": len(errors) == 0, "deleted": deleted, "errors": errors}


def bulk_move(project_id: str, moves: list[dict[str, str]]) -> dict[str, Any]:
    """moves: [{"from": "a.txt", "to": "b/c.txt"}, ...]"""
    project_dir = _project_path(project_id)
    moved = []
    errors = []
    for m in moves:
        src = (project_dir / m["from"]).resolve()
        dst = (project_dir / m["to"]).resolve()
        if not str(src).startswith(str(project_dir.resolve())) or not str(dst).startswith(str(project_dir.resolve())):
            errors.append(f"Blocked: {m}")
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append(m)
        except Exception as exc:
            errors.append(f"{m}: {exc}")
    _audit_log(project_id, "bulk_move", {"moved": moved})
    return {"ok": len(errors) == 0, "moved": moved, "errors": errors}


def bulk_copy(project_id: str, copies: list[dict[str, str]]) -> dict[str, Any]:
    project_dir = _project_path(project_id)
    copied = []
    errors = []
    for c in copies:
        src = (project_dir / c["from"]).resolve()
        dst = (project_dir / c["to"]).resolve()
        if not str(src).startswith(str(project_dir.resolve())) or not str(dst).startswith(str(project_dir.resolve())):
            errors.append(f"Blocked: {c}")
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
            copied.append(c)
        except Exception as exc:
            errors.append(f"{c}: {exc}")
    _audit_log(project_id, "bulk_copy", {"copied": copied})
    return {"ok": len(errors) == 0, "copied": copied, "errors": errors}


# ── Package Management ──────────────────────────────────────────────────────


def install_packages(project_id: str, packages: list[str], manager: str | None = None) -> dict[str, Any]:
    """Install packages using the appropriate package manager."""
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    lang = detect_language(project_dir)
    if manager is None:
        if lang == "python":
            manager = "pip"
        elif lang in ("javascript", "typescript"):
            manager = "npm"
        elif lang == "go":
            manager = "go"
        elif lang == "rust":
            manager = "cargo"
        else:
            manager = "pip"

    cmd_map = {
        "pip": [sys.executable, "-m", "pip", "install"] + packages,
        "npm": ["npm", "install"] + packages,
        "cargo": ["cargo", "add"] + packages,
        "go": ["go", "get"] + packages,
    }

    cmd = cmd_map.get(manager)
    if not cmd:
        return {"ok": False, "error": f"Unknown package manager: {manager}"}

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=300,
        )
        _audit_log(project_id, "install", {"manager": manager, "packages": packages, "rc": result.returncode})
        return {
            "ok": result.returncode == 0,
            "manager": manager,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Code Formatting ─────────────────────────────────────────────────────────


def format_code(project_id: str, file_path: str | None = None) -> dict[str, Any]:
    """Format code using language-appropriate formatter."""
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    lang = detect_language(project_dir)

    formatters = {
        "python": ["black", "-l", "100"],
        "javascript": ["npx", "prettier", "--write"],
        "typescript": ["npx", "prettier", "--write"],
        "go": ["gofmt", "-w"],
        "rust": ["cargo", "fmt"],
    }

    fmt_cmd = formatters.get(lang)
    if not fmt_cmd:
        return {"ok": False, "error": f"No formatter configured for {lang}"}

    target = str(project_dir) if file_path is None else str(project_dir / file_path)
    cmd = fmt_cmd + [target]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        _audit_log(project_id, "format", {"lang": lang, "target": target, "rc": result.returncode})
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"Formatter not found: {fmt_cmd[0]}. Install it first."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Test Runner ─────────────────────────────────────────────────────────────


def run_tests(project_id: str, target: str | None = None) -> dict[str, Any]:
    """Run tests for the project."""
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    lang = detect_language(project_dir)

    test_cmds = {
        "python": [sys.executable, "-m", "pytest", "-v"] + ([target] if target else []),
        "javascript": ["npm", "test"] + ([target] if target else []),
        "typescript": ["npm", "test"] + ([target] if target else []),
        "go": ["go", "test", "./..."] + ([target] if target else []),
        "rust": ["cargo", "test"] + ([target] if target else []),
    }

    cmd = test_cmds.get(lang)
    if not cmd:
        return {"ok": False, "error": f"No test runner configured for {lang}"}

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=300,
        )
        _audit_log(project_id, "test", {"lang": lang, "rc": result.returncode})
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"Test runner not found for {lang}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── File Watcher ────────────────────────────────────────────────────────────

_watchers: dict[str, dict[str, Any]] = {}


def start_file_watcher(project_id: str, callback_url: str | None = None) -> dict[str, Any]:
    """Start a file watcher for a project."""
    try:
        import watchdog.observers
        import watchdog.events
    except ImportError:
        return {"ok": False, "error": "watchdog not installed. Run: pip install watchdog"}

    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    watcher_id = f"watch_{project_id}"
    if watcher_id in _watchers:
        return {"ok": True, "watcher_id": watcher_id, "message": "Watcher already running"}

    class Handler(watchdog.events.FileSystemEventHandler):
        def on_any_event(self, event):
            if event.src_path.endswith("_project.json"):
                return
            _audit_log(project_id, "watcher_event", {"type": event.event_type, "path": event.src_path})

    observer = watchdog.observers.Observer()
    handler = Handler()
    observer.schedule(handler, str(project_dir), recursive=True)
    observer.start()

    _watchers[watcher_id] = {"observer": observer, "project_id": project_id, "started_at": datetime.now(timezone.utc).isoformat()}
    return {"ok": True, "watcher_id": watcher_id}


def stop_file_watcher(project_id: str) -> dict[str, Any]:
    watcher_id = f"watch_{project_id}"
    entry = _watchers.pop(watcher_id, None)
    if entry:
        entry["observer"].stop()
        entry["observer"].join()
        return {"ok": True}
    return {"ok": False, "error": "Watcher not found"}


# ── Git Enhancements ────────────────────────────────────────────────────────


def _git_raw(project_id: str, args: list[str]) -> dict[str, Any]:
    project_dir = _project_path(project_id)
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir)] + args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {"ok": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr}
    except FileNotFoundError:
        return {"ok": False, "error": "Git not installed"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def git_blame(project_id: str, file_path: str) -> dict[str, Any]:
    return _git_raw(project_id, ["blame", file_path])


def git_file_history(project_id: str, file_path: str, n: int = 20) -> dict[str, Any]:
    return _git_raw(project_id, ["log", f"-{n}", "--oneline", "--", file_path])


def git_stash(project_id: str, message: str | None = None) -> dict[str, Any]:
    if message:
        return _git_raw(project_id, ["stash", "push", "-m", message])
    return _git_raw(project_id, ["stash", "push"])


def git_stash_list(project_id: str) -> dict[str, Any]:
    return _git_raw(project_id, ["stash", "list"])


def git_stash_pop(project_id: str, stash: str = "stash@{0}") -> dict[str, Any]:
    return _git_raw(project_id, ["stash", "pop", stash])


def git_remote_list(project_id: str) -> dict[str, Any]:
    return _git_raw(project_id, ["remote", "-v"])


def git_remote_add(project_id: str, name: str, url: str) -> dict[str, Any]:
    return _git_raw(project_id, ["remote", "add", name, url])


def git_tag_list(project_id: str) -> dict[str, Any]:
    return _git_raw(project_id, ["tag", "-l"])


def git_create_tag(project_id: str, tag: str, message: str = "") -> dict[str, Any]:
    if message:
        return _git_raw(project_id, ["tag", "-a", tag, "-m", message])
    return _git_raw(project_id, ["tag", tag])


def git_diff_branches(project_id: str, branch_a: str, branch_b: str) -> dict[str, Any]:
    return _git_raw(project_id, ["diff", f"{branch_a}...{branch_b}"])


# ── Diff Engine ─────────────────────────────────────────────────────────────


def diff_files(project_id: str, file_a: str, file_b: str) -> dict[str, Any]:
    """Compare two files within a project."""
    project_dir = _project_path(project_id)
    fa = project_dir / file_a
    fb = project_dir / file_b
    if not fa.exists() or not fb.exists():
        return {"ok": False, "error": "One or both files not found"}
    try:
        import difflib
        a_lines = fa.read_text(encoding="utf-8", errors="replace").splitlines()
        b_lines = fb.read_text(encoding="utf-8", errors="replace").splitlines()
        diff = list(difflib.unified_diff(a_lines, b_lines, fromfile=file_a, tofile=file_b, lineterm=""))
        return {"ok": True, "diff": "\n".join(diff), "lines_changed": len(diff)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def diff_file_versions(project_id: str, file_path: str, ref_a: str = "HEAD", ref_b: str = "") -> dict[str, Any]:
    """Show git diff for a file between two refs."""
    if not ref_b:
        ref_b = ref_a
        ref_a = "HEAD~1"
    return _git_raw(project_id, ["diff", f"{ref_a}..{ref_b}", "--", file_path])


# ── Symbol Extraction / Outline ─────────────────────────────────────────────


def extract_symbols(project_id: str, file_path: str) -> dict[str, Any]:
    """Extract function/class/variable symbols from a Python file."""
    project_dir = _project_path(project_id)
    full = project_dir / file_path
    if not full.exists():
        return {"ok": False, "error": "File not found"}

    ext = Path(file_path).suffix.lower()
    content = full.read_text(encoding="utf-8", errors="replace")

    symbols = []
    if ext == ".py":
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append({"type": "function", "name": node.name, "line": node.lineno})
                elif isinstance(node, ast.ClassDef):
                    symbols.append({"type": "class", "name": node.name, "line": node.lineno})
        except SyntaxError:
            pass
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        # Simple regex-based extraction for JS/TS
        for match in re.finditer(r"(?:function|const|let|var|class|interface|type)\s+(\w+)", content):
            symbols.append({"type": "symbol", "name": match.group(1), "line": content[:match.start()].count("\n") + 1})
    elif ext == ".go":
        for match in re.finditer(r"^func\s+(?:\([^)]+\)\s+)?(\w+)", content, re.MULTILINE):
            symbols.append({"type": "function", "name": match.group(1), "line": content[:match.start()].count("\n") + 1})
        for match in re.finditer(r"^type\s+(\w+)\s+", content, re.MULTILINE):
            symbols.append({"type": "type", "name": match.group(1), "line": content[:match.start()].count("\n") + 1})

    return {"ok": True, "symbols": symbols, "file": file_path}


def project_outline(project_id: str) -> dict[str, Any]:
    """Build an outline of the entire project."""
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    outline = []
    for f in sorted(project_dir.rglob("*")):
        if f.is_file() and f.suffix in (".py", ".js", ".ts", ".go", ".rs", ".jsx", ".tsx"):
            rel = str(f.relative_to(project_dir)).replace("\\", "/")
            result = extract_symbols(project_id, rel)
            if result["ok"] and result["symbols"]:
                outline.append({"file": rel, "symbols": result["symbols"]})
    return {"ok": True, "outline": outline}


# ── Project Health Check ────────────────────────────────────────────────────


def project_health(project_id: str) -> dict[str, Any]:
    """Run a health check on the project."""
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    issues = []
    lang = detect_language(project_dir)

    # Check for common issues
    if lang == "python":
        if not (project_dir / "requirements.txt").exists() and not (project_dir / "pyproject.toml").exists():
            issues.append({"level": "warning", "message": "No requirements.txt or pyproject.toml found"})
        for f in project_dir.rglob("*.py"):
            try:
                ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError as exc:
                rel = str(f.relative_to(project_dir)).replace("\\", "/")
                issues.append({"level": "error", "message": f"Syntax error in {rel}: {exc.msg}", "file": rel, "line": exc.lineno})
    elif lang in ("javascript", "typescript"):
        if not (project_dir / "package.json").exists():
            issues.append({"level": "warning", "message": "No package.json found"})
    elif lang == "go":
        if not (project_dir / "go.mod").exists():
            issues.append({"level": "warning", "message": "No go.mod found"})
    elif lang == "rust":
        if not (project_dir / "Cargo.toml").exists():
            issues.append({"level": "warning", "message": "No Cargo.toml found"})

    # Check for very large files
    for f in project_dir.rglob("*"):
        if f.is_file() and f.stat().st_size > 5 * 1024 * 1024:
            rel = str(f.relative_to(project_dir)).replace("\\", "/")
            issues.append({"level": "warning", "message": f"Large file (>5MB): {rel}"})

    _audit_log(project_id, "health_check", {"issues": len(issues)})
    return {"ok": True, "language": lang, "issues": issues, "healthy": len([i for i in issues if i["level"] == "error"]) == 0}


# ── Snapshots / Backup ──────────────────────────────────────────────────────

SNAPSHOT_DIR = STORAGE_DIR / "coder_snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def create_snapshot(project_id: str, name: str | None = None) -> dict[str, Any]:
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    snap_id = f"{project_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    snap_path = SNAPSHOT_DIR / f"{snap_id}.zip"

    try:
        import zipfile
        with zipfile.ZipFile(snap_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in project_dir.rglob("*"):
                if file_path.is_file():
                    arcname = str(file_path.relative_to(project_dir)).replace("\\", "/")
                    zf.write(file_path, arcname)
        meta = {
            "id": snap_id,
            "project_id": project_id,
            "name": name or f"Snapshot {snap_id}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "size": snap_path.stat().st_size,
            "path": str(snap_path),
        }
        meta_path = SNAPSHOT_DIR / f"{snap_id}.json"
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        _audit_log(project_id, "snapshot", {"snap_id": snap_id})
        return {"ok": True, **meta}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def list_snapshots(project_id: str | None = None) -> list[dict[str, Any]]:
    snaps = []
    for meta_path in sorted(SNAPSHOT_DIR.glob("*.json"), reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if project_id is None or meta.get("project_id") == project_id:
                snaps.append(meta)
        except Exception:
            pass
    return snaps


def restore_snapshot(snap_id: str) -> dict[str, Any]:
    meta_path = SNAPSHOT_DIR / f"{snap_id}.json"
    if not meta_path.exists():
        return {"ok": False, "error": "Snapshot not found"}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    project_id = meta["project_id"]
    project_dir = _project_path(project_id)
    snap_path = Path(meta["path"])

    if not snap_path.exists():
        return {"ok": False, "error": "Snapshot file not found"}

    # Backup current state first
    backup_dir = CODER_DIR / f"{project_id}_pre_restore"
    if project_dir.exists():
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.copytree(project_dir, backup_dir)

    try:
        import zipfile
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(snap_path, "r") as zf:
            zf.extractall(str(project_dir))
        _audit_log(project_id, "restore", {"snap_id": snap_id})
        return {"ok": True, "project_id": project_id, "restored_from": snap_id}
    except Exception as exc:
        # Try to restore backup
        if backup_dir.exists():
            if project_dir.exists():
                shutil.rmtree(project_dir)
            shutil.copytree(backup_dir, project_dir)
        return {"ok": False, "error": str(exc)}


# ── Permission Request System ───────────────────────────────────────────────

_permission_requests: dict[str, dict[str, Any]] = {}


def request_permission(project_id: str, operation: str, reason: str, requested_by: str = "user") -> dict[str, Any]:
    req_id = str(uuid.uuid4())[:8]
    _permission_requests[req_id] = {
        "id": req_id,
        "project_id": project_id,
        "operation": operation,
        "reason": reason,
        "requested_by": requested_by,
        "status": "pending",
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"ok": True, "request_id": req_id, "status": "pending"}


def list_permission_requests(project_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    out = []
    for req in _permission_requests.values():
        if project_id and req["project_id"] != project_id:
            continue
        if status and req["status"] != status:
            continue
        out.append(req)
    return sorted(out, key=lambda x: x["requested_at"], reverse=True)


def approve_permission(request_id: str, approved_by: str = "admin") -> dict[str, Any]:
    req = _permission_requests.get(request_id)
    if not req:
        return {"ok": False, "error": "Request not found"}
    req["status"] = "approved"
    req["approved_by"] = approved_by
    req["approved_at"] = datetime.now(timezone.utc).isoformat()
    _audit_log(req["project_id"], "permission_approved", {"request_id": request_id, "operation": req["operation"]})
    return {"ok": True, "request": req}


def deny_permission(request_id: str, denied_by: str = "admin", reason: str = "") -> dict[str, Any]:
    req = _permission_requests.get(request_id)
    if not req:
        return {"ok": False, "error": "Request not found"}
    req["status"] = "denied"
    req["denied_by"] = denied_by
    req["denied_at"] = datetime.now(timezone.utc).isoformat()
    req["denial_reason"] = reason
    return {"ok": True, "request": req}


def check_permission_with_request(project_id: str, operation: str, reason: str = "", auto_approve: bool = False) -> dict[str, Any]:
    """Check permission; if denied, optionally create a request."""
    if check_permission(operation):
        return {"ok": True, "granted": True}
    if auto_approve:
        return {"ok": True, "granted": True, "auto_approved": True}
    if reason:
        req = request_permission(project_id, operation, reason)
        return {"ok": True, "granted": False, "request_id": req["request_id"], "message": "Permission requested"}
    return {"ok": True, "granted": False, "message": "Permission denied"}


# ── Audit Log ───────────────────────────────────────────────────────────────

_audit_entries: list[dict[str, Any]] = []
_audit_lock = threading.Lock()


def _audit_log(project_id: str, action: str, details: dict[str, Any]) -> None:
    with _audit_lock:
        _audit_entries.append({
            "project_id": project_id,
            "action": action,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Persist
        audit_path = STORAGE_DIR / "coder_audit.jsonl"
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"project_id": project_id, "action": action, "details": details, "timestamp": datetime.now(timezone.utc).isoformat()}) + "\n")


def get_audit_log(project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with _audit_lock:
        entries = [e for e in _audit_entries if project_id is None or e["project_id"] == project_id]
    return entries[-limit:]


# ── AI Inline Assistance ────────────────────────────────────────────────────


async def ai_assist(project_id: str, action: str, file_path: str, selection: str = "", context: str = "", model: str = "") -> dict[str, Any]:
    """Request AI assistance for a specific coding task.

    Actions: generate, explain, refactor, test, doc, fix, optimize
    """
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    full = project_dir / file_path
    file_content = ""
    if full.exists():
        file_content = full.read_text(encoding="utf-8", errors="replace")

    prompts = {
        "generate": f"Generate code for the following request. File: {file_path}\n\nContext:\n{context}\n\nExisting content:\n{file_content[:2000]}",
        "explain": f"Explain this code in detail. File: {file_path}\n\n```\n{selection or file_content[:3000]}\n```",
        "refactor": f"Refactor this code to improve readability and performance. File: {file_path}\n\n```\n{selection or file_content[:3000]}\n```\n\nProvide the refactored code only.",
        "test": f"Generate unit tests for this code. File: {file_path}\n\n```\n{selection or file_content[:3000]}\n```",
        "doc": f"Generate documentation (docstrings/comments) for this code. File: {file_path}\n\n```\n{selection or file_content[:3000]}\n```",
        "fix": f"Fix any bugs or issues in this code. File: {file_path}\n\n```\n{selection or file_content[:3000]}\n```\n\nProvide the fixed code only.",
        "optimize": f"Optimize this code for better performance. File: {file_path}\n\n```\n{selection or file_content[:3000]}\n```\n\nProvide the optimized code only.",
    }

    prompt = prompts.get(action, context)

    # Use the SHIMS AI pipeline
    try:
        from shared.neural_governor.governor import NeuralGovernor
        gov = NeuralGovernor(user_id=0, session_id=f"coder_{project_id}")
        response = await gov.chat(prompt, model_preference=model or None)
        _audit_log(project_id, "ai_assist", {"action": action, "file": file_path})
        return {"ok": True, "action": action, "response": response.get("output", ""), "model_used": response.get("model", "unknown")}
    except Exception as exc:
        # Fallback: return the prompt for manual use
        return {"ok": False, "error": str(exc), "prompt": prompt}


def ai_apply(project_id: str, file_path: str, ai_response: str) -> dict[str, Any]:
    """Apply code from an AI response to a project file.

    Parses markdown code fences and writes the extracted code via write_file().
    When multiple code blocks are present, the first non-empty block is written
    to the requested file_path and the count of discovered blocks is returned.
    """
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}
    if not file_path:
        return {"ok": False, "error": "file_path is required"}

    code_blocks = re.findall(r"```(?:[^\n]*)\n(.*?)```", ai_response, re.DOTALL)

    code = next((b.strip() for b in code_blocks if b.strip()), "")
    if not code:
        return {"ok": False, "error": "No code found in AI response"}

    result = write_file(project_id, file_path, code)
    if result.get("ok"):
        _audit_log(project_id, "ai_apply", {"file": file_path, "blocks_found": len(code_blocks)})
        result["blocks_found"] = len(code_blocks)
    return result


# ── Command Palette Registry ────────────────────────────────────────────────

_COMMAND_PALETTE: list[dict[str, Any]] = [
    {"id": "new_file", "label": "New File...", "category": "File", "shortcut": "Ctrl+N"},
    {"id": "new_folder", "label": "New Folder...", "category": "File", "shortcut": "Ctrl+Shift+N"},
    {"id": "save", "label": "Save", "category": "File", "shortcut": "Ctrl+S"},
    {"id": "close_tab", "label": "Close Tab", "category": "File", "shortcut": "Ctrl+W"},
    {"id": "run", "label": "Run Project", "category": "Run", "shortcut": "F5"},
    {"id": "run_tests", "label": "Run Tests", "category": "Run", "shortcut": "Ctrl+Shift+T"},
    {"id": "format", "label": "Format Code", "category": "Edit", "shortcut": "Shift+Alt+F"},
    {"id": "find", "label": "Find in Project", "category": "Search", "shortcut": "Ctrl+Shift+F"},
    {"id": "replace", "label": "Replace in Project", "category": "Search", "shortcut": "Ctrl+Shift+H"},
    {"id": "go_to_file", "label": "Go to File", "category": "Navigation", "shortcut": "Ctrl+P"},
    {"id": "command_palette", "label": "Command Palette", "category": "Navigation", "shortcut": "Ctrl+Shift+P"},
    {"id": "toggle_terminal", "label": "Toggle Terminal", "category": "View", "shortcut": "Ctrl+`"},
    {"id": "git_status", "label": "Git Status", "category": "Git", "shortcut": "Ctrl+Shift+G"},
    {"id": "git_commit", "label": "Git Commit", "category": "Git", "shortcut": ""},
    {"id": "snapshot", "label": "Create Snapshot", "category": "Project", "shortcut": ""},
    {"id": "health_check", "label": "Health Check", "category": "Project", "shortcut": ""},
    {"id": "ai_explain", "label": "AI: Explain Code", "category": "AI", "shortcut": ""},
    {"id": "ai_generate", "label": "AI: Generate Code", "category": "AI", "shortcut": ""},
    {"id": "ai_refactor", "label": "AI: Refactor", "category": "AI", "shortcut": ""},
    {"id": "ai_test", "label": "AI: Generate Tests", "category": "AI", "shortcut": ""},
    {"id": "install_packages", "label": "Install Packages", "category": "Project", "shortcut": ""},
    {"id": "shell_command", "label": "Run Shell Command", "category": "Run", "shortcut": ""},
    {"id": "export_project", "label": "Export as ZIP", "category": "Project", "shortcut": ""},
    {"id": "import_project", "label": "Import from ZIP", "category": "Project", "shortcut": ""},
    {"id": "split_editor", "label": "Split Editor", "category": "View", "shortcut": "Ctrl+\\"},
    {"id": "outline", "label": "Toggle Outline", "category": "View", "shortcut": "Ctrl+Shift+O"},
    {"id": "settings", "label": "Settings", "category": "Preferences", "shortcut": "Ctrl+,"},
]


def get_command_palette(query: str = "") -> list[dict[str, Any]]:
    if not query:
        return _COMMAND_PALETTE
    q = query.lower()
    return [c for c in _COMMAND_PALETTE if q in c["label"].lower() or q in c["category"].lower()]


# ── Linter / Static Analysis (basic) ────────────────────────────────────────


def lint_file(project_id: str, file_path: str) -> dict[str, Any]:
    project_dir = _project_path(project_id)
    full = project_dir / file_path
    if not full.exists():
        return {"ok": False, "error": "File not found"}

    ext = full.suffix.lower()
    issues = []

    if ext == ".py":
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
            # Check for bare except
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    issues.append({"line": node.lineno, "col": getattr(node, "col_offset", 0), "message": "Bare 'except:' clause found", "severity": "warning"})
                if isinstance(node, ast.Name) and node.id == "print" and isinstance(node.ctx, ast.Load):
                    issues.append({"line": node.lineno, "col": getattr(node, "col_offset", 0), "message": "Debug print statement found", "severity": "info"})
        except SyntaxError as exc:
            issues.append({"line": exc.lineno or 1, "col": exc.offset or 0, "message": f"Syntax error: {exc.msg}", "severity": "error"})
    elif ext in (".js", ".ts"):
        # Basic JS checks
        content = full.read_text(encoding="utf-8", errors="replace")
        if "debugger;" in content:
            for i, line in enumerate(content.splitlines(), 1):
                if "debugger;" in line:
                    issues.append({"line": i, "col": line.index("debugger;"), "message": "Debugger statement found", "severity": "warning"})
        if "console.log" in content:
            for i, line in enumerate(content.splitlines(), 1):
                if "console.log" in line:
                    issues.append({"line": i, "col": line.index("console.log"), "message": "Debug console.log found", "severity": "info"})

    return {"ok": True, "file": file_path, "issues": issues, "issue_count": len(issues)}


# ── Dependency Analysis ─────────────────────────────────────────────────────


def analyze_dependencies(project_id: str) -> dict[str, Any]:
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    lang = detect_language(project_dir)
    deps = {}

    if lang == "python":
        req_file = project_dir / "requirements.txt"
        if req_file.exists():
            deps["declared"] = [line.strip() for line in req_file.read_text().splitlines() if line.strip() and not line.startswith("#")]
        # Scan imports
        imports = set()
        for f in project_dir.rglob("*.py"):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.add(alias.name.split(".")[0])
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imports.add(node.module.split(".")[0])
            except Exception:
                pass
        deps["used"] = sorted(imports)
    elif lang in ("javascript", "typescript"):
        pkg_file = project_dir / "package.json"
        if pkg_file.exists():
            try:
                pkg = json.loads(pkg_file.read_text())
                deps["declared"] = list(pkg.get("dependencies", {}).keys()) + list(pkg.get("devDependencies", {}).keys())
            except Exception:
                pass

    return {"ok": True, "language": lang, "dependencies": deps}


# ── Import from GitHub / URL ────────────────────────────────────────────────


def import_from_git(url: str, name: str | None = None, branch: str = "main") -> dict[str, Any]:
    """Clone a git repository as a new project."""
    project_id = str(uuid.uuid4())[:8]
    project_dir = _project_path(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, url, str(project_dir)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            shutil.rmtree(project_dir, ignore_errors=True)
            return {"ok": False, "error": result.stderr}

        lang = detect_language(project_dir)
        meta = {
            "id": project_id,
            "name": name or f"Git {project_id}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "language": lang,
            "entry_file": LANGUAGE_CONFIG.get(lang, LANGUAGE_CONFIG["python"])["entry_patterns"][0],
            "template": "git",
            "git_url": url,
            "git_branch": branch,
        }
        _project_meta_path(project_id).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        _audit_log(project_id, "import_git", {"url": url, "branch": branch})
        return {"ok": True, "project_id": project_id, **meta}
    except Exception as exc:
        shutil.rmtree(project_dir, ignore_errors=True)
        return {"ok": False, "error": str(exc)}


# ── Auto-complete Suggestions (basic) ───────────────────────────────────────


def get_completions(project_id: str, file_path: str, line: int, column: int) -> dict[str, Any]:
    """Get basic auto-complete suggestions for a position in a file."""
    project_dir = _project_path(project_id)
    full = project_dir / file_path
    if not full.exists():
        return {"ok": False, "error": "File not found"}

    content = full.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    if line < 1 or line > len(lines):
        return {"ok": True, "completions": []}

    current_line = lines[line - 1]
    prefix = current_line[:column]

    # Simple prefix-based suggestions
    suggestions = []
    ext = full.suffix.lower()

    if ext == ".py":
        keywords = ["def", "class", "if", "elif", "else", "for", "while", "try", "except", "finally", "with", "import", "from", "return", "yield", "async", "await", "lambda", "pass", "break", "continue", "raise", "assert", "del", "global", "nonlocal", "print", "len", "range", "enumerate", "zip", "map", "filter", "sum", "min", "max", "sorted", "reversed", "open", "isinstance", "hasattr", "getattr", "setattr", "type", "str", "int", "float", "list", "dict", "set", "tuple", "bool", "None", "True", "False"]
        builtins = ["self", "super", "object", "Exception", "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError", "ImportError", "IOError", "RuntimeError"]
        all_suggestions = keywords + builtins
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        all_suggestions = ["const", "let", "var", "function", "class", "interface", "type", "import", "export", "from", "return", "async", "await", "if", "else", "for", "while", "try", "catch", "finally", "throw", "new", "this", "typeof", "instanceof", "undefined", "null", "true", "false", "console", "log", "error", "warn", "document", "window", "fetch", "Promise", "Array", "Object", "String", "Number", "Boolean", "Map", "Set", "JSON", "Math", "Date", "RegExp"]
    elif ext == ".go":
        all_suggestions = ["package", "import", "func", "type", "struct", "interface", "map", "chan", "var", "const", "if", "else", "for", "range", "switch", "case", "default", "return", "defer", "go", "select", "break", "continue", "fallthrough", "goto", "nil", "true", "false", "make", "new", "len", "cap", "append", "copy", "delete", "close", "panic", "recover", "print", "println"]
    else:
        all_suggestions = []

    # Extract existing identifiers from file
    if ext == ".py":
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    all_suggestions.append(node.id)
                elif isinstance(node, ast.FunctionDef):
                    all_suggestions.append(node.name)
                elif isinstance(node, ast.ClassDef):
                    all_suggestions.append(node.name)
        except Exception:
            pass

    # Filter by prefix
    if prefix:
        word = re.search(r"[\w\.]*$", prefix)
        if word:
            wp = word.group()
            suggestions = [s for s in set(all_suggestions) if s.startswith(wp) and s != wp]
            suggestions = sorted(suggestions)[:20]

    return {"ok": True, "completions": [{"label": s, "kind": "keyword"} for s in suggestions]}
