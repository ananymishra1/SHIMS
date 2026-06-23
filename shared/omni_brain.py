from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ROOT_DIR, STORAGE_DIR

try:
    from .neural_governor import vector_memory as _vector_memory
except Exception:  # pragma: no cover
    _vector_memory = None

try:
    from .telemetry import build_daily_lessons, log_event, recent_events
except Exception:  # pragma: no cover
    def build_daily_lessons(limit: int = 500) -> dict[str, Any]:
        return {"event_count": 0, "error_count": 0, "prompt_injection": []}

    def log_event(*args: Any, **kwargs: Any) -> None:
        return None

    def recent_events(limit: int = 50) -> list[dict[str, Any]]:
        return []


BRAIN_VERSION = "omni-v16-semantic-kernel"
BRAIN_DB = Path(os.getenv("SHIMS_BRAIN_DB", ROOT_DIR / "data" / "state" / "omni_brain.sqlite3")).resolve()

CORE_MEMORIES = [
    ("system", "identity", "SHIMS is a local-first multimodal realtime AI operating shell, not a text-only chatbot.", ["core", "identity"], True, 2.0),
    ("system", "tool_first", "For file/media/document requests, run deterministic tools first, verify artifacts, then narrate results.", ["core", "tools"], True, 2.0),
    ("system", "gxp_gate", "AI may draft, check, research, and recommend in regulated workflows, but named humans approve GxP final decisions.", ["core", "safety"], True, 2.0),
    ("system", "local_first", "Prefer local Ollama and local storage unless the user selects a configured cloud provider or asks for current web data.", ["core", "privacy"], True, 1.8),
    ("system", "capture_inbox", "Treat shared links, mailbox items, and user-provided snippets as first-class memory/RAG inputs and follow-up tasks.", ["core", "capture", "mailbox"], True, 1.7),
]

BRAIN_DIRECTIVES = [
    "Route first, then reason: decide whether a deterministic tool, web research, RAG, or LLM should own the turn.",
    "Use long-term memory and RAG excerpts as grounding, but say when a fact is not present in retrieved context.",
    "Capture useful outcomes into episodic memory after the turn so future sessions improve.",
    "Use the capture/mailbox inbox for links, RFQs, invoices, research pages, and daily-task follow-ups; Gmail requires explicit OAuth consent.",
    "Background learning may propose improvements, but source changes still require sandbox validation and human approval.",
    "Never claim unlimited capability literally; explain the configured local/cloud boundary when it matters.",
]


@dataclass(frozen=True)
class BrainHit:
    kind: str
    title: str
    content: str
    score: float
    source: str = ""
    url: str = ""
    tags: list[str] | None = None
    created_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now() -> float:
    return time.time()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9_+-]{2,}", text or "") if len(w) > 2]


def _score(query: str, text: str, *, weight: float = 1.0, pinned: bool = False, created_at: float | None = None) -> float:
    q = _tokens(query)
    if not q:
        recency = 0.0
        if created_at:
            recency = max(0.0, 1.0 - ((_now() - created_at) / (90 * 86400)))
        return (2.0 if pinned else 0.0) + float(weight) + recency
    hay = (text or "").lower()
    score = 0.0
    seen = set(q)
    for tok in seen:
        if tok in hay:
            score += 1.0
            score += min(3, hay.count(tok)) * 0.15
    phrase = _clean(query).lower()
    if phrase and phrase in hay:
        score += 3.0
    if pinned:
        score += 0.75
    score *= max(0.1, float(weight or 1.0))
    if created_at:
        score += max(0.0, 0.35 - ((_now() - created_at) / (30 * 86400)))
    return round(score, 4)


def _connect() -> sqlite3.Connection:
    BRAIN_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(BRAIN_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            namespace TEXT NOT NULL DEFAULT 'user',
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            tags_json TEXT DEFAULT '[]',
            pinned INTEGER DEFAULT 0,
            weight REAL DEFAULT 1.0,
            source TEXT DEFAULT 'user',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(namespace, key)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_brain_mem_ns ON memories(namespace)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_brain_mem_updated ON memories(updated_at)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            user_text TEXT NOT NULL,
            assistant_text TEXT DEFAULT '',
            route TEXT DEFAULT '',
            agent TEXT DEFAULT '',
            provider TEXT DEFAULT '',
            model TEXT DEFAULT '',
            quality REAL DEFAULT 0.0,
            metadata_json TEXT DEFAULT '{}',
            created_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_brain_episode_session ON episodes(session_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_brain_episode_created ON episodes(created_at)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL DEFAULT 'note',
            source_uri TEXT DEFAULT '',
            title TEXT NOT NULL,
            chunk_text TEXT NOT NULL,
            tags_json TEXT DEFAULT '[]',
            importance REAL DEFAULT 1.0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_brain_knowledge_title ON knowledge_chunks(title)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_brain_knowledge_created ON knowledge_chunks(created_at)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS research_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT DEFAULT '',
            snippet TEXT DEFAULT '',
            provider TEXT DEFAULT '',
            created_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_brain_research_query ON research_items(query)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_brain_research_created ON research_items(created_at)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS background_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            title TEXT NOT NULL,
            payload_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'queued',
            priority INTEGER DEFAULT 5,
            attempts INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_brain_tasks_status ON background_tasks(status)")
    # Defensive migration: add a result column for the task worker (older DBs lack it).
    try:
        con.execute("ALTER TABLE background_tasks ADD COLUMN result_json TEXT DEFAULT '{}'")
    except Exception:
        pass
    con.commit()
    return con


def ensure_core_memories() -> None:
    for namespace, key, value, tags, pinned, weight in CORE_MEMORIES:
        remember(namespace, key, value, tags=tags, pinned=pinned, weight=weight, source="core")


def remember(
    namespace: str,
    key: str,
    value: str,
    *,
    tags: list[str] | None = None,
    pinned: bool = False,
    weight: float = 1.0,
    source: str = "user",
) -> dict[str, Any]:
    namespace = _clean(namespace or "user")[:80] or "user"
    key = _clean(key or "memory")[:160] or "memory"
    value = str(value or "").strip()
    if not value:
        raise ValueError("memory value is required")
    tags = [str(t).strip()[:60] for t in (tags or []) if str(t).strip()]
    now = _now()
    with _connect() as con:
        con.execute(
            """
            INSERT INTO memories(namespace, key, value, tags_json, pinned, weight, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(namespace, key) DO UPDATE SET
              value=excluded.value,
              tags_json=excluded.tags_json,
              pinned=excluded.pinned,
              weight=excluded.weight,
              source=excluded.source,
              updated_at=excluded.updated_at
            """,
            (namespace, key, value, _json(tags), int(bool(pinned)), float(weight), source[:80], now, now),
        )
        con.commit()
        row = con.execute("SELECT * FROM memories WHERE namespace=? AND key=?", (namespace, key)).fetchone()
    if row:
        source_id = f"memory:{namespace}:{key}"
        if _vector_memory is not None:
            try:
                _vector_memory.delete_embeddings("memory", source_id)
            except Exception:
                pass
        _index_vector(
            "memory",
            source_id,
            f"{key}\n{value}\n{' '.join(tags)}",
            metadata={"namespace": namespace, "key": key, "tags": tags, "pinned": pinned, "weight": weight},
        )
    return _memory_row(row) if row else {}


def _memory_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "namespace": row["namespace"],
        "key": row["key"],
        "title": row["key"],
        "value": row["value"],
        "content": row["value"],
        "tags": _load_json(row["tags_json"], []),
        "pinned": bool(row["pinned"]),
        "weight": float(row["weight"] or 1.0),
        "source": row["source"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_memories(namespace: str | None = None, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    ensure_core_memories()
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM memories ORDER BY pinned DESC, updated_at DESC LIMIT 1000"
        ).fetchall()
    out: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        item = _memory_row(row)
        if namespace and item["namespace"] != namespace:
            continue
        if query:
            s = _score(query, item["key"] + " " + item["value"] + " " + " ".join(item["tags"]), weight=item["weight"], pinned=item["pinned"], created_at=item["updated_at"])
            if s <= 0:
                continue
        else:
            s = _score("", item["value"], weight=item["weight"], pinned=item["pinned"], created_at=item["updated_at"])
        out.append((s, item))
    out.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in out[: max(1, min(limit, 500))]]


def forget_memory(memory_id: int) -> bool:
    with _connect() as con:
        row = con.execute("SELECT namespace, key FROM memories WHERE id=?", (int(memory_id),)).fetchone()
        if row and _vector_memory is not None:
            try:
                _vector_memory.delete_embeddings("memory", f"memory:{row['namespace']}:{row['key']}")
            except Exception:
                pass
        cur = con.execute("DELETE FROM memories WHERE id=?", (int(memory_id),))
        con.commit()
    return cur.rowcount > 0


def _chunk_text(text: str, max_chars: int = 1200, overlap: int = 180) -> list[str]:
    text = re.sub(r"\r\n?", "\n", str(text or "")).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(current) + len(part) + 1 <= max_chars:
            current = (current + " " + part).strip()
        else:
            if current:
                chunks.append(current)
            current = part[:max_chars]
    if current:
        chunks.append(current)
    if overlap > 0 and len(chunks) > 1:
        merged = []
        for i, chunk in enumerate(chunks):
            if i:
                chunk = chunks[i - 1][-overlap:] + " " + chunk
            merged.append(chunk.strip())
        chunks = merged
    return chunks


def _index_vector(source_type: str, source_id: str, text: str, metadata: dict[str, Any] | None = None) -> bool:
    """Store a vector embedding for the given text if the embedding layer is available."""
    if _vector_memory is None:
        return False
    try:
        return _vector_memory.store_embedding(source_type, source_id, text, metadata=metadata)
    except Exception:
        return False


def _search_vectors(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Semantic search over stored embeddings, gracefully degrading if unavailable."""
    if _vector_memory is None:
        return []
    try:
        return _vector_memory.search_vectors(query, limit=limit)
    except Exception:
        return []


def ingest_knowledge(
    title: str,
    text: str,
    *,
    source_type: str = "note",
    source_uri: str = "",
    tags: list[str] | None = None,
    importance: float = 1.0,
) -> dict[str, Any]:
    title = _clean(title or "Untitled knowledge")[:220] or "Untitled knowledge"
    chunks = _chunk_text(text)
    tags = [str(t).strip()[:60] for t in (tags or []) if str(t).strip()]
    now = _now()
    if not chunks:
        return {"ok": False, "title": title, "chunks": 0, "message": "No text to ingest."}
    with _connect() as con:
        # Upsert: remove stale chunks for this source so repeated imports/reparse/rag don't bloat the brain
        existing = con.execute(
            "SELECT id FROM knowledge_chunks WHERE source_type=? AND source_uri=?",
            (source_type[:80], str(source_uri or "")[:500]),
        ).fetchall()
        for row in existing:
            if _vector_memory is not None:
                try:
                    _vector_memory.delete_embeddings("knowledge_chunk", f"kc:{row['id']}")
                except Exception:
                    pass
        con.execute(
            "DELETE FROM knowledge_chunks WHERE source_type=? AND source_uri=?",
            (source_type[:80], str(source_uri or "")[:500]),
        )
        for chunk in chunks:
            cur = con.execute(
                """
                INSERT INTO knowledge_chunks(source_type, source_uri, title, chunk_text, tags_json, importance, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source_type[:80], str(source_uri or "")[:500], title, chunk, _json(tags), float(importance), now, now),
            )
            _index_vector(
                "knowledge_chunk",
                f"kc:{cur.lastrowid}",
                f"{title}\n{chunk}",
                metadata={"source_type": source_type, "source_uri": source_uri, "title": title, "tags": tags},
            )
        con.commit()
    log_event("brain.knowledge.ingested", route="brain:rag", provider="local", model=BRAIN_VERSION, ok=True, message=title, metadata={"chunks": len(chunks), "source_type": source_type})
    return {"ok": True, "title": title, "chunks": len(chunks), "source_type": source_type, "source_uri": source_uri}


def store_research_results(query: str, provider: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    query = _clean(query or "research")
    provider = _clean(provider or "web")
    now = _now()
    count = 0
    with _connect() as con:
        for item in results or []:
            title = _clean(item.get("title") or "Untitled")[:220]
            url = _clean(item.get("url") or "")[:600]
            snippet = _clean(item.get("snippet") or item.get("content") or "")[:2000]
            if not (title or snippet or url):
                continue
            con.execute(
                "INSERT INTO research_items(query, title, url, snippet, provider, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (query, title, url, snippet, provider, now),
            )
            count += 1
        con.commit()
    for item in (results or [])[:12]:
        title = _clean(item.get("title") or query)
        snippet = _clean(item.get("snippet") or item.get("content") or "")
        url = _clean(item.get("url") or "")
        if title or snippet:
            ingest_knowledge(title, f"{title}\nURL: {url}\n{snippet}", source_type="web", source_uri=url, tags=["web", provider, query[:60]], importance=1.15)
    return {"ok": True, "stored": count, "query": query, "provider": provider}


def record_episode(
    session_id: str,
    user_text: str,
    assistant_text: str,
    *,
    route: str = "",
    agent: str = "",
    provider: str = "",
    model: str = "",
    quality: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()
    session_id = _clean(session_id or "default")[:120]
    user_text = str(user_text or "").strip()
    assistant_text = str(assistant_text or "").strip()
    with _connect() as con:
        cur = con.execute(
            """
            INSERT INTO episodes(session_id, user_text, assistant_text, route, agent, provider, model, quality, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, user_text, assistant_text, route[:160], agent[:80], provider[:80], model[:160], float(quality or 0), _json(metadata or {}), now),
        )
        con.commit()
        eid = cur.lastrowid
    _learn_explicit_memories(user_text)
    return {"ok": True, "episode_id": eid, "session_id": session_id}


def _learn_explicit_memories(text: str) -> list[dict[str, Any]]:
    text = str(text or "").strip()
    if not text:
        return []
    patterns = [
        (r"(?i)\bremember that\s+(.{6,240})", "remembered"),
        (r"(?i)\bmy\s+([A-Za-z0-9 _-]{3,60})\s+is\s+(.{2,180})", "user_profile"),
        (r"(?i)\bwe use\s+(.{4,220})", "organization"),
        (r"(?i)\bi prefer\s+(.{4,180})", "preference"),
    ]
    saved: list[dict[str, Any]] = []
    for pattern, tag in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        if len(match.groups()) == 1:
            value = _clean(match.group(1))
            key = tag + ":" + value[:48].lower()
        else:
            key = _clean(match.group(1)).lower()
            value = _clean(match.group(2))
        if value:
            saved.append(remember("user", key, value, tags=[tag, "auto"], pinned=False, weight=1.1, source="conversation"))
    return saved


def _knowledge_rows(limit: int = 1500) -> list[sqlite3.Row]:
    with _connect() as con:
        return con.execute("SELECT * FROM knowledge_chunks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()


def _research_rows(limit: int = 500) -> list[sqlite3.Row]:
    with _connect() as con:
        return con.execute("SELECT * FROM research_items ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


def retrieve_context(query: str, *, limit: int = 8) -> dict[str, Any]:
    ensure_core_memories()
    q = _clean(query)
    hits: list[BrainHit] = []
    seen_keys: set[str] = set()

    def _add_hit(hit: BrainHit) -> None:
        key = f"{hit.kind}:{hit.source}:{hit.title}:{hit.content[:80]}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        hits.append(hit)

    # 1. Keyword/recency hits
    for item in list_memories(query=q if q else None, limit=200):
        score = _score(q, item["key"] + " " + item["value"] + " " + " ".join(item["tags"]), weight=item["weight"], pinned=item["pinned"], created_at=item["updated_at"])
        if score > 0:
            _add_hit(BrainHit("memory", item["namespace"] + ":" + item["key"], item["value"], score, source=item["source"], tags=item["tags"], created_at=item["updated_at"]))
    for row in _knowledge_rows():
        tags = _load_json(row["tags_json"], [])
        body = f"{row['title']} {row['chunk_text']} {' '.join(tags)} {row['source_uri']}"
        score = _score(q, body, weight=float(row["importance"] or 1.0), created_at=row["updated_at"])
        if score > 0:
            _add_hit(BrainHit("rag", row["title"], row["chunk_text"], score, source=row["source_type"], url=row["source_uri"], tags=tags, created_at=row["updated_at"]))
    for row in _research_rows():
        body = f"{row['query']} {row['title']} {row['snippet']} {row['url']}"
        score = _score(q, body, weight=1.05, created_at=row["created_at"])
        if score > 0:
            _add_hit(BrainHit("research", row["title"], row["snippet"], score, source=row["provider"], url=row["url"], tags=[row["query"]], created_at=row["created_at"]))

    # 2. Semantic vector hits (blended)
    vector_hits = _search_vectors(q, limit=max(limit * 2, 12))
    for v in vector_hits:
        sim = float(v.get("similarity") or 0.0)
        if sim < 0.45:
            continue
        score = round(sim * 12.0, 4)
        source_type = v.get("source_type", "")
        source_id = v.get("source_id", "")
        text = _clean(v.get("text_content") or "")
        meta = v.get("metadata") or {}
        if source_type == "memory":
            parts = source_id.split(":", 2)
            namespace = parts[1] if len(parts) > 1 else "user"
            key = parts[2] if len(parts) > 2 else "memory"
            _add_hit(BrainHit("memory", f"{namespace}:{key}", text, score, source="vector", tags=meta.get("tags", ["vector"]), created_at=None))
        elif source_type == "knowledge_chunk":
            title = meta.get("title") or "Knowledge"
            _add_hit(BrainHit("rag", title, text, score, source=meta.get("source_type", "vector"), url=meta.get("source_uri", ""), tags=meta.get("tags", ["vector"]), created_at=None))
        else:
            _add_hit(BrainHit("vector", source_id, text, score, source=source_type, tags=["vector"], created_at=None))

    hits.sort(key=lambda h: h.score, reverse=True)
    selected = hits[: max(1, min(limit, 20))]
    context_text = format_context(selected)
    return {
        "ok": True,
        "query": q,
        "version": BRAIN_VERSION,
        "hits": [h.to_dict() for h in selected],
        "memory_hits": len([h for h in selected if h.kind == "memory"]),
        "rag_hits": len([h for h in selected if h.kind == "rag"]),
        "research_hits": len([h for h in selected if h.kind == "research"]),
        "vector_hits": len([h for h in selected if h.kind == "vector"]),
        "context_text": context_text,
    }


def format_context(hits: list[BrainHit]) -> str:
    if not hits:
        return ""
    lines = ["SHIMS Omni Brain retrieved context:"]
    for idx, hit in enumerate(hits[:12], 1):
        content = _clean(hit.content)[:900]
        source = f" source={hit.source}" if hit.source else ""
        url = f" url={hit.url}" if hit.url else ""
        lines.append(f"[{idx}] {hit.kind.upper()} {hit.title}{source}{url}\n{content}")
    return "\n".join(lines)


def remember_turn(
    session_id: str,
    user_text: str,
    assistant_text: str,
    *,
    route: str = "",
    agent: str = "",
    provider: str = "",
    model: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = record_episode(session_id, user_text, assistant_text, route=route, agent=agent, provider=provider, model=model, metadata=metadata)
    if len(user_text or "") > 40 or len(assistant_text or "") > 60:
        digest = f"User: {user_text}\nAssistant: {assistant_text}"
        ingest_knowledge(
            f"Conversation {session_id} {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            digest[:4000],
            source_type="conversation",
            source_uri=f"session:{session_id}",
            tags=["conversation", agent or "supervisor", route or "chat"],
            importance=0.65,
        )
    return result


def schedule_task(task_type: str, title: str, payload: dict[str, Any] | None = None, *, priority: int = 5) -> dict[str, Any]:
    now = _now()
    with _connect() as con:
        cur = con.execute(
            "INSERT INTO background_tasks(task_type, title, payload_json, status, priority, attempts, created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, 0, ?, ?)",
            (_clean(task_type)[:80], _clean(title)[:220], _json(payload or {}), int(priority), now, now),
        )
        con.commit()
        tid = cur.lastrowid
    return {"ok": True, "task_id": tid, "task_type": task_type, "title": title}


def list_tasks(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as con:
        if status:
            rows = con.execute("SELECT * FROM background_tasks WHERE status=? ORDER BY priority ASC, created_at DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM background_tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [
        {
            "id": r["id"],
            "task_type": r["task_type"],
            "title": r["title"],
            "payload": _load_json(r["payload_json"], {}),
            "status": r["status"],
            "priority": r["priority"],
            "attempts": r["attempts"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "result": _load_json(r["result_json"] or "{}", {}),
        }
        for r in rows
    ]


def get_task(task_id: int) -> dict[str, Any] | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM background_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "task_type": row["task_type"],
        "title": row["title"],
        "payload": _load_json(row["payload_json"], {}),
        "status": row["status"],
        "priority": row["priority"],
        "attempts": row["attempts"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "result": _load_json(row["result_json"] or "{}", {}),
    }


def cancel_task(task_id: int) -> dict[str, Any]:
    with _connect() as con:
        row = con.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "Task not found"}
        if row["status"] in ("done", "failed"):
            return {"ok": False, "error": f"Task already {row['status']}"}
        con.execute("UPDATE background_tasks SET status='cancelled', updated_at=? WHERE id=?", (_now(), task_id))
        con.commit()
    return {"ok": True, "status": "cancelled"}


def run_learning_cycle(*, limit: int = 500, propose: bool = False) -> dict[str, Any]:
    ensure_core_memories()
    lessons = build_daily_lessons(limit=limit)
    events = recent_events(min(limit, 500))
    error_events = [e for e in events if not bool(e.get("ok", True))]
    top_routes = lessons.get("top_routes") or []
    prompt_injection = lessons.get("prompt_injection") or []
    summary = {
        "generated_at": _utc(),
        "event_count": lessons.get("event_count", len(events)),
        "error_count": len(error_events),
        "top_routes": top_routes[:8],
        "prompt_injection": prompt_injection[:8],
        "brain_directives": BRAIN_DIRECTIVES,
    }
    remember("system", "daily_lessons", _json(summary), tags=["daily", "lessons", "background"], pinned=True, weight=1.6, source="background_learning")
    # Queue the real, executable background work (the task worker drains these).
    # Note: the old "self_evolution_review" auto-proposal produced a throwaway docs
    # file and never improved anything — it has been removed. Genuine "evolution"
    # now = memory consolidation + skill extraction (both executed by the worker).
    queued = [
        schedule_task("memory_consolidation",
                      "Consolidate recent episodes into durable preference and project memories",
                      {"event_count": len(events)}, priority=5),
        schedule_task("skill_extraction",
                      "Extract reusable skills/preferences from recent episodes",
                      {"event_count": len(events)}, priority=6),
    ]
    log_event("brain.learning_cycle", route="brain:learn", provider="local", model=BRAIN_VERSION, ok=True, metadata={"summary": summary, "queued": queued})
    return {"ok": True, "version": BRAIN_VERSION, "summary": summary, "queued_tasks": queued}


# ── Background agentic task worker ──────────────────────────────────────────
# Drains the background_tasks queue and actually executes work by type. Wired
# into the Omni lifespan loop and exposed via /tasks/run.

def _claim_next_task() -> dict[str, Any] | None:
    """Atomically claim the highest-priority queued task and mark it running."""
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM background_tasks WHERE status='queued' ORDER BY priority ASC, created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        con.execute("UPDATE background_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                    (_now(), row["id"]))
        con.commit()
        return {"id": row["id"], "task_type": row["task_type"], "title": row["title"],
                "payload": _load_json(row["payload_json"], {}), "attempts": row["attempts"] + 1}


def _finish_task(task_id: int, status: str, result: dict[str, Any]) -> None:
    with _connect() as con:
        con.execute("UPDATE background_tasks SET status=?, result_json=?, updated_at=? WHERE id=?",
                    (status, _json(result), _now(), task_id))
        con.commit()


def _task_memory_consolidation(payload: dict[str, Any]) -> dict[str, Any]:
    """Roll recent episodes into a durable, deduplicated consolidation memory."""
    events = recent_events(200)
    if not events:
        return {"consolidated": 0, "note": "no recent events"}
    routes: dict[str, int] = {}
    for e in events:
        routes[e.get("route") or "?"] = routes.get(e.get("route") or "?", 0) + 1
    top = sorted(routes.items(), key=lambda kv: kv[1], reverse=True)[:8]
    summary = "Recent activity focus: " + ", ".join(f"{r} ({n})" for r, n in top)
    remember("system", "activity_consolidation", summary, tags=["consolidation", "background"],
             pinned=False, weight=1.2, source="task_worker")
    return {"consolidated": len(events), "summary": summary}


def _task_skill_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    """Mine recent user turns for durable preferences/directives → save as skills."""
    from .skills import extract_skill_candidates, save_skill
    learned: list[str] = []
    with _connect() as con:
        rows = con.execute(
            "SELECT user_text FROM episodes ORDER BY created_at DESC LIMIT 80"
        ).fetchall()
    for r in rows:
        for cand in extract_skill_candidates(r["user_text"] or ""):
            name = cand["phrase"][:48].strip().title() or "Preference"
            sk = save_skill(name, cand["phrase"], tags=[cand["kind"], "auto"],
                            source="skill_extraction")
            learned.append(sk["name"])
    return {"skills_learned": len(learned), "names": learned[:10]}


def _task_research_digest(payload: dict[str, Any]) -> dict[str, Any]:
    rows = _research_rows(limit=50)
    if not rows:
        return {"digest": 0, "note": "no research items"}
    titles = [str(r["query"]) for r in rows[:12]]
    digest = "Recent research topics: " + "; ".join(dict.fromkeys(titles))
    remember("system", "research_digest", digest, tags=["research", "digest", "background"],
             weight=1.1, source="task_worker")
    return {"digest": len(rows), "summary": digest}


def _task_file_scan(payload: dict[str, Any]) -> dict[str, Any]:
    """Summarize the desktop workspace (if FileOps is configured) into a memory."""
    try:
        from .fileops import summarize_workspace
    except Exception:
        return {"note": "fileops unavailable"}
    info = summarize_workspace()
    if info.get("ok"):
        remember("system", "workspace_summary", _json(info.get("summary", {})),
                 tags=["files", "workspace", "background"], weight=1.0, source="task_worker")
    return info


def _run_coro(coro: Any) -> Any:
    """Run an awaitable from sync code, whether or not a loop is already running."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def _task_coder_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Background "Codex" job: build a self-contained project (plan→write→run→fix),
    streaming each step to ``storage/coder_jobs/<task_id>/events.jsonl`` so the chat
    can show it live. Driven by ``shared.coder``."""
    from .coder import create_project, iterate
    task_id = payload.get("_task_id")
    goal = (payload.get("goal") or payload.get("name") or "coding task").strip()
    name = (payload.get("name") or goal[:48]).strip()
    ev_dir = STORAGE_DIR / "coder_jobs" / str(task_id)
    ev_dir.mkdir(parents=True, exist_ok=True)
    ev_path = ev_dir / "events.jsonl"

    def emit(ev: dict[str, Any]) -> None:
        rec = {"at": time.time(), **ev}
        with ev_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    emit({"stage": "start", "goal": goal, "name": name})
    try:
        proj = create_project(name, goal)
        pid = proj["id"]
        emit({"stage": "project", "project_id": pid})

        def on_step(step: dict[str, Any]) -> None:
            run = step.get("run") or {}
            emit({"stage": "step", "step": step.get("step"),
                  "explanation": (step.get("explanation") or "")[:600],
                  "files_changed": step.get("files_changed") or [],
                  "run_ok": run.get("ok"),
                  "stdout": (run.get("stdout") or "")[-1500:],
                  "stderr": (run.get("stderr") or "")[-1500:]})

        out = _run_coro(iterate(pid, f"Build this project end to end: {goal}",
                                max_steps=4, auto_run=True, on_step=on_step))
        final_run = out.get("final_run") or {}
        emit({"stage": "done", "ok": bool(final_run.get("ok")), "project_id": pid,
              "files": out.get("files") or [], "entry": out.get("entry"),
              "steps": len(out.get("steps") or [])})
        return {"ok": True, "project_id": pid, "steps": len(out.get("steps") or []),
                "final_ok": bool(final_run.get("ok")), "events_path": str(ev_path)}
    except Exception as exc:
        emit({"stage": "done", "ok": False, "error": str(exc)[:300]})
        return {"ok": False, "error": str(exc)[:300]}


def _task_neural_propose(payload: dict[str, Any]) -> dict[str, Any]:
    """Background neural proposal generation."""
    from .neural_agent import generate_proposal
    intent = payload.get("intent", "")
    file_path = payload.get("file_path", "")
    instructions = payload.get("instructions", "")
    result = generate_proposal(intent=intent, file_path=file_path, instructions=instructions)
    return result


def _task_sandbox_validate(payload: dict[str, Any]) -> dict[str, Any]:
    """Background sandbox validation of a proposal."""
    from .neural_agent import test_proposal
    proposal_id = payload.get("proposal_id", "")
    result = test_proposal(proposal_id)
    return result


def _task_reflect(payload: dict[str, Any]) -> dict[str, Any]:
    """Background reflection cycle. If gaps are found and auto-evolution is on, queue proposals.

    v2: Also checks agent telemetry for broken tools, slow models, and unhandled patterns.
    """
    from .neural_agent import run_reflection
    from .config import settings

    # Phase 1: Codebase reflection (existing)
    result = run_reflection()
    gaps = result.get("gaps_found", 0)
    proposals = result.get("proposals_generated", 0)

    # Phase 2: Agent telemetry gap analysis (v2)
    try:
        from .agent_telemetry import detect_agent_gaps, auto_propose_from_gaps
        telemetry_gaps = detect_agent_gaps()
        telemetry_proposals = auto_propose_from_gaps(telemetry_gaps)
        result["telemetry_gaps"] = telemetry_gaps
        result["telemetry_proposals"] = len(telemetry_proposals)
    except Exception:
        telemetry_gaps = []
        telemetry_proposals = []
        result["telemetry_gaps"] = []
        result["telemetry_proposals"] = 0

    # Phase 3: Auto-queue proposals from both sources
    if settings.auto_evolution:
        # Queue codebase gaps
        if gaps > 0 and proposals == 0:
            gap_list = result.get("gaps", [])
            for gap in gap_list[:3]:
                gap_text = str(gap)[:120]
                schedule_task(
                    "neural_propose",
                    f"Auto-proposal: {gap_text}",
                    {"intent": f"Address gap: {gap_text}", "instructions": f"Improve this area: {gap_text}"},
                    priority=4,
                )
            result["auto_queued"] = min(len(gap_list), 3)

        # Queue telemetry-driven proposals (v2)
        for prop in telemetry_proposals[:3]:
            schedule_task(
                "neural_propose",
                f"Telemetry-driven: {prop.get('intent', 'improvement')}",
                {"intent": prop.get("intent", ""), "file_path": prop.get("file_path", ""), "instructions": prop.get("description", "")},
                priority=3,
            )
        result["telemetry_auto_queued"] = len(telemetry_proposals[:3])

    return result


_TASK_HANDLERS = {
    "memory_consolidation": _task_memory_consolidation,
    "skill_extraction": _task_skill_extraction,
    "research_digest": _task_research_digest,
    "file_scan": _task_file_scan,
    "coder_job": _task_coder_job,
    "neural_propose": _task_neural_propose,
    "sandbox_validate": _task_sandbox_validate,
    "reflect": _task_reflect,
}


def execute_task(task: dict[str, Any]) -> dict[str, Any]:
    handler = _TASK_HANDLERS.get(task.get("task_type"))
    if not handler:
        return {"ok": True, "status": "skipped", "note": f"no handler for {task.get('task_type')}"}
    try:
        payload = dict(task.get("payload") or {})
        if task.get("task_type") == "coder_job":
            payload["_task_id"] = task.get("id")
        result = handler(payload)
        return {"ok": True, "status": "done", "result": result}
    except Exception as exc:  # never let one bad task kill the worker
        return {"ok": False, "status": "failed", "error": str(exc)[:240]}


def drain_tasks(max_tasks: int = 20) -> dict[str, Any]:
    """Execute up to ``max_tasks`` queued background tasks. Returns a run report."""
    done, failed, skipped = 0, 0, 0
    processed: list[dict[str, Any]] = []
    for _ in range(max_tasks):
        task = _claim_next_task()
        if not task:
            break
        outcome = execute_task(task)
        status = outcome.get("status", "done")
        _finish_task(task["id"], "done" if outcome.get("ok") and status != "failed" else status,
                     outcome.get("result") or {"error": outcome.get("error")})
        processed.append({"id": task["id"], "task_type": task["task_type"], "status": status})
        if status == "failed":
            failed += 1
        elif status == "skipped":
            skipped += 1
        else:
            done += 1
    log_event("brain.tasks_drained", route="brain:tasks", provider="local", model=BRAIN_VERSION,
              ok=True, metadata={"done": done, "failed": failed, "skipped": skipped})
    return {"ok": True, "done": done, "failed": failed, "skipped": skipped, "processed": processed}


def reindex_vectors(batch_size: int = 200) -> dict[str, Any]:
    """One-time migration: index all existing memories, knowledge chunks, and research items for semantic search."""
    if _vector_memory is None:
        return {"ok": False, "error": "vector memory unavailable"}
    counts = {"memory": 0, "knowledge_chunk": 0, "research": 0, "failed": 0}
    try:
        with _connect() as con:
            rows = con.execute("SELECT namespace, key, value, tags_json FROM memories").fetchall()
        for row in rows:
            try:
                tags = _load_json(row["tags_json"], [])
                if _index_vector("memory", f"memory:{row['namespace']}:{row['key']}", f"{row['key']}\n{row['value']}\n{' '.join(tags)}"):
                    counts["memory"] += 1
            except Exception:
                counts["failed"] += 1

        with _connect() as con:
            rows = con.execute("SELECT id, source_type, source_uri, title, chunk_text, tags_json FROM knowledge_chunks").fetchall()
        for row in rows:
            try:
                tags = _load_json(row["tags_json"], [])
                if _index_vector("knowledge_chunk", f"kc:{row['id']}", f"{row['title']}\n{row['chunk_text']}", metadata={"source_type": row["source_type"], "source_uri": row["source_uri"], "title": row["title"], "tags": tags}):
                    counts["knowledge_chunk"] += 1
            except Exception:
                counts["failed"] += 1

        with _connect() as con:
            rows = con.execute("SELECT id, query, title, snippet, url, provider FROM research_items").fetchall()
        for row in rows:
            try:
                if _index_vector("research", f"research:{row['id']}", f"{row['query']} {row['title']} {row['snippet']} {row['url']}", metadata={"provider": row["provider"], "url": row["url"]}):
                    counts["research"] += 1
            except Exception:
                counts["failed"] += 1
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "counts": counts}
    return {"ok": True, "counts": counts}


def brain_status() -> dict[str, Any]:
    ensure_core_memories()
    with _connect() as con:
        counts = {
            "memories": con.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"],
            "episodes": con.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"],
            "knowledge_chunks": con.execute("SELECT COUNT(*) c FROM knowledge_chunks").fetchone()["c"],
            "research_items": con.execute("SELECT COUNT(*) c FROM research_items").fetchone()["c"],
            "background_tasks": con.execute("SELECT COUNT(*) c FROM background_tasks").fetchone()["c"],
        }
    return {
        "ok": True,
        "version": BRAIN_VERSION,
        "db_path": str(BRAIN_DB),
        "counts": counts,
        "directives": BRAIN_DIRECTIVES,
        "background_learning_enabled": os.getenv("SHIMS_BRAIN_BACKGROUND_LEARNING", "true").lower() in {"1", "true", "yes", "on"},
        "self_evolution_mode": "propose -> sandbox validate -> human approve -> apply",
    }


def brain_prompt_addendum(query: str, *, agent: str = "supervisor", limit: int = 8, history: list[dict] | None = None) -> tuple[str, dict[str, Any]]:
    ctx = retrieve_context(query, limit=limit)
    lines = [
        f"SHIMS Omni Brain version: {BRAIN_VERSION}.",
        "Operating directives:",
        *[f"- {d}" for d in BRAIN_DIRECTIVES],
        f"Active routed agent: {agent or 'supervisor'}.",
    ]
    # Inject conversation history awareness
    if history and len(history) > 1:
        lines.append("Conversation history summary (you have full access to all prior turns):")
        for msg in history[-10:]:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))[:200]
            if content:
                lines.append(f"- [{role}] {content}{'...' if len(str(msg.get('content',''))) > 200 else ''}")
    else:
        lines.append("This is the first turn of the conversation — no prior history yet.")
    if ctx.get("context_text"):
        lines.append(ctx["context_text"])
    # Inject the most relevant learned skills (procedural memory), if any.
    try:
        from .skills import relevant_skills
        skills = relevant_skills(query, limit=3)
        if skills:
            lines.append("Learned skills / user preferences to apply:")
            for s in skills:
                lines.append(f"- [{s['name']}] {s['summary']}")
            ctx = {**ctx, "skills": skills}
    except Exception:
        pass
    return "\n".join(lines), ctx
