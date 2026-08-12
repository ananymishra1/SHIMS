r"""Local control for the SHIMS Director Cockpit owner dashboard.

The Director Cockpit is a static HTML/JS dashboard at ``C:\SHIMS\director-cockpit``.
It has no server of its own; this module opens it and regenerates its
``data/snapshot.json`` by calling ``refresh_snapshot.py``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Any

_DEFAULT_COCKPIT_ROOT = Path(os.getenv("SHIMS_DIRECTOR_COCKPIT_ROOT") or r"C:\SHIMS\director-cockpit")
_SNAPSHOT_PATH = _DEFAULT_COCKPIT_ROOT / "data" / "snapshot.json"
_REFRESH_TIMEOUT_S = 120.0


def cockpit_root() -> Path:
    return Path(os.getenv("SHIMS_DIRECTOR_COCKPIT_ROOT") or _DEFAULT_COCKPIT_ROOT)


def _python() -> Path:
    """Find a usable Python interpreter."""
    candidates = [
        Path(os.getenv("SHIMS_PYTHON") or ""),
        Path(os.getenv("SHIMS_ENTERPRISE_ROOT") or r"C:\d\SHIMS") / ".venv" / "Scripts" / "python.exe",
        Path(r"C:\Python314\python.exe"),
        Path(r"C:\Python312\python.exe"),
        Path(r"C:\Python311\python.exe"),
        Path(os.getenv("LOCALAPPDATA") or "") / "Programs" / "Python" / "Python312" / "python.exe",
        Path(shutil.which("python") or "") if __import__("shutil").which("python") else Path(),
    ]
    for c in candidates:
        if c and c.is_file():
            return c
    raise FileNotFoundError("No Python found to run Director Cockpit refresh")


def status() -> dict[str, Any]:
    """JSON-serializable cockpit status."""
    snapshot_exists = _SNAPSHOT_PATH.is_file()
    snapshot_age_s: float | None = None
    if snapshot_exists:
        snapshot_age_s = time.time() - _SNAPSHOT_PATH.stat().st_mtime

    return {
        "cockpit_root": str(cockpit_root()),
        "snapshot_exists": snapshot_exists,
        "snapshot_path": str(_SNAPSHOT_PATH),
        "snapshot_age_seconds": round(snapshot_age_s, 1) if snapshot_age_s is not None else None,
        "snapshot_age_human": _human_age(snapshot_age_s) if snapshot_age_s is not None else None,
    }


def _human_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    return f"{int(seconds / 3600)}h ago"


def open_cockpit() -> dict[str, Any]:
    """Open the Director Cockpit in the default browser."""
    index = cockpit_root() / "index.html"
    if not index.is_file():
        return {"ok": False, "error": f"Director Cockpit index not found: {index}"}
    url = index.as_uri()
    try:
        webbrowser.open(url)
        return {"ok": True, "opened": True, "url": url}
    except Exception as exc:
        return {"ok": False, "error": f"Could not open browser: {exc}", "url": url}


def refresh_snapshot(*, timeout: float | None = None) -> dict[str, Any]:
    """Regenerate ``data/snapshot.json`` by running ``refresh_snapshot.py``."""
    root = cockpit_root()
    refresh_script = root / "refresh_snapshot.py"
    if not refresh_script.is_file():
        return {"ok": False, "error": f"refresh_snapshot.py not found: {refresh_script}"}

    env = {**os.environ}
    # Make sure the refresh script can import SHIMS shared modules.
    shims_root = Path(os.getenv("SHIMS_ROOT") or r"C:\d\SHIMS")
    if str(shims_root) not in (env.get("PYTHONPATH") or ""):
        env["PYTHONPATH"] = f"{shims_root}" + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            [str(_python()), str(refresh_script)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout or _REFRESH_TIMEOUT_S,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": "refresh_snapshot.py timed out", "stdout": exc.stdout or "", "stderr": exc.stderr or ""}
    except Exception as exc:
        return {"ok": False, "error": f"Failed to run refresh_snapshot.py: {exc}"}

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": "refresh_snapshot.py exited with errors",
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    return {
        "ok": True,
        "refreshed": True,
        "status": status(),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
