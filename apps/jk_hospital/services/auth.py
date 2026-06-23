"""Simple hospital auth layer."""
from __future__ import annotations

import hashlib
import secrets
from typing import Any

from ..database import execute, insert, query_all, query_one
from ..config import DEFAULT_USERS


def hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def ensure_default_users() -> None:
    existing = {u["username"] for u in query_all("SELECT username FROM hospital_users")}
    for u in DEFAULT_USERS:
        if u["username"] in existing:
            continue
        insert(
            "INSERT INTO hospital_users (username, full_name, role, password_hash, active) VALUES (?, ?, ?, ?, 1)",
            (u["username"], u["full_name"], u["role"], hash_pw(u["password"])),
        )


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    user = query_one("SELECT * FROM hospital_users WHERE username=? AND active=1", (username,))
    if not user:
        return None
    if secrets.compare_digest(user["password_hash"], hash_pw(password)):
        return {k: user[k] for k in user if k != "password_hash"}
    return None


def get_user(user_id: int) -> dict[str, Any] | None:
    user = query_one("SELECT id, username, full_name, role, active, created_at FROM hospital_users WHERE id=?", (user_id,))
    return user


def list_users() -> list[dict[str, Any]]:
    return query_all("SELECT id, username, full_name, role, active, created_at FROM hospital_users ORDER BY role, full_name")
