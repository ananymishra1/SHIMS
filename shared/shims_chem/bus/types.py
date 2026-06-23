"""Bus protocol — implemented by both Redis and in-process backends."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable
import time
import uuid


@dataclass
class Message:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = field(default_factory=time.time)
    kind: str = "message"          # task | progress | result | log | cancel
    task_id: str = ""              # which task this message belongs to
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskRequest:
    task_id: str
    user_text: str
    intent: str                    # "retrosynthesis" | "analysis" | "free_text" | ...
    context: dict[str, Any] = field(default_factory=dict)
    priority: int = 5              # lower = higher priority


@dataclass
class ProgressEvent:
    task_id: str
    stage: str                     # "queued" | "thinking" | "tool_call" | "verifying" | "drafting"
    note: str = ""
    partial: str = ""              # streamed token deltas
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    task_id: str
    ok: bool
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0


class Bus(ABC):
    """Abstract bus. Async everywhere."""

    @abstractmethod
    async def submit_task(self, req: TaskRequest) -> None: ...

    @abstractmethod
    async def consume_tasks(self, consumer: str) -> AsyncIterator[TaskRequest]:
        if False:
            yield   # type: ignore[unreachable]

    @abstractmethod
    async def publish_progress(self, ev: ProgressEvent) -> None: ...

    @abstractmethod
    async def publish_result(self, result: TaskResult) -> None: ...

    @abstractmethod
    async def subscribe_progress(self, task_id: str) -> AsyncIterator[ProgressEvent]:
        if False:
            yield   # type: ignore[unreachable]

    @abstractmethod
    async def get_result(self, task_id: str, timeout_s: float = 60.0) -> TaskResult | None: ...

    @abstractmethod
    async def close(self) -> None: ...


BusFactory = Callable[[], Bus]
