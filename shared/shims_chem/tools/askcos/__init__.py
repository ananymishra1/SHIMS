"""
ASKCOS adapter.

ASKCOS exposes a tree-search retrosynthesis service over HTTP. We talk to it
through its `/api/v2/treebuilder/` endpoint. Run your own ASKCOS server on
the LAN (Docker compose is documented in the ASKCOS repo), set the URL, and
the engine becomes available.

Configuration:
    SHIMS_ASKCOS_URL    = http://askcos.local           (required)
    SHIMS_ASKCOS_TOKEN  = <bearer if you set one>       (optional)

Like the AiZynthFinder adapter, this slots behind the same RouteEngine
interface — `engine="askcos"` is the switch.
"""
from __future__ import annotations
import os
from typing import Any

from ...retrosynthesis.planner import RouteEngine, register_engine


class ASKCOSEngine(RouteEngine):
    name = "askcos"

    def __init__(self, url: str | None = None, token: str | None = None,
                 expansion_time_s: int = 30) -> None:
        self.url = (url or os.environ.get("SHIMS_ASKCOS_URL", "")).rstrip("/")
        self.token = token or os.environ.get("SHIMS_ASKCOS_TOKEN")
        self.expansion_time_s = expansion_time_s

    def plan(self, target_smiles: str, max_routes: int) -> list[dict[str, Any]]:
        if not self.url:
            raise RuntimeError(
                "ASKCOS URL not configured. Set SHIMS_ASKCOS_URL to point at your server."
            )
        try:
            import httpx
        except ImportError as e:                                     # pragma: no cover
            raise RuntimeError("httpx not installed; pip install httpx") from e

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        body = {
            "smiles": target_smiles,
            "max_depth": 8,
            "max_branching": 25,
            "expansion_time": self.expansion_time_s,
            "max_trees": max_routes,
            "return_first": False,
        }
        with httpx.Client(timeout=120.0) as c:
            r = c.post(f"{self.url}/api/v2/treebuilder/", json=body, headers=headers)
            r.raise_for_status()
            data = r.json()

        trees = data.get("trees") or data.get("paths") or []
        out: list[dict[str, Any]] = []
        for tree in trees[:max_routes]:
            steps, sm = _walk_tree(tree)
            out.append({
                "target": target_smiles,
                "steps": steps,
                "intermediates": [],
                "starting_materials": sm,
                "provenance": {"engine": "askcos", "askcos_score": tree.get("score", 0.0)},
            })
        return out


def _walk_tree(tree: dict, steps: list[str] | None = None,
               sm: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Convert ASKCOS's nested tree into ordered reaction-SMILES steps."""
    steps = [] if steps is None else steps
    sm = [] if sm is None else sm
    children = tree.get("children") or []
    if not children:
        # Leaf — buyable molecule
        if tree.get("smiles"):
            sm.append(tree["smiles"])
        return steps, sm
    # Children are reactions
    for rxn in children:
        precursors = rxn.get("children") or []
        precursor_smiles = ".".join(c["smiles"] for c in precursors if c.get("smiles"))
        if precursor_smiles and tree.get("smiles"):
            steps.append(f"{precursor_smiles}>>{tree['smiles']}")
        for c in precursors:
            _walk_tree(c, steps, sm)
    return steps, sm


def register_askcos(url: str | None = None, token: str | None = None) -> None:
    """Call once at process startup to make ASKCOS available."""
    register_engine(ASKCOSEngine(url=url, token=token))
