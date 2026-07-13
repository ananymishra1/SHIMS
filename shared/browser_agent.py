"""SHIMS Browser Agent — "Kimi Claw" for the web.

A headless-browser agent that can visit pages, search, click, fill forms,
extract data, and take screenshots. Built on Playwright for real browser
behavior (JavaScript execution, cookies, redirects).

Uses Playwright's ASYNC API to work correctly inside FastAPI's async handlers.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import time
from pathlib import Path
from typing import Any

from .config import settings

ROOT_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = ROOT_DIR / "data" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# Playwright async helpers
# --------------------------------------------------------------------------- #

_browser_instance: Any = None
_playwright_instance: Any = None


async def _get_browser():
    """Lazy-init a shared headless Chromium browser."""
    global _browser_instance, _playwright_instance
    if _browser_instance is None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Browser tools require it. "
                "Install with: .venv/Scripts/python -m pip install playwright && .venv/Scripts/python -m playwright install chromium"
            ) from exc
        _playwright_instance = await async_playwright().start()
        _browser_instance = await _playwright_instance.chromium.launch(headless=True)
    return _browser_instance


async def _new_page():
    """Create a new browser page with sensible defaults."""
    browser = await _get_browser()
    page = await browser.new_page(viewport={"width": 1280, "height": 800})
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    return page


async def _close_browser():
    """Shut down the shared browser (call on app shutdown)."""
    global _browser_instance, _playwright_instance
    if _browser_instance:
        await _browser_instance.close()
        _browser_instance = None
    if _playwright_instance:
        await _playwright_instance.stop()
        _playwright_instance = None


# --------------------------------------------------------------------------- #
# Core actions
# --------------------------------------------------------------------------- #

async def visit(url: str, wait_for: str = "", scroll: bool = True) -> dict[str, Any]:
    """Visit a URL and return structured page data."""
    page = await _new_page()
    try:
        await page.goto(url, wait_until="networkidle")
        if wait_for:
            try:
                await page.wait_for_selector(wait_for, timeout=10000)
            except Exception:
                pass
        if scroll:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.3)

        title = await page.title()
        # Extract readable text
        text = await page.evaluate("""() => {
            const el = document.querySelector('main') || document.querySelector('article') || document.body;
            return el.innerText || el.textContent || '';
        }""")
        text = _clean_text(text)

        # Extract links
        links = await page.evaluate("""() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
            text: (a.innerText || a.textContent || '').trim().slice(0,120),
            href: a.href
        })).filter(l => l.text && l.href.startsWith('http'))""")

        # Extract forms
        forms = await page.evaluate("""() => Array.from(document.querySelectorAll('form')).map((f, i) => ({
            index: i,
            action: f.action || '',
            method: f.method || 'get',
            inputs: Array.from(f.querySelectorAll('input, textarea, select')).map(inp => ({
                name: inp.name || '',
                type: inp.type || inp.tagName.toLowerCase(),
                placeholder: inp.placeholder || '',
                required: inp.required || false
            })).filter(i => i.name || i.type)
        }))""")

        # Extract headings
        headings = await page.evaluate("""() => Array.from(document.querySelectorAll('h1,h2,h3')).map(h => ({
            level: h.tagName,
            text: (h.innerText || '').trim().slice(0,200)
        })).filter(h => h.text)""")

        return {
            "ok": True,
            "url": page.url,
            "title": title,
            "text": text[:12000],
            "links": links[:50],
            "forms": forms[:5],
            "headings": headings[:30],
            "word_count": len(text.split()),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400], "url": url}
    finally:
        await page.close()


async def search(query: str, max_results: int = 8) -> dict[str, Any]:
    """Search DuckDuckGo and return results."""
    page = await _new_page()
    try:
        await page.goto(f"https://duckduckgo.com/?q={query.replace(' ', '+')}&ia=web", wait_until="networkidle")
        await asyncio.sleep(1.5)  # Let JS render results

        results = await page.evaluate("""() => {
            const items = [];
            document.querySelectorAll('[data-testid="result-title-a"], .result__a, .eVNpHGjtxRBq_gLOsXOw').forEach((a, i) => {
                if (i > 12) return;
                const snippetEl = a.closest('article, .result, [data-testid="result"], .nrn-react-div') || a.parentElement;
                const snippet = snippetEl ? (snippetEl.innerText || '').replace(a.innerText, '').trim().slice(0,300) : '';
                items.push({
                    title: (a.innerText || a.textContent || '').trim().slice(0,120),
                    url: a.href,
                    snippet: snippet
                });
            });
            return items;
        }""")

        # Fallback: try another selector pattern
        if not results:
            results = await page.evaluate("""() => Array.from(document.querySelectorAll('a')).filter(a => {
                const href = a.href || '';
                return href.includes('duckduckgo.com/l/?') || (href.startsWith('http') && a.querySelector('h2, h3, span'));
            }).map(a => ({
                title: (a.innerText || a.textContent || '').trim().slice(0,120),
                url: a.href,
                snippet: ''
            })).slice(0,12)""")

        # Deduplicate by URL
        seen = set()
        unique = []
        for r in results:
            if r["url"] and r["url"] not in seen and r["title"]:
                seen.add(r["url"])
                unique.append(r)

        return {
            "ok": True,
            "query": query,
            "count": len(unique),
            "results": unique[:max_results],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400], "query": query}
    finally:
        await page.close()


async def click(url: str, selector: str = "", text: str = "") -> dict[str, Any]:
    """Click an element on a page by CSS selector or link text, then return the new page."""
    page = await _new_page()
    try:
        await page.goto(url, wait_until="networkidle")
        clicked = False
        if selector:
            await page.click(selector)
            clicked = True
        elif text:
            # Try exact match, then contains
            try:
                await page.get_by_text(text, exact=True).click()
                clicked = True
            except Exception:
                try:
                    await page.get_by_text(text).click()
                    clicked = True
                except Exception:
                    pass
        if not clicked:
            return {"ok": False, "error": f"Could not find element to click (selector={selector}, text={text})", "url": url}

        await page.wait_for_load_state("networkidle")
        return await visit(page.url)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400], "url": url}
    finally:
        await page.close()


async def extract(url: str, selector: str) -> dict[str, Any]:
    """Extract elements from a page using a CSS selector."""
    page = await _new_page()
    try:
        await page.goto(url, wait_until="networkidle")
        elements = await page.evaluate(f"""() => Array.from(document.querySelectorAll('{selector}')).map(el => ({{
            text: (el.innerText || el.textContent || '').trim().slice(0,500),
            html: el.outerHTML.slice(0,1000),
            href: el.href || ''
        }}))""")
        return {
            "ok": True,
            "url": page.url,
            "selector": selector,
            "count": len(elements),
            "elements": elements,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400], "url": url, "selector": selector}
    finally:
        await page.close()


async def fill_form(url: str, fields: dict[str, str], submit_selector: str = "") -> dict[str, Any]:
    """Fill form fields and optionally submit."""
    page = await _new_page()
    try:
        await page.goto(url, wait_until="networkidle")
        for name, value in fields.items():
            try:
                await page.fill(f'input[name="{name}"], textarea[name="{name}"], #{name}', str(value))
            except Exception:
                # Try by placeholder or label
                try:
                    await page.get_by_label(name).fill(str(value))
                except Exception:
                    pass
        if submit_selector:
            await page.click(submit_selector)
        else:
            await page.press('input[type="text"], textarea', 'Enter')
        await page.wait_for_load_state("networkidle")
        return await visit(page.url)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400], "url": url}
    finally:
        await page.close()


async def screenshot(url: str, selector: str = "", full_page: bool = False) -> dict[str, Any]:
    """Take a screenshot and save to data/screenshots/."""
    page = await _new_page()
    try:
        await page.goto(url, wait_until="networkidle")
        await asyncio.sleep(0.5)
        ts = int(time.time())
        filename = f"browser_{ts}.png"
        path = SCREENSHOT_DIR / filename

        if selector:
            el = page.locator(selector).first
            await el.screenshot(path=str(path))
        else:
            await page.screenshot(path=str(path), full_page=full_page)

        # Also return small base64 thumbnail for inline display
        with path.open("rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")

        return {
            "ok": True,
            "url": page.url,
            "filename": filename,
            "path": str(path),
            "screenshot_url": f"/media/files/screenshot/{filename}",
            "base64": b64[:500] + "..." if len(b64) > 500 else b64,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400], "url": url}
    finally:
        await page.close()


async def scroll(url: str, direction: str = "down", amount: int = 800) -> dict[str, Any]:
    """Scroll a page and return new content."""
    page = await _new_page()
    try:
        await page.goto(url, wait_until="networkidle")
        if direction == "down":
            await page.evaluate(f"window.scrollBy(0, {amount})")
        elif direction == "up":
            await page.evaluate(f"window.scrollBy(0, -{amount})")
        elif direction == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(0.5)
        text = await page.evaluate("""() => {
            const el = document.querySelector('main') || document.querySelector('article') || document.body;
            return (el.innerText || el.textContent || '').trim();
        }""")
        scroll_y = await page.evaluate("() => window.scrollY")
        return {
            "ok": True,
            "url": page.url,
            "scroll_y": scroll_y,
            "text": _clean_text(text)[:6000],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400], "url": url}
    finally:
        await page.close()


# --------------------------------------------------------------------------- #
# Unified runner
# --------------------------------------------------------------------------- #

async def run_action(action: str, **kwargs) -> dict[str, Any]:
    """Run a browser action by name."""
    import asyncio
    actions = {
        "visit": visit,
        "search": search,
        "click": click,
        "extract": extract,
        "fill_form": fill_form,
        "screenshot": screenshot,
        "scroll": scroll,
    }
    fn = actions.get(action)
    if not fn:
        return {"ok": False, "error": f"Unknown browser action: {action}. Available: {list(actions.keys())}"}
    return await fn(**kwargs)
