"""Intent classifier — rule-based fast path + SLM validation for ambiguous cases."""
from __future__ import annotations

import re
from typing import Any, Optional

from . import IntentCategory

# Fast-path keyword patterns (regex -> category)
RULE_PATTERNS = [
    (r"\b(write|generate|create)\s+(code|script|function|class|program|app)\b", IntentCategory.CODE_GENERATION),
    (r"\b(cod(e|ing)|python|javascript|java|c\+\+|rust|go\b|typescript)\b", IntentCategory.CODE_GENERATION),
    (r"\b(format|style|template|pdf|docx|document|report|sop|coa|certificate)\b", IntentCategory.DOCUMENT_FORMAT),
    (r"\b(ingest|upload|index|scan|read|parse|extract)\s+(folder|file|document|pdf|docx)\b", IntentCategory.DOCUMENT_INGEST),
    (r"\b(analy(z|s)e|chart|graph|plot|statistic|trend|correlation|data)\b", IntentCategory.DATA_ANALYSIS),
    (r"\b(batch|bmr|mes|production|manufactur|work order|wo\b|plan)\b", IntentCategory.MANUFACTURING_QUERY),
    (r"\b(equipment|machine|reactor|dryer|mill|blender|status|maintenance|cleaning)\b", IntentCategory.EQUIPMENT_QUERY),
    (r"\b(qc|quality|assay|purity|impurity|test result|oos|coa|stability|validation)\b", IntentCategory.QUALITY_CONTROL),
    (r"\b(image|photo|picture|video|audio|voice|speech|multimodal|vision)\b", IntentCategory.MULTIMODAL),
    (r"\b(restart|shutdown|update|upgrade|patch|setting|config|permission|user|role)\b", IntentCategory.SYSTEM_COMMAND),
    (r"\b(research|experiment|reaction|synthesis|rd\b|r&d|patent|molecule|formulation)\b", IntentCategory.RESEARCH),
    (r"\b(admin|approve|reject|audit|log|backup|restore|evolution|proposal)\b", IntentCategory.ADMIN),
]

# Boosters — if these are present, increase confidence
BOOSTER_KEYWORDS = {
    IntentCategory.CODE_GENERATION: ["function", "class", "api", "endpoint", "debug", "error", "exception"],
    IntentCategory.DOCUMENT_FORMAT: ["header", "footer", "watermark", "page", "margin", "font"],
    IntentCategory.DOCUMENT_INGEST: ["folder", "directory", "batch upload", "bulk"],
    IntentCategory.DATA_ANALYSIS: ["csv", "excel", "dataset", "dataframe", "sql", "query"],
    IntentCategory.MANUFACTURING_QUERY: ["batch no", "batch number", "mrp", "bom", "recipe"],
    IntentCategory.EQUIPMENT_QUERY: ["calibration", "iq", "oq", "pq", "qualification"],
    IntentCategory.QUALITY_CONTROL: ["limit", "specification", "spec", "pass", "fail", "retest"],
    IntentCategory.MULTIMODAL: ["generate image", "create video", "transcribe", "caption"],
    IntentCategory.SYSTEM_COMMAND: ["restart server", "stop", "start", "port", "firewall"],
    IntentCategory.RESEARCH: ["literature", "prior art", "novel", "hypothesis", "doi"],
    IntentCategory.ADMIN: ["permission matrix", "role access", "ai quota", "evolution queue"],
}


def classify_intent(text: str) -> tuple[IntentCategory, float]:
    """Fast rule-based intent classification with confidence score.

    Returns (category, confidence) where confidence is 0.0-1.0.
    Confidence >= 0.7 is considered reliable.
    """
    text_lower = text.lower()
    scores: dict[IntentCategory, float] = {}

    for pattern, category in RULE_PATTERNS:
        if re.search(pattern, text_lower):
            scores[category] = scores.get(category, 0.0) + 0.5

    for category, boosters in BOOSTER_KEYWORDS.items():
        for word in boosters:
            if word in text_lower:
                scores[category] = scores.get(category, 0.0) + 0.15

    if not scores:
        return IntentCategory.CONVERSATION, 0.3

    best = max(scores, key=lambda k: scores[k])
    best_score = min(scores[best], 1.0)
    return best, best_score


async def classify_intent_with_slm(text: str, provider: Optional[str] = None, model: Optional[str] = None) -> tuple[IntentCategory, float]:
    """Two-tier classification: rule-based first, SLM validation if ambiguous."""
    category, confidence = classify_intent(text)
    if confidence >= 0.7:
        return category, confidence

    # Ambiguous — ask small model
    try:
        from shared.ai import ask_ai
        prompt = (
            f"Classify the user request into exactly one category.\n"
            f"Categories: {', '.join(c.value for c in IntentCategory)}\n"
            f"Request: {text}\n"
            f"Respond with ONLY the category name."
        )
        result = await ask_ai(prompt, system="You are an intent classifier. Respond with only the category name.", provider=provider, model=model, feature='router')
        raw = result.text.strip().lower().replace(" ", "_")
        for cat in IntentCategory:
            if cat.value == raw or raw in cat.value:
                return cat, max(confidence, 0.6)
    except Exception:
        pass

    return category, confidence
