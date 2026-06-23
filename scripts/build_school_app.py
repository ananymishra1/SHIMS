"""Generate the Stanford International School app using the App Factory."""
from __future__ import annotations

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"

DESIGN_PAYLOAD = {
    "domain": "stanford international school",
    "title": "Stanford International School",
    "prefix": "/school",
    "features": [
        "admissions",
        "attendance",
        "exams",
        "grades",
        "fees",
        "transport",
        "library",
        "parent portal",
        "AI insights",
    ],
    "roles": ["admin", "principal", "teacher", "parent", "librarian", "accountant"],
    "ai_features": True,
}


def main() -> None:
    print("[1/3] Designing app...")
    with httpx.Client(timeout=180) as client:
        r = client.post(f"{BASE}/api/app-factory/design", json=DESIGN_PAYLOAD)
    design = r.json()
    if not design.get("ok"):
        print("Design failed:", design)
        sys.exit(1)
    spec = design["spec"]
    print("  -> spec app_name:", spec.get("app_name"))

    # Save spec for inspection
    spec_path = __file__.replace("build_school_app.py", "stanford_school_spec.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)
    print("  -> spec saved to", spec_path)

    print("[2/3] Building app (this may take a few minutes)...")
    start = time.time()
    with httpx.Client(timeout=600) as client:
        r = client.post(f"{BASE}/api/app-factory/build", json={"spec": spec})
    build = r.json()
    elapsed = time.time() - start
    print(f"  -> build call took {elapsed:.1f}s")

    build_path = __file__.replace("build_school_app.py", "stanford_school_build.json")
    with open(build_path, "w", encoding="utf-8") as f:
        json.dump(build, f, indent=2, ensure_ascii=False)
    print("  -> build result saved to", build_path)

    print("[3/3] Result:")
    print("  ok:", build.get("ok"))
    print("  files_written:", len(build.get("files_written", [])))
    print("  files_failed:", build.get("files_failed"))
    print("  mount_note:", build.get("mount_note"))
    print("  tile_note:", build.get("tile_note"))
    if build.get("error"):
        print("  error:", build["error"])
    if build.get("stdout"):
        print("  test stdout:\n", build["stdout"][-2000:])
    if build.get("stderr"):
        print("  test stderr:\n", build["stderr"][-1000:])


if __name__ == "__main__":
    main()
