"""Native multimodal message formatting for cloud providers.

Converts image sources (data URIs, URLs, file paths) into provider-specific content blocks
so SHIMS can send images natively to Anthropic and OpenAI instead of relying on pre-captioning.
"""
from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any


def _extract_data_uri(src: str) -> tuple[str, str] | None:
    m = re.match(r"data:(.+?);base64,(.+)", src)
    if m:
        return m.group(1), m.group(2)
    return None


def _load_image_base64(src: str) -> tuple[str, str] | None:
    """Return (mime_type, base64_data) for an image source, or None."""
    if src.startswith("http://") or src.startswith("https://"):
        # Try to fetch if requests is available, otherwise return URL as-is
        try:
            import requests
            r = requests.get(src, timeout=20)
            r.raise_for_status()
            mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
            return mime, base64.b64encode(r.content).decode("utf-8")
        except Exception:
            # For OpenAI we can pass the URL directly; for Anthropic we need base64
            return None
    if src.startswith("data:"):
        parsed = _extract_data_uri(src)
        if parsed:
            return parsed
        return None
    p = Path(src)
    if p.exists():
        mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
        return mime, base64.b64encode(p.read_bytes()).decode("utf-8")
    return None


def build_anthropic_content(message: str, images: list[str]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for src in images[:4]:
        loaded = _load_image_base64(src)
        if loaded:
            mime, data = loaded
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            })
        elif src.startswith("http"):
            # Anthropic requires base64; skip remote URLs we can't fetch
            content.append({"type": "text", "text": f"[Image at {src}]"})
    if message:
        content.append({"type": "text", "text": message})
    return content


def build_openai_content(message: str, images: list[str]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for src in images[:4]:
        if src.startswith("http"):
            content.append({"type": "image_url", "image_url": {"url": src}})
        else:
            loaded = _load_image_base64(src)
            if loaded:
                mime, data = loaded
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
            else:
                content.append({"type": "text", "text": f"[Image at {src}]"})
    if message:
        content.append({"type": "text", "text": message})
    return content


def build_user_message(message: str, images: list[str], provider: str) -> dict[str, Any]:
    """Build a user message dict with native multimodal content if supported."""
    provider = provider.lower()
    if not images:
        return {"role": "user", "content": message}
    if provider == "anthropic":
        return {"role": "user", "content": build_anthropic_content(message, images)}
    if provider in {"openai", "gemini", "deepseek", "kimi"}:
        # OpenAI-compatible vision format is widely supported
        return {"role": "user", "content": build_openai_content(message, images)}
    # Ollama / local: plain text (vision description is handled upstream)
    return {"role": "user", "content": message}
