from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Generator
from urllib.parse import urlencode

import httpx

from .omni_brain import ingest_knowledge, schedule_task
from .telemetry import log_event
from .trust_contract import build_trust, evidence_from_mailbox_digest


ROOT_DIR = Path(__file__).resolve().parents[1]
MAILBOX_DB = Path(os.getenv("SHIMS_MAILBOX_DB", ROOT_DIR / "data" / "state" / "shims_mailbox.sqlite3")).resolve()
DEFAULT_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.metadata"]
HEADER_NAMES = ["From", "To", "Cc", "Subject", "Date"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, limit: int = 4000) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    """Open the mailbox database and close it on exit."""
    MAILBOX_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(MAILBOX_DB))
    con.row_factory = sqlite3.Row
    try:
        ensure_mailbox_schema(con)
        con.commit()
        yield con
    finally:
        con.close()


def ensure_mailbox_schema(con: sqlite3.Connection | None = None) -> None:
    own = con is None
    if con is None:
        MAILBOX_DB.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(MAILBOX_DB))
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS capture_items (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT,
            text TEXT,
            metadata_json TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_capture_items_created ON capture_items(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_capture_items_status ON capture_items(status);

        CREATE TABLE IF NOT EXISTS mailbox_messages (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            external_id TEXT,
            thread_id TEXT,
            sender TEXT,
            recipients TEXT,
            subject TEXT,
            snippet TEXT,
            body TEXT,
            labels_json TEXT,
            received_at TEXT,
            source_url TEXT,
            metadata_json TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(provider, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_mailbox_messages_created ON mailbox_messages(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_mailbox_messages_status ON mailbox_messages(status);

        CREATE TABLE IF NOT EXISTS gmail_tokens (
            account TEXT PRIMARY KEY,
            access_token TEXT,
            refresh_token TEXT,
            scope TEXT,
            token_type TEXT,
            expires_at TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )
    if own:
        con.commit()
        con.close()


def _row_to_capture(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["metadata"] = _load_json(data.pop("metadata_json", None), {})
    return data


def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["labels"] = _load_json(data.pop("labels_json", None), [])
    data["metadata"] = _load_json(data.pop("metadata_json", None), {})
    return data


def gmail_scopes() -> list[str]:
    raw = os.getenv("SHIMS_GMAIL_SCOPES", "").strip()
    if not raw:
        return list(DEFAULT_GMAIL_SCOPES)
    return [s.strip() for s in raw.replace(",", " ").split() if s.strip()]


def gmail_config() -> dict[str, Any]:
    scopes = gmail_scopes()
    return {
        "client_id_configured": bool(os.getenv("SHIMS_GMAIL_CLIENT_ID", "").strip()),
        "redirect_uri": os.getenv("SHIMS_GMAIL_REDIRECT_URI", "http://127.0.0.1:8010/mailbox/oauth/callback"),
        "scopes": scopes,
        "restricted_scopes": [s for s in scopes if s in {
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.metadata",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://mail.google.com/",
        }],
        "access_token_configured": bool(os.getenv("SHIMS_GMAIL_ACCESS_TOKEN", "").strip()),
        "client_secret_configured": bool(os.getenv("SHIMS_GMAIL_CLIENT_SECRET", "").strip()),
        "send_enabled": bool(set(scopes) & GMAIL_SEND_SCOPES),
        "mode": "explicit_oauth_consent_required",
    }


def gmail_auth_url(state: str | None = None) -> dict[str, Any]:
    cfg = gmail_config()
    client_id = os.getenv("SHIMS_GMAIL_CLIENT_ID", "").strip()
    state = state or uuid.uuid4().hex
    if not client_id:
        return {
            "ok": False,
            "configured": False,
            "message": "Set SHIMS_GMAIL_CLIENT_ID and SHIMS_GMAIL_REDIRECT_URI to enable Gmail OAuth.",
            "config": cfg,
        }
    params = {
        "client_id": client_id,
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(cfg["scopes"]),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return {
        "ok": True,
        "configured": True,
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params),
        "state": state,
        "config": cfg,
        "notice": "Gmail sync starts only after the user completes Google's OAuth consent screen.",
    }


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_SCOPES = {
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://mail.google.com/",
}


def gmail_send_enabled() -> bool:
    """True when the configured scopes permit sending mail."""
    return bool(set(gmail_scopes()) & GMAIL_SEND_SCOPES)


def _store_token(payload: dict[str, Any], account: str = "me") -> None:
    """Persist an OAuth token response. Refresh tokens are kept across refreshes."""
    expires_in = int(payload.get("expires_in", 0) or 0)
    expires_at = (
        datetime.now(timezone.utc).timestamp() + expires_in - 60  # 60s safety margin
    )
    with _connect() as con:
        existing = con.execute(
            "SELECT refresh_token FROM gmail_tokens WHERE account=?", (account,)
        ).fetchone()
        refresh = payload.get("refresh_token") or (existing["refresh_token"] if existing else None)
        con.execute(
            """INSERT INTO gmail_tokens(account, access_token, refresh_token, scope, token_type, expires_at, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(account) DO UPDATE SET
                 access_token=excluded.access_token,
                 refresh_token=COALESCE(excluded.refresh_token, gmail_tokens.refresh_token),
                 scope=excluded.scope, token_type=excluded.token_type,
                 expires_at=excluded.expires_at, updated_at=excluded.updated_at""",
            (account, payload.get("access_token", ""), refresh, payload.get("scope", ""),
             payload.get("token_type", "Bearer"), str(expires_at), _now()),
        )
        con.commit()


def exchange_code_for_token(code: str, account: str = "me") -> dict[str, Any]:
    """Exchange an OAuth authorization code for access + refresh tokens and store them.

    Requires SHIMS_GMAIL_CLIENT_ID and SHIMS_GMAIL_CLIENT_SECRET. The resulting
    refresh token lets SHIMS keep a valid access token without re-consent.
    """
    client_id = os.getenv("SHIMS_GMAIL_CLIENT_ID", "").strip()
    client_secret = os.getenv("SHIMS_GMAIL_CLIENT_SECRET", "").strip()
    if not code:
        return {"ok": False, "status": "missing_code", "message": "No authorization code supplied."}
    if not client_id or not client_secret:
        return {"ok": False, "status": "not_configured",
                "message": "Set SHIMS_GMAIL_CLIENT_ID and SHIMS_GMAIL_CLIENT_SECRET to complete OAuth."}
    redirect_uri = os.getenv("SHIMS_GMAIL_REDIRECT_URI", "http://127.0.0.1:8010/mailbox/oauth/callback")
    data = {
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(GOOGLE_TOKEN_URL, data=data)
        if resp.status_code != 200:
            return {"ok": False, "status": "token_error", "http_status": resp.status_code,
                    "message": resp.text[:500]}
        payload = resp.json()
    except Exception as exc:  # network/parse failure
        return {"ok": False, "status": "exception", "message": str(exc)}
    _store_token(payload, account=account)
    log_event("gmail_oauth_token_stored", route="mailbox:oauth", provider="gmail",
              ok=True, metadata={"account": account, "scope": payload.get("scope", "")})
    return {"ok": True, "status": "authorized", "scope": payload.get("scope", ""),
            "has_refresh_token": bool(payload.get("refresh_token"))}


def _refresh_access_token(account: str = "me") -> str | None:
    client_id = os.getenv("SHIMS_GMAIL_CLIENT_ID", "").strip()
    client_secret = os.getenv("SHIMS_GMAIL_CLIENT_SECRET", "").strip()
    with _connect() as con:
        row = con.execute("SELECT refresh_token FROM gmail_tokens WHERE account=?", (account,)).fetchone()
    refresh = row["refresh_token"] if row else None
    if not (refresh and client_id and client_secret):
        return None
    data = {"client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh, "grant_type": "refresh_token"}
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(GOOGLE_TOKEN_URL, data=data)
        if resp.status_code != 200:
            return None
        payload = resp.json()
    except Exception:
        return None
    _store_token(payload, account=account)
    return payload.get("access_token")


def get_access_token(account: str = "me") -> str | None:
    """Return a valid Gmail access token, refreshing or falling back to env as needed."""
    with _connect() as con:
        row = con.execute(
            "SELECT access_token, expires_at FROM gmail_tokens WHERE account=?", (account,)
        ).fetchone()
    if row and row["access_token"]:
        try:
            expires_at = float(row["expires_at"] or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at > datetime.now(timezone.utc).timestamp():
            return row["access_token"]
        refreshed = _refresh_access_token(account)
        if refreshed:
            return refreshed
    env_token = os.getenv("SHIMS_GMAIL_ACCESS_TOKEN", "").strip()
    return env_token or None


def send_gmail_message(
    to: str,
    subject: str,
    body: str,
    *,
    cc: str = "",
    thread_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    account: str = "me",
) -> dict[str, Any]:
    """Send (or reply to) an email through the Gmail API.

    Requires a token with a send-capable scope (gmail.send / compose / modify).
    For replies, pass thread_id and the original Message-ID via in_reply_to so the
    message threads correctly in the recipient's client.
    """
    import base64
    from email.message import EmailMessage

    if not gmail_send_enabled():
        return {"ok": False, "status": "scope_required",
                "message": "Configure SHIMS_GMAIL_SCOPES to include gmail.send or gmail.compose to send mail.",
                "send_scopes": sorted(GMAIL_SEND_SCOPES)}
    token = get_access_token(account)
    if not token:
        return {"ok": False, "status": "needs_oauth", "auth": gmail_auth_url(),
                "message": "No Gmail access token. Complete OAuth consent first."}
    if not (to and (subject or body)):
        return {"ok": False, "status": "invalid", "message": "Recipient and subject/body are required."}

    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    payload: dict[str, Any] = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {token}"}, json=payload,
            )
        if resp.status_code not in (200, 201):
            return {"ok": False, "status": "send_error", "http_status": resp.status_code,
                    "message": resp.text[:500]}
        sent = resp.json()
    except Exception as exc:
        return {"ok": False, "status": "exception", "message": str(exc)}
    log_event("gmail_message_sent", route="mailbox:gmail_send", provider="gmail", ok=True,
              message=subject, metadata={"to": to, "thread_id": sent.get("threadId"), "id": sent.get("id")})
    return {"ok": True, "status": "sent", "id": sent.get("id"), "thread_id": sent.get("threadId")}


def reply_to_gmail_message(message_id: str, body: str, account: str = "me") -> dict[str, Any]:
    """Reply to a previously-synced mailbox message, preserving subject and thread."""
    stored = get_mail_message(message_id)
    if not stored:
        return {"ok": False, "status": "not_found", "message": f"No stored message {message_id}."}
    sender = stored.get("sender", "")
    subject = stored.get("subject", "") or "(no subject)"
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    meta = stored.get("metadata") or {}
    rfc_message_id = meta.get("rfc822_message_id") or stored.get("external_id")
    return send_gmail_message(
        to=sender, subject=reply_subject, body=body,
        thread_id=stored.get("thread_id") or None,
        in_reply_to=rfc_message_id, account=account,
    )


def mailbox_policy() -> dict[str, Any]:
    return {
        "ok": True,
        "product_positioning": "SHIMS mailbox is a user-facing productivity assistant for triage, summaries, tasks, and enterprise follow-up.",
        "gmail_access": "No hidden Gmail access. The user must explicitly authorize OAuth scopes before sync.",
        "default_scope": DEFAULT_GMAIL_SCOPES[0],
        "limited_use": [
            "Use Gmail-derived data only for visible mailbox, task, memory, and user-requested assistant features.",
            "Do not sell, transfer, or use Gmail data for advertising.",
            "Do not train a shared model on Gmail data; keep retrieval local/user-specific.",
            "Expose delete/export paths for mailbox and capture data.",
        ],
        "play_store_data_safety": {
            "email_subject_sender_headers": "Emails / app functionality / optional",
            "web_links_shared_to_shims": "Web browsing or user-generated content / app functionality and personalization / optional",
            "oauth_tokens": "Stored only if configured by deployment; must be encrypted at rest in production.",
        },
    }


def save_capture(
    *,
    title: str,
    text: str = "",
    url: str = "",
    kind: str = "link",
    source: str = "share",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = _clean(title or url or "Untitled capture", 240) or "Untitled capture"
    text = _clean(text, 12000)
    url = _clean(url, 1000)
    kind = _clean(kind or "note", 80) or "note"
    source = _clean(source or "share", 120) or "share"
    item_id = "cap_" + uuid.uuid4().hex[:18]
    now = _now()
    with _connect() as con:
        con.execute(
            """
            INSERT INTO capture_items(id, kind, source, title, url, text, metadata_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
            """,
            (item_id, kind, source, title, url, text, _json(metadata or {}), now, now),
        )
        con.commit()
    brain_text = "\n".join(x for x in [title, f"URL: {url}" if url else "", text] if x)
    ingest = ingest_knowledge(
        title=f"Capture: {title}",
        text=brain_text,
        source_type="capture",
        source_uri=url or item_id,
        tags=["capture", source, kind],
        importance=1.1,
    )
    task = schedule_task(
        "capture_review",
        title=f"Review capture: {title[:90]}",
        payload={"capture_id": item_id, "title": title, "url": url, "source": source},
        priority=4,
    )
    log_event("mailbox.capture.saved", route="capture:share", provider="local", model="mailbox-v1", ok=True, message=title, metadata={"kind": kind, "source": source, "url": url})
    return {"ok": True, "item": get_capture(item_id), "brain_ingest": ingest, "task": task}


def get_capture(item_id: str) -> dict[str, Any] | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM capture_items WHERE id=?", (item_id,)).fetchone()
    return _row_to_capture(row) if row else None


def list_captures(limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    with _connect() as con:
        if status:
            rows = con.execute("SELECT * FROM capture_items WHERE status=? ORDER BY created_at DESC LIMIT ?", (_clean(status, 40), limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM capture_items ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_capture(r) for r in rows]


def save_mail_message(
    *,
    provider: str = "local",
    external_id: str = "",
    thread_id: str = "",
    sender: str = "",
    recipients: str = "",
    subject: str = "",
    snippet: str = "",
    body: str = "",
    labels: list[str] | None = None,
    received_at: str = "",
    source_url: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = _clean(provider or "local", 80)
    external_id = _clean(external_id or ("local_" + uuid.uuid4().hex[:18]), 180)
    subject = _clean(subject or "(no subject)", 500)
    now = _now()
    msg_id = "mail_" + uuid.uuid5(uuid.NAMESPACE_URL, provider + ":" + external_id).hex[:22]
    with _connect() as con:
        con.execute(
            """
            INSERT INTO mailbox_messages(
                id, provider, external_id, thread_id, sender, recipients, subject, snippet, body,
                labels_json, received_at, source_url, metadata_json, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
            ON CONFLICT(provider, external_id) DO UPDATE SET
                thread_id=excluded.thread_id,
                sender=excluded.sender,
                recipients=excluded.recipients,
                subject=excluded.subject,
                snippet=excluded.snippet,
                body=excluded.body,
                labels_json=excluded.labels_json,
                received_at=excluded.received_at,
                source_url=excluded.source_url,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                msg_id,
                provider,
                external_id,
                _clean(thread_id, 180),
                _clean(sender, 500),
                _clean(recipients, 800),
                subject,
                _clean(snippet, 1500),
                _clean(body, 16000),
                _json(labels or []),
                _clean(received_at or now, 120),
                _clean(source_url, 1000),
                _json(metadata or {}),
                now,
                now,
            ),
        )
        con.commit()
    text = f"From: {sender}\nTo: {recipients}\nSubject: {subject}\nSnippet: {snippet}\n{body}".strip()
    ingest_knowledge(
        title=f"Mailbox: {subject}",
        text=text,
        source_type="mailbox",
        source_uri=source_url or f"{provider}:{external_id}",
        tags=["mailbox", provider],
        importance=1.05,
    )
    lower = " ".join([subject, snippet, body]).lower()
    if any(k in lower for k in ("urgent", "rfq", "quote", "invoice", "approval", "meeting", "deadline", "payment")):
        schedule_task(
            "mailbox_follow_up",
            title=f"Mailbox follow-up: {subject[:90]}",
            payload={"message_id": msg_id, "provider": provider, "subject": subject, "sender": sender},
            priority=3,
        )
    log_event("mailbox.message.saved", route="mailbox:import", provider=provider, model="mailbox-v1", ok=True, message=subject)
    return {"ok": True, "message": get_mail_message(msg_id)}


def get_mail_message(message_id: str) -> dict[str, Any] | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM mailbox_messages WHERE id=?", (message_id,)).fetchone()
    return _row_to_message(row) if row else None


def list_mail_messages(limit: int = 50, provider: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    with _connect() as con:
        if provider:
            rows = con.execute("SELECT * FROM mailbox_messages WHERE provider=? ORDER BY COALESCE(received_at, created_at) DESC LIMIT ?", (_clean(provider, 80), limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM mailbox_messages ORDER BY COALESCE(received_at, created_at) DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_message(r) for r in rows]


def mailbox_digest(limit: int = 20) -> dict[str, Any]:
    messages = list_mail_messages(limit=limit)
    captures = list_captures(limit=limit)
    action_words = ("urgent", "rfq", "quote", "invoice", "approval", "meeting", "deadline", "payment")
    actions = []
    for msg in messages:
        hay = " ".join([msg.get("subject") or "", msg.get("snippet") or "", msg.get("body") or ""]).lower()
        if any(w in hay for w in action_words):
            actions.append({"type": "mail", "id": msg["id"], "title": msg["subject"], "from": msg.get("sender", "")})
    for cap in captures:
        if cap.get("url"):
            actions.append({"type": "capture", "id": cap["id"], "title": cap["title"], "url": cap.get("url")})
    digest = {
        "ok": True,
        "counts": {"messages": len(messages), "captures": len(captures), "action_candidates": len(actions)},
        "messages": messages[:10],
        "captures": captures[:10],
        "action_candidates": actions[:12],
        "policy": mailbox_policy(),
    }
    evidence = evidence_from_mailbox_digest(digest, limit=8)
    trust = build_trust(
        route="mailbox:digest",
        evidence=evidence,
        missing_evidence=[] if evidence else ["No mailbox messages or captures are available yet."],
        requested_level="draft",
    )
    digest["trust"] = trust
    digest["evidence"] = trust["evidence"]
    digest["confidence"] = trust["confidence"]
    return digest


def mailbox_status() -> dict[str, Any]:
    ensure_mailbox_schema()
    with _connect() as con:
        counts = {
            "messages": con.execute("SELECT COUNT(*) FROM mailbox_messages").fetchone()[0],
            "captures": con.execute("SELECT COUNT(*) FROM capture_items").fetchone()[0],
            "new_messages": con.execute("SELECT COUNT(*) FROM mailbox_messages WHERE status='new'").fetchone()[0],
            "new_captures": con.execute("SELECT COUNT(*) FROM capture_items WHERE status='new'").fetchone()[0],
        }
    return {"ok": True, "version": "mailbox-capture-v1", "db_path": str(MAILBOX_DB), "counts": counts, "gmail": gmail_config(), "policy": mailbox_policy()}


def _headers_to_dict(payload: dict[str, Any]) -> dict[str, str]:
    headers = (payload.get("headers") or []) if payload else []
    return {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in headers if h.get("name")}


def _date_to_iso(value: str) -> str:
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def sync_gmail_metadata(*, access_token: str | None = None, query: str = "", max_results: int = 10) -> dict[str, Any]:
    token = (access_token or get_access_token() or "").strip()
    if not token:
        return {"ok": False, "status": "needs_oauth", "auth": gmail_auth_url(), "message": "No Gmail access token configured."}
    max_results = max(1, min(int(max_results or 10), 25))
    headers = {"Authorization": f"Bearer {token}"}
    params: list[tuple[str, str]] = [("maxResults", str(max_results))]
    if query.strip():
        params.append(("q", query.strip()))
    stored: list[dict[str, Any]] = []
    with httpx.Client(timeout=20) as client:
        listed = client.get("https://gmail.googleapis.com/gmail/v1/users/me/messages", headers=headers, params=params)
        listed.raise_for_status()
        for item in (listed.json().get("messages") or [])[:max_results]:
            message_id = item.get("id")
            if not message_id:
                continue
            detail_params: list[tuple[str, str]] = [("format", "metadata")]
            detail_params += [("metadataHeaders", h) for h in HEADER_NAMES]
            detail = client.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}", headers=headers, params=detail_params)
            detail.raise_for_status()
            data = detail.json()
            hd = _headers_to_dict(data.get("payload") or {})
            saved = save_mail_message(
                provider="gmail",
                external_id=data.get("id") or message_id,
                thread_id=data.get("threadId") or "",
                sender=hd.get("from", ""),
                recipients=", ".join(x for x in [hd.get("to", ""), hd.get("cc", "")] if x),
                subject=hd.get("subject", "(no subject)"),
                snippet=data.get("snippet") or "",
                body="",
                labels=data.get("labelIds") or [],
                received_at=_date_to_iso(hd.get("date", "")),
                source_url=f"https://mail.google.com/mail/u/0/#inbox/{data.get('id') or message_id}",
                metadata={"historyId": data.get("historyId"), "sizeEstimate": data.get("sizeEstimate")},
            )
            stored.append(saved["message"])
    return {"ok": True, "provider": "gmail", "stored": len(stored), "messages": stored, "query": query}
