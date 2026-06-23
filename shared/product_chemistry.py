from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .ai import ask_ai, extract_json_maybe
from .config import GENERATED_DIR, settings
from .database import db
from .enterprise_bmr_corpus import search_corpus
from .privacy_guard import can_use_cloud, classify_sensitivity, sanitize_for_cloud


STYLE_SOURCE_NAMES = [
    "AP-301 STAGE 1 BMR (1).pdf",
    "AP-301 STAGE 2 BMR.pdf",
    "AP-301 STAGE 3 BMR .pdf",
    "BMR DFTA Purification.pdf",
    "Atorvastatin Calcium  -A084 BMR.docx",
    "COA_-DFTA__JKLC_ (2).docx",
    "COA (2R,4S)-5-(biphenyI-4-yl)-4-[(tert-butoxycarbonyl) amino] -2- methylpentanoic acid.docx",
    "ENTRY EXIT  STORE SOP 001 V1.doc",
    "SOP  for HVAC.doc",
    "SOP of SOP QA (Final SOP).doc",
]

SUPPORTED_CLOUD_PROVIDERS = {
    "openai": {"label": "OpenAI", "default_model": "gpt-4.1-mini", "env": "OPENAI_API_KEY"},
    "anthropic": {"label": "Anthropic", "default_model": "claude-sonnet-4-6", "env": "ANTHROPIC_API_KEY"},
    "gemini": {"label": "Google Gemini", "default_model": "gemini-2.5-flash", "env": "GEMINI_API_KEY"},
    "kimi": {"label": "Kimi / Moonshot", "default_model": "kimi-k2", "env": "KIMI_API_KEY"},
    "qwen": {"label": "Qwen / DashScope", "default_model": "qwen-max", "env": "QWEN_API_KEY"},
    "chemdfm": {"label": "ChemDFM", "default_model": "ChemDFM", "env": "CHEMDFM_API_KEY"},
    "deepseek": {"label": "DeepSeek", "default_model": "deepseek-chat", "env": "DEEPSEEK_API_KEY"},
}


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _clean(text: Any, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _like(text: str) -> str:
    return f"%{text.strip()}%"


def _safe_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    try:
        return db.one(sql, params)
    except Exception:
        return None


def _safe_query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return db.query(sql, params)
    except Exception:
        return []


def ensure_product_chemistry_schema() -> None:
    for sql in [
        """
        CREATE TABLE IF NOT EXISTS enterprise_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT NOT NULL UNIQUE,
            product_type TEXT DEFAULT 'api_or_intermediate',
            status TEXT DEFAULT 'active',
            confidence REAL DEFAULT 0.7,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enterprise_product_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            source TEXT DEFAULT 'corpus',
            confidence REAL DEFAULT 0.7,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, alias)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enterprise_product_document_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            original_detected_name TEXT,
            confidence REAL DEFAULT 0.7,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, document_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enterprise_product_review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            detected_name TEXT,
            suggested_product TEXT,
            reason TEXT,
            status TEXT DEFAULT 'needs_review',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(document_id, detected_name)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enterprise_product_chemistry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            summary_json TEXT DEFAULT '{}',
            citations_json TEXT DEFAULT '[]',
            analyzed_by INTEGER,
            analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enterprise_product_route_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            stage_no INTEGER,
            stage_name TEXT,
            raw_materials_json TEXT DEFAULT '[]',
            solvents_json TEXT DEFAULT '[]',
            catalysts_json TEXT DEFAULT '[]',
            equipment_json TEXT DEFAULT '[]',
            conditions_json TEXT DEFAULT '{}',
            yield_notes TEXT,
            qc_controls_json TEXT DEFAULT '[]',
            source_refs_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enterprise_product_chemical_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            stage_no INTEGER,
            change_summary TEXT,
            likely_reaction_class TEXT,
            yield_loss_suspects_json TEXT DEFAULT '[]',
            impurity_hypotheses_json TEXT DEFAULT '[]',
            purge_or_control_strategy TEXT,
            confidence REAL DEFAULT 0.55,
            source_refs_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enterprise_product_impurity_controls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            impurity_name TEXT,
            likely_source TEXT,
            control_strategy TEXT,
            qc_method TEXT,
            acceptance_or_alert TEXT,
            confidence REAL DEFAULT 0.5,
            source_refs_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enterprise_product_route_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            route_name TEXT NOT NULL,
            status TEXT DEFAULT 'proposed',
            route_json TEXT DEFAULT '{}',
            cost_json TEXT DEFAULT '{}',
            scores_json TEXT DEFAULT '{}',
            citations_json TEXT DEFAULT '[]',
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rm_price_book (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            price_per_kg REAL NOT NULL DEFAULT 0,
            currency TEXT DEFAULT 'INR',
            supplier TEXT,
            source_type TEXT DEFAULT 'internal',
            verification_status TEXT DEFAULT 'unverified',
            verified_by INTEGER,
            verified_at TEXT,
            valid_from TEXT,
            valid_to TEXT,
            notes TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rm_price_quote_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_name TEXT NOT NULL,
            suggested_price_per_kg REAL DEFAULT 0,
            currency TEXT DEFAULT 'INR',
            supplier TEXT,
            source_url TEXT,
            source_title TEXT,
            source_type TEXT DEFAULT 'web_or_ai',
            status TEXT DEFAULT 'unverified',
            notes TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rm_price_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            price_id INTEGER,
            action TEXT NOT NULL,
            details_json TEXT DEFAULT '{}',
            user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enterprise_document_style_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_name TEXT NOT NULL UNIQUE,
            document_kind TEXT NOT NULL,
            source_document_ids_json TEXT DEFAULT '[]',
            style_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'active',
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enterprise_document_style_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL,
            block_type TEXT NOT NULL,
            label TEXT,
            content TEXT,
            sequence_no INTEGER DEFAULT 0,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enterprise_ai_provider_keys (
            provider TEXT PRIMARY KEY,
            label TEXT,
            encrypted_key TEXT NOT NULL,
            default_model TEXT,
            base_url TEXT,
            enabled INTEGER DEFAULT 1,
            last_test_status TEXT,
            last_test_at TEXT,
            updated_by INTEGER,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]:
        db.execute(sql)


def _fernet() -> Fernet:
    raw = hashlib.sha256(str(settings.secret_key).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return ""


def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    return secret[:4] + "..." + secret[-4:] if len(secret) > 10 else "****"


_JUNK_NAMES = {
    "",
    "of",
    "for",
    "stage",
    "bmr",
    "coa",
    "document",
    "scan",
    "scanned",
    "material",
    "balance",
    "final",
    "lost",
    "analysis",
    "flow",
    "chart",
    "iv",
    "list",
}
_KNOWN_ALIASES = {
    "cost of fluconazole": "Fluconazole",
    "fluconazole consumption": "Fluconazole",
    "floconazole flow chart": "Fluconazole",
    "floconazole": "Fluconazole",
    "coa dfta jklc": "DFTA",
    "dfta purification": "DFTA",
    "bmr dfta purification": "DFTA",
    "telmisartan & material balance": "Telmisartan",
    "telmi": "Telmisartan",
    "atorvastatin calcium a084": "Atorvastatin Calcium",
    "atorvastatin calcium amorphous": "Atorvastatin Calcium",
    "ap 301": "AP-301",
    "ap-301": "AP-301",
    "tmsi bmr": "TMSI",
    "tmsi": "TMSI",
    "moxi": "L011 Moxi A",
    "moxifloxacin": "L011 Moxi A",
    "moxifloxacin stage a": "L011 Moxi A",
    "l011 moxi": "L011 Moxi A",
    "l011 moxi a": "L011 Moxi A",
    "l011 moxi stage a": "L011 Moxi A",
}


def _title_case_chem(text: str) -> str:
    keep_upper = {"AP", "DFTA", "TMSI", "HCL", "API", "MDC", "ROS"}
    parts = []
    for part in re.split(r"(\s+|-)", text):
        if not part or part.isspace() or part == "-":
            parts.append(part)
        elif part.upper() in keep_upper or re.fullmatch(r"[A-Z]{2,}\d*", part):
            parts.append(part.upper())
        else:
            parts.append(part[:1].upper() + part[1:].lower())
    return "".join(parts).strip()


def canonicalize_product_name(name: str, filename: str = "") -> dict[str, Any]:
    raw = _clean(name or filename, 240)
    file_hint = _clean(filename, 240)
    if re.search(r"(?:^|[\s_-])(img|image|scan|scanner|scanned|wa)\d*|扫描全能王", (raw + " " + file_hint), re.I):
        return {"canonical": "", "confidence": 0.1, "reason": "scanner_or_image_filename", "original": raw}
    lowered = raw.lower()
    lowered = re.sub(r"\.(pdf|docx?|xlsx?|jpg|png|txt)$", "", lowered)
    lowered = re.sub(r"[_()\[\],-]+", " ", lowered)
    lowered = lowered.replace("—", " ").replace("–", " ")
    lowered = re.sub(r"\b(copy|latest|document|doc|file|scan|scanner|扫描全能王)\b", " ", lowered)
    lowered = re.sub(r"\b(stage|stg)\s*\d+\b", " ", lowered)
    lowered = re.sub(r"\b(bmr|bpcr|coa|ros|rm|cost|consumption|material balance|route|process|brief)\b", " ", lowered)
    lowered = re.sub(r"\bno\.?\s*\d+\b", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip(" -_")
    alias_key = raw.lower().strip()
    compact_key = lowered.lower().strip()
    if alias_key in _KNOWN_ALIASES:
        return {"canonical": _KNOWN_ALIASES[alias_key], "confidence": 0.96, "reason": "known_alias", "original": raw}
    if compact_key in _KNOWN_ALIASES:
        return {"canonical": _KNOWN_ALIASES[compact_key], "confidence": 0.94, "reason": "known_alias_cleaned", "original": raw}
    if re.search(r"\bap[-\s]?301\b", raw, re.I):
        return {"canonical": "AP-301", "confidence": 0.95, "reason": "pattern_ap301", "original": raw}
    if re.search(r"\bdfta\b", raw, re.I):
        return {"canonical": "DFTA", "confidence": 0.9, "reason": "pattern_dfta", "original": raw}
    if re.search(r"\btmsi\b", raw, re.I):
        return {"canonical": "TMSI", "confidence": 0.9, "reason": "pattern_tmsi", "original": raw}
    if re.search(r"\b(moxi|moxifloxacin|l011)\b", raw, re.I):
        return {"canonical": "L011 Moxi A", "confidence": 0.9, "reason": "pattern_moxi", "original": raw}
    if re.search(r"\bfl[uo]conazole\b", raw, re.I):
        return {"canonical": "Fluconazole", "confidence": 0.88, "reason": "pattern_fluconazole_typo", "original": raw}
    if re.search(r"difluorophenyl.*triazol|triazol.*difluorophenyl", raw, re.I):
        return {"canonical": "Fluconazole", "confidence": 0.88, "reason": "chemical_name_fluconazole", "original": raw}
    for known in ("Fluconazole", "Atorvastatin Calcium", "Telmisartan", "Rosuvastatin", "Losartan potassium", "Olmesartan Medoxomil"):
        if known.lower().split()[0] in raw.lower():
            return {"canonical": known, "confidence": 0.86, "reason": "known_product_token", "original": raw}
    candidate = lowered or raw
    candidate = re.sub(r"\b\d{1,2}\b$", "", candidate).strip(" -_")
    tokens = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9+-]*", candidate) if t.lower() not in _JUNK_NAMES]
    if not tokens:
        return {"canonical": "", "confidence": 0.1, "reason": "junk_or_empty", "original": raw}
    if len(tokens) == 1 and tokens[0].lower() in {"final", "lost", "analysis", "iv"}:
        return {"canonical": "", "confidence": 0.2, "reason": "generic_filename_fragment", "original": raw}
    if sum(ch.isalpha() for ch in " ".join(tokens)) < 3:
        return {"canonical": "", "confidence": 0.2, "reason": "mostly_numeric_fragment", "original": raw}
    canonical = _title_case_chem(" ".join(tokens[:5]))
    confidence = 0.72 if len(tokens) >= 1 and len(canonical) >= 3 else 0.35
    if len(tokens) == 1 and tokens[0].lower() in _JUNK_NAMES:
        confidence = 0.1
    return {"canonical": canonical, "confidence": confidence, "reason": "heuristic_filename_heading", "original": raw}


def get_canonical_product_name(name: str) -> str:
    ensure_product_chemistry_schema()
    raw = _clean(name, 240)
    if not raw:
        return ""
    row = db.one("SELECT canonical_name FROM enterprise_products WHERE lower(canonical_name)=lower(?)", (raw,))
    if row:
        return row["canonical_name"]
    alias = db.one(
        """
        SELECT p.canonical_name
        FROM enterprise_product_aliases a
        JOIN enterprise_products p ON p.id=a.product_id
        WHERE lower(a.alias)=lower(?)
        ORDER BY a.confidence DESC
        LIMIT 1
        """,
        (raw,),
    )
    if alias:
        return alias["canonical_name"]
    return raw


def _upsert_product(canonical: str, confidence: float = 0.7, metadata: dict[str, Any] | None = None) -> int:
    row = db.one("SELECT id FROM enterprise_products WHERE lower(canonical_name)=lower(?)", (canonical,))
    if row:
        db.execute(
            "UPDATE enterprise_products SET status='active', confidence=MAX(confidence, ?), metadata_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (confidence, _json(metadata or {}), row["id"]),
        )
        return int(row["id"])
    return int(db.execute(
        "INSERT INTO enterprise_products(canonical_name, confidence, metadata_json) VALUES (?, ?, ?)",
        (canonical, confidence, _json(metadata or {})),
    ))


def normalize_corpus_products(user_id: int | None = None) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    docs = db.query("SELECT id, original_name, product_name, metadata_json FROM enterprise_bmr_documents ORDER BY id")
    linked = reviewed = updated = 0
    products: dict[str, int] = {}
    for doc in docs:
        detected = doc.get("product_name") or doc.get("original_name") or ""
        norm = canonicalize_product_name(detected, doc.get("original_name") or "")
        canonical = norm["canonical"]
        confidence = float(norm["confidence"])
        if not canonical or confidence < 0.5:
            db.execute("DELETE FROM enterprise_product_document_links WHERE document_id=?", (doc["id"],))
            if doc.get("product_name"):
                meta = _load_json(doc.get("metadata_json"), {})
                meta.setdefault("rejected_detected_product_name", doc.get("product_name"))
                meta["product_review_reason"] = norm["reason"]
                db.execute(
                    "UPDATE enterprise_bmr_documents SET product_name='', metadata_json=? WHERE id=?",
                    (_json(meta), doc["id"]),
                )
                updated += 1
            db.execute(
                """
                INSERT OR IGNORE INTO enterprise_product_review_queue(document_id, detected_name, suggested_product, reason)
                VALUES (?, ?, ?, ?)
                """,
                (doc["id"], detected, canonical, norm["reason"]),
            )
            reviewed += 1
            continue
        product_id = _upsert_product(canonical, confidence, {"normalization_reason": norm["reason"]})
        products[canonical] = product_id
        db.execute(
            "INSERT OR IGNORE INTO enterprise_product_aliases(product_id, alias, source, confidence) VALUES (?, ?, ?, ?)",
            (product_id, detected, "corpus", confidence),
        )
        db.execute(
            """
            INSERT INTO enterprise_product_document_links(product_id, document_id, original_detected_name, confidence, reason)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(product_id, document_id) DO UPDATE SET
                original_detected_name=excluded.original_detected_name,
                confidence=excluded.confidence,
                reason=excluded.reason
            """,
            (product_id, doc["id"], detected, confidence, norm["reason"]),
        )
        linked += 1
        if doc.get("product_name") != canonical and confidence >= 0.78:
            meta = _load_json(doc.get("metadata_json"), {})
            meta.setdefault("original_detected_product_name", doc.get("product_name"))
            db.execute("UPDATE enterprise_bmr_documents SET product_name=?, metadata_json=? WHERE id=?", (canonical, _json(meta), doc["id"]))
            updated += 1
    db.execute(
        """
        UPDATE enterprise_products
        SET status='review', updated_at=CURRENT_TIMESTAMP
        WHERE status='active'
          AND id NOT IN (SELECT DISTINCT product_id FROM enterprise_product_document_links)
        """
    )
    db.audit(user_id, "normalize", "enterprise_products", "", {"linked": linked, "review_queue": reviewed, "updated_documents": updated})
    return {
        "ok": True,
        "documents_seen": len(docs),
        "linked": linked,
        "review_queue": reviewed,
        "updated_documents": updated,
        "products": [{"product_name": k, "product_id": v} for k, v in sorted(products.items())],
    }


def get_product_id(product_name: str, *, create: bool = True) -> int | None:
    ensure_product_chemistry_schema()
    norm = canonicalize_product_name(product_name)
    canonical = norm["canonical"] or _clean(product_name, 180)
    if not canonical:
        return None
    row = db.one("SELECT id FROM enterprise_products WHERE lower(canonical_name)=lower(?)", (canonical,))
    if row:
        return int(row["id"])
    if create:
        return _upsert_product(canonical, norm["confidence"])
    return None


def get_canonical_product_name(product_name: str) -> str:
    norm = canonicalize_product_name(product_name)
    return norm["canonical"] or _clean(product_name, 180)


def product_search_terms(product_name: str) -> list[str]:
    canonical = get_canonical_product_name(product_name)
    terms: list[str] = []
    for value in (product_name, canonical):
        cleaned = _clean(value, 180)
        if cleaned and cleaned.lower() not in {t.lower() for t in terms}:
            terms.append(cleaned)
    if canonical:
        alias_rows = _safe_query(
            """
            SELECT a.alias
            FROM enterprise_product_aliases a
            JOIN enterprise_products p ON p.id=a.product_id
            WHERE lower(p.canonical_name)=lower(?)
            ORDER BY a.confidence DESC LIMIT 20
            """,
            (canonical,),
        )
        doc_rows = _safe_query(
            """
            SELECT original_name, product_name
            FROM enterprise_bmr_documents
            WHERE product_name LIKE ? OR original_name LIKE ?
            ORDER BY id DESC LIMIT 30
            """,
            (_like(canonical), _like(canonical)),
        )
        for row in alias_rows:
            value = _clean(row.get("alias"), 180)
            if value and value.lower() not in {t.lower() for t in terms}:
                terms.append(value)
        for row in doc_rows:
            for key in ("product_name", "original_name"):
                value = _clean(row.get(key), 180)
                if value and value.lower() not in {t.lower() for t in terms}:
                    terms.append(value)
    return terms[:30]


def _product_where(columns: list[str], terms: list[str]) -> tuple[str, list[Any]]:
    active = [t for t in terms if t]
    if not active:
        return "1=0", []
    clauses: list[str] = []
    params: list[Any] = []
    for col in columns:
        for term in active:
            clauses.append(f"{col} LIKE ?")
            params.append(_like(term))
    return "(" + " OR ".join(clauses) + ")", params


def _facts(product_name: str) -> list[dict[str, Any]]:
    terms = product_search_terms(product_name)
    where, params = _product_where(["d.product_name", "d.original_name"], terms)
    return db.query(
        f"""
        SELECT f.*, d.original_name, d.id document_id
        FROM enterprise_bmr_facts f
        JOIN enterprise_bmr_documents d ON d.id=f.document_id
        WHERE {where}
        ORDER BY f.confidence DESC, f.created_at DESC LIMIT 1000
        """,
        tuple(params),
    )


def _values(facts: list[dict[str, Any]], fact_type: str, limit: int = 25) -> list[str]:
    out: list[str] = []
    for fact in facts:
        if fact.get("fact_type") != fact_type:
            continue
        value = _clean(fact.get("value"), 240)
        if value and value.lower() not in {v.lower() for v in out}:
            out.append(value)
        if len(out) >= limit:
            break
    return out


def _is_junk(value: str) -> bool:
    low = value.lower()
    if any(k in low for k in ("mobile phase", "solubility", "soluble in", "raw material uom", "no. raw material", "s.no", "packing material indent", "back the filtrate")):
        return True
    if "|" in value:
        return True
    if len(value) > 120:
        return True
    # Drop table-of-contents style headers like "4.2.1 Key starting Material"
    if re.match(r'^\d+(\.\d+)+\s+\w', value):
        return True
    return False


def _push_unique(values: list[str], value: str, limit: int = 80) -> None:
    cleaned = _clean(value, 260).strip(" |:-")
    if not cleaned or len(cleaned) < 3:
        return
    if cleaned.lower() in {"appendix", "raw material indent", "packing material indent", "raw material", "raw materials", "raw material details"}:
        return
    if _is_junk(cleaned):
        return
    # Strip leading enumeration like "10. Methanol" -> "Methanol".
    cleaned = re.sub(r'^\d+[\.\)\-]\s+', '', cleaned)
    if cleaned.lower() not in {v.lower() for v in values} and len(values) < limit:
        values.append(cleaned)


def _mine_materials_from_facts(facts: list[dict[str, Any]]) -> dict[str, list[str]]:
    materials: list[str] = []
    solvents: list[str] = []
    controls: list[str] = []
    for fact in facts:
        value = _clean(fact.get("value"), 600)
        low = value.lower()
        if not value:
            continue
        if "appendix" in low and "material indent" in low:
            continue
        if "starting material" in low and "|" in value:
            cells = [c.strip() for c in value.split("|") if c.strip()]
            for idx, cell in enumerate(cells):
                if cell.lower().startswith("starting material") and idx + 1 < len(cells):
                    _push_unique(materials, cells[idx + 1])
        if value.lstrip().startswith("|"):
            cells = [c.strip() for c in value.split("|") if c.strip()]
            if len(cells) >= 3 and any(c.lower() in {"kg", "kgs", "g", "gm", "l", "ltr", "litre", "litres"} for c in cells[:5]):
                candidate = cells[0]
                if not re.search(r"\b(ensure|stir|cool|send|record|fix|stop|wash cake|charge above|date|time)\b", candidate, re.I):
                    if any(k in candidate.lower() for k in ("water", "methanol", "ethanol", "acetone", "toluene", "methylene", "dichloro", "ethyl acetate", "ipa", "isopropyl")):
                        _push_unique(solvents, candidate)
                    else:
                        _push_unique(materials, candidate)
        charge = re.search(
            r"\bcharge\s+(?:above obtained wet cake\s+|)(?:[\d.]+\s*(?:kg|kgs|g|gm|l|ltr|litres?)\s+(?:of\s+)?)?([A-Za-z][A-Za-z0-9 /(),.-]{2,90}?)(?:\s+(?:in\s*to|into|to)\s+(?:the\s+)?reactor|\s*$)",
            value,
            re.I,
        )
        if charge:
            candidate = re.sub(r"\s+Lot-\d+.*$", "", charge.group(1), flags=re.I).strip()
            if not re.search(r"\b(reaction mass|wet cake|above obtained|the reactor)\b", candidate, re.I):
                if "water" in candidate.lower():
                    _push_unique(solvents, candidate)
                else:
                    _push_unique(materials, candidate)
        if any(k in low for k in ("exothermic", "temperature", "tlc", "impurity", "hold", "stir", "cool")):
            _push_unique(controls, value, limit=60)
    return {"materials": materials, "solvents": solvents, "controls": controls}


def _is_process_medium(value: str) -> bool:
    low = value.lower()
    if any(k in low for k in ("hot water circulation", "cooling tower water", "applying cooling water", "cooling water")):
        return False
    if any(k in low for k in ("nmt", "recovery", "mother liquor", "soluble", "solubility", "mobile phase", "|", "solvent media", "w/v", "ltr")):
        return False
    if any(k in low for k in ("purified water", "methanol", "ethanol", "acetone", "toluene", "ethyl acetate", "methylene chloride", "dichloromethane", "ipa", "isopropyl")):
        return True
    return False


def _citations(rows: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        doc_id = int(row.get("document_id") or row.get("id") or 0)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append({"document_id": doc_id, "title": row.get("original_name") or row.get("title") or "", "source_ref": row.get("source_ref", "")})
        if len(out) >= limit:
            break
    return out


def analyze_product_chemistry(product_name: str, user_id: int | None = None) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    canonical = get_canonical_product_name(product_name)
    if not get_product_id(canonical, create=False):
        normalize_corpus_products(user_id)
    product_id = get_product_id(canonical)
    if not product_id:
        return {"ok": False, "message": "Product is required"}
    terms = product_search_terms(canonical)
    facts = _facts(canonical)
    combined_hits: list[dict[str, Any]] = []
    seen_hits: set[int] = set()
    for term in terms[:8] or [canonical]:
        for hit in search_corpus(term, limit=12).get("hits", []):
            doc_id = int(hit.get("document_id") or 0)
            if doc_id in seen_hits:
                continue
            seen_hits.add(doc_id)
            combined_hits.append(hit)
    search = {"hits": combined_hits[:20]}
    citations = _citations(facts) or [{"document_id": h.get("document_id"), "title": h.get("title"), "source_ref": h.get("citation")} for h in search.get("hits", [])]
    mined = _mine_materials_from_facts(facts)
    raw_materials = []
    for value in _values(facts, "raw_material") + mined["materials"]:
        if "appendix" not in value.lower() and "material indent" not in value.lower():
            _push_unique(raw_materials, value)
    solvents = []
    for value in _values(facts, "solvent") + mined["solvents"]:
        if _is_process_medium(value):
            _push_unique(solvents, value)
    equipment = _values(facts, "equipment")
    controls = []
    for value in _values(facts, "control") + mined["controls"]:
        _push_unique(controls, value)
    yields = _values(facts, "yield")
    qc_tests = _values(facts, "qc_test")
    safety = _values(facts, "safety")
    stage_where, stage_params = _product_where(["pm.product_name", "d.original_name", "d.product_name"], terms)
    stages = db.query(
        f"""
        SELECT pm.*, d.original_name
        FROM enterprise_bmr_process_maps pm
        JOIN enterprise_bmr_documents d ON d.id=pm.document_id
        WHERE {stage_where}
        ORDER BY pm.created_at DESC LIMIT 20
        """,
        tuple(stage_params),
    )
    def _names(values: list[Any]) -> list[str]:
        out: list[str] = []
        for v in values:
            if isinstance(v, dict):
                n = v.get("name") or v.get("material") or v.get("raw_material")
                if n:
                    out.append(str(n))
            elif v:
                out.append(str(v))
        return out

    stage_payloads: list[dict[str, Any]] = []
    for row in stages:
        loaded = _load_json(row.get("stages_json"), [])
        for idx, item in enumerate(loaded or [], 1):
            if isinstance(item, dict):
                stage_payloads.append({**item, "source": row.get("original_name"), "document_id": row.get("document_id")})
            else:
                stage_payloads.append({"stage_no": idx, "stage_name": str(item), "source": row.get("original_name"), "document_id": row.get("document_id")})
    if not stage_payloads:
        for idx, value in enumerate(_values(facts, "stage", 20), 1):
            stage_payloads.append({"stage_no": idx, "stage_name": value, "source": "enterprise_bmr_facts"})

    # Enrich keyword-extracted lists with structured raw-material/solvent tables and narrative stages.
    bmr_text = ""
    try:
        from .bmr_raw_material_parser import (
            fetch_best_bmr_summary,
            parse_raw_materials_from_summary,
            parse_solvents_from_summary,
            extract_route_stages_from_text,
        )
        bmr_text = fetch_best_bmr_summary(canonical)
        for r in parse_raw_materials_from_summary(bmr_text):
            _push_unique(raw_materials, r["name"])
        for s in parse_solvents_from_summary(bmr_text):
            _push_unique(solvents, s["name"])
        # Always try to derive structured stages from the full narrative BMR text and merge with process-map stages.
        derived = extract_route_stages_from_text(bmr_text)
        if derived:
            if not stage_payloads or all(
                re.match(r'^stage\s*\d+$', str(st.get('stage_name') or '').lower()) for st in stage_payloads
            ):
                stage_payloads = derived
            else:
                by_stage: dict[int, dict[str, Any]] = {int(s.get('stage_no') or i): s for i, s in enumerate(stage_payloads, 1)}
                for ds in derived:
                    sn = int(ds.get('stage_no') or 0)
                    if sn in by_stage:
                        existing = by_stage[sn]
                        for field in ('raw_materials', 'solvents', 'equipment'):
                            if not existing.get(field):
                                existing[field] = ds.get(field, [])
                        if not existing.get('conditions'):
                            existing['conditions'] = ds.get('conditions', {})
                    else:
                        by_stage[sn] = ds
                stage_payloads = sorted(by_stage.values(), key=lambda s: int(s.get('stage_no') or 999))
        # Normalize lists of names.
        for stage in stage_payloads:
            stage['raw_materials'] = _names(stage.get('raw_materials') or [])
            stage['solvents'] = _names(stage.get('solvents') or [])
            stage['equipment'] = _names(stage.get('equipment') or [])
    except Exception:
        pass

    summary = {
        "product_name": canonical,
        "raw_materials": raw_materials,
        "intermediates": [v for v in raw_materials if any(k in v.lower() for k in ("intermediate", "stage", "df", "ap-"))],
        "solvents": solvents,
        "catalysts_reagents": [v for v in raw_materials if any(k in v.lower() for k in ("acid", "base", "catalyst", "chloride", "hydroxide", "carbonate", "iodide"))],
        "equipment": equipment,
        "controls": controls,
        "yield_notes": yields,
        "qc_tests": qc_tests,
        "safety_notes": safety,
        "missing_data": [
            label for label, values in {
                "verified raw-material prices": list_verified_prices(canonical).get("prices", []),
                "explicit reaction SMILES": [],
                "approved COA template": qc_tests,
                "scale-up trial history": _safe_query("SELECT id FROM scale_up_trials WHERE product_name LIKE ? LIMIT 1", (_like(canonical),)),
            }.items() if not values
        ],
    }
    db.execute(
        """
        INSERT INTO enterprise_product_chemistry(product_id, product_name, summary_json, citations_json, analyzed_by, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET
            summary_json=excluded.summary_json,
            citations_json=excluded.citations_json,
            analyzed_by=excluded.analyzed_by,
            analyzed_at=excluded.analyzed_at
        """,
        (product_id, canonical, _json(summary), _json(citations), user_id, _now()),
    )
    db.execute("DELETE FROM enterprise_product_route_stages WHERE product_id=?", (product_id,))
    db.execute("DELETE FROM enterprise_product_chemical_changes WHERE product_id=?", (product_id,))
    db.execute("DELETE FROM enterprise_product_impurity_controls WHERE product_id=?", (product_id,))
    for idx, stage in enumerate(stage_payloads[:30], 1):
        stage_no = int(stage.get("stage_no") or idx)
        source_refs = [{"document_id": stage.get("document_id"), "title": stage.get("source")}]
        stage_name = _clean(stage.get("stage_name") or stage.get("name") or f"Stage {stage_no}", 180)
        stage_text = " ".join([stage_name, _clean(stage.get("notes"), 600), _clean(stage.get("description"), 600)])
        # If the stage was derived from narrative extraction it already carries structured data.
        stage_rms = stage.get("raw_materials") or [v for v in raw_materials if v.lower() in stage_text.lower()][:10]
        stage_solvents = stage.get("solvents") or [v for v in solvents if v.lower() in stage_text.lower()][:10] or solvents[:3]
        stage_equipment = stage.get("equipment") or [v for v in equipment if v.lower() in stage_text.lower()][:8] or equipment[:2]
        stage_conditions = stage.get("conditions") or {}
        if not stage_conditions:
            stage_conditions = {"notes": stage.get("notes") or stage.get("description") or ""}
        db.execute(
            """
            INSERT INTO enterprise_product_route_stages(
                product_id, stage_no, stage_name, raw_materials_json, solvents_json,
                catalysts_json, equipment_json, conditions_json, yield_notes, qc_controls_json, source_refs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id, stage_no, stage_name, _json(stage_rms), _json(stage_solvents), _json([]),
                _json(stage_equipment), _json(stage_conditions), "; ".join(yields[:4]),
                _json(qc_tests[:8] + controls[:8]), _json(source_refs),
            ),
        )
        reaction_class = _infer_reaction_class(stage_text, stage_solvents, controls)
        db.execute(
            """
            INSERT INTO enterprise_product_chemical_changes(
                product_id, stage_no, change_summary, likely_reaction_class,
                yield_loss_suspects_json, impurity_hypotheses_json, purge_or_control_strategy,
                confidence, source_refs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id, stage_no,
                f"{canonical} {stage_name}: corpus evidence suggests a controlled process step involving {', '.join((stage_rms or raw_materials)[:3]) or 'recorded raw materials'} in {', '.join(stage_solvents[:3]) or 'recorded media'}.",
                reaction_class,
                _json(_yield_loss_suspects(stage_solvents, controls, yields)),
                _json(_impurity_hypotheses(stage_solvents, safety, controls)),
                _purge_strategy(stage_solvents, qc_tests, controls),
                0.62 if source_refs else 0.45,
                _json(source_refs),
            ),
        )
    for idx, qc in enumerate((qc_tests or controls or ["Review impurity trend from COA/BMR evidence"])[:20], 1):
        db.execute(
            """
            INSERT INTO enterprise_product_impurity_controls(
                product_id, impurity_name, likely_source, control_strategy, qc_method,
                acceptance_or_alert, confidence, source_refs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id, f"{canonical} impurity/control {idx}",
                "Likely linked to stage conditions, solvent quality, hold time, or raw-material quality.",
                _clean(qc, 500),
                _clean(qc, 300),
                "Human R&D/QC review required before setting limits.",
                0.55,
                _json(citations[:5]),
            ),
        )
    db.audit(user_id, "analyze", "enterprise_product_chemistry", product_id, {"product_name": canonical, "facts": len(facts), "stages": len(stage_payloads)})
    return {"ok": True, "product_name": canonical, "product_id": product_id, "summary": summary, "stages": list_route_stages(canonical), "chemical_changes": list_chemical_changes(canonical), "citations": citations}


def enrich_all_product_chemistry(limit: int | None = None, user_id: int | None = None) -> dict[str, Any]:
    """Re-analyze product chemistry for every product in the catalog.

    This is the cleanup/scrub button: it pulls the latest BMR corpus extraction,
    rebuilds stage-level raw materials / solvents / equipment / conditions, and
    refreshes impurity hypotheses.
    """
    ensure_product_chemistry_schema()
    rows = db.query("SELECT id, canonical_name FROM enterprise_products ORDER BY canonical_name")
    if limit:
        rows = rows[: int(limit)]
    results: list[dict[str, Any]] = []
    ok_count = 0
    fail_count = 0
    total_stages = 0
    for row in rows:
        try:
            res = analyze_product_chemistry(row["canonical_name"], user_id)
            results.append({"name": row["canonical_name"], "ok": res.get("ok"), "stages": len(res.get("stages") or []), "error": None})
            if res.get("ok"):
                ok_count += 1
                total_stages += len(res.get("stages") or [])
            else:
                fail_count += 1
        except Exception as exc:
            fail_count += 1
            results.append({"name": row["canonical_name"], "ok": False, "stages": 0, "error": str(exc)[:200]})
    db.audit(user_id, "enrich_all", "enterprise_product_chemistry", None, {"ok": ok_count, "failed": fail_count, "products": len(rows)})
    return {
        "ok": True,
        "processed": len(rows),
        "succeeded": ok_count,
        "failed": fail_count,
        "total_stages": total_stages,
        "results": results,
    }


def product_completeness_report() -> dict[str, Any]:
    """Return a coverage report showing which products have complete stage knowledge."""
    ensure_product_chemistry_schema()
    prods = db.query("SELECT id, canonical_name FROM enterprise_products ORDER BY canonical_name")
    chem_rows = {r["product_id"]: r for r in db.query("SELECT product_id FROM enterprise_product_chemistry")}
    impurity_counts = {r["product_id"]: r["c"] for r in db.query("SELECT product_id, COUNT(*) c FROM enterprise_product_impurity_controls GROUP BY product_id")}
    stages = db.query(
        """SELECT product_id, stage_no, stage_name,
                  raw_materials_json, solvents_json, equipment_json, conditions_json
           FROM enterprise_product_route_stages ORDER BY product_id, stage_no"""
    )
    from collections import defaultdict
    stage_by_prod: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for s in stages:
        stage_by_prod[s["product_id"]].append(s)

    def has_data(val: Any) -> bool:
        if not val:
            return False
        try:
            d = json.loads(val)
            return bool(d)
        except Exception:
            return bool(str(val).strip())

    complete: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    empty: list[dict[str, Any]] = []
    for p in prods:
        pid = p["id"]
        name = p["canonical_name"]
        chem = chem_rows.get(pid)
        prod_stages = stage_by_prod.get(pid, [])
        if not chem or not prod_stages:
            empty.append({"id": pid, "name": name, "reason": "no chemistry or no stages"})
            continue
        missing: set[str] = set()
        stage_issues = 0
        for s in prod_stages:
            stage_missing: list[str] = []
            if not has_data(s["raw_materials_json"]) and not has_data(s["solvents_json"]):
                stage_missing.append("raw_materials/solvents")
            if not has_data(s["equipment_json"]):
                stage_missing.append("equipment")
            if not has_data(s["conditions_json"]):
                stage_missing.append("conditions")
            if stage_missing:
                stage_issues += 1
                missing.update(stage_missing)
        has_impurities = impurity_counts.get(pid, 0) > 0
        entry = {
            "id": pid,
            "name": name,
            "stages": len(prod_stages),
            "stage_issues": stage_issues,
            "missing_fields": sorted(missing),
            "impurities": impurity_counts.get(pid, 0),
        }
        if stage_issues == 0 and has_impurities:
            complete.append(entry)
        else:
            partial.append(entry)
    return {
        "ok": True,
        "total_products": len(prods),
        "complete": len(complete),
        "partial": len(partial),
        "empty": len(empty),
        "complete_products": complete,
        "partial_products": partial[:50],
        "empty_products": empty[:50],
    }


def cleanup_junk_products(user_id: int | None = None) -> dict[str, Any]:
    """Mark non-product catalog entries (image names, fragments, missing data) as 'review'."""
    ensure_product_chemistry_schema()
    junk_patterns = [
        r'^img\s',
        r'^stage[-\s]',
        r'^final\b',
        r'^jk\s+master',
        r'^lost[-\s]?\d*',
        r'^iv\s+analysis',
        r'^sop\s',
        r'^entry\s+exit',
        r'^\d+(\s+\d+)*$',
        r'^.{1,3}$',
    ]
    rows = db.query("SELECT id, canonical_name FROM enterprise_products WHERE status='active'")
    marked: list[dict[str, Any]] = []
    for row in rows:
        name = row["canonical_name"]
        chem = db.one("SELECT id FROM enterprise_product_chemistry WHERE product_id=?", (row["id"],))
        stages = db.one("SELECT id FROM enterprise_product_route_stages WHERE product_id=? LIMIT 1", (row["id"],))
        is_junk = any(re.search(p, name, re.I) for p in junk_patterns)
        if is_junk or (not chem and not stages):
            db.execute("UPDATE enterprise_products SET status='review' WHERE id=?", (row["id"],))
            marked.append({"id": row["id"], "name": name, "reason": "junk_name" if is_junk else "no_chemistry_or_stages"})
    db.audit(user_id, "cleanup_junk", "enterprise_products", None, {"marked": len(marked)})
    return {"ok": True, "marked": len(marked), "products": marked}


def _infer_reaction_class(text: str, solvents: list[str], controls: list[str]) -> str:
    hay = " ".join([text, " ".join(solvents), " ".join(controls)]).lower()
    if any(k in hay for k in ("purification", "crystall", "filtration", "drying")):
        return "purification/crystallization"
    if any(k in hay for k in ("hydrogen", "pd/c", "catalyst")):
        return "catalytic transformation"
    if any(k in hay for k in ("acid", "chloride", "ester")):
        return "acid/base or acylation/esterification step"
    if any(k in hay for k in ("coupling", "substitution", "iodide", "bromide", "chloride")):
        return "substitution/coupling step"
    return "process step requiring chemist review"


def _yield_loss_suspects(solvents: list[str], controls: list[str], yields: list[str]) -> list[str]:
    suspects = ["raw-material assay/quality variance", "incomplete conversion", "filtration or drying loss"]
    if solvents:
        suspects.append("solvent water content, recovery, or crystallization solvent ratio")
    if any("temperature" in c.lower() for c in controls):
        suspects.append("temperature excursion or ramp-rate sensitivity")
    if not yields:
        suspects.append("missing stage-wise yield history")
    return suspects[:6]


def _impurity_hypotheses(solvents: list[str], safety: list[str], controls: list[str]) -> list[str]:
    out = ["carryover from previous stage", "degradation during hold or heating", "raw-material related impurity"]
    if any(s.lower() in {"dmf", "dmsO".lower(), "methanol", "toluene"} for s in solvents):
        out.append("residual solvent or solvent-mediated impurity formation")
    if safety:
        out.append("safety-sensitive reagent handling may affect impurity profile")
    if controls:
        out.append("IPC control limits should be reviewed against impurity trend")
    return out[:6]


def _purge_strategy(solvents: list[str], qc_tests: list[str], controls: list[str]) -> str:
    pieces = []
    if solvents:
        pieces.append("compare crystallization/antisolvent ratios and mother-liquor impurity purge")
    if qc_tests:
        pieces.append("tighten IPC/COA sampling around listed analytical controls")
    if controls:
        pieces.append("trend process controls against impurity and yield outcomes")
    return "; ".join(pieces) or "Define purge strategy after R&D/QC review of stage evidence."


def list_product_chemistry(product_name: str) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    canonical = get_canonical_product_name(product_name)
    product_id = get_product_id(canonical, create=False)
    row = db.one("SELECT * FROM enterprise_product_chemistry WHERE product_id=?", (product_id,)) if product_id else None
    if not row and product_name:
        analyzed = analyze_product_chemistry(product_name)
        if analyzed.get("ok"):
            product_id = get_product_id(analyzed.get("product_name") or canonical, create=False)
            row = db.one("SELECT * FROM enterprise_product_chemistry WHERE product_id=?", (product_id,)) if product_id else None
            canonical = analyzed.get("product_name") or canonical
    summary = _load_json(row.get("summary_json"), {}) if row else {}
    citations = _load_json(row.get("citations_json"), []) if row else []
    return {"ok": True, "product_name": canonical, "summary": summary, "citations": citations, "analyzed_at": row.get("analyzed_at") if row else ""}


def list_route_stages(product_name: str) -> list[dict[str, Any]]:
    product_id = get_product_id(product_name, create=False)
    if not product_id:
        return []
    rows = db.query("SELECT * FROM enterprise_product_route_stages WHERE product_id=? ORDER BY stage_no, id", (product_id,))
    for row in rows:
        for key in ("raw_materials_json", "solvents_json", "catalysts_json", "equipment_json", "conditions_json", "qc_controls_json", "source_refs_json"):
            row[key[:-5] if key.endswith("_json") else key] = _load_json(row.get(key), [] if key != "conditions_json" else {})
    return rows


def list_chemical_changes(product_name: str) -> dict[str, Any]:
    product_id = get_product_id(product_name, create=False)
    if not product_id:
        return {"ok": True, "product_name": get_canonical_product_name(product_name), "changes": [], "impurity_controls": []}
    changes = db.query("SELECT * FROM enterprise_product_chemical_changes WHERE product_id=? ORDER BY stage_no, id", (product_id,))
    controls = db.query("SELECT * FROM enterprise_product_impurity_controls WHERE product_id=? ORDER BY id", (product_id,))
    for row in changes:
        for key in ("yield_loss_suspects_json", "impurity_hypotheses_json", "source_refs_json"):
            row[key[:-5]] = _load_json(row.get(key), [])
    for row in controls:
        row["source_refs"] = _load_json(row.get("source_refs_json"), [])
    return {"ok": True, "product_name": get_canonical_product_name(product_name), "changes": changes, "impurity_controls": controls}


def add_rm_price(data: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    material = _clean(data.get("material_name"), 220)
    if not material:
        return {"ok": False, "message": "material_name is required"}
    normalized = _clean(data.get("normalized_name") or material.lower(), 220).lower()
    price = float(data.get("price_per_kg") or 0)
    status = "verified" if data.get("verification_status") == "verified" else "unverified"
    verified_by = user_id if status == "verified" else None
    verified_at = _now() if status == "verified" else None
    price_id = db.execute(
        """
        INSERT INTO rm_price_book(
            material_name, normalized_name, price_per_kg, currency, supplier, source_type,
            verification_status, verified_by, verified_at, valid_from, valid_to, notes, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            material, normalized, price, data.get("currency") or "INR", data.get("supplier") or "",
            data.get("source_type") or "internal", status, verified_by, verified_at,
            data.get("valid_from") or "", data.get("valid_to") or "", data.get("notes") or "", user_id,
        ),
    )
    db.execute("INSERT INTO rm_price_audit(price_id, action, details_json, user_id) VALUES (?, ?, ?, ?)", (price_id, "create", _json(data), user_id))
    db.audit(user_id, "create", "rm_price_book", price_id, {"material_name": material, "status": status})
    return {"ok": True, "id": price_id, "verified": status == "verified"}


def list_rm_prices(query: str = "", verified_only: bool = False, limit: int = 100) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    sql = "SELECT * FROM rm_price_book WHERE 1=1"
    params: list[Any] = []
    if query:
        sql += " AND (material_name LIKE ? OR normalized_name LIKE ? OR supplier LIKE ?)"
        params.extend([_like(query), _like(query.lower()), _like(query)])
    if verified_only:
        sql += " AND verification_status='verified'"
    sql += " ORDER BY verification_status DESC, updated_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    return {"ok": True, "prices": db.query(sql, params)}


def verify_rm_price(price_id: int, user_id: int | None = None) -> dict[str, Any]:
    row = db.one("SELECT * FROM rm_price_book WHERE id=?", (price_id,))
    if not row:
        return {"ok": False, "message": "Price not found"}
    db.execute("UPDATE rm_price_book SET verification_status='verified', verified_by=?, verified_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (user_id, _now(), price_id))
    db.execute("INSERT INTO rm_price_audit(price_id, action, details_json, user_id) VALUES (?, ?, ?, ?)", (price_id, "verify", _json(dict(row)), user_id))
    db.audit(user_id, "verify", "rm_price_book", price_id, {"material_name": row.get("material_name")})
    return {"ok": True, "id": price_id, "message": "Raw-material price verified for costing."}


async def suggest_rm_price(material_name: str, user_id: int | None = None) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    material = _clean(material_name, 220)
    if not material:
        return {"ok": False, "message": "material_name is required"}
    prompt = (
        f"Estimate current Indian bulk raw-material price for pharmaceutical material: {material}. "
        "Return JSON with price_per_kg_inr, supplier_region, trend, notes. Mark as estimate only."
    )
    result = await ask_ai(prompt, system="You are procurement intelligence. Return cautious estimates only; do not claim verified quotes.", provider="ollama", feature='chemistry')
    parsed = extract_json_maybe(result.text) if result.text else None
    price = 0.0
    notes = result.text[:1200]
    supplier = "AI/web estimate"
    if isinstance(parsed, dict):
        price = float(parsed.get("price_per_kg_inr") or parsed.get("price_per_kg") or 0)
        supplier = parsed.get("supplier_region") or supplier
        notes = parsed.get("notes") or notes
    sid = db.execute(
        """
        INSERT INTO rm_price_quote_suggestions(material_name, suggested_price_per_kg, currency, supplier, source_title, source_type, status, notes, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (material, price, "INR", supplier, "AI-assisted unverified estimate", "ai_estimate", "unverified", notes, user_id),
    )
    return {"ok": True, "suggestion_id": sid, "material_name": material, "suggested_price_per_kg": price, "currency": "INR", "status": "unverified", "notes": notes}


def list_verified_prices(product_name: str = "") -> dict[str, Any]:
    if not product_name:
        return list_rm_prices(verified_only=True)
    facts = _values(_facts(product_name), "raw_material", 50)
    prices: list[dict[str, Any]] = []
    for material in facts:
        hit = db.one(
            """
            SELECT * FROM rm_price_book
            WHERE verification_status='verified' AND (lower(?) LIKE '%' || lower(normalized_name) || '%' OR lower(normalized_name) LIKE '%' || lower(?) || '%')
            ORDER BY updated_at DESC LIMIT 1
            """,
            (material, material),
        )
        if hit:
            prices.append(hit)
    return {"ok": True, "prices": prices}


def suggest_manufacturing_routes(product_name: str, user_id: int | None = None) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    analysis = analyze_product_chemistry(product_name, user_id)
    canonical = analysis.get("product_name") or get_canonical_product_name(product_name)
    product_id = get_product_id(canonical)
    summary = analysis.get("summary", {})
    stages = list_route_stages(canonical)
    verified_prices = list_verified_prices(canonical).get("prices", [])
    price_total = sum(float(p.get("price_per_kg") or 0) for p in verified_prices)
    precedent = min(1.0, len(stages) / 6.0)
    yield_score = 0.55 + min(0.25, len(summary.get("yield_notes") or []) * 0.03)
    impurity_risk = 0.25 + min(0.4, len(summary.get("safety_notes") or []) * 0.03)
    cost_score = 0.25 if not verified_prices else max(0.2, min(0.9, 1.0 - price_total / 100000.0))
    base_route = {
        "product_name": canonical,
        "route_basis": "Enterprise BMR corpus + verified RM price book",
        "stages": stages[:12],
        "raw_materials": summary.get("raw_materials", [])[:25],
        "solvents": summary.get("solvents", [])[:25],
        "equipment": summary.get("equipment", [])[:12],
        "verified_prices_used": len(verified_prices),
    }
    options = [
        ("Corpus precedent route", "best_precedent", precedent, yield_score, impurity_risk, cost_score),
        ("Cost-focused route review", "lowest_verified_cost", max(0.3, precedent - 0.1), max(0.45, yield_score - 0.08), impurity_risk + 0.08, cost_score),
        ("Impurity-control route review", "lowest_impurity_risk", precedent, max(0.45, yield_score - 0.03), max(0.05, impurity_risk - 0.12), max(0.2, cost_score - 0.05)),
        ("Scale-up readiness route", "fastest_scaleup", min(1.0, precedent + 0.1), yield_score, impurity_risk, max(0.2, cost_score - 0.03)),
    ]
    created: list[dict[str, Any]] = []
    for name, strategy, precedent_score, y_score, imp_risk, c_score in options:
        scores = {
            "corpus_precedent": round(precedent_score, 3),
            "expected_yield": round(y_score, 3),
            "impurity_risk": round(min(1.0, imp_risk), 3),
            "verified_cost_score": round(c_score, 3),
            "scale_up_readiness": round(min(1.0, precedent_score + (0.1 if strategy == "fastest_scaleup" else 0)), 3),
            "composite": round((precedent_score + y_score + (1 - min(1.0, imp_risk)) + c_score) / 4, 3),
        }
        cost = {
            "verified_rm_price_count": len(verified_prices),
            "verified_rm_total_inr_per_kg_basis": round(price_total, 2),
            "costing_status": "verified_basis_available" if verified_prices else "missing_verified_rm_prices",
            "missing_data": [] if verified_prices else ["Enter and verify raw-material rates before profitability decision."],
        }
        route_id = db.execute(
            """
            INSERT INTO enterprise_product_route_options(product_id, route_name, status, route_json, cost_json, scores_json, citations_json, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (product_id, name, "proposed", _json({**base_route, "strategy": strategy}), _json(cost), _json(scores), _json(analysis.get("citations", [])[:10]), user_id),
        )
        created.append({"id": route_id, "route_name": name, "strategy": strategy, "cost": cost, "scores": scores})
    db.audit(user_id, "suggest", "enterprise_product_route_options", product_id, {"product_name": canonical, "routes": len(created)})
    return {"ok": True, "product_name": canonical, "routes": created, "verified_prices_used": len(verified_prices)}


def manufacturing_options(product_name: str) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    product_id = get_product_id(product_name, create=False)
    if not product_id:
        return {"ok": True, "product_name": get_canonical_product_name(product_name), "options": [], "missing_data": ["Analyze chemistry first."]}
    rows = db.query("SELECT * FROM enterprise_product_route_options WHERE product_id=? ORDER BY created_at DESC LIMIT 50", (product_id,))
    options = []
    for row in rows:
        row["route"] = _load_json(row.get("route_json"), {})
        row["cost"] = _load_json(row.get("cost_json"), {})
        row["scores"] = _load_json(row.get("scores_json"), {})
        row["citations"] = _load_json(row.get("citations_json"), [])
        options.append(row)
    missing = []
    if not any((o.get("cost") or {}).get("verified_rm_price_count") for o in options):
        missing.append("Verified raw-material prices")
    if not options:
        missing.append("Manufacturing route suggestions")
    return {"ok": True, "product_name": get_canonical_product_name(product_name), "options": options, "missing_data": missing}


def learn_document_style(product_name: str = "", user_id: int | None = None) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    names = STYLE_SOURCE_NAMES
    docs = db.query(
        """
        SELECT d.*, f.extracted_text
        FROM enterprise_bmr_documents d
        LEFT JOIN ingest_files f ON f.id=d.ingest_file_id
        WHERE d.original_name IN ({})
           OR d.original_name LIKE '%BMR%'
           OR d.original_name LIKE '%COA%'
           OR d.original_name LIKE '%SOP%'
        ORDER BY d.document_type, d.original_name
        """.format(",".join("?" for _ in names)),
        tuple(names),
    )
    profiles: dict[str, list[dict[str, Any]]] = {"bmr": [], "coa": [], "sop": []}
    for doc in docs:
        original_name = doc.get("original_name", "").lower()
        if doc.get("document_type") == "coa" or "coa" in original_name:
            kind = "coa"
        elif "sop" in original_name:
            kind = "sop"
        else:
            kind = "bmr"
        if kind == "bmr" and "bmr" not in doc.get("original_name", "").lower() and doc.get("document_type") not in {"bmr", "bpcr"}:
            continue
        profiles[kind].append(doc)
    if not profiles["bmr"] or not profiles["coa"]:
        return {"ok": True, "profiles": []}
    created: list[dict[str, Any]] = []
    for kind, kind_docs in profiles.items():
        if not kind_docs:
            continue
        profile_name = f"JK {kind.upper()} Corpus Style"
        source_ids = [d["id"] for d in kind_docs[:12]]
        blocks = _extract_style_blocks(kind_docs)
        style = {
            "document_kind": kind,
            "company_style": "JK Lifecare corpus-derived",
            "source_names": [d.get("original_name") for d in kind_docs[:12]],
            "required_blocks": [b["label"] for b in blocks[:30]],
            "signature_blocks": [b["label"] for b in blocks if "sign" in b["label"].lower() or "approved" in b["label"].lower() or "checked" in b["label"].lower()],
            "table_like_blocks": [b["label"] for b in blocks if b["block_type"] == "table"],
        }
        existing = db.one("SELECT id FROM enterprise_document_style_profiles WHERE profile_name=?", (profile_name,))
        if existing:
            profile_id = existing["id"]
            db.execute("UPDATE enterprise_document_style_profiles SET source_document_ids_json=?, style_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (_json(source_ids), _json(style), profile_id))
            db.execute("DELETE FROM enterprise_document_style_blocks WHERE profile_id=?", (profile_id,))
        else:
            profile_id = db.execute(
                "INSERT INTO enterprise_document_style_profiles(profile_name, document_kind, source_document_ids_json, style_json, created_by) VALUES (?, ?, ?, ?, ?)",
                (profile_name, kind, _json(source_ids), _json(style), user_id),
            )
        for idx, block in enumerate(blocks[:80], 1):
            db.execute(
                "INSERT INTO enterprise_document_style_blocks(profile_id, block_type, label, content, sequence_no, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
                (profile_id, block["block_type"], block["label"], block["content"], idx, _json(block.get("metadata", {}))),
            )
        created.append({"profile_id": profile_id, "profile_name": profile_name, "document_kind": kind, "blocks": len(blocks), "sources": len(kind_docs)})
    db.audit(user_id, "learn", "enterprise_document_style_profiles", "", {"profiles": created})
    return {"ok": True, "profiles": created}


def _extract_style_blocks(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    seen: set[str] = set()
    heading_pat = re.compile(r"^(?:\d+[\). -]+)?([A-Z][A-Za-z0-9 /&().,%:-]{3,90})$")
    for doc in docs:
        text = doc.get("extracted_text") or doc.get("extracted_summary") or ""
        for line in text.splitlines():
            clean = _clean(line, 200)
            if not clean:
                continue
            is_table = "|" in clean or "\t" in clean
            match = heading_pat.match(clean)
            if is_table:
                label = clean.split("|")[0].strip()[:80] or "Table row"
                block_type = "table"
            elif match and len(clean.split()) <= 10:
                label = match.group(1).strip()
                block_type = "heading"
            elif any(k in clean.lower() for k in ("prepared by", "checked by", "approved by", "analyst", "qa", "signature")):
                label = clean[:90]
                block_type = "signature"
            else:
                continue
            key = (block_type + ":" + label).lower()
            if key in seen:
                continue
            seen.add(key)
            labels.append({"block_type": block_type, "label": label, "content": clean, "metadata": {"source": doc.get("original_name"), "document_id": doc.get("id")}})
    defaults = [
        ("heading", "Product and Batch Details", "Product name, batch number, batch size, document number, version"),
        ("heading", "Raw Material Details", "Material, code, batch, quantity, unit, issued by, checked by"),
        ("heading", "Manufacturing Procedure", "Stage-wise procedure with IPC controls"),
        ("heading", "In-Process Controls", "Test, specification, result, analyst, reviewed by"),
        ("signature", "Prepared / Checked / Approved By", "Prepared by, checked by, approved by, QA authorization"),
    ]
    for block_type, label, content in defaults:
        key = (block_type + ":" + label).lower()
        if key not in seen:
            labels.append({"block_type": block_type, "label": label, "content": content, "metadata": {"source": "default_jk_style_backstop"}})
    return labels


def list_style_profiles() -> list[dict[str, Any]]:
    ensure_product_chemistry_schema()
    rows = db.query("SELECT * FROM enterprise_document_style_profiles ORDER BY document_kind, updated_at DESC")
    for row in rows:
        row["style"] = _load_json(row.get("style_json"), {})
        row["source_document_ids"] = _load_json(row.get("source_document_ids_json"), [])
    return rows


def _lines_from_values(title: str, values: list[str], *, max_items: int = 12) -> list[str]:
    lines = [f"{title}:"]
    if not values:
        return lines + ["- Not found in extracted corpus yet; requires human completion."]
    return lines + [f"- {v}" for v in values[:max_items]]


def _draft_citation_lines(citations: list[dict[str, Any]], limit: int = 12) -> list[str]:
    if not citations:
        return ["- No corpus citation available; do not use operationally until evidence is attached."]
    lines = []
    for c in citations[:limit]:
        ref = c.get("source_ref") or c.get("citation") or ""
        title = c.get("title") or f"Document #{c.get('document_id')}"
        lines.append(f"- {title}" + (f" | {ref}" if ref else ""))
    return lines


def build_product_draft(product_name: str, draft_type: str, user_id: int | None = None) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    from .enterprise_bmr_corpus import ensure_bmr_corpus_schema

    ensure_bmr_corpus_schema()
    draft_type = (draft_type or "experiment").lower().strip()
    if draft_type in {"rd", "r&d", "research", "experiment"}:
        draft_type = "experiment"
    elif draft_type in {"scale", "scale-up", "scaleup"}:
        draft_type = "scaleup"
    elif draft_type not in {"experiment", "scaleup", "bmr", "coa"}:
        draft_type = "experiment"

    analysis = analyze_product_chemistry(product_name, user_id)
    canonical = analysis.get("product_name") or get_canonical_product_name(product_name)
    summary = analysis.get("summary") or {}
    stages = analysis.get("stages") or list_route_stages(canonical)
    changes = (analysis.get("chemical_changes") or {}).get("changes", [])
    citations = analysis.get("citations") or []
    routes: list[dict[str, Any]] = []
    if draft_type in {"experiment", "scaleup"}:
        existing_options = manufacturing_options(canonical).get("options", [])
        if existing_options:
            routes = [
                {
                    "id": row.get("id"),
                    "route_name": row.get("route_name"),
                    "strategy": (row.get("route") or {}).get("strategy"),
                    "cost": row.get("cost") or {},
                    "scores": row.get("scores") or {},
                }
                for row in existing_options[:4]
            ]
        else:
            routes = suggest_manufacturing_routes(canonical, user_id).get("routes", [])

    title_map = {
        "experiment": f"R&D experiment draft - {canonical}",
        "scaleup": f"Scale-up plan draft - {canonical}",
        "bmr": f"JK-style BMR draft - {canonical}",
        "coa": f"JK-style COA draft - {canonical}",
    }
    title = title_map[draft_type]
    lines = [
        title,
        "=" * len(title),
        "",
        "Status: PROPOSED DRAFT - requires R&D/QA/Production human review before execution, GMP use, release, or approval.",
        f"Product: {canonical}",
        f"Generated: {_now()}",
        "",
        "Corpus Evidence Summary:",
        f"- Source citations used: {len(citations)}",
        f"- Route stages detected: {len(stages)}",
        f"- Chemical change hypotheses: {len(changes)}",
        "",
    ]
    lines += _lines_from_values("Raw Materials / Reagents", summary.get("raw_materials") or [])
    lines += [""] + _lines_from_values("Solvents / Process Media", summary.get("solvents") or [])
    lines += [""] + _lines_from_values("Equipment", summary.get("equipment") or [])
    lines += [""] + _lines_from_values("Yield Basis", summary.get("yield_notes") or [])
    lines += [""] + _lines_from_values("QC / IPC Controls", summary.get("qc_tests") or summary.get("controls") or [])
    lines += [""] + _lines_from_values("Safety / Scale-Up Warnings", summary.get("safety_notes") or [])

    if draft_type == "experiment":
        lines += [
            "",
            "Experiment Objective:",
            "- Improve yield and reduce impurities using the corpus process as the control route.",
            "- Treat current BMR quantities and process parameters as the baseline; do not change GMP process without approval.",
            "",
            "Control Batch / Baseline:",
            "- Reconstruct the reference Stage A process from cited BMR/BPCR evidence.",
            "- Capture actual yield, residual starting material, TLC/HPLC conversion, moisture/LOD, and impurity profile.",
            "",
            "DOE / Trial Variables:",
            "- Temperature ramp and exotherm control windows, especially during reagent addition and water quench/workup.",
            "- Stirring/hold time around the high-temperature reaction phase.",
            "- Reagent charge verification and staged addition profile for boric acid and zinc chloride where applicable.",
            "- Water addition temperature and rate, because corpus evidence warns impurity can rise if temperature exceeds the process limit.",
            "- Washing volume and centrifuge/filter hold time to reduce entrained impurities and solvent/process-media retention.",
            "",
            "Trial Matrix:",
            "- Trial 1: exact corpus baseline, full analytical capture.",
            "- Trial 2: tighter exotherm and water-addition temperature control.",
            "- Trial 3: optimized post-reaction hold based on TLC endpoint, avoiding unnecessary heat exposure.",
            "- Trial 4: wash/filtration optimization with yield loss tracking.",
            "",
            "Success Criteria:",
            "- Yield not lower than corpus expected range unless impurity reduction is significant.",
            "- Residual starting material/IPC complies with cited limit or tighter internal target.",
            "- No new major impurity trend versus baseline.",
            "- Process remains executable on available GLR/centrifuge/filter equipment.",
        ]
    elif draft_type == "scaleup":
        lines += [
            "",
            "Scale-Up Path:",
            "- Use the corpus batch as the engineering reference and scale only after one confirmed R&D repeat batch.",
            "- Preserve heat-transfer, addition-rate, agitation, sampling, and filtration controls before increasing batch size.",
            "",
            "Critical Scale-Up Risks:",
            "- Exotherm management during reagent additions and water addition/workup.",
            "- Heat-up and cool-down time changes in GLR scale.",
            "- TLC/HPLC endpoint timing drift at larger volume.",
            "- Wet cake washing, centrifuge/filter loading, and hold-time impurity pickup.",
            "",
            "Pilot Plan:",
            "- Stage 1: engineering batch at reduced scale using exact BMR ratios.",
            "- Stage 2: pilot batch with heat balance and sampling frequency increased around exothermic steps.",
            "- Stage 3: process confirmation batch only after QA/R&D review of impurity and yield data.",
            "",
            "Sampling / Acceptance:",
            "- Sample before heat-up, at reaction hold midpoint, endpoint, post-quench, wet cake, wash filtrate, and dried output.",
            "- Track residual starting material, known/unknown impurities, assay, moisture/LOD, and mass balance.",
        ]
    elif draft_type == "bmr":
        lines += [
            "",
            "BMR Structure:",
            "- Batch approval and release",
            "- Batch history and pre-production checks",
            "- Equipment cleaning/status and line clearance",
            "- Batch formula and input material details",
            "- Stage-wise manufacturing operations",
            "- IPC/QC sample request and analytical result recording",
            "- Yield/reconciliation table",
            "- Deviation record",
            "- Production/QA checked and approved blocks",
            "",
            "Draft Manufacturing Procedure Basis:",
        ]
        for stage in stages[:20]:
            lines.append(f"- Stage {stage.get('stage_no')}: {stage.get('stage_name')} | controls: {', '.join((stage.get('qc_controls') or [])[:3])}")
    else:
        lines += [
            "",
            "COA Draft Basis:",
            "- Product identification and batch metadata",
            "- Description/appearance",
            "- Identification",
            "- Assay/purity",
            "- Related substances / impurity profile",
            "- Moisture/LOD where applicable",
            "- Residual solvents/process media where applicable",
            "- Analyst, checked by, and QA approval blocks",
        ]

    if changes:
        lines += ["", "Chemical Change / Impurity Hypotheses:"]
        for change in changes[:12]:
            lines.append(
                f"- Stage {change.get('stage_no')}: {change.get('likely_reaction_class') or 'process change'}; "
                f"{change.get('change_summary')}"
            )
    if routes:
        lines += ["", "Route Option Scores:"]
        for route in routes[:4]:
            lines.append(f"- {route.get('route_name')}: {route.get('scores')} | {route.get('cost')}")
    missing = summary.get("missing_data") or []
    if missing:
        lines += ["", "Missing Data Before Decision:"]
        lines += [f"- {m}" for m in missing]
    lines += ["", "Source Citations:"]
    lines += _draft_citation_lines(citations)

    slug = re.sub(r"[^A-Za-z0-9]+", "_", canonical).strip("_") or "product"
    path = GENERATED_DIR / f"{draft_type}_draft_{slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    payload = {
        "product_name": canonical,
        "draft_type": draft_type,
        "summary": summary,
        "stages": stages[:20],
        "chemical_changes": changes[:20],
        "route_options": routes[:4],
        "artifact_path": str(path),
        "requires_human_review": True,
    }
    draft_id = db.execute(
        """
        INSERT INTO enterprise_bmr_ai_drafts(draft_type, title, status, product_name, prompt, response_text, payload_json, citations_json, artifact_path, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (draft_type, title, "proposed", canonical, f"Product 360 deterministic {draft_type} draft", "\n".join(lines), _json(payload), _json(citations), str(path), user_id),
    )
    db.audit(user_id, "draft", f"product_{draft_type}", draft_id, {"product_name": canonical, "path": str(path)})
    return {
        "ok": True,
        "draft_id": draft_id,
        "draft_type": draft_type,
        "product_name": canonical,
        "title": title,
        "response": "\n".join(lines),
        "payload": payload,
        "citations": citations,
        "path": str(path),
        "url": "/generated/" + path.name,
        "status": "proposed",
    }


def draft_jk_document(product_name: str, kind: str, user_id: int | None = None) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    kind = "coa" if kind.lower() == "coa" else "bmr"
    canonical = get_canonical_product_name(product_name)
    analysis = analyze_product_chemistry(canonical, user_id)
    profiles = [p for p in list_style_profiles() if p.get("document_kind") == kind]
    if not profiles:
        learn_document_style(canonical, user_id)
        profiles = [p for p in list_style_profiles() if p.get("document_kind") == kind]
    profile = profiles[0] if profiles else {"style": {"required_blocks": []}, "profile_name": f"JK {kind.upper()} fallback"}
    options = manufacturing_options(canonical).get("options", [])
    title = f"JK-style {kind.upper()} draft - {canonical}"
    lines = [
        title,
        "=" * len(title),
        "",
        "Status: DRAFT - requires authorized human review and approval before GMP use.",
        f"Style profile: {profile.get('profile_name')}",
        f"Product: {canonical}",
        "",
        "Required style blocks:",
    ]
    for block in (profile.get("style") or {}).get("required_blocks", [])[:30]:
        lines.append(f"- {block}")
    lines.extend(["", "Chemistry / process basis:"])
    summary = analysis.get("summary", {})
    for key in ("raw_materials", "solvents", "equipment", "controls", "yield_notes", "qc_tests", "safety_notes"):
        values = summary.get(key) or []
        lines.append(f"- {key.replace('_', ' ').title()}: " + ("; ".join(values[:10]) if values else "Missing / requires review"))
    if options:
        best = sorted(options, key=lambda r: (r.get("scores") or {}).get("composite", 0), reverse=True)[0]
        lines.extend(["", "Route decision-support basis:", f"- {best.get('route_name')}: {best.get('scores')}"])
    lines.extend(["", "Citations:"])
    for citation in analysis.get("citations", [])[:12]:
        lines.append(f"- {citation.get('title')} {citation.get('source_ref') or ''}".strip())
    path = GENERATED_DIR / f"jk_{kind}_draft_{re.sub(r'[^A-Za-z0-9]+', '_', canonical).strip('_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    db.audit(user_id, "draft", f"jk_{kind}", canonical, {"path": str(path)})
    return {"ok": True, "product_name": canonical, "kind": kind, "path": str(path), "url": "/generated/" + path.name, "profile": profile.get("profile_name"), "status": "draft"}


async def draft_rd_trial(product_name: str, user_id: int | None = None) -> dict[str, Any]:
    return build_product_draft(product_name, "experiment", user_id)


async def draft_scaleup_plan(product_name: str, user_id: int | None = None) -> dict[str, Any]:
    return build_product_draft(product_name, "scaleup", user_id)


def list_provider_keys() -> dict[str, Any]:
    ensure_product_chemistry_schema()
    rows = db.query("SELECT provider, label, default_model, base_url, enabled, last_test_status, last_test_at, updated_at FROM enterprise_ai_provider_keys ORDER BY provider")
    configured = {r["provider"]: r for r in rows}
    providers = []
    for provider, meta in SUPPORTED_CLOUD_PROVIDERS.items():
        row = configured.get(provider)
        providers.append({
            "provider": provider,
            "label": meta["label"],
            "default_model": row.get("default_model") if row else meta["default_model"],
            "base_url": row.get("base_url") if row else "",
            "enabled": bool(row.get("enabled")) if row else False,
            "configured": bool(row),
            "last_test_status": row.get("last_test_status") if row else "",
            "last_test_at": row.get("last_test_at") if row else "",
            "updated_at": row.get("updated_at") if row else "",
        })
    return {"ok": True, "providers": providers}


def save_provider_key(provider: str, api_key: str, *, default_model: str = "", base_url: str = "", enabled: bool = True, user_id: int | None = None) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    provider = provider.lower().strip()
    if provider not in SUPPORTED_CLOUD_PROVIDERS:
        return {"ok": False, "message": f"Unsupported provider: {provider}"}
    api_key = _clean(api_key, 4000)
    if not api_key:
        return {"ok": False, "message": "api_key is required"}
    meta = SUPPORTED_CLOUD_PROVIDERS[provider]
    db.execute(
        """
        INSERT INTO enterprise_ai_provider_keys(provider, label, encrypted_key, default_model, base_url, enabled, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(provider) DO UPDATE SET
            label=excluded.label,
            encrypted_key=excluded.encrypted_key,
            default_model=excluded.default_model,
            base_url=excluded.base_url,
            enabled=excluded.enabled,
            updated_by=excluded.updated_by,
            updated_at=CURRENT_TIMESTAMP
        """,
        (provider, meta["label"], encrypt_secret(api_key), default_model or meta["default_model"], base_url, 1 if enabled else 0, user_id),
    )
    db.audit(user_id, "update", "enterprise_ai_provider_key", provider, {"provider": provider, "masked": mask_secret(api_key), "enabled": enabled})
    return {"ok": True, "provider": provider, "masked": mask_secret(api_key)}


def delete_provider_key(provider: str, user_id: int | None = None) -> dict[str, Any]:
    ensure_product_chemistry_schema()
    provider = provider.lower().strip()
    db.execute("DELETE FROM enterprise_ai_provider_keys WHERE provider=?", (provider,))
    db.audit(user_id, "delete", "enterprise_ai_provider_key", provider)
    return {"ok": True, "provider": provider}


def get_provider_key(provider: str) -> dict[str, Any] | None:
    ensure_product_chemistry_schema()
    row = db.one("SELECT * FROM enterprise_ai_provider_keys WHERE provider=? AND enabled=1", (provider.lower().strip(),))
    if not row:
        return None
    data = dict(row)
    data["api_key"] = decrypt_secret(data.get("encrypted_key") or "")
    data.pop("encrypted_key", None)
    return data


async def test_provider_key(provider: str, user_id: int | None = None) -> dict[str, Any]:
    provider = provider.lower().strip()
    row = get_provider_key(provider)
    if not row:
        return {"ok": False, "provider": provider, "message": "Provider key is not configured or disabled."}
    sensitivity = classify_sensitivity("general provider connectivity test")
    allowed, reason = can_use_cloud("general provider connectivity test")
    if not allowed:
        return {"ok": False, "provider": provider, "message": reason, "sensitivity": sensitivity}
    if provider == "chemdfm":
        try:
            from .rd_brain import RDBrain

            brain = RDBrain(provider="chemdfm", model=row.get("default_model") or None)
            text = await brain._call_ai("Reply with the word ok for provider configuration test.", system="Configuration test only.")
            ok = bool(text)
            status = "ok" if ok else "failed"
            db.execute("UPDATE enterprise_ai_provider_keys SET last_test_status=?, last_test_at=CURRENT_TIMESTAMP WHERE provider=?", (status, provider))
            db.audit(user_id, "test", "enterprise_ai_provider_key", provider, {"status": status, "route": "rd_brain.chemdfm", "privacy": reason})
            return {"ok": ok, "provider": provider, "status": status, "model": row.get("default_model"), "privacy": reason, "message": text[:300]}
        except Exception as exc:
            db.execute("UPDATE enterprise_ai_provider_keys SET last_test_status=?, last_test_at=CURRENT_TIMESTAMP WHERE provider=?", ("failed", provider))
            return {"ok": False, "provider": provider, "status": "failed", "message": str(exc)[:300]}
    # Store-backed provider adapters are not globally injected into Settings yet; verify by key presence and a low-risk fallback call.
    result = await ask_ai("Reply with the word ok for provider configuration test.", system="Configuration test only.", provider=provider, model=row.get("default_model") or None)
    ok = bool(result.text)
    status = "ok" if ok else "failed"
    db.execute("UPDATE enterprise_ai_provider_keys SET last_test_status=?, last_test_at=CURRENT_TIMESTAMP WHERE provider=?", (status, provider))
    db.audit(user_id, "test", "enterprise_ai_provider_key", provider, {"status": status, "route": result.route, "privacy": reason})
    return {"ok": ok, "provider": provider, "status": status, "model": row.get("default_model"), "privacy": reason, "message": result.text[:300]}


def cloud_prompt_allowed(text: str, privacy_mode: str = "balanced") -> dict[str, Any]:
    allowed, reason = can_use_cloud(text, privacy_mode=privacy_mode)
    return {"allowed": allowed, "reason": reason, "sensitivity": classify_sensitivity(text), "sanitized": sanitize_for_cloud(text) if allowed else ""}
