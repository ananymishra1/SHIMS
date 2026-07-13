from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .config import STORAGE_DIR

TOOLS = [
    {"name": "shims.media.image", "kind": "tool", "description": "Create local or provider-backed image artifacts and verify the output file."},
    {"name": "shims.media.video", "kind": "tool", "description": "Create FFmpeg storyboard videos or provider-backed video artifacts."},
    {"name": "shims.document.pdf", "kind": "tool", "description": "Create branded PDFs through ReportLab and register them in the SHA-256 ledger."},
    {"name": "shims.document.ppt", "kind": "tool", "description": "Create PowerPoint decks through python-pptx and register them in the ledger."},
    {"name": "shims.enterprise.gst_invoice", "kind": "tool", "description": "Generate GST invoice draft PDF and e-invoice-style JSON payload."},
    {"name": "shims.enterprise.ewaybill", "kind": "tool", "description": "Generate e-Way Bill draft JSON and distance validation."},
    {"name": "shims.enterprise.coa", "kind": "tool", "description": "Generate QC COA draft and department-linked audit entry."},
    {"name": "shims.brain.context", "kind": "tool", "description": "Retrieve packed long-term memory, RAG, and research context for a user turn."},
    {"name": "shims.brain.ingest", "kind": "tool", "description": "Ingest text, web snippets, artifacts, and notes into the local RAG store."},
    {"name": "shims.brain.learn", "kind": "tool", "description": "Run the background learning cycle over telemetry, episodes, and feedback."},
    {"name": "shims.memory.save", "kind": "tool", "description": "Persist a durable namespaced memory with tags, source, and weight."},
    {"name": "shims.capture.share", "kind": "tool", "description": "Save shared links, notes, snippets, and external content into SHIMS capture inbox, RAG, and task queue."},
    {"name": "shims.mailbox.digest", "kind": "tool", "description": "Summarize local mailbox/capture items and action candidates without hidden account access."},
    {"name": "shims.mailbox.gmail_sync", "kind": "tool", "description": "Sync Gmail metadata only after explicit OAuth consent and configured credentials."},
    {"name": "shims.evolution.propose_patch", "kind": "tool", "description": "Create a signed patch proposal without modifying production files."},
    {"name": "shims.evolution.validate_patch", "kind": "tool", "description": "Apply proposal in sandbox and run tests/compilation."},
    {"name": "shims.evolution.apply_patch", "kind": "tool", "description": "Apply a validated proposal after explicit human approval and backup."},
]

RESOURCES = [
    {"name": "daily_lessons", "uri": "shims://memory/daily_lessons", "description": "Daily self-reflection lessons injected into the SHIMS system prompt."},
    {"name": "omni_brain", "uri": "shims://brain/omni-v15", "description": "Persistent memory, RAG chunks, research items, episodes, and background tasks."},
    {"name": "capture_mailbox", "uri": "shims://mailbox/capture-v1", "description": "User-shared links, mailbox metadata, action candidates, and Play Store-safe Gmail consent policy."},
    {"name": "document_ledger", "uri": "shims://ledger/documents", "description": "SHA-256 artifact ledger for generated regulatory and media outputs."},
    {"name": "enterprise_master_data", "uri": "shims://enterprise/master-data", "description": "Product, material, vendor, batch and department operational data."},
]


def manifest() -> dict[str, Any]:
    body = {
        "name": "SHIMS v13 MCP-style Tool Manifest",
        "version": "13.0.0-platform-foundation",
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "transport": "local FastAPI JSON endpoints now; MCP JSON-RPC compatibility layer active.",
        "tools": TOOLS,
        "resources": RESOURCES,
        "security": {
            "tool_first_routing": True,
            "manifest_signed_by_sha256": True,
            "human_approval_required_for_source_patch": True,
            "gxp_never_autonomous": True,
        },
    }
    canonical = json.dumps({k: v for k, v in body.items() if k != "sha256"}, sort_keys=True, ensure_ascii=False)
    body["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return body


# --------------------------------------------------------------------------- #
# MCP Client (Phase 4.1 Soul, Brain & Swarm upgrade)
# --------------------------------------------------------------------------- #
MCP_SERVERS_PATH = Path(os.getenv("SHIMS_MCP_SERVERS_PATH", STORAGE_DIR / "mcp_servers.json"))


def _default_servers_path() -> Path:
    return MCP_SERVERS_PATH


def load_servers_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read the MCP servers configuration from disk.

    Expected shape:
        {
          "servers": {
            "name": {
              "url": "http://127.0.0.1:8000/sse",
              "endpoint": "/messages/",   # optional; defaults to server root
              "headers": {"Authorization": "Bearer ..."},
              "timeout": 30
            }
          }
        }
    """
    target = Path(path) if path else _default_servers_path()
    if not target.exists():
        return {"servers": {}}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"servers": {}, "error": str(exc)}


@dataclass
class MCPServer:
    name: str
    url: str
    endpoint: str = "/"
    headers: dict[str, str] = field(default_factory=dict)
    timeout: int = 30

    def post_url(self) -> str:
        base = self.url.rstrip("/")
        ep = self.endpoint if self.endpoint.startswith("/") else f"/{self.endpoint}"
        return f"{base}{ep}"


def get_server(name: str, config: dict[str, Any] | None = None) -> MCPServer | None:
    cfg = config if config is not None else load_servers_config()
    servers = cfg.get("servers") or {}
    data = servers.get(name)
    if data is None:
        return None
    return MCPServer(
        name=name,
        url=str(data.get("url") or ""),
        endpoint=str(data.get("endpoint") or "/"),
        headers=dict(data.get("headers") or {}),
        timeout=int(data.get("timeout") or 30),
    )


def list_servers(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = config if config is not None else load_servers_config()
    servers = cfg.get("servers") or {}
    return [
        {
            "name": name,
            "url": data.get("url"),
            "endpoint": data.get("endpoint", "/"),
            "headers_keys": list((data.get("headers") or {}).keys()),
        }
        for name, data in servers.items()
    ]


class MCPClient:
    """Minimal JSON-RPC HTTP client for Model Context Protocol servers.

    Sends JSON-RPC 2.0 requests to a configured MCP server and normalises the
    response into a plain dict suitable for the SHIMS agent loop.
    """

    def __init__(self, server: MCPServer):
        self.server = server

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params or {},
        }
        try:
            with httpx.Client(timeout=self.server.timeout) as client:
                r = client.post(
                    self.server.post_url(),
                    json=payload,
                    headers=self.server.headers,
                )
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as exc:
            return {
                "ok": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:400]}",
                "server": self.server.name,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:400], "server": self.server.name}

        if not isinstance(data, dict):
            return {"ok": False, "error": "invalid JSON-RPC response", "server": self.server.name}
        if "error" in data:
            err = data["error"]
            return {
                "ok": False,
                "error": err.get("message") if isinstance(err, dict) else str(err),
                "code": err.get("code") if isinstance(err, dict) else None,
                "server": self.server.name,
            }
        return {"ok": True, "result": data.get("result"), "server": self.server.name}

    def list_tools(self) -> dict[str, Any]:
        return self._request("tools/list")

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._request("tools/call", {"name": tool_name, "arguments": arguments or {}})


def call_tool(server_name: str, tool_name: str, arguments: dict[str, Any] | None = None,
              *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convenience wrapper: look up a configured server and call a tool on it."""
    server = get_server(server_name, config=config)
    if server is None:
        return {"ok": False, "error": f"unknown MCP server: {server_name}"}
    if not server.url:
        return {"ok": False, "error": f"server '{server_name}' has no URL"}
    client = MCPClient(server)
    return client.call_tool(tool_name, arguments or {})
