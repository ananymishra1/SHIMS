"""Pluggable retrosynthesis planner."""
from __future__ import annotations
import hashlib
import re
from abc import ABC, abstractmethod
from typing import Any

from ..verifier import canonical_smiles, validate_smiles


class RouteEngine(ABC):
    name: str = "abstract"

    @abstractmethod
    def plan(self, target_smiles: str, max_routes: int) -> list[dict[str, Any]]: ...


_ENGINES: dict[str, RouteEngine] = {}


def register_engine(engine: RouteEngine) -> None:
    _ENGINES[engine.name] = engine


def plan_retrosynthesis(target_smiles: str, max_routes: int = 10,
                        engine: str = "auto") -> list[dict[str, Any]]:
    """Top-level entry. Validates target, picks engine, returns routes."""
    v = validate_smiles(target_smiles)
    if not v.ok:
        return []

    canon = v.data["report"]["canonical_smiles"] or target_smiles

    if engine == "auto":
        # Prefer AiZynthFinder if registered; else template walker.
        for cand in ("aizynthfinder", "askcos", "template_walker"):
            if cand in _ENGINES:
                eng = _ENGINES[cand]
                break
        else:
            eng = _TemplateWalker()
    else:
        eng = _ENGINES.get(engine) or _TemplateWalker()

    routes = eng.plan(canon, max_routes=max_routes)
    # Stable IDs
    for i, r in enumerate(routes, 1):
        if "route_id" not in r:
            h = hashlib.sha1((canon + "|".join(r["steps"])).encode()).hexdigest()[:8]
            r["route_id"] = f"R{i}-{h}"
        r.setdefault("target", canon)
        r.setdefault("n_steps", len(r["steps"]))
    return routes


# ----- Built-in template walker ---------------------------------------------

# Tiny, intentionally simple template library. Each template:
#   (name, target_smarts_or_substring, retro_step_template)
# where retro_step_template is a callable producing a list of (precursors, rxn_smiles).
#
# These are deliberately illustrative — meant to give the orchestrator
# realistic-shaped routes to score, NOT to compete with AiZynthFinder.

_AMIDE_RE = re.compile(r"C\(=O\)N|NC\(=O\)")
_ESTER_RE = re.compile(r"C\(=O\)O[A-Za-z]")
_AROMATIC_RE = re.compile(r"c1cc[cn]c|c1ccccc1")
_BENZYL_RE = re.compile(r"Cc1ccccc1|c1ccccc1C")


class _TemplateWalker(RouteEngine):
    name = "template_walker"

    def plan(self, target_smiles: str, max_routes: int) -> list[dict[str, Any]]:
        routes: list[dict[str, Any]] = []
        target = target_smiles

        # Strategy A — Amide disconnection: split at C(=O)N into acid + amine
        if _AMIDE_RE.search(target):
            acid, amine = self._disconnect_amide(target)
            steps = [f"{acid}.{amine}>>{target}"]
            routes.append(self._make_route(target, steps, [acid, amine], "amide_disconnection"))

            # Two-step: acid from anhydride coupling
            anhydride = self._guess_anhydride(acid)
            if anhydride:
                steps2 = [f"{anhydride}.O>>{acid}", f"{acid}.{amine}>>{target}"]
                routes.append(self._make_route(target, steps2, [anhydride, amine],
                                                "anhydride_hydrolysis_then_amide"))

        # Strategy B — Ester disconnection
        if _ESTER_RE.search(target):
            acid, alcohol = self._disconnect_ester(target)
            steps = [f"{acid}.{alcohol}>>{target}.O"]
            routes.append(self._make_route(target, steps, [acid, alcohol], "ester_fischer"))

        # Strategy C — Aromatic substitution from halide
        if _AROMATIC_RE.search(target):
            halide = self._aryl_halide_surrogate(target)
            steps = [f"{halide}.O>>{target}"]
            routes.append(self._make_route(target, steps, [halide], "aromatic_substitution"))

        # Strategy D — Identity (no disconnection found): mark commercially available
        if not routes:
            routes.append(self._make_route(target, [], [target], "commercially_available"))

        # Trim and rank by inverse step count (fewer steps = better baseline rank)
        routes.sort(key=lambda r: (len(r["steps"]), r["provenance"]["template"]))
        return routes[:max_routes]

    # --- disconnection primitives (intentionally simple) -------------------

    @staticmethod
    def _disconnect_amide(target: str) -> tuple[str, str]:
        m = re.search(r"C\(=O\)N([A-Za-z0-9@\[\]\(\)=#/.]*)", target)
        if not m:
            return "CC(=O)O", "CN"
        # Strip C(=O)N to recover an acid + amine fragment (very approximate)
        left = target[: m.start()] + "C(=O)O"
        right = "N" + m.group(1)
        return left, right

    @staticmethod
    def _disconnect_ester(target: str) -> tuple[str, str]:
        m = re.search(r"C\(=O\)O([A-Za-z0-9@\[\]\(\)=#/.]*)", target)
        if not m:
            return "CC(=O)O", "CO"
        left = target[: m.start()] + "C(=O)O"
        right = m.group(1) + "O" if m.group(1) else "CO"
        return left, right

    @staticmethod
    def _guess_anhydride(acid: str) -> str | None:
        if "C(=O)O" not in acid:
            return None
        return acid.replace("C(=O)O", "C(=O)OC(=O)C", 1) + "C"

    @staticmethod
    def _aryl_halide_surrogate(target: str) -> str:
        # Replace one aromatic OH or NH2 substring with Br as a synthetic surrogate
        for sub, repl in (("c1ccc(O)cc1", "c1ccc(Br)cc1"),
                          ("c1ccccc1O", "c1ccccc1Br"),
                          ("c1ccccc1N", "c1ccccc1Br")):
            if sub in target:
                return target.replace(sub, repl, 1)
        return target.replace("c1ccccc1", "c1ccc(Br)cc1", 1)

    @staticmethod
    def _make_route(target: str, steps: list[str], sm: list[str], template: str) -> dict[str, Any]:
        return {
            "target": target,
            "steps": steps,
            "intermediates": [],
            "starting_materials": sm,
            "provenance": {"engine": "template_walker", "template": template, "templates_used": [template]},
        }
