"""Scheduled comms digest — SHIMS-owned taskboard feed.

Periodically scans recent Gmail + WhatsApp (via the shared mailbox and
channels stores), classifies each item into the hub's taskboard buckets
(Urgent / Needs reply / Waiting / FYI), and writes
``data/state/taskboard.json`` in the exact shape the Desktop Hub renders.

One scoped native LLM turn does the classification when the engine is
available; a keyword fallback keeps the feed alive when it is not (engine
busy, loading, or down) — a stale heuristic board beats no board.

Schedule via ``shared.desktop_scheduler`` (``comms_digest`` action);
default interval ``SHIMS_DIGEST_INTERVAL_MIN`` minutes (120).
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ROOT_DIR

TASKBOARD_PATH = ROOT_DIR / "data" / "state" / "taskboard.json"
PINS_PATH = ROOT_DIR / "data" / "state" / "taskboard_pins.json"


def load_pins() -> list[dict[str, Any]]:
    """Manually pinned taskboard items — survive digest regeneration."""
    try:
        if PINS_PATH.is_file():
            data = json.loads(PINS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def add_pin(title: str, *, bucket: str = "Needs reply", area: str = "Manual",
            priority: str = "High", detail: str = "", source: str = "manual",
            url: str = "") -> dict[str, Any]:
    """Pin an open task to the taskboard (deduped by title)."""
    pins = [p for p in load_pins() if p.get("title") != title]
    pins.append({"bucket": bucket if bucket in _BUCKETS else "Needs reply",
                 "area": area, "source": source, "priority": priority, "title": title,
                 "from": source, "snippet": detail, "received_at": _now_iso(),
                 "url": url, "nextAction": "Open task.",
                 "sourceDetail": detail[:120],
                 "pinned": True})
    PINS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PINS_PATH.write_text(json.dumps(pins, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "pins": len(pins)}

_BUCKETS = ("Urgent", "Needs reply", "Waiting", "FYI")

_URGENT_RE = re.compile(
    r"\b(urgent|asap|immediately|deadline|overdue|final notice|compliance|"
    r"payment failed|action required|expires?|expiry|last chance)\b", re.I)
_REPLY_RE = re.compile(
    r"\b(please reply|let me know|can you|could you|are you available|"
    r"confirm|rsvp|feedback|your thoughts|\?)\s*$|\?", re.I)
_FYO_SENDER_RE = re.compile(
    r"(no-?reply|newsletter|notifications?|digest|updates@|promo|marketing)", re.I)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _gather_items(max_mail: int, max_wa: int) -> list[dict[str, Any]]:
    """Normalize recent mail + WhatsApp into taskboard candidate items."""
    items: list[dict[str, Any]] = []
    try:
        from .mailbox import list_mail_messages, sync_gmail_metadata
        if _env_flag("SHIMS_DIGEST_REFRESH_GMAIL", False):
            try:
                sync_gmail_metadata(query="newer_than:2d", max_results=max_mail)
            except Exception:
                pass
        for m in list_mail_messages(limit=max_mail, provider="gmail"):
            ref = m.get("thread_id") or m.get("external_id") or m.get("id") or ""
            items.append({
                "source": "gmail",
                "from": m.get("sender") or "",
                "title": m.get("subject") or "(no subject)",
                "snippet": (m.get("snippet") or "")[:300],
                "received_at": m.get("received_at") or "",
                "url": f"https://mail.google.com/mail/u/1/#all/{ref}" if ref else "",
            })
    except Exception as exc:
        items.append({"source": "gmail", "from": "", "title": "(gmail unavailable)",
                      "snippet": str(exc)[:200], "received_at": "", "_error": True})
    try:
        from . import channels
        wa = channels.recent("whatsapp", max_wa)
        for m in wa.get("messages") or []:
            sender_id = (m.get("sender_id") or "").strip()
            # wa.me opens the DM chat in WhatsApp — only for real phone
            # numbers; groups and status broadcasts have no deep link.
            wa_url = (f"https://wa.me/{sender_id}"
                      if sender_id.isdigit() and not m.get("is_group") else "")
            items.append({
                "source": "whatsapp",
                "from": m.get("sender_name") or "",
                "title": (m.get("text") or "")[:80] or "(media)",
                "snippet": (m.get("text") or "")[:300],
                "received_at": m.get("received_at") or "",
                "url": wa_url,
            })
    except Exception:
        pass  # WhatsApp bridge offline is a normal case — mail still digests
    return [i for i in items if not i.get("_error")]


def _classify_heuristic(item: dict[str, Any]) -> str:
    text = f"{item.get('title', '')} {item.get('snippet', '')}"
    if _FYO_SENDER_RE.search(item.get("from") or ""):
        return "FYI"
    if _URGENT_RE.search(text):
        return "Urgent"
    if _REPLY_RE.search(text.strip()):
        return "Needs reply"
    return "Waiting"


def _classify_llm(items: list[dict[str, Any]]) -> list[str] | None:
    """One scoped native turn classifying all items at once. None on failure."""
    try:
        from .native_engine import get_engine
        engine = get_engine()
        if not engine.loaded_model_id():
            return None
        listing = "\n".join(
            f"{i}. [{it['source']}] from={it.get('from', '')} | {it.get('title', '')} — {it.get('snippet', '')[:120]}"
            for i, it in enumerate(items))
        prompt = (
            "Classify each message into exactly one bucket: Urgent, Needs reply, Waiting, FYI.\n"
            "Urgent = deadlines, compliance, payments, time-critical. Needs reply = a person "
            "expects an answer. Waiting = informational but may need later action. FYI = "
            "newsletters, promos, notifications.\n"
            "Reply with ONLY a JSON array of bucket strings, one per message, in order.\n\n"
            + listing)
        result = engine.chat_raw(
            [{"role": "user", "content": prompt}],
            max_tokens=min(400 + 60 * len(items), 2200), timeout=600.0)
        text = result.get("content") or ""
        # Lenient parse: a thinking model may burn most of the budget on
        # reasoning before the answer — accept a JSON array anywhere, or a
        # line-per-item listing of bucket names, as long as the count matches.
        buckets = None
        start, end = text.find("["), text.rfind("]")
        if 0 <= start < end:
            try:
                parsed = json.loads(text[start:end + 1])
                if isinstance(parsed, list):
                    buckets = parsed
            except Exception:
                buckets = None
        if buckets is None:
            found = re.findall(r"(?i)\b(urgent|needs[ -]?reply|waiting|fyi)\b", text)
            if len(found) >= len(items):
                buckets = found[-len(items):]
        if not isinstance(buckets, list) or len(buckets) != len(items):
            return None
        norm = []
        for b in buckets:
            b = str(b).strip().title().replace("Needs Reply", "Needs reply")
            norm.append(b if b in _BUCKETS else "Waiting")
        return norm
    except Exception:
        return None


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, lo: int = 1, hi: int = 500) -> int:
    try:
        return max(lo, min(int(os.getenv(name, str(default))), hi))
    except Exception:
        return default


def run_comms_digest(
    max_mail: int = 15,
    max_wa: int = 15,
    *,
    refresh_inventory: bool | None = None,
) -> dict[str, Any]:
    """Scan recent comms, classify into taskboard buckets, persist the board."""
    items = _gather_items(max_mail, max_wa)
    buckets = _classify_llm(items) if items else None
    classifier = "llm"
    if buckets is None:
        buckets = [_classify_heuristic(it) for it in items]
        classifier = "heuristic" if items else "none"

    board_items = []
    counts = {"urgent": 0, "needsReplySoon": 0, "waiting": 0, "fyi": 0}
    count_key = {"Urgent": "urgent", "Needs reply": "needsReplySoon",
                 "Waiting": "waiting", "FYI": "fyi"}
    next_action = {"Urgent": "Act today.", "Needs reply": "Reply needed.",
                   "Waiting": "Monitor.", "FYI": "Read when free."}
    for it, bucket in zip(items, buckets):
        counts[count_key[bucket]] += 1
        board_items.append({
            "bucket": bucket,
            "area": it["source"].title(),
            "source": it["source"],
            "priority": "High" if bucket == "Urgent" else (
                "Medium" if bucket == "Needs reply" else "Normal"),
            "title": it.get("title") or "",
            "from": it.get("from") or "",
            "snippet": it.get("snippet") or "",
            "received_at": it.get("received_at") or "",
            "url": it.get("url") or "",
            "nextAction": next_action[bucket],
            "sourceDetail": " · ".join(x for x in (it.get("from"), str(it.get("received_at") or "")) if x),
        })
    # Most actionable first.
    order = {"Urgent": 0, "Needs reply": 1, "Waiting": 2, "FYI": 3}
    board_items.sort(key=lambda x: order.get(x["bucket"], 9))

    # Manual pins survive regeneration and always float to the top.
    pins = load_pins()
    if pins:
        board_items = pins + [i for i in board_items
                              if i.get("title") not in {p.get("title") for p in pins}]

    board = {
        "generatedAt": _now_iso(),
        "scope": "SHIMS comms digest — Gmail (2d) + WhatsApp. Read-only; no state changed.",
        "classifier": classifier,
        "counts": counts,
        "items": board_items,
    }
    TASKBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASKBOARD_PATH.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        from .omni_brain import remember
        remember("comms_digest", f"digest:{int(time.time())}",
                 f"Comms digest: {counts['urgent']} urgent, {counts['needsReplySoon']} need reply, "
                 f"{counts['waiting']} waiting, {counts['fyi']} FYI ({classifier}).",
                 tags=["comms", "digest"], source="scheduler")
    except Exception:
        pass
    # Vendor inventory is useful, but it is heavier than a taskboard refresh:
    # it may touch Gmail history and an LLM. Keep "run now" responsive unless
    # the scheduler/admin explicitly enables the combined refresh.
    if refresh_inventory is None:
        refresh_inventory = _env_flag("SHIMS_DIGEST_REFRESH_INVENTORY", False)
    inventory_refreshed = False
    if refresh_inventory:
        try:
            from .chat_inventory import build_inventory
            build_inventory(
                wa_limit=_env_int("SHIMS_DIGEST_INVENTORY_WA_LIMIT", 300, lo=25, hi=2000),
                mail_limit=_env_int("SHIMS_DIGEST_INVENTORY_MAIL_LIMIT", 40, lo=5, hi=200),
            )
            inventory_refreshed = True
        except Exception:
            pass
    return {"ok": True, "classifier": classifier, "counts": counts,
            "item_count": len(board_items), "inventory_refreshed": inventory_refreshed,
            "path": str(TASKBOARD_PATH)}


def latest_taskboard() -> dict[str, Any]:
    """The most recent board (pins merged even before the next digest run),
    or an empty-shaped one when never generated."""
    board: dict[str, Any]
    try:
        if TASKBOARD_PATH.is_file():
            board = json.loads(TASKBOARD_PATH.read_text(encoding="utf-8"))
        else:
            board = {}
    except Exception:
        board = {}
    if not board:
        board = {"generatedAt": "", "scope": "No digest has run yet.",
                 "counts": {"urgent": 0, "needsReplySoon": 0, "waiting": 0, "fyi": 0},
                 "items": []}
    pins = load_pins()
    if pins:
        pin_titles = {p.get("title") for p in pins}
        board["items"] = pins + [i for i in board.get("items") or []
                                 if i.get("title") not in pin_titles]
    return board


def digest_interval_seconds() -> int:
    try:
        return max(15, int(os.getenv("SHIMS_DIGEST_INTERVAL_MIN", "120"))) * 60
    except ValueError:
        return 120 * 60
