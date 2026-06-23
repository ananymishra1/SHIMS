"""Corpus builder for the SHIMS Local Factory instance.

Gathers knowledge from:
  - the Enterprise BMR corpus (already ingested into omni-brain)
  - the ChemDFM training journal and chemistry facts
  - enterprise SQLite records (products, experiments, raw materials)
  - web searches for APIs, chemistry, pharma topics
  - peer sync from the primary SHIMS instance

Outputs:
  - storage_local/corpus/*.jsonl      raw chunks by source
  - storage_local/training/dataset.jsonl   instruction-style training pairs
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings, STORAGE_DIR, ROOT_DIR
from .database import db
from .local_factory_config import corpus_dir, default_model, training_dir


# ── helpers ──────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _jsonl_append(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")


def _jsonl_read(path: Path, limit: int = 0) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not path.exists():
        return items
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue
            if limit and len(items) >= limit:
                break
    return items


def _slim(text: str, limit: int = 2000) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


# ── source collectors ────────────────────────────────────────────────────────
def _bmr_chunks(limit: int = 2000) -> list[dict[str, Any]]:
    """Pull chunks from the omni-brain that came from the enterprise BMR corpus."""
    chunks: list[dict[str, Any]] = []
    try:
        rows = db.query(
            "SELECT source_uri, title, chunk_text, tags_json FROM knowledge_chunks WHERE source_type=? ORDER BY updated_at DESC LIMIT ?",
            ("enterprise_bmr", max(1, min(int(limit), 5000))),
        )
        for row in rows:
            text = _slim(row.get("chunk_text"), 2500)
            if not text:
                continue
            tags = []
            try:
                tags = json.loads(row.get("tags_json") or "[]")
            except Exception:
                pass
            chunks.append({
                "text": text,
                "source_type": "enterprise_bmr",
                "source_uri": row.get("source_uri", ""),
                "title": row.get("title", ""),
                "metadata": {"tags": tags},
            })
    except Exception as exc:
        return [{"text": f"BMR corpus unavailable: {exc}", "source_type": "error", "source_uri": "", "metadata": {}}]
    return chunks


def _chemistry_chunks(limit: int = 1000) -> list[dict[str, Any]]:
    """Collect chemistry facts from the ChemDFM journal and local chemistry files."""
    chunks: list[dict[str, Any]] = []
    journal_path = STORAGE_DIR / ".." / "data" / "state" / "chemdfm_journal.jsonl"
    journal_path = journal_path.resolve()
    if journal_path.exists():
        with journal_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    prompt = _slim(entry.get("prompt"), 800)
                    response = _slim(entry.get("response"), 1200)
                    if prompt and response:
                        chunks.append({
                            "text": f"Q: {prompt}\nA: {response}",
                            "source_type": "chemistry_journal",
                            "source_uri": "chemdfm_journal",
                            "metadata": {"topic": entry.get("topic", "general")},
                        })
                    if len(chunks) >= limit:
                        break
                except Exception:
                    continue

    # Optional static chemistry Q&A file used by the Chem API.
    chem_qa_path = ROOT_DIR / "data" / "reference" / "chemistry_qa.jsonl"
    if chem_qa_path.exists():
        for item in _jsonl_read(chem_qa_path, limit=max(0, limit - len(chunks))):
            q = _slim(item.get("question") or item.get("instruction"), 800)
            a = _slim(item.get("answer") or item.get("output"), 1200)
            if q and a:
                chunks.append({
                    "text": f"Q: {q}\nA: {a}",
                    "source_type": "chemistry_reference",
                    "source_uri": str(chem_qa_path),
                    "metadata": {},
                })
    return chunks


def _enterprise_record_chunks(limit: int = 1000) -> list[dict[str, Any]]:
    """Collect lightweight records from the enterprise database."""
    chunks: list[dict[str, Any]] = []
    try:
        # Products from the BMR corpus metadata.
        products = db.query(
            "SELECT DISTINCT product_name FROM enterprise_bmr_documents WHERE product_name IS NOT NULL AND product_name != '' LIMIT ?",
            (limit,),
        )
        for row in products:
            name = row.get("product_name", "").strip()
            if name:
                chunks.append({
                    "text": f"SHIMS enterprise product: {name}",
                    "source_type": "enterprise_product",
                    "source_uri": "",
                    "metadata": {},
                })
    except Exception:
        pass

    try:
        # R&D experiments.
        experiments = db.query(
            "SELECT id, name, product FROM rd_experiments ORDER BY updated_at DESC LIMIT ?",
            (max(0, limit - len(chunks)),),
        )
        for row in experiments:
            text = f"R&D experiment '{row.get('name')}': product {row.get('product')}"
            chunks.append({
                "text": text,
                "source_type": "enterprise_rd_experiment",
                "source_uri": f"rd_experiment:{row.get('id')}",
                "metadata": dict(row),
            })
    except Exception:
        pass
    return chunks


async def _web_chunks(queries: list[str] | None = None, max_results: int = 5, limit: int = 500) -> list[dict[str, Any]]:
    """Search the web and fetch readable text from top pages."""
    if queries is None:
        queries = [
            "pharmaceutical manufacturing BMR batch manufacturing record best practices",
            "API active pharmaceutical ingredient synthesis route selection",
            "GMP good manufacturing practice pharma documentation",
        ]
    from . import agent_tools
    chunks: list[dict[str, Any]] = []
    for query in queries:
        if len(chunks) >= limit:
            break
        search = await asyncio.to_thread(
            agent_tools.run_tool,
            "web.search",
            {"query": query, "max_results": max_results},
            allow_gated=False,
        )
        if not search.get("ok"):
            continue
        for item in search.get("results", [])[:max_results]:
            url = item.get("url")
            if not url:
                continue
            fetched = await asyncio.to_thread(
                agent_tools.run_tool,
                "web.fetch",
                {"url": url},
                allow_gated=False,
            )
            if fetched.get("ok") and fetched.get("text"):
                chunks.append({
                    "text": _slim(fetched["text"], 3000),
                    "source_type": "web",
                    "source_uri": url,
                    "metadata": {"title": item.get("title", ""), "query": query},
                })
            if len(chunks) >= limit:
                break
    return chunks


async def _synthesize_qa(text: str, model: str | None = None) -> dict[str, str]:
    """Ask a local model to turn a raw chunk into one instruction/answer pair."""
    from . import ai
    prompt = (
        "Read the following SHIMS knowledge excerpt and produce exactly one concise question "
        "that a user might ask about it, followed by a short accurate answer.\n\n"
        f"Excerpt:\n{text[:1200]}\n\n"
        "Format your response as:\nQuestion: ...\nAnswer: ..."
    )
    try:
        result = await ai.ask_ai(
            prompt,
            system="You are a helpful domain-expert tutor for pharma, chemistry, and manufacturing.",
            provider="ollama",
            model=model or default_model(),
        )
        reply = result.text or ""
        q_match = re.search(r"Question:\s*(.+?)\nAnswer:", reply, re.S | re.I)
        a_match = re.search(r"Answer:\s*(.+)", reply, re.S | re.I)
        question = q_match.group(1).strip() if q_match else ""
        answer = a_match.group(1).strip() if a_match else ""
        if not question or not answer:
            # Fallback: make the chunk itself the answer.
            question = "What is the following SHIMS knowledge about?"
            answer = text[:1500]
        return {"instruction": question, "input": "", "output": answer}
    except Exception:
        return {"instruction": "What is the following SHIMS knowledge about?", "input": "", "output": text[:1500]}


# ── public API ───────────────────────────────────────────────────────────────
def export_corpus(source_type: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    """Return raw corpus chunks, optionally filtered by source_type."""
    out: list[dict[str, Any]] = []
    for path in corpus_dir().glob("*.jsonl"):
        for item in _jsonl_read(path):
            if source_type and item.get("source_type") != source_type:
                continue
            out.append(item)
            if limit and len(out) >= limit:
                return out
    return out


async def build_corpus_async(
    *,
    force: bool = False,
    web_queries: list[str] | None = None,
    max_web_pages: int = 6,
    synthesize_qa: bool = True,
    qa_model: str | None = None,
    max_qa_chunks: int = 200,
) -> dict[str, Any]:
    """Gather all corpus sources and build the training dataset."""
    corpus_dir().mkdir(parents=True, exist_ok=True)
    training_dir().mkdir(parents=True, exist_ok=True)

    if force:
        for p in corpus_dir().glob("*.jsonl"):
            p.unlink(missing_ok=True)
        (training_dir() / "dataset.jsonl").unlink(missing_ok=True)

    stats: dict[str, Any] = {"started_at": _now(), "sources": {}}

    # 1. BMR corpus
    bmr = _bmr_chunks()
    _jsonl_append(corpus_dir() / "bmr_corpus.jsonl", bmr)
    stats["sources"]["bmr"] = len(bmr)

    # 2. Chemistry
    chem = _chemistry_chunks()
    _jsonl_append(corpus_dir() / "chemistry_corpus.jsonl", chem)
    stats["sources"]["chemistry"] = len(chem)

    # 3. Enterprise records
    ent = _enterprise_record_chunks()
    _jsonl_append(corpus_dir() / "enterprise_corpus.jsonl", ent)
    stats["sources"]["enterprise_records"] = len(ent)

    # 4. Web
    web = await _web_chunks(queries=web_queries, limit=max_web_pages)
    _jsonl_append(corpus_dir() / "web_corpus.jsonl", web)
    stats["sources"]["web"] = len(web)

    # 5. Build training dataset
    all_chunks = bmr + chem + ent + web
    if synthesize_qa:
        pairs: list[dict[str, Any]] = []
        for chunk in all_chunks[:max_qa_chunks]:
            pair = await _synthesize_qa(chunk["text"], model=qa_model or default_model())
            pair["source_type"] = chunk.get("source_type", "unknown")
            pair["source_uri"] = chunk.get("source_uri", "")
            pairs.append(pair)
        _jsonl_append(training_dir() / "dataset.jsonl", pairs)
        stats["training_pairs"] = len(pairs)
    else:
        # Use raw chunks as text examples.
        _jsonl_append(training_dir() / "dataset.jsonl", [{"text": c["text"], **c} for c in all_chunks])
        stats["training_pairs"] = len(all_chunks)

    stats["finished_at"] = _now()
    stats["total_chunks"] = len(all_chunks)
    stats_path = corpus_dir() / "last_build_stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "stats": stats}


def build_corpus(*, force: bool = False, **kwargs: Any) -> dict[str, Any]:
    """Synchronous wrapper for :func:`build_corpus_async`."""
    return asyncio.run(build_corpus_async(force=force, **kwargs))


async def sync_from_peer(peer_id: str, *, source_type: str | None = None, limit: int = 1000) -> dict[str, Any]:
    """Pull corpus chunks from a peer instance and ingest them locally."""
    from .inter_instance_bridge import PeerClient, get_peer
    peer = get_peer(peer_id)
    if not peer:
        return {"ok": False, "error": f"peer {peer_id} not found"}
    client = PeerClient(peer)
    remote = await client.sync_corpus(source_type=source_type, limit=limit)
    if not remote.get("ok"):
        return remote
    chunks = remote.get("chunks", [])
    if not chunks:
        return {"ok": True, "ingested": 0}
    _jsonl_append(corpus_dir() / f"peer_{peer_id}.jsonl", chunks)
    from . import omni_brain
    ingested = 0
    for ch in chunks:
        text = ch.get("text") or ch.get("content")
        if not text:
            continue
        omni_brain.ingest_knowledge(
            title=ch.get("title", f"peer:{peer_id}"),
            text=text,
            source_type=ch.get("source_type", f"peer_{peer_id}"),
            source_uri=ch.get("source_uri", f"peer://{peer_id}"),
            tags=["peer_sync", peer_id],
            importance=1.1,
        )
        ingested += 1
    return {"ok": True, "received": len(chunks), "ingested": ingested}


def corpus_stats() -> dict[str, Any]:
    """Human-readable corpus statistics."""
    stats: dict[str, Any] = {"ok": True, "files": {}, "total_chunks": 0}
    for path in sorted(corpus_dir().glob("*.jsonl")):
        count = len(_jsonl_read(path))
        stats["files"][path.name] = count
        stats["total_chunks"] += count
    ds_path = training_dir() / "dataset.jsonl"
    stats["training_pairs"] = len(_jsonl_read(ds_path)) if ds_path.exists() else 0
    last_stats = corpus_dir() / "last_build_stats.json"
    if last_stats.exists():
        try:
            stats["last_build"] = json.loads(last_stats.read_text(encoding="utf-8"))
        except Exception:
            pass
    return stats
