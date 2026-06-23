"""
Hazard pattern flagging.

A curated, conservative rule set. Each rule has a SMARTS pattern (used when
RDKit is available) AND a regex fallback so the layer still flags the worst
offenders without RDKit. This is NOT a replacement for a real SDS / Stoffenmanager
analysis — it's a hard guardrail so the AI can't suggest making TATP without
the system shouting at the user.

Rule sources (curated, conservative):
  * Peroxide-formers: ethers w/ alpha-H, allylic/benzylic ethers, diisopropyl ether.
  * Explosophores: poly-nitro, azide chains, peroxide bonds, fulminate.
  * Pyrophoric: alkyllithium, R3Al, tBuLi, etc.
  * Highly toxic: HCN, phosgene, methyl mercaptan, OPCW Schedule 1 sketches.
  * Strong oxidizers: perchlorate, chlorate, peroxydisulfate.
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass

from .types import Issue, ToolResult

try:                                  # pragma: no cover
    from rdkit import Chem
    _RDKIT = True
except Exception:                     # pragma: no cover
    _RDKIT = False
    Chem = None  # type: ignore


@dataclass
class HazardRule:
    code: str
    name: str
    smarts: str | None             # used with RDKit
    regex: str | None              # fallback structural string match (lowercased)
    severity: str                  # warn|error|critical
    advice: str


HAZARD_RULES: list[HazardRule] = [
    # --- Explosophores ---------------------------------------------------
    HazardRule("EXP_PEROXIDE", "Organic peroxide bond (O-O)",
               smarts="[OX2][OX2]", regex=r"o[-]?o",
               severity="critical",
               advice="Organic peroxides are shock/heat sensitive. Substitute or contain."),
    HazardRule("EXP_TRINITRO", "Three or more nitro groups on one ring",
               smarts="[c,C]([N+](=O)[O-])[c,C][c,C]([N+](=O)[O-])[c,C][c,C][N+](=O)[O-]",
               regex=None,
               severity="critical",
               advice="Poly-nitro aromatics are detonable (e.g., TNT). Strongly avoid as products or impurities."),
    HazardRule("EXP_AZIDE", "Azide group",
               smarts="[N-]=[N+]=[N-]", regex=r"\bn=n=n|n\(=\[n\+\]=\[n-\]\)",
               severity="error",
               advice="Organic azides can be explosive; copper/heavy-metal azides are detonators."),
    HazardRule("EXP_FULMINATE", "Fulminate / isocyanide oxide",
               smarts="[#6]=[N+]=[O-]", regex=r"c=\[n\+\]=\[o-\]",
               severity="critical",
               advice="Fulminates are primary explosives."),
    HazardRule("EXP_PICRATE", "Picric acid / picrate-like (2,4,6-trinitrophenol)",
               smarts="Oc1c([N+](=O)[O-])cc([N+](=O)[O-])cc1[N+](=O)[O-]",
               regex=r"oc1c\(\[n\+\]\(=o\)\[o-\]\)cc\(\[n\+\]\(=o\)\[o-\]\)cc1\[n\+\]\(=o\)\[o-\]",
               severity="critical",
               advice="Picric acid is a powerful secondary explosive when dry."),

    # --- Peroxide-formers (storage hazard) ------------------------------
    HazardRule("PXF_DIISOPROPYL_ETHER", "Diisopropyl ether",
               smarts="CC(C)OC(C)C", regex=r"^cc\(c\)oc\(c\)c$",
               severity="warn",
               advice="Forms shock-sensitive peroxides on storage. Test before distilling to dryness."),
    HazardRule("PXF_THF", "Tetrahydrofuran",
               smarts="C1CCOC1", regex=r"^c1ccoc1$",
               severity="warn",
               advice="THF forms peroxides; stabilize with BHT or test before concentrating."),
    HazardRule("PXF_DIETHYL_ETHER", "Diethyl ether",
               smarts="CCOCC", regex=r"^ccocc$",
               severity="warn",
               advice="Diethyl ether forms peroxides; do not concentrate aged stock to dryness."),

    # --- Pyrophorics -----------------------------------------------------
    HazardRule("PYRO_ALKYLLITHIUM", "Alkyllithium",
               smarts="[Li][CX4]", regex=r"\[li\]c|c\[li\]",
               severity="error",
               advice="Pyrophoric in air. Use inert atmosphere, cooled syringe technique."),
    HazardRule("PYRO_TRIMETHYLALUMINUM", "Trialkylaluminum",
               smarts="[Al]([CX4])([CX4])[CX4]", regex=None,
               severity="error",
               advice="Pyrophoric; ignites in air. Use Schlenk technique."),
    HazardRule("PYRO_DEAL", "DIBAL / dialkylaluminum hydride",
               smarts="[Al]([H])([CX4])[CX4]", regex=None,
               severity="error",
               advice="Pyrophoric and water-reactive."),

    # --- Highly toxic ----------------------------------------------------
    HazardRule("TOX_PHOSGENE", "Phosgene",
               smarts="ClC(=O)Cl", regex=r"^clc\(=o\)cl$",
               severity="critical",
               advice="Acutely toxic gas. Use safer surrogate (triphosgene, CDI, DMC) where possible."),
    HazardRule("TOX_HCN", "Hydrogen cyanide / cyanide salts",
               smarts="[C-]#N", regex=r"\[c-\]#n",
               severity="critical",
               advice="Acutely toxic. Use cyanohydrins or pre-formed nitriles where possible."),
    HazardRule("TOX_OSMIUM_TETROXIDE", "Osmium tetroxide",
               smarts="O=[Os](=O)(=O)=O", regex=r"o=\[os\]\(=o\)\(=o\)=o",
               severity="error",
               advice="Acutely toxic and volatile; use catalytic OsO4 with NMO or Upjohn."),
    HazardRule("TOX_DMS", "Dimethyl sulfate (alkylating, CMR)",
               smarts="COS(=O)(=O)OC", regex=r"^cos\(=o\)\(=o\)oc$",
               severity="error",
               advice="Suspected carcinogen and alkylator. Consider methyl iodide or trimethyl orthoester."),

    # --- Strong oxidizers ------------------------------------------------
    HazardRule("OX_PERCHLORATE", "Perchlorate salt",
               smarts="[Cl](=O)(=O)(=O)[O-]", regex=r"cl\(=o\)\(=o\)\(=o\)\[o-\]",
               severity="warn",
               advice="Powerful oxidizer; incompatible with organic fuels."),
]


def flag_hazards(smiles: str) -> ToolResult:
    """Flag all hazard rules that fire against the given SMILES."""
    t0 = time.perf_counter()
    issues: list[Issue] = []
    fired: list[dict] = []
    lower = smiles.strip().lower()

    if _RDKIT and Chem is not None:        # pragma: no cover
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            issues.append(Issue("hazards_parse", "warn", "Could not parse SMILES for hazard screen"))
        else:
            for rule in HAZARD_RULES:
                if not rule.smarts:
                    continue
                patt = Chem.MolFromSmarts(rule.smarts)
                if patt is not None and mol.HasSubstructMatch(patt):
                    issues.append(Issue(rule.code, rule.severity, rule.name,
                                        detail={"advice": rule.advice}))
                    fired.append({"code": rule.code, "severity": rule.severity,
                                  "name": rule.name, "advice": rule.advice})
    else:
        for rule in HAZARD_RULES:
            if rule.regex and re.search(rule.regex, lower):
                issues.append(Issue(rule.code, rule.severity, rule.name,
                                    detail={"advice": rule.advice, "fallback": True}))
                fired.append({"code": rule.code, "severity": rule.severity,
                              "name": rule.name, "advice": rule.advice})

    return ToolResult(
        ok=not any(i.severity == "critical" for i in issues),
        tool="flag_hazards",
        data={"fired": fired, "rdkit": _RDKIT, "rule_count": len(HAZARD_RULES)},
        issues=issues,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )
