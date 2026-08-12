"""Day observer + nightly loop tests."""
from __future__ import annotations

import json

import pytest

import shared.day_observer as day_observer
import shared.nightly_loop as nightly_loop


@pytest.fixture
def observer_tmp(tmp_path, monkeypatch):
    """Isolate the observer/nightly outputs from the live data stores."""
    obs_dir = tmp_path / "observer"
    obs_dir.mkdir()
    monkeypatch.setattr(day_observer, "OBSERVER_DIR", obs_dir)
    monkeypatch.setattr(day_observer, "ROOT_DIR", tmp_path)  # no live DBs under tmp
    monkeypatch.setattr(nightly_loop, "NIGHTLY_DIR", tmp_path / "nightly")
    nightly_loop.NIGHTLY_DIR.mkdir()
    return tmp_path


# --------------------------------------------------------------------------- #
# day_observer
# --------------------------------------------------------------------------- #

def test_snapshot_appends_jsonl(observer_tmp):
    entry = day_observer.snapshot()
    assert entry["ts"] > 0
    log_path = day_observer.OBSERVER_DIR / f"{day_observer._today()}.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert "native_engine" in parsed
    assert "ledger" in parsed


def test_collect_day_report_empty_day(observer_tmp):
    report = day_observer.collect_day_report()
    assert report["ok"] is True
    assert report["events"]["total"] == 0
    assert report["chat"]["turns"] == 0
    assert report["feedback"] == []
    md = (day_observer.OBSERVER_DIR / f"{report['day']}-report.md").read_text(encoding="utf-8")
    assert "SHIMS day report" in md


def test_collect_day_report_reads_seeded_telemetry(observer_tmp):
    # Seed a telemetry DB under the patched ROOT_DIR for today's window.
    import sqlite3
    import time
    state_dir = observer_tmp / "data" / "state"
    state_dir.mkdir(parents=True)
    conn = sqlite3.connect(state_dir / "shims_telemetry.sqlite3")
    conn.execute(
        "CREATE TABLE telemetry_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, iso_ts TEXT, "
        "event_type TEXT, route TEXT, provider TEXT, model TEXT, latency_ms REAL, ok INTEGER, "
        "message TEXT, metadata_json TEXT)"
    )
    now = time.time()
    conn.execute("INSERT INTO telemetry_events(ts, iso_ts, event_type, route, ok, message, latency_ms) VALUES (?, '', 'chat.turn', 'native-unified', 1, '', 120)", (now,))
    conn.execute("INSERT INTO telemetry_events(ts, iso_ts, event_type, route, ok, message, latency_ms) VALUES (?, '', 'turn.error', 'native-unified', 0, 'boom', 0)", (now,))
    conn.commit()
    conn.close()
    report = day_observer.collect_day_report()
    assert report["events"]["total"] == 2
    assert report["events"]["errors"] == 1
    assert report["events"]["error_samples"] == ["turn.error:native-unified — boom"]


# --------------------------------------------------------------------------- #
# nightly_loop
# --------------------------------------------------------------------------- #

def test_nightly_cycle_passes_day_context_and_auto_apply(observer_tmp, monkeypatch):
    monkeypatch.setattr(
        day_observer, "collect_day_report",
        lambda day=None: {
            "ok": True, "day": "2026-08-05", "report_path": "/tmp/x.md",
            "events": {"total": 3, "errors": 1, "error_samples": ["boom"]},
            "chat": {"turns": 2, "providers": [["native/Qwen3.6", 2]]},
            "feedback": [{"key": "avoid:x", "value": "bad"}],
            "models": [], "tools": [],
        },
    )
    monkeypatch.setattr(nightly_loop, "_diagnose_apps", lambda: {"todo_demo": {"issue_count": 0, "issues": []}})
    captured: dict = {}

    import shared.improvement_loop as il
    monkeypatch.setattr(il, "run_improvement_cycle", lambda **kw: captured.update(kw) or {"ok": True, "proposals": []})

    monkeypatch.setenv("SHIMS_NIGHTLY_AUTO_APPLY", "true")
    result = nightly_loop.run_nightly_cycle()
    assert result["ok"] is True
    assert "SHIMS day report" in captured["extra_context"]
    assert "boom" in captured["extra_context"]
    assert captured["auto_apply_low_risk"] is True
    # Run persisted + listable
    runs = nightly_loop.list_runs()
    assert runs and runs[0]["run_id"] == result["run_id"]


def test_nightly_cycle_honors_auto_apply_off(observer_tmp, monkeypatch):
    monkeypatch.setattr(day_observer, "collect_day_report", lambda day=None: {"ok": True, "day": "2026-08-05", "events": {}, "chat": {}, "feedback": [], "models": [], "tools": []})
    monkeypatch.setattr(nightly_loop, "_diagnose_apps", lambda: {})
    captured: dict = {}
    import shared.improvement_loop as il
    monkeypatch.setattr(il, "run_improvement_cycle", lambda **kw: captured.update(kw) or {"ok": True})
    monkeypatch.setenv("SHIMS_NIGHTLY_AUTO_APPLY", "false")
    nightly_loop.run_nightly_cycle()
    assert captured["auto_apply_low_risk"] is False


# --------------------------------------------------------------------------- #
# scheduler whitelist
# --------------------------------------------------------------------------- #

def test_scheduler_accepts_observer_and_nightly_actions(tmp_path, monkeypatch):
    import shared.desktop_scheduler as sched
    monkeypatch.setattr(sched, "SCHEDULER_DB", tmp_path / "sched.sqlite3")
    monkeypatch.setattr(sched, "record_action", lambda *a, **k: {"ok": True})
    r1 = sched.schedule_task("obs", "interval", "1800", "day_observer", {})
    assert r1["ok"] is True, r1
    r2 = sched.schedule_task("nightly", "cron", "0 1 * * *", "nightly_cycle", {})
    assert r2["ok"] is True, r2
    r3 = sched.schedule_task("bad", "interval", "60", "not_a_real_action", {})
    assert r3["ok"] is False


# --------------------------------------------------------------------------- #
# improvement_loop nightly extension (extra_context + auto-apply low-risk)
# --------------------------------------------------------------------------- #

def test_reflection_prompt_includes_extra_context():
    import shared.improvement_loop as il
    prompt = il._build_reflection_prompt({"results": []}, {"ok": True}, {"summary": {"score": 1.0}}, extra_context="DAY REPORT: 3 errors on route X")
    assert "Observed production activity" in prompt
    assert "DAY REPORT: 3 errors on route X" in prompt


def _fake_control():
    return None  # patch branch never touches the control variant


def test_auto_apply_eligible_tests_path_applies(monkeypatch):
    import shared.improvement_loop as il
    import shared.self_evolver as se

    monkeypatch.setattr(il, "_propose_patch_safe", lambda rel, content, reason: {
        "ok": True, "proposal_id": "p1", "risk": "medium", "size": 120,
    })
    calls: list[str] = []
    monkeypatch.setattr(se, "validate_proposal", lambda pid: calls.append("validate") or type("R", (), {"status": "validated"})())
    monkeypatch.setattr(se, "approve_proposal", lambda pid, **kw: calls.append("approve") or type("R", (), {"status": "approved"})())
    monkeypatch.setattr(se, "apply_proposal", lambda pid, **kw: calls.append("apply") or type("R", (), {"status": "applied"})())

    item = {"type": "patch", "relative_path": "tests/test_x.py", "new_content": "# test", "reason": "nightly"}
    out = il._apply_proposal_item(item, _fake_control(), auto_apply_low_risk=True)
    assert calls == ["validate", "approve", "apply"]
    assert out["auto_applied"] is True


def test_auto_apply_skips_medium_risk_source(monkeypatch):
    import shared.improvement_loop as il
    import shared.self_evolver as se

    monkeypatch.setattr(il, "_propose_patch_safe", lambda rel, content, reason: {
        "ok": True, "proposal_id": "p2", "risk": "medium", "size": 9000,
    })
    calls: list[str] = []
    monkeypatch.setattr(se, "validate_proposal", lambda pid: calls.append("validate"))
    monkeypatch.setattr(se, "apply_proposal", lambda pid, **kw: calls.append("apply"))

    item = {"type": "patch", "relative_path": "shared/omni_brain.py", "new_content": "x" * 9000, "reason": "nightly"}
    out = il._apply_proposal_item(item, _fake_control(), auto_apply_low_risk=True)
    assert calls == []
    assert "auto_applied" not in out


def test_daytime_proposals_never_auto_apply(monkeypatch):
    import shared.improvement_loop as il
    import shared.self_evolver as se

    monkeypatch.setattr(il, "_propose_patch_safe", lambda rel, content, reason: {
        "ok": True, "proposal_id": "p3", "risk": "low", "size": 50,
    })
    calls: list[str] = []
    monkeypatch.setattr(se, "apply_proposal", lambda pid, **kw: calls.append("apply"))

    item = {"type": "patch", "relative_path": "tests/test_y.py", "new_content": "# t", "reason": "daytime"}
    out = il._apply_proposal_item(item, _fake_control())  # auto_apply_low_risk defaults False
    assert calls == []
    assert "auto_applied" not in out
