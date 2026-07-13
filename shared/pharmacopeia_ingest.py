from __future__ import annotations

import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .enterprise_bmr_corpus import import_bmr_folder


def _find_usp_root(folder: Path) -> Path | None:
    """Find the USP folder by pattern; its name may contain an en-dash."""
    for child in folder.iterdir():
        if child.is_dir() and "united states pharmacopeial" in child.name.lower():
            return child
    return None


def collect_pharmacopeia_pdfs(folder: Path, subset: str | None) -> list[Path]:
    """Collect PDFs from a pharmacopeia folder structure.

    Subsets:
        ip             -> INDIAN PHARMACOPOEIA (2022) VOL-*.pdf
        usp-general    -> USP 2024/General/*.pdf
        usp-monographs -> USP 2024/Monographs/*.pdf
        (none)         -> all PDFs recursively
    """
    subset = (subset or "").lower()
    if subset == "ip":
        return sorted(
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() == ".pdf" and "INDIAN PHARMACOPOEIA" in p.name.upper()
        )

    usp_root = _find_usp_root(folder)
    if subset == "usp-general":
        if not usp_root:
            return []
        sub = usp_root / "USP 2024" / "General"
        return sorted(p for p in sub.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf") if sub.exists() else []
    if subset == "usp-monographs":
        if not usp_root:
            return []
        sub = usp_root / "USP 2024" / "Monographs"
        return sorted(p for p in sub.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf") if sub.exists() else []

    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")


# In-memory job registry. Survives for the lifetime of the process.
_ingest_jobs: dict[str, dict[str, Any]] = {}


def list_jobs() -> list[dict[str, Any]]:
    return sorted(
        [{"job_id": k, **{key: v for key, v in v.items() if key != "result"}} for k, v in _ingest_jobs.items()],
        key=lambda x: x.get("started_at", 0),
        reverse=True,
    )


def get_job(job_id: str) -> dict[str, Any] | None:
    return _ingest_jobs.get(job_id)


def run_pharmacopeia_ingest(
    source_path: str | Path,
    subset: str | None = None,
    limit: int | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Run pharmacopeia ingestion synchronously and return the result.

    This is intentionally blocking; callers that need async behavior should run it
    in a thread (e.g. asyncio.to_thread).
    """
    source = Path(source_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Source folder not found: {source}")

    pdfs = collect_pharmacopeia_pdfs(source, subset)
    if limit:
        pdfs = pdfs[: max(0, int(limit))]

    if not pdfs:
        return {"ok": True, "imported": 0, "errors": [], "message": "no PDFs found"}

    staging = Path(tempfile.mkdtemp(prefix="shims_pharmacopeia_"))

    try:
        for p in pdfs:
            dest = staging / p.name
            counter = 1
            while dest.exists():
                dest = staging / f"{p.stem}_{counter:03d}{p.suffix}"
                counter += 1
            shutil.copy2(p, dest)

        result = import_bmr_folder(str(staging), user_id=user_id)
        return {"ok": True, **result}
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def start_pharmacopeia_ingest(
    source_path: str | Path,
    subset: str | None = None,
    limit: int | None = None,
    user_id: int | None = None,
) -> str:
    """Start a background ingestion job and return its job ID."""
    job_id = str(uuid.uuid4())
    _ingest_jobs[job_id] = {
        "status": "running",
        "subset": subset,
        "source": str(Path(source_path).expanduser().resolve()),
        "limit": limit,
        "user_id": user_id,
        "started_at": time.time(),
        "finished_at": None,
        "result": None,
    }

    def _run() -> None:
        try:
            result = run_pharmacopeia_ingest(source_path, subset=subset, limit=limit, user_id=user_id)
            _ingest_jobs[job_id]["status"] = "completed"
            _ingest_jobs[job_id]["result"] = result
        except Exception as exc:
            _ingest_jobs[job_id]["status"] = "failed"
            _ingest_jobs[job_id]["result"] = {"ok": False, "error": str(exc)}
        finally:
            _ingest_jobs[job_id]["finished_at"] = time.time()

    import threading
    threading.Thread(target=_run, daemon=True).start()
    return job_id
