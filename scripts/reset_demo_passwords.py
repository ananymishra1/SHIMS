"""Reset the SHIMS Enterprise demo users to their documented per-role passwords.

The initial seed sets every demo user's password to ``admin123``; the README and
launch checklist, however, advertise role-specific logins (``qc / qc123`` etc.).
This script aligns reality with the docs so every advertised login works.

Usage:
    python scripts/reset_demo_passwords.py            # reset to <role>123
    python scripts/reset_demo_passwords.py --password X  # set every demo user to X
    python scripts/reset_demo_passwords.py --list        # show current demo users
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.database import db
from shared.security import hash_password

DEMO_USERS = ["admin", "executive", "rd", "qc", "warehouse", "production", "procurement", "qa"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset SHIMS Enterprise demo passwords.")
    ap.add_argument("--password", help="Set ALL demo users to this single password.")
    ap.add_argument("--list", action="store_true", help="List demo users and exit.")
    args = ap.parse_args()

    db.init()
    if args.list:
        for row in db.query("SELECT username, role, active FROM users ORDER BY id"):
            print(f"  {row['username']:<12} role={row['role']:<11} active={row['active']}")
        return 0

    changed = []
    for username in DEMO_USERS:
        row = db.one("SELECT id FROM users WHERE username=?", (username,))
        if not row:
            continue
        password = args.password or f"{username}123"
        db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (hash_password(password), row["id"]))
        changed.append(f"{username} / {password}")

    if not changed:
        print("No demo users found. Start the Enterprise app once to seed them.")
        return 1
    print("Demo passwords reset:")
    for line in changed:
        print(f"  {line}")
    print("\nThese are demo credentials. Disable demo mode and rotate them before production.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
