"""Public chat/agent-stream routes.

These routes were extracted from backend/app/main.py as the first step in
breaking up the monolithic route file. They all funnel through
`_safe_brain_stream()` in main.py.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

# NOTE: This module imports runtime helpers from backend.app.main. To avoid
# circular imports, always import ``backend.app.main`` first; it includes this
# router at the bottom of the file after ChatRequest and the helpers are defined.
from backend.app.main import (
    ChatRequest,
    _agentic_intent,
    _safe_brain_stream,
)

router = APIRouter()


@router.post("/api/chat")
async def api_chat(req: ChatRequest) -> dict[str, Any]:
    """Backward-compatible non-streaming chat route for older Omni clients."""
    import json as _json
    answer_parts: list[str] = []
    done: dict[str, Any] = {}
    search_result: dict[str, Any] | None = None
    media_result: dict[str, Any] | None = None
    async for raw in _safe_brain_stream(req):
        try:
            event = _json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if event.get("type") == "token":
            answer_parts.append(str(event.get("content") or ""))
        elif event.get("type") == "search":
            search_result = event.get("search_result")
        elif event.get("type") == "media":
            media_result = event.get("media_result")
        elif event.get("type") == "done":
            done = event
    answer = "".join(answer_parts).strip()
    return {
        "ok": True,
        "independent": True,
        "answer": answer,
        "provider": done.get("provider") or req.provider or "auto",
        "model": done.get("model") or req.model or "",
        "route": done.get("route") or "unknown",
        "search_result": search_result or done.get("search_result"),
        "media_result": media_result or done.get("media_result"),
        **{k: done.get(k) for k in ("trust", "evidence", "confidence", "query_plan", "action_id", "ledger_hash") if k in done},
    }


@router.post("/brain/turn")
async def brain_turn(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(_safe_brain_stream(req), media_type="application/x-ndjson")


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(_safe_brain_stream(req), media_type="application/x-ndjson")


@router.post("/chat/converse")
async def chat_converse(req: ChatRequest) -> StreamingResponse:
    req.conversation_mode = True
    return StreamingResponse(_safe_brain_stream(req), media_type="application/x-ndjson")


@router.post("/api/v11/chat/turn")
async def api_v11_chat_turn(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(_safe_brain_stream(req), media_type="application/x-ndjson")


@router.websocket("/converse/ws")
async def converse_ws(ws: WebSocket) -> None:
    import json as _json
    await ws.accept()
    try:
        while True:
            data = await ws.receive_text()
            try:
                payload = _json.loads(data)
            except Exception:
                payload = {"message": data}
            async for chunk in _safe_brain_stream(ChatRequest(**payload)):
                await ws.send_text(chunk.decode("utf-8"))
    except WebSocketDisconnect:
        return


@router.post("/agent/run")
async def agent_run(req: ChatRequest) -> StreamingResponse:
    """Force the agentic loop for this message (same stream as chat)."""
    req.agent_mode = True
    if not _agentic_intent(req.message):
        req.message = "/do " + (req.message or "")
    return StreamingResponse(_safe_brain_stream(req), media_type="application/x-ndjson")
