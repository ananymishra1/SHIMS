from __future__ import annotations
import hashlib, json, sqlite3, time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from .config import ROOT_DIR
STATE_DIR = ROOT_DIR / 'data' / 'state'
STATE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = STATE_DIR / 'shims_telemetry.sqlite3'
LESSONS_PATH = STATE_DIR / 'daily_lessons.json'

@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL'); conn.execute('PRAGMA busy_timeout=5000')
        yield conn
    finally:
        conn.close()
def init_telemetry() -> None:
    with _connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS telemetry_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, iso_ts TEXT NOT NULL, event_type TEXT NOT NULL, route TEXT DEFAULT '', provider TEXT DEFAULT '', model TEXT DEFAULT '', latency_ms REAL DEFAULT 0, ok INTEGER DEFAULT 1, message TEXT DEFAULT '', metadata_json TEXT DEFAULT '{}')")
        conn.execute("CREATE TABLE IF NOT EXISTS document_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, iso_ts TEXT NOT NULL, document_type TEXT NOT NULL, path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, metadata_json TEXT DEFAULT '{}')")
        conn.commit()
def log_event(event_type: str, *, route: str='', provider: str='', model: str='', latency_ms: float=0, ok: bool=True, message: str='', metadata: dict[str, Any] | None=None) -> None:
    init_telemetry(); now=time.time(); iso=datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute('INSERT INTO telemetry_events(ts, iso_ts, event_type, route, provider, model, latency_ms, ok, message, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (now, iso, event_type, route, provider, model, float(latency_ms or 0), 1 if ok else 0, message[:1000], json.dumps(metadata or {}, ensure_ascii=False, default=str)))
        conn.commit()
def file_sha256(path: str | Path) -> str:
    p=Path(path); h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()
def ledger_document(path: str | Path, document_type: str='document', metadata: dict[str, Any] | None=None) -> dict[str, Any]:
    init_telemetry(); p=Path(path)
    if not p.exists(): raise FileNotFoundError(str(p))
    sha=file_sha256(p); stat=p.stat(); now=time.time(); iso=datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute('''INSERT INTO document_ledger(ts, iso_ts, document_type, path, sha256, size_bytes, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET ts=excluded.ts, iso_ts=excluded.iso_ts, document_type=excluded.document_type, sha256=excluded.sha256, size_bytes=excluded.size_bytes, metadata_json=excluded.metadata_json''', (now, iso, document_type, str(p), sha, int(stat.st_size), json.dumps(metadata or {}, ensure_ascii=False, default=str)))
        conn.commit()
    return {'ok': True, 'path': str(p), 'sha256': sha, 'size_bytes': int(stat.st_size), 'document_type': document_type, 'iso_ts': iso}
def verify_document(path: str | Path) -> dict[str, Any]:
    init_telemetry(); p=Path(path)
    with _connect() as conn: row=conn.execute('SELECT * FROM document_ledger WHERE path=?', (str(p),)).fetchone()
    if not row: return {'ok': False, 'reason': 'not_in_ledger', 'path': str(p)}
    if not p.exists(): return {'ok': False, 'reason': 'missing_file', 'path': str(p), 'ledger_sha256': row['sha256']}
    current=file_sha256(p)
    return {'ok': current == row['sha256'], 'path': str(p), 'sha256': current, 'ledger_sha256': row['sha256'], 'document_type': row['document_type'], 'iso_ts': row['iso_ts']}
def _pct(vals: list[float], q: float) -> float:
    vals=sorted(float(v) for v in vals if float(v)>0)
    if not vals: return 0.0
    if len(vals)==1: return round(vals[0],2)
    k=(len(vals)-1)*q; f=int(k); c=min(f+1,len(vals)-1)
    return round(vals[f]*(c-k)+vals[c]*(k-f),2)
def build_daily_lessons(limit: int=500) -> dict[str, Any]:
    init_telemetry()
    with _connect() as conn:
        rows=conn.execute('SELECT * FROM telemetry_events ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        doc_count=conn.execute('SELECT COUNT(*) c FROM document_ledger').fetchone()['c']
    lats=[float(r['latency_ms'] or 0) for r in rows]
    errors=[r for r in rows if not bool(r['ok'])]
    routes={}; providers={}; feedback=[]
    for r in rows:
        routes[r['route'] or r['event_type']] = routes.get(r['route'] or r['event_type'],0)+1
        providers[r['provider'] or 'none'] = providers.get(r['provider'] or 'none',0)+1
        msg=(r['message'] or '').lower()
        if any(x in msg for x in ['wrong','regenerate','repeat','silence','text based','not what i meant']): feedback.append(r['message'][:220])
    lessons={'generated_at': datetime.now(timezone.utc).isoformat(), 'event_count': len(rows), 'document_ledger_count': doc_count, 'latency': {'p50_ms': _pct(lats,.5), 'p95_ms': _pct(lats,.95), 'p99_ms': _pct(lats,.99)}, 'error_count': len(errors), 'top_routes': sorted(routes.items(), key=lambda x:x[1], reverse=True)[:10], 'provider_usage': sorted(providers.items(), key=lambda x:x[1], reverse=True)[:10], 'feedback_samples': feedback[:10], 'prompt_injection': ['Prefer deterministic tools before LLM narration.', 'Never claim a document/media file exists unless ledger/document verification succeeded.', 'Avoid repeated greetings and silence prompts; answer once per user turn.', 'For local-first mode, use Ollama models before cloud providers unless a cloud provider is explicitly selected and configured.']}
    LESSONS_PATH.write_text(json.dumps(lessons, indent=2, ensure_ascii=False), encoding='utf-8')
    return lessons
def load_daily_lessons_text() -> str:
    if not LESSONS_PATH.exists(): return ''
    try: data=json.loads(LESSONS_PATH.read_text(encoding='utf-8'))
    except Exception: return ''
    out=['Daily SHIMS operating lessons:']
    for b in (data.get('prompt_injection') or [])[:6]: out.append(f'- {b}')
    stats=data.get('latency') or {}; out.append(f"- Recent latency p95={stats.get('p95_ms',0)}ms, p99={stats.get('p99_ms',0)}ms.")
    fb=data.get('feedback_samples') or []
    if fb: out.append('- User feedback signals: ' + '; '.join(fb[:3]))
    return '\n'.join(out)
def recent_events(limit: int=50) -> list[dict[str, Any]]:
    init_telemetry()
    with _connect() as conn: rows=conn.execute('SELECT * FROM telemetry_events ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [dict(r) for r in rows]
init_telemetry()
