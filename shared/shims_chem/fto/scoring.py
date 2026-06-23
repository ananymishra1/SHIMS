"""
Multi-objective route scoring.

Inputs: a list of verified routes (each containing per-step feasibility data
from `score_route_feasibility`).

Output: same routes, augmented with `scores` (per-axis) and `composite` (single
ranking number), Pareto-sorted then composite-sorted.

The composite is intentionally simple and weights-configurable; the Pareto
front is the honest output (no single number does justice to true multi-
objective trade-offs, so we expose both).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from ..verifier import canonical_smiles
from .corpus import PatentCorpus, PatentHit, SyntheticCorpus


@dataclass
class Weights:
    feasibility: float = 0.25
    fto: float = 0.25
    yield_: float = 0.15
    impurity: float = 0.10
    scalability: float = 0.15
    regulatory: float = 0.10


DEFAULT_WEIGHTS = Weights()


# Tiny material catalog (USD/kg). In production: substitute with a live or
# scraped sourcing catalog (Sigma, TCI, regional API suppliers).
_BULK_CATALOG_USD_PER_KG: dict[str, float] = {
    "CCO": 1.5,                 # ethanol
    "CC(=O)O": 2.0,             # acetic acid
    "CN": 4.0,                  # methylamine
    "Nc1ccc(O)cc1": 80.0,       # 4-aminophenol
    "c1ccc(Br)cc1": 25.0,       # bromobenzene
    "O": 0.0,                   # water
    "CO": 1.0,                  # methanol
}


def _starting_material_cost(sm_list: list[str]) -> float:
    """Sum of catalog cost for known starting materials; unknowns get a high penalty cost."""
    total = 0.0
    unknown_penalty = 200.0  # USD/kg for anything we don't know — penalizes exotic SMs
    for sm in sm_list:
        canon = canonical_smiles(sm) or sm
        total += _BULK_CATALOG_USD_PER_KG.get(canon, unknown_penalty)
    return round(total, 2)


def _scalability_score(route: dict[str, Any]) -> float:
    """Higher = more scalable. Penalizes step count, critical hazards, low feasibility."""
    n = max(1, route.get("n_steps", len(route.get("steps", []))))
    base = max(0.0, 1.0 - 0.1 * (n - 1))  # 1 step = 1.0, 5 steps = 0.6
    feas_steps = route.get("feasibility", {}).get("steps", [])
    for s in feas_steps:
        for h in s.get("hazards", []):
            if h.get("severity") == "critical":
                base *= 0.2
            elif h.get("severity") == "error":
                base *= 0.6
    return round(min(1.0, max(0.0, base)), 3)


def _regulatory_score(route: dict[str, Any], market: str) -> float:
    """
    Heuristic: penalize ICH-Q3C class-1 solvents and uncommon catalysts; reward
    routes that consume only common, well-precedented reagents.
    """
    text = "|".join(route.get("steps", []) + route.get("starting_materials", []))
    bad_substrings = ["benzene", "ClCCCl", "CCl4", "[Os]"]
    score = 1.0
    for b in bad_substrings:
        if b in text:
            score *= 0.6
    # Bonus for being in regions we care about — markets is informational, not a multiplier
    return round(max(0.0, min(1.0, score)), 3)


def _yield_estimate(route: dict[str, Any]) -> float:
    """Use the verifier's feasibility as a proxy; in production swap for the Molecular Transformer yield head."""
    return round(float(route.get("feasibility", {}).get("score", 0.0)), 3)


def _impurity_risk(route: dict[str, Any]) -> float:
    """Sum of warn/error severities across steps, normalized to 0..1 risk."""
    feas_steps = route.get("feasibility", {}).get("steps", [])
    n_warns = 0
    for s in feas_steps:
        for h in s.get("hazards", []):
            if h.get("severity") in ("warn", "error"):
                n_warns += 1
    return round(min(1.0, n_warns * 0.15), 3)


def _fto_risk(route: dict[str, Any], corpus: PatentCorpus,
              claim_classifier=None) -> tuple[float, list[PatentHit]]:
    """
    Patent overlap score.

    If a BERT claim classifier is provided, each retrieved patent hit is
    re-scored against the claim text and we use max(p_covered, similarity)
    as the per-patent risk. This usually shrinks false positives (high
    structural similarity but the claim is about something else) and grows
    coverage of true Markush claims that the structural matcher under-weights.
    """
    hits: list[PatentHit] = []
    for s in [route.get("target", ""), *route.get("starting_materials", []), *route.get("intermediates", [])]:
        hits.extend(corpus.lookup_by_similarity(s, top_k=3, min_similarity=0.55))
    # de-dupe by patent_id
    seen: dict[str, PatentHit] = {}
    for h in hits:
        if h.patent_id in seen and seen[h.patent_id].similarity >= h.similarity:
            continue
        seen[h.patent_id] = h
    live_hits = [h for h in seen.values() if h.is_live]
    if not live_hits:
        return 0.0, list(seen.values())

    target = route.get("target", "")
    risks: list[float] = []
    for h in live_hits:
        if claim_classifier is not None and h.independent_claim_snippet:
            try:
                pred = claim_classifier.predict(target, h.independent_claim_snippet)
                risks.append(max(h.similarity, float(pred.p_covered)))
            except Exception:
                risks.append(h.similarity)
        else:
            risks.append(h.similarity)
    top = max(risks) if risks else 0.0
    return round(min(1.0, top), 3), list(seen.values())


def score_one_route(route: dict[str, Any], target_smiles: str, *,
                    corpus: PatentCorpus | None = None,
                    weights: Weights = DEFAULT_WEIGHTS,
                    market: str = "IN+US+EU",
                    claim_classifier=None) -> dict[str, Any]:
    corpus = corpus or SyntheticCorpus()
    feasibility = round(float(route.get("feasibility", {}).get("score", 0.0)), 3)
    yield_est = _yield_estimate(route)
    impurity_risk = _impurity_risk(route)
    scalability = _scalability_score(route)
    regulatory = _regulatory_score(route, market)
    fto_risk, hits = _fto_risk(route, corpus, claim_classifier=claim_classifier)
    cost = _starting_material_cost(route.get("starting_materials", []))

    scores = {
        "feasibility": feasibility,
        "fto_risk": fto_risk,                # 0 = clean, 1 = high overlap with live patents
        "yield_est": yield_est,
        "impurity_risk": impurity_risk,      # 0 = clean
        "scalability": scalability,
        "regulatory": regulatory,
        "cost_usd_per_kg": cost,
    }

    # Composite: higher is better. Note FTO and impurity are RISKS, so they enter inverted.
    w = weights
    composite = (
        w.feasibility * feasibility
        + w.fto * (1 - fto_risk)
        + w.yield_ * yield_est
        + w.impurity * (1 - impurity_risk)
        + w.scalability * scalability
        + w.regulatory * regulatory
    )
    composite = round(composite / max(0.0001, w.feasibility + w.fto + w.yield_
                                       + w.impurity + w.scalability + w.regulatory), 3)

    enriched = dict(route)
    enriched["scores"] = scores
    enriched["composite"] = composite
    enriched["patent_hits"] = [
        {"patent_id": h.patent_id, "jurisdiction": h.jurisdiction,
         "similarity": h.similarity, "title": h.title,
         "assignee": h.assignee, "expiry": h.expiry.isoformat() if h.expiry else None,
         "is_live": h.is_live, "claim_snippet": h.independent_claim_snippet}
        for h in hits
    ]
    return enriched


def pareto_front(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return routes on the Pareto front (no other route dominates them)."""
    def dominates(a: dict, b: dict) -> bool:
        sa, sb = a["scores"], b["scores"]
        better_on = 0
        worse_on = 0
        for k, sign in [("feasibility", +1), ("fto_risk", -1), ("yield_est", +1),
                        ("impurity_risk", -1), ("scalability", +1), ("regulatory", +1)]:
            da = sa[k] * sign
            db = sb[k] * sign
            if da > db:
                better_on += 1
            elif da < db:
                worse_on += 1
        return better_on > 0 and worse_on == 0

    front = []
    for i, r in enumerate(routes):
        if not any(dominates(other, r) for j, other in enumerate(routes) if i != j):
            front.append(r)
    return front


def score_routes(routes: list[dict[str, Any]], target_smiles: str, *,
                 corpus: PatentCorpus | None = None,
                 weights: Weights = DEFAULT_WEIGHTS,
                 regulatory_market: str = "IN+US+EU",
                 claim_classifier=None) -> list[dict[str, Any]]:
    corpus = corpus or SyntheticCorpus()
    scored = [score_one_route(r, target_smiles, corpus=corpus, weights=weights,
                               market=regulatory_market,
                               claim_classifier=claim_classifier) for r in routes]
    # Pareto front first, then composite
    front_ids = {id(r) for r in pareto_front(scored)}
    scored.sort(key=lambda r: (id(r) not in front_ids, -r["composite"]))
    return scored
