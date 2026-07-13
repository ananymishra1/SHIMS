"""
Symbolic-teacher → ternary-student distillation.

We synthesize training data by sampling chemistry inputs (SMILES, candidate
masses, candidate temperatures, candidate impurity %, candidate solvent
choices) and labeling them with the symbolic verifier's own outputs. Then
we fit a small float MLP to imitate those labels and ternarize it.

This is intentionally task-specific: a distinct micro-model per edge job.
A general-purpose 'mini chemistry LM' is huge; a focused 'is THIS dispensed
mass within tolerance for the BMR step?' classifier is tiny — that's the
asymmetry we exploit.

Three distillation jobs come included; add your own by writing a featurizer
+ labeler and calling `distill_to_ternary`.
"""
from __future__ import annotations
import math
import random
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..verifier import flag_hazards, run_tool, validate_smiles
from .ternary import TernaryLayer, TernaryMLP, ternarize


# ---------------------------------------------------------------------------
# Featurizers — turn a structured input into a float feature vector.
# Each edge job has its own; the dimensionality stays small so the resulting
# micro-net fits in tens of KB.
# ---------------------------------------------------------------------------

def featurize_hazard_input(smiles: str, dim: int = 128) -> np.ndarray:
    """Hashed character-bigram bag of SMILES, 128-d."""
    v = np.zeros(dim, dtype=np.float32)
    s = (smiles or "").lower()
    if not s:
        return v
    bgs = [s[i:i+2] for i in range(len(s) - 1)] + list(s)
    for tok in bgs:
        h = (hash(tok) & 0xFFFFFFFF) % dim
        sign = 1.0 if (hash(tok + "_sign") & 1) else -1.0
        v[h] += sign
    n = np.linalg.norm(v) + 1e-9
    return v / n


def featurize_mass_check(target_mg: float, dispensed_mg: float, tol_pct: float, dim: int = 32) -> np.ndarray:
    """Compact numerical features for the weighing-balance micro-net."""
    eps = 1e-9
    rel = (dispensed_mg - target_mg) / (target_mg + eps)
    feat = np.zeros(dim, dtype=np.float32)
    feat[0] = math.log10(max(target_mg, eps))
    feat[1] = math.log10(max(dispensed_mg, eps))
    feat[2] = rel
    feat[3] = abs(rel)
    feat[4] = max(0.0, abs(rel) - tol_pct / 100.0)   # exceedance
    feat[5] = tol_pct / 100.0
    feat[6] = 1.0 if rel > 0 else -1.0
    feat[7] = math.tanh(rel * 10.0)
    # rest are zero — kept for slack so a real plant can extend features
    return feat


def featurize_thermal(temps_c: list[float], setpoint_c: float,
                       rate_c_per_min: float, dim: int = 64) -> np.ndarray:
    """
    Rolling thermal features for the reactor-HMI micro-net.

    Crucially, every feature is in roughly [-2, 2]. The ternary net is very
    sensitive to absolute-scale features dominating (since the recovery scale
    is per-row), so we work in *deltas from setpoint* and *normalized rates*.
    """
    v = np.zeros(dim, dtype=np.float32)
    if not temps_c:
        return v
    arr = np.array(temps_c[-32:], dtype=np.float32)
    delta = arr - setpoint_c                          # delta from setpoint
    v[0] = float(delta[-1]) / 20.0                    # current overshoot in ~[-2, 2]
    v[1] = float(delta.mean()) / 20.0
    v[2] = float(delta.max()) / 20.0
    v[3] = float(delta.min()) / 20.0
    v[4] = float(arr.std()) / 5.0
    v[5] = math.tanh(rate_c_per_min / 2.0)            # rate squashed
    v[6] = 1.0 if rate_c_per_min > 0.5 else 0.0       # explicit pre-runaway flag
    v[7] = 1.0 if rate_c_per_min > 1.5 else 0.0       # explicit runaway flag
    v[8] = 1.0 if delta[-1] > 5 else 0.0
    v[9] = 1.0 if delta[-1] > 10 else 0.0
    # Linear slope over last 8 samples
    if len(arr) >= 8:
        tail = arr[-8:]
        slope = float(np.polyfit(np.arange(8), tail, 1)[0])
        v[10] = math.tanh(slope / 2.0)
    # Pack normalized recent residuals
    v[16:16 + len(arr)] = np.clip(delta / 20.0, -2, 2).astype(np.float32)
    return v


# ---------------------------------------------------------------------------
# Dataset synthesis — call the symbolic verifier as the teacher.
# ---------------------------------------------------------------------------

def build_distillation_dataset(
    job: str,
    n_examples: int = 5000,
    *,
    rng: random.Random | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns (X, y). `y` is class index for classifiers, float for regressors.
    Jobs supported here:
      * "hazard_micro"   — predict (severity bucket) for a SMILES.
      * "mass_check"     — predict (ok / tight / over_low / over_high).
      * "thermal_runaway" — predict (ok / pre_runaway / runaway).
    """
    rng = rng or random.Random(42)

    if job == "hazard_micro":
        return _ds_hazard(n_examples, rng)
    if job == "mass_check":
        return _ds_mass(n_examples, rng)
    if job == "thermal_runaway":
        return _ds_thermal(n_examples, rng)
    raise ValueError(f"unknown distillation job: {job}")


def _ds_hazard(n: int, rng: random.Random) -> tuple[np.ndarray, np.ndarray]:
    """Sample SMILES from a curated base + perturbations; labels come from
    flag_hazards. Classes: 0=clean, 1=warn, 2=error, 3=critical."""
    seeds = [
        "CCO", "CC(=O)O", "c1ccccc1", "ClC(=O)Cl", "[Li]CC",
        "Oc1c([N+](=O)[O-])cc([N+](=O)[O-])cc1[N+](=O)[O-]",
        "CC(C)OC(C)C", "C1CCOC1", "CCOCC", "COS(=O)(=O)OC",
        "O=[Os](=O)(=O)=O", "CC(=O)Nc1ccc(O)cc1",
    ]
    sev_to_idx = {"info": 0, "warn": 1, "error": 2, "critical": 3}

    X = []
    y = []
    for _ in range(n):
        base = rng.choice(seeds)
        # Slight random perturbation: occasionally append a chain
        if rng.random() < 0.4:
            tail = rng.choice(["", "C", "CC", "O", "CO", "N", "Cl"])
            smi = base + tail
        else:
            smi = base
        v = validate_smiles(smi)
        if not v.ok:
            # Skip non-valid; the hazard net assumes a parseable input upstream.
            continue
        r = flag_hazards(smi)
        worst = 0
        for iss in r.issues:
            worst = max(worst, sev_to_idx.get(iss.severity, 0))
        X.append(featurize_hazard_input(smi))
        y.append(worst)
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)


def _ds_mass(n: int, rng: random.Random) -> tuple[np.ndarray, np.ndarray]:
    """Synthetic mass-dispensing data with a deterministic teacher.
    Labels: 0 = ok, 1 = within tol but tight (>50% of tol), 2 = over_low, 3 = over_high."""
    X = []; y = []
    for _ in range(n):
        target = 10 ** rng.uniform(0, 4)               # 1 mg .. 10 g
        tol = rng.choice([0.5, 1.0, 2.0, 5.0])         # tol %
        rel = rng.uniform(-0.2, 0.2)                   # ±20 %
        dispensed = target * (1 + rel)
        excess = abs(rel) - tol / 100.0
        if excess > 0:
            label = 2 if rel < 0 else 3
        elif abs(rel) > 0.5 * (tol / 100.0):
            label = 1
        else:
            label = 0
        X.append(featurize_mass_check(target, dispensed, tol))
        y.append(label)
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)


def _ds_thermal(n: int, rng: random.Random) -> tuple[np.ndarray, np.ndarray]:
    """Synthetic thermal traces with three regimes.
    0 = normal control, 1 = pre-runaway (climbing>0.5°C/min near setpoint),
    2 = runaway (overshoot >10°C and still rising)."""
    X = []; y = []
    for _ in range(n):
        setpoint = rng.uniform(20, 120)
        regime = rng.choice([0, 1, 2])
        if regime == 0:
            base = setpoint + rng.uniform(-2, 2)
            temps = [base + rng.gauss(0, 0.3) for _ in range(32)]
            rate = 0.05 + rng.gauss(0, 0.05)
        elif regime == 1:
            # climbing
            temps = [setpoint - 5 + i * 0.5 + rng.gauss(0, 0.2) for i in range(32)]
            rate = 0.8 + rng.gauss(0, 0.1)
        else:
            temps = [setpoint + 10 + i * 1.5 + rng.gauss(0, 0.4) for i in range(32)]
            rate = 2.0 + rng.gauss(0, 0.3)
        X.append(featurize_thermal(temps, setpoint, rate))
        y.append(regime)
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)


# ---------------------------------------------------------------------------
# Training — small float MLP, then ternarize.
# We do training in pure NumPy too (gradient descent on a 2-3 layer net is
# nothing) so this module has zero training-time dependencies.
# ---------------------------------------------------------------------------

@dataclass
class DistillConfig:
    hidden: int = 48                # large enough that ternarization survives across all default jobs
    layers: int = 2                 # number of hidden layers
    epochs: int = 30
    batch: int = 64
    lr: float = 0.05
    seed: int = 42


def distill_to_ternary(X: np.ndarray, y: np.ndarray, *,
                        num_classes: int,
                        cfg: DistillConfig = DistillConfig()) -> tuple[TernaryMLP, dict]:
    """Train a tiny float MLP on (X, y), ternarize the weights, return it."""
    rng = np.random.RandomState(cfg.seed)
    n, d = X.shape

    # Initialize float weights
    dims = [d] + [cfg.hidden] * cfg.layers + [num_classes]
    Wf = [rng.randn(dims[i + 1], dims[i]).astype(np.float32) * math.sqrt(2 / dims[i])
          for i in range(len(dims) - 1)]
    bf = [np.zeros(dims[i + 1], dtype=np.float32) for i in range(len(dims) - 1)]

    def forward(x: np.ndarray):
        h = x; cache = [h]
        for i in range(len(Wf) - 1):
            h = h @ Wf[i].T + bf[i]
            h = np.maximum(h, 0)
            cache.append(h)
        logits = h @ Wf[-1].T + bf[-1]
        return logits, cache

    def softmax_xent(logits, y):
        z = logits - logits.max(axis=1, keepdims=True)
        e = np.exp(z); s = e.sum(axis=1, keepdims=True); p = e / s
        loss = -np.log(p[np.arange(len(y)), y] + 1e-9).mean()
        return loss, p

    history: list[float] = []
    for ep in range(cfg.epochs):
        perm = rng.permutation(n)
        losses = []
        for i in range(0, n, cfg.batch):
            idx = perm[i:i + cfg.batch]
            xb = X[idx]; yb = y[idx]
            logits, cache = forward(xb)
            loss, p = softmax_xent(logits, yb)
            losses.append(float(loss))

            # Backward
            dlogits = p
            dlogits[np.arange(len(yb)), yb] -= 1
            dlogits /= len(yb)

            gW = [None] * len(Wf)
            gb = [None] * len(bf)

            # Last layer
            gW[-1] = dlogits.T @ cache[-1]
            gb[-1] = dlogits.sum(axis=0)
            dh = dlogits @ Wf[-1]
            # Hidden layers (reverse)
            for li in range(len(Wf) - 2, -1, -1):
                dh = dh * (cache[li + 1] > 0)
                gW[li] = dh.T @ cache[li]
                gb[li] = dh.sum(axis=0)
                dh = dh @ Wf[li]

            for li in range(len(Wf)):
                Wf[li] -= cfg.lr * gW[li]
                bf[li] -= cfg.lr * gb[li]

        history.append(sum(losses) / len(losses))

    # Final accuracy
    logits, _ = forward(X)
    acc = float((logits.argmax(axis=1) == y).mean())

    # Ternarize each weight
    layers: list[TernaryLayer] = []
    for i in range(len(Wf)):
        Wt, scale = ternarize(Wf[i])
        act = "relu" if i < len(Wf) - 1 else "none"
        layers.append(TernaryLayer(W=Wt, scale=scale, bias=bf[i], activation=act))
    mlp = TernaryMLP(
        layers=layers,
        input_dim=int(d),
        output_dim=int(num_classes),
        output_kind="logits",
        meta={
            "train_loss_history": [round(h, 4) for h in history],
            "float_train_acc": round(acc, 4),
        },
    )
    # Eval ternary version
    ty = mlp.predict(X).argmax(axis=1)
    tacc = float((ty == y).mean())
    mlp.meta["ternary_train_acc"] = round(tacc, 4)
    mlp.meta["acc_drop_from_ternarize"] = round(acc - tacc, 4)
    return mlp, mlp.meta
