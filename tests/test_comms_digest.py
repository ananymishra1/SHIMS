"""Comms digest: bucket classification + taskboard JSON shape."""
from __future__ import annotations

import json

from shared import comms_digest
from shared.comms_digest import (
    _classify_heuristic, latest_taskboard, run_comms_digest,
)


def test_heuristic_buckets():
    assert _classify_heuristic(
        {"from": "boss@co.com", "title": "URGENT: compliance deadline", "snippet": ""}) == "Urgent"
    assert _classify_heuristic(
        {"from": "person@x.com", "title": "lunch?", "snippet": "are you available tomorrow?"}) == "Needs reply"
    assert _classify_heuristic(
        {"from": "no-reply@news.com", "title": "Your weekly digest", "snippet": ""}) == "FYI"
    assert _classify_heuristic(
        {"from": "person@x.com", "title": "Quarterly report attached", "snippet": "fyi only"}) == "Waiting"


def test_run_digest_writes_hub_shaped_board(tmp_path, monkeypatch):
    monkeypatch.setattr(comms_digest, "TASKBOARD_PATH", tmp_path / "taskboard.json")
    monkeypatch.setattr(comms_digest, "PINS_PATH", tmp_path / "pins.json")
    monkeypatch.setattr(comms_digest, "_gather_items", lambda *a, **k: [
        {"source": "gmail", "from": "boss@co.com", "title": "URGENT: filing",
         "snippet": "deadline today", "received_at": "2026-08-04"},
        {"source": "whatsapp", "from": "Mom", "title": "call me when free",
         "snippet": "call me when free?", "received_at": "1785"},
        {"source": "gmail", "from": "no-reply@shop.com", "title": "50% off sale",
         "snippet": "promo", "received_at": "2026-08-04"},
    ])
    monkeypatch.setattr(comms_digest, "_classify_llm", lambda items: None)  # engine offline
    result = run_comms_digest()
    assert result["ok"] is True
    assert result["classifier"] == "heuristic"
    board = json.loads((tmp_path / "taskboard.json").read_text(encoding="utf-8"))
    # Exact shape the Desktop Hub renders.
    assert set(board) >= {"generatedAt", "scope", "counts", "items"}
    assert set(board["counts"]) == {"urgent", "needsReplySoon", "waiting", "fyi"}
    assert board["counts"]["urgent"] == 1
    assert board["counts"]["fyi"] == 1
    buckets = [i["bucket"] for i in board["items"]]
    assert buckets == sorted(buckets, key=lambda b: {"Urgent": 0, "Needs reply": 1, "Waiting": 2, "FYI": 3}[b])
    first = board["items"][0]
    assert set(first) >= {"bucket", "area", "priority", "title"}


def test_latest_taskboard_empty_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(comms_digest, "TASKBOARD_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(comms_digest, "PINS_PATH", tmp_path / "pins.json")
    board = latest_taskboard()
    assert board["items"] == []
    assert set(board["counts"]) == {"urgent", "needsReplySoon", "waiting", "fyi"}


def test_pins_survive_and_float_to_top(tmp_path, monkeypatch):
    monkeypatch.setattr(comms_digest, "TASKBOARD_PATH", tmp_path / "taskboard.json")
    monkeypatch.setattr(comms_digest, "PINS_PATH", tmp_path / "pins.json")
    comms_digest.add_pin("Pinned QA task", bucket="Urgent", area="QA", detail="check this")
    monkeypatch.setattr(comms_digest, "_gather_items", lambda *a, **k: [
        {"source": "gmail", "from": "no-reply@x.com", "title": "newsletter", "snippet": "", "received_at": ""},
    ])
    monkeypatch.setattr(comms_digest, "_classify_llm", lambda items: None)
    run_comms_digest()
    board = latest_taskboard()
    assert board["items"][0]["title"] == "Pinned QA task"
    assert board["items"][0].get("pinned") is True
    # Pin again with the same title — deduped, not doubled.
    comms_digest.add_pin("Pinned QA task", bucket="Urgent")
    assert len(comms_digest.load_pins()) == 1


def test_digest_interval_env(monkeypatch):
    monkeypatch.setenv("SHIMS_DIGEST_INTERVAL_MIN", "30")
    assert comms_digest.digest_interval_seconds() == 1800
    monkeypatch.setenv("SHIMS_DIGEST_INTERVAL_MIN", "bogus")
    assert comms_digest.digest_interval_seconds() == 7200


def test_scheduler_accepts_comms_digest_action():
    from shared.desktop_scheduler import schedule_task
    bad = schedule_task("t", "interval", "3600", "not_a_real_action", {})
    assert bad["ok"] is False
    good = schedule_task("test digest", "interval", "3600", "comms_digest", {})
    assert good["ok"] is True
    from shared.desktop_scheduler import delete_task
    delete_task(good["task_id"])
