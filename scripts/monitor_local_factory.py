"""Simple monitor for the SHIMS Local Factory and related services.

Writes a JSON line to logs/factory_monitor.log every interval with:
- timestamp
- service health for Instance A/B/Chem API/frontends
- Ollama model list
- factory status
- chemdfm presence
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "factory_monitor.log"

SERVICES: dict[str, tuple[str, str | None]] = {
    "omni_a": ("http://127.0.0.1:8010/api/peer/health", None),
    "enterprise_a": ("http://127.0.0.1:8020/api/peer/health", None),
    "factory_b": ("http://127.0.0.1:8030/api/peer/health", None),
    "chem_api": ("http://127.0.0.1:8000/health", None),
    "chem_frontend": ("http://127.0.0.1:8088/", None),
    "chembrain_frontend": ("http://127.0.0.1:8089/", None),
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def check_services() -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for name, (url, _) in SERVICES.items():
        try:
            r = httpx.get(url, timeout=10)
            results[name] = {"ok": r.status_code == 200, "status": r.status_code, "latency_ms": int(r.elapsed.total_seconds() * 1000)}
        except Exception as exc:
            results[name] = {"ok": False, "error": str(exc)[:120]}
    return results


def check_ollama() -> dict[str, Any]:
    try:
        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=10)
        models = [m.get("name", "") for m in r.json().get("models", [])]
        return {"ok": True, "models": models, "chemdfm_ready": any("chemdfm" in m for m in models)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def check_factory() -> dict[str, Any]:
    try:
        r = httpx.get("http://127.0.0.1:8030/api/factory/status", timeout=10)
        return {"ok": r.status_code == 200, "data": r.json()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def run_once() -> dict[str, Any]:
    snapshot = {
        "timestamp": _now(),
        "services": check_services(),
        "ollama": check_ollama(),
        "factory": check_factory(),
    }
    return snapshot


def main() -> None:
    interval = int(os.getenv("MONITOR_INTERVAL", "60"))
    print(f"Monitoring Local Factory every {interval}s. Log: {LOG_PATH}", file=sys.stderr)
    while True:
        snapshot = run_once()
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, default=str) + "\n")
        if not os.getenv("MONITOR_LOOP", "1") == "1":
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
