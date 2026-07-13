"""
Dual-brain message bus.

In production, the fast and smart brains live in different processes (or
different machines — the smart brain on the home-base desktop). They
communicate through Redis Streams: the fast brain XADDs work requests onto
`shims:tasks`, the smart brain consumes via XREADGROUP; the smart brain XADDs
progress and final answers onto `shims:progress:<task_id>` and `shims:results`,
which the fast brain (and the UI WebSocket) subscribe to.

For zero-infrastructure runs (CI, demo, tests), we provide an in-process
async-queue implementation with the same interface. Same code path either way.
"""
from .types import Bus, BusFactory, Message, ProgressEvent, TaskRequest, TaskResult
from .factory import make_bus

__all__ = ["Bus", "BusFactory", "Message", "ProgressEvent", "TaskRequest", "TaskResult", "make_bus"]
