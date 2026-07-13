"""Standalone media generation tools for the agent loop.

These are synchronous so they can be called from `shared/agent_tools.py` without
needing an async event loop. Pollinations.ai is the default because it requires
no API key and no heavy local dependencies.
"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .config import GENERATED_DIR

GENERATED_DIR.mkdir(parents=True, exist_ok=True)


def _safe_filename(prompt: str, ext: str = "png") -> str:
    h = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:10]
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in prompt[:40]).rstrip("_")
    return f"{slug}_{h}.{ext}"


def generate_image_pollinations(prompt: str, width: int = 1024, height: int = 1024) -> dict[str, Any]:
    """Generate an image via Pollinations.ai (free, no key)."""
    try:
        encoded = urllib.parse.quote(prompt[:1000])
        seed = abs(hash(prompt)) % 99999
        url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true&seed={seed}"
        req = urllib.request.Request(url, headers={"User-Agent": "SHIMS-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        filename = _safe_filename(prompt, "png")
        path = GENERATED_DIR / filename
        path.write_bytes(data)
        file_url = f"/media/files/image/{filename}"
        return {
            "ok": True,
            "provider": "pollinations",
            "type": "image",
            "title": prompt[:80],
            "filename": filename,
            "url": file_url,
            "file_url": file_url,
            "download_url": file_url,
            "path": str(path),
        }
    except Exception as exc:
        return {"ok": False, "provider": "pollinations", "error": str(exc)[:200]}


def generate_image(
    prompt: str,
    backend: str = "auto",
    width: int = 1024,
    height: int = 1024,
) -> dict[str, Any]:
    """Generate an image using the best available backend.

    Backends: auto, pollinations, openai, diffusers, qwen, stable-diffusion
    """
    backend = (backend or "auto").lower()
    # For now, only pollinations is guaranteed sync + no key. Others can be added.
    if backend in {"auto", "pollinations"}:
        return generate_image_pollinations(prompt, width=width, height=height)
    return {"ok": False, "error": f"backend '{backend}' not available in sync tool mode; try pollinations or use /media/generate endpoint"}


def generate_video_placeholder(prompt: str) -> dict[str, Any]:
    """Placeholder for video generation.

    Real video generation is provider-dependent and usually async/expensive.
    The agent tool returns a clear note so the user knows to use the endpoint.
    """
    return {
        "ok": False,
        "error": "Video generation is not exposed as a sync tool. Use /media/generate with a video backend, or ask me to create a plan that calls the media endpoint.",
    }
