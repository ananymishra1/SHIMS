"""Durable chat-history store for SHIMS.

The brain keeps the working conversation history in an in-memory dict
(``backend.app.main._sessions``). That dict is lost on every restart, which is
why past chats vanished and could not be reopened or continued. This module
persists each session to a small JSON file on disk so history survives restarts.

Design goals:
- **Faithful continuation** — we store the exact ``[{role, content}, ...]``
  message arrays the brain uses, not the lossy chunked RAG archive, so a
  reopened chat continues with the real prompt state.
- **Cheap + safe** — one file per session under ``data/state/chat_sessions/``,
  written atomically, guarded by a lock. Delete is just removing a file.
- **Fail soft** — any disk error is swallowed; the brain keeps working from RAM.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_DIR = Path(os.getenv("SHIMS_CHAT_STORE_DIR", str(_ROOT / "data" / "state" / "chat_sessions")))
_LOCK = threading.Lock()
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _ensure_dir() -> None:
    _DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(session_id: str) -> str:
    """Filesystem-safe filename for a session id (ids are usually uuids)."""
    sid = (session_id or "").strip()
    slug = _SAFE.sub("_", sid)[:120]
    return slug or "unnamed"


def _path(session_id: str) -> Path:
    return _DIR / f"{_safe_name(session_id)}.json"


def _title(messages: list[dict[str, Any]]) -> str:
    """A short human title from the first user message."""
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, list):  # multimodal — pull the first text part
            content = next((p.get("text") for p in content
                            if isinstance(p, dict) and p.get("text")), "")
        text = str(content or "").strip()
        # Per-turn volatile context is PREPENDED as
        #   "[Live context — may change per turn] ... \n\n---\n\n<real message>".
        # Strip it so the title is the user's actual words, not the time/RAG block.
        if "[Live context — may change per turn]" in text:
            parts = text.split("\n\n---\n\n")
            if len(parts) > 1:
                text = parts[-1].strip()
        line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if line:
            return line[:80]
    return "New chat"


def save_session(session_id: str, messages: list[dict[str, Any]]) -> None:
    """Persist one session to disk atomically. Never raises."""
    if not session_id or not messages:
        return
    try:
        _ensure_dir()
        payload = {
            "session_id": session_id,
            "title": _title(messages),
            "message_count": len(messages),
            "updated_at": time.time(),
            "messages": messages,
        }
        dest = _path(session_id)
        tmp = dest.with_suffix(".json.tmp")
        with _LOCK:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, dest)
    except Exception:
        pass


def load_all() -> dict[str, list[dict[str, Any]]]:
    """Load every persisted session, for hydrating the in-memory dict on boot."""
    out: dict[str, list[dict[str, Any]]] = {}
    try:
        if not _DIR.exists():
            return out
        for f in _DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sid = data.get("session_id")
                msgs = data.get("messages")
                if sid and isinstance(msgs, list):
                    out[sid] = msgs
            except Exception:
                continue
    except Exception:
        pass
    return out


def list_sessions() -> list[dict[str, Any]]:
    """Metadata for every persisted session, newest first."""
    rows: list[dict[str, Any]] = []
    try:
        if not _DIR.exists():
            return rows
        for f in _DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                rows.append({
                    "id": data.get("session_id"),
                    "title": data.get("title") or "New chat",
                    "message_count": int(data.get("message_count") or 0),
                    "updated_at": float(data.get("updated_at") or 0.0),
                })
            except Exception:
                continue
    except Exception:
        pass
    rows.sort(key=lambda r: r.get("updated_at") or 0.0, reverse=True)
    return rows


def get_session(session_id: str) -> dict[str, Any] | None:
    """Full stored payload for a session, or None."""
    try:
        p = _path(session_id)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_session(session_id: str) -> bool:
    """Remove a session's file from disk. Returns True if a file was deleted."""
    try:
        p = _path(session_id)
        if p.exists():
            with _LOCK:
                p.unlink(missing_ok=True)
            return True
    except Exception:
        pass
    return False
