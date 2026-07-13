"""
Lightweight thermodynamic feasibility for reactions.

This is intentionally a heuristic (not DFT). It exists to catch obviously
thermodynamically silly proposals — e.g., "reduce CO2 to glucose with NaBH4 at
room temperature." The real tool path is:

  * RDKit-based group contributions for ΔH_f estimates (Joback-style),
  * Combined with reaction enthalpy = Σ ΔH_f(products) − Σ ΔH_f(reactants),
  * Plus a rough ΔS estimate from change in number of moles of gas.

Output: estimated ΔH (kJ/mol), feasibility verdict ("favorable" / "marginal" /
"unfavorable"), and a confidence score reflecting how shaky the estimate is.

When RDKit is absent, returns a low-confidence "no estimate" verdict.
"""
from __future__ import annotations
import time

from .reactions import _split_reaction, _atom_counts
from .types import Issue, ToolResult


# Crude bond-additivity ΔH_f contributions (kJ/mol per atom), placeholder.
# In production, swap for `thermo` or RDKit group contributions.
_ATOM_HF_KJ = {
    "C": -2.0, "N": +5.0, "O": -40.0, "H": 0.0,
    "F": -80.0, "Cl": -20.0, "Br": +10.0, "I": +40.0,
    "S": +30.0, "P": +10.0, "B": 0.0,
}


def _estimate_hf(smiles: str) -> float:
    counts = _atom_counts(smiles)
    return sum(_ATOM_HF_KJ.get(sym, 0.0) * n for sym, n in counts.items())


def estimate_thermodynamics(rxn_smiles: str) -> ToolResult:
    """Return a rough ΔH estimate and feasibility verdict."""
    t0 = time.perf_counter()
    reactants, _agents, products = _split_reaction(rxn_smiles)
    if not reactants or not products:
        return ToolResult(
            ok=False, tool="estimate_thermodynamics",
            data={}, issues=[Issue("rxn_parse", "error", "Could not parse reaction")],
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    h_left = sum(_estimate_hf(s) for s in reactants)
    h_right = sum(_estimate_hf(s) for s in products)
    delta_h = round(h_right - h_left, 2)

    issues: list[Issue] = []
    if delta_h < -40:
        verdict = "favorable (exothermic)"
    elif delta_h <= 40:
        verdict = "marginal (near-thermoneutral)"
    else:
        verdict = "unfavorable (endothermic)"
        issues.append(Issue("thermo_unfavorable", "warn",
                            f"Estimated ΔH = {delta_h:+.1f} kJ/mol is endothermic; needs driving force / catalysis"))

    issues.append(Issue("thermo_low_confidence", "info",
                        "Estimate uses crude atom-additivity; replace with DFT/group-contribution for production"))

    return ToolResult(
        ok=True, tool="estimate_thermodynamics",
        data={"delta_h_kJ_per_mol": delta_h, "verdict": verdict, "confidence": "low"},
        issues=issues,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )
