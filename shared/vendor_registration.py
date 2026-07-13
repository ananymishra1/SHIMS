from __future__ import annotations

import io
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .database import db
from .ocr_service import ocr_image_bytes


def _extract_text_from_file(file_bytes: bytes, filename: str) -> dict[str, Any]:
    """Extract text from a PDF, image, or plain-text upload."""
    name = (filename or "").lower()
    text_exts = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm"}
    if any(name.endswith(ext) for ext in text_exts):
        try:
            return {"ok": True, "text": file_bytes.decode("utf-8", errors="ignore"), "engine": "text"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200], "text": ""}
    if name.endswith(".pdf"):
        try:
            import fitz  # type: ignore
        except Exception as exc:
            return {"ok": False, "error": f"PyMuPDF not available: {exc}", "text": ""}
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            texts = []
            for page in doc:
                # Prefer embedded text for digitally-generated PDFs
                page_text = page.get_text()
                if page_text.strip():
                    texts.append(page_text)
                else:
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    img_bytes = pix.tobytes("png")
                    res = ocr_image_bytes(img_bytes)
                    if res.get("ok"):
                        texts.append(res.get("text", ""))
            return {"ok": bool(texts), "text": "\n".join(texts), "pages": len(doc), "engine": "fitz+ocr"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200], "text": ""}
    # Images and anything else fall back to OCR/vision
    return ocr_image_bytes(file_bytes)


def _find_gstin(text: str) -> str:
    for m in re.finditer(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", text.upper()):
        return m.group(0)
    return ""


def _find_pin(text: str) -> str:
    for m in re.finditer(r"\b[1-9][0-9]{5}\b", text):
        return m.group(0)
    return ""


def _find_drug_license(text: str) -> str:
    for pat in [
        r"(?:DL|Drug License|License\s*No)[.\s:]*([A-Z0-9/\-]{5,})",
        r"\b([A-Z]{1,2}/?\d{2,}[A-Z]?/?\d{4,})\b",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1).strip()
    return ""


def _find_fssai(text: str) -> str:
    m = re.search(r"\b1[0-9]{13}\b|\bFSSAI\s*(?:LIC\.?|LICENSE|NO\.?)?[:\s]*([0-9]{14})\b", text, re.I)
    if m:
        return m.group(1) if m.group(1) else m.group(0)
    return ""


def _first_match(patterns: list[str], text: str) -> str:
    for pat in patterns:
        m = re.search(pat, text, re.I | re.DOTALL)
        if m:
            return " ".join(m.group(1).split()) if m.groups() else m.group(0)
    return ""


def parse_gst_certificate(text: str) -> dict[str, Any]:
    gstin = _find_gstin(text)
    legal = _first_match([
        r"Legal Name of Business\s*[:.]\s*([^\n]{3,})",
        r"Name\s*[:.]\s*([^\n]{3,})",
    ], text)
    trade = _first_match([
        r"Trade Name\s*[:.]\s*([^\n]{3,})",
    ], text)
    address = _first_match([
        r"Address\s*[:.]\s*([^\n]{10,})",
        r"Principal Place of Business\s*[:.]\s*([^\n]{10,})",
    ], text)
    pin = _find_pin(text)
    state_code = gstin[:2] if gstin else ""
    return {
        "gstin": gstin,
        "legal_name": legal,
        "trade_name": trade or legal,
        "address": address,
        "pin": pin,
        "state_code": state_code,
    }


def parse_drug_license(text: str) -> dict[str, Any]:
    lic = _find_drug_license(text)
    firm = _first_match([
        r"(?:Name of the Firm|Firm Name|Licensee|Name of Licensee)\s*[:.]\s*([^\n]{3,})",
        r"M/s\.?\s*[:.]?\s*([^\n]{3,})",
        r"(?:Name|Firm)\s*[:.]\s*([^\n]{3,})",
    ], text)
    address = _first_match([
        r"Address\s*[:.]\s*([^\n]{10,})",
        r"Premises\s*[:.]\s*([^\n]{10,})",
    ], text)
    valid = _first_match([
        r"Valid(?: up)?(?: to)?\s*[:.]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"Expiry\s*[:.]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"Valid\s*Till\s*[:.]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    ], text)
    return {
        "drug_license_no": lic,
        "firm_name": firm,
        "address": address,
        "valid_until": valid,
    }


def _merge_docs(existing: dict[str, Any] | None, new_doc: dict[str, Any]) -> dict[str, Any]:
    docs = dict(existing or {})
    docs.setdefault("files", [])
    docs["files"].append(new_doc)
    return docs


def upsert_vendor(data: dict[str, Any], uploaded_files: list[tuple[str, bytes, str]] | None = None) -> dict[str, Any]:
    """Create or update a vendor from extracted/document data."""
    gstin = (data.get("gstin") or "").strip().upper()
    name = (data.get("name") or data.get("legal_name") or data.get("trade_name") or "Unknown").strip()
    trade_name = (data.get("trade_name") or "").strip()
    state_code = (data.get("state_code") or (gstin[:2] if gstin else "")).strip()
    pin = (data.get("pin") or "").strip()
    address = (data.get("address") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    contact_person = (data.get("contact_person") or "").strip()
    drug_license_no = (data.get("drug_license_no") or "").strip()
    fssai_no = (data.get("fssai_no") or "").strip()

    existing = db.one('SELECT id, documents_json FROM vendors WHERE gstin=?', (gstin,)) if gstin else db.one('SELECT id, documents_json FROM vendors WHERE name=?', (name,))
    now = datetime.now().isoformat()
    docs = json.loads(existing["documents_json"]) if existing and existing.get("documents_json") else {}
    if uploaded_files:
        for label, content, fname in uploaded_files:
            docs = _merge_docs(docs, {"label": label, "filename": fname, "uploaded_at": now})
    docs_json = json.dumps(docs, ensure_ascii=False) if docs else None

    if existing:
        db.execute(
            'UPDATE vendors SET name=?, trade_name=?, gstin=?, state_code=?, pin=?, address=?, phone=?, email=?, contact_person=?, drug_license_no=?, fssai_no=?, documents_json=?, updated_at=? WHERE id=?',
            (name, trade_name, gstin or None, state_code, pin, address, phone, email, contact_person, drug_license_no, fssai_no, docs_json, now, existing["id"])
        )
        return {"ok": True, "id": existing["id"], "created": False, "gstin": gstin}
    vid = db.execute(
        'INSERT INTO vendors(name, trade_name, gstin, state_code, pin, address, phone, email, contact_person, drug_license_no, fssai_no, documents_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (name, trade_name, gstin or None, state_code, pin, address, phone, email, contact_person, drug_license_no, fssai_no, docs_json, now)
    )
    return {"ok": True, "id": vid, "created": True, "gstin": gstin}


def upsert_customer(data: dict[str, Any], uploaded_files: list[tuple[str, bytes, str]] | None = None) -> dict[str, Any]:
    gstin = (data.get("gstin") or "").strip().upper()
    name = (data.get("name") or data.get("legal_name") or data.get("trade_name") or "Unknown").strip()
    trade_name = (data.get("trade_name") or "").strip()
    state_code = (data.get("state_code") or (gstin[:2] if gstin else "")).strip()
    pin = (data.get("pin") or "").strip()
    address = (data.get("address") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    contact_person = (data.get("contact_person") or "").strip()
    drug_license_no = (data.get("drug_license_no") or "").strip()
    fssai_no = (data.get("fssai_no") or "").strip()

    existing = db.one('SELECT id, documents_json FROM customers WHERE gstin=?', (gstin,)) if gstin else db.one('SELECT id, documents_json FROM customers WHERE name=?', (name,))
    now = datetime.now().isoformat()
    docs = json.loads(existing["documents_json"]) if existing and existing.get("documents_json") else {}
    if uploaded_files:
        for label, content, fname in uploaded_files:
            docs = _merge_docs(docs, {"label": label, "filename": fname, "uploaded_at": now})
    docs_json = json.dumps(docs, ensure_ascii=False) if docs else None

    if existing:
        db.execute(
            'UPDATE customers SET name=?, trade_name=?, gstin=?, state_code=?, pin=?, address=?, phone=?, email=?, contact_person=?, drug_license_no=?, fssai_no=?, documents_json=?, updated_at=? WHERE id=?',
            (name, trade_name, gstin or None, state_code, pin, address, phone, email, contact_person, drug_license_no, fssai_no, docs_json, now, existing["id"])
        )
        return {"ok": True, "id": existing["id"], "created": False, "gstin": gstin}
    cid = db.execute(
        'INSERT INTO customers(name, trade_name, gstin, state_code, pin, address, phone, email, contact_person, drug_license_no, fssai_no, documents_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (name, trade_name, gstin or None, state_code, pin, address, phone, email, contact_person, drug_license_no, fssai_no, docs_json, now)
    )
    return {"ok": True, "id": cid, "created": True, "gstin": gstin}


def process_registration_upload(files: list[tuple[str, bytes, str]]) -> dict[str, Any]:
    """Process uploaded GST certificate and drug license; extract and upsert vendor."""
    combined: dict[str, Any] = {}
    ocr_results: list[dict[str, Any]] = []
    any_text = False
    for label, content, fname in files:
        ocr = _extract_text_from_file(content, fname)
        ocr_results.append({"label": label, "filename": fname, **ocr})
        if not ocr.get("ok"):
            continue
        text = ocr.get("text", "")
        if text.strip():
            any_text = True
        if "gst" in label.lower() or "certificate" in label.lower():
            combined.update(parse_gst_certificate(text))
        if "drug" in label.lower() or "license" in label.lower():
            combined.update(parse_drug_license(text))
    if not any_text:
        return {
            "ok": False,
            "error": "Could not read text from the uploaded document(s). "
                     "For image-only files, install/configure an Ollama vision model or enable RapidOCR.",
            "ocr": ocr_results,
        }
    result = upsert_vendor(combined, files)
    return {"ok": True, "entity": "vendor", "extracted": combined, "record": result, "ocr": ocr_results}


def extract_party_from_invoice(text: str, party_type: str = "buyer") -> dict[str, Any]:
    """Extract buyer/seller details from an invoice or offer PDF/image."""
    gstin = _find_gstin(text)
    pin = _find_pin(text)
    pt = party_type.lower()
    name_patterns = [
        rf"{party_type.title()}\s*[:.]\s*([^\n]{{3,}})",
    ]
    if pt == "buyer":
        name_patterns += [
            r"(?:Bill\s*To|Ship\s*To|Consignee)\s*[:.]\s*([^\n]{3,})",
            r"Customer\s*[:.]\s*([^\n]{3,})",
        ]
    else:
        name_patterns += [
            r"(?:Sold\s*By|Supplier|Vendor|Seller)\s*[:.]\s*([^\n]{3,})",
            r"From\s*[:.]\s*([^\n]{3,})",
        ]
    name = _first_match(name_patterns, text)
    address_patterns = [
        rf"{party_type.title()}.*?Address\s*[:.]\s*([^\n]{{10,}})",
        r"Address\s*[:.]\s*([^\n]{10,})",
    ]
    if pt == "buyer":
        address_patterns.insert(0, r"(?:Bill\s*To|Ship\s*To).*?Address\s*[:.]\s*([^\n]{10,})")
    else:
        address_patterns.insert(0, r"(?:Sold\s*By|Supplier|Vendor).*?Address\s*[:.]\s*([^\n]{10,})")
    address = _first_match(address_patterns, text)
    dl = _find_drug_license(text)
    return {
        "name": name,
        "gstin": gstin,
        "pin": pin,
        "address": address,
        "state_code": gstin[:2] if gstin else "",
        "drug_license_no": dl,
    }


def process_vendor_or_customer_document(file_bytes: bytes, filename: str, doc_kind: str = "invoice") -> dict[str, Any]:
    """Auto-create/update buyer or seller records from an invoice/offer upload."""
    ocr = _extract_text_from_file(file_bytes, filename)
    if not ocr.get("ok"):
        return {"ok": False, "error": ocr.get("error", "OCR failed"), "ocr": ocr}
    text = ocr["text"]

    seller = extract_party_from_invoice(text, "seller")
    buyer = extract_party_from_invoice(text, "buyer")
    seller_rec = {"skipped": True}
    if seller.get("name") or seller.get("gstin"):
        seller_rec = upsert_vendor(seller, [(doc_kind, file_bytes, filename)])
    buyer_rec = {"skipped": True}
    if buyer.get("name") or buyer.get("gstin"):
        buyer_rec = upsert_customer(buyer, [(doc_kind, file_bytes, filename)])
    return {
        "ok": True,
        "seller": {"extracted": seller, **seller_rec},
        "buyer": {"extracted": buyer, **buyer_rec},
        "ocr": ocr,
    }


def list_vendors(limit: int = 100) -> list[dict[str, Any]]:
    return db.query('SELECT * FROM vendors ORDER BY updated_at DESC LIMIT ?', (limit,))


def list_customers(limit: int = 100) -> list[dict[str, Any]]:
    return db.query('SELECT * FROM customers ORDER BY updated_at DESC LIMIT ?', (limit,))
