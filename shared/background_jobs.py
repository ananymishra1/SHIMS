"""Background job scheduler and inbox processor for SHIMS.

Ensures default recurring jobs exist on startup:
- Improvement loop (eval-driven proposals)
- Self-indexer (source-tree knowledge)
- Media inbox ingestion (images, audio, video, PDFs, documents)

New files dropped into data/inbox are picked up by the media-ingest runner and
stored in the omni-brain. Processed files are moved to data/inbox/processed.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .action_ledger import record_action
from .config import STORAGE_DIR
from .desktop_scheduler import cancel_task, list_tasks, schedule_task

INBOX_DIR = STORAGE_DIR / "inbox"
PROCESSED_DIR = STORAGE_DIR / "inbox" / "processed"


def ensure_default_jobs() -> dict[str, Any]:
    """Idempotently schedule the factory background jobs."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    existing = {t.task_id: t for t in list_tasks(enabled_only=True, limit=200)}
    created: list[str] = []

    def ensure(title: str, task_id: str, schedule_type: str, when: str, action_type: str, payload: dict[str, Any]) -> None:
        if task_id in existing:
            return
        res = schedule_task(title, schedule_type, when, action_type, payload, task_id=task_id)
        if res.get("ok"):
            created.append(task_id)

    # Run improvement loop every 6 hours.
    ensure(
        "Improvement loop — evals + proposals",
        "shims-bg-improvement-loop",
        "interval",
        str(6 * 3600),
        "tool",
        {"tool": "improvement.run_cycle", "args": {}},
    )

    # Self-index source tree once per day.
    ensure(
        "Self-indexer — source tree knowledge",
        "shims-bg-self-index",
        "interval",
        str(24 * 3600),
        "tool",
        {"tool": "brain.self_index", "args": {"force": False}},
    )

    # Media inbox scan every 30 minutes.
    ensure(
        "Media inbox ingestion",
        "shims-bg-media-inbox",
        "interval",
        str(30 * 60),
        "inbox_ingest",
        {},
    )

    record_action(
        "background_jobs.ensure",
        f"Ensured default background jobs; created {len(created)}",
        result={"created": created},
        requested_level="L1",
    )
    return {"ok": True, "created": created, "existing_count": len(existing)}


def list_background_jobs() -> list[dict[str, Any]]:
    return [t.to_dict() for t in list_tasks(enabled_only=False, limit=200)]


def _tool_payload(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"tool": tool, "args": args}


def run_inbox_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    """Scan data/inbox and ingest media/docs into omni-brain."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    files = [p for p in INBOX_DIR.iterdir() if p.is_file()]
    results: list[dict[str, Any]] = []
    for path in files:
        ext = path.suffix.lower()
        kind = None
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
            kind = "image"
        elif ext in {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".wma"}:
            kind = "audio"
        elif ext in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
            kind = "video"
        elif ext == ".pdf":
            kind = "pdf"
        elif ext in {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt"}:
            kind = "document"
        else:
            kind = "unknown"

        res: dict[str, Any] = {"file": str(path), "kind": kind}
        try:
            from . import agent_tools
            if kind in {"image", "audio", "video"}:
                res["ingest"] = agent_tools.run_tool(
                    "memory.ingest_media",
                    {"path": str(path), "kind": kind, "title": path.stem, "tags": ["inbox", kind]},
                    allow_gated=False,
                )
            elif kind == "pdf":
                # Ingest PDF as media if supported, else describe first page.
                res["ingest"] = agent_tools.run_tool(
                    "memory.ingest_media",
                    {"path": str(path), "kind": "document", "title": path.stem, "tags": ["inbox", "pdf"]},
                    allow_gated=False,
                )
            elif kind == "document":
                # Best-effort text extraction if libraries are available.
                res["text"] = _extract_document_text(path)
            else:
                res["note"] = "unknown extension; left in inbox for manual review"
                continue
            # Move to processed after successful attempt.
            dest = PROCESSED_DIR / f"{int(time.time())}_{path.name}"
            shutil.move(str(path), str(dest))
            res["moved_to"] = str(dest)
        except Exception as exc:
            res["error"] = str(exc)[:200]
        results.append(res)
    return {"ok": True, "processed": len(results), "results": results}


def _extract_document_text(path: Path) -> dict[str, Any]:
    ext = path.suffix.lower()
    text = ""
    try:
        if ext == ".docx":
            import docx  # type: ignore
            doc = docx.Document(str(path))
            text = "\n".join(p.text for p in doc.paragraphs)
        elif ext in {".xlsx", ".xls"}:
            import openpyxl  # type: ignore
            wb = openpyxl.load_workbook(str(path), data_only=True)
            parts = []
            for sheet in wb.worksheets:
                rows = []
                for row in sheet.iter_rows(values_only=True):
                    rows.append(" | ".join(str(c) if c is not None else "" for c in row))
                parts.append(f"Sheet: {sheet.title}\n" + "\n".join(rows))
            text = "\n\n".join(parts)
        else:
            text = "[Text extraction not implemented for this format]"
    except Exception as exc:
        text = f"[Extraction error: {exc}]"
    return {"text": text[:4000], "format": ext}


def reset_background_jobs() -> dict[str, Any]:
    """Cancel factory jobs and re-create them. Useful after config changes."""
    for task_id in ["shims-bg-improvement-loop", "shims-bg-self-index", "shims-bg-media-inbox"]:
        try:
            cancel_task(task_id)
        except Exception:
            pass
    return ensure_default_jobs()
