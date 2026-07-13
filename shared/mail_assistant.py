"""Mail Assistant — unified desktop mail orchestration for SHIMS Omni.

Combines Gmail API tools and browser automation so SHIMS can help organize
and send mail whether the user has OAuth configured or is simply logged into
Gmail in their desktop browser.
"""
from __future__ import annotations

import asyncio
from typing import Any

from . import browser_agent, mailbox


async def check_mail_status() -> dict[str, Any]:
    """Return a snapshot of available mail channels."""
    api_ready = mailbox.gmail_send_enabled() and mailbox.get_access_token() is not None
    browser_ready = False
    browser_user = None
    try:
        # Lightweight probe: visit Gmail and look for sign-in indicator
        result = await browser_agent.visit("https://mail.google.com/mail/u/0/#inbox", wait_for="body", scroll=False)
        page_text = result.get("text", "")
        browser_ready = "Sign in" not in page_text and ("Inbox" in page_text or "Primary" in page_text or "Compose" in page_text)
        if browser_ready:
            # Try to extract account name from Google account avatar area
            ex = await browser_agent.extract("https://mail.google.com/mail/u/0/#inbox", "a[href*='Account']")
            browser_user = ex.get("text", "")[:80] or "unknown"
    except Exception as exc:
        browser_ready = False
        browser_user = str(exc)[:80]

    return {
        "ok": True,
        "api_ready": api_ready,
        "browser_ready": browser_ready,
        "browser_user": browser_user,
        "recommended_channel": "api" if api_ready else ("browser" if browser_ready else "none"),
    }


async def mail_digest(limit: int = 10) -> dict[str, Any]:
    """Return a concise digest of recent/unread mail."""
    status = await check_mail_status()
    channel = status.get("recommended_channel")
    if channel == "api":
        return mailbox.mailbox_digest(limit=limit)
    if channel == "browser":
        try:
            await browser_agent.visit("https://mail.google.com/mail/u/0/#inbox")
            # Extract sender + subject lines from the inbox table
            result = await browser_agent.extract("https://mail.google.com/mail/u/0/#inbox", "table[role=grid]")
            return {
                "ok": True,
                "channel": "browser",
                "digest": result.get("text", "")[:4000],
                "note": "Browser-based digest. For richer organization, connect Gmail API via /mail auth.",
            }
        except Exception as exc:
            return {"ok": False, "error": f"browser digest failed: {exc}"}
    return {"ok": False, "error": "No mail channel available. Open Gmail in your browser or run Gmail OAuth setup."}


async def mail_organize(criteria: str, action: str = "label") -> dict[str, Any]:
    """Organize mail matching criteria. Uses API if available; otherwise plans a browser macro."""
    status = await check_mail_status()
    channel = status.get("recommended_channel")
    if channel == "api":
        return mailbox.organize_gmail(criteria=criteria, action=action)
    if channel == "browser":
        # Build a Gmail search URL and visit it; actual apply requires human confirmation
        search_url = f"https://mail.google.com/mail/u/0/#search/{criteria.replace(' ', '+')}"
        try:
            await browser_agent.visit(search_url)
            return {
                "ok": True,
                "channel": "browser",
                "search_url": search_url,
                "needs_confirmation": True,
                "message": f"Opened Gmail search for '{criteria}'. Apply {action} manually, or confirm to let SHIMS select-all + apply.",
            }
        except Exception as exc:
            return {"ok": False, "error": f"browser organize failed: {exc}"}
    return {"ok": False, "error": "No mail channel available. Open Gmail in your browser or run Gmail OAuth setup."}


async def mail_compose(to: str, subject: str, body: str) -> dict[str, Any]:
    """Compose/send mail via API or browser."""
    status = await check_mail_status()
    channel = status.get("recommended_channel")
    if channel == "api":
        return mailbox.send_gmail_message(to=to, subject=subject, body=body)
    if channel == "browser":
        try:
            await browser_agent.visit("https://mail.google.com/mail/u/0/#compose")
            await asyncio.sleep(0.5)
            # Best-effort fill of the compose iframe is fragile; return URL + guidance
            return {
                "ok": True,
                "channel": "browser",
                "compose_url": f"https://mail.google.com/mail/u/0/?view=cm&to={to.replace(',', '%2C')}&su={subject.replace(' ', '+')}&body={body.replace(' ', '+')}",
                "needs_confirmation": True,
                "message": "Browser compose window opened. Confirm to let SHIMS fill and send, or finish manually.",
            }
        except Exception as exc:
            return {"ok": False, "error": f"browser compose failed: {exc}"}
    return {"ok": False, "error": "No mail channel available. Open Gmail in your browser or run Gmail OAuth setup."}
