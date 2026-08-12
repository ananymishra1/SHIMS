"""Ranged parallel downloader for the Qwen3-235B-A22B Q4_K_M parts.

Per-connection throughput to HF's CDN is throttled (~1-4 MB/s), so this
splits each part into byte-range chunks and fetches many ranges concurrently.
Chunks are written at their file offset (no 2x disk usage). Resume-aware: any
byte prefix already downloaded by earlier curl runs is kept, and completed
chunks are skipped on restart.
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

REPO = "lmstudio-community/Qwen3-235B-A22B-GGUF"
BASE = f"https://huggingface.co/{REPO}/resolve/main"
FILES = [
    ("Qwen3-235B-A22B-Q4_K_M-00001-of-00004.gguf", 39856336608),
    ("Qwen3-235B-A22B-Q4_K_M-00002-of-00004.gguf", 39847230560),
    ("Qwen3-235B-A22B-Q4_K_M-00003-of-00004.gguf", 39847230560),
    ("Qwen3-235B-A22B-Q4_K_M-00004-of-00004.gguf", 23096107744),
]
DEST = Path.home() / ".lmstudio" / "models" / "shims" / "Qwen3-235B-A22B-Q4_K_M"
CHUNK = 64 * 1024 * 1024  # 64 MB ranges
WORKERS = 12
TOKEN = os.environ.get("HF_TOKEN", "").strip()

_started = time.time()
_done_bytes = 0
import threading
_lock = threading.Lock()


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


def fetch_range(path: Path, start: int, end: int, retries: int = 6) -> int:
    """Download bytes [start, end] of `url` into `path` at `start`. Returns byte count."""
    url = f"{BASE}/{path.name}"
    want = end - start + 1
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True) as client:
                with client.stream("GET", url, headers={**_headers(), "Range": f"bytes={start}-{end}"}) as r:
                    r.raise_for_status()
                    buf = bytearray()
                    for data in r.iter_bytes(1024 * 1024):
                        buf.extend(data)
                    if len(buf) != want:
                        raise IOError(f"short read {len(buf)} != {want}")
            with _lock:
                with path.open("r+b") as fh:
                    fh.seek(start)
                    fh.write(buf)
                global _done_bytes
                _done_bytes += want
                mb = _done_bytes / 1e6
                rate = mb / max(1, time.time() - _started)
                print(f"[{time.strftime('%H:%M:%S')}] {path.name} @{start//CHUNK} ok  ({rate:.1f} MB/s sess)", flush=True)
            return want
        except Exception as exc:
            wait = min(60, 5 * attempt)
            print(f"[{time.strftime('%H:%M:%S')}] {path.name} @{start//CHUNK} retry {attempt}: {str(exc)[:100]}", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"chunk failed permanently: {path.name} @{start}")


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[Path, int, int]] = []
    for name, total in FILES:
        path = DEST / name
        if path.exists() and path.stat().st_size == total:
            print(f"{name} already complete", flush=True)
            continue
        # Preallocate the file; any existing prefix (earlier curl bytes) is kept.
        existing = path.stat().st_size if path.exists() else 0
        with path.open("ab") as fh:
            fh.truncate(total)
        prefix_chunks = existing // CHUNK  # complete 64MB chunks already on disk
        for start in range(prefix_chunks * CHUNK, total, CHUNK):
            end = min(start + CHUNK - 1, total - 1)
            tasks.append((path, start, end))
        print(f"{name}: {existing/1e9:.1f}GB prefix kept, {total/1e9:.1f}GB total, {len(tasks)} chunks queued", flush=True)

    if not tasks:
        print("ALL FILES COMPLETE", flush=True)
        return 0

    failed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(fetch_range, p, s, e): (p, s) for p, s, e in tasks}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as exc:
                failed += 1
                print(f"CHUNK LOST: {exc}", flush=True)
    total_gb = sum(p.stat().st_size for p, _, _ in [(DEST / n, 0, 0) for n, _ in FILES]) / 1e9
    print(f"{'FAILED' if failed else 'ALL DONE'} — {total_gb:.1f} GB on disk in {(time.time()-_started)/60:.0f} min", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
