"""Local, offline document ingestion for sensitive files.

Uses:
- pypdf for text-based PDFs
- python-docx / openpyxl for Office docs
- Ollama vision model for scanned PDFs / images
- omni-brain for storage (no cloud)
"""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .omni_brain import ingest_knowledge


OLLAMA_BASE = settings.ollama_base_url.rstrip("/")
_VISION_MODELS = ["moondream", "llava", "llava-phi3", "bakllava", "llama3.2-vision"]


def _vision_model() -> str | None:
    """Pick the first available Ollama vision model."""
    try:
        r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=10)
        r.raise_for_status()
        names = {m.get("name", "").split(":")[0] for m in r.json().get("models", [])}
        for candidate in _VISION_MODELS:
            if candidate in names:
                # return exact tag if present
                for m in r.json().get("models", []):
                    if m.get("name", "").startswith(candidate):
                        return m["name"]
    except Exception:
        pass
    return None


def _ollama_vision_prompt() -> str:
    return (
        "You are an OCR engine. Extract every readable word and number from this image. "
        "Preserve line breaks and tables as best as possible. Return ONLY the extracted text, no commentary."
    )


def _describe_with_ollama(image_bytes: bytes, model: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": _ollama_vision_prompt(),
                "images": [b64],
            }
        ],
        "stream": False,
    }
    r = httpx.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=180)
    r.raise_for_status()
    data = r.json()
    return data.get("message", {}).get("content", "")


def _render_pdf_page_to_image(pdf_path: Path, page_num: int = 0, dpi: int = 150) -> bytes:
    try:
        import fitz
    except Exception as exc:
        raise RuntimeError("PyMuPDF not installed") from exc
    doc = fitz.open(str(pdf_path))
    page = doc.load_page(page_num)
    pix = page.get_pixmap(dpi=dpi)
    img = __import__("PIL").Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def extract_text_local(path: str | Path, prefer_ocr: bool = False) -> dict[str, Any]:
    """Extract text from a file using only local tools."""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "file not found"}
    ext = p.suffix.lower()
    text = ""
    method = ""

    # PDF: try text extraction first, fall back to OCR if empty or prefer_ocr.
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            parts = []
            for page in reader.pages:
                t = page.extract_text() or ""
                parts.append(t)
            text = "\n".join(parts)
            method = "pypdf"
        except Exception:
            text = ""
        if not text.strip() or prefer_ocr:
            model = _vision_model()
            if not model:
                return {"ok": False, "error": "No local Ollama vision model available for OCR"}
            try:
                import fitz
                doc = fitz.open(str(p))
                ocr_parts = []
                for i in range(doc.page_count):
                    img_bytes = _render_pdf_page_to_image(p, i)
                    ocr_parts.append(_describe_with_ollama(img_bytes, model))
                text = "\n\n".join(ocr_parts)
                method = "ollama_ocr"
            except Exception as exc:
                return {"ok": False, "error": f"OCR failed: {exc}"}

    elif ext == ".docx":
        try:
            import docx
            d = docx.Document(str(p))
            text = "\n".join(para.text for para in d.paragraphs)
            method = "docx"
        except Exception as exc:
            return {"ok": False, "error": f"docx extraction failed: {exc}"}

    elif ext in {".xlsx", ".xls"}:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(p), data_only=True)
            parts = []
            for sheet in wb.worksheets:
                rows = []
                for row in sheet.iter_rows(values_only=True):
                    rows.append(" | ".join(str(c) if c is not None else "" for c in row))
                parts.append(f"Sheet: {sheet.title}\n" + "\n".join(rows))
            text = "\n\n".join(parts)
            method = "xlsx"
        except Exception as exc:
            return {"ok": False, "error": f"xlsx extraction failed: {exc}"}

    elif ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
        model = _vision_model()
        if not model:
            return {"ok": False, "error": "No local Ollama vision model available"}
        try:
            text = _describe_with_ollama(p.read_bytes(), model)
            method = "ollama_ocr"
        except Exception as exc:
            return {"ok": False, "error": f"image OCR failed: {exc}"}

    elif ext == ".csv":
        try:
            import csv
            rows: list[list[str]] = []
            with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                reader = csv.reader(fh)
                for row in reader:
                    rows.append(row)
            text = "\n".join(" | ".join(c for c in row) for row in rows)
            method = "csv"
        except Exception as exc:
            return {"ok": False, "error": f"csv extraction failed: {exc}"}

    elif ext in {".txt", ".md", ".json", ".py", ".js", ".html", ".css", ".xml"}:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            method = "text"
        except Exception as exc:
            return {"ok": False, "error": f"text read failed: {exc}"}

    else:
        return {"ok": False, "error": f"unsupported extension {ext}"}

    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not text:
        return {"ok": False, "error": "no text extracted"}
    return {"ok": True, "text": text, "method": method, "chars": len(text)}


def ingest_file_local(path: str | Path, title: str | None = None, tags: list[str] | None = None, prefer_ocr: bool = False) -> dict[str, Any]:
    """Extract text locally and store in omni-brain."""
    p = Path(path)
    extracted = extract_text_local(p, prefer_ocr=prefer_ocr)
    if not extracted["ok"]:
        return extracted
    res = ingest_knowledge(
        title=title or p.stem,
        text=extracted["text"],
        source_type="local_inbox",
        source_uri=str(p.resolve()),
        tags=tags or ["inbox", p.suffix.lower().lstrip(".")],
        importance=1.0,
    )
    res["method"] = extracted.get("method")
    return res


def ingest_directory_local(dir_path: str | Path, prefer_ocr: bool = False) -> dict[str, Any]:
    """Ingest every supported file in a directory using only local tools."""
    p = Path(dir_path)
    if not p.is_dir():
        return {"ok": False, "error": "not a directory"}
    files = [f for f in p.iterdir() if f.is_file()]
    results: list[dict[str, Any]] = []
    for f in files:
        res = ingest_file_local(f, prefer_ocr=prefer_ocr)
        res["file"] = str(f)
        results.append(res)
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "dir": str(p), "total": len(results), "ingested": ok_count, "results": results}
