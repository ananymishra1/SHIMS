"""SHIMS Skill Marketplace — an "App Store for AI skills".

Skills are SHIMS's procedural memory. The marketplace makes them shareable:
install curated skills with one click, export your own as a portable pack, and
import packs from teammates or a future hosted registry. It is a thin layer over
``shared.skills`` so installed skills behave exactly like learned ones.

A pack is plain JSON, so it can live in git, be emailed, or be served from a CDN
later without changing this code.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import skills as skill_store
from .config import ROOT_DIR

# Bundled starter catalog ships with the app; a hosted registry can extend it.
_CATALOG_PATH = Path(ROOT_DIR) / "config" / "skill_catalog.json"

_BUILTIN_CATALOG: list[dict[str, Any]] = [
    {
        "slug": "concise-engineer",
        "name": "Concise Engineer",
        "summary": "Answer like a senior engineer: lead with the fix, then the why.",
        "body": "When answering technical questions, give the concrete action first "
                "(command, diff, or code), then a one-paragraph rationale. Avoid hedging. "
                "Prefer working examples over prose.",
        "tags": ["coding", "style", "productivity"],
        "category": "Productivity",
        "author": "SHIMS",
    },
    {
        "slug": "safe-shell",
        "name": "Safe Shell Operator",
        "summary": "Explain destructive commands and dry-run before executing.",
        "body": "Before running any command that deletes, moves, or overwrites files, "
                "describe exactly what it will affect and propose a dry-run or backup first. "
                "Never chain destructive operations without confirmation.",
        "tags": ["shell", "safety", "ops"],
        "category": "Safety",
        "author": "SHIMS",
    },
    {
        "slug": "meeting-notes",
        "name": "Meeting Notes → Actions",
        "summary": "Turn raw notes into decisions, owners, and dated action items.",
        "body": "Given meeting notes, output three sections: Decisions, Action Items "
                "(owner + due date), and Open Questions. Be specific and assign owners "
                "even if you must infer them and flag the inference.",
        "tags": ["writing", "productivity", "summary"],
        "category": "Productivity",
        "author": "SHIMS",
    },
    {
        "slug": "gmp-reviewer",
        "name": "GMP Document Reviewer",
        "summary": "Review batch records against GMP expectations and flag gaps.",
        "body": "When reviewing pharmaceutical batch records, check for: signatures and "
                "dates on each step, in-process control limits, deviation references, and "
                "yield reconciliation. List gaps as a numbered checklist with severity.",
        "tags": ["pharma", "gmp", "compliance"],
        "category": "Enterprise",
        "author": "SHIMS",
    },
    {
        "slug": "privacy-first",
        "name": "Privacy-First Assistant",
        "summary": "Prefer local tools and warn before anything leaves the machine.",
        "body": "Default to local tools and local models. Before fetching the web or using "
                "a cloud provider, state what data would be sent and offer a local alternative.",
        "tags": ["privacy", "local", "safety"],
        "category": "Safety",
        "author": "SHIMS",
    },
]


def _load_catalog() -> list[dict[str, Any]]:
    catalog = list(_BUILTIN_CATALOG)
    if _CATALOG_PATH.exists():
        try:
            extra = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
            if isinstance(extra, list):
                seen = {c["slug"] for c in catalog}
                catalog.extend(c for c in extra if c.get("slug") not in seen)
        except Exception:
            pass
    return catalog


def list_catalog(category: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
    """Browse available skills, with installed-state annotated."""
    installed_names = {s.get("name", "").lower() for s in skill_store.list_skills(limit=500)}
    items = _load_catalog()
    if category and category.lower() != "all":
        items = [c for c in items if c.get("category", "").lower() == category.lower()]
    if query:
        q = query.lower()
        items = [c for c in items
                 if q in c["name"].lower() or q in c["summary"].lower()
                 or any(q in t for t in c.get("tags", []))]
    for c in items:
        c = c  # annotate copy
        c["installed"] = c["name"].lower() in installed_names
    return items


def categories() -> list[str]:
    return ["All"] + sorted({c.get("category", "Other") for c in _load_catalog()})


def install(slug: str) -> dict[str, Any]:
    """Install a catalog skill into the local skill store."""
    item = next((c for c in _load_catalog() if c.get("slug") == slug), None)
    if not item:
        return {"ok": False, "error": f"Unknown skill: {slug}"}
    saved = skill_store.save_skill(
        name=item["name"],
        summary=item["summary"],
        body=item.get("body", ""),
        tags=item.get("tags", []) + ["marketplace"],
        source="marketplace",
    )
    return {"ok": True, "skill": saved}


def export_pack(skill_ids: list[str] | None = None) -> dict[str, Any]:
    """Export skills as a portable pack (all user skills if none specified)."""
    all_skills = skill_store.list_skills(limit=1000)
    if skill_ids:
        wanted = set(skill_ids)
        all_skills = [s for s in all_skills if s.get("id") in wanted]
    pack = {
        "format": "shims-skill-pack",
        "version": 1,
        "exported_at": time.time(),
        "skills": [
            {k: s.get(k) for k in ("name", "summary", "body", "tags", "pinned")}
            for s in all_skills
        ],
    }
    return pack


def import_pack(pack: dict[str, Any], overwrite: bool = False) -> dict[str, Any]:
    """Import a skill pack. Returns count imported."""
    if not isinstance(pack, dict) or pack.get("format") != "shims-skill-pack":
        return {"ok": False, "error": "Not a valid SHIMS skill pack"}
    existing = {s.get("name", "").lower() for s in skill_store.list_skills(limit=1000)}
    count = 0
    for s in pack.get("skills", []):
        name = (s.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in existing and not overwrite:
            continue
        skill_store.save_skill(
            name=name,
            summary=s.get("summary", ""),
            body=s.get("body", ""),
            tags=(s.get("tags") or []) + ["imported"],
            pinned=bool(s.get("pinned", False)),
            source="import",
        )
        count += 1
    return {"ok": True, "imported": count}
