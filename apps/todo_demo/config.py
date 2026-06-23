"""App-specific configuration for todo_demo."""
from __future__ import annotations

import os
from pathlib import Path

from shared.config import ROOT_DIR, STORAGE_DIR

APP_DIR = ROOT_DIR / "apps" / "todo_demo"
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"
DB_PATH = STORAGE_DIR / "todo_demo.sqlite3"

DEFAULT_ROLES = [{'username': 'admin', 'role': 'admin', 'password': 'admin123'}]

AI_MODEL = os.getenv("SHIMS_AI_MODEL", os.getenv("SHIMS_CODER_MODEL", "claude-sonnet-4-6"))
