"""6-Signal Drift Detector — scores draft outputs before delivery."""
from __future__ import annotations

import math
import re
from typing import Any, Optional

from . import DriftReport, IntentCategory, PersonalProfile

# Signal weights (configurable per role)
DEFAULT_WEIGHTS = {
    "contradiction": 0.20,
    "hallucination_risk": 0.20,
    "tool_dependency": 0.10,
    "user_memory_mismatch": 0.15,
    "role_mismatch": 0.15,
    "task_completion": 0.20,
}


def _token_overlap(a: str, b: str) -> float:
    """Simple token overlap ratio."""
    a_toks = set(re.findall(r"[a-z0-9]{3,}", a.lower()))
    b_toks = set(re.findall(r"[a-z0-9]{3,}", b.lower()))
    if not a_toks or not b_toks:
        return 0.0
    inter = a_toks & b_toks
    return len(inter) / max(len(a_toks), len(b_toks))


def _estimate_confidence_entropy(text: str) -> float:
    """Rough proxy for hallucination risk: factual claims without qualifiers."""
    # Count definitive statements vs hedged statements
    definitive = len(re.findall(r"\b(is|are|was|were|will be|must|always|never)\b", text.lower()))
    hedged = len(re.findall(r"\b(may|might|could|possibly|likely|probably|suggest|appear|seem)\b", text.lower()))
    total = definitive + hedged
    if total == 0:
        return 0.0
    # High definitive + low hedge = higher risk if no sources cited
    risk = (definitive / max(total, 1)) - (hedged / max(total, 1) * 0.5)
    return max(0.0, min(1.0, risk))


def detect_contradiction(context: str, output: str) -> float:
    """Score: does output contradict the provided context?"""
    if not context or not output:
        return 0.0
    # Simple heuristic: if output makes strong claims not in context
    # and context is substantial, flag for review
    overlap = _token_overlap(context, output)
    if len(context) > 200 and overlap < 0.05:
        return 0.6  # low overlap with substantial context is suspicious
    return max(0.0, 1.0 - overlap) * 0.3


def detect_hallucination_risk(context: str, output: str) -> float:
    """Score: likelihood of hallucination."""
    risk = _estimate_confidence_entropy(output)
    # If output has specific numbers/dates not in context, increase risk
    nums_out = set(re.findall(r"\b\d{3,}\b", output))
    nums_ctx = set(re.findall(r"\b\d{3,}\b", context))
    novel_nums = nums_out - nums_ctx
    if novel_nums and len(context) > 100:
        risk += 0.15
    return min(1.0, risk)


def detect_tool_dependency(intent: IntentCategory, output: str) -> float:
    """Score: does the output suggest a tool should have been used?"""
    if intent in {IntentCategory.DATA_ANALYSIS, IntentCategory.DOCUMENT_INGEST, IntentCategory.MULTIMODAL}:
        # These intents often require tools; not using them is suspicious
        if "i cannot" in output.lower() or "i don't have access" in output.lower():
            return 0.7
    # If output contains URL-like strings but no tool was used
    if re.search(r"https?://\S+", output) and "web search" not in output.lower():
        return 0.3
    return 0.0


def detect_user_memory_mismatch(output: str, profile: Optional[PersonalProfile] = None) -> float:
    """Score: does output mismatch user's known style/preferences?"""
    if not profile:
        return 0.0
    mismatch = 0.0
    # Writing style
    style = profile.writing_style.lower()
    output_lower = output.lower()
    if style == "formal":
        if any(w in output_lower for w in ["lol", "haha", "btw", "gonna", "wanna"]):
            mismatch += 0.4
    elif style == "casual":
        if len(output) > 200 and not any(w in output_lower for w in ["hey", "btw", "sure"]):
            mismatch += 0.2
    # Technical depth
    if profile.technical_depth <= 2:
        jargon = ["backpropagation", "latent space", "tensor", "embedding", "gradient", "autoregressive"]
        if sum(1 for j in jargon if j in output_lower) > 2:
            mismatch += 0.3
    return min(1.0, mismatch)


def detect_role_mismatch(output: str, expected_role: str = "professional") -> float:
    """Score: does output tone mismatch expected role?"""
    output_lower = output.lower()
    mismatch = 0.0
    if expected_role == "professional":
        if any(w in output_lower for w in ["dude", "bro", "mate", "lol", "haha"]):
            mismatch += 0.5
    elif expected_role == "friendly":
        if len(output) > 100 and output.count("please") == 0 and output.count("thank") == 0:
            mismatch += 0.1
    return min(1.0, mismatch)


def detect_task_completion(prompt: str, output: str) -> float:
    """Score: does output fully answer the prompt?"""
    # Heuristic: if prompt asks a question and output is very short
    prompt_lower = prompt.lower()
    is_question = any(prompt_lower.strip().startswith(w) for w in ["what", "how", "why", "when", "where", "who", "which", "can", "does", "is", "are"])
    if is_question and len(output) < 30:
        return 0.3
    # If prompt asks for multiple items and output seems short
    item_requests = len(re.findall(r"\b(list|enumerate|steps|points|items|examples?)\b", prompt_lower))
    if item_requests > 0:
        lines = [l for l in output.splitlines() if l.strip()]
        if len(lines) < item_requests * 2:
            return 0.4
    return 0.8  # default assume okay


def compute_drift(
    prompt: str,
    context: str,
    output: str,
    intent: IntentCategory,
    profile: Optional[PersonalProfile] = None,
    expected_role: str = "professional",
    weights: Optional[dict[str, float]] = None,
    threshold: float = 0.38,
) -> DriftReport:
    """Compute full 6-signal drift report."""
    w = weights or DEFAULT_WEIGHTS

    contradiction = detect_contradiction(context, output)
    hallucination_risk = detect_hallucination_risk(context, output)
    tool_dependency = detect_tool_dependency(intent, output)
    user_memory_mismatch = detect_user_memory_mismatch(output, profile)
    role_mismatch = detect_role_mismatch(output, expected_role)
    task_completion = detect_task_completion(prompt, output)

    # Task completion is inverted (higher is better, so lower drift)
    task_drift = 1.0 - task_completion

    composite = (
        w.get("contradiction", 0.2) * contradiction +
        w.get("hallucination_risk", 0.2) * hallucination_risk +
        w.get("tool_dependency", 0.1) * tool_dependency +
        w.get("user_memory_mismatch", 0.15) * user_memory_mismatch +
        w.get("role_mismatch", 0.15) * role_mismatch +
        w.get("task_completion", 0.2) * task_drift
    )

    signals_triggered = []
    if contradiction > 0.5:
        signals_triggered.append("contradiction")
    if hallucination_risk > 0.6:
        signals_triggered.append("hallucination_risk")
    if tool_dependency > 0.5:
        signals_triggered.append("tool_dependency")
    if user_memory_mismatch > 0.5:
        signals_triggered.append("user_memory_mismatch")
    if role_mismatch > 0.5:
        signals_triggered.append("role_mismatch")
    if task_drift > 0.5:
        signals_triggered.append("task_completion")

    return DriftReport(
        contradiction=round(contradiction, 4),
        hallucination_risk=round(hallucination_risk, 4),
        tool_dependency=round(tool_dependency, 4),
        user_memory_mismatch=round(user_memory_mismatch, 4),
        role_mismatch=round(role_mismatch, 4),
        task_completion=round(task_completion, 4),
        composite=round(composite, 4),
        threshold=threshold,
        triggered=composite > threshold,
        signals_triggered=signals_triggered,
    )
