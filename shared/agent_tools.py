"""Agentic tool layer for SHIMS Omni — the *hands* that let the assistant act.

This is what turns Omni from "a chat that can make a PDF" into a Claude-Code-class
desktop coworker: it can **run any command**, **read / write / edit / move / delete
files anywhere on the machine**, **run code**, **browse the web**, **spawn a
background coder**, **learn new skills**, and **modify its own source**.

Safety model (chosen by the user — "ask before risky acts"):
  * Reading, searching, listing, running safe/inspection commands, running
    sandboxed code, web search → run **immediately** (risk = ``safe``).
  * Writing / editing / deleting / moving files **outside the SHIMS repo** (or
    inside an explicitly-allowed extra root), destructive shell commands, and
    editing Omni's own source / self-patching → **gated**: the caller turns the
    call into a *pending action* and asks the human for one-click approval,
    reusing the existing approval / action-ledger machinery in
    ``backend/app/main.py``.

Each tool declares: a name, a human description, a JSON-schema for its arguments,
a *risk classifier* (``risk(args) -> "safe" | "gated"``), and a synchronous
``run(args) -> dict`` executor. The agent loop (``shared/agent_loop.py``) calls
:func:`run_tool`, which enforces the gate. Nothing here imports ``backend`` — the
backend imports *this* — so there is no circular dependency.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import ROOT_DIR, STORAGE_DIR, settings
from .security import new_id

# --------------------------------------------------------------------------- #
# Roots & path classification
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(ROOT_DIR).resolve()
_ROOTS_STATE = STORAGE_DIR / "state" / "agent_roots.json"
_ROOTS_STATE.parent.mkdir(parents=True, exist_ok=True)
_EDIT_UNDO_DIR = STORAGE_DIR / "agent_edits"
_EDIT_UNDO_DIR.mkdir(parents=True, exist_ok=True)

# Inside the repo, these top-level folders are "scratch" — safe to write to
# without approval. Everything else inside the repo is treated as source/config
# and is gated (so the agent can't silently rewrite its own code or .env).
_SCRATCH_TOP = {
    "storage", "workspace", "generated", "logs", "data", "release_checks",
    "tmp", "temp", "output", "outputs", ".gradle-cache", ".gradle-dist",
    ".android-sdk", ".pytest_cache",
}

# Read-only / inspection commands that are safe to run with no approval. We match
# the FIRST token of the command (case-insensitive). Anything not in this set —
# or any command containing a destructive token / redirection — is gated.
_SAFE_SHELL_HEADS = {
    "ls", "dir", "gci", "get-childitem", "tree", "pwd", "cd", "gl", "get-location",
    "cat", "type", "gc", "get-content", "head", "tail", "more", "less",
    "find", "findstr", "grep", "rg", "fd", "select-string", "sls",
    "echo", "printf", "wc", "stat", "file", "du", "df",
    "where", "which", "get-command", "whoami", "hostname", "date", "uptime",
    "python", "python3", "py", "node", "deno", "go", "java", "dotnet", "rustc",
    "pip", "pip3", "npm", "pnpm", "yarn", "poetry", "uv", "conda",
    "git", "gh", "ollama", "docker", "kubectl", "curl", "http", "ping",
}
# Tokens that make ANY command gated regardless of head.
_DANGER_TOKENS = {
    "rm", "del", "erase", "rmdir", "rd", "remove-item", "ri", "unlink",
    "mv", "move", "rename", "ren", "rename-item",
    "format", "mkfs", "dd", "fdisk", "diskpart",
    "shutdown", "reboot", "restart-computer", "stop-computer", "halt",
    "set-content", "sc", "out-file", "add-content", "new-item", "ni",
    "kill", "taskkill", "stop-process", "spps", "pkill",
    "reg", "regedit", "schtasks", "sc.exe",
    ">", ">>", "rm-rf", "rm-rf*",
}
# Sub-commands that flip an otherwise-safe head into "gated".
_GATED_SUBCMDS = {
    "pip": {"install", "uninstall"}, "pip3": {"install", "uninstall"},
    "npm": {"install", "i", "uninstall", "remove", "publish", "ci"},
    "pnpm": {"install", "add", "remove"}, "yarn": {"add", "remove", "install"},
    "git": {"push", "reset", "clean", "rebase", "commit", "rm", "checkout", "merge", "stash"},
    "docker": {"rm", "rmi", "system", "volume", "kill", "stop"},
    "ollama": {"rm", "pull", "create"},
    "go": {"install"}, "dotnet": {"add", "remove"}, "conda": {"install", "remove"},
    "curl": {"-o", "-O", "--output"}, "poetry": {"add", "remove"}, "uv": {"add", "remove", "pip"},
}


def _load_roots() -> list[str]:
    try:
        data = json.loads(_ROOTS_STATE.read_text(encoding="utf-8"))
        return [str(p) for p in data.get("roots", [])]
    except Exception:
        return []


def list_allowed_roots() -> list[str]:
    """Extra folders (outside the repo) the user has marked as safe-to-write."""
    return _load_roots()


def add_allowed_root(path: str) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    roots = set(_load_roots())
    roots.add(str(p))
    _ROOTS_STATE.write_text(json.dumps({"roots": sorted(roots)}, indent=2), encoding="utf-8")
    return {"ok": True, "added": str(p), "roots": sorted(roots)}


def remove_allowed_root(path: str) -> dict[str, Any]:
    p = str(Path(path).expanduser().resolve())
    roots = [r for r in _load_roots() if r != p]
    _ROOTS_STATE.write_text(json.dumps({"roots": sorted(roots)}, indent=2), encoding="utf-8")
    return {"ok": True, "removed": p, "roots": sorted(roots)}


def _resolve(path: str) -> Path:
    """Resolve a possibly-relative path against the repo root."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p.resolve()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def path_class(path: str | Path) -> str:
    """Classify a path: 'repo_scratch' | 'repo_source' | 'allowed_root' | 'outside'."""
    p = _resolve(str(path))
    if _is_within(p, REPO_ROOT):
        try:
            first = p.relative_to(REPO_ROOT).parts[0] if p != REPO_ROOT else ""
        except Exception:
            first = ""
        return "repo_scratch" if first in _SCRATCH_TOP else "repo_source"
    for root in _load_roots():
        try:
            if _is_within(p, Path(root)):
                return "allowed_root"
        except Exception:
            continue
    return "outside"


def _write_risk(path: str | Path) -> str:
    """Risk for a write/edit/mkdir/move-destination on ``path``."""
    from .config import settings
    if settings.omnipotent_mode:
        return "safe"
    return "safe" if path_class(path) in {"repo_scratch", "allowed_root"} else "gated"


def _delete_risk(path: str | Path) -> str:
    """Deletes are riskier: only scratch areas are auto-safe."""
    from .config import settings
    if settings.omnipotent_mode:
        return "safe"
    return "safe" if path_class(path) == "repo_scratch" else "gated"


# --------------------------------------------------------------------------- #
# Shell
# --------------------------------------------------------------------------- #
def _shell_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=(os.name != "nt"))
    except Exception:
        return command.split()


def _shell_risk(args: dict[str, Any]) -> str:
    from .config import settings
    if settings.omnipotent_mode:
        return "safe"
    command = str(args.get("command") or "")
    cwd = args.get("cwd")
    low = command.lower()
    toks = [t.lower() for t in _shell_tokens(command)]
    if not toks:
        return "gated"
    # Any redirection / destructive token anywhere → gated.
    if any(sym in low for sym in (">", ">>")) or any(t in _DANGER_TOKENS for t in toks):
        return "gated"
    head = toks[0]
    # strip a leading path (e.g. ./script, C:\\python.exe) down to the binary name
    head_name = Path(head).name
    sub = toks[1] if len(toks) > 1 else ""
    for key, bad in _GATED_SUBCMDS.items():
        if head_name.startswith(key) and sub in bad:
            return "gated"
    if head_name in _SAFE_SHELL_HEADS or any(head_name.startswith(h) for h in _SAFE_SHELL_HEADS):
        # also require the working dir (if given) to be readable; writing happens
        # via redirection which we already gated above.
        if cwd and _write_risk(cwd) == "gated" and any(t in _DANGER_TOKENS for t in toks):
            return "gated"
        return "safe"
    return "gated"  # unknown command → caution, ask first


def _run_shell(args: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        return {"ok": False, "error": "empty command"}
    cwd = args.get("cwd")
    workdir = _resolve(cwd) if cwd else REPO_ROOT
    timeout = min(int(args.get("timeout") or 90), 600)
    if os.name == "nt":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        argv = ["bash", "-lc", command]
    started = time.time()
    try:
        proc = subprocess.run(
            argv, cwd=str(workdir), capture_output=True, text=True,
            timeout=timeout, env=os.environ.copy(),
        )
        out, err = (proc.stdout or "")[-12000:], (proc.stderr or "")[-8000:]
        return {
            "ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": out, "stderr": err, "command": command, "cwd": str(workdir),
            "elapsed_s": round(time.time() - started, 2),
        }
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "returncode": -1, "stdout": (exc.stdout or "")[-6000:] if isinstance(exc.stdout, str) else "",
                "stderr": f"Command timed out after {timeout}s.", "command": command, "cwd": str(workdir)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400], "command": command, "cwd": str(workdir)}


# --------------------------------------------------------------------------- #
# Filesystem (whole-machine, gated by path)
# --------------------------------------------------------------------------- #
_TEXT_READ_LIMIT = 400_000


def _run_fs_read(args: dict[str, Any]) -> dict[str, Any]:
    p = _resolve(str(args.get("path") or ""))
    if not p.is_file():
        return {"ok": False, "error": "not a file", "path": str(p)}
    try:
        raw = p.read_bytes()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "path": str(p)}
    head = raw[:_TEXT_READ_LIMIT]
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "binary file", "size": len(raw), "path": str(p)}
    return {"ok": True, "path": str(p), "text": text, "size": len(raw),
            "truncated": len(raw) > _TEXT_READ_LIMIT, "class": path_class(p)}


def _run_fs_list(args: dict[str, Any]) -> dict[str, Any]:
    p = _resolve(str(args.get("path") or "."))
    if not p.exists():
        return {"ok": False, "error": "not found", "path": str(p)}
    if p.is_file():
        return {"ok": True, "path": str(p), "entries": [{"name": p.name, "is_dir": False, "size": p.stat().st_size}]}
    entries: list[dict[str, Any]] = []
    for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            entries.append({"name": child.name, "is_dir": child.is_dir(),
                            "size": child.stat().st_size if child.is_file() else 0})
        except Exception:
            continue
        if len(entries) >= 1000:
            break
    return {"ok": True, "path": str(p), "count": len(entries), "entries": entries}


def _run_fs_glob(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve(str(args.get("root") or "."))
    pattern = str(args.get("pattern") or "*")
    hits: list[str] = []
    try:
        for hit in root.glob(pattern):
            hits.append(str(hit))
            if len(hits) >= 500:
                break
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "root": str(root), "pattern": pattern, "count": len(hits), "matches": hits}


def _run_fs_watch(args: dict[str, Any]) -> dict[str, Any]:
    """Watch a directory for changes and return new/modified files since last check."""
    import hashlib
    import time
    path = _resolve(str(args.get("path") or "."))
    if not path.exists() or not path.is_dir():
        return {"ok": False, "error": "path not found or not a directory", "path": str(path)}
    watch_state_dir = STORAGE_DIR / "state" / "fs_watch"
    watch_state_dir.mkdir(parents=True, exist_ok=True)
    state_file = watch_state_dir / f"watch_{hashlib.md5(str(path).encode()).hexdigest()[:12]}.json"
    old_state: dict[str, Any] = {}
    if state_file.exists():
        try:
            old_state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            old_state = {}
    current: dict[str, Any] = {}
    changed: list[dict[str, Any]] = []
    new_files: list[dict[str, Any]] = []
    for item in path.rglob("*"):
        if item.is_file():
            rel = str(item.relative_to(path)).replace("\\", "/")
            try:
                stat = item.stat()
                mtime = stat.st_mtime
                size = stat.st_size
                current[rel] = {"mtime": mtime, "size": size}
                if rel not in old_state:
                    new_files.append({"path": rel, "size": size, "mtime": mtime})
                elif old_state[rel].get("mtime") != mtime or old_state[rel].get("size") != size:
                    changed.append({"path": rel, "size": size, "mtime": mtime, "old_size": old_state[rel].get("size")})
            except Exception:
                continue
    state_file.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "path": str(path),
        "new_files": new_files,
        "changed": changed,
        "total_tracked": len(current),
        "checked_at": time.time(),
    }


_SEARCHABLE = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html", ".css",
               ".yaml", ".yml", ".java", ".kt", ".c", ".cpp", ".h", ".go", ".rs", ".sql", ".sh", ".bat"}


def _run_fs_search(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve(str(args.get("root") or "."))
    query = str(args.get("query") or "")
    if not query:
        return {"ok": False, "error": "empty query"}
    ql = query.lower()
    name_hits: list[str] = []
    content_hits: list[dict[str, Any]] = []
    scanned = 0
    for p in root.rglob("*"):
        if scanned > 20000:
            break
        if not p.is_file() or any(part in {".git", "__pycache__", "node_modules", ".venv"} for part in p.parts):
            continue
        scanned += 1
        if ql in p.name.lower():
            name_hits.append(str(p))
        elif p.suffix.lower() in _SEARCHABLE:
            try:
                if p.stat().st_size < 3_000_000:
                    for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if ql in line.lower():
                            content_hits.append({"path": str(p), "line": i, "text": line.strip()[:200]})
                            break
            except Exception:
                continue
        if len(name_hits) + len(content_hits) >= 200:
            break
    return {"ok": True, "root": str(root), "query": query,
            "name_matches": name_hits[:100], "content_matches": content_hits[:120]}


def _backup_before_write(p: Path) -> str | None:
    """Snapshot existing file content so an edit/write is reversible."""
    if not p.is_file():
        return None
    try:
        undo_id = new_id("edit")
        snap = _EDIT_UNDO_DIR / f"{undo_id}.json"
        snap.write_text(json.dumps({
            "path": str(p), "at": time.time(),
            "content": p.read_text(encoding="utf-8", errors="replace"),
        }, ensure_ascii=False), encoding="utf-8")
        return undo_id
    except Exception:
        return None


def _run_fs_write(args: dict[str, Any]) -> dict[str, Any]:
    p = _resolve(str(args.get("path") or ""))
    content = args.get("content")
    if content is None:
        return {"ok": False, "error": "missing content"}
    if path_class(p) == "repo_source":
        return {"ok": False, "error": "This path is SHIMS's own source/config. Use the self.patch tool so the change is validated in a sandbox first.", "path": str(p)}
    undo_id = _backup_before_write(p)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "path": str(p)}
    return {"ok": True, "path": str(p), "bytes": len(str(content)), "undo_id": undo_id, "class": path_class(p)}


def _run_fs_edit(args: dict[str, Any]) -> dict[str, Any]:
    p = _resolve(str(args.get("path") or ""))
    find = args.get("find")
    replace = args.get("replace")
    if find is None or replace is None:
        return {"ok": False, "error": "fs.edit needs 'find' and 'replace'"}
    if path_class(p) == "repo_source":
        return {"ok": False, "error": "This path is SHIMS's own source/config. Use the self.patch tool instead.", "path": str(p)}
    if not p.is_file():
        return {"ok": False, "error": "not a file", "path": str(p)}
    original = p.read_text(encoding="utf-8", errors="replace")
    count = original.count(str(find))
    if count == 0:
        return {"ok": False, "error": "find string not present", "path": str(p)}
    undo_id = _backup_before_write(p)
    updated = original.replace(str(find), str(replace))
    p.write_text(updated, encoding="utf-8")
    return {"ok": True, "path": str(p), "replacements": count, "undo_id": undo_id}


def _run_fs_mkdir(args: dict[str, Any]) -> dict[str, Any]:
    p = _resolve(str(args.get("path") or ""))
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "path": str(p)}
    return {"ok": True, "path": str(p)}


def _run_fs_move(args: dict[str, Any]) -> dict[str, Any]:
    import shutil
    src = _resolve(str(args.get("src") or ""))
    dst = _resolve(str(args.get("dst") or ""))
    if not src.exists():
        return {"ok": False, "error": "source not found", "src": str(src)}
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}
    return {"ok": True, "src": str(src), "dst": str(dst)}


def _move_risk(args: dict[str, Any]) -> str:
    src, dst = args.get("src") or "", args.get("dst") or ""
    return "gated" if "gated" in (_delete_risk(src), _write_risk(dst)) else "safe"


def _run_fs_delete(args: dict[str, Any]) -> dict[str, Any]:
    import shutil
    p = _resolve(str(args.get("path") or ""))
    if not p.exists():
        return {"ok": False, "error": "not found", "path": str(p)}
    undo_id = _backup_before_write(p) if p.is_file() else None
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "path": str(p)}
    return {"ok": True, "deleted": str(p), "undo_id": undo_id}


def undo_edit(undo_id: str) -> dict[str, Any]:
    snap = _EDIT_UNDO_DIR / f"{undo_id}.json"
    if not snap.exists():
        return {"ok": False, "error": "no such undo id"}
    data = json.loads(snap.read_text(encoding="utf-8"))
    p = Path(data["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data.get("content", ""), encoding="utf-8")
    return {"ok": True, "restored": str(p)}


# --------------------------------------------------------------------------- #
# Code execution
# --------------------------------------------------------------------------- #
def _run_code(args: dict[str, Any]) -> dict[str, Any]:
    language = str(args.get("language") or "python").lower()
    source = str(args.get("source") or "")
    if not source.strip():
        return {"ok": False, "error": "empty source"}
    if language in {"python", "py"}:
        from .code_sandbox import run_python_code
        res = run_python_code(source, filename="main.py")
        res["ok"] = res.get("status") == "passed"
        return res
    # other languages: write to a temp file in the sandbox and run
    from .config import SANDBOX_DIR
    run_dir = SANDBOX_DIR / new_id("run")
    run_dir.mkdir(parents=True, exist_ok=True)
    ext = {"javascript": ".js", "js": ".js", "node": ".js", "bash": ".sh", "sh": ".sh", "powershell": ".ps1"}.get(language, ".txt")
    runner = {".js": ["node"], ".sh": ["bash"], ".ps1": ["powershell", "-NoProfile", "-File"]}.get(ext)
    if not runner:
        return {"ok": False, "error": f"unsupported language: {language}"}
    f = run_dir / f"main{ext}"
    f.write_text(source, encoding="utf-8")
    try:
        proc = subprocess.run(runner + [str(f)], cwd=str(run_dir), capture_output=True,
                              text=True, timeout=settings.code_timeout_seconds)
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-6000:], "stderr": (proc.stderr or "")[-6000:], "language": language}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "language": language}


# --------------------------------------------------------------------------- #
# Web
# --------------------------------------------------------------------------- #
def _run_web_search(args: dict[str, Any]) -> dict[str, Any]:
    import httpx
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "empty query"}
    max_results = min(int(args.get("max_results") or 6), 12)
    searxng = os.getenv("SHIMS_SEARXNG_URL", "").strip()
    results: list[dict[str, Any]] = []
    try:
        if searxng:
            with httpx.Client(timeout=20) as c:
                r = c.get(f"{searxng.rstrip('/')}/search",
                          params={"q": query, "format": "json"}, headers={"Accept": "application/json"})
                r.raise_for_status()
                for item in (r.json().get("results") or [])[:max_results]:
                    results.append({"title": item.get("title"), "url": item.get("url"), "snippet": item.get("content")})
        if not results:
            with httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0 SHIMS"}) as c:
                r = c.get("https://duckduckgo.com/html/", params={"q": query})
                import re as _re
                import html as _html
                for m in _re.finditer(r'result__a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, _re.S):
                    url, title = m.group(1), _re.sub("<[^>]+>", "", m.group(2))
                    results.append({"title": _html.unescape(title.strip()), "url": _html.unescape(url), "snippet": ""})
                    if len(results) >= max_results:
                        break
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "query": query}
    return {"ok": True, "query": query, "count": len(results), "results": results,
            "provider": "searxng" if searxng else "duckduckgo"}


def _run_web_fetch(args: dict[str, Any]) -> dict[str, Any]:
    import httpx
    import re as _re
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "url must start with http(s)://"}
    try:
        with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 SHIMS"}) as c:
            r = c.get(url)
            r.raise_for_status()
            body = r.text
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "url": url}
    text = _re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
    text = _re.sub(r"(?s)<[^>]+>", " ", text)
    import html as _html
    text = _html.unescape(_re.sub(r"\s+", " ", text)).strip()
    return {"ok": True, "url": url, "chars": len(text), "text": text[:8000]}


# --------------------------------------------------------------------------- #
# Browser Agent — "Kimi Claw" for the web
# --------------------------------------------------------------------------- #
def _run_browser_visit(args: dict[str, Any]) -> dict[str, Any]:
    from .browser_agent import visit
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "url must start with http(s)://"}
    return _run_sync(visit(url, wait_for=str(args.get("wait_for") or ""), scroll=bool(args.get("scroll", True))))


def _run_browser_search(args: dict[str, Any]) -> dict[str, Any]:
    from .browser_agent import search
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query required"}
    return _run_sync(search(query, max_results=min(int(args.get("max_results") or 8), 12)))


def _run_browser_click(args: dict[str, Any]) -> dict[str, Any]:
    from .browser_agent import click
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "url must start with http(s)://"}
    return _run_sync(click(url, selector=str(args.get("selector") or ""), text=str(args.get("text") or "")))


def _run_browser_extract(args: dict[str, Any]) -> dict[str, Any]:
    from .browser_agent import extract
    url = str(args.get("url") or "").strip()
    selector = str(args.get("selector") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "url must start with http(s)://"}
    if not selector:
        return {"ok": False, "error": "CSS selector required"}
    return _run_sync(extract(url, selector))


def _run_browser_fill_form(args: dict[str, Any]) -> dict[str, Any]:
    from .browser_agent import fill_form
    url = str(args.get("url") or "").strip()
    fields = args.get("fields") or {}
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "url must start with http(s)://"}
    return _run_sync(fill_form(url, fields, submit_selector=str(args.get("submit_selector") or "")))


def _run_browser_screenshot(args: dict[str, Any]) -> dict[str, Any]:
    from .browser_agent import screenshot
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "url must start with http(s)://"}
    return _run_sync(screenshot(url, selector=str(args.get("selector") or ""), full_page=bool(args.get("full_page", False))))


def _run_browser_scroll(args: dict[str, Any]) -> dict[str, Any]:
    from .browser_agent import scroll
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "url must start with http(s)://"}
    return _run_sync(scroll(url, direction=str(args.get("direction") or "down"), amount=int(args.get("amount") or 800)))


# --------------------------------------------------------------------------- #
# Mailbox / Gmail tools
# --------------------------------------------------------------------------- #
def _run_mailbox_send(args: dict[str, Any]) -> dict[str, Any]:
    from .mailbox import send_gmail_message
    to = str(args.get("to") or "").strip()
    subject = str(args.get("subject") or "").strip()
    body = str(args.get("body") or "").strip()
    if not to or not subject:
        return {"ok": False, "error": "to and subject required"}
    try:
        result = send_gmail_message(to=to, subject=subject, body=body)
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_mailbox_digest(args: dict[str, Any]) -> dict[str, Any]:
    from .mailbox import mailbox_digest
    try:
        limit = min(int(args.get("limit") or 20), 50)
        return {"ok": True, **mailbox_digest(limit=limit)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_mailbox_organize(args: dict[str, Any]) -> dict[str, Any]:
    """Organize Gmail by applying labels based on criteria. Requires OAuth."""
    from .mailbox import get_access_token, sync_gmail_metadata
    criteria = str(args.get("criteria") or "").strip()
    action = str(args.get("action") or "archive").strip().lower()
    if not criteria:
        return {"ok": False, "error": "criteria required (e.g. 'from:newsletter@example.com older_than:7d')"}
    token = get_access_token()
    if not token:
        return {"ok": False, "error": "Gmail not connected. Run /mailbox/oauth in Settings first."}
    try:
        # Sync matching messages
        sync_result = sync_gmail_metadata(access_token=token, query=criteria, max_results=50)
        messages = sync_result.get("messages", [])
        if not messages:
            return {"ok": True, "organized": 0, "note": "No messages matched criteria."}
        # For now, report what would be organized (full label modification requires Gmail modify scope)
        return {
            "ok": True,
            "organized": len(messages),
            "action": action,
            "criteria": criteria,
            "note": f"Found {len(messages)} messages matching '{criteria}'. Apply action '{action}' via Gmail API (modify scope required for live apply).",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


# --------------------------------------------------------------------------- #
# Enterprise bridge tools
# --------------------------------------------------------------------------- #

def _enterprise_disabled() -> dict[str, Any]:
    return {
        "ok": False,
        "enabled": False,
        "error": "Enterprise integration is not configured. Set SHIMS_ENTERPRISE_URL and SHIMS_ENTERPRISE_PAIRING_ENABLED=true to enable.",
    }


def _run_enterprise_command(args: dict[str, Any]) -> dict[str, Any]:
    """Send a command to the paired SHIMS Enterprise instance."""
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    import httpx
    cmd = str(args.get("command") or "").strip()
    payload = args.get("payload") or {}
    if not cmd:
        return {"ok": False, "error": "command required"}
    url = getattr(settings, "enterprise_url", "http://127.0.0.1:8020").rstrip("/")
    token = getattr(settings, "bridge_token", "") or ""
    try:
        r = httpx.post(
            f"{url}/api/bridge/command",
            json={"command": cmd, "payload": payload},
            headers={"X-Bridge-Token": token},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        # Enterprise uses either `ok` or `status` to signal success.
        success = data.get("ok") or str(data.get("status", "")).lower() in {"ok", "success"}
        return {"ok": success, **data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260], "enterprise_url": url}


# --------------------------------------------------------------------------- #
# Coder (background job) / skills
# --------------------------------------------------------------------------- #
def _run_coder_spawn(args: dict[str, Any]) -> dict[str, Any]:
    """Queue a background coder job (plan→write→run→fix). Returns the job id."""
    from . import omni_brain
    goal = str(args.get("goal") or "").strip()
    if not goal:
        return {"ok": False, "error": "missing goal"}
    name = str(args.get("name") or goal[:48] or "coder job")
    res = omni_brain.schedule_task("coder_job", f"Coder: {name}", {"goal": goal, "name": name}, priority=3)
    job_id = res.get("task_id")
    return {"ok": True, "job_id": job_id, "goal": goal, "name": name,
            "note": "Coder is building this in the background. Watch it live in the job card.",
            "stream_url": f"/agent/jobs/{job_id}/stream"}


def _run_coder_status(args: dict[str, Any]) -> dict[str, Any]:
    from . import omni_brain
    job_id = args.get("job_id")
    for t in omni_brain.list_tasks(limit=100):
        if str(t.get("id")) == str(job_id):
            return {"ok": True, "job": t}
    return {"ok": False, "error": "job not found", "job_id": job_id}


def _run_skill_learn(args: dict[str, Any]) -> dict[str, Any]:
    from .skills import save_skill
    name = str(args.get("name") or "").strip()
    instructions = str(args.get("instructions") or args.get("summary") or "").strip()
    if not name or not instructions:
        return {"ok": False, "error": "skill.learn needs 'name' and 'instructions'"}
    sk = save_skill(name, instructions, body=str(args.get("body") or ""),
                    tags=(args.get("tags") or ["learned"]), source="agent_loop")
    return {"ok": True, "skill": {"id": sk["id"], "name": sk["name"]}}


def _run_skill_create_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Turn a code snippet into a dynamic tool skill."""
    from .skills import save_skill
    from .skill_runtime import register_all_skill_tools
    name = str(args.get("name") or "").strip()
    tool_name = str(args.get("tool_name") or "").strip()
    description = str(args.get("description") or "").strip()
    code = str(args.get("code") or args.get("tool_code") or "").strip()
    if not name or not tool_name or not code:
        return {"ok": False, "error": "skill.create_tool needs 'name', 'tool_name', and 'code'"}
    params = args.get("parameters") or {"type": "object", "properties": {}}
    sk = save_skill(
        name,
        description or f"Dynamic tool {tool_name}",
        runtime="tool",
        tool_name=tool_name,
        tool_schema={"name": tool_name, "description": description or name, "parameters": params},
        tool_code=code,
        tags=(args.get("tags") or ["dynamic_tool", "learned"]),
        source="agent_loop",
    )
    reg = register_all_skill_tools()
    return {"ok": True, "skill": {"id": sk["id"], "name": sk["name"]}, "registered": reg}


def _run_skill_execute(args: dict[str, Any]) -> dict[str, Any]:
    """Run a skill by ID or name."""
    from .skill_runtime import execute_skill
    from .skills import list_skills
    skill_id = str(args.get("skill_id") or "").strip()
    name = str(args.get("name") or "").strip()
    if not skill_id and not name:
        return {"ok": False, "error": "skill.execute needs 'skill_id' or 'name'"}
    if not skill_id and name:
        for sk in list_skills(limit=200):
            if sk.get("name", "").strip().lower() == name.lower():
                skill_id = sk.get("id")
                break
    if not skill_id:
        return {"ok": False, "error": f"skill not found: {name}"}
    return execute_skill(skill_id, args.get("args") or {})


def _run_skill_list(args: dict[str, Any]) -> dict[str, Any]:
    from .skills import list_skills
    query = str(args.get("query") or "").strip()
    return {"ok": True, "skills": list_skills(query=query or None, limit=args.get("limit", 50))}


# --------------------------------------------------------------------------- #
# Coder v3 project tools (integrated into chat agent loop)
# --------------------------------------------------------------------------- #
def _run_coder_create_project(args: dict[str, Any]) -> dict[str, Any]:
    from . import coder_v2
    name = str(args.get("name") or "").strip()
    template = str(args.get("template") or "").strip() or None
    if not name:
        return {"ok": False, "error": "name required"}
    result = coder_v2.create_project(name, template=template)
    return result


def _run_coder_write_file(args: dict[str, Any]) -> dict[str, Any]:
    from . import coder_v2
    project_id = str(args.get("project_id") or "").strip()
    file_path = str(args.get("file_path") or "").strip()
    content = str(args.get("content") or "")
    if not project_id or not file_path:
        return {"ok": False, "error": "project_id and file_path required"}
    return coder_v2.write_file(project_id, file_path, content)


def _run_coder_read_file(args: dict[str, Any]) -> dict[str, Any]:
    from . import coder_v2
    project_id = str(args.get("project_id") or "").strip()
    file_path = str(args.get("file_path") or "").strip()
    if not project_id or not file_path:
        return {"ok": False, "error": "project_id and file_path required"}
    return coder_v2.read_file(project_id, file_path)


def _run_coder_run_shell(args: dict[str, Any]) -> dict[str, Any]:
    from . import coder_v3
    project_id = str(args.get("project_id") or "").strip()
    command = str(args.get("command") or "").strip()
    if not project_id or not command:
        return {"ok": False, "error": "project_id and command required"}
    return coder_v3.run_shell_command(project_id, command, timeout=args.get("timeout", 60))


def _run_coder_run_project(args: dict[str, Any]) -> dict[str, Any]:
    from . import coder_v2
    project_id = str(args.get("project_id") or "").strip()
    entry_file = str(args.get("entry_file") or "").strip() or None
    if not project_id:
        return {"ok": False, "error": "project_id required"}
    return coder_v2.run_project(project_id, entry_file=entry_file)


def _run_coder_search(args: dict[str, Any]) -> dict[str, Any]:
    from . import coder_v3
    project_id = str(args.get("project_id") or "").strip()
    query = str(args.get("query") or "").strip()
    if not project_id or not query:
        return {"ok": False, "error": "project_id and query required"}
    return coder_v3.search_in_project(project_id, query, regex=bool(args.get("regex", False)),
                                       case_sensitive=bool(args.get("case_sensitive", False)),
                                       file_pattern=str(args.get("file_pattern") or "*"))


def _run_coder_install(args: dict[str, Any]) -> dict[str, Any]:
    from . import coder_v3
    project_id = str(args.get("project_id") or "").strip()
    packages = args.get("packages") or []
    if not project_id or not packages:
        return {"ok": False, "error": "project_id and packages required"}
    return coder_v3.install_packages(project_id, packages, manager=args.get("manager"))


def _run_coder_git_commit(args: dict[str, Any]) -> dict[str, Any]:
    from . import coder_v2
    project_id = str(args.get("project_id") or "").strip()
    message = str(args.get("message") or "").strip()
    if not project_id or not message:
        return {"ok": False, "error": "project_id and message required"}
    return coder_v2.git_commit(project_id, message)


# --------------------------------------------------------------------------- #
# Neural Agent tools (integrated into chat agent loop)
# --------------------------------------------------------------------------- #
def _run_neural_generate_proposal(args: dict[str, Any]) -> dict[str, Any]:
    from . import neural_agent
    intent = str(args.get("intent") or "").strip()
    file_path = str(args.get("file_path") or "").strip()
    instructions = str(args.get("instructions") or "").strip()
    if not intent:
        return {"ok": False, "error": "intent required"}
    return neural_agent.generate_proposal(intent, file_path=file_path, instructions=instructions)


def _run_neural_apply_proposal(args: dict[str, Any]) -> dict[str, Any]:
    from . import neural_agent
    proposal_id = str(args.get("proposal_id") or "").strip()
    if not proposal_id:
        return {"ok": False, "error": "proposal_id required"}
    return neural_agent.apply_proposal(proposal_id)


def _run_neural_test_proposal(args: dict[str, Any]) -> dict[str, Any]:
    from . import neural_agent
    proposal_id = str(args.get("proposal_id") or "").strip()
    if not proposal_id:
        return {"ok": False, "error": "proposal_id required"}
    return neural_agent.test_proposal(proposal_id)


def _run_neural_reflect(args: dict[str, Any]) -> dict[str, Any]:
    from . import neural_agent
    return neural_agent.run_reflection()


def _run_agent_suggest_tools(args: dict[str, Any]) -> dict[str, Any]:
    """Predict which tools are most relevant for a goal or conversation context."""
    goal = str(args.get("goal") or "").strip().lower()
    context = str(args.get("context") or "").strip().lower()
    combined = goal + " " + context
    suggestions: list[dict[str, Any]] = []
    # Keyword-based heuristics for tool recommendation
    tool_keywords: dict[str, list[str]] = {
        "plan.create": ["plan", "steps", "workflow", "automate", "every day", "schedule"],
        "web.search": ["search", "find", "look up", "research", "google"],
        "browser.visit": ["website", "page", "url", "browse", "login"],
        "enterprise.export": ["export", "csv", "excel", "spreadsheet", "download"],
        "enterprise.command": ["enterprise", "factory", "production", "batch", "qms", "lims"],
        "fs.read": ["read file", "open file", "show file", "contents of"],
        "fs.write": ["write file", "create file", "save to"],
        "shell.run": ["run command", "execute", "terminal", "command line"],
        "code.run": ["python", "calculate", "compute", "script"],
        "mail.compose": ["email", "send mail", "compose"],
        "memory.save": ["remember", "save this", "note that"],
        "media.generate_image": ["image", "picture", "photo", "draw", "generate image"],
        "self.patch": ["fix code", "update source", "modify shims"],
        "skill.learn": ["learn this", "remember how", "save as skill"],
        "improvement.run_cycle": ["eval", "improve", "run evals", "benchmark"],
    }
    for tool, keywords in tool_keywords.items():
        score = sum(1 for k in keywords if k in combined)
        if score:
            suggestions.append({"name": tool, "score": score, "reason": f"matched keywords: {[k for k in keywords if k in combined]}"})
    suggestions.sort(key=lambda x: x["score"], reverse=True)
    return {"ok": True, "goal": goal, "suggestions": suggestions[:8]}


def _run_agent_swarm(args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch multiple SHIMS agents in parallel and synthesize a unified answer.

    When ``orchestrate=true`` (the default), the real meta-orchestrator takes over:
    it analyzes the prompt, builds a dependency-aware plan, runs coder/reviewer/tester
    agents in waves, and synthesizes the result. Every agent step is recorded in
    ``events`` so callers can show a live activity log.
    """
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "prompt required"}
    orchestrate = bool(args.get("orchestrate", True))
    use_llm = bool(args.get("use_llm", True))

    if orchestrate:
        from .swarm_orchestrator import run_orchestrated_swarm, SwarmEvent
        events: list[dict[str, Any]] = []

        def _emit(event: SwarmEvent) -> None:
            events.append(event.to_dict())

        result = _run_sync(run_orchestrated_swarm(prompt, emit=_emit, use_llm=use_llm))
        return {
            "ok": result.get("ok", False),
            "synthesis": result.get("synthesis", ""),
            "analysis": result.get("analysis", ""),
            "scratchpad": result.get("scratchpad", {}),
            "events": events,
            "elapsed_ms": result.get("elapsed_ms", 0.0),
            "mode": "orchestrated",
        }

    # Legacy SwarmDispatcher path (kept for backward compatibility)
    from .swarm_runtime import SwarmDispatcher
    agent_ids = args.get("agent_ids")
    if agent_ids is not None and not isinstance(agent_ids, list):
        return {"ok": False, "error": "agent_ids must be a list of strings"}
    context = args.get("context") or {}
    shared_context = args.get("shared_context") or {}
    dispatcher = SwarmDispatcher()
    result = _run_sync(
        dispatcher.dispatch(
            prompt,
            agent_ids=agent_ids,
            context=context,
            shared_context=shared_context,
        )
    )
    return {
        "ok": result.ok,
        "synthesis": result.synthesis,
        "agent_count": result.agent_count,
        "latency_ms": result.latency_ms,
        "mode": "legacy",
        "results": [
            {
                "agent_id": r.agent_id,
                "ok": r.ok,
                "output": r.output,
                "latency_ms": r.latency_ms,
                "error": r.error,
                "tools_used": r.tools_used,
            }
            for r in result.results
        ],
    }


def _run_task_check_status(args: dict[str, Any]) -> dict[str, Any]:
    from . import omni_brain
    task_id = int(args.get("task_id") or 0)
    if not task_id:
        return {"ok": False, "error": "task_id required"}
    task = omni_brain.get_task(task_id)
    if not task:
        return {"ok": False, "error": "Task not found"}
    return {
        "ok": True,
        "task_id": task["id"],
        "task_type": task["task_type"],
        "title": task["title"],
        "status": task["status"],
        "result": task.get("result", {}),
        "updated_at": task["updated_at"],
    }


def _run_task_list(args: dict[str, Any]) -> dict[str, Any]:
    from . import omni_brain
    status = str(args.get("status") or "").strip() or None
    limit = int(args.get("limit") or 20)
    tasks = omni_brain.list_tasks(status=status, limit=limit)
    return {"ok": True, "tasks": tasks, "count": len(tasks)}


# --------------------------------------------------------------------------- #
# Desktop / sandbox tools (integrated into chat agent loop)
# --------------------------------------------------------------------------- #
def _run_desktop_run_python(args: dict[str, Any]) -> dict[str, Any]:
    """Run Python code in a temporary sandbox file."""
    import tempfile
    code = str(args.get("code") or "").strip()
    if not code:
        return {"ok": False, "error": "code required"}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp = f.name
    try:
        result = subprocess.run(
            [sys.executable, tmp],
            capture_output=True, text=True, timeout=args.get("timeout", 30),
            cwd=str(REPO_ROOT)
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out"}
    finally:
        try:
            Path(tmp).unlink()
        except Exception:
            pass


def _run_plan_create(args: dict[str, Any]) -> dict[str, Any]:
    from .desktop_planner import create_plan, plan_from_goal
    goal = str(args.get("goal") or "").strip()
    steps = args.get("steps")
    if steps and isinstance(steps, list):
        plan = create_plan(goal, steps, context=args.get("context"))
    else:
        plan = plan_from_goal(goal, context=args.get("context"))
    return {"ok": True, "plan": plan.to_dict()}


def _run_plan_list(args: dict[str, Any]) -> dict[str, Any]:
    from .desktop_planner import list_plans
    status = str(args.get("status") or "").strip() or None
    limit = int(args.get("limit") or 20)
    plans = [p.to_dict() for p in list_plans(status=status, limit=limit)]
    return {"ok": True, "plans": plans, "count": len(plans)}


def _run_plan_get(args: dict[str, Any]) -> dict[str, Any]:
    from .desktop_planner import get_plan
    plan_id = str(args.get("plan_id") or "").strip()
    if not plan_id:
        return {"ok": False, "error": "plan_id required"}
    plan = get_plan(plan_id)
    if not plan:
        return {"ok": False, "error": "plan not found"}
    return {"ok": True, "plan": plan.to_dict()}


def _run_plan_cancel(args: dict[str, Any]) -> dict[str, Any]:
    from .desktop_planner import cancel_plan
    plan_id = str(args.get("plan_id") or "").strip()
    if not plan_id:
        return {"ok": False, "error": "plan_id required"}
    return cancel_plan(plan_id)


def _run_plan_run_wave(args: dict[str, Any]) -> dict[str, Any]:
    from .plan_executor import run_plan_wave
    plan_id = str(args.get("plan_id") or "").strip()
    if not plan_id:
        return {"ok": False, "error": "plan_id required"}
    return run_plan_wave(plan_id)


def _run_plan_run_to_completion(args: dict[str, Any]) -> dict[str, Any]:
    from .plan_executor import run_plan_to_completion
    plan_id = str(args.get("plan_id") or "").strip()
    max_waves = int(args.get("max_waves") or 20)
    if not plan_id:
        return {"ok": False, "error": "plan_id required"}
    return run_plan_to_completion(plan_id, max_waves=max_waves)


def _run_plan_suggest(args: dict[str, Any]) -> dict[str, Any]:
    from .plan_learning import suggest_plan_for_goal
    return suggest_plan_for_goal(args.get("goal", ""))


def _run_plan_learn(args: dict[str, Any]) -> dict[str, Any]:
    from .plan_learning import learn_from_completed_plans
    return learn_from_completed_plans(min_steps=int(args.get("min_steps", 2)), limit=int(args.get("limit", 20)))


def _run_schedule_create(args: dict[str, Any]) -> dict[str, Any]:
    from .desktop_scheduler import schedule_task
    return schedule_task(
        title=str(args.get("title") or "").strip(),
        schedule_type=str(args.get("schedule_type") or "").strip(),
        when=str(args.get("when") or "").strip(),
        action_type=str(args.get("action_type") or "").strip(),
        payload=args.get("payload") or {},
    )


def _run_schedule_list(args: dict[str, Any]) -> dict[str, Any]:
    from .desktop_scheduler import list_tasks
    tasks = [t.to_dict() for t in list_tasks(enabled_only=bool(args.get("enabled_only")), limit=int(args.get("limit") or 100))]
    return {"ok": True, "tasks": tasks, "count": len(tasks)}


def _run_schedule_cancel(args: dict[str, Any]) -> dict[str, Any]:
    from .desktop_scheduler import cancel_task
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": "task_id required"}
    return cancel_task(task_id)


def _run_desktop_interpreter(args: dict[str, Any]) -> dict[str, Any]:
    """Run Python code in a richer data-science sandbox.

    Captures matplotlib figures as base64 PNGs and returns generated artifacts.
    Ideal for calculations, CSV analysis, plotting, and small experiments.
    """
    from .code_interpreter import run_interpreter
    code = str(args.get("code") or "").strip()
    if not code:
        return {"ok": False, "error": "code required"}
    return run_interpreter(code, timeout=args.get("timeout", 60))


def _run_desktop_bridge(args: dict[str, Any]) -> dict[str, Any]:
    """Send a command to the paired Desktop Bridge running on the user's machine.

    Actions: ping, shell, screenshot, system_info, find_file, read_file, write_file.
    """
    import asyncio
    from desktop_bridge.bridge_client import DesktopBridge
    uri = os.getenv("SHIMS_DESKTOP_BRIDGE_URI", "ws://localhost:9876/bridge")
    token = os.getenv("SHIMS_DESKTOP_BRIDGE_TOKEN", "")
    if not token:
        return {"ok": False, "error": "Desktop bridge token not configured. Set SHIMS_DESKTOP_BRIDGE_TOKEN in .env and pair the bridge."}
    bridge = DesktopBridge(uri, token)
    action = str(args.get("action") or args.get("type") or "ping").strip().lower()
    try:
        if action == "ping":
            res = asyncio.run(bridge.ping())
        elif action == "shell":
            res = asyncio.run(bridge.shell(str(args.get("command") or ""), cwd=args.get("cwd"), timeout=int(args.get("timeout") or 60)))
        elif action == "screenshot":
            res = asyncio.run(bridge.screenshot())
        elif action == "system_info":
            res = asyncio.run(bridge.system_info())
        elif action == "find_file":
            res = asyncio.run(bridge.find_file(str(args.get("name") or ""), root=str(args.get("root") or "C:\\")))
        elif action == "read_file":
            res = asyncio.run(bridge.read_file(str(args.get("path") or "")))
        elif action == "write_file":
            res = asyncio.run(bridge.write_file(str(args.get("path") or ""), str(args.get("content") or "")))
        else:
            return {"ok": False, "error": f"Unknown bridge action: {action}. Use ping, shell, screenshot, system_info, find_file, read_file, or write_file."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    ok = bool(res.get("ok"))
    return {"ok": ok, "action": action, "result": res, "error": res.get("error") if not ok else None}


def _run_memory_save(args: dict[str, Any]) -> dict[str, Any]:
    from .omni_brain import remember
    content = str(args.get("content") or "").strip()
    if not content:
        return {"ok": False, "error": "content required"}
    key = str(args.get("key") or "").strip() or content[:80]
    namespace = str(args.get("namespace") or "agent").strip()
    result = remember(
        namespace,
        key,
        content,
        tags=args.get("tags") or ["agent"],
        source=args.get("source") or "agent_tool",
        weight=float(args.get("weight", 1.0)),
    )
    return {"ok": True, "memory": result}


def _run_media_generate_image(args: dict[str, Any]) -> dict[str, Any]:
    from .media_tools import generate_image
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "prompt required"}
    return generate_image(prompt, backend=args.get("backend", "auto"), width=args.get("width", 1024), height=args.get("height", 1024))


def _run_media_generate_video(args: dict[str, Any]) -> dict[str, Any]:
    from .media_tools import generate_video_placeholder
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "prompt required"}
    return generate_video_placeholder(prompt)


def _run_memory_search(args: dict[str, Any]) -> dict[str, Any]:
    from .omni_brain import retrieve_context
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query required"}
    limit = int(args.get("limit") or 8)
    ctx = retrieve_context(query, limit=limit)
    hits = ctx.get("hits", [])
    memories = [h for h in hits if h.get("kind") == "memory"]
    return {"ok": True, "query": query, "count": len(memories), "memories": memories, "context_text": ctx.get("context_text", "")}


def _run_media_ingest(args: dict[str, Any]) -> dict[str, Any]:
    from .media_memory import ingest_media
    path = str(args.get("path") or "").strip()
    kind = str(args.get("kind") or "").strip()
    if not path or not kind:
        return {"ok": False, "error": "path and kind required"}
    try:
        return ingest_media(
            path,
            kind,
            title=args.get("title"),
            tags=args.get("tags"),
            metadata=args.get("metadata") or {},
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _run_conversation_summarize(args: dict[str, Any]) -> dict[str, Any]:
    """Summarize conversation context from the omni-brain for compression."""
    from .omni_brain import retrieve_context
    session_id = str(args.get("session_id") or "").strip()
    topic = str(args.get("topic") or "recent conversation").strip()
    if not session_id:
        return {"ok": False, "error": "session_id required"}
    ctx = retrieve_context(f"session:{session_id} {topic}", limit=int(args.get("limit", 20)))
    turns = ctx.get("hits", [])
    if not turns:
        return {"ok": True, "summary": "No conversation history found.", "turns": 0}
    # Simple extractive summary: key facts and decisions
    facts: list[str] = []
    decisions: list[str] = []
    for t in turns:
        content = str(t.get("content") or "").strip()
        if not content:
            continue
        if any(k in content.lower() for k in ("decided", "decision", "approved", "rejected", "chose", "selected")):
            decisions.append(content[:200])
        elif len(content) > 20:
            facts.append(content[:200])
    summary_parts = []
    if decisions:
        summary_parts.append("Key decisions: " + "; ".join(decisions[:5]))
    if facts:
        summary_parts.append("Key facts: " + "; ".join(facts[:8]))
    summary = "\n".join(summary_parts) if summary_parts else "Conversation contained general discussion."
    return {"ok": True, "summary": summary, "turns": len(turns), "session_id": session_id}


def _run_brain_self_index(args: dict[str, Any]) -> dict[str, Any]:
    """Phase 3.1 Self-indexer: ingest allowed SHIMS source into the omni-brain."""
    from . import self_indexer
    return self_indexer.index_shims_source(force=bool(args.get("force", False)))


# --------------------------------------------------------------------------- #
# Self-modification (validated through the self-evolver sandbox)
# --------------------------------------------------------------------------- #
def propose_self_patch(path: str, *, new_content: str | None = None, instructions: str = "",
                       reason: str = "") -> dict[str, Any]:
    """Create + sandbox-validate a proposal to change Omni's own source.

    Does NOT touch live code — only writes a proposal + runs validation in the
    lean copy sandbox. Returns {ok, proposal_id, diff, validation}. The actual
    apply happens later, on human approval (action_type='self_patch_apply').
    """
    from .self_evolver import create_proposal, validate_proposal
    rel = path.replace("\\", "/").lstrip("/")
    target = (REPO_ROOT / rel).resolve()
    if not _is_within(target, REPO_ROOT):
        return {"ok": False, "error": "self.patch can only target files inside the SHIMS repo"}
    current = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    content = new_content
    if content is None:
        # Generate the full new file from current content + instructions (sync LLM call).
        content = _llm_rewrite_file(rel, current, instructions or reason)
        if not content:
            return {"ok": False, "error": "could not generate new file content"}
    proposal = create_proposal(rel, content, reason=reason or instructions or "self-patch via agent loop",
                               author="agent-loop", scope="code")
    if not proposal.get("ok"):
        return proposal
    pid = proposal["proposal_id"]
    validation = validate_proposal(pid)
    import difflib
    diff = "".join(difflib.unified_diff(
        current.splitlines(keepends=True), content.splitlines(keepends=True),
        fromfile=f"a/{rel}", tofile=f"b/{rel}"))[:12000]
    return {"ok": True, "needs_approval": True, "proposal_id": pid, "path": rel,
            "diff": diff, "validation": {"status": validation.status, "message": validation.message},
            "validated": validation.status == "validated"}


def _llm_rewrite_file(rel: str, current: str, instructions: str) -> str:
    """Best-effort full-file rewrite. Prefers fast cloud models (Anthropic) when
    configured; falls back to the local self-evolution model."""
    from .coder import _parse_spec
    from .config import settings
    from .ai import _stored_provider, clean_secret

    system = ("You are SHIMS modifying its own source. Return STRICT JSON only: "
              '{"files": {"%s": "FULL NEW FILE CONTENT"}}. Return the COMPLETE file, '
              "preserving everything that should stay. No prose." % rel)
    prompt = f"FILE: {rel}\n\nCURRENT CONTENT:\n```\n{current[:24000]}\n```\n\nCHANGE REQUESTED:\n{instructions}\n"

    # Prefer Anthropic for speed/quality if a key is available.
    stored = _stored_provider('anthropic')
    api_key = clean_secret((stored or {}).get('api_key') or getattr(settings, 'anthropic_api_key', '') or '')
    if api_key:
        try:
            import httpx, json as _json
            model = (stored or {}).get('default_model') or getattr(settings, 'anthropic_model', 'claude-sonnet-4-6')
            payload = {
                "model": model,
                "max_tokens": settings.max_output_tokens,
                "messages": [{"role": "user", "content": f"{system}\n\n{prompt}"}],
                "stream": False,
            }
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            with httpx.Client(timeout=120) as client:
                r = client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")
            spec = _parse_spec(text)
            files = spec.get("files") or {}
            for _, content in files.items():
                if isinstance(content, str) and content.strip():
                    return content
        except Exception:
            pass

    # Fallback to local Ollama self-evolution model.
    from .ai import ask_ai
    from .coder import _prefer_coder_model
    model = settings.self_evolution_model or _prefer_coder_model("ollama", None)
    try:
        result = _run_sync(ask_ai(prompt, system=system, provider="ollama", model=model))
        spec = _parse_spec(result.text)
        files = spec.get("files") or {}
        for _, content in files.items():
            if isinstance(content, str) and content.strip():
                return content
    except Exception:
        return ""
    return ""


def _run_sync(coro: Any) -> Any:
    """Run an awaitable from sync code, whether or not a loop is already running."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def _run_self_patch(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    result = propose_self_patch(
        str(args.get("path") or ""),
        new_content=args.get("new_content"),
        instructions=str(args.get("instructions") or ""),
        reason=str(args.get("reason") or ""),
    )
    if not result.get("ok") or not result.get("proposal_id"):
        return result
    if settings.omnipotent_mode:
        from .self_evolver import apply_proposal
        applied = apply_proposal(
            result["proposal_id"],
            approved_by="omnipotent-mode",
            approval_phrase="I_APPROVE_SHIMS_PATCH",
        )
        return {
            "ok": applied.status == "applied",
            "applied": applied.status == "applied",
            "proposal_id": result["proposal_id"],
            "path": result.get("path"),
            "status": applied.status,
            "message": applied.message,
            "diff": result.get("diff", ""),
            "validation": result.get("validation"),
        }
    return result


def _run_self_inspect(args: dict[str, Any]) -> dict[str, Any]:
    """Inspect SHIMS code and create a real, validated patch proposal."""
    from .self_check import run_self_check
    scope = str(args.get("scope") or "tests").strip().lower()
    if scope not in {"tests", "lint", "file"}:
        return {"ok": False, "error": "scope must be tests, lint, or file"}
    return _run_sync(run_self_check(
        scope=scope,
        relative_path=args.get("relative_path"),
        goal=args.get("goal"),
        test_path=args.get("test_path"),
    ))


def _run_prompt_list_variants(args: dict[str, Any]) -> dict[str, Any]:
    from .prompt_evolution import compare_variants
    return {"ok": True, "variants": compare_variants()}


def _run_prompt_run_eval(args: dict[str, Any]) -> dict[str, Any]:
    from .prompt_evolution import run_eval_suite, default_eval_cases
    variant_id = str(args.get("variant_id") or "").strip()
    if not variant_id:
        return {"ok": False, "error": "variant_id required"}
    run = run_eval_suite(variant_id, default_eval_cases())
    return {"ok": True, "run_id": run.id, "summary": run.summary}


def _run_prompt_promote(args: dict[str, Any]) -> dict[str, Any]:
    from .prompt_evolution import promote_variant
    variant_id = str(args.get("variant_id") or "").strip()
    if not variant_id:
        return {"ok": False, "error": "variant_id required"}
    promoted = promote_variant(variant_id)
    if promoted is None:
        return {"ok": False, "error": f"variant not found: {variant_id}"}
    return {"ok": True, "variant": {"id": promoted.id, "name": promoted.name, "active": promoted.active}}


def _run_coder_fold(args: dict[str, Any]) -> dict[str, Any]:
    from .coder_bridge import fold_project
    project_id = str(args.get("project_id") or "").strip()
    target_dir = str(args.get("target_dir") or "").strip()
    auto_apply = bool(args.get("auto_apply", False))
    if not project_id or not target_dir:
        return {"ok": False, "error": "coder.fold_project needs project_id and target_dir"}
    return fold_project(project_id, target_dir, auto_apply=auto_apply)


def _run_mail_assist_status(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    from .mail_assistant import check_mail_status
    return asyncio.run(check_mail_status())


def _run_mail_assist_digest(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    from .mail_assistant import mail_digest
    return asyncio.run(mail_digest(limit=int(args.get("limit", 10))))


def _run_mail_assist_compose(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    from .mail_assistant import mail_compose
    return asyncio.run(mail_compose(
        to=str(args.get("to") or ""),
        subject=str(args.get("subject") or ""),
        body=str(args.get("body") or ""),
    ))


def _run_improvement_cycle(args: dict[str, Any]) -> dict[str, Any]:
    from .improvement_loop import run_improvement_cycle
    return run_improvement_cycle(system_prompt_text=str(args.get("system_prompt") or ""))


def _run_improvement_runs(args: dict[str, Any]) -> dict[str, Any]:
    from .improvement_loop import list_improvement_runs
    return {"ok": True, "runs": list_improvement_runs(limit=int(args.get("limit", 20)))}


def _run_improvement_cross_instance_sync(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    from . import cross_instance_improvement
    peer_id = str(args.get("peer_id") or cross_instance_improvement.default_peer_id())
    local_proposals = args.get("local_proposals") or []
    return asyncio.run(cross_instance_improvement.run_cross_instance_sync(local_proposals=local_proposals, peer_id=peer_id))


def _run_vision_describe(args: dict[str, Any]) -> dict[str, Any]:
    from .vision import describe_image
    source = str(args.get("source") or "").strip()
    prompt = str(args.get("prompt") or "Describe this image concisely.").strip()
    backend = str(args.get("backend") or "auto").strip()
    if not source:
        return {"ok": False, "error": "vision.describe needs 'source' (path, URL, or base64 data URI)"}
    return describe_image(source, prompt, backend)


# --------------------------------------------------------------------------- #
# App Factory tools — let Omni scaffold / evolve / test SHIMS vertical apps
# --------------------------------------------------------------------------- #

def _app_factory_overview_skill() -> str:
    """Return the App Factory Overview skill text."""
    from . import skills
    for s in skills.list_skills(query="app_factory overview", limit=5):
        if s.get("name") == "SHIMS App Factory Overview":
            return f"### {s['name']}\n{s.get('summary', '')}\n{s.get('body', '')}"
    return "(app_factory overview not found)"


def _app_factory_skill_for_file(rel_path: str) -> str:
    """Return only the App Factory skill relevant to the file being generated."""
    from . import skills
    mapping = {
        "database.py": "SHIMS App Factory SQLite Layer",
        "services/": "SHIMS App Factory Service Layer",
        "routers/": "SHIMS App Factory Router Factory",
        "templates/": "SHIMS App Factory Templates & Static",
        "static/": "SHIMS App Factory Templates & Static",
        "services/ai.py": "SHIMS App Factory Voice & AI",
        "tests/": "SHIMS App Factory Test Scaffold",
    }
    chosen = "SHIMS App Factory Overview"
    for prefix, skill_name in mapping.items():
        if prefix in rel_path:
            chosen = skill_name
            break
    for s in skills.list_skills(query=chosen, limit=10):
        if s.get("name") == chosen:
            return f"### {s['name']}\n{s.get('summary', '')}\n{s.get('body', '')}"
    return _app_factory_overview_skill()


def _app_factory_context_block(query: str) -> str:
    """Retrieve relevant source context from the omni-brain."""
    try:
        from .omni_brain import retrieve_context
        ctx = retrieve_context(query, limit=8)
        return ctx.get("context_text", "")
    except Exception as exc:
        return f"(brain context unavailable: {exc})"


def _app_factory_parse_json(text: str) -> Any:
    """Best-effort JSON extraction from an LLM response."""
    # Try the whole text, then a code fence, then the largest balanced {} or [] block.
    try:
        return json.loads(text)
    except Exception:
        pass
    # Code fence
    for pattern in (r"```(?:json)?\s*\n(.*?)\n```", r"```(?:json)?\s*(.*?)```"):
        m = re.search(pattern, text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
    # Largest balanced {} or [] block
    best: str | None = None
    for opener, closer in (("{", "}"), ("[", "]")):
        depth = 0
        start: int | None = None
        for i, ch in enumerate(text):
            if ch == opener:
                if depth == 0:
                    start = i
                depth += 1
            elif ch == closer:
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start is not None:
                        block = text[start : i + 1]
                        if best is None or len(block) > len(best):
                            best = block
    if best:
        try:
            return json.loads(best)
        except Exception:
            pass
    return None


def _run_app_factory_design_app(args: dict[str, Any]) -> dict[str, Any]:
    """Turn a natural-language brief into a structured app spec."""
    domain = str(args.get("domain") or "").strip()
    title = str(args.get("title") or "").strip() or f"{domain.title()} Manager"
    prefix = str(args.get("prefix") or "").strip()
    features = args.get("features") or []
    if isinstance(features, str):
        features = [f.strip() for f in features.split(",") if f.strip()]
    roles = args.get("roles") or []
    if isinstance(roles, str):
        roles = [r.strip() for r in roles.split(",") if r.strip()]
    ai_features = bool(args.get("ai_features", True))
    voice_languages = args.get("voice_languages") or ["en"]
    if isinstance(voice_languages, str):
        voice_languages = [l.strip() for l in voice_languages.split(",") if l.strip()]

    if not domain:
        return {"ok": False, "error": "domain is required"}
    if not prefix:
        prefix = f"/{domain.lower().replace(' ', '_')}"

    from .ai import ask_ai
    skill_block = _app_factory_overview_skill()
    system = (
        "You are SHIMS App Factory Designer. You turn a domain brief into a precise, "
        "implementable JSON specification for a SHIMS vertical FastAPI app."
    )
    prompt = f"""Design a SHIMS vertical app for the domain: {domain}
Title: {title}
URL prefix: {prefix}
Features requested: {features!r}
User roles: {roles!r}
AI features: {ai_features}
Voice languages: {voice_languages!r}

App Factory pattern:
{skill_block}

Return ONLY a JSON object with this exact shape (no markdown, no prose):
{{
  "app_name": "lowercase_snake_app_name",
  "title": "Human-readable title",
  "prefix": "/prefix",
  "roles": [{{"username": "admin", "role": "admin", "password": "admin123"}}],
  "entities": [
    {{"name": "student", "fields": [{{"name": "full_name", "type": "text", "required": true}}]}}
  ],
  "routes": [
    {{"path": "/api/students", "method": "POST", "purpose": "create student", "required_fields": ["full_name"]}}
  ],
  "ui_tabs": ["Dashboard", "Students", "Attendance"],
  "ai_endpoints": ["/api/ai/summarize", "/api/ai/insights"],
  "tests": ["test create student", "test attendance"],
  "notes": "any special design notes"
}}
"""
    factory_model = os.getenv("SHIMS_FACTORY_MODEL", "claude-sonnet-4-6")
    cloud_prefixes = ("claude-", "gpt-", "gemini-", "deepseek-", "kimi-")
    provider = None if factory_model.startswith(cloud_prefixes) else "ollama"
    result = _run_sync(ask_ai(prompt, system, provider=provider, model=factory_model))
    if not result.ok:
        return {"ok": False, "error": f"AI designer failed: {result.error}", "raw": result.text}
    spec = _app_factory_parse_json(result.text)
    if not isinstance(spec, dict):
        return {"ok": False, "error": "AI did not return valid JSON spec", "raw": result.text}
    spec.setdefault("app_name", domain.lower().replace(" ", "_"))
    spec.setdefault("title", title)
    spec.setdefault("prefix", prefix)
    return {"ok": True, "spec": spec, "model": result.model, "provider": result.provider}


def _app_factory_generate_file(
    app_name: str,
    title: str,
    prefix: str,
    file_path: str,
    spec: dict[str, Any],
    file_plan: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a single file for the app using the AI."""
    from .ai import ask_ai
    skill_block = _app_factory_skill_for_file(file_path)
    system = (
        "You are SHIMS App Factory Builder. Generate the exact source code for ONE file "
        "of a SHIMS vertical FastAPI app. Output ONLY the file content; no markdown fences, "
        "no explanations."
    )

    extra = ""
    if file_path == "app.py":
        router_files = [
            f for f in (file_plan or [])
            if f.startswith("routers/") and f.endswith(".py") and f != "routers/__init__.py"
        ]
        router_includes = []
        for rf in router_files:
            mod = rf[:-3].replace("/", ".")  # e.g. routers.tasks
            name = mod.split(".")[-1]
            router_includes.append(f"from .{mod} import router as {name}_router\n    router.include_router({name}_router)")
        extra = f"""
CRITICAL: This is the top-level FastAPI router factory for app `{app_name}`.
- Call `ensure_schema()` before returning the router.
- Create `APIRouter(prefix=prefix)` where prefix is exactly `{prefix}`.
- Provide an index route at `@router.get("")` that renders `templates/index.html` with `request` in context.
- Import and include these planned domain routers (each already has its own `APIRouter`):
{chr(10).join(router_includes) if router_includes else "  (none planned)"}
"""
    elif file_path.startswith("routers/") and file_path.endswith(".py"):
        extra = f"""
CRITICAL router rules:
- Create `router = APIRouter()` with NO prefix (the app-level router supplies prefix `{prefix}`).
- Use the shared database helper: `from ..database import get_db` and use `with get_db() as conn:`.
- Do NOT hardcode the DB path or use raw `sqlite3.connect`.
- Do NOT set `status_code=201` on POST endpoints; return the default 200 for all successful writes unless a test explicitly expects another code.
- Use Pydantic models for request bodies and let FastAPI return 422 for invalid input.
"""
    elif file_path.startswith("services/") and file_path.endswith(".py"):
        extra = """
CRITICAL service rules:
- Use `from ..database import get_db` and `with get_db() as conn:` for DB access.
- Do NOT hardcode the DB path.
"""

    prompt = f"""Generate the file `{file_path}` for the SHIMS vertical app `{title}` (`{prefix}`).

App spec:
- app_name: {app_name}
- title: {title}
- prefix: {prefix}
- roles: {spec.get('roles')!r}
- entities: {spec.get('entities')!r}
- routes: {spec.get('routes')!r}
- UI tabs: {spec.get('ui_tabs')!r}
- AI endpoints: {spec.get('ai_endpoints')!r}
- Notes: {spec.get('notes', '')}

App Factory pattern to follow:
{skill_block}
{extra}

Instructions:
- Output ONLY the raw file content (valid Python / HTML / CSS / JavaScript).
- Do NOT wrap the content in markdown fences.
- Do NOT include commentary.
- Make the file self-contained and consistent with the spec and the pattern above.
"""
    factory_model = os.getenv("SHIMS_FACTORY_MODEL", "claude-sonnet-4-6")
    cloud_prefixes = ("claude-", "gpt-", "gemini-", "deepseek-", "kimi-")
    provider = None if factory_model.startswith(cloud_prefixes) else "ollama"
    result = _run_sync(ask_ai(prompt, system, provider=provider, model=factory_model))
    if not result.ok:
        return {"ok": False, "error": f"AI failed: {result.error}", "raw": result.text}
    content = result.text.strip()
    # Strip markdown fences if the model ignored the instruction.
    for pat in (r"^```(?:\w+)?\s*\n", r"\n```\s*$"):
        content = re.sub(pat, "", content, flags=re.S)
    return {"ok": True, "content": content, "model": result.model, "provider": result.provider}


def _run_app_factory_build_app(args: dict[str, Any]) -> dict[str, Any]:
    """Scaffold and generate a complete SHIMS vertical app from a spec, file by file."""
    spec = args.get("spec") or {}
    if not isinstance(spec, dict):
        return {"ok": False, "error": "spec must be an object"}
    app_name = str(spec.get("app_name") or args.get("app_name") or "").strip()
    title = str(spec.get("title") or args.get("title") or "").strip()
    prefix = str(spec.get("prefix") or args.get("prefix") or "").strip()
    if not app_name or not title or not prefix:
        return {"ok": False, "error": "app_name, title, and prefix are required"}

    from .app_factory import create_app_template_files, derive_paths
    from .ai import ask_ai

    # 1. Generic scaffold
    scaffold = create_app_template_files(
        app_name,
        prefix=prefix,
        title=title,
        db_filename=f"{app_name}.sqlite3",
        default_roles=spec.get("roles"),
    )
    paths = derive_paths(app_name)
    app_dir = paths["app_dir"]

    # 2. Ask AI for the file plan
    plan_system = (
        "You are SHIMS App Factory Planner. Given an app spec, emit ONLY a JSON object "
        "listing the source files needed to implement the app."
    )
    plan_prompt = f"""Plan the source files for the SHIMS vertical app `{title}`.

Spec:
- app_name: {app_name}
- title: {title}
- prefix: {prefix}
- roles: {spec.get('roles')!r}
- entities: {spec.get('entities')!r}
- routes: {spec.get('routes')!r}
- UI tabs: {spec.get('ui_tabs')!r}
- AI endpoints: {spec.get('ai_endpoints')!r}
- Tests: {spec.get('tests')!r}

Return ONLY a JSON object like:
{{"files": [
  "database.py",
  "services/auth.py",
  "services/students.py",
  "routers/students.py",
  "services/ai.py",
  "templates/index.html",
  "static/css/{app_name}.css",
  "static/js/{app_name}.js",
  "tests/test_{app_name}.py"
]}}
"""
    plan_result = _run_sync(ask_ai(plan_prompt, plan_system, model=os.getenv("SHIMS_CODER_MODEL", "claude-sonnet-4-6")))
    if not plan_result.ok:
        return {"ok": False, "error": f"AI planner failed: {plan_result.error}", "raw": plan_result.text, "scaffold": scaffold}
    plan = _app_factory_parse_json(plan_result.text)
    file_list: list[str] = []
    if isinstance(plan, dict) and isinstance(plan.get("files"), list):
        file_list = [str(f) for f in plan["files"]]
    # If the spec defines roles, make sure an auth router exists so role-based tests pass.
    if spec.get("roles") and "routers/auth.py" not in file_list:
        file_list.append("routers/auth.py")

    if not file_list:
        # Fallback plan
        file_list = [
            "database.py",
            "services/auth.py",
            "routers/auth.py",
            "services/core.py",
            "services/ai.py",
            "routers/core.py",
            "templates/index.html",
            "static/css/{app_name}.css",
            "static/js/{app_name}.js",
        ]

    # 3. Generate each file in parallel (I/O-bound AI calls)
    files_written: list[str] = []
    files_failed: list[dict[str, Any]] = []
    gen_metadata: list[dict[str, Any]] = []
    from concurrent.futures import ThreadPoolExecutor, wait as concurrent_futures_wait

    def _gen_one(rel: str) -> dict[str, Any]:
        rel = rel.removeprefix(f"apps/{app_name}/")
        if not rel or rel.startswith(".."):
            return {"skip": True, "rel": rel}
        gen = _app_factory_generate_file(app_name, title, prefix, rel, spec, file_plan=file_list)
        if not gen.get("ok"):
            return {"ok": False, "rel": rel, "error": gen.get("error"), "raw": gen.get("raw", "")}
        try:
            if rel.startswith("tests/"):
                target = REPO_ROOT / rel
            else:
                target = app_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(gen["content"], encoding="utf-8")
            return {"ok": True, "rel": rel, "model": gen.get("model"), "provider": gen.get("provider")}
        except Exception as exc:
            return {"ok": False, "rel": rel, "error": str(exc)}

    # Local models (Ollama) serialize requests, so use few workers and a generous timeout.
    factory_model = os.getenv("SHIMS_FACTORY_MODEL", "claude-sonnet-4-6")
    is_cloud = factory_model.startswith(("claude-", "gpt-", "gemini-", "deepseek-", "kimi-"))
    workers = 1 if not is_cloud else min(4, max(1, len(file_list)))
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(_gen_one, rel): rel for rel in file_list}
        # Wait up to 5 minutes for the batch; files still pending are treated as timeouts.
        done, not_done = concurrent_futures_wait(futures, timeout=300)
        for fut in done:
            res = fut.result()
            if res.get("skip"):
                continue
            rel = res["rel"]
            if res.get("ok"):
                files_written.append(rel)
                gen_metadata.append({"file": rel, "model": res.get("model"), "provider": res.get("provider")})
            else:
                files_failed.append({"file": rel, "error": res.get("error"), "raw": res.get("raw", "")})
        for fut in not_done:
            rel = futures.get(fut, "unknown")
            files_failed.append({"file": rel, "error": "generation timed out after 300s", "raw": ""})
    finally:
        # Don't block the whole build because one AI call is still stuck in network/model loading.
        pool.shutdown(wait=False, cancel_futures=True)

    # 4. Fix up app.py to wire router imports and ensure correct prefix
    _app_factory_fixup_app_py(app_name, prefix, file_list)

    # 5. Mount in backend/app/main.py
    mount_note = _app_factory_mount_in_backend(app_name)
    # 6. Add launcher tile
    tile_note = _app_factory_add_launcher_tile(app_name, title, prefix)

    # 7. Run tests
    test_result = _run_app_factory_test_app({"app_name": app_name})

    return {
        "ok": len(files_failed) == 0 and test_result.get("ok"),
        "app_name": app_name,
        "title": title,
        "prefix": prefix,
        "scaffold": scaffold,
        "files_written": files_written,
        "files_failed": files_failed,
        "mount_note": mount_note,
        "tile_note": tile_note,
        "test_result": test_result,
        "gen_metadata": gen_metadata,
        "model": plan_result.model,
        "provider": plan_result.provider,
    }


def _app_factory_fixup_app_py(app_name: str, prefix: str, file_list: list[str]) -> dict[str, Any]:
    """Make sure generated app.py imports and includes every routers/*.py module."""
    app_py = REPO_ROOT / "apps" / app_name / "app.py"
    if not app_py.exists():
        return {"ok": False, "error": "app.py missing"}
    text = app_py.read_text(encoding="utf-8")

    # Ensure the prefix is exact
    text = re.sub(
        r"router\s*=\s*APIRouter\s*\(\s*prefix\s*=\s*['\"][^'\"]*['\"]\s*\)",
        f'router = APIRouter(prefix="{prefix}")',
        text,
    )
    # Ensure ensure_schema is called
    if "ensure_schema()" not in text:
        text = text.replace(
            "def create_" + app_name + "_router() -> APIRouter:",
            "def create_" + app_name + "_router() -> APIRouter:\n    ensure_schema()",
        )

    router_files = [
        f for f in file_list
        if f.startswith("routers/") and f.endswith(".py") and f != "routers/__init__.py"
    ]
    if router_files:
        # Build include block, avoiding duplicate imports
        import_lines: list[str] = []
        include_lines: list[str] = []
        for rf in router_files:
            mod = rf[:-3].replace("/", ".")  # routers.tasks
            name = mod.split(".")[-1]
            alias = f"{name}_router_module"
            import_line = f"from .{mod} import router as {alias}"
            include_line = f"    router.include_router({alias})"
            if import_line not in text and alias not in text:
                import_lines.append(import_line)
                include_lines.append(include_line)

        if include_lines:
            # Insert imports near other from . imports if any
            if "from .database import ensure_schema" in text and import_lines:
                text = text.replace(
                    "from .database import ensure_schema",
                    "from .database import ensure_schema\n" + "\n".join(import_lines),
                )
            else:
                text = "\n".join(import_lines) + "\n" + text

            # Insert includes before the final `return router`
            include_block = "\n".join(include_lines) + "\n"
            text = re.sub(
                r"\n(\s*return\s+router\s*\n?)",
                "\n" + include_block + r"\1",
                text,
            )

    app_py.write_text(text, encoding="utf-8")
    return {"ok": True, "changes": ["prefix", "router_wiring"] if router_files else ["prefix"]}


def _app_factory_mount_in_backend(app_name: str) -> dict[str, Any]:
    """Add import + mount + router include for an app in backend/app/main.py."""
    main_py = REPO_ROOT / "backend" / "app" / "main.py"
    text = main_py.read_text(encoding="utf-8")
    import_line = f"from apps.{app_name}.app import create_{app_name}_router\n"
    mount_line = f'app.mount("/{app_name}-static", StaticFiles(directory=str(ROOT / "apps" / "{app_name}" / "static")), name="{app_name}-static")\n'
    router_line = f"app.include_router(create_{app_name}_router())\n"

    changes: list[str] = []
    if import_line not in text:
        marker = "from apps.jk_hospital.app import create_hospital_router, mount_static\n"
        if marker in text:
            text = text.replace(marker, marker + import_line)
        else:
            text = import_line + text
        changes.append("import")

    if router_line not in text:
        marker = "app.include_router(create_hospital_router())\n"
        if marker in text:
            text = text.replace(marker, marker + router_line)
        else:
            # append before app.router.lifespan_context assignment
            text = text.replace("app.router.lifespan_context = _omni_lifespan", router_line + "app.router.lifespan_context = _omni_lifespan")
        changes.append("router")

    if mount_line not in text:
        marker = 'app.mount("/hospital-static", StaticFiles(directory=str(ROOT / "apps" / "jk_hospital" / "static")), name="hospital-static")\n'
        if marker in text:
            text = text.replace(marker, marker + mount_line)
        changes.append("mount")

    main_py.write_text(text, encoding="utf-8")
    return {"ok": True, "changes": changes}


def _app_factory_add_launcher_tile(app_name: str, title: str, prefix: str) -> dict[str, Any]:
    """Add a launcher tile for the app in frontend/shims_omni.html."""
    html_path = REPO_ROOT / "frontend" / "shims_omni.html"
    text = html_path.read_text(encoding="utf-8", errors="ignore")
    tile_line = f'<div class="nav-row" onclick="window.open(\'{prefix}\',\'_blank\')" style="cursor:pointer"><span>🏢</span>{title}</div>\n'
    if tile_line in text:
        return {"ok": True, "changes": []}
    marker = '<div class="nav-row" onclick="window.open(\'/hospital\',\'_blank\')" style="cursor:pointer"><span>🏥</span>J K Hospital</div>\n'
    if marker in text:
        text = text.replace(marker, marker + tile_line)
    else:
        # append before closing of modules panel (best-effort)
        text = text.replace("</body>", tile_line + "</body>")
    html_path.write_text(text, encoding="utf-8")
    return {"ok": True, "changes": ["tile"]}


def _run_app_factory_evolve_app(args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch the swarm to modify an existing SHIMS vertical app."""
    app_name = str(args.get("app_name") or "").strip()
    change_request = str(args.get("change_request") or "").strip()
    if not app_name or not change_request:
        return {"ok": False, "error": "app_name and change_request are required"}
    app_dir = REPO_ROOT / "apps" / app_name
    if not app_dir.exists():
        return {"ok": False, "error": f"app not found: {app_dir}"}

    skills_block = _app_factory_skills_block()
    prompt = f"""Evolve the existing SHIMS vertical app at apps/{app_name}/.

Change request: {change_request}

App Factory patterns:
{skills_block}

Instructions:
- Read the current files in apps/{app_name}/ and tests/test_{app_name}.py.
- Apply the change request while keeping all existing tests passing.
- Add or update tests for new behavior.
- Run tests and fix any failures.
- Do not touch immutable harness files (shared/self_evolver.py, shared/security.py, shared/config.py).
"""
    return _run_agent_swarm({"prompt": prompt, "orchestrate": True, "use_llm": True})


def _run_app_factory_test_app(args: dict[str, Any]) -> dict[str, Any]:
    """Run pytest for a vertical app."""
    app_name = str(args.get("app_name") or "").strip()
    if not app_name:
        return {"ok": False, "error": "app_name is required"}
    test_file = REPO_ROOT / "tests" / f"test_{app_name}.py"
    if not test_file.exists():
        return {"ok": False, "error": f"test file not found: {test_file}"}
    cmd = [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short"]
    try:
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-4000:],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _app_factory_risk(args: dict[str, Any]) -> str:
    from .config import settings
    if settings.omnipotent_mode:
        return "safe"
    return "gated"


def _run_app_factory_diagnose_app(args: dict[str, Any]) -> dict[str, Any]:
    """Diagnose common bugs in a SHIMS vertical app."""
    from .app_doctor import diagnose_app
    app_name = str(args.get("app_name") or "").strip()
    if not app_name:
        return {"ok": False, "error": "app_name is required"}
    return {"ok": True, "report": diagnose_app(app_name)}


def _run_app_factory_repair_app(args: dict[str, Any]) -> dict[str, Any]:
    """Apply safe automatic fixes to a SHIMS vertical app."""
    from .app_doctor import repair_app
    app_name = str(args.get("app_name") or "").strip()
    if not app_name:
        return {"ok": False, "error": "app_name is required"}
    return {"ok": True, "report": repair_app(app_name)}


# --------------------------------------------------------------------------- #
# Tool registry
# --------------------------------------------------------------------------- #
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], dict[str, Any]]
    risk: Callable[[dict[str, Any]], str] = field(default=lambda args: "safe")

    def spec(self) -> dict[str, Any]:
        """Ollama / OpenAI-style function-calling spec."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


_S = {"type": "string"}
_I = {"type": "integer"}
_A = {"type": "array"}
_O = {"type": "object"}
_N = {"type": "number"}
_B = {"type": "boolean"}

TOOLS: dict[str, Tool] = {}


def _register(t: Tool) -> None:
    TOOLS[t.name] = t


def register_ephemeral_tool(name: str, description: str, run: Callable[[dict[str, Any]], dict[str, Any]]) -> Tool:
    """Register a temporary tool at runtime (used by eval harnesses and skill plugins).

    The tool accepts a single object argument and returns a dict result.
    """
    t = Tool(name, description, _schema({"args": {"type": "object"}}, []), run)
    _register(t)
    return t


_register(Tool("shell.run", "Run a shell command (PowerShell on Windows, bash on Unix). Use for git, listing, building, installing, running programs. Read-only commands run instantly; destructive ones ask for approval.",
               _schema({"command": _S, "cwd": _S, "timeout": _I}, ["command"]), _run_shell, _shell_risk))
_register(Tool("fs.read", "Read a text file from anywhere on the machine.",
               _schema({"path": _S}, ["path"]), _run_fs_read))
_register(Tool("fs.list", "List the contents of a directory.",
               _schema({"path": _S}, ["path"]), _run_fs_list))
_register(Tool("fs.glob", "Find files matching a glob pattern under a root directory.",
               _schema({"pattern": _S, "root": _S}, ["pattern"]), _run_fs_glob))
_register(Tool("fs.watch", "Watch a directory for new or modified files since the last check. Returns new_files and changed lists.",
               _schema({"path": _S}, ["path"]), _run_fs_watch))
_register(Tool("fs.search", "Search file names and text contents for a query under a root directory.",
               _schema({"query": _S, "root": _S}, ["query"]), _run_fs_search))
_register(Tool("fs.write", "Create or overwrite a file with new content. Safe inside the SHIMS repo scratch areas and allowed folders; asks approval outside them. (For SHIMS's own source code use self.patch.)",
               _schema({"path": _S, "content": _S}, ["path", "content"]), _run_fs_write,
               lambda a: _write_risk(a.get("path"))))
_register(Tool("fs.edit", "Replace an exact substring in a file (find → replace).",
               _schema({"path": _S, "find": _S, "replace": _S}, ["path", "find", "replace"]), _run_fs_edit,
               lambda a: _write_risk(a.get("path"))))
_register(Tool("fs.mkdir", "Create a directory (and parents).",
               _schema({"path": _S}, ["path"]), _run_fs_mkdir, lambda a: _write_risk(a.get("path"))))
_register(Tool("fs.move", "Move or rename a file or directory.",
               _schema({"src": _S, "dst": _S}, ["src", "dst"]), _run_fs_move, _move_risk))
_register(Tool("fs.delete", "Delete a file or directory. Always asks for approval outside scratch areas.",
               _schema({"path": _S}, ["path"]), _run_fs_delete, lambda a: _delete_risk(a.get("path"))))
_register(Tool("code.run", "Run a snippet of code (python/javascript/bash) in a sandbox with a timeout and return its output.",
               _schema({"language": _S, "source": _S}, ["source"]), _run_code))
_register(Tool("web.search", "Search the web and return titles, URLs and snippets.",
               _schema({"query": _S, "max_results": _I}, ["query"]), _run_web_search))
_register(Tool("web.fetch", "Fetch a web page and return its readable text.",
               _schema({"url": _S}, ["url"]), _run_web_fetch))

# ── Browser Agent (Kimi Claw) ──
_register(Tool("browser.visit", "Visit a URL with a real headless browser. Returns page title, readable text, links, forms, and headings.",
               _schema({"url": _S, "wait_for": _S, "scroll": {"type": "boolean"}}, ["url"]), _run_browser_visit))
_register(Tool("browser.search", "Search the web using DuckDuckGo with a real browser. Returns titles, URLs, and snippets.",
               _schema({"query": _S, "max_results": _I}, ["query"]), _run_browser_search))
_register(Tool("browser.click", "Click a link or element on a page by CSS selector or link text. Returns the new page after navigation.",
               _schema({"url": _S, "selector": _S, "text": _S}, ["url"]), _run_browser_click))
_register(Tool("browser.extract", "Extract data from a page using a CSS selector. Returns text, HTML, and hrefs of matched elements.",
               _schema({"url": _S, "selector": _S}, ["url", "selector"]), _run_browser_extract))
_register(Tool("browser.fill_form", "Fill form fields and submit. Fields is a dict of {field_name: value}.",
               _schema({"url": _S, "fields": {"type": "object"}, "submit_selector": _S}, ["url", "fields"]), _run_browser_fill_form))
_register(Tool("browser.screenshot", "Take a screenshot of a page or element. Saves to data/screenshots/ and returns the image URL.",
               _schema({"url": _S, "selector": _S, "full_page": {"type": "boolean"}}, ["url"]), _run_browser_screenshot))
_register(Tool("browser.scroll", "Scroll a page up/down/bottom and return the new visible content.",
               _schema({"url": _S, "direction": _S, "amount": _I}, ["url"]), _run_browser_scroll))

# ── Mailbox tools ──
_register(Tool("mailbox.send", "Send an email via Gmail. Requires Gmail OAuth to be connected in Settings.",
               _schema({"to": _S, "subject": _S, "body": _S}, ["to", "subject", "body"]), _run_mailbox_send))
_register(Tool("mailbox.digest", "Get a summary of recent mailbox items — unread counts, categories, priority items.",
               _schema({"limit": _I}, []), _run_mailbox_digest))
_register(Tool("mailbox.organize", "Organize Gmail messages matching criteria (e.g. 'from:newsletter older_than:7d'). Returns what would be organized; live apply requires Gmail modify scope.",
               _schema({"criteria": _S, "action": _S}, ["criteria"]), _run_mailbox_organize))
_register(Tool("mail.assist.status", "Check whether SHIMS can access mail via Gmail API or your desktop browser.",
               _schema({}, []), _run_mail_assist_status))
_register(Tool("mail.assist.digest", "Get a unified mail digest from Gmail API or desktop browser.",
               _schema({"limit": _I}, []), _run_mail_assist_digest))
_register(Tool("mail.assist.compose", "Compose/send an email using the best available channel (API or browser).",
               _schema({"to": _S, "subject": _S, "body": _S}, ["to", "subject", "body"]), _run_mail_assist_compose))

# ── Enterprise bridge tools ──
_register(Tool("enterprise.command", "Send a command to the paired SHIMS Enterprise instance. Examples: summary, list_dashboard, create_experiment, create_procurement_request, harmonize, create_gst_invoice, create_qms_record, create_lims_sample.",
               _schema({"command": _S, "payload": {"type": "object"}}, ["command"]), _run_enterprise_command))

# ── ChemDFM chemistry R&D tools ──
def _run_chemdfm_query(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    query = str(args.get("query") or "").strip()
    topic = str(args.get("topic") or "general").strip()
    if not query:
        return {"ok": False, "error": "query required"}
    try:
        return asyncio.get_event_loop().run_until_complete(
            __import__("shared.chemdfm_bridge", fromlist=["chemdfm_query"]).chemdfm_query(query, topic)
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_chemdfm_train(args: dict[str, Any]) -> dict[str, Any]:
    fact = str(args.get("fact") or "").strip()
    topic = str(args.get("topic") or "general").strip()
    validated_by = str(args.get("validated_by") or "human").strip()
    if not fact:
        return {"ok": False, "error": "fact required"}
    try:
        return __import__("shared.chemdfm_bridge", fromlist=["chemdfm_train"]).chemdfm_train(fact, topic, validated_by)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("chem.chemdfm_query", "Ask ChemDFM (chemistry foundation model) a question about molecules, reactions, properties, or synthesis. Falls back to rule-based chemistry if ChemDFM is offline.",
               _schema({"query": _S, "topic": _S}, ["query"]), _run_chemdfm_query))
_register(Tool("chem.chemdfm_train", "Train/feed a validated chemistry fact into ChemDFM's iterative learning journal. Topics: synthesis, property, safety, retrosynthesis, general.",
               _schema({"fact": _S, "topic": _S, "validated_by": _S}, ["fact"]), _run_chemdfm_train))

# ── R&D Brain v2 product-intent tool ──
def _run_rd_product_assist(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    product_name = str(args.get("product_name") or "").strip()
    intent = str(args.get("intent") or "inspect").strip()
    if not product_name:
        return {"ok": False, "error": "product_name required"}
    try:
        return asyncio.get_event_loop().run_until_complete(
            __import__("shared.rd_brain_v2", fromlist=["product_intent"]).product_intent(product_name, intent=intent)
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("rd.product_assist", "R&D Brain product-intent assistant. Given a product (e.g. Minoxidil), searches the BMR corpus first and suggests next actions: inspect, search_patents, modify_route, run_bmr_route.",
               _schema({"product_name": _S, "intent": _S}, ["product_name"]), _run_rd_product_assist))

# ── R&D predictive-chemistry guardian (deterministic, offline) ──
def _run_rd_predictive_risks(args: dict[str, Any]) -> dict[str, Any]:
    exp_id = args.get("experiment_id")
    if not isinstance(exp_id, int):
        try:
            exp_id = int(exp_id)
        except Exception:
            return {"ok": False, "error": "experiment_id required"}
    try:
        return __import__("shared.rd_predictive", fromlist=["assess_experiment_id"]).assess_experiment_id(exp_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("rd.predictive_risks", "Run the deterministic R&D predictive-chemistry guardian on an experiment. Returns risk flags, heuristic predictions, and next-trial suggestions without needing any AI provider.",
               _schema({"experiment_id": _I}, ["experiment_id"]), _run_rd_predictive_risks))

# ── Tech Transfer scale-up assessment (deterministic, offline) ──
def _run_tt_assess_scale_up(args: dict[str, Any]) -> dict[str, Any]:
    exp_id = args.get("experiment_id")
    target_batch_kg = args.get("target_batch_kg")
    if not isinstance(exp_id, int):
        try:
            exp_id = int(exp_id)
        except Exception:
            return {"ok": False, "error": "experiment_id required"}
    try:
        target_batch_kg = float(target_batch_kg)
    except Exception:
        return {"ok": False, "error": "target_batch_kg required"}
    try:
        from shared import tech_transfer
        result = tech_transfer.create_tt_project(exp_id, {"target_batch_kg": target_batch_kg, "vessel_id": args.get("vessel_id")})
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("tt.assess_scale_up", "Run deterministic Tech-Transfer scale-up math on an R&D experiment for a target batch size. Returns scaling factor, vessel fit, heat-transfer impact, risk flags, and hold points without needing any AI provider.",
               _schema({"experiment_id": _I, "target_batch_kg": {"type": "number"}, "vessel_id": _I}, ["experiment_id", "target_batch_kg"]), _run_tt_assess_scale_up))

# ── Production readiness engine (deterministic, offline) ──
def _run_production_readiness(args: dict[str, Any]) -> dict[str, Any]:
    product_name = str(args.get("product_name") or "").strip()
    target_batch_kg = args.get("target_batch_kg")
    if not product_name:
        return {"ok": False, "error": "product_name required"}
    try:
        target_batch_kg = float(target_batch_kg)
    except Exception:
        return {"ok": False, "error": "target_batch_kg required"}
    try:
        from shared import production_readiness
        return production_readiness.check_readiness(product_name, target_batch_kg)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("production.check_readiness", "Run the deterministic production readiness engine for a product and target batch size. Checks RM stock, equipment availability/cleanliness, manpower, QC capacity, and approved BMR. Returns per-item blockers and next steps without needing any AI provider.",
               _schema({"product_name": _S, "target_batch_kg": {"type": "number"}}, ["product_name", "target_batch_kg"]), _run_production_readiness))

# ── QC/QA sample linking + audit readiness (deterministic, offline) ──
def _run_qc_link_sample(args: dict[str, Any]) -> dict[str, Any]:
    sample_id = args.get("sample_id")
    source_type = str(args.get("source_type") or "").strip()
    source_id = args.get("source_id")
    try:
        sample_id = int(sample_id)
        source_id = int(source_id)
    except Exception:
        return {"ok": False, "error": "sample_id and source_id required"}
    try:
        from shared import qa_qc
        return qa_qc.link_sample(sample_id, source_type, source_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_qa_classify_deviation(args: dict[str, Any]) -> dict[str, Any]:
    text = str(args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "text required"}
    try:
        from shared import qa_qc
        return {"ok": True, **qa_qc.classify_deviation(text)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_qa_audit_readiness(args: dict[str, Any]) -> dict[str, Any]:
    product_name = str(args.get("product_name") or "").strip() or None
    try:
        from shared import qa_qc
        return qa_qc.audit_readiness_score(product_name)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("qc.link_sample", "Link a LIMS sample to its source: an R&D experiment (source_type='experiment') or a plant batch/work order (source_type='batch').",
               _schema({"sample_id": _I, "source_type": _S, "source_id": _I}, ["sample_id", "source_type", "source_id"]), _run_qc_link_sample))
_register(Tool("qa.classify_deviation", "Classify a deviation/capa/change-control description by severity (minor/major/critical) and category. Returns recommended reviewers and whether CAPA is required.",
               _schema({"text": _S}, ["text"]), _run_qa_classify_deviation))
_register(Tool("qa.audit_readiness", "Compute the deterministic audit-readiness score across SOP coverage, training effectiveness, open QMS records, and LIMS OOS status. No AI provider needed.",
               _schema({"product_name": _S}, []), _run_qa_audit_readiness))

# ── Environmental / EHS engine (deterministic, offline) ──
def _run_ehs_batch_balance(args: dict[str, Any]) -> dict[str, Any]:
    product_name = str(args.get("product_name") or "").strip()
    batch_size = args.get("batch_size_kg")
    if not product_name:
        return {"ok": False, "error": "product_name required"}
    try:
        batch_size = float(batch_size)
    except Exception:
        return {"ok": False, "error": "batch_size_kg required"}
    try:
        from shared import ehs_balance
        return ehs_balance.batch_material_balance(
            product_name,
            batch_size,
            args.get("raw_materials", []),
            args.get("outputs"),
            args.get("recovery_paths"),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_ehs_ec_check(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from shared import ehs_balance
        return ehs_balance.ec_limit_check(
            product_mix_count=args.get("product_mix_count"),
            total_effluent_kld=args.get("total_effluent_kld", 0.0),
            fresh_water_kld=args.get("fresh_water_kld", 0.0),
            high_cod_kld=args.get("high_cod_kld"),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_ehs_waste_match(args: dict[str, Any]) -> dict[str, Any]:
    waste_name = str(args.get("waste_name") or "").strip()
    if not waste_name:
        return {"ok": False, "error": "waste_name required"}
    try:
        from shared import ehs_balance
        return {"ok": True, "matches": ehs_balance.waste_rm_match(waste_name)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("ehs.batch_balance", "Compute deterministic environmental material balance for a batch: inputs, outputs, waste, recovery, emissions, scrubber/CETP load, and recovery evaluation. No AI provider needed.",
               _schema({"product_name": _S, "batch_size_kg": {"type": "number"}, "raw_materials": {"type": "array"}, "outputs": {"type": "array"}, "recovery_paths": {"type": "array"}}, ["product_name", "batch_size_kg", "raw_materials"]), _run_ehs_batch_balance))
_register(Tool("ehs.ec_check", "Check plant-level product count, effluent, and fresh-water totals against EC limits. Returns guardrail flags. No AI provider needed.",
               _schema({"product_mix_count": _I, "total_effluent_kld": {"type": "number"}, "fresh_water_kld": {"type": "number"}}, []), _run_ehs_ec_check))
_register(Tool("ehs.waste_match", "Suggest whether a waste stream could be reused as someone else's raw material (internal inventory or process input).",
               _schema({"waste_name": _S}, ["waste_name"]), _run_ehs_waste_match))

# ── Regulatory / DMF builder (deterministic, offline) ──
def _run_regulatory_dmf_build(args: dict[str, Any]) -> dict[str, Any]:
    api_name = str(args.get("api_name") or "").strip()
    if not api_name:
        return {"ok": False, "error": "api_name required"}
    try:
        from shared import dmf_builder
        return dmf_builder.create_dmf({
            "api_name": api_name,
            "holder_name": args.get("holder_name", ""),
            "holder_address": args.get("holder_address", ""),
            "site_address": args.get("site_address", ""),
            "source_type": args.get("source_type"),
            "source_id": args.get("source_id"),
            "autofill": args.get("autofill", True),
        })
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_regulatory_dmf_gap(args: dict[str, Any]) -> dict[str, Any]:
    try:
        dmf_id = int(args.get("dmf_id"))
    except Exception:
        return {"ok": False, "error": "dmf_id required"}
    try:
        from shared import dmf_builder
        return dmf_builder.dmf_gap_analysis(dmf_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("regulatory.dmf_build", "Create a Type II API DMF skeleton (Open Part + Closed Part) for a drug substance. Auto-fills from product/R&D data when available. No AI provider needed.",
               _schema({"api_name": _S, "holder_name": _S, "holder_address": _S, "site_address": _S, "source_type": _S, "source_id": _I, "autofill": {"type": "boolean"}}, ["api_name"]), _run_regulatory_dmf_build))
_register(Tool("regulatory.dmf_gap", "Run a deterministic gap analysis on a DMF record and return a completeness score plus missing required/optional sections.",
               _schema({"dmf_id": _I}, ["dmf_id"]), _run_regulatory_dmf_gap))

# ── Warehouse / procurement intelligence (deterministic, offline) ──
def _run_warehouse_ledger_add(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from shared import warehouse_engine
        return warehouse_engine.add_ledger_entry(
            material_name=str(args.get("material_name") or ""),
            item_type=str(args.get("item_type") or ""),
            quantity=float(args.get("quantity") or 0),
            unit=str(args.get("unit") or "kg"),
            source_batch=args.get("source_batch"),
            source_type=args.get("source_type"),
            source_id=args.get("source_id"),
            quality_grade=args.get("quality_grade"),
            notes=args.get("notes"),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_warehouse_procurement_check(args: dict[str, Any]) -> dict[str, Any]:
    material_name = str(args.get("material_name") or "").strip()
    if not material_name:
        return {"ok": False, "error": "material_name required"}
    try:
        quantity = float(args.get("quantity") or 0)
    except Exception:
        return {"ok": False, "error": "quantity must be a number"}
    try:
        from shared import warehouse_engine
        return warehouse_engine.procurement_cross_check(material_name, quantity, str(args.get("unit") or "kg"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_warehouse_reorder_alerts(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from shared import warehouse_engine
        return {"ok": True, "alerts": warehouse_engine.reorder_alerts()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("warehouse.add_ledger", "Add a waste, recovered, or sellable material entry to the warehouse ledger and get reuse-match suggestions.",
               _schema({"material_name": _S, "item_type": {"enum": ["waste", "recovered", "sellable"]}, "quantity": {"type": "number"}, "unit": _S, "source_batch": _S, "source_type": _S, "source_id": _I, "quality_grade": _S, "notes": _S}, ["material_name", "item_type", "quantity"]), _run_warehouse_ledger_add))
_register(Tool("warehouse.procurement_check", "Cross-check a procurement request before raising a PO: duplicate open requests, available stock, recovered alternatives, and vendor suggestions. No AI provider needed.",
               _schema({"material_name": _S, "quantity": {"type": "number"}, "unit": _S}, ["material_name", "quantity"]), _run_warehouse_procurement_check))
_register(Tool("warehouse.reorder_alerts", "List inventory items at or below minimum stock.",
               _schema({}, []), _run_warehouse_reorder_alerts))

# ── Sales / accounting margin engine (deterministic, offline) ──
def _run_sales_order_margin(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from shared import margin_engine
        return margin_engine.record_sales_order(args)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_sales_margin_report(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from shared import margin_engine
        return margin_engine.margin_report()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_sales_demand_feasibility(args: dict[str, Any]) -> dict[str, Any]:
    product_name = str(args.get("product_name") or "").strip()
    if not product_name:
        return {"ok": False, "error": "product_name required"}
    try:
        quantity = float(args.get("quantity") or 0)
    except Exception:
        return {"ok": False, "error": "quantity must be a number"}
    try:
        from shared import margin_engine
        return margin_engine.demand_feasibility(product_name, quantity, args.get("delivery_date"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("sales.order_margin", "Record a sales order and immediately compute estimated cost per unit, margin per unit, and margin percentage using route RM prices. No AI provider needed.",
               _schema({"product_name": _S, "quantity": {"type": "number"}, "unit_price": {"type": "number"}, "unit": _S, "customer_name": _S, "order_date": _S, "delivery_date": _S, "notes": _S}, ["product_name", "quantity", "unit_price"]), _run_sales_order_margin))
_register(Tool("sales.margin_report", "Return the overall margin report: total revenue, total margin, by-product breakdown, and low-margin products (< 15%).",
               _schema({}, []), _run_sales_margin_report))
_register(Tool("sales.demand_feasibility", "Check whether a customer demand quantity can be produced by a delivery date and estimate cost per kg.",
               _schema({"product_name": _S, "quantity": {"type": "number"}, "delivery_date": _S}, ["product_name", "quantity"]), _run_sales_demand_feasibility))

# ── Background jobs / autonomy hooks ──
def _run_bg_ensure_jobs(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from shared.background_jobs import ensure_default_jobs
        return ensure_default_jobs()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_bg_list_jobs(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from shared.background_jobs import list_background_jobs
        return {"ok": True, "jobs": list_background_jobs()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_bg_ingest_inbox(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from shared.background_jobs import run_inbox_ingest
        return run_inbox_ingest(args or {})
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("background.ensure_jobs", "Ensure the default recurring background jobs are scheduled (improvement loop, self-indexer, media inbox).", _schema({}, []), _run_bg_ensure_jobs))
_register(Tool("background.list_jobs", "List all scheduled background jobs.", _schema({}, []), _run_bg_list_jobs))
_register(Tool("background.ingest_inbox", "Manually scan data/inbox and ingest media/documents into the omni-brain.", _schema({}, []), _run_bg_ingest_inbox))

_register(Tool("coder.spawn", "Hand a self-contained coding project to the background Coder (plan→write→run→fix). It runs in the background and streams progress; you keep talking to the user.",
               _schema({"goal": _S, "name": _S}, ["goal"]), _run_coder_spawn))
_register(Tool("coder.status", "Check the status of a background coder job.",
               _schema({"job_id": _S}, ["job_id"]), _run_coder_status))
_register(Tool("skill.learn", "Remember a reusable skill / preference / how-to so you apply it in future turns.",
               _schema({"name": _S, "instructions": _S, "tags": {"type": "array", "items": _S}}, ["name", "instructions"]), _run_skill_learn))
_register(Tool("skill.create_tool", "Create a dynamic tool from Python code and register it for this session. The code must define a top-level run(args) function returning a dict.",
               _schema({"name": _S, "tool_name": _S, "description": _S, "code": _S, "tags": {"type": "array", "items": _S}}, ["name", "tool_name", "code"]), _run_skill_create_tool))
_register(Tool("skill.execute", "Run a learned skill by name or ID.",
               _schema({"name": _S, "skill_id": _S, "args": {"type": "object"}}, []), _run_skill_execute))
_register(Tool("skill.list", "List learned skills, optionally filtered by query.",
               _schema({"query": _S, "limit": _I}, []), _run_skill_list))
_register(Tool("self.patch", "Modify SHIMS's OWN source code (backend/frontend/shared). The change is validated in a sandbox and shown to the user as a diff for approval before it goes live. Provide either full new_content or natural-language instructions.",
               _schema({"path": _S, "instructions": _S, "new_content": _S, "reason": _S}, ["path"]), _run_self_patch,
               lambda a: "gated"))
_register(Tool("self.inspect", "Inspect SHIMS's own code for issues (test failures, lint errors, or a specific file) and create a real validated patch proposal. The patch is NOT applied automatically — it goes to the Self-Evolution pane for human review.",
               _schema({"scope": _S, "relative_path": _S, "goal": _S, "test_path": _S}, ["scope"]), _run_self_inspect))
_register(Tool("prompt.list_variants", "List prompt variants in the evolution lab.",
               _schema({"limit": _I}, []), _run_prompt_list_variants))
_register(Tool("prompt.run_eval", "Run the eval suite against a prompt variant and update its score.",
               _schema({"variant_id": _S}, ["variant_id"]), _run_prompt_run_eval))
_register(Tool("prompt.promote", "Promote the best-scoring prompt variant to active.",
               _schema({"variant_id": _S}, ["variant_id"]), _run_prompt_promote))
_register(Tool("improvement.run_cycle", "Run the evaluation-driven improvement cycle (reliability + latency + prompt evals) and generate proposals.",
               _schema({"system_prompt": _S}, []), _run_improvement_cycle))
_register(Tool("improvement.list_runs", "List recent improvement-loop runs.",
               _schema({"limit": _I}, []), _run_improvement_runs))
_register(Tool("improvement.cross_instance_sync", "Bidirectionally sync improvement proposals with the peer SHIMS instance (primary ↔ local factory).",
               _schema({"peer_id": _S, "local_proposals": _A}, []), _run_improvement_cross_instance_sync))
_register(Tool("vision.describe", "Describe an image using the best available vision backend (Claude, Ollama vision model, or Gemini).",
               _schema({"source": _S, "prompt": _S, "backend": _S}, ["source"]), _run_vision_describe))

# ── Coder v3 tools ──
_register(Tool("coder.create_project", "Create a new Coder project. Returns the project id.",
               _schema({"name": _S, "template": _S}, ["name"]), _run_coder_create_project))
_register(Tool("coder.read_file", "Read a file inside a Coder project.",
               _schema({"project_id": _S, "file_path": _S}, ["project_id", "file_path"]), _run_coder_read_file))
_register(Tool("coder.write_file", "Write or overwrite a file inside a Coder project.",
               _schema({"project_id": _S, "file_path": _S, "content": _S}, ["project_id", "file_path", "content"]), _run_coder_write_file,
               lambda a: _write_risk(a.get("file_path"))))
_register(Tool("coder.run_shell", "Run a shell command inside a Coder project directory.",
               _schema({"project_id": _S, "command": _S, "timeout": _I}, ["project_id", "command"]), _run_coder_run_shell, _shell_risk))
_register(Tool("coder.run_project", "Run the entry file of a Coder project.",
               _schema({"project_id": _S, "entry_file": _S}, ["project_id"]), _run_coder_run_project))
_register(Tool("coder.search", "Search across all files in a Coder project.",
               _schema({"project_id": _S, "query": _S, "regex": {"type": "boolean"}, "case_sensitive": {"type": "boolean"}, "file_pattern": _S}, ["project_id", "query"]), _run_coder_search))
_register(Tool("coder.install", "Install packages (pip/npm/etc.) for a Coder project.",
               _schema({"project_id": _S, "packages": {"type": "array", "items": _S}, "manager": _S}, ["project_id", "packages"]), _run_coder_install))
_register(Tool("coder.git_commit", "Git commit all changes in a Coder project.",
               _schema({"project_id": _S, "message": _S}, ["project_id", "message"]), _run_coder_git_commit))
_register(Tool("coder.fold_project", "Fold a completed Coder project into the SHIMS main tree as self.patch proposals. Set auto_apply=true to apply immediately if omnipotent mode is enabled.",
               _schema({"project_id": _S, "target_dir": _S, "auto_apply": {"type": "boolean"}}, ["project_id", "target_dir"]), _run_coder_fold,
               lambda a: "gated"))

# ── Neural Agent tools ──
_register(Tool("neural.generate_proposal", "Generate a self-evolution patch proposal. The AI reads the file, thinks about the intent, and creates a patch with diff.",
               _schema({"intent": _S, "file_path": _S, "instructions": _S}, ["intent"]), _run_neural_generate_proposal))
_register(Tool("neural.test_proposal", "Run sandbox tests on a pending proposal.",
               _schema({"proposal_id": _S}, ["proposal_id"]), _run_neural_test_proposal))
_register(Tool("neural.apply_proposal", "Apply an approved proposal to live code. Creates a backup and rolls back if validation fails.",
               _schema({"proposal_id": _S}, ["proposal_id"]), _run_neural_apply_proposal,
               lambda a: "gated"))
_register(Tool("neural.reflect", "Run a reflection cycle — the AI reviews recent changes and generates improvement proposals.",
               _schema({}), _run_neural_reflect))
_register(Tool("agent.suggest_tools", "Predict the most relevant tools for a goal or conversation context. Returns scored suggestions.",
               _schema({"goal": _S, "context": _S}, ["goal"]), _run_agent_suggest_tools))
_register(Tool("agent.swarm", "Dispatch multiple SHIMS agents in parallel with targeted prompts and filtered tools, then synthesize a unified answer.",
               _schema({"prompt": _S, "agent_ids": _A, "context": _O, "shared_context": _O}, ["prompt"]), _run_agent_swarm))

# ── App Factory tools ──
_register(Tool("app_factory.design_app",
               "Design a new SHIMS vertical app from a domain brief. Returns a structured spec (entities, routes, UI tabs, roles, AI endpoints).",
               _schema({"domain": _S, "title": _S, "prefix": _S, "features": _A, "roles": _A, "ai_features": {"type": "boolean"}, "voice_languages": _A},
                       ["domain"]), _run_app_factory_design_app))
_register(Tool("app_factory.build_app",
               "Scaffold and generate a complete SHIMS vertical app from a spec, mount it in the backend, add an Omni launcher tile, and run tests. Gated because it writes source files.",
               _schema({"spec": _O, "app_name": _S, "title": _S, "prefix": _S}, []), _run_app_factory_build_app, _app_factory_risk))
_register(Tool("app_factory.evolve_app",
               "Modify an existing SHIMS vertical app using the agent swarm. Gated because it edits source files.",
               _schema({"app_name": _S, "change_request": _S}, ["app_name", "change_request"]), _run_app_factory_evolve_app, _app_factory_risk))
_register(Tool("app_factory.test_app",
               "Run pytest for a SHIMS vertical app.",
               _schema({"app_name": _S}, ["app_name"]), _run_app_factory_test_app))
_register(Tool("app_factory.diagnose_app",
               "Diagnose common SHIMS vertical-app issues (static paths, auth router, tests).",
               _schema({"app_name": _S}, ["app_name"]), _run_app_factory_diagnose_app))
_register(Tool("app_factory.repair_app",
               "Apply safe automatic fixes to a SHIMS vertical app (static paths, auth router). Gated because it edits source files.",
               _schema({"app_name": _S}, ["app_name"]), _run_app_factory_repair_app, _app_factory_risk))

# ── Background task tools ──
_register(Tool("task.check_status", "Check the status and result of a background task by its ID.",
               _schema({"task_id": _S}, ["task_id"]), _run_task_check_status))
_register(Tool("task.list", "List recent background tasks, optionally filtered by status (queued, running, done, failed, cancelled).",
               _schema({"status": _S, "limit": _I}, []), _run_task_list))

# ── Desktop / sandbox tools ──
_register(Tool("desktop.run_python", "Run Python code in a temporary sandbox. stdout/stderr are returned.",
               _schema({"code": _S, "timeout": _I}, ["code"]), _run_desktop_run_python))
_register(Tool("desktop.interpreter", "Run Python code with automatic figure/artifact capture for data analysis. Use for charts, calculations, CSV/JSON exploration.",
               _schema({"code": _S, "timeout": _I}, ["code"]), _run_desktop_interpreter))
_register(Tool("desktop.bridge", "Control the paired Desktop Bridge on the user's machine. Use for screenshots, shell commands, finding files, reading/writing desktop files, and checking desktop system info. The bridge must already be running and paired.",
               _schema({"action": _S, "command": _S, "cwd": _S, "timeout": _I, "name": _S, "root": _S, "path": _S, "content": _S}, ["action"]), _run_desktop_bridge))

# ── Long-horizon planner & scheduler ──
_register(Tool("plan.create", "Create a multi-step plan from a goal. Provide steps as a list, or let SHIMS split the goal.",
               _schema({"goal": _S, "steps": _A, "context": _O}, ["goal"]), _run_plan_create))
_register(Tool("plan.list", "List active/completed plans.",
               _schema({"status": _S, "limit": _I}, []), _run_plan_list))
_register(Tool("plan.get", "Get one plan by ID with all steps and results.",
               _schema({"plan_id": _S}, ["plan_id"]), _run_plan_get))
_register(Tool("plan.cancel", "Cancel a running plan.",
               _schema({"plan_id": _S}, ["plan_id"]), _run_plan_cancel))
_register(Tool("plan.run_wave", "Execute the next ready wave of a plan.",
               _schema({"plan_id": _S}, ["plan_id"]), _run_plan_run_wave))
_register(Tool("plan.run", "Run a plan to completion (up to max_waves waves).",
               _schema({"plan_id": _S, "max_waves": _I}, ["plan_id"]), _run_plan_run_to_completion))
_register(Tool("plan.suggest", "Suggest a previously learned plan for a goal based on keyword matching.",
               _schema({"goal": _S}, ["goal"]), _run_plan_suggest))
_register(Tool("plan.learn", "Scan completed plans and convert them into reusable skills.",
               _schema({"min_steps": _I, "limit": _I}, []), _run_plan_learn))
_register(Tool("schedule.create", "Schedule a repeating or one-time task. schedule_type is once/interval/cron; action_type is tool/plan/message.",
               _schema({"title": _S, "schedule_type": _S, "when": _S, "action_type": _S, "payload": _O},
                       ["title", "schedule_type", "when", "action_type", "payload"]), _run_schedule_create))
_register(Tool("schedule.list", "List scheduled tasks.",
               _schema({"enabled_only": {"type": "boolean"}, "limit": _I}, []), _run_schedule_list))
_register(Tool("schedule.cancel", "Cancel a scheduled task by ID.",
               _schema({"task_id": _S}, ["task_id"]), _run_schedule_cancel))
_register(Tool("memory.save", "Save a fact or insight to long-term memory for future retrieval.",
               _schema({"content": _S, "key": _S, "namespace": _S, "tags": _A, "source": _S, "weight": {"type": "number"}}, ["content"]), _run_memory_save))
_register(Tool("memory.search", "Search long-term memory by semantic similarity.",
               _schema({"query": _S, "limit": _I}, ["query"]), _run_memory_search))
_register(Tool("memory.ingest_media", "Ingest an image, audio, video, or screen capture into searchable long-term memory.",
               _schema({"path": _S, "kind": _S, "title": _S, "tags": _A, "metadata": _O}, ["path", "kind"]), _run_media_ingest))
_register(Tool("brain.self_index", "Phase 3.1 Self-indexer: crawl allowed SHIMS source roots and store semantic chunks in the omni-brain under the shims_source namespace.",
               _schema({"force": {"type": "boolean"}}, []), _run_brain_self_index))
_register(Tool("conversation.summarize", "Summarize a conversation session into key facts and decisions for context compression.",
               _schema({"session_id": _S, "topic": _S, "limit": _I}, ["session_id"]), _run_conversation_summarize))
_register(Tool("media.generate_image", "Generate an image from a text prompt. Uses Pollinations.ai by default (free, no API key).",
               _schema({"prompt": _S, "backend": _S, "width": _I, "height": _I}, ["prompt"]), _run_media_generate_image))
_register(Tool("media.generate_video", "Generate a video from a text prompt. Currently routes to the async media endpoint; returns guidance.",
               _schema({"prompt": _S, "backend": _S}, ["prompt"]), _run_media_generate_video))

def _run_mail_status(args: dict[str, Any]) -> dict[str, Any]:
    from .mail_assistant import check_mail_status
    try:
        return asyncio.run(check_mail_status())
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _run_mail_digest(args: dict[str, Any]) -> dict[str, Any]:
    from .mail_assistant import mail_digest
    try:
        return asyncio.run(mail_digest(limit=int(args.get("limit") or 10)))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _run_mail_compose(args: dict[str, Any]) -> dict[str, Any]:
    from .mail_assistant import mail_compose
    to = str(args.get("to") or "").strip()
    subject = str(args.get("subject") or "").strip()
    body = str(args.get("body") or "").strip()
    if not to or not subject:
        return {"ok": False, "error": "to and subject required"}
    try:
        return asyncio.run(mail_compose(to, subject, body))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _run_mail_organize(args: dict[str, Any]) -> dict[str, Any]:
    from .mail_assistant import mail_organize
    criteria = str(args.get("criteria") or "").strip()
    action = str(args.get("action") or "label").strip()
    if not criteria:
        return {"ok": False, "error": "criteria required"}
    try:
        return asyncio.run(mail_organize(criteria, action))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}




def _run_enterprise_status(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    import httpx, os
    from .config import settings
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    try:
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{url}/health")
            return {"ok": r.status_code < 400, "enterprise": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200], "url": url}
    except Exception as exc:
        return {"ok": False, "url": url, "detail": str(exc)[:220]}


def _run_enterprise_command(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    import httpx, os
    from .config import settings
    cmd = str(args.get("command") or "").strip()
    payload = args.get("payload") or {}
    if not cmd:
        return {"ok": False, "error": "command required"}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            r = client.post(f"{url}/api/bridge/command", json={"command": cmd, "payload": payload}, headers=headers)
            r.raise_for_status()
            data = r.json()
            success = data.get("ok") or str(data.get("status", "")).lower() in {"ok", "success"}
            return {"ok": success, **data}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:260]}


def _run_enterprise_dashboard(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    return _run_enterprise_command({"command": "summary", "payload": {}})


def _run_enterprise_list_commands(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    return {"ok": True, "commands": [
        {"cmd": "summary", "desc": "Factory-wide harmonized summary"},
        {"cmd": "list_dashboard", "desc": "Department dashboard data"},
        {"cmd": "create_experiment", "desc": "Create R&D experiment record"},
        {"cmd": "create_procurement_request", "desc": "Create procurement request"},
        {"cmd": "harmonize", "desc": "Cross-department harmonization analysis"},
        {"cmd": "create_gst_invoice", "desc": "Generate GST e-invoice"},
        {"cmd": "create_ewaybill", "desc": "Generate e-waybill"},
        {"cmd": "create_qms_record", "desc": "Create QMS deviation/CAPA/change control"},
        {"cmd": "create_lims_sample", "desc": "Create LIMS sample record"},
        {"cmd": "create_ebr_step", "desc": "Create eBR batch step"},
        {"cmd": "create_document", "desc": "Create quotation, PO, SOP, lab notebook"},
        {"cmd": "run_ai_lab", "desc": "Run AI lab process design or document formatting"},
    ]}


def _run_enterprise_lims(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    action = str(args.get("action") or "").strip()
    payload = args.get("payload") or {}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            if action == "list_samples":
                r = client.get(f"{url}/api/lims/samples", headers=headers, params=payload)
            elif action == "get_sample":
                r = client.get(f"{url}/api/lims/samples/{payload.get('sample_id')}", headers=headers)
            elif action == "create_sample":
                r = client.post(f"{url}/api/lims/samples", json=payload, headers=headers)
            elif action == "update_sample":
                r = client.put(f"{url}/api/lims/samples/{payload.get('sample_id')}", json=payload, headers=headers)
            elif action == "add_test":
                r = client.post(f"{url}/api/lims/samples/{payload.get('sample_id')}/tests", json=payload, headers=headers)
            elif action == "list_stability":
                r = client.get(f"{url}/api/lims/stability", headers=headers, params=payload)
            else:
                return {"ok": False, "error": f"Unknown LIMS action: {action}"}
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_enterprise_equipment(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    action = str(args.get("action") or "").strip()
    payload = args.get("payload") or {}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            if action == "list_work_orders":
                r = client.get(f"{url}/api/equipment/work-orders", headers=headers, params=payload)
            elif action == "create_work_order":
                r = client.post(f"{url}/api/equipment/work-orders", json=payload, headers=headers)
            elif action == "update_work_order":
                r = client.put(f"{url}/api/equipment/work-orders/{payload.get('wo_id')}", json=payload, headers=headers)
            elif action == "complete_work_order":
                r = client.post(f"{url}/api/equipment/work-orders/{payload.get('wo_id')}/complete", json=payload, headers=headers)
            elif action == "calibration_due":
                r = client.get(f"{url}/api/equipment/calibration-due", headers=headers, params=payload)
            elif action == "schedule_maintenance":
                r = client.post(f"{url}/api/equipment/schedule-maintenance", json=payload, headers=headers)
            else:
                return {"ok": False, "error": f"Unknown equipment action: {action}"}
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_enterprise_mes(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    action = str(args.get("action") or "").strip()
    payload = args.get("payload") or {}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            if action == "list_batches":
                r = client.get(f"{url}/api/mes/batches", headers=headers, params=payload)
            elif action == "get_batch":
                r = client.get(f"{url}/api/mes/batches/{payload.get('batch_no')}", headers=headers)
            elif action == "create_batch":
                r = client.post(f"{url}/api/mes/batches", json=payload, headers=headers)
            elif action == "transition_batch":
                r = client.post(f"{url}/api/mes/batches/{payload.get('batch_no')}/transition", json=payload, headers=headers)
            elif action == "execute_ebr":
                r = client.post(f"{url}/api/mes/ebr/{payload.get('ebr_id')}/execute", json=payload, headers=headers)
            elif action == "qa_review_ebr":
                r = client.post(f"{url}/api/mes/ebr/{payload.get('ebr_id')}/qa-review", json=payload, headers=headers)
            elif action == "verify_line_clearance":
                r = client.post(f"{url}/api/mes/line-clearance/{payload.get('lc_id')}/verify", json=payload, headers=headers)
            elif action == "create_dispensing":
                r = client.post(f"{url}/api/mes/material-dispensing", json=payload, headers=headers)
            elif action == "dispense_material":
                r = client.post(f"{url}/api/mes/material-dispensing/{payload.get('disp_id')}/dispense", json=payload, headers=headers)
            elif action == "create_ipc":
                r = client.post(f"{url}/api/mes/in-process-checks", json=payload, headers=headers)
            elif action == "record_ipc":
                r = client.post(f"{url}/api/mes/in-process-checks/{payload.get('ipc_id')}/record", json=payload, headers=headers)
            elif action == "reserve_equipment":
                r = client.post(f"{url}/api/mes/batches/{payload.get('batch_no')}/reserve-equipment", json=payload, headers=headers)
            elif action == "release_equipment":
                r = client.post(f"{url}/api/mes/batches/{payload.get('batch_no')}/release-equipment", json=payload, headers=headers)
            else:
                return {"ok": False, "error": f"Unknown MES action: {action}"}
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_enterprise_training(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    action = str(args.get("action") or "").strip()
    payload = args.get("payload") or {}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            if action == "list_assignments":
                r = client.get(f"{url}/api/training/assignments", headers=headers, params=payload)
            elif action == "create_assignment":
                r = client.post(f"{url}/api/training/assignments", json=payload, headers=headers)
            elif action == "update_assignment":
                r = client.put(f"{url}/api/training/assignments/{payload.get('assignment_id')}", json=payload, headers=headers)
            elif action == "complete_assignment":
                r = client.post(f"{url}/api/training/assignments/{payload.get('assignment_id')}/complete", json=payload, headers=headers)
            elif action == "compliance_dashboard":
                r = client.get(f"{url}/api/training/compliance", headers=headers)
            elif action == "overdue":
                r = client.get(f"{url}/api/training/overdue", headers=headers, params=payload)
            else:
                return {"ok": False, "error": f"Unknown training action: {action}"}
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_enterprise_supplier(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    action = str(args.get("action") or "").strip()
    payload = args.get("payload") or {}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            if action == "list_qualifications":
                r = client.get(f"{url}/api/supplier/qualifications", headers=headers, params=payload)
            elif action == "create_qualification":
                r = client.post(f"{url}/api/supplier/qualifications", json=payload, headers=headers)
            elif action == "update_qualification":
                r = client.put(f"{url}/api/supplier/qualifications/{payload.get('qual_id')}", json=payload, headers=headers)
            elif action == "approve_qualification":
                r = client.post(f"{url}/api/supplier/qualifications/{payload.get('qual_id')}/approve", json=payload, headers=headers)
            elif action == "record_audit":
                r = client.post(f"{url}/api/supplier/qualifications/{payload.get('qual_id')}/audit", json=payload, headers=headers)
            elif action == "audit_due":
                r = client.get(f"{url}/api/supplier/audit-due", headers=headers, params=payload)
            elif action == "risk_summary":
                r = client.get(f"{url}/api/supplier/risk-summary", headers=headers)
            elif action == "auto_schedule_audits":
                r = client.post(f"{url}/api/supplier/auto-schedule-audits", json=payload, headers=headers)
            else:
                return {"ok": False, "error": f"Unknown supplier action: {action}"}
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_enterprise_analytics(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    action = str(args.get("action") or "").strip()
    payload = args.get("payload") or {}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            if action == "kpi":
                r = client.get(f"{url}/api/analytics/kpi", headers=headers)
            elif action == "trends":
                r = client.get(f"{url}/api/analytics/trends", headers=headers, params=payload)
            else:
                return {"ok": False, "error": f"Unknown analytics action: {action}"}
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_enterprise_notifications(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    action = str(args.get("action") or "").strip()
    payload = args.get("payload") or {}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            if action == "list":
                r = client.get(f"{url}/api/notifications", headers=headers, params=payload)
            elif action == "create":
                r = client.post(f"{url}/api/notifications", json=payload, headers=headers)
            elif action == "mark_read":
                r = client.post(f"{url}/api/notifications/{payload.get('note_id')}/read", headers=headers)
            elif action == "auto_check":
                r = client.get(f"{url}/api/notifications/auto-check", headers=headers)
            else:
                return {"ok": False, "error": f"Unknown notification action: {action}"}
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_enterprise_dms(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    action = str(args.get("action") or "").strip()
    payload = args.get("payload") or {}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            if action == "list_documents":
                r = client.get(f"{url}/api/dms/documents", headers=headers, params=payload)
            elif action == "create_document":
                r = client.post(f"{url}/api/dms/documents", json=payload, headers=headers)
            elif action == "update_document":
                r = client.put(f"{url}/api/dms/documents/{payload.get('doc_id')}", json=payload, headers=headers)
            elif action == "transition":
                r = client.post(f"{url}/api/dms/documents/{payload.get('doc_id')}/transition", json=payload, headers=headers)
            elif action == "new_version":
                r = client.post(f"{url}/api/dms/documents/{payload.get('doc_id')}/version", json=payload, headers=headers)
            else:
                return {"ok": False, "error": f"Unknown DMS action: {action}"}
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_enterprise_ehs(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    action = str(args.get("action") or "").strip()
    payload = args.get("payload") or {}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            if action == "list_incidents":
                r = client.get(f"{url}/api/ehs/incidents", headers=headers, params=payload)
            elif action == "create_incident":
                r = client.post(f"{url}/api/ehs/incidents", json=payload, headers=headers)
            elif action == "update_incident":
                r = client.put(f"{url}/api/ehs/incidents/{payload.get('incident_id')}", json=payload, headers=headers)
            elif action == "summary":
                r = client.get(f"{url}/api/ehs/summary", headers=headers)
            else:
                return {"ok": False, "error": f"Unknown EHS action: {action}"}
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_enterprise_rim(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    action = str(args.get("action") or "").strip()
    payload = args.get("payload") or {}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            if action == "list_submissions":
                r = client.get(f"{url}/api/rim/submissions", headers=headers, params=payload)
            elif action == "create_submission":
                r = client.post(f"{url}/api/rim/submissions", json=payload, headers=headers)
            elif action == "update_submission":
                r = client.put(f"{url}/api/rim/submissions/{payload.get('sub_id')}", json=payload, headers=headers)
            elif action == "list_commitments":
                r = client.get(f"{url}/api/rim/commitments", headers=headers, params=payload)
            elif action == "create_commitment":
                r = client.post(f"{url}/api/rim/commitments", json=payload, headers=headers)
            elif action == "upcoming":
                r = client.get(f"{url}/api/rim/upcoming", headers=headers, params=payload)
            else:
                return {"ok": False, "error": f"Unknown RIM action: {action}"}
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


def _run_chem_chemdfm_query(args: dict[str, Any]) -> dict[str, Any]:
    from .chemdfm_bridge import chemdfm_query
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query required"}
    try:
        return asyncio.run(chemdfm_query(query, topic=args.get("topic", "general")))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _run_chem_chemdfm_train(args: dict[str, Any]) -> dict[str, Any]:
    from .chemdfm_bridge import chemdfm_train
    fact = str(args.get("fact") or "").strip()
    if not fact:
        return {"ok": False, "error": "fact required"}
    return chemdfm_train(fact, topic=args.get("topic", "general"), validated_by=args.get("validated_by", "human"))


def _run_chem_chemdfm_journal(args: dict[str, Any]) -> dict[str, Any]:
    from .chemdfm_bridge import get_journal_summary, chemdfm_iterative_learn
    mode = str(args.get("mode") or "summary").strip()
    if mode == "learn":
        return chemdfm_iterative_learn()
    return get_journal_summary(limit=int(args.get("limit") or 100))

_register(Tool("mail.status", "Check available mail channels (Gmail API vs browser).",
               _schema({}), _run_mail_status))
_register(Tool("mail.digest", "Get a digest of recent unread mail.",
               _schema({"limit": _I}, []), _run_mail_digest))
_register(Tool("mail.compose", "Compose or send an email via API or browser compose URL.",
               _schema({"to": _S, "subject": _S, "body": _S}, ["to", "subject", "body"]), _run_mail_compose))
_register(Tool("mail.organize", "Organize mail matching criteria (label, archive, delete).",
               _schema({"criteria": _S, "action": _S}, ["criteria"]), _run_mail_organize))
_register(Tool("enterprise.status", "Check if SHIMS Enterprise is online and reachable.",
               _schema({}), _run_enterprise_status))
_register(Tool("enterprise.commands", "List available Enterprise bridge commands.",
               _schema({}), _run_enterprise_list_commands))
_register(Tool("enterprise.dashboard", "Get the Enterprise factory-wide dashboard summary.",
               _schema({}), _run_enterprise_dashboard))
_register(Tool("enterprise.lims", "Query or modify Enterprise LIMS (samples, tests, stability). Actions: list_samples, get_sample, create_sample, update_sample, add_test, list_stability.",
               _schema({"action": _S, "payload": _O}, ["action"]), _run_enterprise_lims))
_register(Tool("enterprise.equipment", "Query or modify Enterprise equipment (work orders, calibration, maintenance). Actions: list_work_orders, create_work_order, update_work_order, complete_work_order, calibration_due, schedule_maintenance.",
               _schema({"action": _S, "payload": _O}, ["action"]), _run_enterprise_equipment))
_register(Tool("enterprise.mes", "Query or modify Enterprise MES (batches, eBR execution, line clearance, material dispensing, IPC, equipment reservation). Actions: list_batches, get_batch, create_batch, transition_batch, execute_ebr, qa_review_ebr, verify_line_clearance, create_dispensing, dispense_material, create_ipc, record_ipc, reserve_equipment, release_equipment.",
               _schema({"action": _S, "payload": _O}, ["action"]), _run_enterprise_mes))
_register(Tool("enterprise.training", "Query or modify Enterprise Training Management. Actions: list_assignments, create_assignment, update_assignment, complete_assignment, compliance_dashboard, overdue.",
               _schema({"action": _S, "payload": _O}, ["action"]), _run_enterprise_training))
_register(Tool("enterprise.supplier", "Query or modify Enterprise Supplier Qualification. Actions: list_qualifications, create_qualification, update_qualification, approve_qualification, record_audit, audit_due, risk_summary, auto_schedule_audits.",
               _schema({"action": _S, "payload": _O}, ["action"]), _run_enterprise_supplier))
_register(Tool("enterprise.analytics", "Query Enterprise cross-module analytics and KPIs. Actions: kpi, trends.",
               _schema({"action": _S, "payload": _O}, ["action"]), _run_enterprise_analytics))
_register(Tool("enterprise.notifications", "Query or modify Enterprise notifications. Actions: list, create, mark_read, auto_check.",
               _schema({"action": _S, "payload": _O}, ["action"]), _run_enterprise_notifications))
_register(Tool("enterprise.dms", "Query or modify Enterprise Document Management. Actions: list_documents, create_document, update_document, transition, new_version.",
               _schema({"action": _S, "payload": _O}, ["action"]), _run_enterprise_dms))
_register(Tool("enterprise.ehs", "Query or modify Enterprise EHS incidents. Actions: list_incidents, create_incident, update_incident, summary.",
               _schema({"action": _S, "payload": _O}, ["action"]), _run_enterprise_ehs))
_register(Tool("enterprise.rim", "Query or modify Enterprise Regulatory Information. Actions: list_submissions, create_submission, update_submission, list_commitments, create_commitment, upcoming.",
               _schema({"action": _S, "payload": _O}, ["action"]), _run_enterprise_rim))

_register(Tool("enterprise.command", "Run any Enterprise bridge command with a payload.",
               _schema({"command": _S, "payload": _O}, ["command"]), _run_enterprise_command))
_register(Tool("chem.chemdfm_query", "Ask ChemDFM a chemistry question (molecule, reaction, property).",
               _schema({"query": _S, "topic": _S}, ["query"]), _run_chem_chemdfm_query))
_register(Tool("chem.chemdfm_train", "Record a validated chemistry fact for iterative ChemDFM learning.",
               _schema({"fact": _S, "topic": _S, "validated_by": _S}, ["fact"]), _run_chem_chemdfm_train))
_register(Tool("chem.chemdfm_journal", "Get ChemDFM training journal summary or learning-gap analysis.",
               _schema({"mode": _S, "limit": _I}, []), _run_chem_chemdfm_journal))




def tool_specs(names: list[str] | None = None) -> list[dict[str, Any]]:
    items = [TOOLS[n] for n in (names or TOOLS.keys()) if n in TOOLS]
    return [t.spec() for t in items]


def capabilities() -> dict[str, Any]:
    """Human/JSON manifest of everything the agent can do (for the UI)."""
    return {"ok": True, "repo_root": str(REPO_ROOT), "allowed_roots": list_allowed_roots(),
            "tools": [{"name": t.name, "description": t.description,
                       "risk_default": t.risk({}) if t.name not in {"fs.write", "fs.edit", "fs.delete", "fs.move"} else "by-path"}
                      for t in TOOLS.values()]}


def run_tool(name: str, args: dict[str, Any], *, allow_gated: bool = False,
             session_id: str | None = None) -> dict[str, Any]:
    """Execute a tool, enforcing the approval gate.

    If a tool is ``gated`` and ``allow_gated`` is False, this returns a payload
    with ``needs_approval=True`` describing the action; the caller turns that
    into a pending approval. With ``allow_gated=True`` (post-approval execution
    path) the tool runs for real.
    """
    from .config import settings
    tool = TOOLS.get(name)
    if not tool:
        return {"ok": False, "error": f"unknown tool: {name}"}
    args = args or {}
    risk = "safe"
    try:
        risk = tool.risk(args)
    except Exception:
        risk = "gated"
    if settings.omnipotent_mode:
        risk = "safe"
    if risk == "gated" and not allow_gated:
        # self.patch is special: we PROPOSE+VALIDATE now (sandbox only, no live
        # change) so the user sees a diff, and apply happens on approval.
        if name == "self.patch":
            proposed = tool.run(args)
            proposed["needs_approval"] = True
            proposed["tool"] = name
            return proposed
        return {"ok": True, "needs_approval": True, "risk": "gated", "tool": name, "args": args,
                "title": f"Run {name}", "summary": _gate_summary(name, args)}
    result = tool.run(args)
    if settings.omnipotent_mode and isinstance(result, dict):
        result.pop("needs_approval", None)
    return result


def _gate_summary(name: str, args: dict[str, Any]) -> str:
    if name == "shell.run":
        return f"Run command: {str(args.get('command'))[:160]}"
    if name in {"fs.write", "fs.edit", "fs.mkdir"}:
        return f"{name} → {args.get('path')}"
    if name == "fs.delete":
        return f"Delete {args.get('path')}"
    if name == "fs.move":
        return f"Move {args.get('src')} → {args.get('dst')}"
    return f"{name} with {json.dumps(args)[:160]}"


def _run_enterprise_export(args: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    if not getattr(settings, "enterprise_pairing_enabled", False):
        return _enterprise_disabled()
    action = str(args.get("action") or "").strip()
    module = str(args.get("module") or "").strip()
    payload = args.get("payload") or {}
    url = getattr(settings, "enterprise_url", os.getenv("SHIMS_ENTERPRISE_URL", "http://127.0.0.1:8020"))
    token = getattr(settings, "bridge_token", os.getenv("SHIMS_BRIDGE_TOKEN", ""))
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            headers = {"X-Bridge-Token": token or ""}
            if action == "modules":
                r = client.get(f"{url}/api/export/modules", headers=headers, params=payload)
                r.raise_for_status()
                return r.json()
            if action in ("csv", "xlsx"):
                if not module:
                    return {"ok": False, "error": "module required for csv/xlsx export"}
                r = client.get(f"{url}/api/export/{module}/{action}", headers=headers, params=payload)
                r.raise_for_status()
                content = r.content
                fname = f"{module}.{action}"
                cd = r.headers.get("content-disposition", "")
                if "filename=" in cd:
                    fname = cd.split("filename=")[-1].strip('"')
                out = pathlib.Path("storage/downloads") / fname
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(content)
                return {"ok": True, "module": module, "format": action, "path": str(out), "bytes": len(content)}
            return {"ok": False, "error": f"Unknown export action: {action}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:260]}


_register(Tool("enterprise.export", "Export Enterprise module data to CSV or Excel. Actions: modules, csv, xlsx. Module keys: qms, lims (alias), lims_samples, lims_tests, equipment, equipment_work_orders, mes_batches, mes_ebr, training, supplier, procurement, inventory, ehs_incidents, rim_submissions, rim_commitments, dms_documents.",
               _schema({"action": _S, "module": _S, "payload": _O}, ["action"]), _run_enterprise_export))

# ── MCP client tools (Phase 4.1 Soul, Brain & Swarm upgrade) ──
def _run_mcp_list_servers(args: dict[str, Any]) -> dict[str, Any]:
    from .mcp_registry import list_servers, load_servers_config
    config = load_servers_config()
    if config.get("error"):
        return {"ok": False, "error": config["error"]}
    return {"ok": True, "servers": list_servers(config)}


def _run_mcp_call_tool(args: dict[str, Any]) -> dict[str, Any]:
    from .mcp_registry import call_tool
    server_name = str(args.get("server") or "").strip()
    tool_name = str(args.get("tool_name") or "").strip()
    arguments = args.get("arguments") or args.get("args") or {}
    if not server_name:
        return {"ok": False, "error": "server required"}
    if not tool_name:
        return {"ok": False, "error": "tool_name required"}
    return call_tool(server_name, tool_name, arguments)


_register(Tool("mcp.list_servers", "List configured MCP (Model Context Protocol) servers and their endpoints.",
               _schema({}, []), _run_mcp_list_servers))
_register(Tool("mcp.call_tool", "Call a tool on a configured MCP server via JSON-RPC over HTTP.",
               _schema({"server": _S, "tool_name": _S, "arguments": _O}, ["server", "tool_name"]), _run_mcp_call_tool))


# ── Inter-instance bridge tools ──────────────────────────────────────────────
def _run_peer_status(args: dict[str, Any]) -> dict[str, Any]:
    from .inter_instance_bridge import list_peers
    return {"ok": True, "instance_id": os.getenv("SHIMS_INSTANCE_ID", "primary"), "peers": list_peers()}


def _run_peer_call(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    from .inter_instance_bridge import PeerClient, get_peer
    peer_id = str(args.get("peer_id") or "local").strip()
    tool = str(args.get("tool") or "").strip()
    tool_args = args.get("args") or {}
    if not tool:
        return {"ok": False, "error": "tool required"}
    peer = get_peer(peer_id)
    if not peer:
        return {"ok": False, "error": f"peer {peer_id} not found"}
    return asyncio.run(PeerClient(peer).call_tool(tool, tool_args))


def _run_peer_sync_corpus(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    from .inter_instance_bridge import PeerClient, get_peer
    peer_id = str(args.get("peer_id") or "primary").strip()
    source_type = args.get("source_type")
    limit = int(args.get("limit", 1000))
    peer = get_peer(peer_id)
    if not peer:
        return {"ok": False, "error": f"peer {peer_id} not found"}
    return asyncio.run(PeerClient(peer).sync_corpus(source_type=source_type, limit=limit))


def _run_local_llm_chat(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    from . import ai
    from .local_factory_config import resolve_role_model
    messages = args.get("messages") or []
    local_role = str(args.get("role") or "").strip().lower()
    if not local_role:
        hay = " ".join(str(m.get("content", "")) for m in messages if isinstance(m, dict)).lower()
        if any(token in hay for token in ("chemdfm", "reaction", "synthesis", "solvent", "impurity", "smiles", "stoichiometry")):
            local_role = "chemistry"
        elif any(token in hay for token in ("manufacturing", "production", "batch", "bmr", "gmp", "qms", "lims", "mes", "scale-up", "material balance")):
            local_role = "heavy"
        elif any(token in hay for token in ("voice", "latency", "realtime", "real-time", "fast")):
            local_role = "fast"
        else:
            local_role = "smart"
    model = args.get("model") or resolve_role_model(local_role)
    temperature = float(args.get("temperature", 0.3))
    timeout = max(5.0, float(args.get("timeout", os.getenv("SHIMS_LOCAL_LLM_TOOL_TIMEOUT_SECONDS", "45"))))
    system = ""
    prompt_parts: list[str] = []
    for m in messages:
        msg_role = m.get("role", "user")
        content = m.get("content", "")
        if msg_role == "system":
            system = content
        elif msg_role == "user":
            prompt_parts.append(content)
        elif msg_role == "assistant":
            prompt_parts.append(f"Assistant: {content}")
    prompt = "\n\n".join(prompt_parts)
    async def _run() -> Any:
        return await asyncio.wait_for(
            ai.ask_ai(
                prompt,
                system=system or "You are SHIMS, a helpful local AI.",
                provider="ollama",
                model=model,
            ),
            timeout=timeout,
        )

    started = time.perf_counter()
    try:
        result = asyncio.run(_run())
    except asyncio.TimeoutError:
        return {"ok": False, "model": model, "role": local_role, "error": f"local LLM timed out after {timeout:g}s"}
    return {
        "ok": result.ok,
        "model": model,
        "role": local_role,
        "content": result.text,
        "provider": result.provider,
        "route": result.route,
        "error": result.error,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }


_register(Tool("peer.status", "List configured SHIMS peer instances (primary / local factory).",
               _schema({}, []), _run_peer_status))
_register(Tool("peer.call", "Call a whitelisted tool on a peer SHIMS instance.",
               _schema({"peer_id": _S, "tool": _S, "args": _O}, ["peer_id", "tool"]), _run_peer_call))
_register(Tool("peer.sync_corpus", "Sync corpus chunks from a peer instance into the local brain.",
               _schema({"peer_id": _S, "source_type": _S, "limit": _I}, ["peer_id"]), _run_peer_sync_corpus))
_register(Tool("local_llm.chat", "Chat with a local Ollama model (3B default, 7B heavy, chemdfm chemistry).",
               _schema({"messages": _A, "model": _S, "role": _S, "temperature": _N, "timeout": _N}, ["messages"]), _run_local_llm_chat))


# ── Local Factory tools ──────────────────────────────────────────────────────
def _run_factory_corpus_stats(args: dict[str, Any]) -> dict[str, Any]:
    from .local_factory_corpus import corpus_stats
    return corpus_stats()


def _run_factory_build_corpus(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    from .local_factory_corpus import build_corpus_async
    return asyncio.run(build_corpus_async(
        force=bool(args.get("force")),
        web_queries=args.get("web_queries"),
        max_web_pages=int(args.get("max_web_pages", 6)),
        synthesize_qa=bool(args.get("synthesize_qa", True)),
        max_qa_chunks=int(args.get("max_qa_chunks", 200)),
    ))


def _run_factory_train_model(args: dict[str, Any]) -> dict[str, Any]:
    import subprocess
    mode = str(args.get("mode") or os.getenv("SHIMS_FACTORY_TRAIN_MODE", "ollama")).lower()
    script = ROOT_DIR / "scripts" / "train_local_factory_model.py"
    python = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    cmd = [str(python), str(script), "--mode", mode]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=86400)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-600:],
            "stderr": result.stderr[-600:],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def _run_factory_run_evolution(args: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    from .factory_evolution_loop import run_evolution_cycle, start_background_evolution
    train_mode = str(args.get("train_mode") or os.getenv("SHIMS_FACTORY_TRAIN_MODE", "ollama"))
    sync_peers = args.get("sync_peers")
    if isinstance(sync_peers, str):
        sync_peers = [sync_peers]
    if args.get("background", True):
        return start_background_evolution(train_mode=train_mode, sync_peers=sync_peers)
    return asyncio.run(run_evolution_cycle(train_mode=train_mode, sync_peers=sync_peers))


_register(Tool("factory.corpus_stats", "Show Local Factory corpus statistics.",
               _schema({}, []), _run_factory_corpus_stats))
_register(Tool("factory.build_corpus", "Build/refine the Local Factory corpus from BMR, chemistry, enterprise, web.",
               _schema({"force": _B, "max_web_pages": _I, "synthesize_qa": _B, "max_qa_chunks": _I}, []), _run_factory_build_corpus))
_register(Tool("factory.train_model", "Train the Local Factory model (ollama persona or peft LoRA).",
               _schema({"mode": _S}, []), _run_factory_train_model))
_register(Tool("factory.run_evolution", "Run one Local Factory evolution cycle: corpus -> train -> evaluate -> propose.",
               _schema({"train_mode": _S, "sync_peers": _A, "background": _B}, []), _run_factory_run_evolution))
