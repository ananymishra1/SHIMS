"""Local privacy guard — scans text for sensitive pharma/GxP data.

Runs 100% offline (regex + keyword lists, no LLM call).
Used by the brain router to decide whether a prompt can safely go to a cloud provider.
"""
from __future__ import annotations

import re
from typing import Any


# Keywords that indicate HIGH sensitivity — never send to cloud
_HIGH_KEYWORDS: tuple[str, ...] = (
    # Batch / lot identifiers
    "batch no", "batch number", "lot no", "lot number", "batch id", "lot id",
    "batch:", "lot:", "bno", "lno",
    # COA / QC data
    "coa", "certificate of analysis", "assay result", "assay value",
    "purity result", "impurity profile", "chromatogram", "hplc result",
    # Proprietary / formulation
    "formulation", "master formula", "proprietary", "trade secret",
    "active ingredient", "api concentration", "excipient ratio",
    # Patient / PII
    "patient name", "patient id", "mrn", "medical record", "date of birth",
    "aadhaar", "pan number", "phone number", "email address",
    # Financial / vendor
    "vendor price", "quotation", "purchase price", "cost per unit",
    "invoice amount", "payment terms", "vendor discount",
    # Internal audit / deviations
    "deviation report", "capa", "corrective action", "preventive action",
    "audit finding", "internal audit", "regulatory finding", "483",
    "warning letter", "observation",
    # SOP / document control with internal refs
    "sop rev", "sop revision", "document control number", "change control",
    # Specific company identifiers
    "jk lifecare", "shims internal", "confidential",
)

# Keywords that indicate MEDIUM sensitivity — ask user or route based on mode
_MEDIUM_KEYWORDS: tuple[str, ...] = (
    "sop", "standard operating procedure",
    "batch record", "manufacturing record", "production log",
    "qc", "quality control", "qa", "quality assurance",
    "gmp", "good manufacturing practice", "glp", "gcp",
    "stability study", "accelerated stability", "retest date",
    "validation protocol", "qualification", "calibration certificate",
    "vendor", "supplier", "raw material", "rm code",
    "product code", "item code", "sku", "material code",
    "warehouse", "inventory count", "stock ledger",
    "equipment id", "instrument id", "asset tag",
)

# Regex patterns for structured sensitive data
_HIGH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"batch[\s_-]?(?:no|num|number|id)?[\s.:_-]?\w{3,}", re.I),
    re.compile(r"lot[\s_-]?(?:no|num|number|id)?[\s.:_-]?\w{3,}", re.I),
    re.compile(r"coa[\s.:_-]?\d{3,}", re.I),
    re.compile(r"\b(?:mrn|patient[\s_-]?id)[\s.:_-]?\d{4,}", re.I),
    re.compile(r"\b\d{4}[\s/-]?\d{4}[\s/-]?\d{4}\b"),  # Aadhaar-like
    re.compile(r"[a-z]{3}[pcahfatblj][a-z]\d{4}[a-z]", re.I),  # PAN-like
    re.compile(r"\+?\d{1,3}[\s-]?\d{5}[\s-]?\d{5}", re.I),  # Phone
    re.compile(r"(?:rs\.?|inr|₹)\s*\d{1,3}(?:,\d{3})+(?:\.\d{2})?", re.I),  # INR amounts
]

_LOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"image .*of|image banao|generate (?:an? )?image|create (?:an? )?picture|photo of|picture of|drawing of", re.I),
    re.compile(r"video .*of|generate (?:an? )?video|create (?:an? )?video|video banao|movie of", re.I),
    re.compile(r"write .*code|build .*app|python .*script|flask .*app|javascript|react|vue|angular|html|css|sql|database|programming", re.I),
    re.compile(r"explain\b|what is\b|how to\b|tutorial|guide|learn\b|definition of", re.I),
    re.compile(r"research .*on|web search|search for|google .*|find information|look up", re.I),
]


def classify_sensitivity(text: str) -> str:
    """Return 'high', 'medium', or 'low' based on content.

    'high'   → NEVER send to cloud. Contains proprietary pharma/GxP data.
    'medium' → May contain internal context; user discretion in strict mode.
    'low'    → Safe for cloud: general coding, creative, research queries.
    """
    lowered = text.lower()

    # HIGH: any keyword match
    for kw in _HIGH_KEYWORDS:
        if kw in lowered:
            return "high"

    # HIGH: any regex match
    for pat in _HIGH_PATTERNS:
        if pat.search(text):
            return "high"

    # MEDIUM: keyword match
    for kw in _MEDIUM_KEYWORDS:
        if kw in lowered:
            return "medium"

    # LOW: explicit low-sensitivity patterns (coding, creative, research)
    for pat in _LOW_PATTERNS:
        if pat.search(text):
            return "low"

    # Default: medium (conservative — when in doubt, keep it local)
    return "medium"


def can_use_cloud(text: str, privacy_mode: str = "balanced") -> tuple[bool, str]:
    """Return (allowed: bool, reason: str).

    Reasons:
    - "privacy-guard-high"     → sensitive data detected, forced local
    - "privacy-mode-strict"    → strict mode forces local for medium+ too
    - "privacy-guard-ok"       → low sensitivity, cloud allowed
    - "privacy-mode-performance" → performance mode allows cloud for low
    """
    level = classify_sensitivity(text)

    if level == "high":
        return False, "privacy-guard-high"

    if privacy_mode == "strict":
        return False, "privacy-mode-strict"

    if level == "medium":
        if privacy_mode == "performance":
            # In performance mode, we warn but still allow cloud for medium
            return True, "privacy-mode-performance"
        # Balanced mode: keep medium local by default
        return False, "privacy-guard-medium"

    # level == "low"
    return True, "privacy-guard-ok"


def sanitize_for_cloud(text: str) -> str:
    """Basic anonymization: replace known sensitive patterns with placeholders.

    This is a best-effort helper — it does NOT guarantee privacy.
    The privacy guard's `can_use_cloud` is the authoritative gatekeeper.
    """
    # Batch numbers → [BATCH_ID]
    text = re.sub(r"batch[\s_-]?(?:no|num|number|id)?[\s.:_-]?\w{3,}", "[BATCH_ID]", text, flags=re.I)
    # Lot numbers → [LOT_ID]
    text = re.sub(r"lot[\s_-]?(?:no|num|number|id)?[\s.:_-]?\w{3,}", "[LOT_ID]", text, flags=re.I)
    # COA numbers → [COA_ID]
    text = re.sub(r"coa[\s.:_-]?\d{3,}", "[COA_ID]", text, flags=re.I)
    # Phone numbers → [PHONE]
    text = re.sub(r"\+?\d{1,3}[\s-]?\d{5}[\s-]?\d{5}", "[PHONE]", text, flags=re.I)
    # INR amounts → [AMOUNT]
    text = re.sub(r"(?:rs\.?|inr|₹)\s*\d{1,3}(?:,\d{3})+(?:\.\d{2})?", "[AMOUNT]", text, flags=re.I)
    return text
