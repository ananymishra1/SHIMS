"""Agent telemetry — tracks tool and model performance for self-evolution.

Records every tool call, LLM call, fallback, and replan event.
Provides gap detection to auto-generate evolution proposals.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .config import ROOT_DIR

_TELEMETRY_DB = Path(ROOT_DIR) / "data" / "state" / "agent_telemetry.sqlite3"
_TELEMETRY_DB.parent.mkdir(parents=True, exist_ok=True)

_MIN_CALLS_FOR_GAP = 3
_FAILURE_RATE_THRESHOLD = 0.5
_TIMEOUT_RATE_THRESHOLD = 0.3


@dataclass
class ToolMetric:
    tool_name: str
    calls: int = 0
    successes: int = 0
    failures: int = 0
    timeouts: int = 0
    avg_latency_ms: float = 0.0
    last_used: float = 0.0


@dataclass
class ModelMetric:
    provider: str
    model: str
    calls: int = 0
    successes: int = 0
    failures: int = 0
    timeouts: int = 0
    avg_latency_ms: float = 0.0
    fallback_count: int = 0


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(_TELEMETRY_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_name TEXT NOT NULL,
            args_hash TEXT,
            success INTEGER DEFAULT 0,
            timeout INTEGER DEFAULT 0,
            latency_ms REAL DEFAULT 0,
            error TEXT,
            session_id TEXT,
            created_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_tc_tool ON tool_calls(tool_name)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_tc_session ON tool_calls(session_id)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS model_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            success INTEGER DEFAULT 0,
            timeout INTEGER DEFAULT 0,
            latency_ms REAL DEFAULT 0,
            error TEXT,
            is_fallback INTEGER DEFAULT 0,
            session_id TEXT,
            created_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_mc_model ON model_calls(provider, model)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS replan_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            failed_step_idx INTEGER,
            failed_tool TEXT,
            error TEXT,
            reflection TEXT,
            created_at REAL NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS unhandled_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT NOT NULL,
            count INTEGER DEFAULT 1,
            last_seen REAL NOT NULL,
            UNIQUE(pattern)
        )
        """
    )
    return con


# ------------------------------------------------------------------ #
# Recording
# ------------------------------------------------------------------ #
def record_tool_call(
    tool_name: str,
    success: bool,
    latency_ms: float = 0.0,
    timeout: bool = False,
    error: str = "",
    args_hash: str = "",
    session_id: str = "",
) -> None:
    try:
        con = _connect()
        con.execute(
            "INSERT INTO tool_calls (tool_name, args_hash, success, timeout, latency_ms, error, session_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tool_name, args_hash, int(success), int(timeout), latency_ms, error[:500], session_id, time.time()),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def record_model_call(
    provider: str,
    model: str,
    success: bool,
    latency_ms: float = 0.0,
    timeout: bool = False,
    error: str = "",
    is_fallback: bool = False,
    session_id: str = "",
) -> None:
    try:
        con = _connect()
        con.execute(
            "INSERT INTO model_calls (provider, model, success, timeout, latency_ms, error, is_fallback, session_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (provider, model, int(success), int(timeout), latency_ms, error[:500], int(is_fallback), session_id, time.time()),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def record_replan(
    failed_step_idx: int,
    failed_tool: str,
    error: str,
    reflection: str = "",
    session_id: str = "",
) -> None:
    try:
        con = _connect()
        con.execute(
            "INSERT INTO replan_events (session_id, failed_step_idx, failed_tool, error, reflection, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, failed_step_idx, failed_tool, error[:500], reflection[:500], time.time()),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def record_unhandled_pattern(pattern: str) -> None:
    """Record a user request pattern that no tool handled."""
    try:
        con = _connect()
        con.execute(
            """
            INSERT INTO unhandled_patterns (pattern, count, last_seen)
            VALUES (?, 1, ?)
            ON CONFLICT(pattern) DO UPDATE SET count = count + 1, last_seen = excluded.last_seen
            """,
            (pattern[:200], time.time()),
        )
        con.commit()
        con.close()
    except Exception:
        pass


# ------------------------------------------------------------------ #
# Metrics aggregation
# ------------------------------------------------------------------ #
def get_tool_metrics(since_hours: int = 168) -> dict[str, ToolMetric]:
    """Aggregate tool performance over the last N hours (default 1 week)."""
    cutoff = time.time() - since_hours * 3600
    try:
        con = _connect()
        rows = con.execute(
            "SELECT tool_name, SUM(success), SUM(1-success), SUM(timeout), AVG(latency_ms), MAX(created_at), COUNT(*) FROM tool_calls WHERE created_at > ? GROUP BY tool_name",
            (cutoff,),
        ).fetchall()
        con.close()
        def _int(v):
            try:
                return int(v) if v is not None else 0
            except (TypeError, ValueError):
                return 0

        def _float(v):
            try:
                return float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        return {
            r["tool_name"]: ToolMetric(
                tool_name=r["tool_name"],
                calls=_int(r[6]),
                successes=_int(r[0]),
                failures=_int(r[1]),
                timeouts=_int(r[2]),
                avg_latency_ms=round(_float(r[3]), 1),
                last_used=_float(r[4]),
            )
            for r in rows
        }
    except Exception:
        return {}


def get_model_metrics(since_hours: int = 168) -> dict[str, ModelMetric]:
    """Aggregate model performance."""
    cutoff = time.time() - since_hours * 3600
    try:
        con = _connect()
        rows = con.execute(
            "SELECT provider, model, SUM(success), SUM(1-success), SUM(timeout), AVG(latency_ms), SUM(is_fallback), COUNT(*) FROM model_calls WHERE created_at > ? GROUP BY provider, model",
            (cutoff,),
        ).fetchall()
        con.close()
        def _int(v):
            try:
                return int(v) if v is not None else 0
            except (TypeError, ValueError):
                return 0

        def _float(v):
            try:
                return float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        return {
            f"{r['provider']}:{r['model']}": ModelMetric(
                provider=r["provider"],
                model=r["model"],
                calls=_int(r[7]),
                successes=_int(r[0]),
                failures=_int(r[1]),
                timeouts=_int(r[2]),
                avg_latency_ms=round(_float(r[3]), 1),
                fallback_count=_int(r[6]),
            )
            for r in rows
        }
    except Exception:
        return {}


# ------------------------------------------------------------------ #
# Gap detection
# ------------------------------------------------------------------ #
def detect_agent_gaps() -> list[dict[str, Any]]:
    """Analyze telemetry and return detected gaps with auto-proposals."""
    gaps: list[dict[str, Any]] = []
    tool_metrics = get_tool_metrics()
    model_metrics = get_model_metrics()

    # Gap 1: Tool with high failure rate
    for name, metric in tool_metrics.items():
        if metric.calls >= _MIN_CALLS_FOR_GAP and metric.failures / metric.calls >= _FAILURE_RATE_THRESHOLD:
            gaps.append({
                "type": "broken_tool",
                "severity": "high" if metric.failures / metric.calls > 0.8 else "medium",
                "tool": name,
                "calls": metric.calls,
                "failures": metric.failures,
                "failure_rate": round(metric.failures / metric.calls, 2),
                "proposal": f"Fix {name} reliability — it fails {metric.failures}/{metric.calls} times ({metric.failures*100//metric.calls}%)",
                "target_file": "shared/agent_tools.py",
            })

    # Gap 2: Model with high timeout rate
    for name, metric in model_metrics.items():
        if metric.calls >= _MIN_CALLS_FOR_GAP and metric.timeouts / metric.calls >= _TIMEOUT_RATE_THRESHOLD:
            gaps.append({
                "type": "slow_model",
                "severity": "medium",
                "provider": metric.provider,
                "model": metric.model,
                "calls": metric.calls,
                "timeouts": metric.timeouts,
                "timeout_rate": round(metric.timeouts / metric.calls, 2),
                "avg_latency_ms": metric.avg_latency_ms,
                "proposal": f"Add faster fallback chain for {metric.provider}/{metric.model} — timeouts {metric.timeouts}/{metric.calls}",
                "target_file": "shared/agent_loop.py",
            })

    # Gap 3: Model with high failure rate
    for name, metric in model_metrics.items():
        if metric.calls >= _MIN_CALLS_FOR_GAP and metric.failures / metric.calls >= _FAILURE_RATE_THRESHOLD:
            gaps.append({
                "type": "unreliable_model",
                "severity": "high",
                "provider": metric.provider,
                "model": metric.model,
                "calls": metric.calls,
                "failures": metric.failures,
                "failure_rate": round(metric.failures / metric.calls, 2),
                "proposal": f"Investigate {metric.provider}/{metric.model} errors — {metric.failures}/{metric.calls} failures",
                "target_file": "shared/agent_loop.py",
            })

    # Gap 4: Unhandled patterns (user requests no tool handles)
    try:
        con = _connect()
        rows = con.execute(
            "SELECT pattern, count FROM unhandled_patterns WHERE count >= 2 ORDER BY count DESC LIMIT 5"
        ).fetchall()
        con.close()
        for r in rows:
            gaps.append({
                "type": "missing_tool",
                "severity": "low",
                "pattern": r["pattern"],
                "count": r["count"],
                "proposal": f"Add a tool for: '{r['pattern']}' (seen {r['count']} times)",
                "target_file": "shared/agent_tools.py",
            })
    except Exception:
        pass

    return gaps


def auto_propose_from_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate evolution proposals from detected gaps.
    Returns list of proposal dicts ready for the neural governor."""
    proposals: list[dict[str, Any]] = []
    for gap in gaps:
        p = {
            "ok": True,
            "proposal_id": f"auto_{gap['type']}_{int(time.time())}_{len(proposals)}",
            "intent": gap["proposal"],
            "file_path": gap.get("target_file", "shared/agent_tools.py"),
            "description": json.dumps(gap, default=str),
            "auto_queued": True,
            "source": "agent_telemetry",
        }
        proposals.append(p)
    return proposals


def get_telemetry_summary() -> dict[str, Any]:
    """Human-readable summary of recent agent telemetry."""
    tools = get_tool_metrics(since_hours=24)
    models = get_model_metrics(since_hours=24)
    return {
        "period_hours": 24,
        "total_tool_calls": sum(m.calls for m in tools.values()),
        "total_model_calls": sum(m.calls for m in models.values()),
        "tools": {name: asdict(m) for name, m in tools.items()},
        "models": {name: asdict(m) for name, m in models.items()},
        "gaps": detect_agent_gaps(),
    }
