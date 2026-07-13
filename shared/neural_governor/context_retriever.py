"""Unified context retriever — fetches memory from all SHIMS sources."""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from . import HardwareProfile


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


def retrieve_unified_context(
    query: str,
    user_id: int,
    session_id: str,
    limit: int = 12,
    include_personal: bool = True,
    include_enterprise: bool = True,
    include_omni: bool = True,
    include_rag: bool = True,
    include_research: bool = True,
) -> dict[str, Any]:
    """Retrieve context from all available SHIMS memory sources.

    Returns structured context with provenance for each source.
    """
    sources_used: list[str] = []
    context_parts: list[str] = []
    metadata: dict[str, Any] = {}

    # 1. Personal Layer
    if include_personal:
        try:
            from .personal_layer import get_profile, format_profile_context
            profile = get_profile(user_id)
            if profile:
                ctx = format_profile_context(profile)
                if ctx:
                    context_parts.append(f"[PERSONAL PROFILE]\n{ctx}")
                    sources_used.append("personal_layer")
                    metadata["personal"] = profile.to_dict()
        except Exception:
            pass

    # 2. Omni Brain (memories, episodes)
    if include_omni:
        try:
            from shared.omni_brain import retrieve_context as omni_retrieve, list_memories
            omni_ctx = omni_retrieve(query, limit=limit)
            if omni_ctx.get("context_text"):
                context_parts.append(omni_ctx["context_text"])
                sources_used.append("omni_brain")
                metadata["omni_hits"] = {
                    "memory": omni_ctx.get("memory_hits", 0),
                    "rag": omni_ctx.get("rag_hits", 0),
                    "research": omni_ctx.get("research_hits", 0),
                }
        except Exception:
            pass

    # 3. Enterprise ERP Context
    if include_enterprise:
        try:
            from shared.database import db
            erp_parts = []
            # Active BMRs
            rows = db.query(
                "SELECT id, product_name, batch_no, status FROM bmr_records WHERE status IN ('draft','in_progress') ORDER BY updated_at DESC LIMIT 3",
                (), fetchall=True
            )
            if rows:
                erp_parts.append("Active BMRs: " + ", ".join(f"{r['product_name']} ({r['status']})" for r in rows))
            # Recent QC
            rows = db.query(
                "SELECT id, sample_id, test_name, status FROM qc_sample_requests WHERE status='pending' ORDER BY created_at DESC LIMIT 3",
                (), fetchall=True
            )
            if rows:
                erp_parts.append("Pending QC: " + ", ".join(f"{r['sample_id']} — {r['test_name']}" for r in rows))
            # Equipment alerts
            rows = db.query(
                "SELECT equipment_id, equipment_name, status FROM equipment_status_history WHERE status IN ('maintenance','down') ORDER BY updated_at DESC LIMIT 3",
                (), fetchall=True
            )
            if rows:
                erp_parts.append("Equipment alerts: " + ", ".join(f"{r['equipment_name']} ({r['status']})" for r in rows))
            if erp_parts:
                context_parts.append("[ENTERPRISE CONTEXT]\n" + "\n".join(erp_parts))
                sources_used.append("enterprise_erp")
        except Exception:
            pass

    # 4. RAG Vector Search (semantic)
    if include_rag:
        try:
            from .vector_memory import search_vectors
            vec_results = search_vectors(query, limit=limit // 2)
            if vec_results:
                lines = ["[SEMANTIC RAG RESULTS]"]
                for r in vec_results:
                    lines.append(f"- {r.get('source_type', 'doc')}: {r.get('text_content', '')[:300]}")
                context_parts.append("\n".join(lines))
                sources_used.append("rag_vector")
                metadata["rag_hits"] = len(vec_results)
        except Exception:
            pass

    # 5. Research cache
    if include_research:
        try:
            from shared.omni_brain import store_research_results  # just to check module
            # We already got research from omni_brain retrieve_context
            pass
        except Exception:
            pass

    full_context = "\n\n".join(context_parts)
    return {
        "query": query,
        "context": full_context,
        "sources_used": sources_used,
        "metadata": metadata,
        "timestamp": _now(),
    }
