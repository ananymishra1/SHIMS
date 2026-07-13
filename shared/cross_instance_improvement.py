"""Cross-instance improvement sync for SHIMS primary ↔ local factory.

Allows the two Omni instances to improve each other by exchanging improvement
proposals (patches, skills, prompt variants) and by delegating reflection or
evaluation to the peer when it has a better-suited model.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .config import STORAGE_DIR
from .inter_instance_bridge import PeerClient, get_peer

SYNC_DIR = STORAGE_DIR / "cross_instance"
SYNC_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return str(time.time())


def _peer_proposals_path(peer_id: str) -> Path:
    return SYNC_DIR / f"proposals_from_{peer_id}.jsonl"


def _save_received_proposals(peer_id: str, proposals: list[dict[str, Any]]) -> int:
    path = _peer_proposals_path(peer_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for p in proposals:
            entry = {"received_at": _now(), "source_peer": peer_id, "proposal": p}
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return len(proposals)


def list_received_proposals(peer_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Return proposals received from a peer (or all peers if peer_id is None)."""
    paths = [SYNC_DIR / f"proposals_from_{peer_id}.jsonl"] if peer_id else list(SYNC_DIR.glob("proposals_from_*.jsonl"))
    items: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except Exception:
                    continue
    items.sort(key=lambda x: float(x.get("received_at", 0)), reverse=True)
    return items[:limit]


async def push_proposals_to_peer(peer_id: str, proposals: list[dict[str, Any]]) -> dict[str, Any]:
    """Push a list of improvement proposals to the peer instance."""
    peer = get_peer(peer_id)
    if not peer:
        return {"ok": False, "error": f"peer {peer_id} not found in config/peers.json"}
    if not proposals:
        return {"ok": True, "sent": 0}
    client = PeerClient(peer)
    result = await client.send_proposals(proposals)
    return {"ok": bool(result.get("ok")), "sent": len(proposals), "peer_result": result}


async def pull_proposals_from_peer(peer_id: str, limit: int = 50) -> dict[str, Any]:
    """Pull improvement proposals from the peer instance and store them locally."""
    peer = get_peer(peer_id)
    if not peer:
        return {"ok": False, "error": f"peer {peer_id} not found in config/peers.json"}
    client = PeerClient(peer)
    result = await client.get_proposals(limit=limit)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "peer request failed")}
    proposals = result.get("proposals", [])
    ingested = _save_received_proposals(peer_id, proposals)
    return {"ok": True, "ingested": ingested, "proposals": proposals}


async def sync_proposals_with_peer(peer_id: str, local_proposals: list[dict[str, Any]], limit: int = 50) -> dict[str, Any]:
    """Bidirectional proposal sync: push local, pull remote."""
    push_result = await push_proposals_to_peer(peer_id, local_proposals)
    pull_result = await pull_proposals_from_peer(peer_id, limit=limit)
    return {
        "ok": push_result.get("ok") and pull_result.get("ok"),
        "push": push_result,
        "pull": pull_result,
    }


def default_peer_id() -> str:
    """Return the peer id that is 'the other instance'."""
    instance_id = (os.getenv("SHIMS_INSTANCE_ID") or "primary").strip().lower()
    return "local" if instance_id == "primary" else "primary"


async def run_cross_instance_sync(local_proposals: list[dict[str, Any]] | None = None, peer_id: str | None = None) -> dict[str, Any]:
    """Convenience entry point: sync proposals with the default peer."""
    peer_id = peer_id or default_peer_id()
    local_proposals = local_proposals or []
    return await sync_proposals_with_peer(peer_id, local_proposals)
