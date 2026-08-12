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

from .config import GENERATED_DIR, ROOT_DIR

GENERATED_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR = ROOT_DIR / "data" / "media" / "images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)


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
        path = IMAGE_DIR / filename
        path.write_bytes(data)
        file_url = f"/media/files/images/{filename}"
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
    queue_if_busy: bool = True,
) -> dict[str, Any]:
    """Generate an image using the best available backend.

    Backends: auto, pollinations, comfyui, openai, diffusers, qwen, stable-diffusion.

    With ``backend="comfyui"`` (or ``auto`` when the compute orchestrator reports
    ComfyUI available but the machine is currently busy), the job is queued for
    downtime generation via the compute orchestrator and ``{"queued": job_id}``
    is returned instead of blocking on a busy GPU.
    """
    backend = (backend or "auto").lower()
    if backend == "comfyui" or (backend == "auto" and queue_if_busy and _orchestrator_wants_queue()):
        return _queue_orchestrator_image(prompt, width=width, height=height)
    if backend in {"auto", "pollinations"}:
        return generate_image_pollinations(prompt, width=width, height=height)
    return {"ok": False, "error": f"backend '{backend}' not available in sync tool mode; try pollinations/comfyui or use /media/generate endpoint"}


def _orchestrator_wants_queue() -> bool:
    """True when ComfyUI is launchable but the machine is too busy to run it now."""
    try:
        from .compute_orchestrator import get_orchestrator
        orch = get_orchestrator()
        return orch.comfy_available() and not orch.is_idle()
    except Exception:
        return False


def _queue_orchestrator_image(prompt: str, width: int = 1024, height: int = 1024) -> dict[str, Any]:
    """Enqueue a downtime ComfyUI job through the compute orchestrator."""
    try:
        from .compute_orchestrator import get_orchestrator
        job_id = get_orchestrator().queue_image_job(prompt, width=width, height=height)
        return {
            "ok": True,
            "provider": "comfyui",
            "type": "image",
            "queued": job_id,
            "title": prompt[:80],
            "note": "Queued for downtime generation; check /api/orchestrator/image-job/" + job_id,
        }
    except Exception as exc:
        return {"ok": False, "provider": "comfyui", "error": str(exc)[:200]}


def generate_video_placeholder(prompt: str) -> dict[str, Any]:
    """Placeholder for video generation.

    Real video generation is provider-dependent and usually async/expensive.
    The agent tool returns a clear note so the user knows to use the endpoint.
    """
    return {
        "ok": False,
        "error": "Video generation is not exposed as a sync tool. Use /media/generate with a video backend, or ask me to create a plan that calls the media endpoint.",
    }
