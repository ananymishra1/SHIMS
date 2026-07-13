"""AI-driven process map synthesis from BMR corpus documents.

Aggregates all extracted text and facts for a product, then prompts an LLM to
produce a structured API manufacturing process map with unit operation stages,
materials, equipment, IPCs, and critical parameters.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from .ai import ask_ai, extract_json_maybe
from .config import settings
from .database import db


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _clean(text: str | None) -> str:
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def gather_product_documents(product_name: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return all extracted corpus documents matching the product."""
    rows = db.query(
        """
        SELECT d.id, d.original_name, d.document_type, d.product_name, d.extracted_summary,
               f.extracted_text
        FROM enterprise_bmr_documents d
        LEFT JOIN ingest_files f ON f.id = d.ingest_file_id
        WHERE lower(d.product_name) = lower(?)
           OR lower(d.original_name) LIKE ?
        ORDER BY d.extracted_chars DESC
        LIMIT ?
        """,
        (product_name, f'%{product_name.lower()}%', limit),
    )
    docs = []
    for r in rows:
        text = str(r.get('extracted_text') or r.get('extracted_summary') or '')
        if len(text) < 30:
            continue
        docs.append({
            'id': r['id'],
            'name': r.get('original_name') or '',
            'type': r.get('document_type') or 'other',
            'text': text,
        })
    return docs


def gather_product_facts(product_name: str) -> list[dict[str, Any]]:
    rows = db.query(
        """
        SELECT f.*, d.original_name FROM enterprise_bmr_facts f
        JOIN enterprise_bmr_documents d ON d.id = f.document_id
        WHERE lower(d.product_name) = lower(?)
        ORDER BY f.confidence DESC
        LIMIT 200
        """,
        (product_name,),
    )
    return [dict(r) for r in rows]


STAGE_NAMES = [
    'reaction', 'quench', 'extraction', 'wash', 'crystallization',
    'centrifugation', 'filtration', 'drying', 'sizing', 'milling',
    'sifting', 'blending', 'packing', 'packaging', 'labeling',
]


SYSTEM_PROMPT = """You are a pharmaceutical process engineering AI. Given source documents and extracted facts about an API manufacturing process, synthesize a structured process map.

Return ONLY a JSON object with this exact structure:
{
  "product_name": "...",
  "route_name": "...",
  "batch_size_kg": 100,
  "expected_yield_percent": 75,
  "stages": [
    {
      "stage_no": 1,
      "stage_name": "reaction",
      "stage_label": "Stage I: Reaction",
      "description": " brief description ",
      "input_materials": ["material 1", "..."],
      "output_material": "...",
      "equipment": ["reactor"],
      "critical_parameters": [{"param": "temperature", "value": "50-55 C", "note": ""}],
      "ipc_checks": ["pH", "..."],
      "safety_notes": ["..."],
      "duration_hours": 4
    }
  ],
  "controls": [
    {"check": "Assay", "stage": "final", "limit": "NLT 98.0%"}
  ],
  "source_refs": ["doc name 1", "doc name 2"]
}

Rules:
- stage_name MUST be one of: reaction, quench, extraction, wash, crystallization, centrifugation, filtration, drying, sizing, milling, sifting, blending, packing, packaging, labeling.
- Use only information present in the source text. Do not invent solvents or reagents not mentioned.
- If the text is vague, include the stages that are clearly mentioned and mark uncertain items with "(inferred)".
- batch_size_kg and expected_yield_percent are optional; use null if not stated.
"""


def synthesize_process_map(product_name: str) -> dict[str, Any]:
    docs = gather_product_documents(product_name)
    facts = gather_product_facts(product_name)
    if not docs and not facts:
        return {'ok': False, 'error': 'No corpus documents or facts found'}

    context_parts = [f'PRODUCT: {product_name}\n']
    context_parts.append('--- SOURCE DOCUMENTS ---')
    for d in docs:
        snippet = d['text'][:2500]
        context_parts.append(f"DOC: {d['name']} ({d['type']})\n{snippet}\n")

    if facts:
        context_parts.append('--- EXTRACTED FACTS ---')
        for f in facts[:60]:
            context_parts.append(f"[{f.get('fact_type')}] {f.get('label')}: {f.get('value')}")

    context = '\n'.join(context_parts)
    prompt = f"Synthesize a structured API process map from the following documents and facts.\n\n{context[:20000]}\n\nNow produce the JSON process map."

    result = asyncio.run(ask_ai(prompt, system=SYSTEM_PROMPT, provider=settings.ai_provider, model=settings.ollama_model))
    raw = result.text
    parsed = extract_json_maybe(raw)
    if not isinstance(parsed, dict):
        # Try extracting JSON block
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            parsed = extract_json_maybe(m.group(0))
    if not isinstance(parsed, dict):
        return {'ok': False, 'error': 'Could not parse AI response as JSON', 'raw': raw[:500]}

    # Normalize stages
    stages = parsed.get('stages') or []
    for idx, st in enumerate(stages, 1):
        st['stage_no'] = st.get('stage_no') or idx
        name = str(st.get('stage_name') or '').lower().strip()
        # Match canonical names
        matched = None
        for canon in STAGE_NAMES:
            if canon in name or name in canon:
                matched = canon
                break
        st['stage_name'] = matched or name or 'operation'

    # Persist (document_id=-1 marks AI-synthesized maps)
    db.execute(
        "DELETE FROM enterprise_bmr_process_maps WHERE document_id=-1 AND lower(product_name)=lower(?)",
        (product_name,),
    )
    db.execute(
        """
        INSERT INTO enterprise_bmr_process_maps(document_id, product_name, route_name, stages_json, controls_json, source_refs_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            -1,
            product_name,
            parsed.get('route_name') or product_name,
            json.dumps(stages),
            json.dumps(parsed.get('controls') or []),
            json.dumps(parsed.get('source_refs') or []),
        ),
    )
    return {'ok': True, 'product_name': product_name, 'stages': len(stages), 'route_name': parsed.get('route_name')}


def list_ai_process_maps() -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM enterprise_bmr_process_maps WHERE document_id=-1 ORDER BY product_name")
    out = []
    for r in rows:
        out.append({
            'id': r['id'],
            'product_name': r['product_name'],
            'route_name': r['route_name'],
            'stages': _load_json(r.get('stages_json'), []),
            'controls': _load_json(r.get('controls_json'), []),
        })
    return out


def get_ai_process_map(product_name: str) -> dict[str, Any] | None:
    row = db.one('SELECT * FROM enterprise_bmr_process_maps WHERE document_id=-1 AND lower(product_name)=lower(?) ORDER BY id DESC LIMIT 1', (product_name,))
    if not row:
        return None
    return {
        'id': row['id'],
        'product_name': row['product_name'],
        'route_name': row['route_name'],
        'stages': _load_json(row.get('stages_json'), []),
        'controls': _load_json(row.get('controls_json'), []),
    }
