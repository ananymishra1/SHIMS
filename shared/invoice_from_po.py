from __future__ import annotations

import re
from typing import Any

from .enterprise_documents import create_gst_invoice, validate_gstin
from .vendor_registration import _extract_text_from_file, _find_gstin, _find_pin


def _first(patterns: list[str], text: str) -> str:
    for pat in patterns:
        m = re.search(pat, text, re.I | re.DOTALL)
        if m:
            return " ".join((m.group(1) if m.groups() else m.group(0)).split())
    return ""


def _find_po_number(text: str) -> str:
    return _first([
        r"P\.?O\.?\s*(?:No\.?|Number)?[:\s#]*([A-Z0-9/\-]{3,})",
        r"Purchase\s*Order\s*(?:No\.?|Number)?[:\s#]*([A-Z0-9/\-]{3,})",
    ], text)


def _find_po_date(text: str) -> str:
    return _first([
        r"P\.?O\.?\s*Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"Purchase\s*Order\s*Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    ], text)


def _find_buyer_name(text: str) -> str:
    return _first([
        r"Buyer\s*[:.]\s*([^\n]{3,})",
        r"Bill\s*To\s*[:.]\s*([^\n]{3,})",
        r"Customer\s*[:.]\s*([^\n]{3,})",
        r"To\s*[:.]\s*([^\n]{3,})",
    ], text)


def _find_buyer_address(text: str) -> str:
    return _first([
        r"Buyer.*?Address\s*[:.]\s*([^\n]{10,})",
        r"Address\s*[:.]\s*([^\n]{10,})",
    ], text)


def _clean_desc(desc: str) -> str:
    d = desc.strip()
    # Remove leading numbering like "1." or "1     "
    d = re.sub(r"^\d+[\.\)\-]?\s*", "", d)
    return d.rstrip("0123456789 ./")


def _parse_line_items(text: str) -> list[dict[str, Any]]:
    """Best-effort line-item extraction from a PO text."""
    items: list[dict[str, Any]] = []
    block_match = re.search(
        r"(?:S\.?\s*No|Item|Description|Material).*?(?:Total|Grand\s*Total|Amount\s*Due)",
        text, re.I | re.DOTALL
    )
    block = block_match.group(0) if block_match else text
    # Primary row pattern: S.No Description Qty Unit Rate Amount
    pat = r"(?P<sl>\d+)\s*[\.\-]?\s*(?P<desc>[^\n]{5,60}?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<unit>[A-Za-z]+)\s+(?P<rate>\d+(?:,\d+)*(?:\.\d+)?)(?:\s+(?P<amt>\d+(?:,\d+)*(?:\.\d+)?))?"
    seen: set[tuple[str, float, float]] = set()
    for m in re.finditer(pat, block, re.I):
        desc = _clean_desc(m.group("desc"))
        if not desc:
            continue
        qty = float(m.group("qty").replace(",", ""))
        rate = float(m.group("rate").replace(",", ""))
        key = (desc.lower(), qty, rate)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "name": desc,
            "hsn": "3004",
            "qty": qty,
            "unit": m.group("unit").lower(),
            "rate": rate,
            "gst_rate": 18.0,
        })
    return items


def parse_purchase_order(text: str) -> dict[str, Any]:
    buyer_name = _find_buyer_name(text)
    buyer_address = _find_buyer_address(text)
    buyer_gstin = _find_gstin(text)
    buyer_pin = _find_pin(text)
    items = _parse_line_items(text)
    return {
        "po_no": _find_po_number(text),
        "po_date": _find_po_date(text),
        "buyer_name": buyer_name,
        "buyer_address": buyer_address,
        "buyer_gstin": buyer_gstin,
        "buyer_pin": buyer_pin,
        "buyer_place": buyer_address.split(",")[-1].strip() if buyer_address else "",
        "items": items,
    }


def create_invoice_from_po_file(file_bytes: bytes, filename: str) -> dict[str, Any]:
    """Extract a customer PO and generate a GST invoice draft."""
    ocr = _extract_text_from_file(file_bytes, filename)
    if not ocr.get("ok"):
        return {"ok": False, "error": ocr.get("error", "Extraction failed"), "ocr": ocr}
    parsed = parse_purchase_order(ocr["text"])
    if not parsed["buyer_name"]:
        parsed["buyer_name"] = "Customer"
    if not parsed["items"]:
        return {"ok": False, "error": "Could not extract line items from PO", "parsed": parsed, "ocr": ocr}
    if parsed["buyer_gstin"] and not validate_gstin(parsed["buyer_gstin"]):
        return {"ok": False, "error": f"Buyer GSTIN '{parsed['buyer_gstin']}' extracted from PO is invalid", "parsed": parsed, "ocr": ocr}
    data = {
        "buyer_name": parsed["buyer_name"],
        "buyer_gstin": parsed["buyer_gstin"],
        "buyer_address": parsed["buyer_address"],
        "buyer_place": parsed["buyer_place"],
        "buyer_pin": parsed["buyer_pin"],
        "items": parsed["items"],
        "vehicle_no": "",
        "transporter": "",
    }
    result = create_gst_invoice(data)
    return {"ok": True, "po": parsed, **result}
