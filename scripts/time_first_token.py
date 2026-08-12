"""Measure ms-to-first-token for /brain/turn across representative prompt classes.

Usage:
    .venv/Scripts/python scripts/time_first_token.py
    SHIMS_BASE_URL=http://127.0.0.1:8030 .venv/Scripts/python scripts/time_first_token.py
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid

BASE_URL = os.getenv("SHIMS_BASE_URL", "http://127.0.0.1:8010").rstrip("/")

PROMPTS = [
    ("greeting", "hi"),
    ("tool-intent", "check if the desktop bridge is running"),
    ("rag-worthy", "what do you know about my IEC certificate"),
    ("code", "write a python function to reverse a string"),
    ("search", "search the web for today's news"),
]


def time_to_first_token(prompt: str) -> tuple[float, str]:
    """POST /brain/turn and return (ms to first content/meta event, route tag)."""
    body = json.dumps({
        "message": prompt,
        "session_id": f"ttft-{uuid.uuid4()}",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/brain/turn",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    first_ms: float | None = None
    route = ""
    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type") or evt.get("event") or ""
            if first_ms is None and etype in {"meta", "delta", "token", "content"}:
                first_ms = (time.perf_counter() - start) * 1000.0
                route = evt.get("route") or evt.get("model") or ""
            if etype == "done":
                route = evt.get("route") or route
                break
    total_ms = (time.perf_counter() - start) * 1000.0
    return (first_ms if first_ms is not None else total_ms), route


def main() -> None:
    print(f"Target: {BASE_URL}/brain/turn")
    print(f"{'prompt class':<14} {'first-event ms':>15}  route")
    for label, prompt in PROMPTS:
        try:
            ms, route = time_to_first_token(prompt)
            print(f"{label:<14} {ms:>15.0f}  {route}")
        except Exception as exc:  # noqa: BLE001 - diagnostic script
            print(f"{label:<14} {'ERROR':>15}  {exc}")


if __name__ == "__main__":
    main()
