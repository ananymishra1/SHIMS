"""
Redis Streams bus.

Streams used:
  shims:tasks                   — work queue (XADD on submit, XREADGROUP on consume)
  shims:progress:<task_id>      — per-task streaming progress (token deltas, stage updates)
  shims:results                 — final results (hash keyed by task_id)

Consumer group: shims-workers (smart brains join this group).
"""
from __future__ import annotations
import asyncio
import json
import time
from typing import AsyncIterator

from .types import Bus, ProgressEvent, TaskRequest, TaskResult


_STREAM_TASKS = "shims:tasks"
_GROUP = "shims-workers"


class RedisBus(Bus):
    def __init__(self, url: str) -> None:
        try:
            import redis.asyncio as aioredis      # type: ignore
        except ImportError as e:                  # pragma: no cover
            raise RuntimeError(
                "redis not installed; install with: pip install 'shims-chem[bus]'"
            ) from e
        self._aioredis = aioredis
        self._url = url
        self._r = aioredis.from_url(url, decode_responses=True)
        self._ensured = False

    async def _ensure_group(self) -> None:
        if self._ensured:
            return
        try:
            await self._r.xgroup_create(_STREAM_TASKS, _GROUP, id="0", mkstream=True)
        except Exception as e:    # BUSYGROUP if it exists — fine
            if "BUSYGROUP" not in str(e):
                raise
        self._ensured = True

    async def submit_task(self, req: TaskRequest) -> None:
        await self._ensure_group()
        await self._r.xadd(
            _STREAM_TASKS,
            {"json": json.dumps(req.__dict__, default=str), "priority": str(req.priority)},
            maxlen=10_000,
            approximate=True,
        )

    async def consume_tasks(self, consumer: str) -> AsyncIterator[TaskRequest]:
        await self._ensure_group()
        while True:
            resp = await self._r.xreadgroup(
                _GROUP, consumer, {_STREAM_TASKS: ">"}, count=1, block=500,
            )
            if not resp:
                await asyncio.sleep(0.05)
                continue
            for _stream, entries in resp:
                for msg_id, fields in entries:
                    try:
                        d = json.loads(fields["json"])
                        yield TaskRequest(**d)
                    finally:
                        await self._r.xack(_STREAM_TASKS, _GROUP, msg_id)

    async def publish_progress(self, ev: ProgressEvent) -> None:
        await self._r.xadd(
            f"shims:progress:{ev.task_id}",
            {"json": json.dumps(ev.__dict__)},
            maxlen=2000, approximate=True,
        )

    async def publish_result(self, result: TaskResult) -> None:
        await self._r.hset(
            "shims:results",
            mapping={result.task_id: json.dumps(result.__dict__, default=str)},
        )
        # Sentinel on the progress stream so subscribers know it's done
        await self._r.xadd(
            f"shims:progress:{result.task_id}",
            {"done": "1"}, maxlen=2000, approximate=True,
        )

    async def subscribe_progress(self, task_id: str) -> AsyncIterator[ProgressEvent]:
        stream = f"shims:progress:{task_id}"
        last_id = "0"
        while True:
            resp = await self._r.xread({stream: last_id}, count=10, block=500)
            if not resp:
                continue
            for _s, entries in resp:
                for msg_id, fields in entries:
                    last_id = msg_id
                    if "done" in fields:
                        return
                    if "json" in fields:
                        yield ProgressEvent(**json.loads(fields["json"]))

    async def get_result(self, task_id: str, timeout_s: float = 60.0) -> TaskResult | None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            data = await self._r.hget("shims:results", task_id)
            if data:
                return TaskResult(**json.loads(data))
            await asyncio.sleep(0.2)
        return None

    async def close(self) -> None:
        try:
            await self._r.close()
        except Exception:
            pass
