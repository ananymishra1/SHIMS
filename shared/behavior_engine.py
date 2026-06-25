"""SHIMS Behavior Engine — lightweight, local, on-CPU behavior learning.

This is the "small ML model = pattern detector, LLM = action executor" design:
a dependency-free behavior model that observes what the user does, learns
patterns incrementally, and feeds *predictions as context* into the LLM. The
LLM is never fine-tuned — it simply receives better instructions.

Why not per-user fine-tuning? It needs a GPU, is slow to adapt, overfits, and
must be retrained on every change. This engine instead runs four cheap models
that update in real time on CPU and can be reset without retraining anything:

  1. Sequence model   — first-order Markov "what usually follows action X".
  2. Temporal model   — hour-of-day / weekday propensity per action.
  3. Recency model    — exponentially-decayed frequency (what's hot now).
  4. Feedback model   — 👍/👎 reinforcement that boosts or suppresses actions.

Predictions carry a confidence in [0,1]. Callers act by threshold:

  >= 0.85  → auto-execute (with the usual approval gate for risky tools)
  0.70-85  → proactively suggest
  0.50-70  → offer quietly / rank
  < 0.50   → stay silent

Everything persists to a small JSON file so learning survives restarts.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import ROOT_DIR

_STATE_DIR = Path(ROOT_DIR) / "data" / "state" / "behavior"
_STATE_DIR.mkdir(parents=True, exist_ok=True)

# Confidence thresholds (single source of truth, also surfaced to the UI).
AUTO_THRESHOLD = 0.85
SUGGEST_THRESHOLD = 0.70
RANK_THRESHOLD = 0.50

# Half-life (seconds) for the recency model's exponential decay (~7 days).
_RECENCY_HALF_LIFE = 7 * 24 * 3600
_DECAY_LAMBDA = math.log(2) / _RECENCY_HALF_LIFE

_MAX_EVENTS = 2000  # ring buffer cap


@dataclass
class Event:
    """A single observed user/agent action."""
    action: str
    ts: float = field(default_factory=time.time)
    context: str = ""          # coarse situational tag (e.g. "morning", "repo:shims")
    outcome: str = "neutral"   # neutral | positive | negative (from feedback)

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "ts": self.ts, "context": self.context, "outcome": self.outcome}


@dataclass
class Prediction:
    action: str
    confidence: float
    reasons: list[str] = field(default_factory=list)

    @property
    def tier(self) -> str:
        if self.confidence >= AUTO_THRESHOLD:
            return "auto"
        if self.confidence >= SUGGEST_THRESHOLD:
            return "suggest"
        if self.confidence >= RANK_THRESHOLD:
            return "rank"
        return "silent"

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "confidence": round(self.confidence, 3),
                "tier": self.tier, "reasons": self.reasons}


def _now_bucket(ts: Optional[float] = None) -> tuple[int, int]:
    """Return (hour_of_day, weekday) for a timestamp."""
    lt = time.localtime(ts if ts is not None else time.time())
    return lt.tm_hour, lt.tm_wday


class BehaviorEngine:
    """Incremental, CPU-only behavior learner with JSON persistence."""

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id or "default"
        self.path = _STATE_DIR / f"{self.user_id}.json"
        self.events: list[Event] = []
        # Sequence: transitions[a][b] = count of a -> b
        self.transitions: dict[str, dict[str, float]] = {}
        # Temporal: hour_counts[action][hour] and day_counts[action][weekday]
        self.hour_counts: dict[str, list[float]] = {}
        self.day_counts: dict[str, list[float]] = {}
        # Recency: decayed_score[action] with last update time
        self.recency: dict[str, float] = {}
        self._recency_ts: float = time.time()
        # Feedback: reinforcement multiplier per action (starts at 1.0)
        self.feedback: dict[str, float] = {}
        self.totals: dict[str, float] = {}
        self._last_action: Optional[str] = None
        self._load()

    # ------------------------------------------------------------------ #
    # Observation / learning
    # ------------------------------------------------------------------ #
    def record(self, action: str, context: str = "", ts: Optional[float] = None) -> None:
        """Observe an action and update all four models incrementally."""
        action = (action or "").strip()
        if not action:
            return
        ts = ts if ts is not None else time.time()
        ev = Event(action=action, ts=ts, context=context)
        self.events.append(ev)
        while len(self.events) > _MAX_EVENTS:
            self.events.pop(0)

        # Sequence
        if self._last_action and self._last_action != action:
            row = self.transitions.setdefault(self._last_action, {})
            row[action] = row.get(action, 0.0) + 1.0
        self._last_action = action

        # Temporal
        hour, day = _now_bucket(ts)
        hc = self.hour_counts.setdefault(action, [0.0] * 24)
        hc[hour] += 1.0
        dc = self.day_counts.setdefault(action, [0.0] * 7)
        dc[day] += 1.0

        # Recency (decay existing, then add)
        self._decay_recency(ts)
        self.recency[action] = self.recency.get(action, 0.0) + 1.0

        self.totals[action] = self.totals.get(action, 0.0) + 1.0
        self.feedback.setdefault(action, 1.0)
        self._save()

    def _decay_recency(self, now: float) -> None:
        dt = max(0.0, now - self._recency_ts)
        if dt <= 0:
            return
        factor = math.exp(-_DECAY_LAMBDA * dt)
        for a in list(self.recency.keys()):
            self.recency[a] *= factor
        self._recency_ts = now

    def reinforce(self, action: str, positive: bool) -> None:
        """Apply 👍/👎 feedback. Bounded multiplier keeps it stable."""
        action = (action or "").strip()
        if not action:
            return
        cur = self.feedback.get(action, 1.0)
        cur *= 1.25 if positive else 0.8
        self.feedback[action] = max(0.25, min(3.0, cur))
        # tag the most recent matching event for auditability
        for ev in reversed(self.events):
            if ev.action == action:
                ev.outcome = "positive" if positive else "negative"
                break
        self._save()

    # ------------------------------------------------------------------ #
    # Prediction
    # ------------------------------------------------------------------ #
    def predict(self, ts: Optional[float] = None, top_k: int = 3) -> list[Prediction]:
        """Blend the four models into ranked, confidence-scored predictions."""
        ts = ts if ts is not None else time.time()
        self._decay_recency(ts)
        hour, day = _now_bucket(ts)

        candidates = set(self.totals.keys())
        if not candidates:
            return []

        # Component scores, each normalised to [0,1] across candidates.
        seq_raw: dict[str, float] = {}
        if self._last_action and self._last_action in self.transitions:
            row = self.transitions[self._last_action]
            tot = sum(row.values()) or 1.0
            for a, c in row.items():
                seq_raw[a] = c / tot

        temp_raw: dict[str, float] = {}
        for a in candidates:
            hc = self.hour_counts.get(a, [0.0] * 24)
            dc = self.day_counts.get(a, [0.0] * 7)
            h_score = hc[hour] / (sum(hc) or 1.0)
            d_score = dc[day] / (sum(dc) or 1.0)
            temp_raw[a] = 0.6 * h_score + 0.4 * d_score

        rec_tot = sum(self.recency.values()) or 1.0
        rec_raw = {a: self.recency.get(a, 0.0) / rec_tot for a in candidates}

        preds: list[Prediction] = []
        for a in candidates:
            seq = seq_raw.get(a, 0.0)
            temp = temp_raw.get(a, 0.0)
            rec = rec_raw.get(a, 0.0)
            fb = self.feedback.get(a, 1.0)

            # Weighted blend; sequence is the strongest signal when present.
            base = 0.45 * seq + 0.25 * temp + 0.30 * rec
            conf = base * fb
            # Squash into [0,1] so a feedback boost can't exceed 1.
            conf = 1.0 - math.exp(-2.2 * conf)

            reasons = []
            if seq > 0.25:
                reasons.append(f"usually follows '{self._last_action}'")
            if temp > 0.2:
                reasons.append(f"common around {hour:02d}:00")
            if rec > 0.2:
                reasons.append("done frequently lately")
            if fb > 1.05:
                reasons.append("you've reinforced this")
            elif fb < 0.95:
                reasons.append("you've discouraged this")

            preds.append(Prediction(action=a, confidence=round(conf, 4), reasons=reasons))

        preds.sort(key=lambda p: p.confidence, reverse=True)
        return preds[:top_k]

    def suggest(self, ts: Optional[float] = None) -> Optional[Prediction]:
        """Return the single best prediction if it clears the suggest bar."""
        preds = self.predict(ts=ts, top_k=1)
        if preds and preds[0].confidence >= SUGGEST_THRESHOLD:
            return preds[0]
        return None

    def to_context(self, ts: Optional[float] = None, max_items: int = 3) -> str:
        """Render predictions as a prompt block to inject into the LLM context.

        This is the bridge: the behavior model speaks, the LLM acts.
        """
        preds = [p for p in self.predict(ts=ts, top_k=max_items) if p.confidence >= RANK_THRESHOLD]
        if not preds:
            return ""
        lines = ["BEHAVIOR SIGNALS (predicted user intent — use to be proactively helpful, do not force):"]
        for p in preds:
            why = (" — " + "; ".join(p.reasons)) if p.reasons else ""
            lines.append(f"- {p.action} (confidence {p.confidence:.0%}, {p.tier}){why}")
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "events": len(self.events),
            "actions_known": len(self.totals),
            "thresholds": {"auto": AUTO_THRESHOLD, "suggest": SUGGEST_THRESHOLD, "rank": RANK_THRESHOLD},
            "top": [p.to_dict() for p in self.predict(top_k=5)],
        }

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _save(self) -> None:
        try:
            data = {
                "user_id": self.user_id,
                "events": [e.to_dict() for e in self.events[-_MAX_EVENTS:]],
                "transitions": self.transitions,
                "hour_counts": self.hour_counts,
                "day_counts": self.day_counts,
                "recency": self.recency,
                "recency_ts": self._recency_ts,
                "feedback": self.feedback,
                "totals": self.totals,
                "last_action": self._last_action,
            }
            self.path.write_text(json.dumps(data, default=str), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.events = [Event(**{k: e.get(k) for k in ("action", "ts", "context", "outcome")})
                       for e in data.get("events", []) if e.get("action")]
        self.transitions = data.get("transitions", {})
        self.hour_counts = data.get("hour_counts", {})
        self.day_counts = data.get("day_counts", {})
        self.recency = data.get("recency", {})
        self._recency_ts = data.get("recency_ts", time.time())
        self.feedback = data.get("feedback", {})
        self.totals = data.get("totals", {})
        self._last_action = data.get("last_action")

    def reset(self) -> None:
        """Wipe all learning for this user (no retraining needed — just clear)."""
        self.events.clear()
        self.transitions.clear()
        self.hour_counts.clear()
        self.day_counts.clear()
        self.recency.clear()
        self.feedback.clear()
        self.totals.clear()
        self._last_action = None
        self._save()


# Process-wide cache of per-user engines.
_engines: dict[str, BehaviorEngine] = {}


def get_behavior_engine(user_id: str = "default") -> BehaviorEngine:
    eng = _engines.get(user_id)
    if eng is None:
        eng = BehaviorEngine(user_id)
        _engines[user_id] = eng
    return eng
