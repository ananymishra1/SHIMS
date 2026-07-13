"""
Patent-aware multi-objective Freedom-to-Operate (FTO) scoring.

For each candidate route, computes scores on:
  • fto_risk          — IP infringement risk via patent similarity search
  • cost              — material cost from a local catalog
  • yield_est         — expected overall yield (heuristic from per-step)
  • impurity_risk     — risk of carrying impurity through (uses verifier)
  • scalability       — kg-to-tonne feasibility (hazards + step count + temperatures)
  • regulatory        — regulatory feasibility (Schedule M / ICH-Q7 friendliness)
  • composite         — single number for ranking (configurable weights)
and returns the routes Pareto-sorted + composite-ranked.

Patent corpus interface (`corpus.PatentCorpus`) is pluggable: bundled
SyntheticCorpus runs offline; production swaps in SureChEMBL / USPTO bulk.
"""
from .scoring import score_routes, score_one_route, Weights, DEFAULT_WEIGHTS
from .corpus import PatentCorpus, SyntheticCorpus, PatentHit

__all__ = [
    "score_routes", "score_one_route",
    "Weights", "DEFAULT_WEIGHTS",
    "PatentCorpus", "SyntheticCorpus", "PatentHit",
]
