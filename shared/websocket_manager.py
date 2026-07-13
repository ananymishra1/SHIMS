from __future__ import annotations

import asyncio
import json
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect


class ConnectionManager:
    """Simple broadcast manager for WebSocket connections."""

    def __init__(self) -> None:
        self.active: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        text = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self.active)
        for client in clients:
            try:
                await client.send_text(text)
            except Exception:
                dead.append(client)
        if dead:
            async with self._lock:
                for d in dead:
                    if d in self.active:
                        self.active.remove(d)


# Global manager instance
manager = ConnectionManager()


async def broadcast_event(category: str, title: str, message: str, entity_type: str = "", entity_id: str = "", extra: dict[str, Any] | None = None) -> None:
    """Broadcast a live event to all connected WebSocket clients."""
    payload = {
        "type": "enterprise_event",
        "category": category,
        "title": title,
        "message": message,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "timestamp": json.dumps(None),
    }
    if extra:
        payload.update(extra)
    await manager.broadcast(payload)
