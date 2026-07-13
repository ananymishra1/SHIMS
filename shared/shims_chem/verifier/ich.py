"""
ICH Q3A/B/C/D impurity threshold checks.

Q3A (drug substance) reporting/identification/qualification thresholds depend
on max daily dose. We implement the canonical table from ICH Q3A(R2) and
provide a `check_ich_q3_impurity` tool that returns the right verdict for a
given impurity %.

Q3C (residual solvents) classes: 1 (avoid), 2 (limit), 3 (low concern).
Q3D (elemental impurities) PDE values for common metals (catalysts!).
"""
from __future__ import annotations
import time
from dataclasses import dataclass

from .types import Issue, ToolResult


# ICH Q3A(R2) — drug substance thresholds
# Format: (max daily dose, reporting, identification, qualification) — % unless noted
Q3A_TABLE = [
    # max_daily_dose_g_or_less, reporting_pct, identification_pct, qualification_pct
    (2.0,  0.05, 0.10, 0.15),
    (None, 0.03, 0.05, 0.05),    # > 2 g/day
]

# ICH Q3C(R8) — solvent classes (selected; expand from official table as needed)
Q3C_CLASS_1 = {  # to be avoided
    "benzene": 2, "carbon tetrachloride": 4, "1,2-dichloroethane": 5,
    "1,1-dichloroethene": 8, "1,1,1-trichloroethane": 1500,
}
Q3C_CLASS_2 = {  # PDE in mg/day -> derived concentration limits
    "acetonitrile": 4.1, "chlorobenzene": 3.6, "chloroform": 0.6,
    "cyclohexane": 38.8, "1,2-dichloroethene": 18.7, "dichloromethane": 6.0,
    "1,2-dimethoxyethane": 1.0, "n,n-dimethylacetamide": 10.9,
    "n,n-dimethylformamide": 8.8, "1,4-dioxane": 3.8, "2-ethoxyethanol": 1.6,
    "ethyleneglycol": 6.2, "formamide": 2.2, "hexane": 2.9, "methanol": 30.0,
    "2-methoxyethanol": 0.5, "methylbutyl ketone": 0.5, "methylcyclohexane": 11.8,
    "n-methylpyrrolidone": 5.3, "nitromethane": 0.5, "pyridine": 2.0,
    "sulfolane": 1.6, "tetrahydrofuran": 7.2, "tetralin": 1.0, "toluene": 8.9,
    "1,1,2-trichloroethene": 0.8, "xylene": 21.7,
}
Q3C_CLASS_3 = {  # low toxic potential — up to 50 mg/day generally acceptable
    "acetic acid", "acetone", "anisole", "1-butanol", "2-butanol",
    "butyl acetate", "tert-butylmethyl ether", "dimethyl sulfoxide",
    "ethanol", "ethyl acetate", "ethyl ether", "ethyl formate", "formic acid",
    "heptane", "isobutyl acetate", "isopropyl acetate", "methyl acetate",
    "3-methyl-1-butanol", "methylethyl ketone", "methylisobutyl ketone",
    "2-methyl-1-propanol", "pentane", "1-pentanol", "1-propanol",
    "2-propanol", "propyl acetate", "triethylamine",
}

# ICH Q3D(R2) — selected elemental PDEs (oral, μg/day) — relevant catalysts
Q3D_ORAL_PDE_UG = {
    "Pd": 100, "Pt": 100, "Ni": 200, "Rh": 100, "Ru": 100,
    "Ir": 100, "Os": 100, "Cr": 11000, "Mo": 3000, "Cu": 3000,
    "As": 15, "Pb": 5, "Cd": 5, "Hg": 30,
}


ICH_Q3_THRESHOLDS = {
    "Q3A_drug_substance": Q3A_TABLE,
    "Q3C_class1_solvents": Q3C_CLASS_1,
    "Q3C_class2_solvents_pde_mg": Q3C_CLASS_2,
    "Q3C_class3_solvents": sorted(Q3C_CLASS_3),
    "Q3D_oral_PDE_ug": Q3D_ORAL_PDE_UG,
}


def _q3a_thresholds(max_daily_dose_g: float) -> tuple[float, float, float]:
    for cutoff, rep, ide, qual in Q3A_TABLE:
        if cutoff is None or max_daily_dose_g <= cutoff:
            return rep, ide, qual
    return Q3A_TABLE[-1][1:]  # type: ignore[return-value]


def check_ich_q3_impurity(
    impurity_pct: float,
    max_daily_dose_g: float = 1.0,
    impurity_name: str | None = None,
    is_solvent: bool = False,
    elemental_symbol: str | None = None,
) -> ToolResult:
    """Apply ICH Q3 thresholds to an impurity value."""
    t0 = time.perf_counter()
    issues: list[Issue] = []
    data: dict = {"impurity_pct": impurity_pct, "max_daily_dose_g": max_daily_dose_g}

    # Q3A drug-substance
    rep, ide, qual = _q3a_thresholds(max_daily_dose_g)
    data["q3a"] = {"reporting": rep, "identification": ide, "qualification": qual}
    if impurity_pct >= qual:
        issues.append(Issue("Q3A_qualification", "error",
                            f"Impurity {impurity_pct:.3f}% ≥ qualification threshold {qual}% — toxicology data needed",
                            detail={"limits": data["q3a"]}))
    elif impurity_pct >= ide:
        issues.append(Issue("Q3A_identification", "warn",
                            f"Impurity {impurity_pct:.3f}% ≥ identification threshold {ide}% — structural ID required"))
    elif impurity_pct >= rep:
        issues.append(Issue("Q3A_reporting", "info",
                            f"Impurity {impurity_pct:.3f}% ≥ reporting threshold {rep}% — must report in COA"))

    # Q3C class
    if is_solvent and impurity_name:
        name = impurity_name.lower().strip()
        if name in Q3C_CLASS_1:
            issues.append(Issue("Q3C_class1", "error",
                                f"Class 1 solvent '{name}' — must be avoided; ICH limit {Q3C_CLASS_1[name]} ppm"))
        elif name in Q3C_CLASS_2:
            pde = Q3C_CLASS_2[name]
            issues.append(Issue("Q3C_class2", "warn",
                                f"Class 2 solvent '{name}' — PDE {pde} mg/day; calculate concentration limit"))
        elif name in Q3C_CLASS_3:
            issues.append(Issue("Q3C_class3", "info",
                                f"Class 3 solvent '{name}' — low toxic potential; ≤50 mg/day generally acceptable"))

    # Q3D elemental
    if elemental_symbol:
        sym = elemental_symbol.strip()
        if sym in Q3D_ORAL_PDE_UG:
            pde = Q3D_ORAL_PDE_UG[sym]
            issues.append(Issue("Q3D", "info",
                                f"Element {sym} oral PDE = {pde} μg/day (ICH Q3D)"))

    ok = not any(i.severity in ("error", "critical") for i in issues)
    return ToolResult(
        ok=ok, tool="check_ich_q3_impurity",
        data=data, issues=issues,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )
