#!/usr/bin/env python3
"""
SHIMS Desktop Bridge Client
============================
Used by the SHIMS backend to send commands to a user's desktop machine
running bridge_server.py.

Usage:
    from desktop_bridge.bridge_client import DesktopBridge
    bridge = DesktopBridge("ws://localhost:9876/bridge", token="secret")
    result = await bridge.shell("dir")
    screenshot = await bridge.screenshot()
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

try:
    import websockets
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "websockets is required. Install with: pip install websockets"
    ) from exc


class DesktopBridge:
    """Async client for SHIMS Desktop Bridge."""

    def __init__(self, uri: str, token: str, timeout: float = 30.0):
        self.uri = uri
        self.token = token
        self.timeout = timeout

    async def _send(self, cmd: dict[str, Any]) -> dict[str, Any]:
        try:
            async with websockets.connect(self.uri, open_timeout=self.timeout) as ws:  # type: ignore[no-untyped-call]
                cmd["token"] = self.token
                await ws.send(json.dumps(cmd))
                raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                return json.loads(raw)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Desktop bridge timeout"}
        except Exception as exc:
            return {"ok": False, "error": f"Bridge connection failed: {exc}"}

    async def ping(self) -> dict[str, Any]:
        return await self._send({"type": "ping"})

    async def shell(self, command: str, cwd: str | None = None, timeout: int = 60) -> dict[str, Any]:
        return await self._send({"type": "shell", "command": command, "cwd": cwd, "timeout": timeout})

    async def screenshot(self) -> dict[str, Any]:
        return await self._send({"type": "screenshot"})

    async def read_file(self, path: str) -> dict[str, Any]:
        return await self._send({"type": "read_file", "path": path})

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        return await self._send({"type": "write_file", "path": path, "content": content})

    async def system_info(self) -> dict[str, Any]:
        return await self._send({"type": "system_info"})

    async def find_file(self, name: str, root: str = "C:\\\\") -> dict[str, Any]:
        return await self._send({"type": "find_file", "name": name, "root": root})

    async def save_screenshot(self, out_dir: str | Path = ".") -> Path | None:
        """Take a screenshot via the bridge and save it locally."""
        res = await self.screenshot()
        if not res.get("ok"):
            return None
        result = res.get("result", {})
        if not result.get("ok"):
            return None
        data = base64.b64decode(result["data"])
        dest = Path(out_dir) / f"bridge_screenshot_{int(asyncio.get_event_loop().time())}.png"
        dest.write_bytes(data)
        return dest


# Simple CLI for testing
async def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", default="ws://localhost:9876/bridge")
    parser.add_argument("--token", default="shims-desktop-bridge-token")
    parser.add_argument("--command", default="ping")
    parser.add_argument("--arg", default="")
    args = parser.parse_args()

    bridge = DesktopBridge(args.uri, args.token)
    if args.command == "ping":
        print(json.dumps(await bridge.ping(), indent=2))
    elif args.command == "shell":
        print(json.dumps(await bridge.shell(args.arg), indent=2))
    elif args.command == "screenshot":
        path = await bridge.save_screenshot()
        print("Saved screenshot to:", path)
    elif args.command == "info":
        print(json.dumps(await bridge.system_info(), indent=2))
    elif args.command == "find":
        print(json.dumps(await bridge.find_file(args.arg), indent=2))
    else:
        print("Unknown command:", args.command)


if __name__ == "__main__":
    asyncio.run(_cli())
