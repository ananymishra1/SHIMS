"""SHIMS Omni Neural Governor v1.0

Local autonomous AI operating system using gated runtime cognitive governance,
memory-conditioned arbitration, tool-verification, and sandboxed self-modification.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class IntentCategory(str, Enum):
    CONVERSATION = "conversation"
    CODE_GENERATION = "code_generation"
    DOCUMENT_FORMAT = "document_format"
    DOCUMENT_INGEST = "document_ingest"
    DATA_ANALYSIS = "data_analysis"
    MANUFACTURING_QUERY = "manufacturing_query"
    EQUIPMENT_QUERY = "equipment_query"
    QUALITY_CONTROL = "quality_control"
    MULTIMODAL = "multimodal"
    SYSTEM_COMMAND = "system_command"
    RESEARCH = "research"
    ADMIN = "admin"
    UNKNOWN = "unknown"


class ProposalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEPLOYED = "deployed"
    ROLLED_BACK = "rolled_back"


@dataclass
class HardwareProfile:
    total_ram_gb: float = 0.0
    vram_gb: float = 0.0
    cpu_cores: int = 0
    cuda_available: bool = False
    cuda_version: str = ""
    internet_available: bool = True
    battery_powered: bool = False
    disk_space_gb: float = 0.0
    platform: str = ""  # windows, linux, android, darwin

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_ram_gb": self.total_ram_gb,
            "vram_gb": self.vram_gb,
            "cpu_cores": self.cpu_cores,
            "cuda_available": self.cuda_available,
            "cuda_version": self.cuda_version,
            "internet_available": self.internet_available,
            "battery_powered": self.battery_powered,
            "disk_space_gb": self.disk_space_gb,
            "platform": self.platform,
        }


@dataclass
class ModelCapability:
    text: bool = True
    code: bool = False
    reasoning: bool = False
    creativity: bool = False
    vision: bool = False
    audio: bool = False
    multimodal: bool = False
    speed_rating: int = 3  # 1-5, higher is faster
    quality_rating: int = 3  # 1-5, higher is better
    offline_capable: bool = True


@dataclass
class ModelInfo:
    name: str
    provider: str
    params_b: float = 0.0  # billions
    quantization: str = ""
    vram_required_gb: float = 0.0
    ram_required_gb: float = 0.0
    capabilities: ModelCapability = field(default_factory=ModelCapability)
    cost_per_1k_tokens: float = 0.0  # 0.0 = free/local
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "params_b": self.params_b,
            "quantization": self.quantization,
            "vram_required_gb": self.vram_required_gb,
            "ram_required_gb": self.ram_required_gb,
            "capabilities": self.capabilities.__dict__,
            "cost_per_1k_tokens": self.cost_per_1k_tokens,
            "aliases": self.aliases,
        }


@dataclass
class RoutingDecision:
    provider: str
    model: str
    reason: str
    fallback_chain: list[dict[str, str]] = field(default_factory=list)
    estimated_latency_ms: int = 0
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "reason": self.reason,
            "fallback_chain": self.fallback_chain,
            "estimated_latency_ms": self.estimated_latency_ms,
            "confidence": self.confidence,
        }


@dataclass
class DriftReport:
    contradiction: float = 0.0
    hallucination_risk: float = 0.0
    tool_dependency: float = 0.0
    user_memory_mismatch: float = 0.0
    role_mismatch: float = 0.0
    task_completion: float = 0.0
    composite: float = 0.0
    threshold: float = 0.38
    triggered: bool = False
    signals_triggered: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction": self.contradiction,
            "hallucination_risk": self.hallucination_risk,
            "tool_dependency": self.tool_dependency,
            "user_memory_mismatch": self.user_memory_mismatch,
            "role_mismatch": self.role_mismatch,
            "task_completion": self.task_completion,
            "composite": self.composite,
            "threshold": self.threshold,
            "triggered": self.triggered,
            "signals_triggered": self.signs_triggered,
        }


@dataclass
class ResponseLineage:
    lineage_id: str
    timestamp: datetime
    user_id: int
    session_id: str
    intent: IntentCategory
    routing_decision: RoutingDecision
    context_sources: list[str] = field(default_factory=list)
    draft_output: str = ""
    drift_report: Optional[DriftReport] = None
    arbitrator_used: bool = False
    tools_used: list[str] = field(default_factory=list)
    final_output: str = ""
    latency_ms: int = 0
    trust_score: float = 0.0
    action_ledger_hash: str = ""
    feedback_rating: Optional[int] = None
    feedback_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "lineage_id": self.lineage_id,
            "timestamp": self.timestamp.isoformat(),
            "user_id": self.user_id,
            "session_id": self.session_id,
            "intent": self.intent.value,
            "routing_decision": self.routing_decision.to_dict(),
            "context_sources": self.context_sources,
            "draft_output": self.draft_output,
            "drift_report": self.drift_report.to_dict() if self.drift_report else None,
            "arbitrator_used": self.arbitrator_used,
            "tools_used": self.tools_used,
            "final_output": self.final_output,
            "latency_ms": self.latency_ms,
            "trust_score": self.trust_score,
            "action_ledger_hash": self.action_ledger_hash,
            "feedback_rating": self.feedback_rating,
            "feedback_notes": self.feedback_notes,
        }


@dataclass
class PersonalProfile:
    user_id: int
    writing_style: str = "formal"
    preferred_formats: list[str] = field(default_factory=list)
    sentence_length: str = "medium"
    technical_depth: int = 3
    factory_context: dict[str, Any] = field(default_factory=dict)
    rd_habits: list[str] = field(default_factory=list)
    document_patterns: list[str] = field(default_factory=list)
    workflow_sequences: list[dict[str, Any]] = field(default_factory=list)
    active_projects: list[str] = field(default_factory=list)
    communication_tone: str = "professional"
    correction_history: list[dict[str, Any]] = field(default_factory=list)
    peak_hours: list[int] = field(default_factory=list)
    learning_enabled: bool = True
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "writing_style": self.writing_style,
            "preferred_formats": self.preferred_formats,
            "sentence_length": self.sentence_length,
            "technical_depth": self.technical_depth,
            "factory_context": self.factory_context,
            "rd_habits": self.rd_habits,
            "document_patterns": self.document_patterns,
            "workflow_sequences": self.workflow_sequences,
            "active_projects": self.active_projects,
            "communication_tone": self.communication_tone,
            "correction_history": self.correction_history,
            "peak_hours": self.peak_hours,
            "learning_enabled": self.learning_enabled,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def new_lineage_id() -> str:
    return str(uuid.uuid4())
