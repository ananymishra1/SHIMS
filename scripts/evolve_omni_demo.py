"""Automation demo for the evolved Omni Brain.

Run with:
    .venv/Scripts/python scripts/evolve_omni_demo.py
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

# Ensure project root is on path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.omni_brain import reindex_vectors, retrieve_context
from shared.config import STORAGE_DIR
from shared.prompt_evolution import (
    ensure_control_variant,
    generate_mutations,
    promote_variant,
    run_eval_suite,
    default_eval_cases,
)
from shared.desktop_planner import plan_from_goal, get_plan
from shared.media_memory import ingest_media


def _vector_count() -> int:
    import sqlite3
    db = STORAGE_DIR / "state" / "governor_vectors.sqlite3"
    if not db.exists():
        return 0
    con = sqlite3.connect(str(db))
    cur = con.execute("SELECT COUNT(*) FROM vectors")
    return cur.fetchone()[0]


def task1_reindex_and_query():
    print("\n=== TASK 1: Reindex vectors + semantic query ===")
    existing = _vector_count()
    if existing > 0:
        print(f"Vectors already indexed ({existing} rows); skipping reindex.")
    else:
        result = reindex_vectors(batch_size=200)
        print(f"Reindex result: {result}")
    ctx = retrieve_context("What do I know about fluconazole API pricing?", limit=8)
    hits = ctx.get("hits", [])
    print(f"Semantic query returned {len(hits)} hits (memory={ctx.get('memory_hits')}, rag={ctx.get('rag_hits')}, vector={ctx.get('vector_hits')})")
    print("Top hits:")
    for h in hits[:5]:
        print(f"  - [{h.get('kind')}] {h.get('title', '')[:80]}: {str(h.get('content', ''))[:160]}...")
    return ctx


def task2_prompt_evolution():
    print("\n=== TASK 2: Prompt evolution cycle ===")
    control = ensure_control_variant("You are a helpful AI assistant.", name="control")
    print(f"Control variant: {control.id} ({control.name})")
    children = generate_mutations(control, n=3)
    print(f"Generated {len(children)} mutations")
    for c in children:
        print(f"  - {c.id}: {c.name}")
    if children:
        run = run_eval_suite(children[0].id, default_eval_cases())
        print(f"Eval run {run.id}: score={run.summary.get('score')}, pass_rate={run.summary.get('pass_rate')}")
        promoted = promote_variant(children[0].id)
        print(f"Promoted: {promoted.id if promoted else 'None'}")
    return children


def task3_llm_plan():
    print("\n=== TASK 3: LLM plan generation ===")
    goal = (
        "Research the latest fluconazole API price trends in India, "
        "search SHIMS knowledge for related suppliers, "
        "draft a short procurement memo, and save the findings to memory."
    )
    plan = plan_from_goal(goal, context={"demo": True})
    print(f"Created plan {plan.plan_id}: {plan.goal}")
    print(f"Steps ({len(plan.steps)}):")
    for s in plan.steps:
        deps = ", ".join(s.depends_on or [])
        print(f"  - {s.step_id}: [{s.tool_hint}] {s.description} (depends_on: {deps})")
    return plan


def task4_media_memory():
    print("\n=== TASK 4: Media memory ingestion ===")
    # Create a tiny fake PNG so the vision path has a real file
    tmp = Path(tempfile.gettempdir()) / "shims_demo_screen.png"
    # Minimal valid PNG 1x1 pixel
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452"
        "000000010000000108020000009077536a0000000a49444154"
        "789c63000000010200050d0a2db40000000049454e44ae426082"
    )
    tmp.write_bytes(png_bytes)
    result = ingest_media(str(tmp), "screen", title="Demo dashboard screenshot", tags=["demo", "screen"])
    print(f"Media ingest result: {result.get('ok')} doc_id={result.get('doc_id')}")
    if result.get("ok"):
        ctx = retrieve_context("demo dashboard screenshot", limit=5)
        hits = ctx.get("hits", [])
        print(f"Search returned {len(hits)} hits")
        for h in hits[:3]:
            print(f"  - {h.get('title')}: {str(h.get('content', ''))[:120]}...")
    else:
        print(f"Media ingest skipped: {result.get('error')}")
    return result


def main():
    task1_reindex_and_query()
    task2_prompt_evolution()
    task3_llm_plan()
    task4_media_memory()
    print("\n=== Demo complete ===")


if __name__ == "__main__":
    main()
