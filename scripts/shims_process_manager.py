"""Process manager for the two SHIMS instances and Ollama.

Starts, monitors, and restarts:
  - Ollama server (configurable port, default 11435 if 11434 is busy/stuck)
  - Instance A Omni (port 8010)
  - Instance A Enterprise (port 8020)
  - Instance B Local Factory (port 8030)

Usage:
    .venv/Scripts/python scripts/shims_process_manager.py start
    .venv/Scripts/python scripts/shims_process_manager.py stop
    .venv/Scripts/python scripts/shims_process_manager.py status
    .venv/Scripts/python scripts/shims_process_manager.py restart
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "storage" / "process_manager"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "pids.json"

OLLAMA_EXE = Path(os.getenv("OLLAMA_EXE", "C:/Users/alapm/AppData/Local/Programs/Ollama/ollama.exe"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1:11435")
OLLAMA_MODELS = os.getenv("OLLAMA_MODELS", "E:/ollama/models")
PEER_TOKEN = os.getenv("INTER_INSTANCE_TOKEN", "local-factory-shared-token-2026")


def _port_from_url(url: str) -> int | None:
    try:
        return int(url.split(":")[-1].split("/")[0])
    except Exception:
        return None


def _find_listening_pid(port: int) -> int | None:
    """Find the process currently listening on the given TCP port."""
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                return conn.pid
    except Exception:
        pass
    return None

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

SERVICES = {
    "ollama": {
        "cmd": [str(OLLAMA_EXE), "serve"],
        "env": {"OLLAMA_HOST": OLLAMA_HOST, "OLLAMA_MODELS": OLLAMA_MODELS},
        "health": f"http://{OLLAMA_HOST}/api/tags",
    },
    "omni_a": {
        "cmd": [
            str(PYTHON), "-u", "-m", "uvicorn", "backend.app.main:app",
            "--host", "127.0.0.1", "--port", "8010", "--no-access-log",
        ],
        "env": {
            "INTER_INSTANCE_TOKEN": PEER_TOKEN,
            "SHIMS_PEERS_FILE": str(ROOT / "config" / "peers.json"),
            "OLLAMA_BASE_URL": f"http://{OLLAMA_HOST}",
            "SHIMS_APP_NAME": "omni",
            "SHIMS_STORAGE_DIR": str(ROOT / "storage" / "omni"),
            "SHIMS_DB_PATH": str(ROOT / "storage" / "omni" / "shims.sqlite3"),
        },
        "health": "http://127.0.0.1:8010/api/peer/health",
    },
    "enterprise_a": {
        "cmd": [
            str(PYTHON), "-u", "-m", "uvicorn", "shims_enterprise.app:app",
            "--host", "127.0.0.1", "--port", "8020", "--no-access-log",
        ],
        "env": {
            "INTER_INSTANCE_TOKEN": PEER_TOKEN,
            "SHIMS_PEERS_FILE": str(ROOT / "config" / "peers.json"),
            "OLLAMA_BASE_URL": f"http://{OLLAMA_HOST}",
            "SHIMS_APP_NAME": "enterprise",
            "SHIMS_STORAGE_DIR": str(ROOT / "storage" / "enterprise"),
            "SHIMS_DB_PATH": str(ROOT / "storage" / "enterprise" / "shims.sqlite3"),
        },
        "health": "http://127.0.0.1:8020/api/peer/health",
    },
    "factory_b": {
        "cmd": [
            str(PYTHON), "-u", "-m", "uvicorn", "backend.app.main:app",
            "--host", "127.0.0.1", "--port", "8030", "--no-access-log",
        ],
        "env": {
            "SHIMS_INSTANCE_ID": "local",
            "SHIMS_ENV_FILE": str(ROOT / ".env.local"),
            "SHIMS_PEERS_FILE": str(ROOT / "config" / "peers.json"),
            "INTER_INSTANCE_TOKEN": PEER_TOKEN,
            "OLLAMA_BASE_URL": f"http://{OLLAMA_HOST}",
            "SHIMS_APP_NAME": "factory",
            "SHIMS_STORAGE_DIR": str(ROOT / "storage" / "factory"),
            "SHIMS_DB_PATH": str(ROOT / "storage" / "factory" / "shims.sqlite3"),
        },
        "health": "http://127.0.0.1:8030/api/peer/health",
    },
}


def _load_state() -> dict[str, int | None]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {name: None for name in SERVICES}


def _save_state(state: dict[str, int | None]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        # Windows-specific check via tasklist
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        return str(pid) in result.stdout and "No tasks" not in result.stdout
    except Exception:
        return False


def _service_env(name: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(SERVICES[name]["env"])
    return env


# Keep file handles and Popen objects alive so the child stdout/stderr pipes stay open.
_proc_handles: dict[str, tuple[subprocess.Popen, object, object]] = {}


def _start_service(name: str) -> int | None:
    cfg = SERVICES[name]
    log_out = ROOT / "logs" / f"pm_{name}.out.log"
    log_err = ROOT / "logs" / f"pm_{name}.err.log"
    port = _port_from_url(cfg["health"])
    try:
        out_fp = open(log_out, "a", encoding="utf-8")
        err_fp = open(log_err, "a", encoding="utf-8")
        proc = subprocess.Popen(
            cfg["cmd"],
            cwd=str(ROOT),
            env=_service_env(name),
            stdout=out_fp,
            stderr=err_fp,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        # Wait up to 30s for the service to bind its port, then store the listening PID.
        deadline = time.time() + 30
        while time.time() < deadline:
            pid = _find_listening_pid(port) if port else None
            if pid:
                _proc_handles[name] = (proc, out_fp, err_fp)
                return pid
            time.sleep(0.5)
        _proc_handles[name] = (proc, out_fp, err_fp)
        print(f"{name}: did not bind port {port} in time", file=sys.stderr)
        return proc.pid
    except Exception as exc:
        print(f"Failed to start {name}: {exc}", file=sys.stderr)
        return None


def _stop_service(pid: int | None) -> bool:
    if pid is None or not _is_alive(pid):
        return True
    try:
        proc = psutil.Process(pid)
        children = proc.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except Exception:
                pass
        proc.terminate()
        gone, alive = psutil.wait_procs(children + [proc], timeout=5)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
        return not _is_alive(pid)
    except Exception as exc:
        print(f"Failed to kill {pid}: {exc}", file=sys.stderr)
        return False


def _health(name: str) -> dict[str, Any]:
    url = SERVICES[name]["health"]
    try:
        r = httpx.get(url, timeout=10)
        return {"ok": r.status_code == 200, "status": r.status_code, "latency_ms": int(r.elapsed.total_seconds() * 1000)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def start_all() -> None:
    state = _load_state()
    for name in SERVICES:
        pid = state.get(name)
        if _is_alive(pid):
            print(f"{name}: already running (pid {pid})")
            continue
        new_pid = _start_service(name)
        if new_pid:
            state[name] = new_pid
            print(f"{name}: started (pid {new_pid})")
        time.sleep(2)
    _save_state(state)


def stop_all() -> None:
    state = _load_state()
    for name in SERVICES:
        pid = state.get(name)
        if _stop_service(pid):
            print(f"{name}: stopped")
        else:
            print(f"{name}: could not stop (pid {pid})")
        state[name] = None
    _save_state(state)


def status() -> None:
    state = _load_state()
    for name in SERVICES:
        pid = state.get(name)
        alive = _is_alive(pid)
        health = _health(name) if alive else {"ok": False, "error": "not running"}
        print(f"{name:15s} pid={pid} alive={alive} health={health}")


def restart_all() -> None:
    stop_all()
    time.sleep(2)
    start_all()


def monitor_loop(interval: int = 30) -> None:
    print(f"Monitoring SHIMS services every {interval}s. Press Ctrl+C to stop.", file=sys.stderr)
    while True:
        state = _load_state()
        changed = False
        for name in SERVICES:
            pid = state.get(name)
            if not _is_alive(pid):
                print(f"{name} (pid {pid}) is down; restarting...", file=sys.stderr)
                new_pid = _start_service(name)
                if new_pid:
                    state[name] = new_pid
                    changed = True
            else:
                h = _health(name)
                if not h.get("ok"):
                    print(f"{name} health check failed: {h}; will restart on next tick if still bad", file=sys.stderr)
        if changed:
            _save_state(state)
        time.sleep(interval)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: shims_process_manager.py start|stop|status|restart|monitor", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1].lower()
    if cmd == "start":
        start_all()
    elif cmd == "stop":
        stop_all()
    elif cmd == "status":
        status()
    elif cmd == "restart":
        restart_all()
    elif cmd == "monitor":
        monitor_loop()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    from typing import Any
    main()
