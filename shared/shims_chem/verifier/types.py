"""Shared types used by every verifier tool."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Literal


Severity = Literal["info", "warn", "error", "critical"]


@dataclass
class Issue:
    code: str
    severity: Severity
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Uniform contract for every verifier tool. Never raise; return this."""
    ok: bool
    tool: str
    data: dict[str, Any] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "data": self.data,
            "issues": [asdict(i) for i in self.issues],
            "elapsed_ms": round(self.elapsed_ms, 3),
        }

    def has_blockers(self) -> bool:
        return any(i.severity in ("error", "critical") for i in self.issues)


@dataclass
class MoleculeReport:
    input_smiles: str
    canonical_smiles: str | None
    valid: bool
    mol_weight: float | None
    heavy_atoms: int | None
    formula: str | None
    hazards: list[Issue] = field(default_factory=list)


@dataclass
class ReactionReport:
    reaction_smiles: str
    balanced: bool
    atom_map_confidence: float | None
    reactants: list[str]
    products: list[str]
    missing_atoms: dict[str, int] = field(default_factory=dict)


class VerifierError(Exception):
    """Raised only on programmer error (bad type, missing tool) — not chemistry failures."""
