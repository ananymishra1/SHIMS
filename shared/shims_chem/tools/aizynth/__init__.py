"""
AiZynthFinder adapter.

Plugs into the existing `RouteEngine` interface so `plan_retrosynthesis(...,
engine="aizynthfinder")` works once the user has installed it and supplied
configuration.

Installation (on the user's box, not in this scaffold):
    pip install aizynthfinder
    download_public_data .

Configuration via env:
    SHIMS_AIZYNTH_CONFIG = /path/to/config.yml   (required)
    SHIMS_AIZYNTH_STOCK  = stock|zinc|...        (optional, named in the config)
    SHIMS_AIZYNTH_POLICY = uspto|... etc         (optional, named in the config)

Registration: call `register_aizynthfinder()` once at startup; from then on
`plan_retrosynthesis(..., engine="aizynthfinder")` (or `engine="auto"`)
returns its routes in the same Route shape as the template walker.
"""
from __future__ import annotations
import os
from typing import Any

from ..retrosynthesis.planner import RouteEngine, register_engine


class AiZynthFinderEngine(RouteEngine):
    name = "aizynthfinder"

    def __init__(self, config_path: str | None = None,
                 stock: str | None = None, policy: str | None = None) -> None:
        self.config_path = config_path or os.environ.get("SHIMS_AIZYNTH_CONFIG")
        self.stock = stock or os.environ.get("SHIMS_AIZYNTH_STOCK")
        self.policy = policy or os.environ.get("SHIMS_AIZYNTH_POLICY")
        self._finder = None      # lazy

    def _ensure_loaded(self) -> None:
        if self._finder is not None:
            return
        if not self.config_path:
            raise RuntimeError(
                "AiZynthFinder config not configured. Set SHIMS_AIZYNTH_CONFIG to point at your config.yml."
            )
        try:
            from aizynthfinder.aizynthfinder import AiZynthFinder    # type: ignore
        except Exception as e:                                       # pragma: no cover
            raise RuntimeError(
                "aizynthfinder not installed. pip install aizynthfinder + download_public_data."
            ) from e
        self._finder = AiZynthFinder(configfile=self.config_path)
        if self.stock:
            self._finder.stock.select(self.stock)
        if self.policy:
            self._finder.expansion_policy.select(self.policy)

    def plan(self, target_smiles: str, max_routes: int) -> list[dict[str, Any]]:
        self._ensure_loaded()
        assert self._finder is not None
        self._finder.target_smiles = target_smiles
        self._finder.tree_search()
        self._finder.build_routes()
        out: list[dict[str, Any]] = []
        for i, r in enumerate(self._finder.routes[:max_routes]):
            steps = [rxn.reaction_smiles() for rxn in r.reactions()]
            sm = [m.smiles for m in r.leafs() if m.in_stock]
            out.append({
                "target": target_smiles,
                "steps": steps,
                "intermediates": [m.smiles for m in r.molecules() if not m.in_stock and m.smiles != target_smiles],
                "starting_materials": sm,
                "provenance": {
                    "engine": "aizynthfinder",
                    "in_stock_count": sum(1 for m in r.leafs() if m.in_stock),
                    "score": float(getattr(r, "score", 0.0)),
                    "templates_used": [rxn.metadata.get("template_hash", "") for rxn in r.reactions()],
                },
            })
        return out


def register_aizynthfinder(config_path: str | None = None,
                            stock: str | None = None,
                            policy: str | None = None) -> None:
    """Call once at process startup to make AiZynthFinder available."""
    register_engine(AiZynthFinderEngine(config_path=config_path, stock=stock, policy=policy))
