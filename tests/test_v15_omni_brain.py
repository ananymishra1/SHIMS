from fastapi.testclient import TestClient

from backend.app.main import app
import shared.omni_brain as ob


def test_omni_brain_memory_and_rag(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "BRAIN_DB", tmp_path / "brain.sqlite3")

    memory = ob.remember("user", "preferred_language", "Use Indian English with Hinglish when natural.", tags=["preference"], pinned=True)
    assert memory["key"] == "preferred_language"

    ingested = ob.ingest_knowledge("Fluconazole note", "Fluconazole process research should track yield, impurity, solvent recovery, and QC samples.", tags=["rd"])
    assert ingested["ok"] is True
    assert ingested["chunks"] >= 1

    ctx = ob.retrieve_context("fluconazole yield impurity", limit=5)
    assert ctx["ok"] is True
    assert ctx["rag_hits"] >= 1
    assert "Fluconazole" in ctx["context_text"]


def test_omni_brain_api_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "BRAIN_DB", tmp_path / "api_brain.sqlite3")
    c = TestClient(app)

    status = c.get("/brain/status").json()
    assert status["ok"] is True
    assert status["version"] == ob.BRAIN_VERSION

    saved = c.post("/memory/save", json={"key": "factory_focus", "value": "R&D and QC workflows are priority."}).json()
    assert saved["ok"] is True

    c.post("/brain/ingest", json={"title": "QC note", "text": "COA drafts must be checked by QA before final release.", "tags": ["qc"]})
    ctx = c.post("/brain/context", json={"query": "COA QA final release", "limit": 5}).json()
    assert ctx["ok"] is True
    assert ctx["rag_hits"] >= 1

    health = c.get("/health").json()
    assert health["capabilities"]["rag"] is True
    assert health["capabilities"]["long_term_memory"] is True
