"""R&D Brain — AI-powered research & development intelligence for SHIMS.

Supports:
  • DeepSeek cloud API (deepseek-chat, deepseek-reasoner)
  • Local Ollama models (auto-selects best available)
  • Patent search & synthesis
  • Industrial process comparison & synthesis
  • Raw material pricing intelligence
  • Yield prediction & purity testing methods
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from shared.config import GENERATED_DIR, settings
from shared.ai import ask_ai, AIResult
from shared.document_engine import BrandedPDF, DocumentLine, DocumentSection, FormatConfig

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODELS = ["deepseek-reasoner", "deepseek-chat"]
CHEMDFM_DEFAULT_MODEL = os.getenv("CHEMDFM_MODEL", "ChemDFM")


def _deepseek_key() -> str | None:
    key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_KEY")
    if key:
        return key
    try:
        from .product_chemistry import get_provider_key

        configured = get_provider_key("deepseek")
        return configured.get("api_key") if configured else None
    except Exception:
        return None


def _chemdfm_key() -> str | None:
    key = os.getenv("CHEMDFM_API_KEY") or os.getenv("CHEMDFM_KEY")
    if key:
        return key
    try:
        from .product_chemistry import get_provider_key

        configured = get_provider_key("chemdfm")
        return configured.get("api_key") if configured else None
    except Exception:
        return None


def _chemdfm_base_url() -> str | None:
    base = os.getenv("CHEMDFM_BASE_URL") or os.getenv("CHEMDFM_API_BASE")
    if base:
        return base
    try:
        from .product_chemistry import get_provider_key

        configured = get_provider_key("chemdfm")
        return configured.get("base_url") if configured else None
    except Exception:
        return None


@dataclass
class PatentResult:
    patent_number: str
    title: str
    assignee: str
    filing_date: str
    abstract: str
    relevance_score: float
    url: str = ""


@dataclass
class ProcessStep:
    step_number: int
    description: str
    raw_materials: list[str] = field(default_factory=list)
    conditions: str = ""
    equipment: str = ""
    time_hours: float = 0.0
    temperature_c: float = 0.0
    pressure_bar: float = 0.0
    expected_yield_pct: float = 0.0
    notes: str = ""


@dataclass
class SynthesizedProcess:
    target_product: str
    raw_materials: list[str]
    overall_yield_pct: float
    steps: list[ProcessStep]
    safety_notes: str = ""
    environmental_notes: str = ""
    reference_patents: list[str] = field(default_factory=list)
    reference_literature: list[str] = field(default_factory=list)


@dataclass
class RMPricing:
    material: str
    price_per_kg_inr: float
    supplier_region: str
    price_date: str
    trend: str = "stable"  # rising | falling | stable
    notes: str = ""


@dataclass
class YieldPrediction:
    predicted_yield_pct: float
    confidence: str  # high | medium | low
    key_variables: list[str] = field(default_factory=list)
    optimization_suggestions: list[str] = field(default_factory=list)


@dataclass
class PurityTestMethod:
    test_name: str
    method: str  # HPLC, GC, IR, UV, Titration, etc.
    specification: str
    reference_standard: str = ""
    notes: str = ""


class RDBrain:
    """R&D Brain with local Ollama, chemistry-specialist, and cloud fallback routing."""

    def __init__(self, provider: str = "auto", model: str | None = None):
        """Initialize R&D Brain.

        Args:
            provider: 'chemdfm', 'deepseek', 'qwen', 'ollama', or 'auto'
            model: Specific model name to use (optional)
        """
        self.provider = provider
        self.model = model
        self._detect_provider()

    def _detect_provider(self) -> None:
        if self.provider == "auto":
            if _chemdfm_key() and _chemdfm_base_url():
                self.provider = "chemdfm"
                self.model = self.model or CHEMDFM_DEFAULT_MODEL
            elif _deepseek_key():
                self.provider = "deepseek"
                self.model = self.model or DEEPSEEK_MODELS[0]
            else:
                self.provider = "ollama"
                self.model = self.model or settings.ollama_model
        elif self.provider == "chemdfm":
            self.model = self.model or CHEMDFM_DEFAULT_MODEL
        elif self.provider == "deepseek":
            self.model = self.model or DEEPSEEK_MODELS[0]
        elif self.provider == "qwen":
            self.model = self.model or "qwen2.5:14b"
        elif self.provider == "ollama":
            self.model = self.model or settings.ollama_model

    def _with_predictive_grounding(self, experiment_data: dict[str, Any]) -> str:
        """Prepend deterministic flags from rd_predictive as grounding context."""
        try:
            from shared import rd_predictive
            assessment = rd_predictive.assess_experiment(experiment_data)
            flags = assessment.get('flags') or []
            predictions = assessment.get('predictions') or {}
            if not flags and not predictions:
                return ""
            lines = ["\n=== Deterministic risk flags (grounding) ==="]
            for f in flags:
                lines.append(
                    f"- [{f.get('severity', 'info').upper()}] {f.get('code')} "
                    f"(stage {f.get('stage_no') or '-'}): {f.get('message')}"
                )
            if predictions:
                lines.append("Predictions: " + json.dumps(predictions, default=str))
            lines.append("Use these flags as context; do not contradict them without explaining why.\n")
            return "\n".join(lines)
        except Exception:
            return ""

    async def _call_ai(self, prompt: str, system: str = "", temperature: float = 0.2) -> str:
        """Call AI with the configured provider."""
        if self.provider == "chemdfm":
            return await self._call_chemdfm(prompt, system, temperature)
        if self.provider == "deepseek":
            return await self._call_deepseek(prompt, system, temperature)
        if self.provider == "qwen":
            return await self._call_qwen(prompt, system, temperature)
        # Fallback to shared ask_ai (ollama) — pass model so Ollama respects RDBrain selection
        result = await ask_ai(prompt, system=system, provider="ollama", model=self.model, feature='chemistry')
        return result.text

    async def _call_qwen(self, prompt: str, system: str, temperature: float) -> str:
        """Call Qwen2.5 14B via Ollama for R&D-specific deep reasoning."""
        qwen_model = self.model or "qwen2.5:14b"
        payload = {
            "model": qwen_model,
            "messages": [
                {"role": "system", "content": system or "You are a pharmaceutical R&D scientist with deep expertise in organic synthesis, process development, and analytical chemistry."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": 32768},
        }
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                res = await client.post(
                    f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                    json=payload,
                )
                res.raise_for_status()
                data = res.json()
            text = data.get("message", {}).get("content") or data.get("response") or ""
            return text.strip() or "Qwen returned an empty response."
        except Exception as exc:
            # Fallback to general Ollama provider
            result = await ask_ai(prompt, system=system, provider="ollama", model=qwen_model, feature='chemistry')
            return f"[Qwen fallback] {result.text}"

    async def _call_deepseek(self, prompt: str, system: str, temperature: float) -> str:
        key = _deepseek_key()
        if not key:
            raise RuntimeError("DeepSeek API key not configured")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 8192,
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(DEEPSEEK_API_URL, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]

    async def _call_chemdfm(self, prompt: str, system: str, temperature: float) -> str:
        key = _chemdfm_key()
        base = _chemdfm_base_url()
        if not key or not base:
            result = await ask_ai(prompt, system=system, provider="ollama", feature='chemistry')
            return f"[ChemDFM not configured; local chemistry fallback] {result.text}"
        endpoint = base.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            if endpoint.endswith("/v1"):
                endpoint = endpoint + "/chat/completions"
            else:
                endpoint = endpoint + "/v1/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        messages = []
        messages.append({
            "role": "system",
            "content": system or (
                "You are SHIMS R&D Chemistry Brain. Reason about industrial API process "
                "development, solvents, impurities, yield, pH, safety, scale-up, and BMR/COA evidence. "
                "Return practical, cited decision-support drafts only."
            ),
        })
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model or CHEMDFM_DEFAULT_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 8192,
        }
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                r = await client.post(endpoint, headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            result = await ask_ai(prompt, system=system, provider="ollama", feature='chemistry')
            return f"[ChemDFM call failed: {exc}; local chemistry fallback] {result.text}"

    # ── Web search helpers for real patent grounding ───────────────────────

    async def _search_duckduckgo(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Lightweight no-key web search via DuckDuckGo HTML."""
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "SHIMS/14 research assistant"}) as client:
                r = await client.get("https://duckduckgo.com/html/", params={"q": query + " patent"})
                r.raise_for_status()
                html = r.text
            out: list[dict[str, str]] = []
            blocks = re.findall(
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?(?:<a[^>]+class="result__snippet"[^>]*>(.*?)</a>|<div[^>]+class="result__snippet"[^>]*>(.*?)</div>)',
                html, flags=re.I | re.S,
            )
            for url, title, sn1, sn2 in blocks[:max_results]:
                clean = lambda x: re.sub(r"\s+", " ", re.sub(r"<.*?>", "", x or "")).strip()
                out.append({"title": clean(title), "url": clean(url), "snippet": clean(sn1 or sn2)})
            return out
        except Exception:
            return []

    async def _search_tavily(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        key = os.getenv("TAVILY_API_KEY")
        if not key:
            return []
        try:
            payload = {"query": query + " patent", "max_results": max_results, "search_depth": "basic", "include_answer": False}
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=payload)
                r.raise_for_status()
                data = r.json()
            return [{"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("content", "")} for item in (data.get("results") or [])[:max_results]]
        except Exception:
            return []

    async def _search_brave(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        key = os.getenv("BRAVE_SEARCH_API_KEY")
        if not key:
            return []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get("https://api.search.brave.com/res/v1/web/search", headers={"X-Subscription-Token": key, "Accept": "application/json"}, params={"q": query + " patent", "count": min(max_results, 10)})
                r.raise_for_status()
                data = r.json()
            return [{"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("description", "")} for item in ((data.get("web") or {}).get("results") or [])[:max_results]]
        except Exception:
            return []

    # ── Real patent database APIs ──────────────────────────────────────────

    async def _search_serpapi_patents(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Query SerpAPI Google Patents engine (requires SERPAPI_API_KEY)."""
        key = os.getenv("SERPAPI_API_KEY")
        if not key:
            return []
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.get(
                    "https://serpapi.com/search.json",
                    params={"engine": "google_patents", "q": query, "api_key": key, "num": max(10, min(max_results, 100))},
                )
                r.raise_for_status()
                data = r.json()
            out: list[dict[str, str]] = []
            for item in (data.get("organic_results") or [])[:max_results]:
                out.append({
                    "title": item.get("title", ""),
                    "patent_id": item.get("patent_id") or item.get("publication_number", ""),
                    "assignee": item.get("assignee", ""),
                    "filing_date": item.get("filing_date", ""),
                    "abstract": item.get("snippet", ""),
                    "url": item.get("patent_link", ""),
                })
            return out
        except Exception:
            return []

    async def _search_uspto_ppub(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Query USPTO Patent Public Search API (requires free USPTO_API_KEY)."""
        key = os.getenv("USPTO_API_KEY")
        if not key:
            return []
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.post(
                    "https://ppubs.uspto.gov/api/search",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"searchText": query, "offset": 0, "limit": min(max_results, 10)},
                )
                r.raise_for_status()
                data = r.json()
            out: list[dict[str, str]] = []
            for item in (data.get("patents") or data.get("results") or [])[:max_results]:
                out.append({
                    "title": item.get("title") or item.get("patentTitle", ""),
                    "patent_id": item.get("patentNumber") or item.get("publicationNumber", ""),
                    "assignee": item.get("assignee") or item.get("assigneeName", ""),
                    "filing_date": item.get("filingDate") or item.get("filing_date", ""),
                    "abstract": item.get("abstractText") or item.get("abstract", ""),
                    "url": f"https://patents.uspto.gov/patent/{item.get('patentNumber', '')}",
                })
            return out
        except Exception:
            return []

    async def _search_cnipa(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Search Chinese patents via CNIPA / Google Patents cross-index.

        Uses SerpAPI Google Patents with CN-specific targeting if available,
        otherwise falls back to general web search for CNIPA-published patents.
        """
        key = os.getenv("SERPAPI_API_KEY")
        if key:
            try:
                async with httpx.AsyncClient(timeout=25) as client:
                    r = await client.get(
                        "https://serpapi.com/search.json",
                        params={"engine": "google_patents", "q": query + " country:CN", "api_key": key, "num": max(10, min(max_results, 100))},
                    )
                    r.raise_for_status()
                    data = r.json()
                out: list[dict[str, str]] = []
                for item in (data.get("organic_results") or [])[:max_results]:
                    pid = item.get("patent_id") or item.get("publication_number", "")
                    if "CN" in pid.upper() or "WO" in pid.upper():
                        out.append({
                            "title": item.get("title", ""),
                            "patent_id": pid,
                            "assignee": item.get("assignee", ""),
                            "filing_date": item.get("filing_date", ""),
                            "abstract": item.get("snippet", ""),
                            "url": item.get("patent_link", ""),
                        })
                if out:
                    return out
            except Exception:
                pass
        # Fallback: try web search for CNIPA patents
        web = await self._search_duckduckgo(f"site:cnipa.gov.cn {query}", max_results=max_results)
        if web:
            return [
                {
                    "title": w.get("title", ""),
                    "patent_id": "CN-" + w.get("title", "").split()[-1] if w.get("title") else "N/A",
                    "assignee": "",
                    "filing_date": "",
                    "abstract": w.get("snippet", ""),
                    "url": w.get("url", ""),
                }
                for w in web[:max_results]
            ]
        return []

    async def _fetch_web_context(self, query: str) -> str:
        """Fetch real patent results to ground patent search.

        Priority: SerpAPI Google Patents → USPTO → CNIPA → Tavily → Brave → DuckDuckGo.
        """
        # Try real patent databases first
        api_results: list[dict[str, str]] = []
        for fn in (self._search_serpapi_patents, self._search_uspto_ppub, self._search_cnipa):
            api_results = await fn(query, max_results=5)
            if api_results:
                break
        if api_results:
            lines = [
                "The following are real patent database results for this query. "
                "Ground your JSON output strictly in these records. Cite the exact patent numbers, assignees, and dates.",
                "",
            ]
            for i, item in enumerate(api_results, 1):
                lines.append(
                    f"{i}. {item['patent_id']} — {item['title']}\n"
                    f"   Assignee: {item['assignee']}\n"
                    f"   Filing date: {item['filing_date']}\n"
                    f"   URL: {item['url']}\n"
                    f"   Abstract: {item['abstract'][:400]}"
                )
            return "\n".join(lines)

        # Fall back to general web search
        web_results: list[dict[str, str]] = []
        for fn in (self._search_tavily, self._search_brave, self._search_duckduckgo):
            web_results = await fn(query, max_results=5)
            if web_results:
                break
        if not web_results:
            return ""
        lines = ["The following real web search results were found for this query. Use them to ground your patent output. Cite actual patent numbers, assignees, and titles where possible.", ""]
        for i, item in enumerate(web_results, 1):
            lines.append(f"{i}. {item['title']}\n   URL: {item['url']}\n   Snippet: {item['snippet'][:300]}")
        return "\n".join(lines)

    # ── Patent Search ──────────────────────────────────────────────────────

    async def _search_epo(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """EPO Open Patent Services (OPS) bibliographic search (free, registration recommended)."""
        try:
            async with httpx.AsyncClient(timeout=25, headers={"Accept": "application/json"}) as client:
                r = await client.get(
                    "https://ops.epo.org/3.2/rest-services/published-data/search/biblio",
                    params={"q": query, "Range": f"1-{min(max_results, 25)}"},
                )
                r.raise_for_status()
                data = r.json()
            out: list[dict[str, str]] = []
            for item in (data.get("ops:world-patent-data", {}).get("ops:biblio-search", {}).get("ops:search-result", {}).get("ops:publication-reference", []) or []):
                doc = item.get("document-id", [{}])[0] if isinstance(item.get("document-id"), list) else item.get("document-id", {})
                pn = doc.get("doc-number", "")
                country = doc.get("country", "")
                kind = doc.get("kind", "")
                out.append({
                    "title": f"EP patent {country}{pn}{kind}",
                    "patent_id": f"{country}{pn}{kind}",
                    "assignee": "",
                    "filing_date": doc.get("date", ""),
                    "abstract": "",
                    "url": f"https://register.epo.org/application?number={country}{pn}",
                })
            return out
        except Exception:
            return []

    async def _search_wipo(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """WIPO PATENTSCOPE simple search (public, no key)."""
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.get(
                    "https://patentscope.wipo.int/search/en/result.jsf",
                    params={"query": query, "page": 1, "pageSize": min(max_results, 25)},
                )
                r.raise_for_status()
                html = r.text
            out: list[dict[str, str]] = []
            # PATENTSCOPE result rows contain publication numbers like WO2020/123456
            for m in re.finditer(r'(WO\d{4}/\d{1,8})', html):
                pn = m.group(1)
                if any(o.get('patent_id') == pn for o in out):
                    continue
                out.append({
                    "title": f"PCT application {pn}",
                    "patent_id": pn,
                    "assignee": "",
                    "filing_date": "",
                    "abstract": "",
                    "url": f"https://patentscope.wipo.int/search/en/detail.jsf?docId={pn}",
                })
                if len(out) >= max_results:
                    break
            return out
        except Exception:
            return []

    async def _search_ipo_india(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Indian Patent Office public search fallback (web scrape)."""
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.get(
                    "https://ipindiaservices.gov.in/publicsearch",
                    params={"SearchText": query, "Page": 1},
                )
                r.raise_for_status()
                html = r.text
            out: list[dict[str, str]] = []
            for m in re.finditer(r'(IN\d{1,2}[A-Z]?\d{4,7}[A-Z]?\d?)', html):
                pn = m.group(1)
                if any(o.get('patent_id') == pn for o in out):
                    continue
                out.append({
                    "title": f"Indian patent {pn}",
                    "patent_id": pn,
                    "assignee": "",
                    "filing_date": "",
                    "abstract": "",
                    "url": "https://ipindiaservices.gov.in/publicsearch",
                })
                if len(out) >= max_results:
                    break
            return out
        except Exception:
            return []

    async def _search_jplatpat(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """J-PlatPat (Japan) web fallback."""
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.get(
                    "https://www.j-platpat.inpit.go.jp/c1800/PU/JP-9-500001/11/en",
                    params={"q": query},
                )
                r.raise_for_status()
                html = r.text
            out: list[dict[str, str]] = []
            for m in re.finditer(r'(JP\d{4,8}[A-Z]?)', html):
                pn = m.group(1)
                if any(o.get('patent_id') == pn for o in out):
                    continue
                out.append({
                    "title": f"Japan patent {pn}",
                    "patent_id": pn,
                    "assignee": "",
                    "filing_date": "",
                    "abstract": "",
                    "url": "https://www.j-platpat.inpit.go.jp/",
                })
                if len(out) >= max_results:
                    break
            return out
        except Exception:
            return []

    async def patent_search(self, query: str, top_k: int = 10, jurisdictions: list[str] | None = None) -> list[PatentResult]:
        """Search patents and return structured results.

        Real patent databases are queried first (SerpAPI Google Patents, USPTO
        Patent Public Search, CNIPA, EPO, WIPO, IPO India, J-PlatPat). If no API
        keys are configured, the search falls back to general web search. The LLM
        synthesizes structured JSON grounded in whichever results are found.
        """
        jurisdictions = [j.upper() for j in (jurisdictions or ['US', 'CN', 'EP', 'WO', 'IN', 'JP'])]
        source_map = {
            'US': self._search_serpapi_patents,
            'CN': self._search_cnipa,
            'EP': self._search_epo,
            'WO': self._search_wipo,
            'IN': self._search_ipo_india,
            'JP': self._search_jplatpat,
        }
        # Always include SerpAPI Google Patents as a broad cross-check
        raw_api_results: list[dict[str, str]] = []
        serp_results = await self._search_serpapi_patents(query, max_results=top_k)
        if serp_results:
            raw_api_results.extend(serp_results)
        for j in jurisdictions:
            fn = source_map.get(j)
            if not fn:
                continue
            try:
                results = await fn(query, max_results=top_k)
                if results:
                    raw_api_results.extend(results)
            except Exception:
                continue
        # Deduplicate by patent_id
        seen: set[str] = set()
        deduped: list[dict[str, str]] = []
        for r in raw_api_results:
            pid = r.get('patent_id', '').strip()
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            deduped.append(r)
        raw_api_results = deduped
        system = (
            "You are a pharmaceutical patent research assistant. "
            "Given a search query (and optional patent database / web search context), "
            "produce a JSON array of relevant patents. "
            "Each patent must have: patent_number, title, assignee, filing_date, abstract, relevance_score (0-1). "
            "Only output valid JSON. No markdown."
        )
        web_context = ""
        if raw_api_results:
            lines = [
                "The following are real patent database results for this query. "
                "Ground your JSON output strictly in these records. Cite the exact patent numbers, assignees, and dates.",
                "",
            ]
            for i, item in enumerate(raw_api_results[:top_k * 2], 1):
                lines.append(
                    f"{i}. {item.get('patent_id', '')} — {item.get('title', '')}\n"
                    f"   Assignee: {item.get('assignee', '')}\n"
                    f"   Filing date: {item.get('filing_date', '')}\n"
                    f"   URL: {item.get('url', '')}\n"
                    f"   Abstract: {item.get('abstract', '')[:400]}"
                )
            web_context = "\n".join(lines)
        else:
            web_results: list[dict[str, str]] = []
            for fn in (self._search_tavily, self._search_brave, self._search_duckduckgo):
                web_results = await fn(query, max_results=top_k)
                if web_results:
                    break
            if web_results:
                lines = ["The following real web search results were found for this query. Use them to ground your patent output. Cite actual patent numbers, assignees, and titles where possible.", ""]
                for i, item in enumerate(web_results, 1):
                    lines.append(f"{i}. {item.get('title', '')}\n   URL: {item.get('url', '')}\n   Snippet: {item.get('snippet', '')[:300]}")
                web_context = "\n".join(lines)

        prompt = f"Search query: {query}\n"
        if web_context:
            prompt += f"\nPatent database / web search context:\n{web_context}\n"
        prompt += f"\nReturn top {top_k} most relevant patents as JSON."
        text = await self._call_ai(prompt, system=system, temperature=0.1)
        return self._parse_patent_json(text, query, raw_api_results)

    async def patent_search_accuracy_loop(
        self,
        query: str,
        top_k: int = 10,
        jurisdictions: list[str] | None = None,
        min_results: int = 5,
        max_variants: int = 3,
    ) -> list[PatentResult]:
        """Run patent search, expand query variants if recall is low, and return merged results."""
        results = await self.patent_search(query, top_k=top_k, jurisdictions=jurisdictions)
        if len(results) >= min_results and all(r.relevance_score >= 0.5 for r in results):
            return results
        variants = [
            f"{query} process for preparation",
            f"{query} synthesis",
            f"{query} API patent",
        ]
        seen = {r.patent_number for r in results}
        for v in variants[:max_variants]:
            try:
                extra = await self.patent_search(v, top_k=top_k, jurisdictions=jurisdictions)
            except Exception:
                continue
            for r in extra:
                if r.patent_number not in seen:
                    seen.add(r.patent_number)
                    results.append(r)
            if len(results) >= min_results:
                break
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results[:top_k]

    def _parse_patent_json(self, text: str, query: str, raw_results: list[dict[str, str]] | None = None) -> list[PatentResult]:
        """Robustly parse JSON patent output from an LLM.

        If parsing fails and raw_results are provided, return them directly.
        """
        # Strip DeepSeek/R1 <think> blocks and markdown code fences
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
        cleaned = re.sub(r"```json\s*|\s*```", "", cleaned)
        cleaned = cleaned.strip()
        # Try to find a JSON array
        for strategy in (lambda t: t, lambda t: t[t.find("["):t.rfind("]") + 1] if "[" in t else t):
            try:
                data = json.loads(strategy(cleaned))
                if isinstance(data, dict):
                    # Some models wrap the array in {"patents": [...]}
                    data = data.get("patents") or data.get("results") or []
                if isinstance(data, list):
                    return [PatentResult(**p) for p in data]
            except Exception:
                continue
        # Fallback: if we have raw API results, return them directly
        if raw_results:
            return [
                PatentResult(
                    patent_number=r.get("patent_id", "N/A"),
                    title=r.get("title", ""),
                    assignee=r.get("assignee", ""),
                    filing_date=r.get("filing_date", ""),
                    abstract=r.get("abstract", ""),
                    relevance_score=1.0,
                    url=r.get("url", ""),
                )
                for r in raw_results
            ]
        # Ultimate fallback: return a single result with raw text
        return [PatentResult(
            patent_number="N/A",
            title=f"Search results for: {query}",
            assignee="",
            filing_date="",
            abstract=text[:2000],
            relevance_score=1.0,
        )]

    # ── Process Synthesis ──────────────────────────────────────────────────

    async def synthesize_process(
        self,
        target_product: str,
        raw_materials: list[str],
        constraints: str = "",
    ) -> SynthesizedProcess:
        """Synthesize an industrial manufacturing process from raw materials."""
        system = (
            "You are a pharmaceutical process development chemist. "
            "Given a target product and raw materials, synthesize a detailed step-by-step manufacturing process. "
            "Output must be valid JSON with this structure:\n"
            '{"target_product":"...","raw_materials":["..."],"overall_yield_pct":85.0,"steps":['
            '{"step_number":1,"description":"...","raw_materials":["..."],"conditions":"...",'
            '"equipment":"...","time_hours":2.0,"temperature_c":25.0,"pressure_bar":1.0,"expected_yield_pct":95.0,"notes":"..."}'
            '],"safety_notes":"...","environmental_notes":"...","reference_patents":["..."],"reference_literature":["..."]}\n'
            "Only output valid JSON. No markdown."
        )
        prompt = f"Target product: {target_product}\nRaw materials available: {', '.join(raw_materials)}"
        if constraints:
            prompt += f"\nConstraints / requirements: {constraints}"
        text = await self._call_ai(prompt, system=system, temperature=0.2)
        try:
            data = json.loads(text)
            steps = [ProcessStep(**s) for s in data.get("steps", [])]
            return SynthesizedProcess(
                target_product=data.get("target_product", target_product),
                raw_materials=data.get("raw_materials", raw_materials),
                overall_yield_pct=data.get("overall_yield_pct", 0.0),
                steps=steps,
                safety_notes=data.get("safety_notes", ""),
                environmental_notes=data.get("environmental_notes", ""),
                reference_patents=data.get("reference_patents", []),
                reference_literature=data.get("reference_literature", []),
            )
        except Exception:
            # Fallback: create a basic process from the raw text
            return SynthesizedProcess(
                target_product=target_product,
                raw_materials=raw_materials,
                overall_yield_pct=0.0,
                steps=[ProcessStep(step_number=1, description=text[:2000], notes="AI response (parse failed)")],
            )

    # ── Process Comparison ─────────────────────────────────────────────────

    async def compare_processes(self, process_a: str, process_b: str) -> dict[str, Any]:
        """Compare two industrial processes and return structured analysis."""
        system = (
            "You are a process engineering analyst. Compare two pharmaceutical manufacturing processes. "
            "Output valid JSON with keys: winner (A/B/tie), yield_comparison, cost_comparison, "
            "time_comparison, safety_comparison, environmental_comparison, recommendation."
        )
        prompt = f"Process A:\n{process_a}\n\nProcess B:\n{process_b}\n\nProvide detailed comparison."
        text = await self._call_ai(prompt, system=system, temperature=0.2)
        try:
            return json.loads(text)
        except Exception:
            return {"comparison_text": text, "parse_error": True}

    # ── Raw Material Pricing ───────────────────────────────────────────────

    async def raw_material_pricing(self, materials: list[str]) -> list[RMPricing]:
        """Get estimated pricing intelligence for raw materials."""
        system = (
            "You are a pharmaceutical procurement intelligence analyst. "
            "Given a list of raw materials, provide estimated market pricing in INR per kg. "
            "Output valid JSON array with: material, price_per_kg_inr, supplier_region, price_date, trend, notes."
        )
        prompt = f"Raw materials: {', '.join(materials)}\nProvide current estimated market pricing in India."
        text = await self._call_ai(prompt, system=system, temperature=0.2)
        try:
            data = json.loads(text)
            return [RMPricing(**p) for p in data]
        except Exception:
            return [
                RMPricing(
                    material=m,
                    price_per_kg_inr=0.0,
                    supplier_region="India",
                    price_date=datetime.now().strftime("%Y-%m-%d"),
                    notes=text[:1000],
                )
                for m in materials
            ]

    # ── Yield Prediction ───────────────────────────────────────────────────

    async def predict_yield(self, process_description: str, historical_yields: list[float] | None = None) -> YieldPrediction:
        """Predict yield for a given process."""
        system = (
            "You are a pharmaceutical yield prediction specialist. "
            "Given a process description, predict the expected yield percentage. "
            "Output valid JSON: {predicted_yield_pct: 85.5, confidence: 'high', key_variables: ['...'], optimization_suggestions: ['...']}. "
            "Confidence must be one of: high, medium, low."
        )
        prompt = f"Process description:\n{process_description}"
        if historical_yields:
            prompt += f"\nHistorical yields: {historical_yields}"
        text = await self._call_ai(prompt, system=system, temperature=0.2)
        try:
            data = json.loads(text)
            return YieldPrediction(**data)
        except Exception:
            return YieldPrediction(
                predicted_yield_pct=0.0,
                confidence="low",
                optimization_suggestions=["AI response could not be parsed", text[:500]],
            )

    # ── Purity Testing Methods ─────────────────────────────────────────────

    async def purity_testing_methods(self, product: str, dosage_form: str = "API") -> list[PurityTestMethod]:
        """Suggest purity testing methods for a product."""
        system = (
            "You are a QC analytical chemist. Given a pharmaceutical product, suggest purity testing methods. "
            "Output valid JSON array with: test_name, method, specification, reference_standard, notes."
        )
        prompt = f"Product: {product}\nDosage form: {dosage_form}\nSuggest comprehensive purity testing methods per pharmacopeial standards (IP/BP/USP)."
        text = await self._call_ai(prompt, system=system, temperature=0.2)
        try:
            data = json.loads(text)
            return [PurityTestMethod(**t) for t in data]
        except Exception:
            return [PurityTestMethod(
                test_name="Comprehensive Analysis",
                method="Multiple",
                specification="As per pharmacopeia",
                notes=text[:2000],
            )]

    # ── Research Brief Generator ───────────────────────────────────────────

    async def generate_research_brief(
        self,
        title: str,
        objective: str,
        background: str,
        target_product: str,
        raw_materials: list[str],
    ) -> Path:
        """Generate a branded PDF research brief with AI-synthesized content."""
        # Gather intelligence in parallel
        process = await self.synthesize_process(target_product, raw_materials)
        pricing = await self.raw_material_pricing(raw_materials)
        tests = await self.purity_testing_methods(target_product)
        yield_pred = await self.predict_yield(
            f"Synthesis of {target_product} from {', '.join(raw_materials)}"
        )

        # Build PDF
        pdf = BrandedPDF(
            title=f"Research Brief: {title}",
            doc_id=f"RB-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            kind="research_brief",
            format_config=FormatConfig(
                header_font_size=16,
                body_font_size=10,
                table_header_bg="#1E40AF",
                primary_color="#1E3A5F",
                accent_color="#2563EB",
                show_logo=True,
                signature_lines=2,
            ),
        )
        pdf.add_meta("Project Title", title)
        pdf.add_meta("Objective", objective)
        pdf.add_meta("Target Product", target_product)
        pdf.add_meta("Date", datetime.now().strftime("%d-%b-%Y"))

        # Background
        pdf.add_section(DocumentSection(
            title="1. Background & Objective",
            lines=[DocumentLine(key="bg", label="Background", value=background, type="text")],
        ))

        # Process synthesis
        process_lines = [
            DocumentLine(key="product", label="Target Product", value=process.target_product),
            DocumentLine(key="yield", label="Predicted Overall Yield", value=f"{process.overall_yield_pct}%"),
        ]
        for step in process.steps:
            process_lines.append(DocumentLine(
                key=f"step_{step.step_number}",
                label=f"Step {step.step_number}: {step.description[:60]}",
                value=f"T={step.temperature_c}°C, P={step.pressure_bar}bar, {step.time_hours}h, Yield={step.expected_yield_pct}%",
                spec=step.conditions,
                unit=step.equipment,
            ))
        pdf.add_section(DocumentSection(title="2. Proposed Process Flow", lines=process_lines))

        # Raw material pricing
        pricing_lines = [
            DocumentLine(key="p_header", label="Material", value="Price (INR/kg)", type="subheader"),
        ]
        for p in pricing:
            pricing_lines.append(DocumentLine(
                key=f"price_{p.material}",
                label=p.material,
                value=f"₹{p.price_per_kg_inr:,.2f}",
                spec=p.supplier_region,
                unit=p.trend,
            ))
        pdf.add_section(DocumentSection(title="3. Raw Material Pricing Intelligence", lines=pricing_lines))

        # Purity tests
        test_lines = [
            DocumentLine(key="t_header", label="Test", value="Method", type="subheader"),
        ]
        for t in tests:
            test_lines.append(DocumentLine(
                key=f"test_{t.test_name}",
                label=t.test_name,
                value=t.method,
                spec=t.specification,
                unit=t.reference_standard,
            ))
        pdf.add_section(DocumentSection(title="4. Purity Testing Methods", lines=test_lines))

        # Yield prediction
        pdf.add_section(DocumentSection(
            title="5. Yield Prediction",
            lines=[
                DocumentLine(key="pred_yield", label="Predicted Yield", value=f"{yield_pred.predicted_yield_pct}%"),
                DocumentLine(key="confidence", label="Confidence", value=yield_pred.confidence),
                DocumentLine(key="variables", label="Key Variables", value="; ".join(yield_pred.key_variables)),
                DocumentLine(key="optimizations", label="Optimization Suggestions", value="; ".join(yield_pred.optimization_suggestions)),
            ],
        ))

        # Safety & references
        pdf.add_section(DocumentSection(
            title="6. Safety & Environmental Notes",
            lines=[
                DocumentLine(key="safety", label="Safety Notes", value=process.safety_notes),
                DocumentLine(key="env", label="Environmental Notes", value=process.environmental_notes),
                DocumentLine(key="patents", label="Reference Patents", value="; ".join(process.reference_patents[:5])),
            ],
        ))

        slug = title.lower().replace(" ", "_")[:30]
        output_path = GENERATED_DIR / f"ResearchBrief_{slug}_{pdf.doc_id}.pdf"
        return pdf.build(output_path)

    # ── Impurity Intelligence ──────────────────────────────────────────────

    async def analyze_impurity_trend(
        self,
        experiments: list[dict[str, Any]],
        target_impurities: list[str] | None = None,
    ) -> dict[str, Any]:
        """Compare multiple experiments and suggest parameter changes to reduce impurities.

        Args:
            experiments: List of experiment dicts with stages and impurity profiles.
            target_impurities: Optional list of impurity names to focus on (e.g. ['Impurity A', 'Impurity C'])
        """
        system = (
            "You are a pharmaceutical impurity specialist and process analytical chemist. "
            "Given multiple experiment records with temperature, pH, solvent, catalyst, and impurity profiles, "
            "analyze trends and suggest which parameter changes reduce specific impurities. "
            "Output valid JSON with keys: summary (string), per_impurity_analysis (array of {impurity_name, trend, likely_cause, recommended_change, confidence}), "
            "best_conditions (object with temp_c, ph, solvent, catalyst), and process_recommendations (array of strings)."
        )
        prompt = f"Experiments to analyze:\n{json.dumps(experiments, indent=2, default=str)}"
        if target_impurities:
            prompt += f"\n\nFocus on these impurities: {', '.join(target_impurities)}"
        text = await self._call_ai(prompt, system=system, temperature=0.2)
        try:
            return json.loads(text)
        except Exception:
            return {
                "summary": text[:2000],
                "parse_error": True,
                "per_impurity_analysis": [],
                "best_conditions": {},
                "process_recommendations": ["Could not parse structured output. Review text summary."],
            }

    async def predict_impurity_solubility(
        self,
        impurity_name: str,
        impurity_smiles: str | None = None,
        solvents: list[str] | None = None,
    ) -> dict[str, Any]:
        """Predict impurity solubility in various solvents and suggest purge strategies.

        Args:
            impurity_name: Name of the impurity (e.g., 'Fluconazole Impurity A')
            impurity_smiles: Optional SMILES string for structure-based prediction
            solvents: List of solvents to evaluate (defaults to common pharma solvents)
        """
        solvents = solvents or ["Water", "Methanol", "Ethanol", "Acetone", "Toluene", "Ethyl acetate", "DMF", "DMSO", "Isopropanol", "Dichloromethane"]
        system = (
            "You are a computational chemist specializing in solubility prediction and crystallization. "
            "Given an impurity name, optional SMILES, and a list of solvents, predict relative solubility trends. "
            "Output valid JSON with keys: impurity_name, structure_notes (string), solubility_predictions (array of {solvent, predicted_solubility_mg_ml, confidence, purge_strategy}), "
            "formation_pathway (string), and recommended_rejection_method (string)."
        )
        prompt = f"Impurity: {impurity_name}\nSMILES: {impurity_smiles or 'Not provided'}\nSolvents: {', '.join(solvents)}\n\nPredict solubility and suggest crystallization/rejection strategies."
        text = await self._call_ai(prompt, system=system, temperature=0.2)
        try:
            return json.loads(text)
        except Exception:
            return {
                "impurity_name": impurity_name,
                "structure_notes": text[:1500],
                "parse_error": True,
                "solubility_predictions": [
                    {"solvent": s, "predicted_solubility_mg_ml": "unknown", "confidence": "low", "purge_strategy": "Requires experimental validation"}
                    for s in solvents
                ],
            }

    async def suggest_process_optimization(
        self,
        product_name: str,
        experiments: list[dict[str, Any]],
        constraints: str = "",
    ) -> dict[str, Any]:
        """Given a set of experiments, suggest the optimal process parameters.

        Args:
            product_name: Target product name
            experiments: List of experiment records with stages and results
            constraints: Optional constraints (e.g., 'max temp 100C', 'avoid DMF')
        """
        system = (
            "You are a process development scientist. Given experiment data for a pharmaceutical product, "
            "identify the best parameter set that maximizes yield and purity while minimizing impurities. "
            "Output valid JSON with keys: recommended_parameters (object), expected_yield_pct, expected_purity_pct, "
            "risk_assessment (string), scale_up_notes (array of strings), and parameter_sensitivity (array of {parameter, sensitivity, acceptable_range})."
        )
        prompt = f"Product: {product_name}\nExperiments:\n{json.dumps(experiments, indent=2, default=str)}"
        if constraints:
            prompt += f"\n\nConstraints: {constraints}"
        text = await self._call_ai(prompt, system=system, temperature=0.2)
        try:
            return json.loads(text)
        except Exception:
            return {
                "recommended_parameters": {},
                "expected_yield_pct": 0.0,
                "expected_purity_pct": 0.0,
                "risk_assessment": text[:1500],
                "parse_error": True,
            }

    async def generate_process_flow_diagram(
        self,
        product_name: str,
        stages: list[dict[str, Any]],
    ) -> str:
        """Generate a text-based process flow diagram description (Mermaid/DOT compatible).

        Returns a Mermaid flowchart string that can be rendered in the browser.
        """
        system = (
            "You are a process engineer creating visual process flow diagrams. "
            "Given a product and its process stages, output a Mermaid flowchart diagram (flowchart TD). "
            "Use node shapes: rounded rectangles for stages, diamonds for decisions, circles for materials. "
            "Include yield%, temp, and solvent on edges. Only output the Mermaid code block, nothing else."
        )
        prompt = f"Product: {product_name}\nStages:\n{json.dumps(stages, indent=2, default=str)}\n\nGenerate a Mermaid flowchart TD."
        text = await self._call_ai(prompt, system=system, temperature=0.3)
        # Extract mermaid block if wrapped in markdown
        match = re.search(r"```mermaid\n(.*?)```", text, re.S)
        if match:
            return match.group(1).strip()
        match = re.search(r"```\n(.*?)```", text, re.S)
        if match:
            return match.group(1).strip()
        return text.strip()

    async def finalize_process(
        self,
        product_name: str,
        selected_experiment_id: int,
        experiments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Finalize the best process based on experiment comparison.

        Returns a structured process finalization report with material balance per stage.
        """
        system = (
            "You are a senior process development manager finalizing a manufacturing process for GMP scale-up. "
            "Given experiment data, produce a process finalization report. "
            "Output valid JSON with keys: final_process (object with stages array), material_balance_summary (string), "
            "theoretical_overall_yield, recommended_batch_size, critical_quality_attributes (array), "
            "control_strategy (string), and approval_recommendation (string: 'approve'/'conditional'/'reject')."
        )
        prompt = f"Product: {product_name}\nSelected experiment ID: {selected_experiment_id}\nAll experiments:\n{json.dumps(experiments, indent=2, default=str)}"
        text = await self._call_ai(prompt, system=system, temperature=0.2)
        try:
            return json.loads(text)
        except Exception:
            return {
                "final_process": {},
                "material_balance_summary": text[:2000],
                "parse_error": True,
                "approval_recommendation": "conditional",
            }

    # ═══════════════════════════════════════════════════════════════════════════
    # R&D Brain v2 — Qwen2.5 14B Enhanced Capabilities
    # ═══════════════════════════════════════════════════════════════════════════

    async def design_experiment_from_composition(
        self,
        product_name: str,
        chemical_formula: str,
        target_purity: float = 99.0,
        constraints: str = "",
    ) -> dict[str, Any]:
        """Design a complete experiment from the chemical composition of the end product.

        Uses Qwen2.5 14B to synthesize a full process with stages, RM, conditions, tests.
        """
        system = (
            "You are a senior pharmaceutical process development chemist. "
            "Given a target product name and chemical formula, design a complete synthetic route and experiment plan. "
            "Output valid JSON with this exact structure:\n"
            '{\n'
            '  "product_name": "...",\n'
            '  "chemical_formula": "...",\n'
            '  "ksm": "key starting material name",\n'
            '  "route_name": "brief route description",\n'
            '  "stages": [\n'
            '    {\n'
            '      "stage_no": 1,\n'
            '      "stage_name": "...",\n'
            '      "description": "...",\n'
            '      "raw_materials": [{"name": "...", "planned_qty": 100, "unit": "g", "equivalents": 1.0}],\n'
            '      "solvents": [{"name": "...", "quantity_ml": 500}],\n'
            '      "catalyst": "...",\n'
            '      "temperature_c": 80,\n'
            '      "pressure_bar": 1.0,\n'
            '      "ph_value": 7.0,\n'
            '      "reaction_time_hours": 6,\n'
            '      "theoretical_yield_pct": 85,\n'
            '      "tests": ["TLC", "HPLC"],\n'
            '      "safety_warnings": ["..."],\n'
            '      "critical_controls": ["..."]\n'
            '    }\n'
            '  ],\n'
            '  "overall_yield_pct": 75,\n'
            '  "safety_notes": "...",\n'
            '  "environmental_notes": "...",\n'
            '  "recommended_equipment": ["..."]\n'
            '}\n'
            "Only output valid JSON. No markdown."
        )
        prompt = (
            f"Design an experiment for: {product_name}\n"
            f"Chemical formula: {chemical_formula}\n"
            f"Target purity: {target_purity}%\n"
        )
        if constraints:
            prompt += f"Constraints: {constraints}\n"
        text = await self._call_ai(prompt, system=system, temperature=0.15)
        try:
            return json.loads(text)
        except Exception:
            return {
                "product_name": product_name,
                "chemical_formula": chemical_formula,
                "parse_error": True,
                "raw_response": text[:3000],
            }

    async def predict_reaction_dynamics(
        self,
        stage_description: str,
        reagents: list[str],
        current_conditions: dict[str, Any],
    ) -> dict[str, Any]:
        """Predict reaction dynamics: temperature profile, pH profile, pressure, warnings, conversion curve.

        Returns a detailed prediction for safe and optimal reaction execution.
        """
        system = (
            "You are a reaction engineering specialist. Given a reaction stage description, reagents, and conditions, "
            "predict the reaction dynamics and safe operating envelope. "
            "Output valid JSON with:\n"
            '{\n'
            '  "temperature_profile": [{"time_min": 0, "temp_c": 25, "rationale": "..."}],\n'
            '  "ph_profile": [{"time_min": 0, "ph": 7.0, "adjustment": "none"}],\n'
            '  "pressure_profile": [{"time_min": 0, "pressure_bar": 1.0, "safety_limit_bar": 2.0}],\n'
            '  "expected_conversion_curve": [{"time_min": 0, "conversion_pct": 0}, {"time_min": 60, "conversion_pct": 45}, {"time_min": 360, "conversion_pct": 95}],\n'
            '  "warnings": ["Exotherm risk above 85°C", "Pressure build-up if sealed", "Toxic gas evolution — use scrubber"],\n'
            '  "critical_control_points": ["Add reagent over 30 min", "Maintain temp <80°C", "Cool to 10°C before quench"],\n'
            '  "recommended_endpoint": {"conversion_pct": 98, "time_min": 360, "test": "HPLC"},\n'
            '  "safety_notes": "...",\n'
            '  "scale_up_risks": ["Heat transfer limitation", "Mixing uniformity"]\n'
            '}\n'
            "Only output valid JSON. No markdown."
        )
        prompt = (
            f"Stage description: {stage_description}\n"
            f"Reagents: {', '.join(reagents)}\n"
            f"Current conditions: {json.dumps(current_conditions)}\n"
        )
        text = await self._call_ai(prompt, system=system, temperature=0.15)
        try:
            return json.loads(text)
        except Exception:
            return {
                "parse_error": True,
                "raw_response": text[:3000],
                "warnings": ["Could not parse AI response. Review raw output manually."],
            }

    async def generate_experiment_template(
        self,
        product_name: str,
        patents: list[dict[str, Any]],
        constraints: str = "",
    ) -> dict[str, Any]:
        """Generate a reusable experiment template from patent analysis.

        Reads patents, synthesizes best practices, and outputs a template structure.
        """
        system = (
            "You are a pharmaceutical IP and process development expert. "
            "Given patent data for a product, synthesize the best experiment template. "
            "Output valid JSON with:\n"
            '{\n'
            '  "template_name": "...",\n'
            '  "product_name": "...",\n'
            '  "route_name": "...",\n'
            '  "description": "...",\n'
            '  "stages": [{"stage_no": 1, "stage_name": "...", "temperature_c": 80, "ph_value": 7, "pressure_bar": 1, "theoretical_yield_pct": 85, "tests": ["TLC", "HPLC"], "critical_controls": ["..."]}],\n'
            '  "raw_materials": [{"name": "...", "planned_qty": 100, "unit": "g", "equivalents": 1.0}],\n'
            '  "solvents": [{"name": "...", "quantity_ml": 500}],\n'
            '  "tests": [{"stage_no": 1, "test_name": "HPLC", "specification": ">95%"}],\n'
            '  "target_conditions": {"overall_yield_pct": 75, "target_purity_pct": 99},\n'
            '  "reference_patents": ["..."]\n'
            '}\n'
            "Only output valid JSON. No markdown."
        )
        prompt = f"Product: {product_name}\nPatents analyzed:\n{json.dumps(patents, indent=2, default=str)[:4000]}\n"
        if constraints:
            prompt += f"Constraints: {constraints}\n"
        text = await self._call_ai(prompt, system=system, temperature=0.15)
        try:
            return json.loads(text)
        except Exception:
            return {
                "template_name": f"{product_name} Template",
                "product_name": product_name,
                "parse_error": True,
                "raw_response": text[:3000],
            }

    async def analyze_rm_reduction(
        self,
        experiment_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Analyze stage-wise RM usage and suggest reduction opportunities.

        Input: experiment detail dict with stages and stage_raw_materials.
        Output: specific reduction suggestions with cost impact.
        """
        system = (
            "You are a process optimization and cost reduction specialist in pharmaceutical manufacturing. "
            "Given experiment data with stage-wise raw material consumption, analyze efficiency and suggest reductions. "
            "Output valid JSON with:\n"
            '{\n'
            '  "per_stage_efficiency": [{"stage_name": "...", "rm_name": "...", "planned": 100, "actual": 115, "waste": 5, "efficiency_pct": 87, "deviation_pct": 15}],\n'
            '  "reduction_opportunities": [\n'
            '    {"stage_name": "...", "rm_name": "...", "current_usage": 115, "suggested_usage": 100, "reduction_pct": 13, "rationale": "...", "annual_savings_inr": 500000}\n'
            '  ],\n'
            '  "cost_impact": {"current_cost_per_kg": 2500, "optimized_cost_per_kg": 2200, "savings_pct": 12},\n'
            '  "material_balance_report": {"total_in": 1000, "total_out": 820, "total_waste": 80, "total_recovery": 50, "unaccounted": 50},\n'
            '  "recommendations": ["Recycle mother liquor", "Optimize wash volume", "Check transfer losses"]\n'
            '}\n'
            "Only output valid JSON. No markdown."
        )
        prompt = self._with_predictive_grounding(experiment_data)
        prompt += f"Experiment data:\n{json.dumps(experiment_data, indent=2, default=str)[:6000]}\n"
        text = await self._call_ai(prompt, system=system, temperature=0.2)
        try:
            return json.loads(text)
        except Exception:
            return {
                "parse_error": True,
                "raw_response": text[:3000],
                "reduction_opportunities": [],
                "recommendations": ["Could not parse AI analysis. Review raw output."],
            }

    async def compare_to_standard(
        self,
        experiment_data: dict[str, Any],
        standard_conditions: dict[str, Any],
    ) -> dict[str, Any]:
        """Compare experiment actuals against standard/template conditions.

        Flags deviations and suggests root causes.
        """
        system = (
            "You are a QA and process analytical chemist. Compare experiment actual data against standard conditions. "
            "Output valid JSON with:\n"
            '{\n'
            '  "overall_status": "compliant" | "at_risk" | "non_compliant",\n'
            '  "deviations": [\n'
            '    {"category": "rm|yield|temp|ph|pressure|test", "stage_name": "...", "parameter": "...", "standard": "...", "actual": "...", "deviation_pct": 15, "severity": "warn" | "critical", "root_cause_suggestion": "..."}\n'
            '  ],\n'
            '  "compliance_pct": 85,\n'
            '  "action_items": ["..."]\n'
            '}\n'
            "Only output valid JSON. No markdown."
        )
        prompt = self._with_predictive_grounding(experiment_data)
        prompt += (
            f"Experiment actual data:\n{json.dumps(experiment_data, indent=2, default=str)[:4000]}\n\n"
            f"Standard conditions:\n{json.dumps(standard_conditions, indent=2, default=str)[:4000]}\n"
        )
        text = await self._call_ai(prompt, system=system, temperature=0.15)
        try:
            return json.loads(text)
        except Exception:
            return {
                "overall_status": "at_risk",
                "parse_error": True,
                "raw_response": text[:3000],
                "deviations": [],
                "action_items": ["Could not parse AI comparison. Review raw output."],
            }
