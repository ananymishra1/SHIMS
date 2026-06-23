from __future__ import annotations

import io
import os
import sys
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SHIMS_ENV", "test")

import pytest
from fastapi.testclient import TestClient

from shims_enterprise.app import app
from shared.config import settings
from shared.database import db
from shared.enterprise_documents import set_invoice_counter


@pytest.fixture
def client(monkeypatch):
    db.init()
    set_invoice_counter("INV", 2026, 5)
    # Avoid loading ONNX OCR in test runner; mock extraction for document tests
    import shared.vendor_registration as vr
    def fake_extract(file_bytes, filename):
        return {"ok": True, "text": file_bytes.decode("utf-8", errors="ignore")}
    monkeypatch.setattr(vr, "_extract_text_from_file", fake_extract)
    with TestClient(app) as c:
        # Log in as admin so session cookie is set for HTML/multipart routes
        login = c.post('/login', data={'username': 'admin', 'password': 'SHIMS2025!'})
        assert login.status_code in (200, 302, 303), login.text
        yield c


def _bridge_headers():
    return {"X-Bridge-Token": settings.bridge_token}


def test_invoice_counter_and_multi_item(client):
    r = client.post(
        "/api/gst/invoice-json",
        headers=_bridge_headers(),
        json={
            "buyer_name": "Test Buyer Pvt Ltd",
            "buyer_gstin": "27BBBBB0000B1Z5",
            "buyer_address": "Mumbai",
            "buyer_place": "Mumbai",
            "buyer_pin": "400001",
            "items": [
                {"name": "Product A", "hsn": "2933", "qty": 2, "unit": "kg", "rate": 1000, "gst_rate": 18},
                {"name": "Product B", "hsn": "3004", "qty": 5, "unit": "nos", "rate": 200, "gst_rate": 12},
            ],
            "vehicle_no": "MP13AB1234",
            "transporter": "Fast Transport",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    payload = data["payload"]
    assert payload["DocDtls"]["No"] == "INV-2026-0006"
    assert len(payload["ItemList"]) == 2
    assert payload["ValDtls"]["AssVal"] == 3000.0
    assert payload["ValDtls"]["IgstVal"] == 480.0
    assert payload["EwbDtls"]["VehNo"] == "MP13AB1234"


def test_ewaybill_prefills_vehicle_from_invoice(client):
    inv = client.post(
        "/api/gst/invoice-json",
        headers=_bridge_headers(),
        json={
            "buyer_name": "Test Buyer Pvt Ltd",
            "buyer_gstin": "27BBBBB0000B1Z5",
            "buyer_address": "Mumbai",
            "buyer_place": "Mumbai",
            "buyer_pin": "400001",
            "items": [{"name": "Product A", "hsn": "2933", "qty": 1, "unit": "kg", "rate": 1000, "gst_rate": 18}],
            "vehicle_no": "MP13AB1234",
            "transporter": "Fast Transport",
        },
    ).json()
    invoice_no = inv["payload"]["DocDtls"]["No"]

    r = client.get(
        f"/api/gst/ewaybill-from-invoice?invoice_no={invoice_no}",
        headers=_bridge_headers(),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["payload"]["PartB"]["vehicleNo"] == "MP13AB1234"
    assert data["payload"]["PartB"]["transporterName"] == "Fast Transport"


def test_vendor_customer_extraction(client):
    sample_text = (
        "Legal Name of Business: ABC Pharma Pvt Ltd\n"
        "Trade Name: ABC Pharma\n"
        "Address: 123 Sector 5, Mumbai, Maharashtra - 400001\n"
        "GSTIN: 27AABCU9603R1ZM\n"
        "Drug License No: MH-123456"
    ).encode("utf-8")
    r = client.post(
        "/api/vendor/extract-document",
        headers=_bridge_headers(),
        data={"kind": "invoice"},
        files={"document": ("sample_invoice.txt", io.BytesIO(sample_text), "text/plain")},
    )
    assert r.status_code in (200, 303), r.text


def test_admin_invoice_counter(client):
    r = client.post(
        "/api/admin/invoice-counter",
        headers=_bridge_headers(),
        json={"prefix": "INV", "year": 2026, "number": 10},
    )
    assert r.status_code == 200, r.text
    assert r.json()["number"] == 10
