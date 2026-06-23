from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from .ai import ask_ai
from .database import db
from .enterprise_bmr_corpus import search_corpus
from .product_chemistry import get_canonical_product_name


def ensure_product_intelligence_schema() -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS enterprise_product_lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            lesson_type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            source_refs_json TEXT DEFAULT '[]',
            confidence REAL DEFAULT 0.5,
            status TEXT DEFAULT 'learned',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _safe_query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return db.query(sql, params)
    except Exception:
        return []


def _safe_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    try:
        return db.one(sql, params)
    except Exception:
        return None


def _norm_product(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:180]


def _like(product_name: str) -> str:
    return f"%{product_name.strip()}%"


def _count(table: str, product_name: str, column: str = "product_name") -> int:
    row = _safe_one(f"SELECT COUNT(*) count FROM {table} WHERE {column} LIKE ?", (_like(product_name),))
    return int((row or {}).get("count") or 0)


def list_products(limit: int = 250) -> list[dict[str, Any]]:
    """Return known product names gathered across the scattered Enterprise modules."""
    sources = [
        ("enterprise_products", "canonical_name"),
        ("enterprise_bmr_documents", "product_name"),
        ("bmr_records", "product_name"),
        ("rd_experiments", "product_name"),
        ("experiments", "product_name"),
        ("coa_records", "product_name"),
        ("coa_templates", "product_name"),
        ("production_batches", "product_name"),
        ("qms_records", "product_name"),
        ("rim_submissions", "product_name"),
        ("tech_transfer_packages", "product_name"),
        ("scale_up_trials", "product_name"),
        ("product_research", "product_name"),
        ("lims_samples", "product_name"),
        ("stability_protocols", "product_name"),
    ]
    seen: dict[str, dict[str, Any]] = {}
    for table, column in sources:
        status_filter = "AND COALESCE(status, 'active')='active'" if table == "enterprise_products" else ""
        rows = _safe_query(
            f"""
            SELECT {column} product_name, COUNT(*) count, MAX(COALESCE(created_at, '')) last_seen
            FROM {table}
            WHERE COALESCE({column}, '') != ''
              {status_filter}
            GROUP BY {column}
            ORDER BY count DESC
            LIMIT 500
            """
        )
        for row in rows:
            name = _norm_product(row.get("product_name"))
            if not name:
                continue
            key = name.lower()
            item = seen.setdefault(key, {"product_name": name, "mentions": 0, "sources": set(), "last_seen": ""})
            item["mentions"] += int(row.get("count") or 0)
            item["sources"].add(table)
            item["last_seen"] = max(item.get("last_seen") or "", row.get("last_seen") or "")

    products = []
    for item in seen.values():
        name = item["product_name"]
        products.append({
            "product_name": name,
            "mentions": item["mentions"],
            "sources": sorted(item["sources"]),
            "last_seen": item["last_seen"],
            "bmr_docs": _count("enterprise_bmr_documents", name),
            "bmr_records": _count("bmr_records", name),
            "rd_experiments": _count("rd_experiments", name) + _count("experiments", name),
            "scaleup_trials": _count("scale_up_trials", name),
            "coa_records": _count("coa_records", name),
            "production_batches": _count("production_batches", name),
            "qms_records": _count("qms_records", name),
        })
    products.sort(key=lambda p: (p["mentions"], p["bmr_docs"], p["last_seen"]), reverse=True)
    return products[: max(1, min(int(limit), 500))]


def product_dashboard(product_name: str) -> dict[str, Any]:
    ensure_product_intelligence_schema()
    product = _norm_product(get_canonical_product_name(product_name)) if product_name else ""
    if not product:
        products = list_products(1)
        product = products[0]["product_name"] if products else ""
    corpus = search_corpus(product, limit=12) if product else {"hits": [], "facts": []}
    tables = {
        "rd_experiments": _safe_query("SELECT * FROM rd_experiments WHERE product_name LIKE ? ORDER BY updated_at DESC LIMIT 25", (_like(product),)),
        "legacy_experiments": _safe_query("SELECT * FROM experiments WHERE product_name LIKE ? ORDER BY updated_at DESC LIMIT 20", (_like(product),)),
        "scaleup_trials": _safe_query("SELECT * FROM scale_up_trials WHERE product_name LIKE ? ORDER BY created_at DESC LIMIT 20", (_like(product),)),
        "tech_transfer": _safe_query("SELECT * FROM tech_transfer_packages WHERE product_name LIKE ? ORDER BY created_at DESC LIMIT 20", (_like(product),)),
        "bmr_records": _safe_query("SELECT * FROM bmr_records WHERE product_name LIKE ? ORDER BY created_at DESC LIMIT 25", (_like(product),)),
        "coa_records": _safe_query("SELECT * FROM coa_records WHERE product_name LIKE ? ORDER BY updated_at DESC LIMIT 25", (_like(product),)),
        "coa_templates": _safe_query("SELECT * FROM coa_templates WHERE product_name LIKE ? ORDER BY created_at DESC LIMIT 15", (_like(product),)),
        "production_batches": _safe_query("SELECT * FROM production_batches WHERE product_name LIKE ? ORDER BY updated_at DESC LIMIT 25", (_like(product),)),
        "qms_records": _safe_query("SELECT * FROM qms_records WHERE product_name LIKE ? ORDER BY updated_at DESC LIMIT 25", (_like(product),)),
        "rim_submissions": _safe_query("SELECT * FROM rim_submissions WHERE product_name LIKE ? ORDER BY updated_at DESC LIMIT 20", (_like(product),)),
        "lims_samples": _safe_query("SELECT * FROM lims_samples WHERE product_name LIKE ? ORDER BY created_at DESC LIMIT 20", (_like(product),)),
        "stability": _safe_query("SELECT * FROM stability_protocols WHERE product_name LIKE ? ORDER BY created_at DESC LIMIT 20", (_like(product),)),
    }
    counts = {
        "corpus_docs": len(corpus.get("hits") or []),
        "rd": len(tables["rd_experiments"]) + len(tables["legacy_experiments"]),
        "scaleup": len(tables["scaleup_trials"]),
        "bmr": len(tables["bmr_records"]),
        "coa": len(tables["coa_records"]),
        "production": len(tables["production_batches"]),
        "quality": len(tables["qms_records"]) + len(tables["lims_samples"]) + len(tables["stability"]),
        "regulatory": len(tables["rim_submissions"]),
    }
    facts_by_type: dict[str, list[dict[str, Any]]] = {}
    for fact in corpus.get("facts") or []:
        facts_by_type.setdefault(fact.get("fact_type") or "fact", []).append(fact)
    return {
        "product_name": product,
        "products": list_products(),
        "counts": counts,
        "corpus": corpus,
        "tables": tables,
        "facts_by_type": facts_by_type,
        "lessons": list_product_lessons(product),
        "next_actions": product_next_actions(product, counts, tables, corpus),
        "module_links": product_module_links(product),
    }


def product_module_links(product_name: str) -> list[dict[str, str]]:
    q = product_name.replace(" ", "+")
    return [
        {"label": "BMR Knowledge", "url": f"/bmr-knowledge?q={q}"},
        {"label": "R&D Process", "url": f"/rd/process?product_name={q}"},
        {"label": "Tech Transfer", "url": f"/rd/tech-transfer?product_name={q}"},
        {"label": "Production Planning", "url": f"/production/planning?product_name={q}"},
        {"label": "BMR Records", "url": f"/production/bmr?product_name={q}"},
        {"label": "QC / COA", "url": f"/qc/lab?product_name={q}"},
        {"label": "QA / SOP", "url": f"/qa?product_name={q}"},
        {"label": "Documents", "url": f"/documents?product_name={q}"},
    ]


def product_next_actions(product_name: str, counts: dict[str, int], tables: dict[str, list[dict[str, Any]]], corpus: dict[str, Any]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if counts.get("corpus_docs", 0) > 0 and counts.get("rd", 0) == 0:
        actions.append({"priority": "high", "area": "R&D", "action": "Create a corpus-derived R&D experiment template before starting new trials."})
    if counts.get("rd", 0) > 0 and counts.get("scaleup", 0) == 0:
        actions.append({"priority": "medium", "area": "Scale-up", "action": "Compare recorded R&D runs and draft a pilot-scale trial with equipment fit and sampling criteria."})
    if counts.get("bmr", 0) == 0 and counts.get("corpus_docs", 0) > 0:
        actions.append({"priority": "medium", "area": "Production", "action": "Draft a controlled BMR structure from approved corpus evidence and route it for review."})
    if counts.get("coa", 0) == 0:
        actions.append({"priority": "medium", "area": "QC", "action": "Create or import a COA template so analytical controls follow product history."})
    if tables.get("qms_records"):
        actions.append({"priority": "high", "area": "QA", "action": "Review open quality records for impurity, yield, or documentation trends before batch release decisions."})
    if any((h.get("extraction_status") or "") in {"needs_conversion", "needs_ocr"} for h in corpus.get("hits", [])):
        actions.append({"priority": "medium", "area": "Knowledge", "action": "Convert legacy DOC/OCR-needed evidence so the product brain has complete process history."})
    if not actions:
        actions.append({"priority": "low", "area": "Control", "action": "Use the product brain to summarize development, production, QC, and regulatory readiness."})
    return actions[:8]


async def product_brain(product_name: str, question: str, user_id: int | None = None) -> dict[str, Any]:
    dashboard = product_dashboard(product_name)
    lower_question = question.lower()
    from .product_chemistry import (
        analyze_product_chemistry,
        draft_jk_document,
        draft_rd_trial,
        draft_scaleup_plan,
        suggest_manufacturing_routes,
    )

    if any(k in lower_question for k in ("experiment", "r&d", "rd trial", "trial plan", "e&d")):
        draft = await draft_rd_trial(dashboard["product_name"], user_id)
        return {
            "ok": True,
            "product_name": draft.get("product_name") or dashboard["product_name"],
            "answer": f"{draft.get('title')}\n\nArtifact: {draft.get('url')}\nDraft ID: {draft.get('draft_id')}\n\n{draft.get('response', '')}",
            "draft": draft,
            "citations": draft.get("citations", []),
        }
    if any(k in lower_question for k in ("scale up", "scale-up", "scaleup", "pilot", "commercial scale")):
        draft = await draft_scaleup_plan(dashboard["product_name"], user_id)
        return {
            "ok": True,
            "product_name": draft.get("product_name") or dashboard["product_name"],
            "answer": f"{draft.get('title')}\n\nArtifact: {draft.get('url')}\nDraft ID: {draft.get('draft_id')}\n\n{draft.get('response', '')}",
            "draft": draft,
            "citations": draft.get("citations", []),
        }
    if any(k in lower_question for k in ("bmr", "batch manufacturing", "bpcr")):
        draft = draft_jk_document(dashboard["product_name"], "bmr", user_id)
        response = ""
        try:
            response = open(draft.get("path", ""), "r", encoding="utf-8").read()
        except Exception:
            response = json.dumps(draft, default=str, indent=2)
        return {
            "ok": True,
            "product_name": draft.get("product_name") or dashboard["product_name"],
            "answer": f"{draft.get('kind', 'bmr').upper()} draft ready\n\nArtifact: {draft.get('url')}\n\n{response}",
            "draft": draft,
            "citations": [],
        }
    if any(k in lower_question for k in ("chemistry", "chemical", "reaction", "impurity", "solvent", "route")):
        analysis = analyze_product_chemistry(dashboard["product_name"], user_id)
        routes = suggest_manufacturing_routes(dashboard["product_name"], user_id) if "route" in lower_question else {"routes": []}
        answer = _deterministic_product_answer(dashboard["product_name"], question, analysis, routes.get("routes") or [])
        return {
            "ok": True,
            "product_name": analysis.get("product_name") or dashboard["product_name"],
            "answer": answer,
            "analysis": analysis,
            "routes": routes.get("routes") or [],
            "citations": analysis.get("citations", []),
        }

    hits = dashboard["corpus"].get("hits") or []
    evidence = "\n\n".join(f"[{i+1}] {h.get('citation')}\n{h.get('snippet')}" for i, h in enumerate(hits[:8]))
    compact = {
        "product": dashboard["product_name"],
        "counts": dashboard["counts"],
        "next_actions": dashboard["next_actions"],
        "recent_bmrs": dashboard["tables"]["bmr_records"][:5],
        "recent_rd": dashboard["tables"]["rd_experiments"][:5],
        "scaleup": dashboard["tables"]["scaleup_trials"][:5],
        "coa": dashboard["tables"]["coa_records"][:5],
        "production": dashboard["tables"]["production_batches"][:5],
        "quality": dashboard["tables"]["qms_records"][:5],
        "learned_lessons": dashboard["lessons"][:12],
    }
    prompt = (
        f"Product: {dashboard['product_name']}\n"
        f"Question/request: {question}\n\n"
        f"Enterprise product context:\n{json.dumps(compact, default=str)[:9000]}\n\n"
        f"BMR/COA/manufacturing corpus evidence:\n{evidence[:14000]}\n\n"
        "Give a practical product-development answer. Cover R&D, impurity/yield/cost thinking, "
        "production readiness, QC/COA, QA/SOP needs, and cite corpus evidence numbers when used. "
        "Do not claim human approval or GMP release."
    )
    fallback_answer = _dashboard_product_answer(dashboard, question)
    try:
        result = await asyncio.wait_for(
            ask_ai(prompt, system="You are the SHIMS Product Brain for an API/pharma manufacturing enterprise. Be specific, source-aware, and approval-gated.", provider="ollama", model="qwen2.5:14b"),
            timeout=25,
        )
        answer = result.text if result.text and "unavailable" not in result.text.lower() else fallback_answer
        provider = result.provider
        model = result.model
    except Exception as exc:
        answer = fallback_answer + f"\n\nAI enhancement skipped: {exc}"
        provider = "local-deterministic"
        model = "corpus-rules"
    db.execute(
        "INSERT INTO ai_insights(department, insight_type, title, body, priority) VALUES (?, ?, ?, ?, ?)",
        ("products", "product_brain", f"{dashboard['product_name']}: {question[:80]}", answer, "normal"),
    )
    return {
        "ok": True,
        "product_name": dashboard["product_name"],
        "answer": answer,
        "provider": provider,
        "model": model,
        "citations": [{"title": h.get("title"), "document_id": h.get("document_id"), "citation": h.get("citation")} for h in hits[:8]],
    }


def _deterministic_product_answer(product_name: str, question: str, analysis: dict[str, Any], routes: list[dict[str, Any]]) -> str:
    summary = analysis.get("summary") or {}
    lines = [
        f"Product chemistry readout for {analysis.get('product_name') or product_name}",
        "",
        "What the corpus says:",
    ]
    for label, key in [
        ("Raw materials/reagents", "raw_materials"),
        ("Solvents/process media", "solvents"),
        ("Equipment", "equipment"),
        ("Yield notes", "yield_notes"),
        ("QC/IPC controls", "qc_tests"),
        ("Safety/process warnings", "safety_notes"),
    ]:
        values = summary.get(key) or []
        lines.append(f"- {label}: " + ("; ".join(values[:8]) if values else "not extracted yet"))
    changes = (analysis.get("chemical_changes") or {}).get("changes", [])
    if changes:
        lines += ["", "Chemical/change hypotheses:"]
        for change in changes[:8]:
            lines.append(f"- Stage {change.get('stage_no')}: {change.get('likely_reaction_class')} | {change.get('change_summary')}")
    if routes:
        lines += ["", "Manufacturing route options:"]
        for route in routes[:4]:
            lines.append(f"- {route.get('route_name')}: {route.get('scores')} | costing: {route.get('cost')}")
    citations = analysis.get("citations") or []
    if citations:
        lines += ["", "Citations:"]
        for c in citations[:8]:
            lines.append(f"- {c.get('title')} {c.get('source_ref') or ''}".strip())
    lines += ["", "Status: decision-support only. Human R&D/QA approval is required before execution."]
    return "\n".join(lines)


def _dashboard_product_answer(dashboard: dict[str, Any], question: str) -> str:
    hits = dashboard.get("corpus", {}).get("hits") or []
    counts = dashboard.get("counts") or {}
    lines = [
        f"Product Brain readout for {dashboard.get('product_name')}",
        "",
        f"Request: {question}",
        "",
        "Enterprise state:",
        f"- Corpus docs visible: {counts.get('corpus_docs', 0)}",
        f"- R&D records: {counts.get('rd', 0)}",
        f"- Scale-up records: {counts.get('scaleup', 0)}",
        f"- BMR records: {counts.get('bmr', 0)}",
        f"- Quality items: {counts.get('quality', 0)}",
        "",
        "Best next actions:",
    ]
    for action in dashboard.get("next_actions", [])[:6]:
        lines.append(f"- [{action.get('priority')}] {action.get('area')}: {action.get('action')}")
    if hits:
        lines += ["", "Corpus evidence:"]
        for idx, hit in enumerate(hits[:8], 1):
            lines.append(f"- [{idx}] {hit.get('title')} ({hit.get('document_type')}, {hit.get('extraction_status')})")
            if hit.get("snippet"):
                lines.append(f"  {hit.get('snippet')[:350]}")
    else:
        lines += ["", "No corpus evidence matched this product name. Try Normalize Corpus or select the canonical product from Product 360."]
    return "\n".join(lines)


def create_rd_template_from_product(product_name: str, user_id: int | None = None) -> dict[str, Any]:
    """Create a reusable R&D experiment template from the product's corpus process maps."""
    product = _norm_product(product_name)
    maps = _safe_query(
        """
        SELECT * FROM enterprise_bmr_process_maps
        WHERE product_name LIKE ?
        ORDER BY created_at DESC LIMIT 10
        """,
        (_like(product),),
    )
    stages: list[dict[str, Any]] = []
    controls: list[Any] = []
    for row in maps:
        try:
            stages.extend(json.loads(row.get("stages_json") or "[]"))
        except Exception:
            pass
        try:
            controls.extend(json.loads(row.get("controls_json") or "[]"))
        except Exception:
            pass
    if not stages:
        hits = search_corpus(product, limit=5).get("hits") or []
        stages = [{"stage_no": i + 1, "stage_name": h.get("title", f"Corpus stage {i+1}"), "notes": h.get("snippet", "")[:600]} for i, h in enumerate(hits[:5])]
    if not stages:
        return {"ok": False, "message": "No corpus evidence found for this product."}

    template_name = f"{product} corpus R&D template {datetime.now().strftime('%Y%m%d%H%M')}"
    template_id = db.execute(
        """
        INSERT INTO rd_experiment_templates(
            template_name, product_name, route_name, description, stages_json,
            raw_materials_json, solvents_json, tests_json, target_conditions_json,
            ai_generated, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            template_name,
            product,
            "Corpus-derived route",
            "Generated from Enterprise-owned BMR/COA corpus evidence. Review before execution.",
            json.dumps(stages[:20], default=str),
            "[]",
            "[]",
            json.dumps(controls[:20], default=str),
            json.dumps({"source": "enterprise_bmr_corpus", "requires_human_review": True}),
            1,
            user_id,
        ),
    )
    db.audit(user_id, "create", "rd_experiment_template_from_product", template_id, {"product_name": product, "stages": len(stages)})
    return {"ok": True, "template_id": template_id, "template_name": template_name, "stages": stages[:20], "controls": controls[:20]}


def list_product_lessons(product_name: str, limit: int = 50) -> list[dict[str, Any]]:
    ensure_product_intelligence_schema()
    product = _norm_product(product_name)
    rows = _safe_query(
        """
        SELECT * FROM enterprise_product_lessons
        WHERE product_name LIKE ?
        ORDER BY confidence DESC, updated_at DESC LIMIT ?
        """,
        (_like(product), max(1, min(int(limit), 200))),
    )
    for row in rows:
        try:
            row["source_refs"] = json.loads(row.get("source_refs_json") or "[]")
        except Exception:
            row["source_refs"] = []
    return rows


def learn_product_from_corpus(product_name: str, user_id: int | None = None) -> dict[str, Any]:
    """Persist reusable product/process lessons extracted from corpus facts."""
    ensure_product_intelligence_schema()
    product = _norm_product(product_name)
    if not product:
        return {"ok": False, "message": "product_name is required"}
    facts = _safe_query(
        """
        SELECT f.*, d.original_name, d.id document_id
        FROM enterprise_bmr_facts f
        JOIN enterprise_bmr_documents d ON d.id=f.document_id
        WHERE d.product_name LIKE ? OR d.original_name LIKE ?
        ORDER BY f.confidence DESC, f.created_at DESC LIMIT 500
        """,
        (_like(product), _like(product)),
    )
    buckets: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        buckets.setdefault(fact.get("fact_type") or "fact", []).append(fact)
    created = 0
    for lesson_type, items in buckets.items():
        values = []
        refs = []
        for item in items[:30]:
            value = _norm_product(item.get("value"))
            if value and value.lower() not in {v.lower() for v in values}:
                values.append(value)
            refs.append({"document_id": item.get("document_id"), "title": item.get("original_name"), "source_ref": item.get("source_ref")})
        if not values:
            continue
        title = {
            "solvent": "Solvent and media pattern",
            "yield": "Yield and recovery pattern",
            "qc_test": "QC and analytical control pattern",
            "control": "Manufacturing control pattern",
            "safety": "Safety and handling pattern",
            "equipment": "Equipment/process-fit pattern",
            "raw_material": "Raw-material norm",
        }.get(lesson_type, f"{lesson_type.replace('_', ' ').title()} pattern")
        existing = _safe_one(
            "SELECT id FROM enterprise_product_lessons WHERE product_name=? AND lesson_type=? AND title=?",
            (product, lesson_type, title),
        )
        body = "; ".join(values[:12])
        if existing:
            db.execute(
                "UPDATE enterprise_product_lessons SET body=?, source_refs_json=?, confidence=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (body, json.dumps(refs[:20], default=str), min(0.95, 0.45 + len(values) * 0.04), existing["id"]),
            )
        else:
            db.execute(
                "INSERT INTO enterprise_product_lessons(product_name, lesson_type, title, body, source_refs_json, confidence) VALUES (?, ?, ?, ?, ?, ?)",
                (product, lesson_type, title, body, json.dumps(refs[:20], default=str), min(0.95, 0.45 + len(values) * 0.04)),
            )
            created += 1
    db.audit(user_id, "learn", "enterprise_product_lessons", product, {"facts": len(facts), "created": created})
    return {"ok": True, "product_name": product, "facts_used": len(facts), "created": created, "lessons": list_product_lessons(product)}
