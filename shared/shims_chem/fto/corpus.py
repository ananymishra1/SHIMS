"""
Patent corpus interface.

Production: subclass `PatentCorpus`, ingest SureChEMBL bulk + USPTO grants +
Indian Patent Office, expose `lookup_by_similarity` over Tanimoto on Morgan
fingerprints, and (optionally) a BERT claim classifier per the Freunek-Bodmer
FTO paper. The interface here is what `scoring.py` consumes — anything that
satisfies it works.

For the offline scaffold, `SyntheticCorpus` provides a small set of plausible
hits keyed by structural substrings, so the FTO pipeline runs without network
or 100 GB of patent data.
"""
from __future__ import annotations
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass
class PatentHit:
    patent_id: str                  # e.g. "US10123456B2"
    jurisdiction: str               # "US" | "EP" | "IN" | "WO" | "JP" | "CN"
    title: str
    similarity: float               # 0..1
    independent_claim_snippet: str
    expiry: date | None             # None if unknown
    assignee: str | None = None
    pdf_url: str | None = None      # may be None for offline-only

    @property
    def is_live(self) -> bool:
        return self.expiry is None or self.expiry > date.today()


class PatentCorpus(ABC):
    @abstractmethod
    def lookup_by_similarity(self, smiles: str, *, top_k: int = 5,
                              min_similarity: float = 0.6) -> list[PatentHit]: ...

    @abstractmethod
    def __len__(self) -> int: ...


# ----- Bundled offline corpus -----------------------------------------------

_SYNTHETIC_DB: list[dict] = [
    {
        "patent_id": "US10123456B2", "jurisdiction": "US",
        "title": "Process for the preparation of paracetamol intermediates",
        "substrings": ["CC(=O)Nc1ccc", "Nc1ccc(O)cc1"],
        "claim": "A process for preparing 4-aminophenol comprising hydrogenation of p-nitrophenol in the presence of...",
        "expiry": "2027-04-15", "assignee": "GenericPharma Inc.",
    },
    {
        "patent_id": "EP3456789B1", "jurisdiction": "EP",
        "title": "Crystalline form II of an antihypertensive amide",
        "substrings": ["C(=O)N", "c1ccncc1"],
        "claim": "Crystalline form II of the compound of formula (I) characterized by an XRPD pattern...",
        "expiry": "2031-09-30", "assignee": "EuroPharma SA",
    },
    {
        "patent_id": "IN395678", "jurisdiction": "IN",
        "title": "An improved process for an API featuring an aryl-Br Buchwald coupling",
        "substrings": ["c1ccc(Br)cc1", "Pd"],
        "claim": "A process comprising contacting an aryl bromide with an amine in the presence of a palladium catalyst...",
        "expiry": "2028-12-01", "assignee": "Bharat API Labs",
    },
    {
        "patent_id": "WO2022112233A1", "jurisdiction": "WO",
        "title": "Continuous flow synthesis of beta-lactam intermediates",
        "substrings": ["C1CC(=O)N1", "S1"],
        "claim": "A continuous flow process for preparing a beta-lactam comprising mixing reagent streams...",
        "expiry": "2042-03-12", "assignee": "FlowChem Pte Ltd",
    },
    {
        "patent_id": "US9876543B1", "jurisdiction": "US",
        "title": "Crystalline polymorph of a kinase inhibitor",
        "substrings": ["c1ncccc1N", "S(=O)(=O)"],
        "claim": "A crystalline polymorph of the compound of formula (II) characterized by...",
        "expiry": "2024-07-19", "assignee": "OncoSmall Co.",  # already expired — useful test
    },
]


class SyntheticCorpus(PatentCorpus):
    """Offline patent corpus for demo/test. Matches on substring + crude Tanimoto-like scoring."""

    def __len__(self) -> int:
        return len(_SYNTHETIC_DB)

    def lookup_by_similarity(self, smiles: str, *, top_k: int = 5,
                              min_similarity: float = 0.6) -> list[PatentHit]:
        if not smiles:
            return []
        hits: list[PatentHit] = []
        s = smiles
        for rec in _SYNTHETIC_DB:
            # Best substring match
            best = 0.0
            for sub in rec["substrings"]:
                if sub in s:
                    # crude similarity ~ ratio of substring length to total
                    sim = min(1.0, len(sub) / max(len(s), 1) + 0.4)
                else:
                    # fuzzy: count shared bigrams
                    sim = _bigram_jaccard(sub, s)
                best = max(best, sim)
            if best >= min_similarity:
                expiry = None
                try:
                    expiry = date.fromisoformat(rec["expiry"])
                except Exception:
                    pass
                hits.append(PatentHit(
                    patent_id=rec["patent_id"],
                    jurisdiction=rec["jurisdiction"],
                    title=rec["title"],
                    similarity=round(best, 3),
                    independent_claim_snippet=rec["claim"],
                    expiry=expiry,
                    assignee=rec.get("assignee"),
                ))
        hits.sort(key=lambda h: h.similarity, reverse=True)
        return hits[:top_k]


def _bigram_jaccard(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    A = {a[i:i+2] for i in range(len(a) - 1)}
    B = {b[i:i+2] for i in range(len(b) - 1)}
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)
