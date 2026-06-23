"""Memory store types."""
from __future__ import annotations
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EpisodicRecord:
    task_id: str
    ts: float = field(default_factory=time.time)
    user_text: str = ""
    intent: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    elapsed_s: float = 0.0


@dataclass
class SemanticConcept:
    concept_id: str
    name: str
    body: str
    confidence: float = 0.7
    learned_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    source_task_ids: list[str] = field(default_factory=list)

    @classmethod
    def new(cls, name: str, body: str, **kw: Any) -> "SemanticConcept":
        return cls(concept_id=f"cn-{uuid.uuid4().hex[:10]}", name=name, body=body, **kw)


class MemoryStore(ABC):
    @abstractmethod
    def write_episode(self, record: EpisodicRecord) -> None: ...

    @abstractmethod
    def recent_episodes(self, limit: int = 20) -> list[EpisodicRecord]: ...

    @abstractmethod
    def get_episode(self, task_id: str) -> EpisodicRecord | None: ...

    @abstractmethod
    def write_concept(self, concept: SemanticConcept) -> None: ...

    @abstractmethod
    def search_concepts(self, query: str, limit: int = 5) -> list[SemanticConcept]: ...

    @abstractmethod
    def close(self) -> None: ...
