"""Tests for background job scheduling and inbox ingestion."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIMS_DATA_DIR", str(tmp_path))


def test_ensure_default_jobs():
    from shared.background_jobs import ensure_default_jobs, list_background_jobs
    res = ensure_default_jobs()
    assert res["ok"]
    jobs = list_background_jobs()
    assert any(j["task_id"] == "shims-bg-improvement-loop" for j in jobs)
    assert any(j["task_id"] == "shims-bg-self-index" for j in jobs)
    assert any(j["task_id"] == "shims-bg-media-inbox" for j in jobs)


def test_ensure_idempotent():
    from shared.background_jobs import ensure_default_jobs
    first = ensure_default_jobs()
    second = ensure_default_jobs()
    assert second["created"] == []


def test_run_inbox_ingest_empty():
    from shared.background_jobs import run_inbox_ingest
    res = run_inbox_ingest({})
    assert res["ok"]
    assert res["processed"] == 0
