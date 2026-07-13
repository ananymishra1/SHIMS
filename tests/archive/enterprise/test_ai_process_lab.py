from fastapi.testclient import TestClient

from shims_enterprise.app import app


def _client():
    client = TestClient(app, raise_server_exceptions=False)
    client.post("/login", data={"username": "admin", "password": "SHIMS2025!"}, follow_redirects=False)
    return client


def test_ai_process_lab_page_and_capabilities_exist():
    client = _client()
    page = client.get("/ai-lab")
    assert page.status_code == 200
    assert "AI Process Lab" in page.text
    assert "Route Synthesizer" in page.text
    assert "Patent Finder" in page.text
    assert "Structure Reader" in page.text

    caps = client.get("/api/ai-process-lab/capabilities")
    assert caps.status_code == 200
    groups = {g["group"] for g in caps.json()["capabilities"]}
    assert {"Patent Intelligence", "Structure Reader", "Route Synthesis"} <= groups


def test_ai_process_lab_fast_chemistry_and_route_synthesis():
    client = _client()
    patent = client.post(
        "/api/ai-process-lab/patent-finder",
        json={"query": "Moxifloxacin synthesis process patent", "use_ai": False},
    )
    assert patent.status_code == 200
    assert patent.json()["research_links"]

    structure = client.post(
        "/api/ai-process-lab/structure-reader",
        json={"smiles": "CC(=O)O", "rxn_smiles": "CC(=O)O.CN>>CC(=O)NC", "impurity_pct": 0.1},
    )
    assert structure.status_code == 200
    body = structure.json()
    assert body["smiles"]["ok"] is True
    assert "reaction" in body

    route = client.post(
        "/api/ai-process-lab/route-synthesizer",
        json={
            "product_name": "Moxi",
            "target_smiles": "CC(=O)O",
            "raw_materials": "acetic acid, methylamine",
            "constraints": "reduce impurity and cost",
            "use_ai": False,
            "search_patents": True,
        },
    )
    assert route.status_code == 200
    data = route.json()
    assert data["retrosynthesis_routes"]
    assert data["corpus_evidence"] is not None
    assert data["factory_predictions"] is not None
    assert data["research_links"]
