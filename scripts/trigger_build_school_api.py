"""Trigger SHIMS App Factory to build Stanford International School via REST."""
from __future__ import annotations

import json
import sys
import time

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from scripts.build_stanford_school import SPEC

BASE = "http://127.0.0.1:8000"
POLL_INTERVAL = 10


def main() -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Triggering App Factory build for '{SPEC['title']}'...")
    res = requests.post(f"{BASE}/api/app-factory/build", json={"spec": SPEC}, timeout=30)
    res.raise_for_status()
    data = res.json()
    job_id = data["job_id"]
    print(f"[{time.strftime('%H:%M:%S')}] Job started: {job_id}")

    while True:
        time.sleep(POLL_INTERVAL)
        status_res = requests.get(f"{BASE}/api/app-factory/build/{job_id}", timeout=30)
        status_res.raise_for_status()
        status = status_res.json()
        job_status = status.get("status")
        print(f"[{time.strftime('%H:%M:%S')}] status={job_status}")
        if job_status in ("done", "failed"):
            result = status.get("result") or {}
            print(json.dumps(result, indent=2, ensure_ascii=False))
            with open("scripts/stanford_school_build.json", "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            if result.get("ok"):
                print("\n✅ Stanford International School app built and tested.")
            else:
                print("\n❌ Build failed. See scripts/stanford_school_build.json")
            break


if __name__ == "__main__":
    main()
