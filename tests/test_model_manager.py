"""Tests for shared/model_manager.py — HF search/list parsing, download job
lifecycle, cancellation, and the safe-delete endpoint. All network access is
monkeypatched; no real HF calls and no real downloads happen."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import shared.model_manager as mm


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #

class FakeStreamResponse:
    """Stand-in for a requests streaming response."""

    def __init__(self, chunks: list[bytes], total: int | None = None, delay: float = 0.0):
        self._chunks = chunks
        self.headers = {"Content-Length": str(total if total is not None else sum(map(len, chunks)))}
        self._delay = delay
        self.closed = False

    def iter_content(self, chunk_size: int = 1 << 20):
        for c in self._chunks:
            if self._delay:
                time.sleep(self._delay)
            yield c

    def close(self):
        self.closed = True


def _wait_for_state(job_id: str, states: set[str], timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = next((j for j in mm.list_jobs() if j["job_id"] == job_id), None)
        if job and job["state"] in states:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached {states}; jobs={mm.list_jobs()}")


@pytest.fixture()
def dl_dir(tmp_path, monkeypatch):
    """Redirect downloads into a tmp dir."""
    target = tmp_path / "storage" / "models"
    monkeypatch.setattr(mm, "DOWNLOAD_DIR", target)
    return target


# --------------------------------------------------------------------- #
# Search parsing
# --------------------------------------------------------------------- #

def test_search_parses_canned_response(monkeypatch):
    canned = [
        {"id": "TheBloke/Llama-2-7B-GGUF", "downloads": 12345, "likes": 67,
         "tags": ["gguf", "llama", "text-generation"]},
        {"modelId": "QuantFactory/Qwen2.5-7B-GGUF", "downloads": 500, "likes": 3, "tags": []},
        {"id": "", "downloads": 1},  # no repo id — must be dropped
    ]
    monkeypatch.setattr(mm, "_get_json", lambda url, **kw: canned)
    out = mm.search_hf_models("llama", limit=10)
    assert [m["repo_id"] for m in out] == ["TheBloke/Llama-2-7B-GGUF", "QuantFactory/Qwen2.5-7B-GGUF"]
    assert out[0]["downloads"] == 12345
    assert out[0]["likes"] == 67
    assert "gguf" in out[0]["tags"]


def test_search_returns_empty_on_failure(monkeypatch):
    def boom(url, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(mm, "_get_json", boom)
    assert mm.search_hf_models("anything") == []
    assert mm.search_hf_models("") == []


# --------------------------------------------------------------------- #
# File listing
# --------------------------------------------------------------------- #

def test_list_files_filters_non_gguf_and_flags_mmproj(monkeypatch):
    canned = {
        "siblings": [
            {"rfilename": "README.md", "size": 100},
            {"rfilename": "model-q4_k_m.gguf", "size": 4_000_000_000},
            {"rfilename": "model-q8_0.gguf", "size": 8_000_000_000},
            {"rfilename": "mmproj-model-f16.gguf", "size": 900_000_000},
            {"rfilename": "tokenizer.json"},
        ]
    }
    monkeypatch.setattr(mm, "_get_json", lambda url, **kw: canned)
    out = mm.list_hf_gguf_files("some/repo")
    assert out["ok"] is True
    names = [f["filename"] for f in out["files"]]
    assert names == ["model-q4_k_m.gguf", "model-q8_0.gguf"]  # sorted by size
    assert out["files"][0]["size_bytes"] == 4_000_000_000
    assert [p["filename"] for p in out["projectors"]] == ["mmproj-model-f16.gguf"]


def test_list_files_handles_failure(monkeypatch):
    def boom(url, **kw):
        raise RuntimeError("nope")
    monkeypatch.setattr(mm, "_get_json", boom)
    out = mm.list_hf_gguf_files("some/repo")
    assert out["ok"] is False and out["files"] == [] and out["error"]


# --------------------------------------------------------------------- #
# Download lifecycle
# --------------------------------------------------------------------- #

def test_download_job_runs_to_done(dl_dir, monkeypatch):
    payload = b"GGUF-fake-bytes-" * 1000
    chunks = [payload[i:i + 4096] for i in range(0, len(payload), 4096)]
    monkeypatch.setattr(mm, "_open_stream", lambda url: FakeStreamResponse(chunks))

    res = mm.start_download("some/repo", "done-model.gguf")
    assert res["ok"] and res["job_id"]
    job = _wait_for_state(res["job_id"], {"done", "error"})

    assert job["state"] == "done", job.get("error")
    assert job["bytes_done"] == len(payload)
    assert job["bytes_total"] == len(payload)
    assert job["pct"] == 100.0
    final = dl_dir / "done-model.gguf"
    assert final.read_bytes() == payload          # streamed content landed
    assert not (dl_dir / "done-model.gguf.part").exists()  # .part renamed away


def test_download_progress_updates(dl_dir, monkeypatch):
    chunks = [b"x" * 4096] * 8
    monkeypatch.setattr(mm, "_open_stream", lambda url: FakeStreamResponse(chunks, delay=0.01))
    res = mm.start_download("some/repo", "progress-model.gguf")
    job = _wait_for_state(res["job_id"], {"done", "error"})
    assert job["state"] == "done"
    assert job["speed_bps"] > 0
    assert job["bytes_done"] == 8 * 4096


def test_download_cancel_deletes_part(dl_dir, monkeypatch):
    # Endless-ish slow chunk stream so we can cancel mid-flight.
    chunks = [b"y" * 1024] * 100_000
    monkeypatch.setattr(mm, "_open_stream", lambda url: FakeStreamResponse(chunks, delay=0.002))
    res = mm.start_download("some/repo", "cancel-model.gguf")
    _wait_for_state(res["job_id"], {"downloading"})

    cancel = mm.cancel_job(res["job_id"])
    assert cancel["ok"]
    job = _wait_for_state(res["job_id"], {"cancelled", "error"})
    assert job["state"] == "cancelled"
    assert not (dl_dir / "cancel-model.gguf.part").exists()   # partial file removed
    assert not (dl_dir / "cancel-model.gguf").exists()


def test_download_error_state(dl_dir, monkeypatch):
    def boom(url):
        raise RuntimeError("HTTP 404")
    monkeypatch.setattr(mm, "_open_stream", boom)
    res = mm.start_download("some/repo", "missing-model.gguf")
    job = _wait_for_state(res["job_id"], {"error"})
    assert "404" in job["error"]
    assert not (dl_dir / "missing-model.gguf.part").exists()


def test_start_download_rejects_bad_input(dl_dir):
    assert mm.start_download("", "x.gguf")["ok"] is False
    assert mm.start_download("a/b", "not-a-model.bin")["ok"] is False
    # path traversal in filename is stripped to the basename
    res = mm.start_download("a/b", "../evil.gguf")
    assert res["ok"] is True
    job = next(j for j in mm.list_jobs() if j["job_id"] == res["job_id"])
    assert job["filename"] == "evil.gguf"
    mm.cancel_job(res["job_id"])


# --------------------------------------------------------------------- #
# Safe delete
# --------------------------------------------------------------------- #

def test_is_managed_path(tmp_path, monkeypatch):
    managed = tmp_path / "storage" / "models"
    managed_data = tmp_path / "data" / "models"
    other = tmp_path / ".lmstudio" / "models"
    for d in (managed, managed_data, other):
        d.mkdir(parents=True)
    monkeypatch.setattr(mm, "DOWNLOAD_DIR", managed)
    monkeypatch.setattr(mm, "DATA_MODELS_DIR", managed_data)

    assert mm.is_managed_path(managed / "a.gguf")
    assert mm.is_managed_path(managed_data / "nested" / "b.gguf")
    assert not mm.is_managed_path(other / "c.gguf")
    assert not mm.is_managed_path(managed / ".." / "escape.gguf")


def test_delete_endpoint_containment(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app

    managed = tmp_path / "storage" / "models"
    managed.mkdir(parents=True)
    outside = tmp_path / "lmstudio"
    outside.mkdir()
    keep = managed / "keep.gguf"
    keep.write_bytes(b"gguf")
    forbidden = outside / "forbidden.gguf"
    forbidden.write_bytes(b"gguf")
    monkeypatch.setattr(mm, "DOWNLOAD_DIR", managed)
    monkeypatch.setattr(mm, "DATA_MODELS_DIR", tmp_path / "data" / "models")

    c = TestClient(app)
    # Inside storage/models => deleted.
    r = c.delete("/api/models/local", params={"path": str(keep)})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert not keep.exists()
    # Outside managed dirs => refused, file untouched.
    r = c.delete("/api/models/local", params={"path": str(forbidden)})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert forbidden.exists()
    # Traversal out of the managed dir => refused.
    r = c.delete("/api/models/local", params={"path": str(managed / ".." / ".." / "lmstudio" / "forbidden.gguf")})
    assert r.json()["ok"] is False
    assert forbidden.exists()


def test_delete_local_model_refuses_non_gguf(tmp_path, monkeypatch):
    managed = tmp_path / "storage" / "models"
    managed.mkdir(parents=True)
    monkeypatch.setattr(mm, "DOWNLOAD_DIR", managed)
    monkeypatch.setattr(mm, "DATA_MODELS_DIR", tmp_path / "data" / "models")
    assert mm.delete_local_model(str(managed / "notes.txt"))["ok"] is False
    assert mm.delete_local_model("")["ok"] is False
