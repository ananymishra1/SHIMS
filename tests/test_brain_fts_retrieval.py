"""Retrieval must reach the WHOLE knowledge corpus, not just recent chunks.

Regression: retrieve_context scored only the newest 1,500 chunks (a Python
scan over `ORDER BY updated_at DESC LIMIT 1500`). Once the corpus grew past
that, older documents became invisible — and because retrieval still returned
*something* (whatever recent chunks scored above zero), the model answered
confidently from unrelated context instead of reporting a miss. Measured on
the real corpus: 173 chunks mentioned "immunogenicity" and none were inside
the window.

The fix is an FTS5 index over knowledge_chunks so SQLite matches the whole
corpus and Python scores only the candidates it returns.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from shared import omni_brain
from shared.omni_brain import (
    BRAIN_DB,
    _fts_match_expression,
    _knowledge_candidates,
    ensure_fts_index,
    ingest_knowledge,
    retrieve_context,
)


def _fts_available() -> bool:
    try:
        sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except Exception:
        return False


requires_fts = pytest.mark.skipif(not _fts_available(), reason="SQLite build lacks FTS5")


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------
def test_match_expression_quotes_every_term():
    expr = _fts_match_expression("immunogenicity assay validation")
    assert expr == '"immunogenicity" OR "assay" OR "validation"'


def test_match_expression_neutralizes_fts_operators():
    """A user typing FTS syntax must not produce a malformed MATCH query."""
    expr = _fts_match_expression('sulfa* AND NOT "quoted" -minus col:on')
    assert "*" not in expr and "-" not in expr and ":" not in expr
    for token in expr.split(" OR "):
        assert token.startswith('"') and token.endswith('"')


def test_match_expression_empty_for_stopword_only_input():
    assert _fts_match_expression("hi") == ""
    assert _fts_match_expression("") == ""


@requires_fts
def test_match_expression_is_valid_sql_for_hostile_input():
    """The built expression must actually execute, not just look sane."""
    ensure_fts_index()
    expr = _fts_match_expression('"; DROP TABLE knowledge_chunks; -- AND OR *')
    assert expr
    con = sqlite3.connect(str(BRAIN_DB))
    try:
        con.execute("SELECT rowid FROM knowledge_fts WHERE knowledge_fts MATCH ? LIMIT 1", (expr,)).fetchall()
    finally:
        con.close()
    assert con  # reached without an FTS syntax error
    with sqlite3.connect(str(BRAIN_DB)) as check:
        assert check.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] > 0


# ---------------------------------------------------------------------------
# Whole-corpus reachability
# ---------------------------------------------------------------------------
@requires_fts
def test_old_chunk_is_retrievable_after_many_newer_chunks():
    """The actual regression: bury a distinctive document under more than
    1,500 newer chunks and confirm it is still retrievable."""
    marker = f"zeaxanthinolide{uuid.uuid4().hex[:8]}"
    ingest_knowledge(
        f"Assay monograph {marker}",
        f"The {marker} reference standard is assayed by titration against perchloric acid.",
        source_type="test-corpus",
        source_uri=f"test://old/{marker}",
        tags=["test", "old"],
    )

    # Bury it: 1,600 newer chunks, more than the old 1,500-row window.
    now = omni_brain._now()
    with sqlite3.connect(str(BRAIN_DB)) as con:
        con.executemany(
            "INSERT INTO knowledge_chunks(source_type, source_uri, title, chunk_text, tags_json, importance, created_at, updated_at) "
            "VALUES ('test-filler', ?, 'filler', 'unrelated filler content about packaging logistics', '[]', 1.0, ?, ?)",
            [(f"test://filler/{i}", now + 1 + i, now + 1 + i) for i in range(1600)],
        )
        con.commit()
    ensure_fts_index(force=True)

    try:
        hits = retrieve_context(f"{marker} titration perchloric", limit=8)
        texts = " ".join(h["content"] for h in hits["hits"])
        assert marker in texts, "a chunk buried under 1,600 newer ones must still be retrievable"
    finally:
        with sqlite3.connect(str(BRAIN_DB)) as con:
            con.execute("DELETE FROM knowledge_chunks WHERE source_type IN ('test-filler','test-corpus')")
            con.commit()
        ensure_fts_index(force=True)


@requires_fts
def test_candidates_draw_from_whole_corpus_not_just_recent():
    marker = f"pyridoxalquinate{uuid.uuid4().hex[:8]}"
    ingest_knowledge(
        f"Old spec {marker}",
        f"Specification for {marker} intermediate, retained sample protocol.",
        source_type="test-corpus",
        source_uri=f"test://cand/{marker}",
    )
    now = omni_brain._now()
    with sqlite3.connect(str(BRAIN_DB)) as con:
        con.executemany(
            "INSERT INTO knowledge_chunks(source_type, source_uri, title, chunk_text, tags_json, importance, created_at, updated_at) "
            "VALUES ('test-filler', ?, 'filler', 'noise', '[]', 1.0, ?, ?)",
            [(f"test://cf/{i}", now + 1 + i, now + 1 + i) for i in range(1600)],
        )
        con.commit()
    ensure_fts_index(force=True)
    try:
        rows = _knowledge_candidates(f"{marker} retained sample")
        assert any(marker in (r["chunk_text"] or "") for r in rows)
    finally:
        with sqlite3.connect(str(BRAIN_DB)) as con:
            con.execute("DELETE FROM knowledge_chunks WHERE source_type IN ('test-filler','test-corpus')")
            con.commit()
        ensure_fts_index(force=True)


@requires_fts
def test_vague_query_still_gets_recent_context():
    """FTS matching must not cost us the recency behaviour vague turns rely on."""
    rows = _knowledge_candidates("hi")
    assert rows, "a query with no usable search terms must still return recent chunks"


# ---------------------------------------------------------------------------
# Index maintenance
# ---------------------------------------------------------------------------
@requires_fts
def test_ensure_fts_index_is_idempotent_and_cheap():
    first = ensure_fts_index(force=True)
    assert first["ok"] and first["rebuilt"]
    second = ensure_fts_index()
    assert second["ok"] and not second.get("rebuilt")


@requires_fts
def test_triggers_index_new_chunks_without_manual_rebuild():
    """Ingestion must stay searchable immediately — no rebuild step."""
    ensure_fts_index()
    marker = f"trigmarker{uuid.uuid4().hex[:8]}"
    ingest_knowledge(
        f"Trigger check {marker}",
        f"Content containing {marker} for the trigger path.",
        source_type="test-corpus",
        source_uri=f"test://trig/{marker}",
    )
    try:
        with sqlite3.connect(str(BRAIN_DB)) as con:
            n = con.execute(
                "SELECT COUNT(*) FROM knowledge_fts WHERE knowledge_fts MATCH ?", (f'"{marker}"',)
            ).fetchone()[0]
        assert n >= 1, "AFTER INSERT trigger should have indexed the new chunk"
    finally:
        with sqlite3.connect(str(BRAIN_DB)) as con:
            con.execute("DELETE FROM knowledge_chunks WHERE source_type='test-corpus'")
            con.commit()


@requires_fts
def test_deleted_chunks_leave_the_index():
    ensure_fts_index()
    marker = f"delmarker{uuid.uuid4().hex[:8]}"
    ingest_knowledge(
        f"Delete check {marker}",
        f"Content containing {marker} that will be removed.",
        source_type="test-corpus",
        source_uri=f"test://del/{marker}",
    )
    with sqlite3.connect(str(BRAIN_DB)) as con:
        con.execute("DELETE FROM knowledge_chunks WHERE source_uri=?", (f"test://del/{marker}",))
        con.commit()
        n = con.execute(
            "SELECT COUNT(*) FROM knowledge_fts WHERE knowledge_fts MATCH ?", (f'"{marker}"',)
        ).fetchone()[0]
    assert n == 0, "AFTER DELETE trigger should have removed the chunk from the index"
