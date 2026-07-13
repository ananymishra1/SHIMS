#!/usr/bin/env bash
# ============================================================
#  SHIMS clean master starter (Unix)
#  Starts: Desktop Bridge -> Omni -> Enterprise
#  Configuration is read from .env in the project root.
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f ".venv/bin/python" ]; then
  echo "[SHIMS] Virtual environment not found. Run setup.bat on Windows or create .venv manually."
  exit 1
fi

.venv/bin/python scripts/start_shims.py "$@"
