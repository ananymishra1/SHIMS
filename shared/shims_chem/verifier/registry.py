"""
The single registry of every verifier tool the brains can call.

Each tool is described with a JSON-schema-shaped signature so we can hand the
list to any OpenAI-compatible tool-using LLM (function-calling) without
modification. The same registry powers the MCP server when we wire it.
"""
from __future__ import annotations
import inspect
from typing import Any, Callable

from .hazards import flag_hazards
from .ich import check_ich_q3_impurity
from .reactions import atom_map_reaction, check_reaction_balance
from .smiles import sanitize_molecule, validate_smiles
from .thermo import estimate_thermodynamics
from .types import ToolResult, VerifierError
from .feasibility import score_route_feasibility


def _schema(fn: Callable[..., Any], description: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


_TOOLS: dict[str, tuple[Callable[..., ToolResult], dict]] = {
    "validate_smiles": (
        validate_smiles,
        _schema(validate_smiles,
                "Validate and canonicalize a SMILES string. Returns molecular weight, formula, heavy atom count, and any structural errors.",
                {"smiles": {"type": "string", "description": "SMILES string to validate"}},
                ["smiles"]),
    ),
    "sanitize_molecule": (
        sanitize_molecule,
        _schema(sanitize_molecule, "Alias of validate_smiles with explicit sanitization.",
                {"smiles": {"type": "string"}}, ["smiles"]),
    ),
    "check_reaction_balance": (
        check_reaction_balance,
        _schema(check_reaction_balance,
                "Check that a reaction SMILES (A.B>>C) has balanced heavy atoms on both sides.",
                {"rxn_smiles": {"type": "string", "description": "Reaction SMILES 'A.B>>C' or 'A.B>cat>C'"}},
                ["rxn_smiles"]),
    ),
    "atom_map_reaction": (
        atom_map_reaction,
        _schema(atom_map_reaction,
                "Atom-map a reaction SMILES and return a confidence score (0..1). Confidence <0.4 means the proposed transformation is implausible.",
                {"rxn_smiles": {"type": "string"}}, ["rxn_smiles"]),
    ),
    "flag_hazards": (
        flag_hazards,
        _schema(flag_hazards,
                "Screen a molecule against the hazard rule set: explosophores, peroxide-formers, pyrophorics, highly toxic moieties, strong oxidizers.",
                {"smiles": {"type": "string"}}, ["smiles"]),
    ),
    "check_ich_q3_impurity": (
        check_ich_q3_impurity,
        _schema(check_ich_q3_impurity,
                "Apply ICH Q3A/Q3C/Q3D thresholds to an impurity. Returns reporting/identification/qualification verdict.",
                {
                    "impurity_pct": {"type": "number", "description": "Impurity level in %"},
                    "max_daily_dose_g": {"type": "number", "description": "Max daily dose of drug substance in g", "default": 1.0},
                    "impurity_name": {"type": "string", "description": "Name of impurity if known"},
                    "is_solvent": {"type": "boolean", "description": "Whether the impurity is a residual solvent"},
                    "elemental_symbol": {"type": "string", "description": "Element symbol if elemental impurity (e.g. 'Pd')"},
                },
                ["impurity_pct"]),
    ),
    "estimate_thermodynamics": (
        estimate_thermodynamics,
        _schema(estimate_thermodynamics,
                "Rough thermodynamic feasibility estimate for a reaction (ΔH, verdict).",
                {"rxn_smiles": {"type": "string"}}, ["rxn_smiles"]),
    ),
    "score_route_feasibility": (
        score_route_feasibility,
        _schema(score_route_feasibility,
                "Score the end-to-end feasibility of a synthetic route (a list of reaction SMILES) using every verifier.",
                {"steps": {"type": "array", "items": {"type": "string"}, "description": "Ordered list of reaction SMILES, one per step"}},
                ["steps"]),
    ),
}


def list_tools() -> list[dict]:
    """JSON-schema list of every tool, suitable for OpenAI-style function calling."""
    return [schema for _fn, schema in _TOOLS.values()]


def get_tool(name: str) -> Callable[..., ToolResult]:
    if name not in _TOOLS:
        raise VerifierError(f"Unknown tool: {name}. Known: {sorted(_TOOLS)}")
    return _TOOLS[name][0]


def run_tool(name: str, **kwargs) -> ToolResult:
    """Safe invocation: filters kwargs to those the function actually accepts."""
    fn = get_tool(name)
    sig = inspect.signature(fn)
    accepted = {k: v for k, v in kwargs.items() if k in sig.parameters}
    return fn(**accepted)
