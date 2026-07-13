"""Neural Governor orchestrator — main entry point for governed AI interactions."""
from __future__ import annotations

import time
from typing import Any, AsyncGenerator, Optional

from . import DriftReport, IntentCategory, ResponseLineage, RoutingDecision, new_lineage_id
from .circuit_breaker import can_use, record_failure, record_success
from .context_retriever import retrieve_unified_context
from .drift_detector import compute_drift
from .event_bus import publish
from .hardware_profiler import profile_hardware
from .intent_classifier import classify_intent_with_slm
from .lineage import record_lineage, compute_trust_score
from .model_router import route_model
from .personal_layer import ensure_profile, format_profile_context, learn_from_interaction
from .resource_governor import request_end, request_start, should_throttle, recommend_downgrade


class NeuralGovernor:
    """Main governor class. Instantiate once and reuse."""

    def __init__(self, user_id: int = 0, session_id: str = "default"):
        self.user_id = user_id
        self.session_id = session_id
        self.profile = ensure_profile(user_id)

    async def chat(
        self,
        prompt: str,
        system: str = "",
        provider_preference: Optional[str] = None,
        model_preference: Optional[str] = None,
        allowed_providers: Optional[list[str]] = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Execute a full governed chat cycle.

        Returns dict with keys: lineage_id, output, drift_report, routing, trust_score, latency_ms
        """
        start_time = time.time()
        lineage_id = new_lineage_id()

        # 1. Resource check
        if should_throttle():
            downgrade = recommend_downgrade()
            if downgrade:
                model_preference = downgrade

        request_start()
        try:
            # 2. Intent classification
            intent, intent_conf = await classify_intent_with_slm(prompt)

            # 3. Context retrieval
            ctx = retrieve_unified_context(
                query=prompt,
                user_id=self.user_id,
                session_id=self.session_id,
            )
            context_text = ctx["context"]
            context_sources = ctx["sources_used"]

            # 4. Model routing
            routing = route_model(
                intent=intent,
                provider_preference=provider_preference,
                model_preference=model_preference,
                allowed_providers=allowed_providers,
                prefer_free=True,
                prefer_speed=(intent == IntentCategory.CONVERSATION),
            )

            # 5. Circuit breaker check
            if not can_use(routing.provider):
                # Use fallback
                if routing.fallback_chain:
                    fb = routing.fallback_chain[0]
                    routing = RoutingDecision(
                        provider=fb["provider"],
                        model=fb["model"],
                        reason=f"circuit_breaker_fallback:{routing.provider}",
                        fallback_chain=routing.fallback_chain[1:],
                    )

            # 6. Build enriched prompt with context
            enriched_prompt = prompt
            if context_text:
                enriched_prompt = f"{context_text}\n\nUser request: {prompt}"

            # Inject personal profile into system prompt
            profile_ctx = format_profile_context(self.profile)
            full_system = system or "You are SHIMS, a careful local-first AI operating system."
            if profile_ctx:
                full_system = f"{full_system}\n\n{profile_ctx}"

            # 7. Generate draft
            from shared.ai import ask_ai
            draft_result = await ask_ai(enriched_prompt, system=full_system, provider=routing.provider, model=routing.model, feature='governor')
            draft_output = draft_result.text

            # Track provider success/failure for circuit breaker
            if draft_result.provider != "fallback":
                record_success(draft_result.provider)
            else:
                record_failure(routing.provider)

            # 8. Drift detection
            drift = compute_drift(
                prompt=prompt,
                context=context_text,
                output=draft_output,
                intent=intent,
                profile=self.profile,
                expected_role=self.profile.communication_tone,
            )

            final_output = draft_output
            arbitrator_used = False
            tools_used: list[str] = []

            # 9. Arbitration if drift detected
            if drift.triggered:
                final_output, arbitrator_used, tools_used = await self._arbitrate(
                    prompt=prompt,
                    draft=draft_output,
                    drift=drift,
                    context=context_text,
                    routing=routing,
                    allowed_providers=allowed_providers,
                )

            # 10. Build lineage
            latency_ms = int((time.time() - start_time) * 1000)
            lineage = ResponseLineage(
                lineage_id=lineage_id,
                timestamp=datetime_now(),
                user_id=self.user_id,
                session_id=self.session_id,
                intent=intent,
                routing_decision=routing,
                context_sources=context_sources,
                draft_output=draft_output,
                drift_report=drift,
                arbitrator_used=arbitrator_used,
                tools_used=tools_used,
                final_output=final_output,
                latency_ms=latency_ms,
                trust_score=0.0,
                action_ledger_hash="",
            )

            # Compute trust score
            lineage.trust_score = compute_trust_score(lineage_id)
            lineage.action_ledger_hash = self._hash_lineage(lineage)

            # Persist
            record_lineage(lineage)

            # 11. Record to omni brain episodes
            try:
                from shared.omni_brain import record_episode
                record_episode(
                    session_id=self.session_id,
                    user_text=prompt,
                    assistant_text=final_output,
                    route=f"governor:{intent.value}",
                    agent="neural_governor",
                    provider=routing.provider,
                    model=routing.model,
                    quality=lineage.trust_score,
                    metadata={"lineage_id": lineage_id, "drift": drift.composite},
                )
            except Exception:
                pass

            return {
                "lineage_id": lineage_id,
                "output": final_output,
                "intent": intent.value,
                "drift_report": drift.to_dict(),
                "routing": routing.to_dict(),
                "trust_score": lineage.trust_score,
                "latency_ms": latency_ms,
                "arbitrator_used": arbitrator_used,
                "tools_used": tools_used,
                "context_sources": context_sources,
            }

        finally:
            request_end()

    async def _arbitrate(
        self,
        prompt: str,
        draft: str,
        drift: DriftReport,
        context: str,
        routing: RoutingDecision,
        allowed_providers: Optional[list[str]] = None,
    ) -> tuple[str, bool, list[str]]:
        """Run arbitrator correction. Returns (corrected_output, was_used, tools_used)."""
        arbitrator_model = "gemma3:1b"  # smallest fast model
        arbitrator_provider = "ollama"

        # If ollama not available, use same provider
        if not can_use("ollama"):
            arbitrator_provider = routing.provider
            arbitrator_model = routing.model

        # Build arbitrator prompt
        arb_prompt = (
            f"The following AI draft has quality issues. Please correct it.\n\n"
            f"Original request: {prompt}\n\n"
            f"Context: {context[:1000]}\n\n"
            f"Draft output: {draft}\n\n"
            f"Issues detected: {', '.join(drift.signals_triggered)}\n\n"
            f"Please provide a corrected response that addresses these issues."
        )

        from shared.ai import ask_ai
        try:
            arb_result = await ask_ai(arb_prompt, system="You are a quality arbitrator. Fix errors and improve responses.", provider=arbitrator_provider, model=arbitrator_model, feature='governor')
            corrected = arb_result.text
            if corrected and len(corrected) > 20:
                return corrected, True, ["arbitrator"]
        except Exception:
            pass

        # If arbitrator fails, try tool verification for specific issues
        tools_used: list[str] = []
        if "tool_dependency" in drift.signals_triggered:
            corrected = await self._invoke_tools_for_prompt(prompt, draft)
            if corrected != draft:
                tools_used.append("tool_router")
                return corrected, True, tools_used

        return draft, False, []

    async def _invoke_tools_for_prompt(self, prompt: str, draft: str) -> str:
        """Attempt to invoke tools to improve the response."""
        # Simple heuristic-based tool invocation
        prompt_lower = prompt.lower()

        if any(w in prompt_lower for w in ["web", "search", "internet", "news", "current"]):
            try:
                from shared.web_search import web_search
                results = web_search(prompt)
                if results:
                    snippets = "\n".join(f"- {r.get('title', '')}: {r.get('snippet', '')}" for r in results[:3])
                    return f"Based on web search results:\n{snippets}\n\n{draft}"
            except Exception:
                pass

        if any(w in prompt_lower for w in ["document", "pdf", "file", "ingest"]):
            try:
                from shared.omni_brain import retrieve_context
                ctx = retrieve_context(prompt, limit=5)
                if ctx.get("context_text"):
                    return f"Relevant documents found:\n{ctx['context_text'][:1000]}\n\n{draft}"
            except Exception:
                pass

        return draft

    def _hash_lineage(self, lineage: ResponseLineage) -> str:
        import hashlib, json
        data = {
            "lineage_id": lineage.lineage_id,
            "user_id": lineage.user_id,
            "intent": lineage.intent.value,
            "provider": lineage.routing_decision.provider,
            "model": lineage.routing_decision.model,
            "drift": lineage.drift_report.composite if lineage.drift_report else 0.0,
            "timestamp": lineage.timestamp.isoformat(),
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:32]

    async def feedback(self, lineage_id: str, rating: int, notes: str = "") -> bool:
        """Record user feedback for a lineage."""
        from .lineage import add_feedback
        result = add_feedback(lineage_id, rating, notes)
        if result:
            learn_from_interaction(self.user_id, "", "", feedback_rating=rating, feedback_notes=notes)
        return result


def datetime_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
