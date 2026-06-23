"""Web crawler — fetches and extracts full page content, not just search snippets."""
from __future__ import annotations

import re
from typing import Any, Optional

import httpx


# Sites we should not crawl
BLOCKED_DOMAINS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
    "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
}

MAX_PAGE_SIZE = 500_000  # 500KB max HTML
TIMEOUT = 15


def _is_blocked(url: str) -> bool:
    low = url.lower()
    for blocked in BLOCKED_DOMAINS:
        if blocked in low:
            return True
    return False


def _extract_text(html: str, url: str) -> str:
    """Extract readable text from HTML."""
    # Remove scripts and styles
    html = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", html, flags=re.I)
    html = re.sub(r"<nav[^>]*>[\s\S]*?</nav>", " ", html, flags=re.I)
    html = re.sub(r"<footer[^>]*>[\s\S]*?</footer>", " ", html, flags=re.I)
    html = re.sub(r"<header[^>]*>[\s\S]*?</header>", " ", html, flags=re.I)
    html = re.sub(r"<aside[^>]*>[\s\S]*?</aside>", " ", html, flags=re.I)
    # Replace common block tags with newlines
    for tag in ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th", "br"]:
        html = re.sub(rf"</{tag}>", "\n", html, flags=re.I)
        html = re.sub(rf"<{tag}[^>]*>", "\n", html, flags=re.I)
    # Remove all remaining tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode common entities
    html = html.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    # Clean whitespace
    lines = []
    for line in html.splitlines():
        line = line.strip()
        if line and len(line) > 2:
            lines.append(line)
    text = "\n".join(lines)
    # Deduplicate whitespace
    text = re.sub(r"\s+", " ", text)
    # Limit length
    if len(text) > 50_000:
        text = text[:50_000] + "\n\n[Content truncated at 50KB]"
    return text.strip()


async def fetch_page(url: str, max_length: int = 25_000) -> dict[str, Any]:
    """Fetch a single page and extract its text content."""
    url = url.strip()
    if not url:
        return {"ok": False, "error": "Empty URL"}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if _is_blocked(url):
        return {"ok": False, "error": "Blocked URL"}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}) as client:
            r = await client.get(url)
            r.raise_for_status()
            content_type = r.headers.get("content-type", "").lower()
            if "application/pdf" in content_type:
                return {"ok": True, "url": url, "title": "PDF document", "text": "[PDF content - download and parse separately]", "content_type": "pdf"}
            html = r.text[:MAX_PAGE_SIZE]
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc)[:300]}

    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = title_match.group(1).strip() if title_match else ""
    title = re.sub(r"<[^>]+>", "", title)

    text = _extract_text(html, url)
    if len(text) > max_length:
        text = text[:max_length] + "\n\n[Truncated]"

    return {
        "ok": True,
        "url": url,
        "title": title,
        "text": text,
        "length": len(text),
        "content_type": "html",
    }


async def deep_research(query: str, search_fn, max_search_results: int = 5, max_pages: int = 3) -> dict[str, Any]:
    """Deep research: search, then fetch and read top pages fully.

    search_fn: async function(query, max_results) -> dict with 'results' list
    """
    # Step 1: Search
    search_result = await search_fn(query, max_search_results)
    if not search_result.get("ok"):
        return {"ok": False, "error": search_result.get("message", "Search failed"), "phase": "search"}

    results = search_result.get("results", [])
    if not results:
        return {"ok": False, "error": "No search results found", "phase": "search"}

    # Step 2: Fetch top pages
    pages: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in results[:max_pages]:
        url = item.get("url", "")
        if not url:
            continue
        page = await fetch_page(url)
        if page.get("ok"):
            pages.append(page)
        else:
            errors.append(f"{url}: {page.get('error', 'unknown')}")

    # Step 3: Compile full context
    context_parts = []
    for page in pages:
        context_parts.append(f"---\nURL: {page['url']}\nTitle: {page['title']}\n\n{page['text']}\n")

    full_context = "\n".join(context_parts)

    return {
        "ok": True,
        "query": query,
        "search_provider": search_result.get("provider"),
        "pages_fetched": len(pages),
        "pages": pages,
        "full_context": full_context,
        "errors": errors,
        "sources": [{"url": p["url"], "title": p["title"], "length": p["length"]} for p in pages],
    }
