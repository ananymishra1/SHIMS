"""Iterative evolution loop for the SHIMS Local Factory instance.

Overnight cycle:
  1. Refresh the corpus (BMR, chemistry, enterprise, web, peer sync).
  2. Train the factory model (Ollama persona or PEFT LoRA).
  3. Evaluate the model against a benchmark.
  4. Compare to the previous best; promote if improved.
  5. Reflect on failures and generate improvement proposals.

The loop never auto-applies code changes.  Proposals are saved to
storage_local/evolution/proposals/ for user approval.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ROOT_DIR, settings
from . import cross_instance_improvement
from .local_factory_config import default_model, evolution_dir, models_dir
from .local_factory_corpus import build_corpus_async, corpus_stats


INSTANCE_ID = (os.getenv("SHIMS_INSTANCE_ID") or "primary").strip()
EVOLUTION_STATE_PATH = evolution_dir() / "state.json"
FEEDBACK_PATH = evolution_dir() / "feedback.jsonl"
BENCHMARK_PATH = evolution_dir() / "benchmark.jsonl"
PROPOSALS_DIR = evolution_dir() / "proposals"

_default_benchmark: list[dict[str, Any]] = [
    {"category": "bmr", "question": "What is a Batch Manufacturing Record (BMR)?", "expected": "batch manufacturing record"},
    {"category": "chemistry", "question": "What does ChemDFM help with?", "expected": "chemistry"},
    {"category": "enterprise", "question": "Name one SHIMS enterprise module.", "expected": "module"},
    {"category": "troubleshooting", "question": "How should a factory investigate a production deviation?", "expected": "deviation"},
]


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


def load_state() -> dict[str, Any]:
    if EVOLUTION_STATE_PATH.exists():
        try:
            return json.loads(EVOLUTION_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "best_score": 0.0,
        "best_tag": default_model(),
        "runs": [],
        "status": "idle",
        "last_run": None,
    }


def save_state(state: dict[str, Any]) -> None:
    EVOLUTION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVOLUTION_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_benchmark() -> list[dict[str, Any]]:
    if BENCHMARK_PATH.exists():
        return _jsonl_read(BENCHMARK_PATH)
    BENCHMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _jsonl_append(BENCHMARK_PATH, _default_benchmark)
    return _default_benchmark


def add_feedback(entry: dict[str, Any]) -> None:
    entry["recorded_at"] = _now()
    _jsonl_append(FEEDBACK_PATH, [entry])


def _score_answer(answer: str, expected: str) -> float:
    ans = answer.lower()
    exp = expected.lower()
    # Simple keyword score plus fuzzy overlap.
    if exp in ans:
        return 1.0
    exp_tokens = set(re.findall(r"\b\w+\b", exp))
    ans_tokens = set(re.findall(r"\b\w+\b", ans))
    if not exp_tokens:
        return 0.0
    overlap = len(exp_tokens & ans_tokens) / len(exp_tokens)
    return round(overlap, 2)


async def evaluate_model(tag: str, benchmark: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run the benchmark against a local Ollama model tag."""
    from . import ai
    benchmark = benchmark or load_benchmark()
    scores: list[float] = []
    details: list[dict[str, Any]] = []
    for item in benchmark:
        question = item.get("question", "")
        expected = item.get("expected", "")
        try:
            result = await ai.ask_ai(
                question,
                system="You are the SHIMS Local Factory assistant. Answer concisely and accurately.",
                provider="ollama",
                model=tag,
            )
            answer = result.text or ""
            score = _score_answer(answer, expected)
        except Exception as exc:
            answer = ""
            score = 0.0
            result = type("obj", (object,), {"error": str(exc), "route": "error"})()
        scores.append(score)
        details.append({
            "question": question,
            "expected": expected,
            "answer": answer[:500],
            "score": score,
            "error": getattr(result, "error", ""),
        })
    avg = round(sum(scores) / len(scores), 3) if scores else 0.0
    return {"average_score": avg, "details": details, "tag": tag, "evaluated_at": _now()}


def _train_model_subprocess(mode: str = "ollama") -> dict[str, Any]:
    script = ROOT_DIR / "scripts" / "train_local_factory_model.py"
    python = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    cmd = [str(python), str(script), "--mode", mode]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=86400,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-600:],
            "stderr": result.stderr[-600:],
        }
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": "training timed out", "partial_stderr": (exc.stderr or "")[-500:]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


async def _reflect_and_propose(failures: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    """Use the local model to generate concrete improvement proposals."""
    from . import ai
    proposals: list[dict[str, Any]] = []
    if not failures:
        return proposals

    failure_text = "\n".join(
        f"Q: {f['question']}\nExpected: {f['expected']}\nGot: {f['answer'][:200]}"
        for f in failures[:10]
    )
    prompt = (
        "You are improving the SHIMS Local Factory assistant. The model failed the following benchmark items. "
        "Propose 1-3 concrete improvements. Each proposal must be one of:\n"
        "- new_training_example (provide question + answer)\n"
        "- prompt_variant (provide a better system prompt snippet)\n"
        "- source_suggestion (suggest a new corpus source or web query)\n\n"
        f"Failures:\n{failure_text}\n\n"
        "Respond as a JSON list. Each item must include: type, title, purpose, detailed_description, target_instance (local|both), expected_benefit, risk (low|medium|high), content."
    )
    try:
        result = await ai.ask_ai(
            prompt,
            system="You are a careful improvement engineer. Output only valid JSON.",
            provider="ollama",
            model=default_model(),
        )
        raw = result.text or "[]"
        # Try to extract JSON list.
        match = re.search(r"\[.*\]", raw, re.S)
        if match:
            raw = match.group(0)
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            for p in parsed:
                proposal = {
                    "type": p.get("type", "unknown"),
                    "title": p.get("title", ""),
                    "purpose": p.get("purpose", ""),
                    "detailed_description": p.get("detailed_description", ""),
                    "target_instance": p.get("target_instance", "local"),
                    "expected_benefit": p.get("expected_benefit", ""),
                    "risk": p.get("risk", ""),
                    "content": p.get("content", ""),
                    "created_at": _now(),
                    "run_id": state.get("current_run_id"),
                }
                proposals.append(proposal)
                _jsonl_append(PROPOSALS_DIR / "proposals.jsonl", [proposal])
    except Exception as exc:
        proposals.append({"type": "error", "title": "reflection failed", "content": str(exc)[:300], "created_at": _now()})
    return proposals


def _record_run(state: dict[str, Any], run: dict[str, Any]) -> None:
    runs = state.get("runs", [])
    runs.append(run)
    state["runs"] = runs[-50:]  # keep last 50
    state["last_run"] = run["finished_at"]
    state["status"] = "idle"
    save_state(state)


async def run_evolution_cycle(*, train_mode: str = "ollama", sync_peers: list[str] | None = None) -> dict[str, Any]:
    """Execute one full evolution cycle."""
    state = load_state()
    run_id = f"run-{int(time.time())}"
    state["current_run_id"] = run_id
    state["status"] = "building_corpus"
    save_state(state)

    run: dict[str, Any] = {
        "run_id": run_id,
        "started_at": _now(),
        "train_mode": train_mode,
    }

    # 1. Corpus
    corpus = await build_corpus_async(force=False, max_web_pages=6)
    run["corpus"] = corpus

    # Optional peer sync.
    if sync_peers:
        from .inter_instance_bridge import sync_from_peer
        for peer_id in sync_peers:
            peer_result = await sync_from_peer(peer_id)
            run.setdefault("peer_sync", {})[peer_id] = peer_result

    state["status"] = "training"
    save_state(state)

    # 2. Train
    train_result = _train_model_subprocess(mode=train_mode)
    run["training"] = train_result

    state["status"] = "evaluating"
    save_state(state)

    # 3. Evaluate
    factory_tag = os.getenv("SHIMS_FACTORY_OLLAMA_TAG", "qwen2.5-3b-factory")
    eval_result = await evaluate_model(factory_tag)
    run["evaluation"] = eval_result

    # 4. Promote if improved
    best_score = state.get("best_score", 0.0)
    improved = eval_result["average_score"] > best_score
    run["improved"] = improved
    run["previous_best_score"] = best_score
    if improved:
        state["best_score"] = eval_result["average_score"]
        state["best_tag"] = factory_tag
        run["promoted_to"] = factory_tag

    # 5. Reflect on failures
    failures = [d for d in eval_result.get("details", []) if d.get("score", 0) < 0.6]
    run["proposals"] = await _reflect_and_propose(failures, state)

    run["finished_at"] = _now()

    # Cross-instance sync: share factory proposals with the peer Omni instance.
    if os.getenv("SHIMS_CROSS_INSTANCE_SYNC", "").strip().lower() in {"1", "true", "yes"}:
        try:
            peer_id = os.getenv("SHIMS_CROSS_INSTANCE_PEER", cross_instance_improvement.default_peer_id())
            sync_result = await cross_instance_improvement.run_cross_instance_sync(
                local_proposals=run.get("proposals", []), peer_id=peer_id
            )
            run["cross_instance_sync"] = sync_result
        except Exception as exc:
            run["cross_instance_sync"] = {"ok": False, "error": str(exc)[:200]}

    _record_run(state, run)
    return {"ok": True, "run": run, "state": state}


def start_background_evolution(train_mode: str = "ollama", sync_peers: list[str] | None = None) -> dict[str, Any]:
    """Start the evolution cycle in a background thread."""
    def _target() -> None:
        try:
            asyncio.run(run_evolution_cycle(train_mode=train_mode, sync_peers=sync_peers))
        except Exception as exc:
            state = load_state()
            state["status"] = f"error: {exc}"
            save_state(state)

    thread = threading.Thread(target=_target, name="factory-evolution", daemon=True)
    thread.start()
    return {"ok": True, "status": "started", "state": load_state()}


def evolution_status() -> dict[str, Any]:
    state = load_state()
    return {
        "ok": True,
        "instance_id": INSTANCE_ID,
        "status": state.get("status", "idle"),
        "best_score": state.get("best_score", 0.0),
        "best_tag": state.get("best_tag", default_model()),
        "last_run": state.get("last_run"),
        "runs_count": len(state.get("runs", [])),
        "corpus": corpus_stats(),
    }
