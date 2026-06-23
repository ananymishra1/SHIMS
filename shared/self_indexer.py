from __future__ import annotations

import ast
import re
import time
from pathlib import Path
from typing import Any

from .config import ROOT_DIR
from .omni_brain import ingest_knowledge, remember
from .self_evolver import ALLOWED_ROOTS, BLOCKED_PARTS, IMMUTABLE_RELATIVE_PATHS

SOURCE_NAMESPACE = "shims_source"
SUPPORTED_SUFFIXES = {".py", ".js", ".html", ".htm", ".css"}
_MIN_REINDEX_SECONDS = 300


def _relative_posix(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT_DIR.resolve())).replace("\\", "/")


def _is_allowed(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return False
    if any(part in BLOCKED_PARTS for part in path.parts):
        return False
    rel = _relative_posix(path)
    if rel in IMMUTABLE_RELATIVE_PATHS:
        return False
    if not rel or rel.split("/")[0] not in ALLOWED_ROOTS:
        return False
    return True


def _chunk_python(text: str, rel: str) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [{"title": f"{rel}:module", "body": text.strip()[:4000]}]

    lines = text.splitlines()
    first_def_line = len(lines)

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno - 1
            end = node.end_lineno or start + 1
            body = "\n".join(lines[start:end])
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            chunks.append({"title": f"{rel}:{kind}:{node.name}", "body": body})
            first_def_line = min(first_def_line, start)

    header = "\n".join(lines[:first_def_line]).strip()
    if header:
        chunks.insert(0, {"title": f"{rel}:module:header", "body": header})

    return chunks or [{"title": f"{rel}:module", "body": text.strip()[:4000]}]


def _find_balanced_end(lines: list[str], start: int) -> int:
    """Simple brace balance scanner for JS/CSS blocks."""
    brace_count = 0
    in_string: str | None = None
    escape = False
    found_open = False
    for i in range(start, len(lines)):
        line = lines[i]
        for ch in line:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if in_string:
                if ch == in_string:
                    in_string = None
                continue
            if ch in "'\"`":
                in_string = ch
                continue
            if ch == "{" or ch == "(" or ch == "[":
                if ch == "{":
                    brace_count += 1
                    found_open = True
            elif ch == "}" or ch == ")" or ch == "]":
                if ch == "}":
                    brace_count -= 1
                if found_open and brace_count == 0:
                    return i
    return len(lines) - 1


def _chunk_js(text: str, rel: str) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    lines = text.splitlines()
    # Patterns for common JS/TS top-level declarations.
    patterns = [
        (r"^(?:export\s+|default\s+)?(?:async\s+)?function\s+(\w+)", "function"),
        (r"^(?:export\s+)?class\s+(\w+)", "class"),
        (r"^(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function\s*\(|\(.*\)\s*=>)", "function"),
        (r"^(?:const|let|var)\s+(\w+)\s*=\s*\{", "object"),
    ]
    seen_starts: set[int] = set()
    for i, line in enumerate(lines):
        for pattern, kind in patterns:
            m = re.match(pattern, line.strip())
            if not m or i in seen_starts:
                continue
            name = m.group(1)
            end = _find_balanced_end(lines, i)
            body = "\n".join(lines[i:end + 1])
            chunks.append({"title": f"{rel}:{kind}:{name}", "body": body})
            for k in range(i, end + 1):
                seen_starts.add(k)
            break
    return chunks or [{"title": f"{rel}:module", "body": text.strip()[:4000]}]


def _chunk_css(text: str, rel: str) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    # Split on top-level `}` while preserving @media/@keyframes blocks.
    depth = 0
    current: list[str] = []
    selector = "rules"
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not depth and stripped and stripped[-1] == "{" and stripped[0] != "@":
            selector = stripped[:-1].strip().split("{")[0].strip() or "rules"
            selector = selector.replace("\n", " ").strip()
        current.append(line)
        depth += stripped.count("{")
        depth -= stripped.count("}")
        if depth == 0 and current:
            body = "\n".join(current).strip()
            if body:
                chunks.append({"title": f"{rel}:css:{selector[:80]}", "body": body})
            current = []
            selector = "rules"
    if current:
        body = "\n".join(current).strip()
        if body:
            chunks.append({"title": f"{rel}:css:{selector[:80]}", "body": body})
    return chunks or [{"title": f"{rel}:module", "body": text.strip()[:4000]}]


def _chunk_html(text: str, rel: str) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    # Split by major structural tags.
    parts = re.split(r"(?i)(</?(?:section|div|main|article|header|footer|script|style|nav|aside)[^>]*>)", text)
    buffer = ""
    tag_name = "body"
    for part in parts:
        if re.match(r"(?i)</?(section|div|main|article|header|footer|script|style|nav|aside)", part):
            if buffer.strip():
                chunks.append({"title": f"{rel}:html:{tag_name}", "body": buffer.strip()[:4000]})
                buffer = ""
            tag_name = re.sub(r"[^a-zA-Z0-9_-]", "_", part.strip("</>").split()[0].lower()) or "section"
            buffer = part
        else:
            buffer += part
    if buffer.strip():
        chunks.append({"title": f"{rel}:html:{tag_name}", "body": buffer.strip()[:4000]})
    return chunks or [{"title": f"{rel}:module", "body": text.strip()[:4000]}]


def _chunk_file(path: Path, rel: str) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _chunk_python(text, rel)
    if suffix == ".js":
        return _chunk_js(text, rel)
    if suffix in {".html", ".htm"}:
        return _chunk_html(text, rel)
    if suffix == ".css":
        return _chunk_css(text, rel)
    return [{"title": f"{rel}:module", "body": text.strip()[:4000]}]


def _last_indexed_at() -> float:
    from .omni_brain import list_memories
    try:
        for mem in list_memories(namespace="system", query=SOURCE_NAMESPACE, limit=5):
            if mem.get("key") == f"{SOURCE_NAMESPACE}_indexed_at":
                return float(mem.get("value", 0))
    except Exception:
        pass
    return 0.0


def index_shims_source(force: bool = False) -> dict[str, Any]:
    """Walk allowed SHIMS source roots and ingest semantic chunks into the omni-brain.

    Parameters
    ----------
    force: bool
        If False, skip re-indexing if the last run was within ``_MIN_REINDEX_SECONDS``.

    Returns
    -------
    dict with ok, files, chunks, skipped, blocked, immutable, and elapsed_s.
    """
    start = time.time()
    if not force:
        last = _last_indexed_at()
        if last and (time.time() - last) < _MIN_REINDEX_SECONDS:
            return {"ok": True, "skipped": True, "reason": "recently_indexed", "seconds_since": round(time.time() - last, 1)}

    files_indexed = 0
    chunks_indexed = 0
    files_blocked = 0
    files_immutable = 0
    files_error = 0

    for root_name in ALLOWED_ROOTS:
        root = ROOT_DIR / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = _relative_posix(path)
            if any(part in BLOCKED_PARTS for part in path.parts):
                files_blocked += 1
                continue
            if rel in IMMUTABLE_RELATIVE_PATHS:
                files_immutable += 1
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            try:
                chunks = _chunk_file(path, rel)
                for chunk in chunks:
                    ingest_knowledge(
                        title=chunk["title"],
                        text=chunk["body"],
                        source_type=SOURCE_NAMESPACE,
                        source_uri=rel,
                        tags=[SOURCE_NAMESPACE, path.suffix.lstrip(".")],
                        importance=1.0,
                    )
                    chunks_indexed += 1
                files_indexed += 1
            except Exception:
                files_error += 1

    remember(
        "system",
        f"{SOURCE_NAMESPACE}_indexed_at",
        str(time.time()),
        tags=[SOURCE_NAMESPACE, "indexer"],
        pinned=False,
        weight=1.0,
        source="self_indexer",
    )

    elapsed = round(time.time() - start, 3)
    return {
        "ok": True,
        "skipped": False,
        "namespace": SOURCE_NAMESPACE,
        "files_indexed": files_indexed,
        "chunks_indexed": chunks_indexed,
        "files_blocked": files_blocked,
        "files_immutable": files_immutable,
        "files_error": files_error,
        "elapsed_s": elapsed,
    }
