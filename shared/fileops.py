"""Desktop cowork — safe, confined file operations for SHIMS.

SHIMS helps organize files, but every operation is confined to a single
user-chosen **workspace root** and follows the house rule: *AI recommends, human
confirms*. Organization is a two-step flow — `propose_organization()` returns a
dry-run move plan, and `apply_moves()` only runs a plan the user confirmed,
writing an undo manifest so every change is reversible.

Nothing here can touch paths outside the workspace root (no ``..`` escape, no
absolute paths), and the project's own storage/venv are never the workspace.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import STORAGE_DIR
from .security import new_id

_STATE_FILE = STORAGE_DIR / "state" / "workspace.json"
_UNDO_DIR = STORAGE_DIR / "fileops_undo"
_UNDO_DIR.mkdir(parents=True, exist_ok=True)
_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

# Extension → category, used by the heuristic organizer.
_CATEGORIES: dict[str, set[str]] = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".svg", ".tiff"},
    "Documents": {".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pages"},
    "PDFs": {".pdf"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".tsv", ".ods"},
    "Presentations": {".ppt", ".pptx", ".odp", ".key"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"},
    "Video": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "Code": {".py", ".js", ".ts", ".java", ".kt", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".json", ".yaml", ".yml", ".html", ".css"},
    "Data": {".db", ".sqlite", ".sqlite3", ".parquet", ".jsonl"},
}


def _category_for(suffix: str) -> str:
    s = suffix.lower()
    for cat, exts in _CATEGORIES.items():
        if s in exts:
            return cat
    return "Other"


def default_workspace() -> Path:
    return Path.home() / "SHIMS-Workspace"


def get_workspace() -> Path:
    """Resolve the active workspace root (state file → env → default), creating it."""
    import os
    root = None
    try:
        if _STATE_FILE.exists():
            root = json.loads(_STATE_FILE.read_text(encoding="utf-8")).get("workspace")
    except Exception:
        root = None
    root = root or os.getenv("SHIMS_WORKSPACE_DIR") or str(default_workspace())
    p = Path(root).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def set_workspace(path: str) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps({"workspace": str(p)}), encoding="utf-8")
    return {"ok": True, "workspace": str(p)}


def _safe(relpath: str | None) -> Path:
    """Resolve relpath inside the workspace; raise on escape attempts."""
    root = get_workspace()
    target = (root / (relpath or "")).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Path is outside the SHIMS workspace.")
    return target


def _rel(path: Path) -> str:
    return str(path.relative_to(get_workspace())).replace("\\", "/")


def tree(subpath: str = "", max_entries: int = 500) -> dict[str, Any]:
    base = _safe(subpath)
    if not base.exists():
        return {"ok": False, "error": "not found"}
    entries: list[dict[str, Any]] = []
    count = 0
    for p in sorted(base.rglob("*"), key=lambda x: str(x).lower()):
        if any(part in {".git", "__pycache__", "node_modules"} for part in p.parts):
            continue
        count += 1
        if count > max_entries:
            break
        try:
            entries.append({
                "path": _rel(p), "is_dir": p.is_dir(),
                "size": p.stat().st_size if p.is_file() else 0,
                "category": "" if p.is_dir() else _category_for(p.suffix),
            })
        except Exception:
            continue
    return {"ok": True, "workspace": str(get_workspace()), "count": len(entries),
            "truncated": count > max_entries, "entries": entries}


def read_text(relpath: str, max_bytes: int = 200_000) -> dict[str, Any]:
    target = _safe(relpath)
    if not target.is_file():
        return {"ok": False, "error": "not a file"}
    data = target.read_bytes()[:max_bytes]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "binary file", "size": target.stat().st_size}
    return {"ok": True, "path": _rel(target), "text": text,
            "truncated": target.stat().st_size > max_bytes}


def search(query: str, *, in_content: bool = True, max_results: int = 100) -> dict[str, Any]:
    root = get_workspace()
    q = (query or "").lower()
    if not q:
        return {"ok": False, "error": "empty query"}
    name_hits, content_hits = [], []
    for p in root.rglob("*"):
        if not p.is_file() or any(part in {".git", "__pycache__"} for part in p.parts):
            continue
        if q in p.name.lower():
            name_hits.append(_rel(p))
        elif in_content and p.suffix.lower() in {".txt", ".md", ".py", ".json", ".csv", ".html", ".js", ".yaml", ".yml"}:
            try:
                if p.stat().st_size < 2_000_000 and q in p.read_text(encoding="utf-8", errors="ignore").lower():
                    content_hits.append(_rel(p))
            except Exception:
                continue
        if len(name_hits) + len(content_hits) >= max_results:
            break
    return {"ok": True, "name_matches": name_hits, "content_matches": content_hits}


def find_duplicates() -> dict[str, Any]:
    root = get_workspace()
    by_hash: dict[str, list[str]] = defaultdict(list)
    for p in root.rglob("*"):
        if not p.is_file() or any(part in {".git", "__pycache__"} for part in p.parts):
            continue
        try:
            if p.stat().st_size == 0:
                continue
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            by_hash[h].append(_rel(p))
        except Exception:
            continue
    dupes = {h: paths for h, paths in by_hash.items() if len(paths) > 1}
    return {"ok": True, "duplicate_groups": list(dupes.values())}


def summarize_folder(subpath: str = "") -> dict[str, Any]:
    base = _safe(subpath)
    cats: dict[str, int] = defaultdict(int)
    total_size = 0
    file_count = 0
    for p in base.rglob("*"):
        if p.is_file() and not any(part in {".git", "__pycache__"} for part in p.parts):
            file_count += 1
            total_size += p.stat().st_size
            cats[_category_for(p.suffix)] += 1
    return {"ok": True, "path": _rel(base) if base != get_workspace() else "",
            "files": file_count, "total_mb": round(total_size / 1024 / 1024, 2),
            "by_category": dict(sorted(cats.items(), key=lambda kv: kv[1], reverse=True))}


def summarize_workspace() -> dict[str, Any]:
    """Used by the background file_scan task."""
    s = summarize_folder("")
    return {"ok": True, "summary": s}


def propose_organization(subpath: str = "") -> dict[str, Any]:
    """Dry-run plan: move loose files in `subpath` into category subfolders.

    Only proposes moving files that are currently directly inside `subpath`
    (not already in a category folder). Returns a plan; nothing is moved.
    """
    base = _safe(subpath)
    plan: list[dict[str, str]] = []
    for p in base.iterdir():
        if not p.is_file():
            continue
        cat = _category_for(p.suffix)
        dest_dir = base / cat
        dest = dest_dir / p.name
        if dest.resolve() == p.resolve():
            continue
        plan.append({"from": _rel(p), "to": _rel(dest_dir) + "/" + p.name, "category": cat})
    return {"ok": True, "workspace": str(get_workspace()), "moves": plan, "count": len(plan),
            "note": "Dry run. Review and call apply_moves(plan) to execute; it is reversible."}


def apply_moves(moves: list[dict[str, str]]) -> dict[str, Any]:
    """Execute a confirmed move plan, recording an undo manifest."""
    if not moves:
        return {"ok": False, "error": "empty plan"}
    undo_id = new_id("undo")
    manifest: list[dict[str, str]] = []
    applied = 0
    for mv in moves:
        try:
            src = _safe(mv["from"])
            dst = _safe(mv["to"])
            if not src.is_file():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():  # avoid clobber — suffix a counter
                stem, suf = dst.stem, dst.suffix
                n = 1
                while dst.exists():
                    dst = dst.with_name(f"{stem}_{n}{suf}")
                    n += 1
            shutil.move(str(src), str(dst))
            manifest.append({"from": _rel(dst), "to": _rel(src)})  # reverse for undo
            applied += 1
        except Exception:
            continue
    (_UNDO_DIR / f"{undo_id}.json").write_text(
        json.dumps({"created_at": time.time(), "moves": manifest}, indent=2), encoding="utf-8")
    return {"ok": True, "applied": applied, "undo_id": undo_id,
            "note": f"Moved {applied} files. Undo with undo_moves('{undo_id}')."}


def undo_moves(undo_id: str) -> dict[str, Any]:
    f = _UNDO_DIR / f"{undo_id}.json"
    if not f.exists():
        return {"ok": False, "error": "undo manifest not found"}
    data = json.loads(f.read_text(encoding="utf-8"))
    restored = 0
    for mv in data.get("moves", []):
        try:
            src = _safe(mv["from"])
            dst = _safe(mv["to"])
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                restored += 1
        except Exception:
            continue
    return {"ok": True, "restored": restored}
