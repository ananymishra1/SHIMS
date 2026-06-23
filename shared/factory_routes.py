"""FastAPI router for Local Factory corpus and evolution endpoints.

Mounted by SHIMS Omni (backend/app/main.py) on both Instance A and Instance B.
On Instance A the endpoints report that the factory is remote; on Instance B
they perform the actual work.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request

from .local_factory_config import is_factory_instance
from .local_factory_corpus import build_corpus_async, corpus_stats, sync_from_peer
from .factory_evolution_loop import evolution_status, run_evolution_cycle, start_background_evolution

router = APIRouter(prefix="/api/factory", tags=["factory"])


def _require_factory() -> dict[str, Any] | None:
    if not is_factory_instance():
        return {
            "ok": False,
            "note": "This is the primary SHIMS instance. The Local Factory runs on Instance B.",
            "factory_url": "http://127.0.0.1:8030/api/factory/status",
        }
    return None


@router.get("/status")
async def factory_status() -> dict[str, Any]:
    remote = _require_factory()
    if remote:
        return remote
    return evolution_status()


@router.post("/corpus/build")
async def factory_corpus_build(request: Request) -> dict[str, Any]:
    remote = _require_factory()
    if remote:
        return remote
    body = await request.json()
    return await build_corpus_async(
        force=bool(body.get("force")),
        web_queries=body.get("web_queries"),
        max_web_pages=int(body.get("max_web_pages", 6)),
        synthesize_qa=bool(body.get("synthesize_qa", True)),
        max_qa_chunks=int(body.get("max_qa_chunks", 200)),
    )


@router.get("/corpus/stats")
async def factory_corpus_stats() -> dict[str, Any]:
    remote = _require_factory()
    if remote:
        return remote
    return corpus_stats()


@router.post("/corpus/sync-peer")
async def factory_corpus_sync_peer(request: Request) -> dict[str, Any]:
    remote = _require_factory()
    if remote:
        return remote
    body = await request.json()
    peer_id = str(body.get("peer_id", "primary"))
    return await sync_from_peer(
        peer_id,
        source_type=body.get("source_type"),
        limit=int(body.get("limit", 1000)),
    )


@router.post("/evolution/run")
async def factory_evolution_run(request: Request) -> dict[str, Any]:
    remote = _require_factory()
    if remote:
        return remote
    body = await request.json() or {}
    train_mode = str(body.get("train_mode", os.getenv("SHIMS_FACTORY_TRAIN_MODE", "ollama")))
    sync_peers = body.get("sync_peers")
    if isinstance(sync_peers, str):
        sync_peers = [sync_peers]
    background = bool(body.get("background", True))
    if background:
        return start_background_evolution(train_mode=train_mode, sync_peers=sync_peers)
    return await run_evolution_cycle(train_mode=train_mode, sync_peers=sync_peers)
