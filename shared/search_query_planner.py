from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


CASUAL_PATTERNS = [
    r"^\s*(hi|hello|hey|haan|han|yes|ok|okay|thanks|thank you)\s*[.!?]*\s*$",
    r"^\s*(hey|hi|hello)\s+shims\s*[.!?]*\s*$",
    r"^\s*(how are you|sun rahe ho|suno)\s*[?!.]*\s*$",
]

SEARCH_TRIGGERS = [
    "search the", "search for", "search web", "search online", "web search",
    "internet search", "look up", "lookup", "browse", "google for",
    "verify online", "check online", "find online", "research online",
    "latest news", "breaking news", "news today", "current events",
    "patent", "patents", "cas number", "cas no", "regulation",
    "rule", "law", "price", "market", "source", "citation", "cite",
    "what is the latest", "what are the latest", "recent developments",
    "upcoming", "new release", "just announced",
]

COMMAND_PATTERNS = [
    r"\b(?:hey|hi|hello|ok|okay|please|pls|shims|suno|sun|arre)\b",
    r"\b(?:can you|could you|would you|i need you to|i want you to|need to|help me)\b",
    r"\b(?:search(?:\s+the)?\s+(?:web|internet)?\s*(?:for)?|web\s+search(?:\s+for)?|internet\s+search(?:\s+for)?)\b",
    r"\b(?:look\s+up|lookup|browse|google|find\s+(?:me\s+)?(?:online\s+)?|research\s+(?:online\s+)?)\b",
    r"\b(?:check\s+(?:the\s+)?(?:internet|web|online)?(?:\s+for)?|verify\s+(?:online\s+)?)\b",
    r"\b(?:tell\s+me\s+(?:about|the)?|what\s+is|what\s+are|give\s+me|show\s+me)\b",
]

FILLER_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could",
    "do", "does", "for", "from", "has", "have", "how", "i", "in", "is",
    "it", "me", "my", "near", "now", "of", "on", "or", "please", "pls",
    "show", "tell", "that", "the", "their", "this", "to", "we", "what",
    "when", "where", "which", "who", "why", "with", "you", "your",
}

OPERATOR_RE = re.compile(r'(?:"[^"]+"|\b(?:site|filetype|intitle|inurl|after|before|source):\S+|-\S+)', re.I)
URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)


@dataclass(frozen=True)
class SearchQueryPlan:
    original_query: str
    should_search: bool
    primary_query: str
    variants: list[str]
    intent: str
    reason: str
    operators: list[str]
    quoted_phrases: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _is_casual(text: str) -> bool:
    return any(re.match(pattern, text or "", flags=re.I) for pattern in CASUAL_PATTERNS)


def _has_trigger(text: str) -> bool:
    low = (text or "").lower()
    return any(t in low for t in SEARCH_TRIGGERS)


def _strip_commands(text: str) -> str:
    out = text or ""
    for pattern in COMMAND_PATTERNS:
        out = re.sub(pattern, " ", out, flags=re.I)
    out = re.sub(r"\b(?:for me|right now|as of now|up to date)\b", " ", out, flags=re.I)
    return _clean_spaces(out.strip(" .,:;!?-"))


def _normalize_domain_terms(text: str) -> str:
    replacements = [
        (r"\be[\s-]?invoice\b", "e invoice"),
        (r"\be[\s-]?way[\s-]?bill\b", "e way bill"),
        (r"\bcas\s*(?:no\.?|number)?\b", "CAS number"),
        (r"\bgst\b", "GST"),
        (r"\bfto\b", "FTO"),
        (r"\bcoa\b", "COA"),
        (r"\bapi\b", "API"),
        (r"\bgmp\b", "GMP"),
        (r"\bich\b", "ICH"),
        (r"\bmsds\b", "MSDS"),
    ]
    out = text or ""
    for pattern, repl in replacements:
        out = re.sub(pattern, repl, out, flags=re.I)
    return _clean_spaces(out)


def _extract_operators(text: str) -> tuple[str, list[str], list[str]]:
    operators: list[str] = []
    quoted: list[str] = []

    def repl(match: re.Match[str]) -> str:
        token = match.group(0).strip()
        if token.startswith('"') and token.endswith('"'):
            quoted.append(token.strip('"'))
        else:
            operators.append(token)
        return " "

    stripped = OPERATOR_RE.sub(repl, text or "")
    return stripped, operators, quoted


def _keyword_query(text: str, *, operators: list[str], quoted: list[str], max_terms: int = 12) -> str:
    protected = operators + [f'"{q}"' for q in quoted if q]
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9+./%_-]*", text or ""):
        low = token.lower()
        if low in FILLER_WORDS:
            continue
        if low in {"latest", "current", "recent", "today", "news"}:
            # Keep temporal keywords for search queries — they matter for freshness
            terms.append(token)
            continue
        if low == "e":
            terms.append(token)
            continue
        if len(token) <= 2 and not token.isupper():
            continue
        terms.append(token)
    deduped: list[str] = []
    seen = set()
    for token in protected + terms:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(token)
    return _clean_spaces(" ".join(deduped[:max_terms]))


def _classify_intent(text: str) -> str:
    low = (text or "").lower()
    if "patent" in low or "fto" in low or "freedom to operate" in low:
        return "patent"
    if any(x in low for x in ["regulation", "rule", "law", "compliance", "gmp", "schedule m", "ich", "fda", "cdsco"]):
        return "regulatory"
    if any(x in low for x in ["price", "market", "supplier", "vendor", "cost"]):
        return "market"
    if any(x in low for x in ["news", "latest", "today", "current", "recent"]):
        return "fresh"
    if "cas" in low:
        return "identifier"
    return "general"


def _variants(primary: str, original: str, intent: str, max_variants: int) -> list[str]:
    out = [primary]
    low = primary.lower()
    if intent == "patent":
        if "patent" not in low:
            out.append(primary + " patent")
        out.append("site:patents.google.com " + primary)
    if intent == "regulatory":
        if "india" not in low and ("gst" in low or "schedule m" in low or "cdsco" in low):
            out.append(primary + " India")
        out.append(primary + " official")
    if intent == "market":
        if "india" not in low:
            out.append(primary + " India")
        out.append(primary + " supplier price")
    if intent == "identifier" and "CAS number".lower() not in low:
        out.append(primary + " CAS number")
    compact_original = _keyword_query(_strip_commands(_normalize_domain_terms(original)), operators=[], quoted=[], max_terms=10)
    if compact_original and compact_original.lower() != primary.lower():
        out.append(compact_original)
    deduped: list[str] = []
    seen = set()
    for item in out:
        item = _clean_spaces(item)
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[: max(1, max_variants)]


def plan_search_query(text: str, *, web_mode: bool = False, force_search: bool = False, max_variants: int = 4) -> SearchQueryPlan:
    original = _clean_spaces(text or "")
    if not original:
        return SearchQueryPlan(original, False, "", [], "none", "empty_input", [], [])
    if _is_casual(original):
        return SearchQueryPlan(original, False, "", [], "none", "casual_greeting", [], [])
    url_match = URL_RE.search(original)
    if url_match and force_search:
        url = url_match.group(0)
        return SearchQueryPlan(original, True, url, [url], "url", "forced_url_lookup", [], [])

    # Explicit search-engine operators are an unambiguous request for the web.
    has_operators = bool(re.search(r"\b(?:site|filetype|intitle|inurl):\S+", original, flags=re.I))
    should_search = force_search or _has_trigger(original) or has_operators
    if web_mode and not should_search:
        low = original.lower()
        # Only search in web_mode if there are explicit research-oriented phrases
        # or freshness keywords ("current AI news", "latest prices") — the WEB
        # toggle means the user wants live data when the turn asks for it.
        should_search = any(phrase in low for phrase in [
            "research", "verify", "find source", "cite source", "compare",
            "check facts", "fact check", "what does the research say",
        ]) or bool(re.search(r"\b(news|latest|current|recent|today|this week|this month|this year)\b", low))
    if not should_search:
        return SearchQueryPlan(original, False, "", [], "none", "local_chat_sufficient", [], [])

    normalized = _normalize_domain_terms(original)
    without_ops, operators, quoted = _extract_operators(normalized)
    stripped = _strip_commands(without_ops)
    primary = _keyword_query(stripped, operators=operators, quoted=quoted)
    if not primary:
        primary = _clean_spaces(" ".join(operators + [f'"{q}"' for q in quoted] + [stripped or normalized]))
    if len(primary) > 180:
        primary = " ".join(primary.split()[:18])

    intent = _classify_intent(original + " " + primary)
    variants = _variants(primary, original, intent, max_variants)
    return SearchQueryPlan(original, True, primary, variants, intent, "planned_query", operators, quoted)
