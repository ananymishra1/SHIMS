"""
Retrosynthesis planning.

In production: drop in AiZynthFinder 4.0 (preferred) or ASKCOS. This module
defines the interface they slot into and provides a built-in template-walker
that generates plausible (deliberately stylized) routes from a tiny library of
disconnection templates. The walker keeps the entire pipeline runnable offline
so the orchestrator, verifier, and FTO scorer can be exercised end-to-end on
any developer machine.

Interface:
    plan_retrosynthesis(target_smiles, max_routes=10) -> list[Route]
where each Route is a dict:
    {
      "route_id": str,
      "target": str,                # canonical SMILES
      "steps": [rxn_smiles, ...],   # ordered, forward direction
      "intermediates": [smiles, ...],
      "starting_materials": [smiles, ...],
      "n_steps": int,
      "provenance": {"engine": "...", "templates_used": [...]},
    }
"""
from .planner import plan_retrosynthesis, RouteEngine, register_engine

__all__ = ["plan_retrosynthesis", "RouteEngine", "register_engine"]
