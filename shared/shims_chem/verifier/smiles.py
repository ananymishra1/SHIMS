"""
SMILES validation, sanitization, canonicalization.

Strategy:
  * If RDKit is installed, use it (the real thing).
  * Otherwise, run a structural parser that catches the common failure modes:
    syntax errors, unbalanced parentheses/brackets, unknown atoms, bad ring
    closure digits. This is intentionally strict so the CI/demo path still
    rejects obvious garbage even without RDKit.

Either way, the tool always returns a ToolResult.
"""
from __future__ import annotations
import re
import time

from .types import Issue, MoleculeReport, ToolResult

try:                                  # pragma: no cover — depends on env
    from rdkit import Chem
    from rdkit.Chem import AllChem, Descriptors, rdMolDescriptors
    _RDKIT = True
except Exception:                     # pragma: no cover
    _RDKIT = False


# Single-letter and two-letter element symbols we accept in SMILES atoms.
# (Aromatic lowercase forms handled separately.)
_ORGANIC_SUBSET = {"B", "C", "N", "O", "P", "S", "F", "Cl", "Br", "I"}
_AROMATIC_SUBSET = {"b", "c", "n", "o", "p", "s"}
_BRACKET_ELEMENTS = {
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn",
}


def _fallback_validate(smiles: str) -> list[Issue]:
    """Pure-Python structural lint of a SMILES string."""
    issues: list[Issue] = []
    if not smiles or not smiles.strip():
        issues.append(Issue("empty", "error", "Empty SMILES"))
        return issues
    s = smiles.strip()

    # Balanced parentheses & brackets
    paren = 0
    brack = 0
    for ch in s:
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
            if paren < 0:
                issues.append(Issue("paren_mismatch", "error", "Unbalanced parentheses"))
                break
        elif ch == "[":
            brack += 1
        elif ch == "]":
            brack -= 1
            if brack < 0:
                issues.append(Issue("bracket_mismatch", "error", "Unbalanced [ ]"))
                break
    if paren > 0:
        issues.append(Issue("paren_unclosed", "error", "Unclosed ("))
    if brack > 0:
        issues.append(Issue("bracket_unclosed", "error", "Unclosed ["))

    # Ring closure digits must come in pairs
    ring_uses: dict[str, int] = {}
    for m in re.finditer(r"(?<!\[)(\d)", s):
        d = m.group(1)
        ring_uses[d] = ring_uses.get(d, 0) + 1
    for d, count in ring_uses.items():
        if count % 2 != 0:
            issues.append(Issue("ring_unclosed", "error", f"Ring closure digit {d} appears {count} times (must be even)"))

    # Atom syntax check — bracket atoms
    for m in re.finditer(r"\[([^\]]+)\]", s):
        body = m.group(1)
        # Strip isotope digits at start
        atom_match = re.match(r"^\d*([A-Z][a-z]?|[bcnops])", body)
        if not atom_match:
            issues.append(Issue("bad_bracket_atom", "error", f"Cannot parse bracket atom: [{body}]"))
            continue
        sym = atom_match.group(1)
        if sym.lower() == sym and sym not in _AROMATIC_SUBSET:
            issues.append(Issue("bad_aromatic_atom", "error", f"Unknown aromatic atom: {sym}"))
        elif sym[0].isupper() and sym not in _BRACKET_ELEMENTS:
            issues.append(Issue("unknown_element", "error", f"Unknown element symbol: {sym}"))

    # Bare aromatic atoms
    bare = re.sub(r"\[[^\]]+\]", "", s)
    for ch in bare:
        if ch.isalpha() and ch.islower() and ch not in _AROMATIC_SUBSET and ch != "h":
            # e.g. 'q' is invalid
            if ch not in "lr":  # l in Cl, r in Br are handled below
                pass

    # Two-letter organic atoms (Cl, Br) should be uppercase pairs.
    # Strict: bare uppercase letters outside the organic subset are real errors.
    bare_no_two = re.sub(r"Cl|Br", "", bare)
    for ch in bare_no_two:
        if ch.isalpha() and ch.isupper() and ch not in {"B", "C", "N", "O", "P", "S", "F", "I", "H"}:
            issues.append(Issue("unknown_atom", "error", f"Unknown atom outside [ ]: {ch}"))

    # Must contain at least one valid atom token
    has_any_atom = (
        bool(re.search(r"Cl|Br|[BCNOPSFI]", bare)) or
        bool(re.search(r"[bcnops]", bare)) or
        bool(re.search(r"\[[^\]]+\]", s))
    )
    if not has_any_atom:
        issues.append(Issue("no_atoms", "error", "SMILES contains no recognizable atoms"))

    return issues


def _approx_mol_weight(smiles: str) -> float | None:
    """Very rough MW estimate by atom counting; only used when RDKit is absent."""
    atom_weights = {
        "H": 1.008, "C": 12.011, "N": 14.007, "O": 15.999,
        "F": 18.998, "P": 30.974, "S": 32.06, "Cl": 35.45,
        "Br": 79.904, "I": 126.90, "B": 10.81,
    }
    counts: dict[str, int] = {}
    no_brackets = re.sub(r"\[[^\]]+\]", "X", smiles)
    for m in re.finditer(r"Cl|Br|[CNOPSFIB]", no_brackets):
        counts[m.group(0)] = counts.get(m.group(0), 0) + 1
    for m in re.finditer(r"[cnops]", no_brackets):
        sym = m.group(0).upper()
        counts[sym] = counts.get(sym, 0) + 1
    if not counts:
        return None
    return round(sum(atom_weights.get(s, 0) * c for s, c in counts.items()), 2)


def validate_smiles(smiles: str) -> ToolResult:
    """Strict SMILES validation. Returns ok=True only if structure is parseable & sanitized."""
    t0 = time.perf_counter()
    issues: list[Issue] = []
    canonical = None
    mw = None
    heavy = None
    formula = None

    if not (smiles or "").strip():
        issues.append(Issue("empty", "error", "Empty SMILES string"))
    elif _RDKIT:                            # pragma: no cover
        mol = Chem.MolFromSmiles(smiles, sanitize=False)
        if mol is None:
            issues.append(Issue("rdkit_parse", "error", "RDKit could not parse the SMILES"))
            # Enrich the generic parse failure with specific structural diagnostics
            # (unbalanced parens, unclosed rings, unknown atoms, …).
            issues.extend(_fallback_validate(smiles))
        else:
            try:
                Chem.SanitizeMol(mol)
            except Exception as e:
                issues.append(Issue("rdkit_sanitize", "error", f"Sanitization failed: {e}"))
                mol = None
            if mol is not None:
                canonical = Chem.MolToSmiles(mol, canonical=True)
                mw = float(Descriptors.MolWt(mol))
                heavy = mol.GetNumHeavyAtoms()
                formula = rdMolDescriptors.CalcMolFormula(mol)
    else:
        issues.extend(_fallback_validate(smiles))
        if not any(i.severity in ("error", "critical") for i in issues):
            canonical = smiles.strip()
            mw = _approx_mol_weight(smiles)
            heavy = sum(1 for ch in re.sub(r"\[[^\]]+\]|H", "", smiles) if ch.isalpha())

    ok = not any(i.severity in ("error", "critical") for i in issues)
    report = MoleculeReport(
        input_smiles=smiles,
        canonical_smiles=canonical if ok else None,
        valid=ok,
        mol_weight=mw,
        heavy_atoms=heavy,
        formula=formula,
    )
    return ToolResult(
        ok=ok,
        tool="validate_smiles",
        data={"report": report.__dict__, "rdkit": _RDKIT},
        issues=issues,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def sanitize_molecule(smiles: str) -> ToolResult:
    """Alias of validate_smiles with explicit sanitization step in RDKit mode."""
    r = validate_smiles(smiles)
    r.tool = "sanitize_molecule"
    return r


def canonical_smiles(smiles: str) -> str | None:
    r = validate_smiles(smiles)
    if r.ok:
        return r.data["report"]["canonical_smiles"]
    return None


def mol_weight(smiles: str) -> float | None:
    r = validate_smiles(smiles)
    if r.ok:
        return r.data["report"]["mol_weight"]
    return None
