"""
Ternary-weight MLP.

Each weight is one of {-1, 0, +1}, stored as int8 for simplicity (1.58 bits is
the theoretical minimum; packing to actually 1.58 bpw is left to the C runtime).
Activations stay float32 in inference. Forward pass is therefore a sequence of
sign-multiplied accumulations with no float multiplies — viable on a Pentium II.

This is the runtime. Distillation (training) is in distill.py.

Why pure NumPy: the whole point is to run with zero heavy deps, including on
old industrial PCs whose Python is too old for modern PyTorch. NumPy ships
everywhere; even microPython has a slim variant.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class TernaryLayer:
    """One linear layer + activation. W in {-1, 0, +1}, scale s, bias b."""
    W: np.ndarray             # int8, shape (out, in)
    scale: np.ndarray         # float32, shape (out,) — per-row scale (BitNet b1.58 style)
    bias: np.ndarray          # float32, shape (out,)
    activation: str = "relu"  # "relu" | "tanh" | "none"

    def forward(self, x: np.ndarray) -> np.ndarray:
        # x shape: (batch, in). W shape: (out, in). y = (W @ x.T).T * scale + bias
        # We do it with int8 @ float32 to keep it cheap; production C kernel
        # would use lookup tables + popcount.
        y = (x @ self.W.astype(np.float32).T) * self.scale + self.bias
        if self.activation == "relu":
            np.maximum(y, 0, out=y)
        elif self.activation == "tanh":
            np.tanh(y, out=y)
        return y


@dataclass
class TernaryMLP:
    """Simple sequential MLP of ternary layers."""
    layers: list[TernaryLayer]
    input_dim: int
    output_dim: int
    output_kind: str = "logits"      # 'logits' or 'regression'
    meta: dict = field(default_factory=dict)

    def forward(self, x: np.ndarray) -> np.ndarray:
        for L in self.layers:
            x = L.forward(x)
        return x

    def predict(self, x: np.ndarray) -> np.ndarray:
        y = self.forward(x)
        if self.output_kind == "logits":
            # Softmax
            y = y - y.max(axis=-1, keepdims=True)
            e = np.exp(y)
            return e / e.sum(axis=-1, keepdims=True)
        return y

    @property
    def n_params(self) -> int:
        return sum(int(L.W.size + L.scale.size + L.bias.size) for L in self.layers)

    @property
    def bytes_on_disk(self) -> int:
        # int8 weights + float32 scale + float32 bias
        return sum(int(L.W.size + L.scale.size * 4 + L.bias.size * 4) for L in self.layers)


def ternarize(W: np.ndarray, alpha: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """
    Convert a float weight matrix to BitNet b1.58-style ternary.

    Per BitNet b1.58: choose a per-row threshold; values |w|<threshold map to 0,
    positives to +1, negatives to -1; then a per-row scale s recovers magnitude.
    Empirically, threshold = 0.75 * mean(|W|) (per row) works well; we use that.
    """
    W = np.asarray(W, dtype=np.float32)
    if W.ndim != 2:
        raise ValueError("Expected 2D weight matrix")
    abs_mean = np.abs(W).mean(axis=1, keepdims=True) + 1e-9
    thr = 0.75 * abs_mean
    Wt = np.zeros_like(W, dtype=np.int8)
    Wt[W > thr] = 1
    Wt[W < -thr] = -1
    # Recovery scale: argmin ||s * Wt - W||^2 with Wt in {-1, 0, +1}
    # = sum(W * Wt) / sum(Wt^2) per row
    num = (W * Wt).sum(axis=1)
    den = (Wt.astype(np.float32) ** 2).sum(axis=1) + 1e-9
    scale = (num / den).astype(np.float32)
    return Wt, scale


def save_ternary(mlp: TernaryMLP, path: str | Path) -> None:
    """Write a single .npz with the ternary weights + meta JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict = {"_n_layers": np.array([len(mlp.layers)], dtype=np.int32)}
    for i, L in enumerate(mlp.layers):
        arrays[f"W_{i}"] = L.W
        arrays[f"s_{i}"] = L.scale
        arrays[f"b_{i}"] = L.bias
        arrays[f"act_{i}"] = np.array([L.activation], dtype=object)
    np.savez_compressed(p, **arrays)
    p.with_suffix(".json").write_text(json.dumps({
        "input_dim": mlp.input_dim,
        "output_dim": mlp.output_dim,
        "output_kind": mlp.output_kind,
        "n_layers": len(mlp.layers),
        "n_params": mlp.n_params,
        "bytes_on_disk": mlp.bytes_on_disk,
        "meta": mlp.meta,
    }, indent=2))


def load_ternary(path: str | Path) -> TernaryMLP:
    p = Path(path)
    meta = json.loads(p.with_suffix(".json").read_text())
    z = np.load(p, allow_pickle=True)
    n = int(z["_n_layers"][0])
    layers: list[TernaryLayer] = []
    for i in range(n):
        layers.append(TernaryLayer(
            W=z[f"W_{i}"].astype(np.int8),
            scale=z[f"s_{i}"].astype(np.float32),
            bias=z[f"b_{i}"].astype(np.float32),
            activation=str(z[f"act_{i}"][0]),
        ))
    return TernaryMLP(
        layers=layers,
        input_dim=int(meta["input_dim"]),
        output_dim=int(meta["output_dim"]),
        output_kind=meta.get("output_kind", "logits"),
        meta=meta.get("meta", {}),
    )


# ---------------------------------------------------------------------------
# C-runner exporter — writes the compact binary format described in runner.c
# ---------------------------------------------------------------------------

import struct

_MAGIC = 0x53484D45         # 'SHME'
_ACT_CODE = {"none": 0, "relu": 1, "tanh": 2}


def export_for_c_runner(mlp: TernaryMLP, path: str | Path) -> int:
    """Write the binary the bundled `runner.c` reads. Returns bytes written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    buf = bytearray()
    buf += struct.pack("<II", _MAGIC, len(mlp.layers))
    for L in mlp.layers:
        in_dim = int(L.W.shape[1])
        out_dim = int(L.W.shape[0])
        buf += struct.pack("<IIB", in_dim, out_dim,
                            _ACT_CODE.get(L.activation, 0))
        buf += L.W.astype(np.int8).tobytes(order="C")
        buf += L.scale.astype("<f4").tobytes()
        buf += L.bias.astype("<f4").tobytes()
    p.write_bytes(buf)
    return len(buf)
