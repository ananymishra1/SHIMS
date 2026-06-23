"""Vision pipeline — describe images using the best available vision backend.

Backends (in preference order):
1. Anthropic Claude (cloud, highest quality)
2. Ollama vision model (local, e.g. llava, bakllava, moondream)
3. Google Gemini (cloud)

The module is intentionally small: given an image path/URL/base64, return a
compact text description that can be injected into the chat context.
"""
from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .ai import _stored_provider, clean_secret
from .config import settings

SUPPORTED_OLLAMA_VISION = ["llava", "llava-phi3", "bakllava", "moondream", "llama3.2-vision"]


def _guess_mime(path_or_url: str) -> str:
    mime, _ = mimetypes.guess_type(path_or_url)
    return mime or "image/jpeg"


def _load_image_bytes(source: str) -> tuple[bytes, str]:
    """Load image bytes from a local path, http URL, or base64 data URI."""
    if source.startswith("data:"):
        # data:image/png;base64,...
        header, _, b64 = source.partition(",")
        mime = header.split(";")[0].split(":")[1] if ";" in header else "image/jpeg"
        return base64.b64decode(b64), mime
    if source.startswith("http://") or source.startswith("https://"):
        with httpx.Client(timeout=30) as client:
            r = client.get(source)
            r.raise_for_status()
            return r.content, _guess_mime(source)
    p = Path(source)
    if not p.exists():
        raise FileNotFoundError(f"image not found: {source}")
    return p.read_bytes(), _guess_mime(str(p))


def _resize_if_huge(data: bytes, max_pixels: int = 2_000_000) -> bytes:
    """Downscale enormous images so we don't blow token budgets."""
    try:
        from PIL import Image
    except Exception:
        return data
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        if w * h <= max_pixels:
            return data
        scale = (max_pixels / (w * h)) ** 0.5
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        fmt = img.format or "JPEG"
        img.save(buf, format=fmt)
        return buf.getvalue()
    except Exception:
        return data


def _ollama_vision_model() -> str | None:
    """Pick a vision-capable Ollama model if one is available."""
    try:
        base = settings.ollama_base_url.rstrip("/")
        r = httpx.get(f"{base}/api/tags", timeout=10)
        r.raise_for_status()
        names = {m.get("name", "").split(":")[0] for m in r.json().get("models", [])}
        for candidate in SUPPORTED_OLLAMA_VISION:
            if candidate in names:
                # prefer exact match or latest tag
                for m in r.json().get("models", []):
                    if m.get("name", "").startswith(candidate):
                        return m["name"]
        return None
    except Exception:
        return None


def _describe_with_anthropic(data: bytes, mime: str, prompt: str, model: str | None = None) -> dict[str, Any]:
    stored = _stored_provider("anthropic")
    api_key = clean_secret((stored or {}).get("api_key") or getattr(settings, "anthropic_api_key", "") or "")
    if not api_key:
        return {"ok": False, "error": "no anthropic key", "backend": "anthropic"}
    used_model = model or (stored or {}).get("default_model") or getattr(settings, "anthropic_model", "claude-sonnet-4-6")
    b64 = base64.b64encode(data).decode("utf-8")
    payload = {
        "model": used_model,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text", "text": prompt or "Describe this image concisely."},
                ],
            }
        ],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    with httpx.Client(timeout=60) as client:
        r = client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    return {"ok": True, "backend": "anthropic", "model": used_model, "description": text.strip()}


def _describe_with_ollama(data: bytes, mime: str, prompt: str, model: str | None = None) -> dict[str, Any]:
    vision_model = model or _ollama_vision_model()
    if not vision_model:
        return {"ok": False, "error": "no ollama vision model available", "backend": "ollama"}
    b64 = base64.b64encode(data).decode("utf-8")
    base = settings.ollama_base_url.rstrip("/")
    payload = {
        "model": vision_model,
        "messages": [
            {
                "role": "user",
                "content": prompt or "Describe this image concisely.",
                "images": [b64],
            }
        ],
        "stream": False,
    }
    with httpx.Client(timeout=120) as client:
        r = client.post(f"{base}/api/chat", json=payload, timeout=120)
        r.raise_for_status()
        resp = r.json()
    return {"ok": True, "backend": "ollama", "model": vision_model, "description": (resp.get("message", {}).get("content", "")).strip()}


def describe_image(source: str, prompt: str = "Describe this image concisely.",
                   backend: str = "auto", model: str | None = None) -> dict[str, Any]:
    """Describe an image from path, URL, or base64 data URI.

    backend: 'auto' | 'anthropic' | 'ollama'
    """
    try:
        data, mime = _load_image_bytes(source)
        data = _resize_if_huge(data)
    except Exception as exc:
        return {"ok": False, "error": f"failed to load image: {exc}", "backend": backend}

    if backend == "anthropic":
        return _describe_with_anthropic(data, mime, prompt, model)
    if backend == "ollama":
        return _describe_with_ollama(data, mime, prompt, model)

    # auto: prefer anthropic if configured, else ollama
    stored = _stored_provider("anthropic")
    api_key = clean_secret((stored or {}).get("api_key") or getattr(settings, "anthropic_api_key", "") or "")
    if api_key:
        return _describe_with_anthropic(data, mime, prompt, model)
    return _describe_with_ollama(data, mime, prompt, model)
