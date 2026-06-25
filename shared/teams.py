"""SHIMS Teams — workspaces, members, roles, and invitations.

A lightweight, file-backed team layer so Pro/Enterprise customers can share a
workspace (and a skill library) with seat limits enforced against the active
license. Storage is JSON under ``data/state/teams`` — swap for Postgres in a
multi-node deployment without changing the public API.

Roles: owner > admin > member. Seats are capped by ``licensing.current_license().seats``.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .config import ROOT_DIR
from . import licensing

_TEAMS_DIR = Path(ROOT_DIR) / "data" / "state" / "teams"
_TEAMS_DIR.mkdir(parents=True, exist_ok=True)


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


_ROLE_RANK = {Role.MEMBER: 0, Role.ADMIN: 1, Role.OWNER: 2}


@dataclass
class Member:
    email: str
    role: Role = Role.MEMBER
    joined_at: float = field(default_factory=time.time)
    status: str = "active"  # active | invited

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        return d


@dataclass
class Invite:
    token: str
    email: str
    role: Role = Role.MEMBER
    created_at: float = field(default_factory=time.time)
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        return d


class TeamError(Exception):
    pass


class Team:
    def __init__(self, team_id: str):
        self.team_id = team_id
        self.path = _TEAMS_DIR / f"{team_id}.json"
        self.name: str = team_id
        self.created_at: float = time.time()
        self.members: list[Member] = []
        self.invites: list[Invite] = []
        self._load()

    # -- queries ---------------------------------------------------------- #
    @property
    def seat_limit(self) -> int:
        return max(1, licensing.current_license().seats)

    @property
    def seats_used(self) -> int:
        return len([m for m in self.members if m.status == "active"]) + \
            len([i for i in self.invites if not i.accepted])

    @property
    def seats_available(self) -> int:
        return max(0, self.seat_limit - self.seats_used)

    def get_member(self, email: str) -> Optional[Member]:
        email = (email or "").lower().strip()
        return next((m for m in self.members if m.email.lower() == email), None)

    def can_manage(self, actor_email: str) -> bool:
        m = self.get_member(actor_email)
        return bool(m and _ROLE_RANK[m.role] >= _ROLE_RANK[Role.ADMIN])

    # -- mutations -------------------------------------------------------- #
    def add_member(self, email: str, role: Role | str = Role.MEMBER, status: str = "active") -> Member:
        email = (email or "").lower().strip()
        if not email or "@" not in email:
            raise TeamError("valid email required")
        if self.get_member(email):
            raise TeamError("already a member")
        if status == "active" and self.seats_available <= 0:
            raise TeamError(f"seat limit reached ({self.seat_limit}). Upgrade to add more seats.")
        role = Role(role) if not isinstance(role, Role) else role
        m = Member(email=email, role=role, status=status)
        self.members.append(m)
        self._save()
        return m

    def invite(self, email: str, role: Role | str = Role.MEMBER) -> Invite:
        email = (email or "").lower().strip()
        if not email or "@" not in email:
            raise TeamError("valid email required")
        if self.get_member(email):
            raise TeamError("already a member")
        if any(i.email.lower() == email and not i.accepted for i in self.invites):
            raise TeamError("invite already pending")
        if self.seats_available <= 0:
            raise TeamError(f"seat limit reached ({self.seat_limit}). Upgrade to add more seats.")
        inv = Invite(token=secrets.token_urlsafe(18), email=email,
                     role=Role(role) if not isinstance(role, Role) else role)
        self.invites.append(inv)
        self._save()
        return inv

    def accept_invite(self, token: str) -> Member:
        inv = next((i for i in self.invites if i.token == token and not i.accepted), None)
        if not inv:
            raise TeamError("invalid or used invite")
        inv.accepted = True
        m = Member(email=inv.email, role=inv.role, status="active")
        self.members.append(m)
        self._save()
        return m

    def set_role(self, email: str, role: Role | str) -> Member:
        m = self.get_member(email)
        if not m:
            raise TeamError("not a member")
        role = Role(role) if not isinstance(role, Role) else role
        if m.role == Role.OWNER and role != Role.OWNER and \
                len([x for x in self.members if x.role == Role.OWNER]) <= 1:
            raise TeamError("cannot demote the only owner")
        m.role = role
        self._save()
        return m

    def remove_member(self, email: str) -> bool:
        m = self.get_member(email)
        if not m:
            return False
        if m.role == Role.OWNER and len([x for x in self.members if x.role == Role.OWNER]) <= 1:
            raise TeamError("cannot remove the only owner")
        self.members = [x for x in self.members if x.email.lower() != email.lower()]
        self._save()
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "name": self.name,
            "created_at": self.created_at,
            "seat_limit": self.seat_limit,
            "seats_used": self.seats_used,
            "seats_available": self.seats_available,
            "members": [m.to_dict() for m in self.members],
            "invites": [i.to_dict() for i in self.invites if not i.accepted],
        }

    # -- persistence ------------------------------------------------------ #
    def _save(self) -> None:
        data = {
            "team_id": self.team_id, "name": self.name, "created_at": self.created_at,
            "members": [m.to_dict() for m in self.members],
            "invites": [i.to_dict() for i in self.invites],
        }
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.name = data.get("name", self.team_id)
        self.created_at = data.get("created_at", time.time())
        self.members = [Member(email=m["email"], role=Role(m.get("role", "member")),
                               joined_at=m.get("joined_at", time.time()),
                               status=m.get("status", "active")) for m in data.get("members", [])]
        self.invites = [Invite(token=i["token"], email=i["email"], role=Role(i.get("role", "member")),
                               created_at=i.get("created_at", time.time()),
                               accepted=i.get("accepted", False)) for i in data.get("invites", [])]


def create_team(name: str, owner_email: str, team_id: Optional[str] = None) -> Team:
    """Create a team with an owner (does not count against seats)."""
    tid = team_id or ("team_" + secrets.token_hex(4))
    t = Team(tid)
    t.name = name or tid
    t.members = [Member(email=(owner_email or "owner@local").lower().strip(), role=Role.OWNER)]
    t._save()
    return t


def get_team(team_id: str) -> Optional[Team]:
    if not (_TEAMS_DIR / f"{team_id}.json").exists():
        return None
    return Team(team_id)


def list_teams() -> list[dict[str, Any]]:
    out = []
    for p in _TEAMS_DIR.glob("*.json"):
        try:
            out.append(Team(p.stem).to_dict())
        except Exception:
            pass
    return out
