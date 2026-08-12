"""SHIMS nightly feedback + self-fix loop.

Runs once a night (default 01:00 local, seeded via the desktop scheduler as
action_type ``nightly_cycle``). Slow is fine — accuracy is the goal, so the
reflection model is the biggest native GGUF available (``SHIMS_NIGHTLY_MODEL``)
with a generous timeout (``SHIMS_NIGHTLY_TIMEOUT_S``, default 1800 s).

Pipeline:
1. ``day_observer.collect_day_report()`` — the day's turns, errors, latency,
   tool/model health, feedback, ledger state.
2. ``app_doctor.diagnose_app`` for every app under ``apps/`` — the
   enterprise/app fix pass.
3. ``improvement_loop.run_improvement_cycle(extra_context=...)`` — evals plus
   an LLM reflection over the *actual day*, producing proposals.
4. Low-risk patch proposals auto-apply through the normal
   validate → approve → apply pipeline (auto-rollback on failure) when
   ``SHIMS_NIGHTLY_AUTO_APPLY`` is true (default). Anything riskier waits for
   morning approval, exactly like daytime proposals.

Every run is persisted to ``storage/nightly_loop/<run_id>.json`` and logged as
a ``nightly_loop.run`` telemetry event.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .config import ROOT_DIR

NIGHTLY_DIR = ROOT_DIR / "storage" / "nightly_loop"
NIGHTLY_DIR.mkdir(parents=True, exist_ok=True)


def _log_event(event_type: str, **kwargs: Any) -> None:
    try:
        from .telemetry import log_event
        log_event(event_type, **kwargs)
    except Exception:  # pragma: no cover - telemetry must never break the run
        pass


def nightly_auto_apply() -> bool:
    return (os.getenv("SHIMS_NIGHTLY_AUTO_APPLY", "true").strip().lower()
            in {"1", "true", "yes", "on"})


def _diagnose_apps() -> dict[str, Any]:
    """Run app doctor over every vertical app (enterprise fix pass)."""
    findings: dict[str, Any] = {}
    try:
        from .app_doctor import diagnose_app
    except Exception:
        return {"skipped": "app_doctor unavailable"}
    apps_dir = ROOT_DIR / "apps"
    if not apps_dir.exists():
        return findings
    for child in sorted(apps_dir.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        try:
            report = diagnose_app(child.name)
            issues = report.get("issues") or []
            findings[child.name] = {
                "issue_count": len(issues),
                "issues": [str(i)[:200] for i in issues[:10]],
            }
        except Exception as exc:
            findings[child.name] = {"error": str(exc)[:200]}
    return findings


def run_nightly_cycle(*, day: str | None = None) -> dict[str, Any]:
    """Execute the full nightly loop. Safe to call manually anytime."""
    run_id = f"nightly_{int(time.time())}"
    started = time.time()

    from . import day_observer
    report = day_observer.collect_day_report(day)
    app_findings = _diagnose_apps()

    extra_context = (
        "SHIMS day report (real activity from the last 24h — prioritize these "
        "failures over synthetic eval results):\n"
        + json.dumps(
            {
                "events": report.get("events"),
                "chat": {k: v for k, v in (report.get("chat") or {}).items() if k != "sample_user_turns"},
                "feedback": report.get("feedback"),
                "models": report.get("models"),
                "tools": report.get("tools"),
                "app_doctor": app_findings,
            },
            ensure_ascii=False, default=str,
        )[:8000]
    )

    from .improvement_loop import run_improvement_cycle
    improvement = run_improvement_cycle(
        extra_context=extra_context,
        auto_apply_low_risk=nightly_auto_apply(),
    )

    result: dict[str, Any] = {
        "ok": True,
        "run_id": run_id,
        "started_at": started,
        "finished_at": time.time(),
        "duration_s": round(time.time() - started, 1),
        "day": report.get("day"),
        "report_path": report.get("report_path"),
        "app_doctor": app_findings,
        "improvement": improvement,
        "auto_apply_low_risk": nightly_auto_apply(),
    }
    (NIGHTLY_DIR / f"{run_id}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    _log_event(
        "nightly_loop.run",
        route="nightly",
        latency_ms=(time.time() - started) * 1000,
        ok=True,
        message=f"day={report.get('day')} errors={report.get('events', {}).get('errors', 0)}",
    )
    return result


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Most recent nightly runs, newest first."""
    runs: list[dict[str, Any]] = []
    for path in sorted(NIGHTLY_DIR.glob("nightly_*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            runs.append(
                {
                    "run_id": data.get("run_id", path.stem),
                    "day": data.get("day"),
                    "duration_s": data.get("duration_s"),
                    "proposals": len((data.get("improvement") or {}).get("proposals", []) or []),
                    "path": str(path),
                }
            )
        except Exception:
            continue
    return runs
