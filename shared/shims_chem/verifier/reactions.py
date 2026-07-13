"""
Reaction-level checks.

  check_reaction_balance — counts heavy-atom formulas on each side; flags any
    atom whose count differs. This catches the "LLM forgot a reagent" case
    that RXNMapper alone would not catch.
  atom_map_reaction      — wraps RXNMapper when available; in fallback mode
    returns a heuristic confidence based on balance + complexity.

Input format: standard reaction SMILES "A.B>>C" or "A.B>cat>C".
"""
from __future__ import annotations
import re
import time

from .types import Issue, ReactionReport, ToolResult
from .smiles import _RDKIT, _approx_mol_weight, validate_smiles

try:                                  # pragma: no cover
    from rdkit import Chem
except Exception:                     # pragma: no cover
    Chem = None  # type: ignore

try:                                  # pragma: no cover
    from rxnmapper import RXNMapper
    _RXN_MAPPER: RXNMapper | None = RXNMapper()
except Exception:                     # pragma: no cover
    _RXN_MAPPER = None


def _split_reaction(rxn_smiles: str) -> tuple[list[str], list[str], list[str]]:
    """Split 'A.B>cat>C.D' into ([A,B], [cat], [C,D])."""
    parts = rxn_smiles.split(">")
    if len(parts) == 2:
        reactants_s, products_s = parts
        agents_s = ""
    elif len(parts) == 3:
        reactants_s, agents_s, products_s = parts
    else:
        return [], [], []
    def _split(s: str) -> list[str]:
        return [x for x in s.split(".") if x]
    return _split(reactants_s), _split(agents_s), _split(products_s)


def _atom_counts(smiles: str) -> dict[str, int]:
    """Count atoms in a SMILES string (heavy atoms only, ignoring implicit H)."""
    if _RDKIT and Chem is not None:    # pragma: no cover
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return {}
        counts: dict[str, int] = {}
        for atom in mol.GetAtoms():
            sym = atom.GetSymbol()
            counts[sym] = counts.get(sym, 0) + 1
        return counts
    # Fallback regex
    counts: dict[str, int] = {}
    no_brackets = re.sub(r"\[([^\]]+)\]", "", smiles)
    # Bracket atoms
    for m in re.finditer(r"\[([^\]]+)\]", smiles):
        body = m.group(1)
        am = re.match(r"^\d*([A-Z][a-z]?|[bcnops])", body)
        if am:
            sym = am.group(1)
            if sym[0].islower():
                sym = sym.upper()
            counts[sym] = counts.get(sym, 0) + 1
    # Two-letter organics first to avoid double counting
    for m in re.finditer(r"Cl|Br", no_brackets):
        counts[m.group(0)] = counts.get(m.group(0), 0) + 1
    rest = re.sub(r"Cl|Br", "", no_brackets)
    for ch in rest:
        if ch.isalpha() and ch.upper() in {"C", "N", "O", "P", "S", "F", "I", "B"}:
            sym = ch.upper()
            counts[sym] = counts.get(sym, 0) + 1
    return counts


def check_reaction_balance(rxn_smiles: str) -> ToolResult:
    """Atom-balance check for a reaction SMILES."""
    t0 = time.perf_counter()
    reactants, agents, products = _split_reaction(rxn_smiles)
    issues: list[Issue] = []
    if not reactants or not products:
        issues.append(Issue("rxn_parse", "error", "Could not split reaction SMILES into reactants and products"))
        return ToolResult(
            ok=False, tool="check_reaction_balance",
            data={"reactants": reactants, "products": products},
            issues=issues, elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    # Validate each side
    for s in [*reactants, *products]:
        v = validate_smiles(s)
        if not v.ok:
            issues.append(Issue("invalid_component", "error",
                                f"Invalid SMILES component: {s}",
                                detail={"component_issues": [i.__dict__ for i in v.issues]}))

    # Balance
    left: dict[str, int] = {}
    right: dict[str, int] = {}
    for s in reactants:
        for sym, n in _atom_counts(s).items():
            left[sym] = left.get(sym, 0) + n
    for s in products:
        for sym, n in _atom_counts(s).items():
            right[sym] = right.get(sym, 0) + n

    all_syms = set(left) | set(right)
    missing: dict[str, int] = {}
    for sym in all_syms:
        diff = left.get(sym, 0) - right.get(sym, 0)
        if diff != 0:
            missing[sym] = diff
    balanced = len(missing) == 0
    if not balanced:
        issues.append(Issue("unbalanced", "warn",
                            f"Reaction is unbalanced (left-right): {missing}",
                            detail={"missing": missing}))

    report = ReactionReport(
        reaction_smiles=rxn_smiles,
        balanced=balanced,
        atom_map_confidence=None,
        reactants=reactants,
        products=products,
        missing_atoms=missing,
    )
    return ToolResult(
        ok=not any(i.severity in ("error", "critical") for i in issues),
        tool="check_reaction_balance",
        data={"report": report.__dict__, "agents": agents, "rdkit": _RDKIT},
        issues=issues,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def atom_map_reaction(rxn_smiles: str) -> ToolResult:
    """Atom-map a reaction and return a confidence score (0..1)."""
    t0 = time.perf_counter()
    issues: list[Issue] = []
    mapped: str | None = None
    confidence: float | None = None

    if _RXN_MAPPER is not None:                # pragma: no cover
        try:
            res = _RXN_MAPPER.get_attention_guided_atom_maps([rxn_smiles])
            if res:
                mapped = res[0]["mapped_rxn"]
                confidence = float(res[0].get("confidence", 0.0))
        except Exception as e:
            issues.append(Issue("rxnmapper_failed", "warn", f"RXNMapper failed: {e}"))

    if confidence is None:
        # Fallback: confidence = 1.0 if balanced and all components valid, scaled down by complexity
        bal = check_reaction_balance(rxn_smiles)
        if not bal.ok:
            confidence = 0.0
        else:
            balanced = bal.data["report"]["balanced"]
            complexity = sum(len(s) for s in bal.data["report"]["reactants"] + bal.data["report"]["products"])
            confidence = (0.9 if balanced else 0.3) * max(0.4, 1.0 - complexity / 400)
            confidence = round(min(1.0, max(0.0, confidence)), 3)
            mapped = rxn_smiles
            issues.append(Issue("fallback_atom_map", "info",
                                "RXNMapper not available; using balance-based heuristic confidence"))

    if confidence is not None and confidence < 0.4:
        issues.append(Issue("low_atom_map_confidence", "warn",
                            f"Atom-mapping confidence is low: {confidence:.2f} (RXNMapper paper considers <0.4 unreliable)"))

    return ToolResult(
        ok=confidence is not None and confidence >= 0.2,
        tool="atom_map_reaction",
        data={"mapped_rxn": mapped, "confidence": confidence, "rxnmapper": _RXN_MAPPER is not None},
        issues=issues,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )
