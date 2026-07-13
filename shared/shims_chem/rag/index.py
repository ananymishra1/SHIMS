"""
Hybrid BM25 + dense retrieval with reciprocal-rank fusion.

Dependencies are soft: if rank_bm25 and sentence-transformers are installed
they are used; otherwise we fall back to pure-Python TF-IDF and a hashed-
bag dense substitute. Either way the API is identical.

Why RRF (not weighted-sum)? Sparse and dense scores live on incompatible
scales; weighted-sum requires per-corpus calibration. RRF (1/(k + rank))
is scale-invariant and beats most ad-hoc fusions on standard benchmarks
(Cormack et al. 2009; widely used in BEIR).
"""
from __future__ import annotations
import math
import re
import struct
import hashlib
from collections import Counter, defaultdict
from typing import Iterable

try:                                            # pragma: no cover
    from rank_bm25 import BM25Okapi             # type: ignore
    _BM25_LIB = True
except Exception:                               # pragma: no cover
    _BM25_LIB = False

try:                                            # pragma: no cover
    from sentence_transformers import SentenceTransformer  # type: ignore
    _ST = True
except Exception:                               # pragma: no cover
    _ST = False

from .types import Chunk, Hit, Store


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\[\]\(\)=#+\-./\\@%]")


def _tokenize_for_bm25(text: str) -> list[str]:
    """Lower-cased word tokens + raw chemistry punctuation as discrete tokens.
    Keeping punctuation as tokens lets BM25 find SMILES-by-substring."""
    return [t.lower() for t in _TOKEN_RE.findall(text)]


# ---------- BM25 / TF-IDF ---------------------------------------------------

class _PurePyTFIDF:
    """Tiny TF-IDF used when rank_bm25 is unavailable. Same interface."""

    def __init__(self, corpus_tokens: list[list[str]]) -> None:
        self.docs = corpus_tokens
        self.N = len(corpus_tokens)
        self.df: Counter[str] = Counter()
        for d in corpus_tokens:
            for t in set(d):
                self.df[t] += 1

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.N
        if not query_tokens:
            return scores
        for i, d in enumerate(self.docs):
            tf = Counter(d)
            for q in query_tokens:
                if q not in tf:
                    continue
                idf = math.log(1 + self.N / (1 + self.df[q]))
                scores[i] += (1 + math.log(tf[q])) * idf
            # length norm
            denom = max(1, len(d)) ** 0.25
            scores[i] /= denom
        return scores


# ---------- Dense -----------------------------------------------------------

class _HashedBagEncoder:
    """Cheap dense-ish encoder: hashed bigram bag projected to 256d, used when
    sentence-transformers is missing. Not as good as a real model — but it
    captures lexical overlap usefully and lets the fusion code run."""

    DIM = 256

    @staticmethod
    def _bigrams(text: str) -> list[str]:
        toks = _tokenize_for_bm25(text)
        return [toks[i] + " " + toks[i + 1] for i in range(len(toks) - 1)] + toks

    def encode(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            v = [0.0] * self.DIM
            for bg in self._bigrams(text):
                h = hashlib.md5(bg.encode("utf-8")).digest()
                # Use two int32 from the digest for hash-index + sign
                idx = struct.unpack_from(">I", h, 0)[0] % self.DIM
                sign = 1.0 if (h[4] & 1) else -1.0
                v[idx] += sign
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


# ---------- Hybrid index ---------------------------------------------------

class HybridIndex:
    """Two-track index that holds whatever Store gives it."""

    def __init__(self, store: Store, *, dense_model: str | None = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.store = store
        self._chunks: list[Chunk] = []
        self._bm25 = None
        self._dense_vecs: list[list[float]] = []
        self._encoder: object | None = None
        self._dense_model = dense_model

    # ---- ingestion -------------------------------------------------------

    def reindex(self) -> None:
        """Rebuild BM25 and dense indices from the store."""
        self._chunks = self.store.all_chunks()
        if not self._chunks:
            self._bm25 = None
            self._dense_vecs = []
            return
        tokenized = [_tokenize_for_bm25(c.text) for c in self._chunks]
        if _BM25_LIB:                            # pragma: no cover
            self._bm25 = BM25Okapi(tokenized)
        else:
            self._bm25 = _PurePyTFIDF(tokenized)

        # Dense
        if _ST and self._dense_model:            # pragma: no cover
            try:
                self._encoder = SentenceTransformer(self._dense_model)
                self._dense_vecs = self._encoder.encode(
                    [c.text for c in self._chunks], normalize_embeddings=True
                ).tolist()
            except Exception:
                self._encoder = _HashedBagEncoder()
                self._dense_vecs = self._encoder.encode([c.text for c in self._chunks])
        else:
            self._encoder = _HashedBagEncoder()
            self._dense_vecs = self._encoder.encode([c.text for c in self._chunks])

    # ---- query -----------------------------------------------------------

    def search(self, query: str, *, top_k: int = 6, k_rrf: int = 60) -> list[Hit]:
        if not self._chunks:
            self.reindex()
            if not self._chunks:
                return []

        # Sparse
        q_toks = _tokenize_for_bm25(query)
        sparse_scores = self._bm25.get_scores(q_toks) if self._bm25 is not None else [0.0] * len(self._chunks)

        # Dense
        if isinstance(self._encoder, _HashedBagEncoder):
            qv = self._encoder.encode([query])[0]
        elif self._encoder is not None:           # pragma: no cover
            qv = self._encoder.encode([query], normalize_embeddings=True).tolist()[0]
        else:
            qv = None

        if qv is not None and self._dense_vecs:
            dense_scores = [_dot(qv, v) for v in self._dense_vecs]
        else:
            dense_scores = [0.0] * len(self._chunks)

        # Rank by each
        sparse_rank = _rank_indices(sparse_scores)
        dense_rank = _rank_indices(dense_scores)

        # RRF
        rrf: dict[int, float] = defaultdict(float)
        for i, r in sparse_rank.items():
            rrf[i] += 1 / (k_rrf + r)
        for i, r in dense_rank.items():
            rrf[i] += 1 / (k_rrf + r)

        top = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        hits = [Hit(chunk=self._chunks[i], score=round(s, 5), source="fused") for i, s in top]
        return hits


def _rank_indices(scores: list[float]) -> dict[int, int]:
    """Return {doc_idx: rank} where rank is 1-based, by descending score."""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return {idx: rank + 1 for rank, idx in enumerate(order)}


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
