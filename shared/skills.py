"""Procedural memory — SHIMS's growing library of learned skills / preferences.

A *skill* is a named, reusable thing SHIMS learned about how to help this user —
e.g. "COA formatting preference", "preferred email tone", "how to lay out an SOP".
This is the part of "ever-evolving" that actually sticks and is inspectable:
skills are plain files the user can list, pin, edit, or delete.

Storage: one JSON sidecar per skill under ``storage/skills/`` (plus an optional
markdown body for long content). No database, fully portable and human-readable.
"""
# SHIMS skill memory module — runtime tested.
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .config import STORAGE_DIR
from .security import new_id

SKILLS_DIR = STORAGE_DIR / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def _path(skill_id: str) -> Path:
    return SKILLS_DIR / f"{skill_id}.json"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_skill(
    name: str,
    summary: str,
    *,
    body: str = "",
    tags: list[str] | None = None,
    pinned: bool = False,
    source: str = "user",
    skill_id: str | None = None,
    weight: float = 1.0,
    runtime: str | None = None,
    tool_schema: dict[str, Any] | None = None,
    tool_code: str = "",
    tool_name: str = "",
) -> dict[str, Any]:
    """Create or update a skill. If ``skill_id`` is given, updates in place."""
    name = (name or "").strip() or "Untitled skill"
    now = time.time()
    if skill_id:
        existing = _read(_path(skill_id)) or {}
    else:
        # De-dupe by exact name (case-insensitive) so repeated learning updates, not piles up.
        for p in SKILLS_DIR.glob("*.json"):
            d = _read(p) or {}
            if d.get("name", "").strip().lower() == name.lower():
                existing, skill_id = d, d.get("id")
                break
        else:
            existing, skill_id = {}, new_id("skill")
    skill: dict[str, Any] = {
        "id": skill_id,
        "name": name,
        "summary": (summary or "").strip(),
        "body": body or existing.get("body", ""),
        "tags": tags if tags is not None else existing.get("tags", []),
        "pinned": pinned or existing.get("pinned", False),
        "source": source or existing.get("source", "user"),
        "weight": float(weight if weight is not None else existing.get("weight", 1.0)),
        "uses": existing.get("uses", 0),
        "created_at": existing.get("created_at", now),
        "updated_at": now,
    }
    if runtime:
        skill["runtime"] = runtime
    elif existing.get("runtime"):
        skill["runtime"] = existing["runtime"]
    if tool_schema:
        skill["tool_schema"] = tool_schema
    elif existing.get("tool_schema"):
        skill["tool_schema"] = existing["tool_schema"]
    if tool_code:
        skill["tool_code"] = tool_code
    elif existing.get("tool_code"):
        skill["tool_code"] = existing["tool_code"]
    if tool_name:
        skill["tool_name"] = tool_name
    elif existing.get("tool_name"):
        skill["tool_name"] = existing["tool_name"]
    _path(skill_id).write_text(json.dumps(skill, indent=2, ensure_ascii=False), encoding="utf-8")
    return skill


def get_skill(skill_id: str) -> dict[str, Any] | None:
    return _read(_path(skill_id))


def list_skills(query: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    skills = [d for p in SKILLS_DIR.glob("*.json") if (d := _read(p))]
    if query:
        q = _tokens(query)
        skills = [s for s in skills if q & _tokens(s.get("name", "") + " " + s.get("summary", "") + " " + " ".join(s.get("tags", [])))]
    skills.sort(key=lambda s: (not s.get("pinned"), -s.get("updated_at", 0)))
    return skills[:limit]


def forget_skill(skill_id: str) -> bool:
    p = _path(skill_id)
    if p.exists():
        p.unlink()
        return True
    return False


def _score(query_tokens: set[str], skill: dict[str, Any]) -> float:
    text = " ".join([skill.get("name", ""), skill.get("summary", ""),
                     " ".join(skill.get("tags", [])), skill.get("body", "")])
    st = _tokens(text)
    if not st:
        return 0.0
    overlap = len(query_tokens & st)
    if overlap == 0:
        return 0.0
    score = overlap / (len(query_tokens) + 1)
    score *= float(skill.get("weight", 1.0))
    if skill.get("pinned"):
        score *= 1.5
    return score


def relevant_skills(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Return the top skills relevant to ``query`` (for prompt injection)."""
    q = _tokens(query)
    if not q:
        # No query terms — surface pinned skills only.
        return [s for s in list_skills(limit=limit) if s.get("pinned")][:limit]
    scored = [(s, _score(q, s)) for s in list_skills(limit=500)]
    scored = [(s, v) for s, v in scored if v > 0 or s.get("pinned")]
    scored.sort(key=lambda sv: sv[1], reverse=True)
    return [s for s, _ in scored[:limit]]


def touch_skill(skill_id: str) -> None:
    """Increment usage count when a skill is applied (lightweight reinforcement)."""
    d = _read(_path(skill_id))
    if d:
        d["uses"] = int(d.get("uses", 0)) + 1
        d["updated_at"] = time.time()
        _path(skill_id).write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


# Heuristic extractors for the background skill-extraction task. Kept deterministic
# (no LLM dependency) so it always runs; the LLM path can enrich later.
_PREF_PATTERNS = [
    (re.compile(r"\bi (?:prefer|like|want|always|usually)\b(.{4,140})", re.I), "preference"),
    (re.compile(r"\b(?:please )?(?:always|never|from now on)\b(.{4,140})", re.I), "directive"),
    (re.compile(r"\bremember (?:that |to )?(.{4,140})", re.I), "memory"),
]


def extract_skill_candidates(text: str) -> list[dict[str, str]]:
    """Pull candidate preference/directive statements out of a user's message."""
    out: list[dict[str, str]] = []
    for line in (text or "").splitlines():
        for pat, kind in _PREF_PATTERNS:
            m = pat.search(line)
            if m:
                phrase = m.group(1).strip(" .,:;-")
                if len(phrase) >= 4:
                    out.append({"kind": kind, "phrase": phrase, "full": line.strip()[:200]})
    return out
