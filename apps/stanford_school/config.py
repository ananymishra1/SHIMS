"""App-specific configuration for stanford_school."""
from __future__ import annotations

import os
from pathlib import Path

from shared.config import ROOT_DIR, STORAGE_DIR

APP_DIR = ROOT_DIR / "apps" / "stanford_school"
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"
DB_PATH = STORAGE_DIR / "stanford_school.sqlite3"

DEFAULT_ROLES = [{'username': 'admin', 'role': 'admin', 'password': 'admin123'}, {'username': 'principal', 'role': 'principal', 'password': 'principal123'}, {'username': 'teacher', 'role': 'teacher', 'password': 'teacher123'}, {'username': 'student', 'role': 'student', 'password': 'student123'}]

AI_MODEL = os.getenv("SHIMS_AI_MODEL", os.getenv("SHIMS_CODER_MODEL", "claude-sonnet-4-6"))
