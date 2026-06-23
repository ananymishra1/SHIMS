"""
Symbolic-neural chemistry grounding.

Every molecule, every reaction, every condition the LLMs emit passes through
this layer. The contract is binary: either a claim survives every applicable
check, or it is rejected with a structured reason the calling brain must show.

Tools (each exposed as a Python callable AND as an MCP-style JSON tool):
  - validate_smiles
  - sanitize_molecule
  - check_reaction_balance
  - atom_map_reaction
  - flag_hazards
  - check_ich_q3_impurity
  - estimate_thermodynamics
  - score_route_feasibility

All return ToolResult — never raise — so the agent can reason about failures.
"""
from .types import ToolResult, VerifierError, MoleculeReport, ReactionReport
from .smiles import validate_smiles, sanitize_molecule, canonical_smiles, mol_weight
from .reactions import check_reaction_balance, atom_map_reaction
from .hazards import flag_hazards, HAZARD_RULES
from .ich import check_ich_q3_impurity, ICH_Q3_THRESHOLDS
from .thermo import estimate_thermodynamics
from .feasibility import score_route_feasibility
from .registry import list_tools, get_tool, run_tool

__all__ = [
    "ToolResult", "VerifierError", "MoleculeReport", "ReactionReport",
    "validate_smiles", "sanitize_molecule", "canonical_smiles", "mol_weight",
    "check_reaction_balance", "atom_map_reaction",
    "flag_hazards", "HAZARD_RULES",
    "check_ich_q3_impurity", "ICH_Q3_THRESHOLDS",
    "estimate_thermodynamics",
    "score_route_feasibility",
    "list_tools", "get_tool", "run_tool",
]
