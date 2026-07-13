#!/usr/bin/env python3
"""
SHIMS Desktop Bridge Server
============================
Runs on the user's Windows/Mac/Linux machine. Accepts authenticated commands
from SHIMS backend via WebSocket and executes them locally.

Capabilities:
- Shell command execution with live stdout/stderr streaming
- Desktop screenshot capture
- File system read/write/list
- Process list / kill
- System info (CPU, memory, disk)

Usage:
    python bridge_server.py --token my-secret-token

The SHIMS backend connects to ws://localhost:9876/bridge and sends JSON
commands. The token must match the one configured in SHIMS_BRIDGE_TOKEN.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    import websockets
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "websockets is required. Install with: pip install websockets"
    ) from exc


# Non-WebSocket TCP probes (health checks, port scanners, stale HTTP clients)
# spam the bridge port and websockets logs a full handshake traceback at ERROR
# level. Those are harmless; raise the log level to CRITICAL so they don't
# drown out real errors.
logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
logging.getLogger("websockets.protocol").setLevel(logging.CRITICAL)


DEFAULT_PORT = 9876
THIS_DIR = Path(__file__).resolve().parent


def _b64_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _take_screenshot() -> dict[str, Any]:
    system = platform.system()
    try:
        if system == "Windows":
            from PIL import ImageGrab

            img = ImageGrab.grab()
            buf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            img.save(buf.name, format="PNG")
            buf.close()
            data = _b64_image(Path(buf.name))
            os.unlink(buf.name)
            return {"ok": True, "format": "png", "data": data}
        if system == "Darwin":
            out = Path(tempfile.mktemp(suffix=".png"))
            subprocess.run(["screencapture", "-x", str(out)], check=True)
            data = _b64_image(out)
            out.unlink(missing_ok=True)
            return {"ok": True, "format": "png", "data": data}
        # Linux
        out = Path(tempfile.mktemp(suffix=".png"))
        tools = [["gnome-screenshot", "-f", str(out)], ["import", "-window", "root", str(out)]]
        for cmd in tools:
            if shutil.which(cmd[0]):
                subprocess.run(cmd, check=True)
                data = _b64_image(out)
                out.unlink(missing_ok=True)
                return {"ok": True, "format": "png", "data": data}
        return {"ok": False, "error": "No screenshot tool found (gnome-screenshot or ImageMagick 'import')"}
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": f"Screenshot failed: {exc}"}


def _run_shell(command: str, cwd: str | None = None, timeout: int = 60) -> dict[str, Any]:
    try:
        shell = platform.system() == "Windows"
        proc = subprocess.run(
            command,
            shell=shell,
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Command timed out after {timeout}s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _read_file(path: str) -> dict[str, Any]:
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return {"ok": False, "error": "File not found"}
        if p.is_dir():
            entries = []
            for child in p.iterdir():
                try:
                    stat = child.stat()
                    entries.append({
                        "name": child.name,
                        "is_dir": child.is_dir(),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    })
                except OSError:
                    continue
            return {"ok": True, "type": "directory", "entries": entries}
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "type": "file", "content": content, "size": len(content.encode("utf-8"))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _write_file(path: str, content: str) -> dict[str, Any]:
    try:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "size": len(content.encode("utf-8"))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _system_info() -> dict[str, Any]:
    return {
        "ok": True,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "hostname": platform.node(),
        "cwd": os.getcwd(),
        "home": str(Path.home()),
    }


def _find_file(name: str, root: str = "C:\\\\") -> dict[str, Any]:
    system = platform.system()
    try:
        if system == "Windows":
            cmd = f'where /r "{root}" {name}'
        else:
            cmd = f'find "{root}" -name "{name}" 2>/dev/null | head -20'
        result = _run_shell(cmd, timeout=120)
        return {"ok": True, "matches": [line for line in (result.get("stdout") or "").splitlines() if line.strip()]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _handle_command(cmd: dict[str, Any]) -> dict[str, Any]:
    t = cmd.get("type")
    if t == "ping":
        return {"ok": True, "type": "pong", "time": time.time()}
    if t == "shell":
        return {"ok": True, "type": "shell_result", "result": _run_shell(cmd.get("command", ""), cmd.get("cwd"), cmd.get("timeout", 60))}
    if t == "screenshot":
        return {"ok": True, "type": "screenshot_result", "result": _take_screenshot()}
    if t == "read_file":
        return {"ok": True, "type": "read_file_result", "result": _read_file(cmd.get("path", ""))}
    if t == "write_file":
        return {"ok": True, "type": "write_file_result", "result": _write_file(cmd.get("path", ""), cmd.get("content", ""))}
    if t == "system_info":
        return {"ok": True, "type": "system_info_result", "result": _system_info()}
    if t == "find_file":
        return {"ok": True, "type": "find_file_result", "result": _find_file(cmd.get("name", ""), cmd.get("root", "C:\\\\"))}
    return {"ok": False, "error": f"Unknown command type: {t}"}


async def _bridge_handler(websocket, token: str):  # type: ignore[no-untyped-def]
    remote = websocket.remote_address
    print(f"[bridge] Connection from {remote}")
    try:
        async for message in websocket:
            try:
                cmd = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"ok": False, "error": "Invalid JSON"}))
                continue
            if cmd.get("token") != token:
                await websocket.send(json.dumps({"ok": False, "error": "Invalid token"}))
                continue
            response = await _handle_command(cmd)
            await websocket.send(json.dumps(response))
    except websockets.exceptions.ConnectionClosed:
        print(f"[bridge] Disconnected from {remote}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SHIMS Desktop Bridge Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to bind (default: {DEFAULT_PORT})")
    parser.add_argument("--token", default=os.environ.get("SHIMS_BRIDGE_TOKEN", "shims-desktop-bridge-token"), help="Auth token")
    args = parser.parse_args()

    if args.token == "shims-desktop-bridge-token":
        print("[WARNING] Using default token. Set --token or SHIMS_BRIDGE_TOKEN for security.")

    bound_handler = lambda ws: _bridge_handler(ws, args.token)  # noqa: E731

    async def run_server() -> None:
        async with websockets.serve(bound_handler, args.host, args.port):  # type: ignore[no-untyped-call]
            print(f"[bridge] SHIMS Desktop Bridge listening on ws://{args.host}:{args.port}")
            print(f"[bridge] Token: {'*' * len(args.token)}")
            await asyncio.Future()  # run forever

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
