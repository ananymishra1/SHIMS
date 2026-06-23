#!/usr/bin/env python3
"""SHIMS capability audit.

Probes key Omni / Enterprise / Bridge endpoints and writes a Markdown report.
Run after starting the stack with `scripts/start_shims.py --no-verify`.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import requests

BASE_OMNI = "http://localhost:8010"
BASE_ENT = "http://localhost:8020"
BRIDGE_WS = "ws://localhost:9876/bridge"
REPORT_PATH = Path("logs/shims_capability_audit.md")
TIMEOUT = 15
CHAT_TIMEOUT = 10


def _trunc(text: str | None, limit: int = 240) -> str:
    if text is None:
        return ""
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _call(method: str, url: str, payload: dict | None = None, timeout: int = TIMEOUT) -> dict:
    start = time.time()
    try:
        kwargs: dict = {"timeout": timeout}
        if payload is not None:
            kwargs["json"] = payload
        r = requests.request(method, url, **kwargs)
        latency = round(time.time() - start, 3)
        return {
            "status": r.status_code,
            "latency": latency,
            "ok": r.ok,
            "snippet": _trunc(r.text),
        }
    except requests.exceptions.Timeout:
        return {"status": "TIMEOUT", "latency": round(time.time() - start, 3), "ok": False, "snippet": "Request timed out"}
    except requests.exceptions.ConnectionError as e:
        return {"status": "CONN_ERR", "latency": round(time.time() - start, 3), "ok": False, "snippet": str(e)[:200]}
    except Exception as e:
        return {"status": "ERROR", "latency": round(time.time() - start, 3), "ok": False, "snippet": str(e)[:200]}


def _row(method: str, endpoint: str, payload: dict | None, result: dict) -> str:
    status = str(result["status"])
    badge = "✅" if result["ok"] else ("⚠️" if status in {"TIMEOUT", "CONN_ERR"} else "❌")
    payload_str = json.dumps(payload) if payload else ""
    return (
        f"| `{method.upper()}` | `{endpoint}` | `{_trunc(payload_str, 80)}` | "
        f"{badge} `{status}` | {result['latency']}s | {_trunc(result['snippet'], 160)} |"
    )


def main() -> None:
    rows: list[tuple[str, str, dict | None, dict]] = []

    tests: list[tuple[str, str, str, dict | None, int | None]] = [
        # Omni health / meta
        ("omni", "GET", "/health", None, None),
        ("omni", "GET", "/launch/readiness", None, None),
        ("omni", "GET", "/api/ai/health", None, None),
        ("omni", "GET", "/ollama/status", None, None),
        ("omni", "GET", "/api/v13/health", None, None),
        # Chat (short timeout to surface hanging LLM calls)
        ("omni", "POST", "/api/chat", {"message": "hello", "session_id": "audit-chat", "source": "audit"}, CHAT_TIMEOUT),
        # Memory
        ("omni", "POST", "/api/memory/save", {"content": "audit memory entry", "key": "audit-key", "namespace": "audit", "tags": ["audit"]}, None),
        ("omni", "POST", "/api/memory/search", {"query": "audit memory", "limit": 5}, None),
        ("omni", "POST", "/brain/ingest", {"content": "audit knowledge chunk", "source": "audit", "source_uri": "audit://chunk"}, None),
        ("omni", "POST", "/rag/search", {"query": "audit knowledge", "limit": 5}, None),
        # Plans & schedule
        ("omni", "POST", "/api/plans", {"goal": "audit plan", "steps": [], "context": {}}, None),
        ("omni", "GET", "/api/plans", None, None),
        ("omni", "POST", "/api/schedule", {"title": "audit task", "schedule_type": "once", "when": "2099-01-01T00:00:00", "action_type": "message", "payload": {"text": "audit"}}, None),
        ("omni", "GET", "/api/schedule", None, None),
        # Tasks
        ("omni", "GET", "/api/tasks", None, None),
        ("omni", "POST", "/api/tasks", {"title": "audit task", "tool": "memory.search", "payload": {"query": "audit"}}, None),
        # Vision / interpreter
        ("omni", "POST", "/api/vision/describe", {"source": "", "prompt": "Describe this image."}, None),
        ("omni", "POST", "/api/interpreter/run", {"code": "print(2+2)", "timeout": 10}, None),
        # Agent tools / swarm
        ("omni", "GET", "/agent/capabilities", None, None),
        ("omni", "GET", "/agent/tools", None, None),
        ("omni", "POST", "/agent/swarm", {"prompt": "audit swarm test", "use_llm": False, "orchestrate": False}, None),
        # Bridge
        ("omni", "GET", "/api/desktop/bridge/status", None, None),
        ("omni", "POST", "/api/desktop/bridge/command", {"command": "ping"}, None),
        # Enterprise
        ("omni", "GET", "/enterprise/status", None, None),
        ("omni", "GET", "/enterprise/commands", None, None),
        ("omni", "GET", "/enterprise/dashboard", None, None),
        ("omni", "POST", "/enterprise/command", {"command": "status"}, None),
        ("ent", "GET", "/executive", None, None),
        ("ent", "GET", "/api/executive/kpis", None, None),
        ("ent", "GET", "/api/rd/v2/products", None, None),
        # Improvement / evolution
        ("omni", "GET", "/improvement/runs", None, None),
        ("omni", "GET", "/evolution/proposals", None, None),
        ("omni", "GET", "/self/status", None, None),
        # App factory
        ("omni", "POST", "/api/app-factory/diagnose", {"app_name": "todo_demo"}, None),
        # Neural governor
        ("omni", "GET", "/api/neural-governor/models", None, None),
        ("omni", "GET", "/api/neural-governor/diagnostics", None, None),
        # Skills
        ("omni", "GET", "/skills", None, None),
        ("omni", "POST", "/skills/save", {"name": "audit-skill", "text": "This is an audit skill.", "tags": ["audit"]}, None),
        # Voice / media
        ("omni", "GET", "/voice/config", None, None),
        ("omni", "GET", "/stt/health", None, None),
        ("omni", "GET", "/media/settings", None, None),
        # Web / browser
        ("omni", "GET", "/web/health", None, None),
        ("omni", "POST", "/web/fetch", {"url": "https://example.com"}, None),
        # Coder
        ("omni", "GET", "/coder/v3/projects", None, None),
        # Actions / approvals
        ("omni", "GET", "/actions/pending", None, None),
        ("omni", "GET", "/approvals/pending", None, None),
        # Documents
        ("omni", "GET", "/documents", None, None),
        # Sessions
        ("omni", "POST", "/sessions/new", {}, None),
        ("omni", "GET", "/sessions", None, None),
    ]

    for scope, method, endpoint, payload, custom_timeout in tests:
        base = BASE_OMNI if scope == "omni" else BASE_ENT
        result = _call(method, base + endpoint, payload, timeout=custom_timeout or TIMEOUT)
        rows.append((method, endpoint, payload, result))

    passed = sum(1 for _, _, _, r in rows if r["ok"])
    total = len(rows)

    lines = [
        "# SHIMS Capability Audit Report",
        f"**Generated:** {datetime.utcnow().isoformat()}Z",
        f"**Omni:** {BASE_OMNI} | **Enterprise:** {BASE_ENT} | **Bridge WS:** {BRIDGE_WS}",
        "",
        f"**Summary:** {passed}/{total} probes returned HTTP 2xx/ok.",
        "",
        "| Method | Endpoint | Payload | Result | Latency | Snippet |",
        "|---|---|---|---|---|---|",
    ]
    for method, endpoint, payload, result in rows:
        lines.append(_row(method, endpoint, payload, result))
    lines.append("")
    lines.append("## Notes")
    lines.append("- `TIMEOUT` usually means the route tried to call an offline LLM / model and hung.")
    lines.append("- `CONN_ERR` means the target service was not reachable on the expected port.")
    lines.append("- `4xx` errors on POST endpoints with minimal payloads are often validation errors, not functional failures.")
    lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Audit complete: {passed}/{total} passed. Report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
