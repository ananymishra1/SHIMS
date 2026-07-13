from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

LOG = Path("storage/telemetry/v15_turns.jsonl")


class TurnTimer:
    def __init__(self, source: str = "text"):
        self.turn_id = str(uuid4())
        self.source = source
        self.t0 = time.perf_counter()
        self.events = []

    def mark(self, name: str, **data):
        self.events.append({"name": name, "ms": int((time.perf_counter() - self.t0) * 1000), "data": data})

    def finish(self, route: str, ok: bool = True, **data):
        LOG.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "turn_id": self.turn_id,
            "source": self.source,
            "route": route,
            "ok": ok,
            "total_ms": int((time.perf_counter() - self.t0) * 1000),
            "events": self.events,
            "data": data,
            "created_at": time.time(),
        }
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload
