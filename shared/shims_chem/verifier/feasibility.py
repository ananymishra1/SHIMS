"""
End-to-end route feasibility scoring.

Given a list of reaction-SMILES (a multi-step route), run every verifier and
roll the results into a single feasibility score with a structured breakdown.

This is what the retrosynthesis Pareto layer (fto/scoring.py) consumes.
"""
from __future__ import annotations
import time

from .hazards import flag_hazards
from .reactions import atom_map_reaction, check_reaction_balance
from .smiles import validate_smiles
from .thermo import estimate_thermodynamics
from .types import Issue, ToolResult


def score_route_feasibility(steps: list[str]) -> ToolResult:
    """
    `steps`: ordered list of reaction-SMILES strings (one per synthetic step).
    Returns: ToolResult with overall feasibility 0..1 and per-step detail.
    """
    t0 = time.perf_counter()
    if not steps:
        return ToolResult(False, "score_route_feasibility", {},
                          [Issue("empty_route", "error", "Empty route")],
                          (time.perf_counter() - t0) * 1000)

    per_step: list[dict] = []
    issues: list[Issue] = []
    score = 1.0

    for i, step in enumerate(steps, 1):
        bal = check_reaction_balance(step)
        atm = atom_map_reaction(step)
        thr = estimate_thermodynamics(step)

        # Per-product hazard scan
        prod_smis = bal.data.get("report", {}).get("products", [])
        haz_issues: list[Issue] = []
        for p in prod_smis:
            haz = flag_hazards(p)
            for iss in haz.issues:
                if iss.severity in ("warn", "error", "critical"):
                    haz_issues.append(iss)

        step_score = 1.0
        if bal.has_blockers():
            step_score *= 0.0
        elif not bal.data.get("report", {}).get("balanced", False):
            step_score *= 0.8

        if atm.data.get("confidence") is not None:
            step_score *= max(0.4, float(atm.data["confidence"]))

        if thr.data.get("verdict", "").startswith("unfavorable"):
            step_score *= 0.7

        # Hazard penalties
        for iss in haz_issues:
            if iss.severity == "critical":
                step_score *= 0.1
            elif iss.severity == "error":
                step_score *= 0.6
            elif iss.severity == "warn":
                step_score *= 0.9

        per_step.append({
            "step": i, "rxn": step, "step_score": round(step_score, 3),
            "balanced": bal.data.get("report", {}).get("balanced"),
            "atom_map_confidence": atm.data.get("confidence"),
            "thermo": thr.data.get("verdict"),
            "hazards": [i.__dict__ for i in haz_issues],
            "blockers": [i.__dict__ for i in bal.issues if i.severity in ("error", "critical")],
        })
        score *= step_score

    score = round(score, 4)
    if score < 0.05:
        issues.append(Issue("route_blocked", "error", f"Route feasibility {score} — at least one step is blocked"))
    elif score < 0.4:
        issues.append(Issue("route_weak", "warn", f"Route feasibility {score} is low; consider alternatives"))

    return ToolResult(
        ok=score >= 0.05,
        tool="score_route_feasibility",
        data={"score": score, "steps": per_step, "n_steps": len(steps)},
        issues=issues,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )
