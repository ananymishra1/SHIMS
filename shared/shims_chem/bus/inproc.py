"""In-process async bus — zero dependencies, same interface as Redis."""
from __future__ import annotations
import asyncio
from collections import defaultdict
from typing import AsyncIterator

from .types import Bus, ProgressEvent, TaskRequest, TaskResult


class InProcessBus(Bus):
    """Single-process pub/sub queues. Fine for the demo, tests, single-box mode."""

    def __init__(self) -> None:
        self._tasks: asyncio.PriorityQueue[tuple[int, float, TaskRequest]] = asyncio.PriorityQueue()
        self._progress: dict[str, asyncio.Queue[ProgressEvent | None]] = defaultdict(asyncio.Queue)
        self._results: dict[str, asyncio.Future[TaskResult]] = {}
        self._closed = False
        self._seq = 0

    async def submit_task(self, req: TaskRequest) -> None:
        if self._closed:
            return
        self._seq += 1
        await self._tasks.put((req.priority, self._seq, req))
        # Pre-create the result future so subscribers can await it
        loop = asyncio.get_event_loop()
        self._results.setdefault(req.task_id, loop.create_future())

    async def consume_tasks(self, consumer: str) -> AsyncIterator[TaskRequest]:
        while not self._closed:
            try:
                _, _, req = await asyncio.wait_for(self._tasks.get(), timeout=0.5)
                yield req
            except asyncio.TimeoutError:
                continue

    async def publish_progress(self, ev: ProgressEvent) -> None:
        await self._progress[ev.task_id].put(ev)

    async def publish_result(self, result: TaskResult) -> None:
        # Sentinel to close the progress stream
        await self._progress[result.task_id].put(None)
        fut = self._results.get(result.task_id)
        if fut is None or fut.done():
            loop = asyncio.get_event_loop()
            self._results[result.task_id] = loop.create_future()
            fut = self._results[result.task_id]
        if not fut.done():
            fut.set_result(result)

    async def subscribe_progress(self, task_id: str) -> AsyncIterator[ProgressEvent]:
        q = self._progress[task_id]
        while True:
            ev = await q.get()
            if ev is None:
                break
            yield ev

    async def get_result(self, task_id: str, timeout_s: float = 60.0) -> TaskResult | None:
        loop = asyncio.get_event_loop()
        fut = self._results.setdefault(task_id, loop.create_future())
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        self._closed = True
