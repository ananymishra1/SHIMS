"""
BERT-based patent claim classifier.

Two pieces:

  * `PatentClaimClassifier` — inference shim. Given (target_smiles,
    independent_claim_text) returns a probability that the claim covers
    the target. Designed to upgrade `fto.scoring._fto_risk` from Tanimoto-
    plus-substring to a real claim-language model.

  * `train.py` — the training script you run once on your SureChEMBL +
    USPTO slice. We don't ship weights (there's no public Freunek-Bodmer
    checkpoint to redistribute), but the pipeline is here end-to-end.

Both use HuggingFace transformers when installed; degrade to a deterministic
heuristic when not, so the FTO layer never blocks on missing weights.
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:                                            # pragma: no cover
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    _TX = True
except Exception:                               # pragma: no cover
    _TX = False


@dataclass
class ClaimPrediction:
    smiles: str
    claim: str
    p_covered: float        # 0..1
    method: str             # "bert" | "heuristic"
    rationale: str = ""


# Keyword bags used by the heuristic fallback. Tuned to be conservative
# (high precision, lower recall) so the FTO layer never inflates risk.
_PROCESS_KW = {
    "process", "method", "preparing", "preparation", "synthesis", "reacting",
    "contacting", "treating", "comprising the steps",
}
_COMPOSITION_KW = {
    "compound of formula", "compound according to", "pharmaceutical composition",
    "crystalline form", "polymorph", "salt of", "hydrate", "solvate",
}
_PROCESS_VERBS = re.compile(
    r"\b(react|contact|treat|couple|coupling|hydrogenat|cycliz|alkylat|acetylat|"
    r"acylat|esterificat|amidat|deprotect|protect|hydrolyz|hydrolys|oxidiz|"
    r"oxidis|reduc|condens|methylat|aminat|halogenat|brominat|chlorinat|"
    r"phosphorylat|sulfonat|nitrat)[a-zA-Z]*\b",
    re.IGNORECASE,
)


def _heuristic_claim_score(smiles: str, claim: str) -> tuple[float, str]:
    """Conservative, deterministic. Returns (p_covered, rationale)."""
    claim_l = claim.lower()
    score = 0.0
    notes: list[str] = []

    # 1) Token-level structural overlap with the target SMILES.
    # Extract distinctive sub-tokens (length≥4) from the SMILES and look for them.
    toks = [t for t in re.findall(r"[A-Za-z0-9@\[\]\(\)=#]{4,}", smiles) if any(c.isalpha() for c in t)]
    hits = sum(1 for t in toks if t in claim)
    if toks:
        score += 0.45 * (hits / len(toks))
        if hits:
            notes.append(f"{hits}/{len(toks)} SMILES sub-tokens appear in claim")

    # 2) Claim type signals
    if any(kw in claim_l for kw in _PROCESS_KW) and _PROCESS_VERBS.search(claim_l):
        score += 0.25
        notes.append("process-style claim")
    if any(kw in claim_l for kw in _COMPOSITION_KW):
        score += 0.25
        notes.append("composition-style claim")

    # 3) Markush "formula (I)" or similar — broad coverage
    if re.search(r"\bformula\s*\(?[I-Xa-z0-9]+\)?", claim, re.IGNORECASE):
        score += 0.1
        notes.append("Markush formula reference")

    return min(1.0, score), "; ".join(notes) or "no specific markers"


class PatentClaimClassifier:
    """Inference shim. Loads a fine-tuned transformer if available; else heuristic."""

    def __init__(self, model_dir: str | os.PathLike | None = None) -> None:
        self.model_dir = Path(model_dir) if model_dir else None
        self._model = None
        self._tok = None
        if _TX and self.model_dir and (self.model_dir / "config.json").exists():
            try:                                # pragma: no cover
                self._tok = AutoTokenizer.from_pretrained(str(self.model_dir))
                self._model = AutoModelForSequenceClassification.from_pretrained(str(self.model_dir))
                self._model.eval()
            except Exception:
                self._model = None

    def predict(self, smiles: str, claim: str) -> ClaimPrediction:
        if self._model is not None and self._tok is not None:        # pragma: no cover
            inputs = self._tok(
                f"[SMILES] {smiles} [SEP] {claim}",
                truncation=True, max_length=512, return_tensors="pt",
            )
            with torch.no_grad():
                logits = self._model(**inputs).logits[0]
                probs = torch.softmax(logits, dim=-1)
                p = float(probs[1].item()) if probs.shape[0] >= 2 else float(probs[0].item())
            return ClaimPrediction(smiles=smiles, claim=claim, p_covered=p,
                                    method="bert", rationale="model logits")
        p, why = _heuristic_claim_score(smiles, claim)
        return ClaimPrediction(smiles=smiles, claim=claim, p_covered=p,
                                method="heuristic", rationale=why)


# ---------------------------------------------------------------------------
# Training script lives in `train.py` next to this file.
# ---------------------------------------------------------------------------
