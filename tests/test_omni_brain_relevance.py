"""RAG relevance gate tests.

Stopword-only overlap, recency bumps, pinned core memories, and feedback
memories must not leak into retrieval hits or trust levels.
"""
from __future__ import annotations

import pytest

import shared.omni_brain as ob
from shared.trust_contract import build_trust


@pytest.fixture(autouse=True)
def _brain_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "BRAIN_DB", tmp_path / "brain.sqlite3")


def _seed_irrelevant_docs() -> None:
    ob.ingest_knowledge(
        "IEC certificate",
        "IEC certificate IEC-2024-0042 issued to Example Pharma Pvt Ltd for import export "
        "code compliance. Valid for pharmaceutical raw material imports and exports.",
        source_uri="docs/iec_certificate.txt",
        tags=["regulatory"],
    )
    ob.ingest_knowledge(
        "Hospital emergency plan",
        "Emergency response SOP: hospital fire drill evacuation procedure, assembly "
        "points, ward transfer protocol and incident command roles.",
        source_uri="docs/hospital_emergency_plan.txt",
        tags=["sop"],
    )


def test_stopword_query_yields_zero_hits():
    """'can you check if the desktop bridge is running?' shares only stopwords
    with the seeded docs — nothing may be retrieved."""
    _seed_irrelevant_docs()
    ctx = ob.retrieve_context("can you check if the desktop bridge is running?", limit=8)
    assert ctx["ok"] is True
    assert ctx["hits"] == []
    assert ctx["memory_hits"] == 0
    assert ctx["rag_hits"] == 0


def test_content_word_overlap_returns_hit():
    """A query sharing real content words with a seeded doc still retrieves it."""
    ob.ingest_knowledge(
        "Desktop bridge",
        "The desktop bridge runs a websocket server on port 8765 and pairs the PC "
        "with SHIMS for screenshots, shell commands and file reads.",
        source_uri="docs/desktop_bridge.txt",
        tags=["desktop"],
    )
    _seed_irrelevant_docs()
    ctx = ob.retrieve_context("desktop bridge websocket port", limit=8)
    assert ctx["rag_hits"] >= 1
    titles = [h["title"] for h in ctx["hits"]]
    assert any("Desktop bridge" in t for t in titles)


def test_feedback_memories_never_in_hits_but_in_addendum():
    """omni_feedback anti-patterns are diverted out of hits into feedback_prefs
    and rendered as a short preferences line in the prompt addendum."""
    ob.ingest_knowledge(
        "Desktop bridge",
        "The desktop bridge runs a websocket server on port 8765 and pairs the PC "
        "with SHIMS for screenshots, shell commands and file reads.",
        tags=["desktop"],
    )
    ob.remember(
        "omni_feedback",
        "avoid:desktop bridge",
        "User rejected an answer about: desktop bridge. What was wrong: hallucinated status",
        tags=["feedback", "anti_pattern"],
        weight=2.0,
        source="feedback",
    )
    ctx = ob.retrieve_context("desktop bridge websocket port", limit=8)
    assert ctx["hits"], "expected the real desktop-bridge doc to be retrieved"
    assert all("omni_feedback" not in h["title"] for h in ctx["hits"])
    assert all("feedback" not in {str(t).lower() for t in (h.get("tags") or [])} for h in ctx["hits"])
    assert ctx["feedback_prefs"], "feedback memory should be returned separately"

    addendum, _ = ob.brain_prompt_addendum("desktop bridge websocket port")
    assert "User preferences from feedback:" in addendum
    assert "hallucinated status" in addendum


def test_core_memories_excluded_from_hits():
    """Pinned system:* core memories enter via BRAIN_DIRECTIVES, not top-k hits."""
    _seed_irrelevant_docs()
    ctx = ob.retrieve_context("SHIMS system identity core directives", limit=8)
    assert all(not h["title"].startswith("system:") for h in ctx["hits"])
    assert all((h.get("source") or "") != "core" for h in ctx["hits"])


def test_build_trust_draft_when_only_core_or_feedback_matched():
    core_ev = [{"kind": "memory", "title": "system:identity", "score": 3.4,
                "metadata": {"source": "core", "tags": ["core", "identity"]}}]
    trust = build_trust(route="test", evidence=core_ev, requested_level="draft")
    assert trust["trust_level"] == "draft"

    feedback_ev = [{"kind": "memory", "title": "omni_feedback:avoid:x", "score": 4.6,
                    "metadata": {"source": "feedback", "tags": ["feedback", "anti_pattern"]}}]
    trust = build_trust(route="test", evidence=feedback_ev, requested_level="draft")
    assert trust["trust_level"] == "draft"

    low_score_ev = [{"kind": "rag", "title": "IEC certificate", "score": 0.9,
                     "metadata": {"source": "note", "tags": []}}]
    trust = build_trust(route="test", evidence=low_score_ev, requested_level="draft")
    assert trust["trust_level"] == "draft"


def test_build_trust_memory_backed_for_genuine_hit():
    good_ev = [{"kind": "rag", "title": "Desktop bridge", "score": 2.3,
                "metadata": {"source": "note", "tags": ["desktop"]}}]
    trust = build_trust(route="test", evidence=good_ev, requested_level="draft")
    assert trust["trust_level"] == "memory-backed"


# --------------------------------------------------------------------------- #
# Strong-hit gate — weak corpus matches must not pollute chat context
# --------------------------------------------------------------------------- #

def test_weak_single_token_match_is_dropped_by_strong_gate():
    """The user's report: conversational turns pulled irrelevant pharma chunks
    on a single shared word. One matched token out of a multi-word query is
    below both the strong score bar and the coverage bar — dropped entirely."""
    ob.ingest_knowledge(
        "Dissolution procedure",
        "The dissolution profile comparison uses the f2 similarity factor for "
        "the dissolution test; at least three time points are required.",
        source_uri="docs/dissolution.txt",
        tags=["pharma"],
    )
    # One shared token ("dissolution") out of a 4-token conversational query.
    ctx = ob.retrieve_context("the dissolution of our partnership", limit=8)
    assert ctx["hits"] == []
    assert ctx["rag_hits"] == 0
    assert ctx["context_text"] == ""


def test_strong_gate_env_escape_restores_lenient_behavior(monkeypatch):
    monkeypatch.setenv("SHIMS_RAG_STRONG_SCORE", "1.5")
    monkeypatch.setenv("SHIMS_RAG_COVERAGE_MIN", "1")
    ob.ingest_knowledge(
        "Dissolution procedure",
        "The dissolution profile comparison uses the f2 similarity factor for "
        "the dissolution test; at least three time points are required.",
        source_uri="docs/dissolution.txt",
        tags=["pharma"],
    )
    ctx = ob.retrieve_context("the dissolution of our partnership", limit=8)
    assert ctx["rag_hits"] >= 1


def test_strong_multi_word_match_still_retrieves():
    """Genuinely relevant docs clear the strong bar and are returned."""
    ob.ingest_knowledge(
        "Desktop bridge",
        "The desktop bridge runs a websocket server on port 8765 and pairs the PC "
        "with SHIMS for screenshots, shell commands and file reads.",
        source_uri="docs/desktop_bridge.txt",
        tags=["desktop"],
    )
    ctx = ob.retrieve_context("desktop bridge websocket port", limit=8)
    assert ctx["rag_hits"] >= 1


def test_strong_threshold_env_parsing(monkeypatch):
    monkeypatch.setenv("SHIMS_RAG_STRONG_SCORE", "4.25")
    assert ob._rag_strong_score() == 4.25
    monkeypatch.setenv("SHIMS_VECTOR_STRONG_SIM", "0.81")
    assert ob._vector_strong_sim() == 0.81
    monkeypatch.setenv("SHIMS_RAG_STRONG_SCORE", "not-a-number")
    assert ob._rag_strong_score() == 3.0
    monkeypatch.setenv("SHIMS_VECTOR_STRONG_SIM", "not-a-number")
    assert ob._vector_strong_sim() == 0.70
