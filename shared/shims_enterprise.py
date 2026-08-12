r"""Local-process control for the standalone SHIMS Enterprise app.

Provides start / stop / restart / status for the Enterprise FastAPI server that
lives in ``C:\d\shims_enterprise_local`` and serves on ``127.0.0.1:8020``.

This module is intentionally separate from ``shared/enterprise_bridge.py``:
- ``enterprise_bridge`` talks to a running Enterprise over HTTP.
- ``enterprise_control`` manages the Enterprise process itself.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from .bridge_auth import derived_bridge_token

_DEFAULT_PORT = 8020
_DEFAULT_HOST = "127.0.0.1"
_ENTERPRISE_ROOT = Path(os.getenv("SHIMS_ENTERPRISE_ROOT") or r"C:\d\shims_enterprise_local")
_START_TIMEOUT_S = float(os.getenv("SHIMS_ENTERPRISE_START_TIMEOUT_S", "60"))
_STOP_TIMEOUT_S = 15.0


def _enterprise_python() -> Path:
    """Prefer the Enterprise venv python, fall back to the SHIMS python."""
    candidates = [
        _ENTERPRISE_ROOT / ".venv" / "Scripts" / "python.exe",
        _ENTERPRISE_ROOT / "venv" / "Scripts" / "python.exe",
        Path(os.getenv("SHIMS_PYTHON") or ""),
        Path(shutil.which("python") or ""),
    ]
    for c in candidates:
        if c and c.is_file():
            return c
    raise FileNotFoundError(
        f"No Python found for Enterprise at {_ENTERPRISE_ROOT}; "
        "set SHIMS_ENTERPRISE_ROOT or SHIMS_PYTHON"
    )


def _enterprise_entrypoint() -> list[str]:
    """Return the argv prefix to launch Enterprise."""
    python = _enterprise_python()
    # Prefer the project's own starter if present.
    starter = _ENTERPRISE_ROOT / "start_enterprise.py"
    if starter.is_file():
        return [str(python), str(starter)]
    return [
        str(python), "-m", "uvicorn",
        "shims_enterprise.app:app",
        "--host", _DEFAULT_HOST,
        "--port", str(_DEFAULT_PORT),
        "--no-access-log",
    ]


def _find_enterprise_pids() -> list[int]:
    """Return PIDs of processes whose command line contains the Enterprise app."""
    pids: list[int] = []
    try:
        import psutil
    except Exception:
        return pids

    marker = "shims_enterprise.app:app"
    starter_marker = str(_ENTERPRISE_ROOT / "start_enterprise.py")
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = proc.info.get("cmdline") or []
            text = " ".join(str(x) for x in cmd)
            if marker in text or starter_marker in text:
                pids.append(proc.pid)
        except Exception:
            continue
    return pids


def enterprise_url() -> str:
    """Configured Enterprise base URL."""
    return (os.getenv("SHIMS_ENTERPRISE_URL") or f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}").rstrip("/")


def is_running() -> bool:
    """True if the Enterprise HTTP port answers healthily."""
    try:
        r = httpx.get(f"{enterprise_url()}/health", timeout=5)
        return r.status_code in (200, 401, 403)
    except Exception:
        return False


def status() -> dict[str, Any]:
    """JSON-serializable status snapshot."""
    pids = _find_enterprise_pids()
    return {
        "running": is_running(),
        "pids": pids,
        "url": enterprise_url(),
        "root": str(_ENTERPRISE_ROOT),
    }


def start(*, wait: bool = True, timeout: float | None = None) -> dict[str, Any]:
    """Start the Enterprise app if it is not already running."""
    if is_running():
        return {"ok": True, "started": False, "message": "Enterprise is already running", "status": status()}

    # Kill any stale orphaned Enterprise processes first.
    for pid in _find_enterprise_pids():
        try:
            import psutil
            psutil.Process(pid).kill()
        except Exception:
            pass

    env = {**os.environ}
    # Make sure the bridge token is available to the child process.
    env["SHIMS_BRIDGE_TOKEN"] = derived_bridge_token() or env.get("SHIMS_BRIDGE_TOKEN", "")
    env["ENTERPRISE_BRIDGE_TOKEN"] = env["SHIMS_BRIDGE_TOKEN"]

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )

    try:
        subprocess.Popen(
            _enterprise_entrypoint(),
            cwd=str(_ENTERPRISE_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as exc:
        return {"ok": False, "error": f"Failed to start Enterprise: {exc}", "status": status()}

    if wait:
        deadline = time.time() + (timeout or _START_TIMEOUT_S)
        while time.time() < deadline:
            if is_running():
                return {"ok": True, "started": True, "message": "Enterprise started", "status": status()}
            time.sleep(1.0)
        return {"ok": False, "error": "Enterprise did not become ready in time", "status": status()}

    return {"ok": True, "started": True, "message": "Enterprise launch initiated", "status": status()}


def stop(*, wait: bool = True, timeout: float | None = None) -> dict[str, Any]:
    """Stop the Enterprise app."""
    pids = _find_enterprise_pids()
    if not pids and not is_running():
        return {"ok": True, "stopped": False, "message": "Enterprise is not running", "status": status()}

    killed: list[int] = []
    for pid in pids:
        try:
            import psutil
            proc = psutil.Process(pid)
            proc.terminate()
            killed.append(pid)
        except Exception:
            pass

    if wait:
        deadline = time.time() + (timeout or _STOP_TIMEOUT_S)
        while time.time() < deadline:
            if not _find_enterprise_pids():
                break
            time.sleep(0.5)
        # Force-kill stragglers.
        for pid in _find_enterprise_pids():
            try:
                import psutil
                psutil.Process(pid).kill()
                killed.append(pid)
            except Exception:
                pass

    return {"ok": True, "stopped": True, "pids": killed, "status": status()}


def restart(*, wait: bool = True) -> dict[str, Any]:
    """Restart Enterprise."""
    stop(wait=wait)
    return start(wait=wait)
