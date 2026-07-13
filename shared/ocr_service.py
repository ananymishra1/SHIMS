"""OCR — extract text from images (and image-only PDFs), offline-first.

Primary engine on most platforms: ``rapidocr-onnxruntime`` (pure pip, ONNX,
bundles its models, no system Tesseract, runs fully offline). On Windows this
engine is disabled by default because the bundled ONNX runtime can trigger an
access-violation crash on import; set ``SHIMS_ENABLE_RAPIDOCR=1`` to override.

Fallback: a vision-capable Ollama model (e.g. ``llava``/``qwen2.5-vl``) if one
is installed. If neither is available, returns a clear, actionable status
instead of failing.
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from typing import Any

# rapidocr-onnxruntime is known to crash on import on some Windows configurations
# (access violation in onnxruntime.dll). Disable it by default on Windows unless
# explicitly requested.
_RAPID_DISABLED = (
    sys.platform == "win32" and os.environ.get("SHIMS_ENABLE_RAPIDOCR") not in {"1", "true", "yes"}
)
_RAPID = None          # cached RapidOCR engine
_RAPID_TRIED = False


def _engine():
    global _RAPID, _RAPID_TRIED
    if _RAPID_DISABLED:
        return None
    if _RAPID is None and not _RAPID_TRIED:
        _RAPID_TRIED = True
        try:
            from rapidocr_onnxruntime import RapidOCR
            _RAPID = RapidOCR()
        except Exception:
            _RAPID = None
    return _RAPID


def engine_name() -> str:
    return "disabled-on-windows" if _RAPID_DISABLED else "rapidocr-onnxruntime"


def ocr_available() -> bool:
    return _engine() is not None


def ocr_image_bytes(data: bytes) -> dict[str, Any]:
    """Run OCR over raw image bytes. Returns {ok, text, blocks, engine}."""
    engine = _engine()
    if engine is not None:
        import numpy as np
        from PIL import Image
        import io
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            result, _elapsed = engine(np.array(img))
        except Exception as exc:
            return {"ok": False, "engine": "rapidocr", "error": str(exc)[:200]}
        blocks = []
        lines = []
        for item in (result or []):
            # rapidocr returns [box, text, score]
            try:
                box, text, score = item[0], item[1], float(item[2])
            except Exception:
                continue
            blocks.append({"text": text, "confidence": round(score, 3)})
            lines.append(text)
        return {"ok": True, "engine": "rapidocr", "text": "\n".join(lines), "blocks": blocks}

    # Fallback: vision-capable Ollama model, if present.
    vision = _ollama_vision_ocr(data)
    if vision is not None:
        return vision

    return {
        "ok": False,
        "engine": "none",
        "error": "No OCR engine available.",
        "hint": (
            "On Windows RapidOCR is disabled by default (crash risk). "
            "Set SHIMS_ENABLE_RAPIDOCR=1 to re-enable, or pull an Ollama vision model: ollama pull llava"
        ),
    }


def ocr_image_file(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": "file not found"}
    return ocr_image_bytes(p.read_bytes())


def _ollama_vision_ocr(data: bytes) -> dict[str, Any] | None:
    """Try a local Ollama vision model for OCR. Returns None if unavailable."""
    import os
    import httpx
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        with httpx.Client(timeout=10) as c:
            tags = c.get(f"{base}/api/tags")
            tags.raise_for_status()
            names = [m.get("name", "") for m in tags.json().get("models", [])]
    except Exception:
        return None
    if not names:
        return None
    model = os.getenv("SHIMS_VISION_MODEL", "")
    if not model:
        model = next((n for n in names if any(v in n.lower() for v in ("llava", "vl", "vision", "minicpm"))), "")
    if not model:
        return None
    try:
        b64 = base64.b64encode(data).decode("ascii")
        with httpx.Client(timeout=120) as c:
            r = c.post(f"{base}/api/generate", json={
                "model": model,
                "prompt": "Extract ALL text from this image verbatim. Output only the text.",
                "images": [b64], "stream": False,
            })
            r.raise_for_status()
            text = (r.json().get("response") or "").strip()
        return {"ok": bool(text), "engine": f"ollama:{model}", "text": text, "blocks": []}
    except Exception as exc:
        return {"ok": False, "engine": f"ollama:{model}", "error": str(exc)[:200]}
