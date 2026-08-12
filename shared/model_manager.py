"""Model Manager — browse and download GGUF models from Hugging Face.

Talks to the public HF HTTP API with plain ``requests`` (already a project
dependency); no ``huggingface_hub`` and no CLI required. Downloads stream to
``storage/models/<filename>.part`` and are atomically renamed on completion.

Download jobs run on a single daemon worker thread (concurrency cap of 1;
extra requests queue). Progress lives in a module-level jobs dict polled by
the UI via ``GET /api/models/downloads``. Cancellation is cooperative: a
threading.Event is checked between chunks and the ``.part`` file is removed.

``HF_TOKEN`` (env) is sent as a bearer token when present, for gated repos.
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from shared.config import ROOT_DIR, STORAGE_DIR

def _default_download_dir() -> Path:
    """Where new GGUFs land.

    Defaults into the LM Studio library when one exists, so native and LM Studio
    share a single copy of every model — the user's existing models are already
    there, and a second copy of a 40 GB GGUF is not free. Downloads go in a
    ``shims/`` subdir rather than the library root: it keeps provenance obvious,
    cannot collide with LM Studio's ``publisher/repo/`` tree, and — because
    ``managed_dirs()`` is what the delete endpoint trusts — means SHIMS may
    delete what it downloaded without ever being able to delete the user's
    pre-existing LM Studio models.

    Override with ``SHIMS_MODEL_DOWNLOAD_DIR``.
    """
    override = (os.getenv("SHIMS_MODEL_DOWNLOAD_DIR") or "").strip()
    if override:
        return Path(override)
    lmstudio = Path.home() / ".lmstudio" / "models"
    if lmstudio.is_dir():
        return lmstudio / "shims"
    return STORAGE_DIR / "models"


# Where downloads land and which dirs the delete endpoint is allowed to touch.
# Module-level so tests can monkeypatch them to tmp dirs.
DOWNLOAD_DIR = _default_download_dir()
DATA_MODELS_DIR = ROOT_DIR / "data" / "models"

_HF_API = "https://huggingface.co/api/models"
_HTTP_TIMEOUT = 10  # seconds, per spec
_CHUNK = 1 << 20    # 1 MiB stream chunks

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_work: "queue.Queue[str]" = queue.Queue()
_worker_started = False


def managed_dirs() -> list[Path]:
    """Dirs the delete endpoint may remove GGUFs from.

    Only SHIMS-owned roots. When ``DOWNLOAD_DIR`` points inside an LM Studio
    library it is the ``shims/`` subdir, so the user's pre-existing models stay
    outside every path listed here and cannot be deleted through SHIMS.
    """
    return [Path(DOWNLOAD_DIR), Path(DATA_MODELS_DIR)]


def _free_disk_bytes(path: Path) -> int:
    """Free bytes on the volume holding ``path`` (walking up to an existing
    parent, since the target dir may not be created yet). 0 when unknown."""
    import shutil

    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except Exception:
        return 0


_QUANT_RE = re.compile(
    r"(?:^|[-_.])((?:UD[-_])?(?:[IT]?Q\d+(?:_[A-Z0-9]+)*|BF16|F16|F32))(?:$|[-_.])",
    re.IGNORECASE,
)


def quant_label(filename: str) -> str:
    """Best-effort quantisation tag (``Q4_K_M``, ``UD-IQ2_XXS``, ``BF16``, ...).

    Matched by pattern, not a fixed list — publishers keep inventing variants
    (unsloth's ``UD-`` dynamic quants, ``IQ1_S``, ternary ``TQ1_0``), and a
    blank tag makes the picker much harder to read when a repo ships 28 files.
    """
    match = _QUANT_RE.search(PurePosixPath(filename).stem)
    return match.group(1).upper() if match else ""


def _auth_headers() -> dict[str, str]:
    token = (os.getenv("HF_TOKEN") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _get_json(url: str, *, timeout: int = _HTTP_TIMEOUT) -> Any:
    """Thin requests wrapper — the single network point tests monkeypatch."""
    import requests
    r = requests.get(url, headers=_auth_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def _open_stream(url: str):
    """Open a streaming GET for a file download; tests monkeypatch this."""
    import requests
    r = requests.get(url, headers=_auth_headers(), stream=True, timeout=60)
    r.raise_for_status()
    return r


# --------------------------------------------------------------------- #
# Hugging Face browse
# --------------------------------------------------------------------- #

def search_hf_models(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search HF for GGUF repos, sorted by downloads. [] on any failure."""
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit or 20), 50))
    try:
        data = _get_json(
            f"{_HF_API}?search={query}&filter=gguf&sort=downloads&direction=-1&limit={limit}"
        )
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for m in data or []:
        out.append({
            "repo_id": m.get("id") or m.get("modelId") or "",
            "downloads": int(m.get("downloads") or 0),
            "likes": int(m.get("likes") or 0),
            "tags": [t for t in (m.get("tags") or []) if isinstance(t, str)][:12],
        })
    return [m for m in out if m["repo_id"]]


def list_hf_gguf_files(repo_id: str) -> dict[str, Any]:
    """List *.gguf siblings of a repo. ``mmproj-*`` vision projectors are
    split out into ``projectors`` instead of the main ``files`` list."""
    repo_id = (repo_id or "").strip().strip("/")
    if not repo_id:
        return {"ok": False, "files": [], "projectors": [], "error": "repo id required"}
    try:
        data = _get_json(f"{_HF_API}/{repo_id}?blobs=true")
    except Exception as exc:
        return {"ok": False, "files": [], "projectors": [], "error": str(exc)[:200]}
    files: list[dict[str, Any]] = []
    projectors: list[dict[str, Any]] = []
    for s in (data or {}).get("siblings") or []:
        name = PurePosixPath(s.get("rfilename") or "").name
        if not name.lower().endswith(".gguf"):
            continue
        entry = {"filename": name, "size_bytes": int(s.get("size") or 0),
                 "quant": quant_label(name)}
        if name.lower().startswith("mmproj"):
            projectors.append(entry)
        else:
            files.append(entry)
    files.sort(key=lambda f: f["size_bytes"])
    projectors.sort(key=lambda f: f["size_bytes"])
    return {"ok": True, "files": files, "projectors": projectors}


# --------------------------------------------------------------------- #
# Download jobs
# --------------------------------------------------------------------- #

def _new_job(repo_id: str, filename: str) -> dict[str, Any]:
    return {
        "job_id": uuid.uuid4().hex[:12],
        "repo": repo_id,
        "filename": filename,
        "bytes_done": 0,
        "bytes_total": 0,
        "pct": 0.0,
        "speed_bps": 0.0,
        "state": "queued",  # queued|downloading|done|error|cancelled
        "error": "",
        "created_at": time.time(),
        "cancel_event": threading.Event(),
    }


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in job.items() if k != "cancel_event"}


def _ensure_worker() -> None:
    global _worker_started
    with _jobs_lock:
        if _worker_started:
            return
        _worker_started = True
    t = threading.Thread(target=_worker_loop, name="hf-model-downloader", daemon=True)
    t.start()


def _worker_loop() -> None:
    """Single worker => at most one active download; others stay queued."""
    while True:
        job_id = _work.get()
        with _jobs_lock:
            job = _jobs.get(job_id)
        if job is None:
            continue
        try:
            download_hf_model(job["repo"], job["filename"], job_id)
        except Exception as exc:  # last-resort guard; download_hf_model self-reports
            job["state"] = "error"
            job["error"] = str(exc)[:300]


def start_download(repo_id: str, filename: str) -> dict[str, Any]:
    """Queue a download. Returns {ok, job_id} or {ok: False, error}."""
    repo_id = (repo_id or "").strip().strip("/")
    filename = PurePosixPath(filename or "").name  # strip any path parts
    if not repo_id or not filename.lower().endswith(".gguf"):
        return {"ok": False, "error": "repo_id and a .gguf filename are required"}
    with _jobs_lock:
        for j in _jobs.values():
            if (j["repo"] == repo_id and j["filename"] == filename
                    and j["state"] in ("queued", "downloading")):
                return {"ok": True, "job_id": j["job_id"], "already": True}
        job = _new_job(repo_id, filename)
        _jobs[job["job_id"]] = job
    _ensure_worker()
    _work.put(job["job_id"])
    return {"ok": True, "job_id": job["job_id"]}


class _Cancelled(Exception):
    pass


def download_hf_model(repo_id: str, filename: str, job_id: str) -> dict[str, Any]:
    """Synchronous download body — runs on the worker thread.

    Streams to ``DOWNLOAD_DIR/<filename>.part`` then atomically renames.
    Updates the job dict in place; never raises for expected failures.
    """
    job = _jobs[job_id]
    target_dir = Path(DOWNLOAD_DIR)
    part = target_dir / (job["filename"] + ".part")
    final = target_dir / job["filename"]
    url = f"https://huggingface.co/{repo_id}/resolve/main/{job['filename']}"
    try:
        if job["cancel_event"].is_set():
            raise _Cancelled()
        target_dir.mkdir(parents=True, exist_ok=True)
        resp = _open_stream(url)
        try:
            total = int(resp.headers.get("Content-Length") or 0)
            # Fail fast on a model that cannot fit. These files run to tens of
            # GB; discovering the shortfall at 99% wastes the whole transfer and
            # leaves a large .part behind.
            if total:
                free = _free_disk_bytes(target_dir)
                if free and free < total:
                    raise RuntimeError(
                        f"not enough free space: need {total / 1e9:.1f} GB, "
                        f"{free / 1e9:.1f} GB free on {target_dir}"
                    )
            with _jobs_lock:
                job["bytes_total"] = total
                job["state"] = "downloading"
            started = time.time()
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=_CHUNK):
                    if job["cancel_event"].is_set():
                        raise _Cancelled()
                    if not chunk:
                        continue
                    fh.write(chunk)
                    elapsed = time.time() - started
                    with _jobs_lock:
                        job["bytes_done"] += len(chunk)
                        job["speed_bps"] = job["bytes_done"] / elapsed if elapsed > 0 else 0.0
                        job["pct"] = round(100.0 * job["bytes_done"] / total, 1) if total else 0.0
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                close()
        if job["cancel_event"].is_set():
            raise _Cancelled()
        os.replace(part, final)
        with _jobs_lock:
            job["state"] = "done"
            job["pct"] = 100.0
            if job["bytes_total"] == 0:
                job["bytes_total"] = job["bytes_done"]
    except _Cancelled:
        with _jobs_lock:
            job["state"] = "cancelled"
        part.unlink(missing_ok=True)
    except Exception as exc:
        with _jobs_lock:
            job["state"] = "error"
            job["error"] = str(exc)[:300]
        part.unlink(missing_ok=True)
    return _public_job(job)


def list_jobs() -> list[dict[str, Any]]:
    """All job states, newest first (for UI polling)."""
    with _jobs_lock:
        jobs = [_public_job(j) for j in _jobs.values()]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs


def cancel_job(job_id: str) -> dict[str, Any]:
    """Cooperatively cancel a queued/downloading job (deletes the .part)."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "unknown job_id"}
        if job["state"] in ("done", "error", "cancelled"):
            return {"ok": True, "state": job["state"]}
        job["cancel_event"].set()
        if job["state"] == "queued":
            job["state"] = "cancelled"
    return {"ok": True, "state": "cancelling"}


# --------------------------------------------------------------------- #
# Local models + safe delete
# --------------------------------------------------------------------- #

def is_managed_path(path: str | Path) -> bool:
    """True only for paths inside storage/models or data/models."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    for root in managed_dirs():
        try:
            if resolved.is_relative_to(root.resolve()):
                return True
        except OSError:
            continue
    return False


def delete_local_model(path: str) -> dict[str, Any]:
    """Delete a GGUF from disk — only inside the SHIMS-managed dirs and only
    when it is not the currently loaded native-engine model."""
    if not path or not str(path).lower().endswith(".gguf"):
        return {"ok": False, "error": "a .gguf path is required"}
    if not is_managed_path(path):
        return {"ok": False, "error": "refusing to delete outside storage/models or data/models"}
    try:
        resolved = Path(path).resolve()
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    try:
        from shared.native_engine import get_engine
        loaded_path = (get_engine().health().get("model_path") or "").strip()
        if loaded_path and Path(loaded_path).resolve() == resolved:
            return {"ok": False, "error": "model is currently loaded — unload it first"}
    except Exception:
        pass  # engine unavailable => nothing loaded from this process
    try:
        resolved.unlink()
    except FileNotFoundError:
        return {"ok": False, "error": "file not found"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "deleted": str(resolved)}


def local_models() -> dict[str, Any]:
    """Discovered GGUFs annotated with loaded flag, size, and whether the
    file lives in a SHIMS-managed dir (deletable) vs e.g. LM Studio."""
    from shared.native_engine import get_engine
    engine = get_engine()
    models = engine.models()
    for m in models:
        m["size_gb"] = round(m.get("size_bytes", 0) / (1024 ** 3), 2)
        m["managed"] = is_managed_path(m.get("path", ""))
        m.pop("source_dir", None)
    return {"ok": True, "models": models, "loaded": engine.loaded_model_id()}
