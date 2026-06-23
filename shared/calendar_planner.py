from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ROOT_DIR


CALENDAR_DIR = ROOT_DIR / "data" / "media" / "documents"


def _clean(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _safe_name(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", (title or "calendar_event")[:80]).strip("_") or "calendar_event"
    return f"{slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.ics"


def _parse_dt(value: str | None) -> datetime:
    value = (value or "").strip()
    if not value:
        return datetime.now(timezone.utc) + timedelta(hours=1)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        parsed = datetime.now(timezone.utc) + timedelta(hours=1)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ics_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ics_escape(text: str) -> str:
    text = _clean(text, 2400)
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def build_ics(
    *,
    title: str,
    start: str | None = None,
    end: str | None = None,
    duration_minutes: int = 30,
    description: str = "",
    location: str = "",
) -> dict[str, Any]:
    title = _clean(title or "SHIMS task", 180)
    starts = _parse_dt(start)
    if end:
        ends = _parse_dt(end)
    else:
        ends = starts + timedelta(minutes=max(5, min(int(duration_minutes or 30), 24 * 60)))
    if ends <= starts:
        ends = starts + timedelta(minutes=30)
    uid = f"shims-{uuid.uuid4().hex}@local"
    now = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SHIMS//Reliability Core v16//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_ics_dt(now)}",
        f"DTSTART:{_ics_dt(starts)}",
        f"DTEND:{_ics_dt(ends)}",
        f"SUMMARY:{_ics_escape(title)}",
        f"DESCRIPTION:{_ics_escape(description or 'Created by SHIMS as a local ICS draft. No Google Calendar sync was performed.')}",
    ]
    if location:
        lines.append(f"LOCATION:{_ics_escape(location)}")
    lines += ["END:VEVENT", "END:VCALENDAR", ""]
    ics = "\r\n".join(lines)
    return {
        "ok": True,
        "uid": uid,
        "title": title,
        "start": starts.isoformat(),
        "end": ends.isoformat(),
        "ics": ics,
        "sync": "none",
        "policy": "Local ICS draft only; import manually or approve a future OAuth calendar sync.",
    }


def save_ics_event(**kwargs: Any) -> dict[str, Any]:
    event = build_ics(**kwargs)
    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)
    filename = _safe_name(event["title"])
    path = CALENDAR_DIR / filename
    path.write_text(event["ics"], encoding="utf-8", newline="")
    event.update({"filename": filename, "path": str(path), "url": f"/media/files/documents/{filename}", "file_url": f"/media/files/documents/{filename}"})
    return event
