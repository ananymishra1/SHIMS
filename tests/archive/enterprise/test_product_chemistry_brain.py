from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from shared.product_chemistry import (
    add_rm_price,
    analyze_product_chemistry,
    build_product_draft,
    canonicalize_product_name,
    delete_provider_key,
    ensure_product_chemistry_schema,
    learn_document_style,
    list_provider_keys,
    normalize_corpus_products,
    save_provider_key,
    suggest_manufacturing_routes,
    verify_rm_price,
)
from shims_enterprise.app import app


def test_product_name_canonicalization_rules():
    assert canonicalize_product_name("cost of fluconazole")["canonical"] == "Fluconazole"
    assert canonicalize_product_name("COA DFTA JKLC")["canonical"] == "DFTA"
    assert canonicalize_product_name("Telmisartan & material balance")["canonical"] == "Telmisartan"
    assert canonicalize_product_name("moxi")["canonical"] == "L011 Moxi A"
    assert canonicalize_product_name("Moxifloxacin")["canonical"] == "L011 Moxi A"
    assert canonicalize_product_name("of")["confidence"] < 0.5
    assert canonicalize_product_name("IMG 20201130 WA0220", "IMG 20201130 WA0220.jpg")["confidence"] < 0.5


def test_product_chemistry_workflow_smoke():
    ensure_product_chemistry_schema()
    normalized = normalize_corpus_products(user_id=None)
    assert normalized["ok"] is True
    if not normalized["documents_seen"]:
        pytest.skip("Imported BMR corpus is not present in this test database")
    assert normalized["linked"] + normalized["review_queue"] >= 1

    analysis = analyze_product_chemistry("Fluconazole", user_id=None)
    assert analysis["ok"] is True
    assert analysis["summary"]["product_name"] == "Fluconazole"
    assert "solvents" in analysis["summary"]

    price = add_rm_price(
        {
            "material_name": "Methanol",
            "price_per_kg": 42,
            "supplier": "Unit test supplier",
            "verification_status": "unverified",
        },
        user_id=None,
    )
    assert price["ok"] is True
    verified = verify_rm_price(price["id"], user_id=None)
    assert verified["ok"] is True

    routes = suggest_manufacturing_routes("Fluconazole", user_id=None)
    assert routes["ok"] is True
    assert routes["routes"]
    assert "scores" in routes["routes"][0]


def test_moxi_corpus_drafts_are_immediate_and_evidence_backed():
    ensure_product_chemistry_schema()
    normalize_corpus_products(user_id=None)
    analysis = analyze_product_chemistry("Moxifloxacin", user_id=None)
    if not analysis.get("citations"):
        pytest.skip("Moxi/Moxifloxacin corpus document is not present in this test database")
    assert analysis["product_name"] == "L011 Moxi A"
    raw_materials = " ".join(analysis["summary"].get("raw_materials") or [])
    assert "Acetic anhydride" in raw_materials
    assert "Zinc chloride" in raw_materials

    experiment = build_product_draft("moxi", "experiment", user_id=None)
    assert experiment["ok"] is True
    assert experiment["url"]
    assert "R&D experiment" in experiment["response"]

    scaleup = build_product_draft("moxifloxacin", "scaleup", user_id=None)
    assert scaleup["ok"] is True
    assert "Scale-Up" in scaleup["response"] or "Scale-up" in scaleup["response"]


def test_jk_style_and_provider_key_storage():
    ensure_product_chemistry_schema()
    style = learn_document_style("Fluconazole", user_id=None)
    assert style["ok"] is True
    if not style["profiles"]:
        pytest.skip("JK BMR/COA style source documents are not present in this test database")
    assert any(p["document_kind"] == "bmr" for p in style["profiles"])
    assert any(p["document_kind"] == "coa" for p in style["profiles"])

    saved = save_provider_key("kimi", "unit-test-secret-123456", default_model="kimi-k2", user_id=None)
    assert saved["ok"] is True
    providers = list_provider_keys()["providers"]
    kimi = next(p for p in providers if p["provider"] == "kimi")
    assert kimi["configured"] is True
    assert "unit-test-secret" not in str(kimi)
    assert delete_provider_key("kimi", user_id=None)["ok"] is True


def test_product_chemistry_api_routes():
    client = TestClient(app, raise_server_exceptions=False)
    client.post("/login", data={"username": "admin", "password": "SHIMS2025!"}, follow_redirects=False)

    page = client.get("/products", params={"product_name": "Fluconazole"})
    assert page.status_code == 200
    assert "Products 360" in page.text

    assert client.post("/api/products/normalize-corpus").status_code == 200
    assert client.post("/api/products/Fluconazole/chemistry/analyze").status_code == 200
    assert client.get("/api/products/Fluconazole/chemical-changes").status_code == 200
    assert client.post("/api/products/Fluconazole/routes/suggest").status_code == 200
    assert client.get("/api/products/Fluconazole/manufacturing-options").status_code == 200
    assert client.post("/api/products/Fluconazole/style/learn").status_code == 200
    assert client.get("/api/admin/ai-providers").status_code == 200
