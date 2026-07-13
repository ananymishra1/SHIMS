"""SHIMS Skill Registry — the hosted layer of the skill marketplace.

The local marketplace ships a curated catalog. The *registry* lets that catalog
be hosted and shared: a SHIMS instance can both **pull** published skills from a
remote registry URL and **publish** its own. Because any SHIMS instance can serve
the registry endpoints (see backend/app/routes_growth.py), a team can self-host a
private registry with zero extra infrastructure, or point at a public one later.

A "published" skill is just a catalog entry persisted to
``data/state/registry/published.json`` and served at ``/registry/skills``.

Env:
  SHIMS_REGISTRY_URL    base URL of a remote registry (e.g. https://hub.shims.ai)
  SHIMS_REGISTRY_TOKEN  optional bearer token for publishing to it
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from .config import ROOT_DIR

_REG_DIR = Path(ROOT_DIR) / "data" / "state" / "registry"
_REG_DIR.mkdir(parents=True, exist_ok=True)
_PUBLISHED = _REG_DIR / "published.json"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def registry_url() -> str:
    return os.getenv("SHIMS_REGISTRY_URL", "").rstrip("/")


def is_configured() -> bool:
    return bool(registry_url())


def _slugify(name: str) -> str:
    return _SLUG_RE.sub("-", (name or "skill").lower()).strip("-") or "skill"


# --------------------------------------------------------------------------- #
# Local registry store (this instance acting AS a registry)
# --------------------------------------------------------------------------- #

def _load_published() -> list[dict[str, Any]]:
    if not _PUBLISHED.exists():
        return []
    try:
        return json.loads(_PUBLISHED.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_published(items: list[dict[str, Any]]) -> None:
    _PUBLISHED.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def published_catalog() -> list[dict[str, Any]]:
    """Entries this instance serves at /registry/skills."""
    return _load_published()


def publish_local(name: str, summary: str, body: str = "", tags: Optional[list[str]] = None,
                  author: str = "community", category: str = "Community") -> dict[str, Any]:
    """Add/replace a skill in this instance's registry."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}
    items = _load_published()
    slug = _slugify(name)
    entry = {
        "slug": slug, "name": name, "summary": summary, "body": body,
        "tags": tags or [], "author": author, "category": category,
        "published_at": time.time(), "downloads": 0,
    }
    items = [e for e in items if e.get("slug") != slug]
    items.append(entry)
    _save_published(items)
    return {"ok": True, "entry": entry}


def record_download(slug: str) -> None:
    items = _load_published()
    for e in items:
        if e.get("slug") == slug:
            e["downloads"] = int(e.get("downloads", 0)) + 1
    _save_published(items)


# --------------------------------------------------------------------------- #
# Remote registry client (this instance pulling FROM / pushing TO a hub)
# --------------------------------------------------------------------------- #

def fetch_remote_catalog(query: Optional[str] = None) -> dict[str, Any]:
    """Pull the catalog from the configured remote registry."""
    base = registry_url()
    if not base:
        return {"ok": False, "error": "no_registry_configured", "skills": []}
    try:
        import httpx
        params = {"query": query} if query else {}
        with httpx.Client(timeout=12, follow_redirects=True) as client:
            r = client.get(f"{base}/registry/skills", params=params)
            r.raise_for_status()
            data = r.json()
        skills = data.get("skills", data if isinstance(data, list) else [])
        return {"ok": True, "skills": skills, "source": base}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160], "skills": []}


def publish_remote(name: str, summary: str, body: str = "",
                   tags: Optional[list[str]] = None) -> dict[str, Any]:
    """Publish a skill to the configured remote registry (requires token)."""
    base = registry_url()
    if not base:
        return {"ok": False, "error": "no_registry_configured"}
    token = os.getenv("SHIMS_REGISTRY_TOKEN", "")
    try:
        import httpx
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with httpx.Client(timeout=12, follow_redirects=True) as client:
            r = client.post(f"{base}/registry/publish", headers=headers,
                            json={"name": name, "summary": summary, "body": body, "tags": tags or []})
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160]}


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "remote_configured": is_configured(),
        "remote_url": registry_url() or "(none)",
        "publish_token_set": bool(os.getenv("SHIMS_REGISTRY_TOKEN", "")),
        "locally_published": len(_load_published()),
    }
