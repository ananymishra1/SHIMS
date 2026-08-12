"""Per-model measured performance ledger for the native engine.

The concept LM Studio/Ollama don't have: SHIMS records the ACTUAL prompt and
generation speed of every model from real turns on THIS machine, persists it,
and feeds it back into the model advisor and the UI. Measured beats predicted:
this session proved a config drift can silently 30x-degrade a model — with a
ledger, any such regression is visible the moment it happens.

Storage: data/state/native_perf.json — one entry per model id with EMA speeds
(alpha 0.3, so recent turns dominate but one outlier doesn't) and turn counts.
Thread-safe; every write is atomic (tmp + replace). All failures are soft —
perf recording must never break a chat turn.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
PERF_PATH = Path(os.getenv("SHIMS_NATIVE_PERF_PATH", _ROOT / "data" / "state" / "native_perf.json"))
_LOCK = threading.Lock()
_EMA_ALPHA = 0.3


def _load() -> dict[str, Any]:
    try:
        return json.loads(PERF_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict[str, Any]) -> None:
    try:
        PERF_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PERF_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, PERF_PATH)
    except Exception:
        pass


def _ema(old: float | None, new: float) -> float:
    if not old or old <= 0:
        return round(new, 2)
    return round(old + _EMA_ALPHA * (new - old), 2)


def record(model: str, *, prompt_tokens: int, gen_tokens: int,
           ttft_s: float, total_s: float) -> None:
    """Record one completed turn's timings. Called from the engine facade."""
    if not model or total_s <= 0:
        return
    gen_time = max(total_s - ttft_s, 0.05)
    gen_tps = gen_tokens / gen_time if gen_tokens > 0 else 0.0
    prompt_tps = prompt_tokens / ttft_s if (prompt_tokens > 0 and ttft_s > 0.05) else 0.0
    with _LOCK:
        data = _load()
        entry = data.get(model) or {}
        if gen_tps > 0:
            entry["gen_tps"] = _ema(entry.get("gen_tps"), gen_tps)
        if prompt_tps > 0:
            entry["prompt_tps"] = _ema(entry.get("prompt_tps"), prompt_tps)
        entry["turns"] = int(entry.get("turns", 0)) + 1
        entry["last_ttft_s"] = round(ttft_s, 1)
        entry["last_total_s"] = round(total_s, 1)
        entry["last_ts"] = int(time.time())
        data[model] = entry
        _save(data)


def summary(model: str) -> dict[str, Any] | None:
    """Measured speeds for one model, or None if never measured."""
    with _LOCK:
        return _load().get(model)


def all_summaries() -> dict[str, Any]:
    with _LOCK:
        return _load()


def rating_from_measured(entry: dict[str, Any]) -> str | None:
    """fast/ok/slow from measured generation speed (human-chat thresholds)."""
    tps = float(entry.get("gen_tps") or 0)
    if tps <= 0:
        return None
    if tps >= 8:
        return "fast"
    if tps >= 4:
        return "ok"
    return "slow"
