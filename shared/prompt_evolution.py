"""Prompt Evolution Lab — A/B test system-prompt variants against eval tasks.

Storage layout:
    storage/prompt_variants/<variant_id>.json
    storage/prompt_evolution/runs/<run_id>.json

A variant is a candidate system prompt. A run pits one or more variants against
a set of eval cases and produces a scorecard. The best-scoring variant can be
promoted to "active" and injected into the live system prompt.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from .config import STORAGE_DIR
from .security import new_id

VARIANTS_DIR = STORAGE_DIR / "prompt_variants"
RUNS_DIR = STORAGE_DIR / "prompt_evolution" / "runs"
for d in (VARIANTS_DIR, RUNS_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _variant_path(variant_id: str) -> Path:
    return VARIANTS_DIR / f"{variant_id}.json"


def _run_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


@dataclass
class PromptVariant:
    id: str
    name: str
    prompt: str
    parent_id: str | None = None
    generation: int = 0
    active: bool = False
    runs: int = 0
    wins: int = 0
    avg_score: float = 0.0
    avg_latency_ms: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PromptVariant":
        return PromptVariant(**{k: v for k, v in d.items() if k in PromptVariant.__dataclass_fields__})


@dataclass
class EvalResult:
    case: str
    ok: bool
    latency_ms: float
    score: float  # 0.0 - 1.0
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Run:
    id: str
    variant_id: str
    started_at: float
    finished_at: float | None = None
    results: list[EvalResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def save_variant(v: PromptVariant) -> PromptVariant:
    v.updated_at = time.time()
    _variant_path(v.id).write_text(json.dumps(v.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return v


def load_variant(variant_id: str) -> PromptVariant | None:
    p = _variant_path(variant_id)
    if not p.exists():
        return None
    try:
        return PromptVariant.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def list_variants() -> list[PromptVariant]:
    out: list[PromptVariant] = []
    for p in VARIANTS_DIR.glob("*.json"):
        v = load_variant(p.stem)
        if v:
            out.append(v)
    out.sort(key=lambda x: (-x.active, -x.avg_score, -x.wins, x.runs))
    return out


def get_active_variant() -> PromptVariant | None:
    for v in list_variants():
        if v.active:
            return v
    return None


def ensure_control_variant(prompt_text: str, name: str = "control") -> PromptVariant:
    """Create a control variant if none exists, or return the matching active/name one."""
    active = get_active_variant()
    if active and active.name == name:
        return active
    for v in list_variants():
        if v.name == name:
            if not v.active:
                # Deactivate any other active variant and reactivate this baseline.
                for other in list_variants():
                    if other.active:
                        other.active = False
                        save_variant(other)
                v.active = True
                save_variant(v)
            return v
    v = PromptVariant(id=new_id("prompt"), name=name, prompt=prompt_text, generation=0, active=True)
    return save_variant(v)


def generate_mutations(parent: PromptVariant, n: int = 3,
                       mutate_fn: Callable[[str], list[str]] | None = None) -> list[PromptVariant]:
    """Generate N mutant variants from a parent prompt."""
    if mutate_fn is None:
        mutate_fn = _default_mutate_fn
    texts = mutate_fn(parent.prompt)
    variants: list[PromptVariant] = []
    for i, text in enumerate(texts[:n]):
        v = PromptVariant(
            id=new_id("prompt"),
            name=f"{parent.name}-gen{parent.generation + 1}-{i + 1}",
            prompt=text,
            parent_id=parent.id,
            generation=parent.generation + 1,
        )
        save_variant(v)
        variants.append(v)
    return variants


def _default_mutate_fn(prompt: str) -> list[str]:
    """Generate prompt mutations. Tries an LLM first, falls back to heuristics."""
    try:
        llm_mutants = _llm_mutate_fn(prompt)
        if llm_mutants:
            return llm_mutants
    except Exception:
        pass
    # Fallback heuristics
    mutants: list[str] = []
    if "Think step by step" not in prompt:
        mutants.append(prompt + "\n\nThink step by step before choosing tools.")
    if "JSON" not in prompt:
        mutants.append(prompt + "\n\nWhen calling tools, output strictly valid JSON.")
    mutants.append(prompt.replace("helpful", "concise, helpful"))
    return mutants


def default_eval_cases() -> list[tuple[str, Callable[[], tuple[bool, str, dict[str, Any]]]]]:
    """Default lightweight eval cases that score prompt quality by content."""
    prompt_text = ""
    active = get_active_variant()
    if active:
        prompt_text = active.prompt
    else:
        from shared.config import settings
        prompt_text = getattr(settings, "system_prompt", "")

    def _has_any(text: str, *needles: str) -> bool:
        lower = text.lower()
        return any(n in lower for n in needles)

    def _case_identity() -> tuple[bool, str, dict[str, Any]]:
        ok = _has_any(prompt_text, "shims", "assistant", "ai operator")
        return ok, "prompt names the assistant identity", {"found_identity": ok}

    def _case_tool_instructions() -> tuple[bool, str, dict[str, Any]]:
        ok = _has_any(prompt_text, "tool", "tools", "function", "json")
        return ok, "prompt mentions tools/tool JSON", {"has_tool_instructions": ok}

    def _case_safety_reminder() -> tuple[bool, str, dict[str, Any]]:
        ok = _has_any(prompt_text, "safe", "safety", "harmful", "refuse", "approve", "confirmation")
        return ok, "prompt contains safety/approval guidance", {"has_safety": ok}

    def _case_memory_guidance() -> tuple[bool, str, dict[str, Any]]:
        ok = _has_any(prompt_text, "memory", "remember", "retrieve", "context")
        return ok, "prompt encourages memory/context use", {"has_memory": ok}

    def _case_conciseness() -> tuple[bool, str, dict[str, Any]]:
        ok = len(prompt_text) < 12000
        return ok, "prompt is not excessively long", {"length": len(prompt_text), "under_limit": ok}

    return [
        ("identity", _case_identity),
        ("tool_instructions", _case_tool_instructions),
        ("safety_reminder", _case_safety_reminder),
        ("memory_guidance", _case_memory_guidance),
        ("conciseness", _case_conciseness),
    ]


def _llm_mutate_fn(prompt: str, n: int = 3) -> list[str]:
    """Ask a cheap local model to produce prompt mutations."""
    if not prompt or len(prompt) < 20:
        return []

    system_prompt = (
        "You are a prompt-engineering assistant. Given a system prompt, produce exactly 3 alternative variants "
        "that might improve task performance. Return ONLY a JSON array of strings. No markdown, no explanation."
    )
    user_prompt = (
        f"Original system prompt:\n```\n{prompt[:3000]}\n```\n\n"
        "Generate 3 mutated variants. Each variant should be a complete system prompt. "
        "Vary style, structure, or emphasis (e.g., more concise, stronger tool instructions, more safety reminders). "
        'Return: ["variant1", "variant2", "variant3"]'
    )

    model = os.getenv("SHIMS_MUTATION_MODEL", "qwen2.5:7b")
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.8, "num_predict": 2048},
        "keep_alive": "5m",
    }

    with httpx.Client(timeout=90.0) as client:
        r = client.post(f"{host}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()

    raw = (data.get("message") or {}).get("content") or data.get("response") or ""
    raw = raw.strip()
    if "[" in raw and "]" in raw:
        raw = raw[raw.find("[") : raw.rfind("]") + 1]
    if not raw:
        return []

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        return []

    mutants: list[str] = []
    for item in parsed:
        text = str(item).strip()
        if text and text.lower() != prompt.strip().lower() and len(text) > 20:
            mutants.append(text)
    return mutants[:n]


def score_run(results: list[EvalResult]) -> dict[str, Any]:
    if not results:
        return {"score": 0.0, "pass_rate": 0.0, "avg_latency_ms": 0.0, "cases": 0}
    ok_count = sum(1 for r in results if r.ok)
    total_score = sum(r.score for r in results)
    total_latency = sum(r.latency_ms for r in results)
    return {
        "score": round(total_score / len(results), 3),
        "pass_rate": round(ok_count / len(results), 3),
        "avg_latency_ms": round(total_latency / len(results), 1),
        "cases": len(results),
    }


def run_eval_suite(variant_id: str, cases: list[tuple[str, Callable[[str], tuple[bool, str, dict[str, Any]]]]],
                   prompt_injector: Callable[[str], Any] | None = None) -> Run:
    """Run a suite of eval cases against a variant and update its stats."""
    variant = load_variant(variant_id)
    if variant is None:
        raise ValueError(f"variant not found: {variant_id}")

    run_id = new_id("run")
    run = Run(id=run_id, variant_id=variant_id, started_at=time.time())
    results: list[EvalResult] = []

    # Optional side effect: tell the framework to use this prompt globally during eval.
    if prompt_injector:
        prompt_injector(variant.prompt)

    for name, check in cases:
        t0 = time.perf_counter()
        try:
            ok, message, metadata = check()
        except Exception as exc:
            ok, message, metadata = False, str(exc)[:200], {}
        latency_ms = (time.perf_counter() - t0) * 1000
        score = 1.0 if ok else 0.0
        results.append(EvalResult(case=name, ok=ok, latency_ms=latency_ms, score=score, message=message, metadata=metadata))

    run.results = results
    run.finished_at = time.time()
    summary = score_run(results)
    run.summary = summary

    # Persist run
    _run_path(run_id).write_text(
        json.dumps({
            "id": run.id,
            "variant_id": run.variant_id,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "results": [asdict(r) for r in results],
            "summary": summary,
        }, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # Update variant stats
    variant.runs += 1
    all_runs = [_run_path(r) for r in RUNS_DIR.glob("*.json") if json.loads(r.read_text(encoding="utf-8")).get("variant_id") == variant_id]
    scores: list[float] = []
    latencies: list[float] = []
    wins = 0
    for rp in all_runs:
        try:
            rd = json.loads(rp.read_text(encoding="utf-8"))
            scores.append(rd["summary"]["score"])
            latencies.append(rd["summary"]["avg_latency_ms"])
            if rd["summary"].get("score", 0) >= 0.8 and rd["summary"].get("pass_rate", 0) >= 0.8:
                wins += 1
        except Exception:
            continue
    if scores:
        variant.avg_score = round(sum(scores) / len(scores), 3)
    if latencies:
        variant.avg_latency_ms = round(sum(latencies) / len(latencies), 1)
    variant.wins = wins
    save_variant(variant)

    return run


def promote_variant(variant_id: str) -> PromptVariant | None:
    """Mark a variant as active and deactivate all others."""
    target = load_variant(variant_id)
    if target is None:
        return None
    for v in list_variants():
        if v.active and v.id != variant_id:
            v.active = False
            save_variant(v)
    target.active = True
    return save_variant(target)


def compare_variants(variant_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Return a scoreboard of variants."""
    variants = [load_variant(vid) for vid in variant_ids] if variant_ids else list_variants()
    rows: list[dict[str, Any]] = []
    for v in variants:
        if v is None:
            continue
        rows.append({
            "id": v.id,
            "name": v.name,
            "generation": v.generation,
            "active": v.active,
            "runs": v.runs,
            "wins": v.wins,
            "avg_score": v.avg_score,
            "avg_latency_ms": v.avg_latency_ms,
        })
    rows.sort(key=lambda r: (-r["active"], -r["avg_score"], -r["wins"]))
    return rows
