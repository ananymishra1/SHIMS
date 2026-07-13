"""Inter-instance bridge for SHIMS primary ↔ local factory communication.

Allows the main SHIMS instance (Instance A, usually cloud-backed) to consult the
isolated Local Factory instance (Instance B, Ollama + ChemDFM) and vice versa.

Public API:
    register_peer_routes(app, prefix="/api/peer")   # mount peer endpoints
    PeerClient(peer)                                # call a peer instance
    list_peers()                                    # load peer definitions
    peer_auth(request)                              # FastAPI dependency
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from starlette.requests import Request as StarletteRequest

from . import agent_tools
from .config import settings, STORAGE_DIR, ROOT_DIR
from .local_factory_config import inter_instance_token, peers_file

PEER_AUTH_HEADER = "X-Peer-Token"
INSTANCE_ID = (os.getenv("SHIMS_INSTANCE_ID") or "primary").strip()
PEER_STATUS_TIMEOUT = float(os.getenv("SHIMS_PEER_STATUS_TIMEOUT_SECONDS", "4"))
PEER_LLM_TIMEOUT = float(os.getenv("SHIMS_PEER_LLM_TIMEOUT_SECONDS", "45"))
PEER_DB_STATUS_TIMEOUT = float(os.getenv("SHIMS_PEER_DB_STATUS_TIMEOUT_SECONDS", "2"))

# Tools a remote peer is allowed to invoke.  This whitelist prevents a peer from
# triggering risky local actions (shell, file writes, self-evolution patches).
PEER_TOOL_WHITELIST: set[str] = {
    "brain.search",
    "brain.self_index",
    "memory.search",
    "memory.save",
    "memory.ingest_media",
    "chem.chemdfm_query",
    "chem.chemdfm_journal",
    "chem.chemdfm_train",
    "enterprise.status",
    "enterprise.commands",
    "enterprise.dashboard",
    "rd.product_assist",
    "app_factory.diagnose_app",
    "app_factory.repair_app",
    "vision.describe",
    "desktop.interpreter",
    "plan.create",
    "plan.list",
    "plan.get",
    "schedule.list",
    "search.web",
    "search.deep_research",
    "local_llm.chat",
}


def _constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _token() -> str:
    return inter_instance_token() or settings.bridge_token or ""


def peer_auth(request: StarletteRequest, x_peer_token: str | None = Header(None, alias=PEER_AUTH_HEADER)) -> str:
    """FastAPI dependency that validates the peer token."""
    expected = _token()
    if not expected:
        raise HTTPException(status_code=500, detail="inter-instance token not configured")
    if not x_peer_token or not _constant_time_compare(x_peer_token, expected):
        raise HTTPException(status_code=403, detail="invalid peer token")
    return x_peer_token


def list_peers() -> list[dict[str, Any]]:
    """Load peer definitions from config/peers.json plus SHIMS_PEERS_INSTANCES env."""
    peers: list[dict[str, Any]] = []
    seen: set[str] = set()

    env_peers = os.getenv("SHIMS_PEERS_INSTANCES", "").strip()
    if env_peers:
        try:
            for p in json.loads(env_peers):
                if p.get("id") and p["id"] not in seen and p.get("url"):
                    seen.add(p["id"])
                    peers.append(p)
        except Exception:
            pass

    path = peers_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            token = data.get("token", _token())
            for p in data.get("instances", []):
                if p.get("id") in seen or not p.get("url"):
                    continue
                p = dict(p)
                p.setdefault("token", token)
                seen.add(p["id"])
                peers.append(p)
        except Exception:
            pass

    return peers


def get_peer(peer_id: str) -> dict[str, Any] | None:
    for p in list_peers():
        if p.get("id") == peer_id:
            return p
    return None


def _capability_flags(ollama_models: list[str]) -> dict[str, Any]:
    names = [m.lower() for m in ollama_models if isinstance(m, str)]
    has = lambda *needles: any(any(n in model for n in needles) for model in names)
    return {
        "chat": bool(names),
        "fast_chat": has("qwen2.5:3b", "gemma3:1b", "llama3.2"),
        "heavy_reasoning": has("qwen2.5:7b", "qwen2.5:14b", "qwen2.5-coder:14b"),
        "chemistry": has("chemdfm"),
        "coding": has("qwen2.5-coder", "qwen-coder"),
        "vision": has("llava", "bakllava", "moondream", "vision"),
        "tool_calling": has("qwen2.5", "qwen3", "llama3.1", "llama3.2", "mistral-nemo"),
    }


async def _ollama_model_names(timeout: float = PEER_STATUS_TIMEOUT) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
            if r.status_code == 200:
                return [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
    except Exception:
        pass
    return []


def quick_brain_status() -> dict[str, Any]:
    """Cheap brain DB status for health checks; never loads embedding models."""
    db_path = Path(os.getenv("SHIMS_BRAIN_DB", ROOT_DIR / "data" / "state" / "omni_brain.sqlite3")).resolve()
    if not db_path.exists():
        return {"ok": False, "db_path": str(db_path), "error": "brain database not found"}
    tables = ["memories", "episodes", "knowledge_chunks", "research_items", "background_tasks"]
    counts: dict[str, int] = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.25)
        con.row_factory = sqlite3.Row
        with con:
            existing = {
                row["name"]
                for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for table in tables:
                if table in existing:
                    counts[table] = int(con.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"])
                else:
                    counts[table] = 0
    except Exception as exc:
        return {"ok": False, "db_path": str(db_path), "error": str(exc)[:200], "counts": counts}
    return {"ok": True, "db_path": str(db_path), "counts": counts, "mode": "quick"}


async def _peer_capabilities_payload() -> dict[str, Any]:
    started = time.perf_counter()
    ollama_models = await _ollama_model_names()
    try:
        from .local_factory_config import (
            chemistry_model,
            coder_model,
            default_model,
            heavy_model,
            is_factory_instance,
            router_model,
        )

        role_models = {
            "fast": default_model(),
            "heavy": heavy_model(),
            "chemistry": chemistry_model(),
            "coder": coder_model(),
            "router": router_model(),
        }
        factory = is_factory_instance()
    except Exception:
        role_models = {
            "fast": settings.ollama_model,
            "heavy": os.getenv("SHIMS_HEAVY_MODEL", settings.ollama_model),
            "chemistry": os.getenv("CHEMDFM_OLLAMA_TAG", "chemdfm"),
            "coder": getattr(settings, "self_evolution_model", "qwen2.5-coder:14b"),
            "router": os.getenv("SHIMS_ROUTER_MODEL", settings.ollama_model),
        }
        factory = False

    brain_status = quick_brain_status()

    try:
        from .local_factory_corpus import corpus_stats

        corpus = await asyncio.wait_for(asyncio.to_thread(corpus_stats), timeout=PEER_DB_STATUS_TIMEOUT)
    except asyncio.TimeoutError:
        corpus = {"ok": False, "error": f"corpus stats timed out after {PEER_DB_STATUS_TIMEOUT:g}s"}
    except Exception as exc:
        corpus = {"ok": False, "error": str(exc)[:200]}

    return {
        "ok": True,
        "instance_id": INSTANCE_ID,
        "factory_instance": factory,
        "provider": settings.ai_provider,
        "default_model": settings.ollama_model,
        "role_models": role_models,
        "ollama": {
            "base_url": settings.ollama_base_url,
            "online": bool(ollama_models),
            "models": ollama_models,
        },
        "capabilities": _capability_flags(ollama_models),
        "brain": brain_status,
        "corpus": corpus,
        "storage": str(STORAGE_DIR),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "checked_at": time.time(),
    }


class PeerClient:
    """HTTP client for calling another SHIMS instance's peer endpoints."""

    def __init__(self, peer: dict[str, Any]) -> None:
        self.peer = peer
        self.url = peer["url"].rstrip("/")
        self.token = peer.get("token") or _token()
        self.timeout = float(peer.get("timeout", 120.0))

    def _headers(self) -> dict[str, str]:
        return {PEER_AUTH_HEADER: self.token, "Content-Type": "application/json"}

    def _path(self, path: str) -> str:
        return f"{self.url}/api/peer/{path.lstrip('/')}"

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(self._path("health"), headers=self._headers())
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}

    async def status(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, PEER_STATUS_TIMEOUT + 2.0)) as c:
                r = await c.get(self._path("status"), headers=self._headers())
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(
                    self._path("call"),
                    headers=self._headers(),
                    json={"tool": name, "args": args or {}},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}

    async def capabilities(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, PEER_STATUS_TIMEOUT + 2.0)) as c:
                r = await c.get(self._path("capabilities"), headers=self._headers())
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            fallback = await self.status()
            if fallback.get("ok"):
                models = fallback.get("ollama_models") or []
                fallback.setdefault("capabilities", _capability_flags(models))
                fallback.setdefault("role_models", {
                    "fast": fallback.get("default_model") or "qwen2.5:3b",
                    "heavy": "qwen2.5:7b" if "qwen2.5:7b" in models else fallback.get("default_model", ""),
                    "chemistry": "chemdfm" if "chemdfm" in models else "",
                    "coder": "qwen2.5-coder:14b" if "qwen2.5-coder:14b" in models else "",
                })
                fallback["capabilities_endpoint"] = "missing"
                fallback["capabilities_error"] = str(exc)[:200]
                return fallback
            return {"ok": False, "error": str(exc)[:200], "status_fallback": fallback}

    async def chat_local(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.3,
        role: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=timeout or self.timeout) as c:
                r = await c.post(
                    self._path("llm"),
                    headers=self._headers(),
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "role": role,
                        "timeout": timeout,
                    },
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}

    async def sync_corpus(self, source_type: str | None = None, limit: int = 1000) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(
                    self._path("corpus/sync"),
                    headers=self._headers(),
                    json={"source_type": source_type, "limit": limit},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}

    async def send_corpus(self, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(
                    self._path("corpus/receive"),
                    headers=self._headers(),
                    json={"chunks": chunks},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}

    async def send_proposals(self, proposals: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(
                    self._path("proposals"),
                    headers=self._headers(),
                    json={"proposals": proposals},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}

    async def get_proposals(self, limit: int = 50) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(
                    self._path("proposals"),
                    headers=self._headers(),
                    params={"limit": limit},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}


# ── route registration ───────────────────────────────────────────────────────

def _peer_proposals_path() -> Path:
    d = STORAGE_DIR / "peer_sync"
    d.mkdir(parents=True, exist_ok=True)
    return d / "proposals.jsonl"


def _save_peer_proposals(proposals: list[dict[str, Any]]) -> int:
    path = _peer_proposals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for p in proposals:
            entry = {"received_at": time.time(), "proposal": p, "source_instance": INSTANCE_ID}
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return len(proposals)


def _load_peer_proposals(limit: int = 50) -> list[dict[str, Any]]:
    path = _peer_proposals_path()
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue
            if limit and len(items) >= limit:
                break
    return items


def register_peer_routes(app: FastAPI, prefix: str = "/api/peer") -> None:
    """Mount peer endpoints on a FastAPI app (Omni or Enterprise)."""

    @app.get(f"{prefix}/health")
    async def peer_health() -> dict[str, Any]:
        return {
            "ok": True,
            "instance_id": INSTANCE_ID,
            "provider": settings.ai_provider,
            "model": settings.ollama_model,
            "timestamp": time.time(),
        }

    @app.get(f"{prefix}/status", dependencies=[Depends(peer_auth)])
    async def peer_status() -> dict[str, Any]:
        caps = await _peer_capabilities_payload()

        return {
            "ok": True,
            "instance_id": INSTANCE_ID,
            "provider": settings.ai_provider,
            "default_model": settings.ollama_model,
            "ollama_models": caps["ollama"]["models"],
            "role_models": caps["role_models"],
            "capabilities": caps["capabilities"],
            "brain": caps["brain"],
            "corpus": caps["corpus"],
            "storage": str(STORAGE_DIR),
            "latency_ms": caps["latency_ms"],
        }

    @app.get(f"{prefix}/capabilities", dependencies=[Depends(peer_auth)])
    async def peer_capabilities() -> dict[str, Any]:
        return await _peer_capabilities_payload()

    @app.post(f"{prefix}/call", dependencies=[Depends(peer_auth)])
    async def peer_call(request: Request) -> dict[str, Any]:
        body = await request.json()
        name = str(body.get("tool", "")).strip()
        args = body.get("args") or {}
        if not name:
            raise HTTPException(status_code=400, detail="missing tool name")
        if name not in PEER_TOOL_WHITELIST:
            raise HTTPException(status_code=403, detail=f"tool {name} is not peer-whitelisted")

        def _run() -> dict[str, Any]:
            return agent_tools.run_tool(name, args, allow_gated=False)

        try:
            result = await asyncio.to_thread(_run)
            return {"ok": True, "tool": name, "result": result}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)[:500])

    @app.post(f"{prefix}/llm", dependencies=[Depends(peer_auth)])
    async def peer_llm(request: Request) -> dict[str, Any]:
        body = await request.json()
        model = body.get("model")
        role = str(body.get("role") or "smart")
        messages = body.get("messages") or []
        temperature = float(body.get("temperature", 0.3))
        timeout = max(5.0, float(body.get("timeout") or PEER_LLM_TIMEOUT))
        if not messages:
            raise HTTPException(status_code=400, detail="messages required")

        system = ""
        prompt_parts: list[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system = content
            elif role == "user":
                prompt_parts.append(content)
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
        prompt = "\n\n".join(prompt_parts)

        try:
            from . import ai
            from .local_factory_config import resolve_role_model

            used_model = model or resolve_role_model(role)
            started = time.perf_counter()
            result = await asyncio.wait_for(
                ai.ask_ai(
                    prompt,
                    system=system or "You are SHIMS, a helpful local AI.",
                    model=used_model,
                    provider="ollama",
                ),
                timeout=timeout,
            )
            return {
                "ok": result.ok,
                "model": used_model,
                "role": role,
                "content": result.text,
                "provider": result.provider,
                "route": result.route,
                "error": result.error,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail=f"local LLM timed out after {timeout:g}s")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)[:500])

    @app.post(f"{prefix}/corpus/sync", dependencies=[Depends(peer_auth)])
    async def peer_corpus_sync(request: Request) -> dict[str, Any]:
        body = await request.json()
        source_type = body.get("source_type")
        limit = int(body.get("limit", 1000))
        try:
            from . import local_factory_corpus
            chunks = local_factory_corpus.export_corpus(source_type=source_type, limit=limit)
            return {"ok": True, "count": len(chunks), "chunks": chunks}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)[:500])

    @app.post(f"{prefix}/corpus/receive", dependencies=[Depends(peer_auth)])
    async def peer_corpus_receive(request: Request) -> dict[str, Any]:
        body = await request.json()
        chunks = body.get("chunks") or []
        if not chunks:
            return {"ok": True, "ingested": 0}
        try:
            from . import omni_brain
            ingested = 0
            for ch in chunks:
                text = ch.get("text") or ch.get("content")
                if not text:
                    continue
                omni_brain.ingest_knowledge(
                    text=text,
                    source_type=ch.get("source_type", "peer_sync"),
                    source_uri=ch.get("source_uri", f"peer://{INSTANCE_ID}"),
                    metadata=ch.get("metadata") or {},
                )
                ingested += 1
            return {"ok": True, "ingested": ingested}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)[:500])

    @app.post(f"{prefix}/proposals", dependencies=[Depends(peer_auth)])
    async def peer_proposals_receive(request: Request) -> dict[str, Any]:
        body = await request.json()
        proposals = body.get("proposals") or []
        if not isinstance(proposals, list):
            raise HTTPException(status_code=400, detail="proposals must be a list")
        ingested = _save_peer_proposals(proposals)
        return {"ok": True, "ingested": ingested}

    @app.get(f"{prefix}/proposals", dependencies=[Depends(peer_auth)])
    async def peer_proposals_get(limit: int = 50) -> dict[str, Any]:
        return {"ok": True, "proposals": _load_peer_proposals(limit=limit)}
