from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List


@dataclass(frozen=True)
class ShimsAgent:
    id: str
    name: str
    purpose: str
    tools: List[str]
    blocked_actions: List[str]
    preferred_model_role: str
    latency_budget_ms: int
    approval_level: str
    status: str = "ready"
    specialist_model_env: str | None = None  # e.g. SHIMS_CODER_MODEL


AGENTS: Dict[str, ShimsAgent] = {
    "supervisor": ShimsAgent(
        id="supervisor",
        name="SHIMS Supervisor",
        purpose="Owns every user-visible turn, routes to tools/agents, and keeps one-answer-per-turn discipline.",
        tools=["intent.route", "memory.retrieve", "tool.dispatch", "telemetry.write"],
        blocked_actions=["gxp_final_approval", "unsafe_code_apply"],
        preferred_model_role="smart",
        specialist_model_env="SHIMS_SMART_MODEL",
        latency_budget_ms=1500,
        approval_level="normal",
    ),
    "voice": ShimsAgent(
        id="voice",
        name="Realtime Voice Agent",
        purpose="Handles wake, STT, half-duplex state, TTS, barge-in, and speech telemetry.",
        tools=["voice.listen", "voice.speak", "voice.profile"],
        blocked_actions=["clone_voice_without_consent"],
        preferred_model_role="fast",
        specialist_model_env="SHIMS_FAST_MODEL",
        latency_budget_ms=500,
        approval_level="normal",
    ),
    "search": ShimsAgent(
        id="search",
        name="Search Agent",
        purpose="Searches the internet only when freshness or external verification is required.",
        tools=["web.search", "web.health"],
        blocked_actions=["search_private_data_without_permission"],
        preferred_model_role="smart",
        specialist_model_env="SHIMS_SMART_MODEL",
        latency_budget_ms=4000,
        approval_level="normal",
    ),
    "media": ShimsAgent(
        id="media",
        name="Media Forge Agent",
        purpose="Generates image, audio, and video artifacts with verification before narration.",
        tools=["image.generate", "audio.generate", "video.generate", "artifact.verify"],
        blocked_actions=["fake_artifact_success"],
        preferred_model_role="creative",
        specialist_model_env="SHIMS_CREATIVE_MODEL",
        latency_budget_ms=120000,
        approval_level="normal",
    ),
    "documents": ShimsAgent(
        id="documents",
        name="Document Forge Agent",
        purpose="Creates PDF, DOCX, PPTX, reports, SOP drafts, and branded outputs.",
        tools=["pdf.generate", "ppt.generate", "docx.generate", "artifact.verify"],
        blocked_actions=["fake_artifact_success"],
        preferred_model_role="smart",
        specialist_model_env="SHIMS_SMART_MODEL",
        latency_budget_ms=30000,
        approval_level="normal",
    ),
    "code": ShimsAgent(
        id="code",
        name="Code Forge Agent",
        purpose="Writes, tests, and proposes code changes inside a sandbox.",
        tools=["code.write", "code.test", "evolution.propose"],
        blocked_actions=["direct_apply_without_tests", "modify_safety_harness"],
        preferred_model_role="coder",
        specialist_model_env="SHIMS_CODER_MODEL",
        latency_budget_ms=60000,
        approval_level="human_required",
    ),
    "memory": ShimsAgent(
        id="memory",
        name="Memory Agent",
        purpose="Stores, retrieves, edits, forgets, and consolidates user/company memories.",
        tools=["memory.save", "memory.search", "memory.forget", "memory.consolidate"],
        blocked_actions=["store_secret_without_permission"],
        preferred_model_role="fast",
        specialist_model_env="SHIMS_FAST_MODEL",
        latency_budget_ms=800,
        approval_level="normal",
    ),
    "rag": ShimsAgent(
        id="rag",
        name="RAG Knowledge Agent",
        purpose="Indexes local notes, conversation episodes, web research snippets, and generated artifacts into durable retrieval context.",
        tools=["rag.ingest", "rag.search", "context.pack", "source.rank"],
        blocked_actions=["leak_secret_context", "invent_source"],
        preferred_model_role="fast",
        specialist_model_env="SHIMS_FAST_MODEL",
        latency_budget_ms=1200,
        approval_level="normal",
    ),
    "research_synthesizer": ShimsAgent(
        id="research_synthesizer",
        name="Research Synthesizer Agent",
        purpose="Combines live web search, stored RAG, citations, and model reasoning into grounded research briefs.",
        tools=["web.search", "rag.search", "research.store", "citation.format"],
        blocked_actions=["cite_unread_source", "treat_unverified_web_as_gxp_truth"],
        preferred_model_role="smart",
        specialist_model_env="SHIMS_RESEARCH_MODEL",
        latency_budget_ms=45000,
        approval_level="normal",
    ),
    "capture_inbox": ShimsAgent(
        id="capture_inbox",
        name="Capture Inbox Agent",
        purpose="Turns shared links, notes, email snippets, and Gmail metadata into durable memory, RAG chunks, and follow-up tasks.",
        tools=["capture.share", "mailbox.import", "mailbox.digest", "rag.ingest"],
        blocked_actions=["read_private_mail_without_oauth_consent", "send_email_without_user_confirmation"],
        preferred_model_role="fast",
        specialist_model_env="SHIMS_FAST_MODEL",
        latency_budget_ms=1200,
        approval_level="normal",
    ),
    "business_operator": ShimsAgent(
        id="business_operator",
        name="Business Operator Agent",
        purpose="Coordinates campaigns, RFQs, documents, mailbox follow-ups, enterprise records, and media work into auditable business workflows.",
        tools=["mailbox.digest", "documents.generate", "media.generate", "enterprise.action", "task.plan"],
        blocked_actions=["regulated_final_approval", "payment_release", "send_campaign_without_review"],
        preferred_model_role="smart",
        specialist_model_env="SHIMS_SMART_MODEL",
        latency_budget_ms=45000,
        approval_level="human_required_for_external_send",
    ),
    "background_learning": ShimsAgent(
        id="background_learning",
        name="Background Learning Agent",
        purpose="Continuously reviews telemetry, episodes, tool failures, and feedback to produce lessons and safe improvement tasks.",
        tools=["telemetry.read", "memory.consolidate", "lesson.write", "evolution.propose"],
        blocked_actions=["direct_apply_without_human_approval", "modify_safety_harness"],
        preferred_model_role="fast",
        specialist_model_env="SHIMS_FAST_MODEL",
        latency_budget_ms=60000,
        approval_level="human_required_for_code",
    ),
    "safety_governor": ShimsAgent(
        id="safety_governor",
        name="Safety Governor Agent",
        purpose="Applies autonomy, GxP, secret-handling, and artifact-verification rules before actions are executed.",
        tools=["autonomy.check", "artifact.verify", "secret.redact", "policy.audit"],
        blocked_actions=[],
        preferred_model_role="fast",
        specialist_model_env="SHIMS_FAST_MODEL",
        latency_budget_ms=700,
        approval_level="gxp_human_required",
    ),
    "verifier": ShimsAgent(
        id="verifier",
        name="Verifier Agent",
        purpose="Checks files, hashes, tool results, model routing, and no-fake-success guarantees.",
        tools=["artifact.hash", "artifact.exists", "route.audit"],
        blocked_actions=[],
        preferred_model_role="fast",
        specialist_model_env="SHIMS_FAST_MODEL",
        latency_budget_ms=1000,
        approval_level="normal",
    ),
    "enterprise_bridge": ShimsAgent(
        id="enterprise_bridge",
        name="Enterprise Bridge Agent",
        purpose="Safely connects SHIMS personal agent to Enterprise workflows without taking regulated final approvals.",
        tools=["enterprise.query", "enterprise.action", "enterprise.status"],
        blocked_actions=["batch_release", "capa_closure", "oos_final_disposition", "change_control_approval"],
        preferred_model_role="smart",
        specialist_model_env="SHIMS_SMART_MODEL",
        latency_budget_ms=3000,
        approval_level="gxp_human_required",
    ),
    "rd": ShimsAgent(
        id="rd",
        name="R&D Brain Agent",
        purpose="Patent search, process synthesis, raw material pricing, yield prediction, purity testing, and research brief generation.",
        tools=["rd.patents", "rd.synthesize", "rd.pricing", "rd.yield", "rd.purity", "rd.research_brief"],
        blocked_actions=[],
        preferred_model_role="smart",
        specialist_model_env="SHIMS_RESEARCH_MODEL",
        latency_budget_ms=30000,
        approval_level="normal",
    ),
    "chemistry": ShimsAgent(
        id="chemistry",
        name="Chemistry Brain Agent",
        purpose="Symbolic chemistry verification: SMILES validation, hazard checks, retrosynthesis, reaction balance, ICH impurity rules, and FTO scoring via dual fast+smart brain.",
        tools=["chem.verify", "chem.reaction", "chem.retro", "chem.hazard", "chem.ich", "chem.fto"],
        blocked_actions=[],
        preferred_model_role="smart",
        specialist_model_env="SHIMS_CHEMISTRY_MODEL",
        latency_budget_ms=45000,
        approval_level="normal",
    ),
}


def list_agents() -> list[dict]:
    return [asdict(agent) for agent in AGENTS.values()]


def get_agent(agent_id: str) -> dict | None:
    agent = AGENTS.get(agent_id)
    return asdict(agent) if agent else None
