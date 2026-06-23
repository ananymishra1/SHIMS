"""Generate the Stanford International School app by calling the App Factory tool directly.

This avoids HTTP-level timeouts while the tool makes many LLM calls.
"""
from __future__ import annotations

import json
import sys
import time

from shared.agent_tools import _run_app_factory_build_app


def main() -> None:
    spec_path = __file__.replace("build_school_app_direct.py", "stanford_school_spec.json")
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    print(f"[{time.strftime('%H:%M:%S')}] Starting App Factory build for {spec.get('app_name')}...", flush=True)
    start = time.time()
    result = _run_app_factory_build_app({"spec": spec})
    elapsed = time.time() - start
    print(f"[{time.strftime('%H:%M:%S')}] Build finished in {elapsed:.1f}s", flush=True)

    out_path = __file__.replace("build_school_app_direct.py", "stanford_school_build_direct.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"[{time.strftime('%H:%M:%S')}] Result saved to {out_path}", flush=True)

    print("ok:", result.get("ok"), flush=True)
    print("files_written:", len(result.get("files_written", [])), flush=True)
    print("files_failed:", result.get("files_failed"), flush=True)
    test = result.get("test_result") or {}
    print("test ok:", test.get("ok"), "returncode:", test.get("returncode"), flush=True)
    if test.get("stdout"):
        print("test stdout tail:\n", test["stdout"][-2000:], flush=True)
    if test.get("stderr"):
        print("test stderr tail:\n", test["stderr"][-1000:], flush=True)
    if result.get("error"):
        print("error:", result["error"], flush=True)


if __name__ == "__main__":
    main()
