"""Evaluation-Driven Improvement Loop — SHIMS self-improvement orchestrator.

Phase 3.2 rewrite:
* Uses real eval cases from shared/prompt_evolution.py::default_eval_cases().
* Generates LLM-based root-cause reflection on failures.
* Produces concrete, safe proposals only (never auto-applies):
  - self.patch proposals for allowed source targets
  - new skills via shared/skills.py
  - prompt variant mutations via shared/prompt_evolution.py

It is safe to run automatically: it only *proposes* changes; human approval is
still required before any live code is modified.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .config import STORAGE_DIR
from .eval_harness import run_reliability_evals
from .prompt_evolution import (
    PromptVariant,
    default_eval_cases,
    ensure_control_variant,
    generate_mutations,
    get_active_variant,
    run_eval_suite,
)
from .security import new_id
from .self_evolver import propose_patch
from .skills import save_skill
from . import cross_instance_improvement

try:
    from .telemetry import log_event
except Exception:  # pragma: no cover
    def log_event(*args: Any, **kwargs: Any) -> None:
        return None

IMPROVEMENT_DIR = STORAGE_DIR / "improvement_loop"
IMPROVEMENT_DIR.mkdir(parents=True, exist_ok=True)

# Model used for root-cause reflection. Defaults to the mutation model so the
# improvement loop can run entirely on local infrastructure.
_IMPROVEMENT_REFLECTION_MODEL = os.getenv(
    "SHIMS_IMPROVEMENT_MODEL", os.getenv("SHIMS_MUTATION_MODEL", "qwen2.5:7b")
)


def _now() -> str:
    return str(time.time())


def _load_run(run_id: str) -> dict[str, Any]:
    p = IMPROVEMENT_DIR / f"{run_id}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"ok": False, "error": "run not found"}


def _save_run(run_id: str, data: dict[str, Any]) -> None:
    (IMPROVEMENT_DIR / f"{run_id}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


def _parse_reflection_json(raw: str) -> dict[str, Any]:
    """Extract the structured reflection object from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


def _call_reflection_llm(prompt: str, eval_summary: dict[str, Any]) -> dict[str, Any]:
    """Ask the reflection model to produce a structured improvement plan.

    Tests should monkeypatch this function to avoid requiring a live LLM.
    The ``eval_summary`` argument is included for callers/tests that want to
    key mocks off the actual failures without re-parsing the full prompt.

    Expected JSON shape:
    {
      "reflection": "<short narrative>",
      "root_causes": [{"area": "reliability|wave|prompt", "detail": "..."}],
      "proposals": [
        {
          "type": "patch|skill|prompt_variant",
          "title": "...",
          "purpose": "...",
          "detailed_description": "...",
          "target_instance": "primary|local|both",
          "affected_files": ["..."],
          "expected_benefit": "...",
          "risk": "...",
          ...type-specific fields...
        }
      ]
    }
    """
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    system_prompt = (
        "You are SHIMS's self-improvement auditor. Given eval results, produce a short root-cause "
        "reflection and concrete, safe improvement proposals. Return ONLY valid JSON matching the "
        "requested schema. Do not auto-apply anything; only propose."
    )
    payload = {
        "model": _IMPROVEMENT_REFLECTION_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.4, "num_predict": 2048},
        "keep_alive": "5m",
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(f"{host}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return {}
    raw = (data.get("message") or {}).get("content") or data.get("response") or ""
    return _parse_reflection_json(raw)


def _build_reflection_prompt(
    reliability: dict[str, Any],
    wave: dict[str, Any],
    prompt_run: dict[str, Any],
) -> str:
    schema = {
        "reflection": "<1-2 sentence root-cause summary>",
        "root_causes": [
            {"area": "reliability|wave|prompt", "detail": "<what failed and why>"}
        ],
        "proposals": [
            {
                "type": "patch",
                "title": "<short human-readable title>",
                "why_proposal": "<what eval failure or gap motivates this proposal>",
                "problem_statement": "<clear statement of the problem being solved>",
                "solution_proposed": "<concrete description of the fix>",
                "options_considered": ["<option 1>", "<option 2>", "<chosen option and why>"],
                "files_to_change": ["shared/example.py", "backend/app/main.py"],
                "purpose": "<what problem this patch solves in one sentence>",
                "detailed_description": "<why this exact change is needed and how it fixes the failure>",
                "target_instance": "primary|local|both",
                "affected_files": ["shared/example.py", "backend/app/main.py"],
                "expected_benefit": "<quantitative or qualitative benefit>",
                "risk": "<low|medium|high> — <what could go wrong>",
                "relative_path": "shared/... or backend/... (allowed roots only)",
                "new_content": "<full proposed file content>",
                "reason": "<why this fixes a failure>",
            },
            {
                "type": "skill",
                "title": "<short human-readable title>",
                "why_proposal": "<what eval failure or gap motivates this proposal>",
                "problem_statement": "<clear statement of the problem being solved>",
                "solution_proposed": "<concrete description of the skill>",
                "options_considered": ["<option 1>", "<option 2>", "<chosen option and why>"],
                "files_to_change": [],
                "purpose": "<what the skill is for>",
                "detailed_description": "<when and how the skill should be used>",
                "target_instance": "primary|local|both",
                "expected_benefit": "<benefit>",
                "risk": "<low|medium|high> — <what could go wrong>",
                "name": "<skill name>",
                "summary": "<one-line summary>",
                "body": "<skill body>",
                "tags": ["auto", "improvement-loop"],
            },
            {
                "type": "prompt_variant",
                "title": "<short human-readable title>",
                "why_proposal": "<what eval failure or gap motivates this proposal>",
                "problem_statement": "<clear statement of the problem being solved>",
                "solution_proposed": "<concrete description of the prompt change>",
                "options_considered": ["<option 1>", "<option 2>", "<chosen option and why>"],
                "files_to_change": [],
                "purpose": "<what behavior this variant is meant to improve>",
                "detailed_description": "<how the wording changes behavior>",
                "target_instance": "primary|local|both",
                "expected_benefit": "<expected eval improvement>",
                "risk": "<low|medium|high> — <what could go wrong>",
                "prompt_text": "<complete alternative system prompt>",
                "reason": "<why it should score better>",
            },
        ],
    }
    parts = [
        "SHIMS improvement-loop eval summary:",
        "",
        "## Reliability evals",
        json.dumps(reliability, indent=2, default=str),
        "",
        "## Wave latency evals",
        json.dumps(wave, indent=2, default=str),
        "",
        "## Prompt-quality evals",
        json.dumps(prompt_run, indent=2, default=str),
        "",
        "Analyze the failures and produce a JSON object with this exact schema:",
        json.dumps(schema, indent=2),
        "",
        "Rules:",
        "- Only propose patches for paths under allowed SHIMS source roots:",
        "  shared/, backend/, apps/, shims_enterprise/, frontend/, tests/, docs/, scripts/, android_app/, termux_offline_runtime/, desktop_bridge/.",
        "- NEVER target shared/self_evolver.py, shared/security.py, or shared/config.py.",
        "- Keep new_content small and surgical. If no concrete fix is appropriate, emit an empty proposals list.",
        "- For prompt_variant, provide a complete, usable system prompt that differs from the current one.",
        "- target_instance must be one of: primary, local, both. Use 'local' only if the change is specific to the offline Ollama factory instance.",
        "- risk must be one of: low, medium, high. Include a brief justification.",
    ]
    return "\n".join(parts)


def _run_wave_latency_evals() -> dict[str, Any]:
    """Run the wave-latency pytest harness in a subprocess."""
    wave_summary: dict[str, Any] = {"ok": False, "error": "not run"}
    try:
        import subprocess
        import sys

        python_exe = sys.executable
        proc = subprocess.run(
            [python_exe, "-m", "pytest", "tests/test_wave_latency.py", "-v", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        wave_summary = {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:],
        }
    except Exception as exc:
        wave_summary = {"ok": False, "error": str(exc)[:200]}
    return wave_summary


def _run_prompt_evals(system_prompt_text: str) -> dict[str, Any]:
    """Run real prompt-quality eval cases against the active/control variant."""
    prompt_run: dict[str, Any] = {"ok": False, "error": "not run"}
    try:
        control = ensure_control_variant(system_prompt_text or "You are SHIMS.")
        pr = run_eval_suite(control.id, default_eval_cases())
        prompt_run = {
            "ok": True,
            "run_id": pr.id,
            "summary": pr.summary,
            "results": [r.__dict__ for r in pr.results],
        }
    except Exception as exc:
        prompt_run = {"ok": False, "error": str(exc)[:200]}
    return prompt_run


def _propose_patch_safe(relative_path: str, new_content: str, reason: str) -> dict[str, Any]:
    """Wrap self-evolver propose_patch so the loop never auto-applies."""
    return propose_patch(
        relative_path=relative_path,
        new_content=new_content,
        reason=reason,
        scope="improvement_loop",
        proposed_by="improvement_loop",
    )


def _propose_skill(
    name: str, summary: str, body: str, tags: list[str] | None = None
) -> dict[str, Any]:
    return save_skill(
        name=name,
        summary=summary,
        body=body,
        tags=tags or ["auto", "improvement-loop"],
        source="improvement_loop",
    )


def _propose_prompt_variant(parent: PromptVariant, prompt_text: str, reason: str) -> list[dict[str, Any]]:
    """Create a prompt variant from an LLM-suggested mutation (no live LLM round-trip)."""

    def _llm_mutation(_prompt: str) -> list[str]:
        return [prompt_text]

    variants = generate_mutations(parent, n=1, mutate_fn=_llm_mutation)
    return [{"type": "prompt_variant", "variant_id": v.id, "reason": reason} for v in variants]


def _apply_proposal_item(item: dict[str, Any], control: PromptVariant) -> dict[str, Any] | None:
    """Turn one reflection proposal into a concrete artifact. Never auto-applies code."""
    ptype = item.get("type")
    meta = {
        "title": item.get("title", "").strip() or item.get("reason", "").strip()[:80],
        "why_proposal": item.get("why_proposal", "").strip(),
        "problem_statement": item.get("problem_statement", "").strip(),
        "solution_proposed": item.get("solution_proposed", "").strip(),
        "options_considered": item.get("options_considered", []) if isinstance(item.get("options_considered"), list) else [],
        "files_to_change": item.get("files_to_change", []) if isinstance(item.get("files_to_change"), list) else [],
        "purpose": item.get("purpose", "").strip(),
        "detailed_description": item.get("detailed_description", "").strip(),
        "target_instance": item.get("target_instance", "both"),
        "expected_benefit": item.get("expected_benefit", "").strip(),
        "risk": item.get("risk", "").strip(),
    }
    if ptype == "patch":
        rel = item.get("relative_path", "").strip()
        content = item.get("new_content", "")
        reason = item.get("reason", "proposed by improvement loop")
        if not rel or not content:
            return None
        patch = _propose_patch_safe(rel, content, reason)
        patch["meta"] = meta
        patch["affected_files"] = item.get("affected_files", [rel])
        return {"type": "patch", **patch}
    if ptype == "skill":
        name = item.get("name", "").strip()
        summary = item.get("summary", "").strip()
        body = item.get("body", "")
        tags = item.get("tags") or ["auto", "improvement-loop"]
        if not name or not summary:
            return None
        skill = _propose_skill(name, summary, body, tags)
        skill["meta"] = meta
        return {"type": "skill", **skill}
    if ptype == "prompt_variant":
        prompt_text = item.get("prompt_text", "").strip()
        reason = item.get("reason", "proposed by improvement loop")
        if not prompt_text or prompt_text.strip().lower() == control.prompt.strip().lower():
            # If the LLM did not provide a concrete prompt, fall back to a deterministic mutation.
            variants = generate_mutations(control, n=1, mutate_fn=_fallback_mutate_fn)
            return {"type": "prompt_variant", "variants": [{"id": v.id, "reason": reason, "meta": meta} for v in variants]}
        variants = _propose_prompt_variant(control, prompt_text, reason)
        for v in variants:
            v["meta"] = meta
        return {"type": "prompt_variant", "variants": variants}
    return None


def _fallback_mutate_fn(prompt: str) -> list[str]:
    """Deterministic mutation fallback that avoids a live LLM call."""
    mutants: list[str] = []
    if "step by step" not in prompt.lower():
        mutants.append(prompt + "\n\nThink step by step before choosing tools.")
    if "json" not in prompt.lower():
        mutants.append(prompt + "\n\nWhen calling tools, output strictly valid JSON.")
    mutants.append(prompt.replace("helpful", "concise, helpful"))
    return mutants


def _heuristic_fallback_proposals(
    failed_reliability: list[dict[str, Any]],
    wave_ok: bool,
    prompt_score: float,
    control: PromptVariant,
) -> list[dict[str, Any]]:
    """Produce concrete proposals when the LLM reflection is unavailable or empty."""
    proposals: list[dict[str, Any]] = []
    if failed_reliability:
        sk = _propose_skill(
            name=f"Auto-fix from eval ({len(failed_reliability)} failures)",
            summary="Generated by improvement loop after eval failures: "
            + ", ".join(f.get("name", f.get("case", "unknown")) for f in failed_reliability[:3]),
            body=json.dumps({"failures": failed_reliability, "timestamp": _now()}, indent=2, default=str),
            tags=["auto", "improvement-loop"],
        )
        proposals.append({"type": "skill", "skill_id": sk["id"], "reason": "capture eval failure pattern"})
    if not wave_ok:
        variants = generate_mutations(control, n=1, mutate_fn=_fallback_mutate_fn)
        proposals.extend(
            {"type": "prompt_variant", "variant_id": v.id, "reason": "wave latency eval failed; suggest a tighter prompt"}
            for v in variants
        )
    if prompt_score < 0.8:
        variants = generate_mutations(control, n=1, mutate_fn=_fallback_mutate_fn)
        proposals.extend(
            {"type": "prompt_variant", "variant_id": v.id, "reason": "prompt variant scored below threshold; suggest mutation"}
            for v in variants
        )
    return proposals


def run_improvement_cycle(system_prompt_text: str = "") -> dict[str, Any]:
    """Run one full eval → reflect → propose cycle."""
    run_id = new_id("imp")
    started = time.time()

    # 1) Reliability evals
    reliability = run_reliability_evals()

    # 2) Wave latency evals
    wave_summary = _run_wave_latency_evals()

    # 3) Prompt-quality evals using real cases from prompt_evolution
    prompt_run = _run_prompt_evals(system_prompt_text)

    # 4) Reflection & proposal generation
    control = get_active_variant()
    if control is None:
        control = ensure_control_variant(system_prompt_text or "You are SHIMS.")

    failed_rel = [r for r in reliability.get("results", []) if not r.get("ok")]
    prompt_score = (prompt_run.get("summary") or {}).get("score", 0.0)

    reflection_payload: dict[str, Any] = {}
    proposals: list[dict[str, Any]] = []
    try:
        prompt = _build_reflection_prompt(reliability, wave_summary, prompt_run)
        eval_summary = {
            "failed_reliability": failed_rel,
            "wave_ok": wave_summary.get("ok", False),
            "prompt_score": prompt_score,
        }
        reflection_payload = _call_reflection_llm(prompt, eval_summary)
        if not isinstance(reflection_payload, dict):
            reflection_payload = {"error": "llm returned non-object"}
    except Exception as exc:
        reflection_payload = {"error": str(exc)[:200]}

    if isinstance(reflection_payload, dict) and reflection_payload.get("proposals"):
        for item in reflection_payload.get("proposals", []):
            try:
                applied = _apply_proposal_item(item, control)
                if applied:
                    proposals.append(applied)
            except Exception as exc:
                proposals.append({"type": "error", "error": str(exc)[:200], "item": item})
    else:
        proposals = _heuristic_fallback_proposals(
            failed_rel, wave_summary.get("ok", False), prompt_score, control
        )

    # 5) Persist run
    result = {
        "ok": True,
        "run_id": run_id,
        "started_at": started,
        "finished_at": time.time(),
        "reliability": reliability,
        "wave": wave_summary,
        "prompt": prompt_run,
        "proposals": proposals,
        "reflection": {
            "text": reflection_payload.get("reflection", "") if isinstance(reflection_payload, dict) else "",
            "root_causes": reflection_payload.get("root_causes", []) if isinstance(reflection_payload, dict) else [],
            "llm_error": reflection_payload.get("error", "") if isinstance(reflection_payload, dict) else "",
            "total_cases": len(reliability.get("results", [])),
            "failed_cases": len(failed_rel),
            "wave_latency_ok": wave_summary.get("ok", False),
            "prompt_score": prompt_score,
        },
    }
    _save_run(run_id, result)

    log_event(
        "improvement_loop.run",
        route="improvement",
        provider="local",
        model="self",
        ok=True,
        message=f"{len(proposals)} proposals generated",
        metadata={"run_id": run_id, "failed": len(failed_rel), "proposals": len(proposals)},
    )

    # Cross-instance sync: share proposals with the peer Omni instance.
    if os.getenv("SHIMS_CROSS_INSTANCE_SYNC", "").strip().lower() in {"1", "true", "yes"}:
        try:
            peer_id = os.getenv("SHIMS_CROSS_INSTANCE_PEER", cross_instance_improvement.default_peer_id())
            sync_result = cross_instance_improvement.run_cross_instance_sync(local_proposals=proposals, peer_id=peer_id)
            result["cross_instance_sync"] = sync_result
        except Exception as exc:
            result["cross_instance_sync"] = {"ok": False, "error": str(exc)[:200]}

    _save_run(run_id, result)

    return result


def list_improvement_runs(limit: int = 20) -> list[dict[str, Any]]:
    runs: list[tuple[float, dict[str, Any]]] = []
    for p in sorted(IMPROVEMENT_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            runs.append((d.get("finished_at", 0), d))
        except Exception:
            continue
    return [r[1] for r in runs]
