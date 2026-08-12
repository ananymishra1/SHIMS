"""Tests for shared/compute_orchestrator.py (Phase 5).

All lifecycle tests use fakes — no real ComfyUI, fish-speech, or subprocess
servers are started. pytest must run with --basetemp=.pytest_tmp on Windows.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

import shared.compute_orchestrator as co


class FakeProc:
    """Minimal subprocess.Popen stand-in."""

    def __init__(self, *args, **kwargs):
        self.args = args[0] if args else []
        self.pid = 4321
        self._alive = True
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False
        self.killed = True

    def wait(self, timeout=None):
        self._alive = False
        return 0


class DummyLog:
    def write(self, *_a, **_kw):
        pass

    def close(self):
        pass


@pytest.fixture()
def orch(tmp_path):
    """A fresh orchestrator with output redirected to tmp_path."""
    instance = co.ComputeOrchestrator(output_dir=tmp_path, popen=FakeProc)
    yield instance
    # Never leak drain threads between tests.
    instance._stop_dequeue.set()
    thread = instance._drain_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=5.0)


def _force_idle(orch, seconds: float = 100000.0) -> None:
    orch._last_activity = orch._now() - seconds


def test_budget_ledger_math(orch, monkeypatch):
    monkeypatch.setattr(co, "_free_ram_bytes", lambda: 100 * co._GB)
    monkeypatch.setattr(co.ComputeOrchestrator, "_native_report",
                        lambda self: {"model": "m.gguf", "reserved_bytes": 10 * co._GB})
    # comfy owned + running consumes its reserve.
    orch._comfy_owned = True
    orch._comfy_proc = FakeProc()
    expected = 100 * co._GB - 10 * co._GB - orch.comfy_reserve_bytes()
    assert orch.headroom_bytes() == expected
    status = orch.status()
    assert status["ok"] is True
    assert status["workloads"]["native_llm"]["reserved_bytes"] == 10 * co._GB
    assert status["workloads"]["comfyui"]["running"] is True
    assert status["memory"]["headroom_bytes"] == expected
    # can_afford respects the reserve.
    monkeypatch.setattr(co, "_free_ram_bytes", lambda: 1 * co._GB)
    assert orch.can_afford("fish") is False


def test_drain_completes_queued_job_when_idle(orch, monkeypatch, tmp_path):
    monkeypatch.setattr(co.ComputeOrchestrator, "ensure_comfyui", lambda self: True)
    monkeypatch.setattr(co.ComputeOrchestrator, "can_afford", lambda self, w: True)
    monkeypatch.setattr(co.ComputeOrchestrator, "_native_report",
                        lambda self: {"model": "", "reserved_bytes": 0})

    def fake_generate(job, out_path: Path):
        out_path.write_bytes(b"fake-png")
        return {"ok": True, "path": str(out_path)}

    orch._generate_fn = fake_generate
    _force_idle(orch)
    job_id = orch.queue_image_job("a red dragon over ujjain", width=512, height=512)
    orch.tick()
    thread = orch._drain_thread
    assert thread is not None, "idle + pending job should start a drain"
    thread.join(timeout=10.0)
    job = orch.job_status(job_id)
    assert job["status"] == "done"
    assert Path(job["path"]).read_bytes() == b"fake-png"
    assert job["url"].endswith(f"{job_id}.png")


def test_failed_job_records_error(orch, monkeypatch):
    monkeypatch.setattr(co.ComputeOrchestrator, "ensure_comfyui", lambda self: True)
    monkeypatch.setattr(co.ComputeOrchestrator, "can_afford", lambda self, w: True)
    orch._generate_fn = lambda job, out_path: {"ok": False, "error": "boom"}
    _force_idle(orch)
    job_id = orch.queue_image_job("anything")
    orch.tick()
    orch._drain_thread.join(timeout=10.0)
    job = orch.job_status(job_id)
    assert job["status"] == "failed"
    assert "boom" in job["error"]


def test_activity_stops_dequeue_and_suspends_owned_comfyui(orch, monkeypatch):
    kills = []
    monkeypatch.setattr(co, "_kill_process_tree", lambda proc: kills.append(proc))
    orch._comfy_owned = True
    orch._comfy_proc = FakeProc()
    orch._comfy_state = "owned"
    # Simulate an in-flight drain started before any external activity.
    orch._draining = True
    orch._current_job_id = "img_inflight"
    orch._drain_seq = orch._activity_seq  # drain began "now"
    # New chat activity arrives.
    orch.touch_activity()
    orch.tick()
    assert orch._suspend_requested is True
    assert orch._stop_dequeue.is_set()
    assert kills == [], "in-flight job must be allowed to finish first"
    # Job finishes; next tick suspends and kills ONLY because SHIMS owned it.
    orch._draining = False
    orch._current_job_id = None
    orch.tick()
    assert len(kills) == 1
    assert orch._comfy_state == "suspended"


def test_external_comfyui_never_killed(orch, monkeypatch):
    kills = []
    monkeypatch.setattr(co, "_kill_process_tree", lambda proc: kills.append(proc))
    orch._comfy_state = "external"
    orch._comfy_probe_ok = True
    orch._comfy_owned = False
    orch._suspend_requested = True
    orch._suspend_comfyui()
    assert kills == []
    assert orch._comfy_state == "suspended"


def test_fish_lifecycle_start_use_idle_stop(orch, monkeypatch):
    kills = []
    monkeypatch.setattr(co, "_kill_process_tree", lambda proc: kills.append(proc))
    monkeypatch.setattr(co.ComputeOrchestrator, "can_afford", lambda self, w: True)
    monkeypatch.setattr(co.ComputeOrchestrator, "_locate_fish",
                        lambda self: (Path("/fake/python.exe"), Path("/fake/fish")))
    monkeypatch.setattr(co.ComputeOrchestrator, "_open_log", lambda self, name: DummyLog())
    monkeypatch.setattr(co.ComputeOrchestrator, "_wait_ready", lambda self, prober, timeout, what: True)
    # Server not reachable at first → ensure_fish spawns it SHIMS-owned.
    monkeypatch.setattr(co.ComputeOrchestrator, "_fish_reachable", lambda self: False)
    assert orch.ensure_fish() is True
    assert orch._fish_owned is True
    assert orch._fish_state == "owned"
    assert orch._fish_last_used > 0
    # Use refreshes the idle timer.
    before = orch._fish_last_used
    orch._now = lambda: before + 10.0
    orch.fish_used()
    assert orch._fish_last_used == before + 10.0
    # Past the idle TTL, the owned server is stopped.
    orch._now = lambda: before + 10.0 + orch.fish_idle_threshold_s() + 1.0
    orch._maybe_idle_stop_fish()
    assert len(kills) == 1
    assert orch._fish_state == "stopped"


def test_fish_external_never_killed(orch, monkeypatch):
    kills = []
    monkeypatch.setattr(co, "_kill_process_tree", lambda proc: kills.append(proc))
    monkeypatch.setattr(co.ComputeOrchestrator, "_fish_reachable", lambda self: True)
    assert orch.ensure_fish() is True
    assert orch._fish_state == "external"
    orch.stop_fish()
    assert kills == [], "a server SHIMS did not start must never be killed"


def test_ensure_fish_unavailable_install(orch, monkeypatch):
    monkeypatch.setattr(co.ComputeOrchestrator, "_fish_reachable", lambda self: False)
    monkeypatch.setattr(co.ComputeOrchestrator, "can_afford", lambda self, w: True)
    monkeypatch.setattr(co.ComputeOrchestrator, "_locate_fish", lambda self: None)
    assert orch.ensure_fish() is False
    assert orch._fish_state == "unavailable"


def test_orchestrator_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app

    instance = co.ComputeOrchestrator(output_dir=tmp_path, popen=FakeProc)
    monkeypatch.setattr(co, "_ORCH", instance)
    c = TestClient(app)

    status = c.get("/api/orchestrator/status").json()
    assert status["ok"] is True
    assert "workloads" in status and "idle" in status and "queue" in status

    queued = c.post("/api/orchestrator/image-job",
                    json={"prompt": "test image", "width": 256, "height": 256}).json()
    assert queued["ok"] is True
    job = c.get(f"/api/orchestrator/image-job/{queued['job_id']}").json()
    assert job["ok"] is True
    assert job["job"]["status"] == "queued"

    missing = c.get("/api/orchestrator/image-job/img_nope").json()
    assert missing["ok"] is False
