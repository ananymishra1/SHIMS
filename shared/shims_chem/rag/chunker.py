"""
Chemistry-aware chunker.

Goal: produce ~256-token chunks suitable for retrieval, BUT never split inside:
  * SMILES strings (including bracketed atoms, branches, ring closures)
  * Reaction SMILES (A.B>>C or A.B>cat>C)
  * CAS numbers (NN-NN-N)
  * Molecular formulae (C8H9NO2)
  * Reaction scheme blocks marked between ``` chem ... ``` fences

The strategy: tokenize the document into "atoms" of (text-run | smiles |
rxn-smiles | cas | formula | fenced-block), then greedily pack atoms into
chunks that respect a target size + overlap.

This is intentionally simple and dependency-free.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Iterator

from .types import Chunk, Document


# Patterns ------------------------------------------------------------------
# Recognise chemistry tokens. Order matters: longest/most-specific first.
_FENCE_RE = re.compile(r"```chem\b[\s\S]*?```", re.IGNORECASE)
_RXN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9@+\-\[\]\(\)=#/.\\%]{2,})(>>|>[^>\s]+>)([A-Za-z0-9@+\-\[\]\(\)=#/.\\%]{2,})(?![A-Za-z0-9])")
_SMILES_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9@+\-\[\]\(\)=#/.\\%]{4,})(?![A-Za-z0-9])")
_CAS_RE = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")
_FORMULA_RE = re.compile(r"\b([A-Z][a-z]?\d*){2,8}\b")


@dataclass
class _Atom:
    kind: str       # 'text' | 'smiles' | 'rxn' | 'cas' | 'formula' | 'fence'
    text: str
    start: int
    end: int


def _tokenize_atoms(text: str) -> list[_Atom]:
    """Find every chemistry token; the rest is plain text."""
    atoms: list[_Atom] = []
    # 1) Fenced blocks first (highest priority)
    spans: list[tuple[int, int, str]] = []
    for m in _FENCE_RE.finditer(text):
        spans.append((m.start(), m.end(), "fence"))
    # 2) Reaction SMILES
    for m in _RXN_RE.finditer(text):
        if not _overlaps_any(m.start(), m.end(), spans):
            spans.append((m.start(), m.end(), "rxn"))
    # 3) Plain SMILES (must look like SMILES — has at least one bond/bracket/digit-ring)
    for m in _SMILES_RE.finditer(text):
        s = m.group(1)
        if not _overlaps_any(m.start(), m.end(), spans):
            if any(c in s for c in "()=#[]") or re.search(r"\d", s):
                # Heuristic: rule out URLs and ordinary code identifiers
                if "://" in s or s.lower() in {"true", "false", "null", "none"}:
                    continue
                spans.append((m.start(), m.end(), "smiles"))
    # 4) CAS
    for m in _CAS_RE.finditer(text):
        if not _overlaps_any(m.start(), m.end(), spans):
            spans.append((m.start(), m.end(), "cas"))
    # 5) Formulae
    for m in _FORMULA_RE.finditer(text):
        if not _overlaps_any(m.start(), m.end(), spans):
            spans.append((m.start(), m.end(), "formula"))

    spans.sort(key=lambda s: s[0])

    # Fill gaps with text atoms
    pos = 0
    for s, e, kind in spans:
        if s > pos:
            atoms.append(_Atom("text", text[pos:s], pos, s))
        atoms.append(_Atom(kind, text[s:e], s, e))
        pos = e
    if pos < len(text):
        atoms.append(_Atom("text", text[pos:], pos, len(text)))
    return atoms


def _overlaps_any(s: int, e: int, spans: list[tuple[int, int, str]]) -> bool:
    return any(not (e <= a or s >= b) for a, b, _ in spans)


def _split_text_into_sentences(text: str) -> list[tuple[str, int, int]]:
    """Sentence-ish split respecting offsets."""
    out: list[tuple[str, int, int]] = []
    pos = 0
    for m in re.finditer(r"[^.!?\n]+[.!?\n]|[^.!?\n]+$", text):
        seg = m.group(0)
        out.append((seg, m.start(), m.end()))
    if not out and text:
        out.append((text, 0, len(text)))
    # Adjust offsets to absolute caller frame done by caller
    return out


def chemistry_chunk(doc: Document, *, target_chars: int = 1200,
                     overlap_chars: int = 200) -> list[Chunk]:
    """Chunk `doc.text` into Chunk objects, preserving chemistry tokens."""
    if not doc.text:
        return []

    atoms = _tokenize_atoms(doc.text)
    # Explode text atoms into sentences while keeping the rest intact
    units: list[_Atom] = []
    for a in atoms:
        if a.kind != "text":
            units.append(a)
            continue
        for seg, ls, le in _split_text_into_sentences(a.text):
            if not seg.strip():
                # Whitespace — attach to previous as overlap padding
                continue
            units.append(_Atom("text", seg, a.start + ls, a.start + le))

    # Greedily pack units into target_chars-sized chunks
    chunks: list[Chunk] = []
    cur: list[_Atom] = []
    cur_chars = 0
    for u in units:
        if cur_chars + len(u.text) > target_chars and cur:
            chunks.append(_emit(cur, doc))
            # Overlap window: keep tail units worth ~overlap_chars
            tail: list[_Atom] = []
            tail_chars = 0
            for tu in reversed(cur):
                if tail_chars + len(tu.text) > overlap_chars:
                    break
                tail.insert(0, tu)
                tail_chars += len(tu.text)
            cur = tail
            cur_chars = tail_chars
        cur.append(u)
        cur_chars += len(u.text)
    if cur:
        chunks.append(_emit(cur, doc))

    return chunks


def _emit(units: list[_Atom], doc: Document) -> Chunk:
    text = "".join(u.text for u in units)
    span_start = units[0].start
    span_end = units[-1].end
    kinds = sorted({u.kind for u in units if u.kind != "text"})
    return Chunk.new(doc, text=text, span_start=span_start, span_end=span_end,
                     chem_kinds=kinds)


def iter_chemistry_tokens(text: str) -> Iterator[tuple[str, str]]:
    """Stream every chemistry token (kind, value) found in `text` — utility."""
    for a in _tokenize_atoms(text):
        if a.kind != "text":
            yield a.kind, a.text
