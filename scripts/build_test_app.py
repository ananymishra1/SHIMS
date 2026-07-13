"""Quick test of the App Factory build tool with a tiny app."""
from __future__ import annotations

import json
import sys
import time

from shared.agent_tools import _run_app_factory_build_app

SPEC = {
    "app_name": "todo_demo",
    "title": "Todo Demo",
    "prefix": "/todo",
    "roles": [{"username": "admin", "role": "admin", "password": "admin123"}],
    "entities": [
        {"name": "task", "fields": [
            {"name": "title", "type": "text", "required": True},
            {"name": "done", "type": "boolean", "required": False},
        ]}
    ],
    "routes": [
        {"path": "/api/tasks", "method": "POST", "purpose": "create task", "required_fields": ["title"]},
        {"path": "/api/tasks", "method": "GET", "purpose": "list tasks"},
    ],
    "ui_tabs": ["Tasks"],
    "ai_endpoints": [],
    "tests": ["test create and list tasks"],
}


def main() -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Building tiny todo app...", flush=True)
    start = time.time()
    result = _run_app_factory_build_app({"spec": SPEC})
    elapsed = time.time() - start
    print(f"[{time.strftime('%H:%M:%S')}] Done in {elapsed:.1f}s", flush=True)
    print("ok:", result.get("ok"), flush=True)
    print("files_written:", result.get("files_written"), flush=True)
    print("files_failed:", result.get("files_failed"), flush=True)
    test = result.get("test_result") or {}
    print("test ok:", test.get("ok"), "returncode:", test.get("returncode"), flush=True)
    if test.get("stdout"):
        print("stdout tail:\n", test["stdout"][-2000:], flush=True)
    if test.get("stderr"):
        print("stderr tail:\n", test["stderr"][-1000:], flush=True)
    if result.get("error"):
        print("error:", result["error"], flush=True)
    with open("scripts/todo_demo_build.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
