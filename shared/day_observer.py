"""SHIMS day observer.

Watches the system through the day and rolls everything up for the nightly
self-fix loop. It adds NO new logging pipeline of its own for the report —
chat turns, tool/model calls, errors, feedback, and ledger actions are already
persisted by the existing stores; the observer only snapshots live status and
aggregates what is already there.

Files:
- ``logs/observer/YYYY-MM-DD.jsonl`` — periodic status snapshots (``snapshot``).
- ``logs/observer/YYYY-MM-DD-report.md`` — end-of-day human-readable report
  (``collect_day_report``), also returned as a dict for the nightly LLM.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ROOT_DIR

OBSERVER_DIR = ROOT_DIR / "logs" / "observer"
OBSERVER_DIR.mkdir(parents=True, exist_ok=True)


def _day_bounds(day: str) -> tuple[float, float]:
    """Epoch bounds [start, end) for a local YYYY-MM-DD day."""
    start = datetime.strptime(day, "%Y-%m-%d").timestamp()
    return start, start + 86400.0


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _query(db: Path, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def snapshot() -> dict[str, Any]:
    """Append one status snapshot line to today's observer log. Cheap and
    best-effort: any subsystem that fails to report is recorded as unknown."""
    now = time.time()
    entry: dict[str, Any] = {"ts": now, "iso": datetime.now().isoformat(timespec="seconds")}

    try:
        from shared.native_engine import get_engine
        health = get_engine().health() or {}
        entry["native_engine"] = {
            "state": health.get("state", "unknown"),
            "model": health.get("model", ""),
            "ready": bool(health.get("ready")),
        }
    except Exception as exc:  # pragma: no cover - defensive
        entry["native_engine"] = {"state": "unknown", "error": str(exc)[:120]}

    try:
        from shared.compute_orchestrator import get_orchestrator
        orch = get_orchestrator().status()
        entry["orchestrator"] = {
            "idle": bool((orch.get("idle") or {}).get("is_idle")),
            "queued_image_jobs": (orch.get("queue") or {}).get("pending", 0),
            "free_ram_bytes": (orch.get("memory") or {}).get("free_ram_bytes", 0),
        }
    except Exception:
        entry["orchestrator"] = {"state": "unknown"}

    try:
        from shared import telemetry as _tel
        recent = _tel.recent_events(limit=100)
        entry["recent_errors"] = sum(1 for e in recent if not e.get("ok", 1))
    except Exception:
        entry["recent_errors"] = -1

    try:
        from shared.action_ledger import action_status
        entry["ledger"] = (action_status() or {}).get("counts", {})
    except Exception:
        entry["ledger"] = {}

    try:
        log_path = OBSERVER_DIR / f"{_today()}.jsonl"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        entry["log"] = str(log_path)
    except Exception:
        pass
    return entry


def collect_day_report(day: str | None = None) -> dict[str, Any]:
    """Aggregate one day's activity from the existing stores and write the
    markdown report consumed by the nightly self-fix loop."""
    day = day or _today()
    start, end = _day_bounds(day)
    state_dir = ROOT_DIR / "data" / "state"

    # --- Telemetry events (errors, latency, routes) ---
    tel_db = state_dir / "shims_telemetry.sqlite3"
    events = _query(
        tel_db,
        "SELECT event_type, route, provider, model, latency_ms, ok, message "
        "FROM telemetry_events WHERE ts >= ? AND ts < ?",
        (start, end),
    )
    errors = [e for e in events if not e.get("ok", 1)]
    latencies = sorted(float(e["latency_ms"] or 0) for e in events if float(e["latency_ms"] or 0) > 0)
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
    route_counts: dict[str, int] = {}
    for e in events:
        key = e.get("route") or e.get("event_type") or "unknown"
        route_counts[key] = route_counts.get(key, 0) + 1

    # --- Chat turns (episodes) ---
    brain_db = Path(os.getenv("SHIMS_BRAIN_DB", str(state_dir / "omni_brain.sqlite3")))
    episodes = _query(
        brain_db,
        "SELECT session_id, route, provider, model, quality, "
        "substr(user_text, 1, 160) AS user_excerpt, created_at "
        "FROM episodes WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
        (start, end),
    )
    providers: dict[str, int] = {}
    for ep in episodes:
        key = f"{ep.get('provider') or '?'}/{ep.get('model') or '?'}"
        providers[key] = providers.get(key, 0) + 1

    # --- Feedback (thumbs up/down memories) ---
    feedback = _query(
        brain_db,
        "SELECT key, value, tags_json, created_at FROM memories "
        "WHERE namespace = 'omni_feedback' AND updated_at >= ? AND updated_at < ? "
        "ORDER BY updated_at",
        (start, end),
    )

    # --- Agent telemetry (tool + model call health) ---
    agent_db = state_dir / "agent_telemetry.sqlite3"
    tool_calls = _query(
        agent_db,
        "SELECT tool_name, success, COUNT(*) AS n, AVG(latency_ms) AS avg_ms "
        "FROM tool_calls WHERE created_at >= ? AND created_at < ? "
        "GROUP BY tool_name, success",
        (start, end),
    )
    model_calls = _query(
        agent_db,
        "SELECT provider, model, success, COUNT(*) AS n, AVG(latency_ms) AS avg_ms "
        "FROM model_calls WHERE created_at >= ? AND created_at < ? "
        "GROUP BY provider, model, success",
        (start, end),
    )

    # --- Action ledger ---
    try:
        from shared.action_ledger import action_status
        ledger = (action_status() or {}).get("counts", {})
    except Exception:
        ledger = {}

    # --- Observer snapshots taken during the day ---
    snapshots: list[dict[str, Any]] = []
    snap_path = OBSERVER_DIR / f"{day}.jsonl"
    if snap_path.exists():
        for line in snap_path.read_text(encoding="utf-8").splitlines():
            try:
                snapshots.append(json.loads(line))
            except Exception:
                continue

    report: dict[str, Any] = {
        "ok": True,
        "day": day,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "events": {
            "total": len(events),
            "errors": len(errors),
            "error_samples": [
                f"{e.get('event_type')}:{e.get('route')} — {str(e.get('message') or '')[:160]}"
                for e in errors[:15]
            ],
            "latency_p95_ms": round(p95, 1),
            "top_routes": sorted(route_counts.items(), key=lambda kv: kv[1], reverse=True)[:10],
        },
        "chat": {
            "turns": len(episodes),
            "providers": sorted(providers.items(), key=lambda kv: kv[1], reverse=True),
            "sample_user_turns": [ep.get("user_excerpt") for ep in episodes[:20]],
        },
        "feedback": [
            {"key": f.get("key"), "value": str(f.get("value") or "")[:240]}
            for f in feedback
        ],
        "tools": tool_calls,
        "models": model_calls,
        "ledger": ledger,
        "snapshots": len(snapshots),
    }

    md = render_report_markdown(report)
    report_path = OBSERVER_DIR / f"{day}-report.md"
    report_path.write_text(md, encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def render_report_markdown(report: dict[str, Any]) -> str:
    ev = report["events"]
    chat = report["chat"]
    lines = [
        f"# SHIMS day report — {report['day']}",
        "",
        f"- Events: {ev['total']} ({ev['errors']} errors), latency p95 {ev['latency_p95_ms']} ms",
        f"- Chat turns: {chat['turns']}",
        f"- Feedback items: {len(report['feedback'])}",
        f"- Ledger: {json.dumps(report['ledger'])}",
        "",
        "## Top routes",
    ]
    lines += [f"- {name}: {n}" for name, n in ev["top_routes"]] or ["- (none)"]
    lines += ["", "## Errors"]
    lines += [f"- {s}" for s in ev["error_samples"]] or ["- (none)"]
    lines += ["", "## Model calls"]
    lines += [
        f"- {m['provider']}/{m['model']} success={m['success']} n={m['n']} avg={round(m['avg_ms'] or 0)}ms"
        for m in report["models"]
    ] or ["- (none)"]
    lines += ["", "## Tool calls"]
    lines += [
        f"- {t['tool_name']} success={t['success']} n={t['n']} avg={round(t['avg_ms'] or 0)}ms"
        for t in report["tools"]
    ] or ["- (none)"]
    lines += ["", "## Feedback (thumbs)"]
    lines += [f"- {f['key']}: {f['value']}" for f in report["feedback"]] or ["- (none)"]
    lines += ["", "## Sample user turns"]
    lines += [f"- {s}" for s in chat["sample_user_turns"]] or ["- (none)"]
    return "\n".join(lines) + "\n"
