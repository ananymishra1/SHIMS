#!/usr/bin/env python3
"""
Start the isolated SHIMS Local Factory instance (Instance B).

Uses .env.local for configuration:
    - Omni on port 8030
    - Enterprise on port 8040
    - Isolated storage in storage_local/
    - Local Ollama models (Qwen 3B default)

Usage:
    .venv/Scripts/python scripts/start_shims_local_factory.py
    .venv/Scripts/python scripts/start_shims_local_factory.py --no-bridge
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_LOCAL = ROOT_DIR / ".env.local"

if not ENV_LOCAL.exists():
    print(f"[local-factory] ERROR: {ENV_LOCAL} not found. Run setup first.")
    sys.exit(1)

# Tell SHIMS to use the isolated env file and tag this instance.
os.environ["SHIMS_ENV_FILE"] = str(ENV_LOCAL)
os.environ["SHIMS_INSTANCE_ID"] = "local"

# Force the local model stack for Instance B:
#   - 3B for fast/default, 7B for heavy/research, ChemDFM for chemistry,
#   - Coder 14B for code tasks, Ollama as the active provider.
os.environ.setdefault("SHIMS_AI_PROVIDER", "ollama")
os.environ.setdefault("SHIMS_OLLAMA_MODEL", "qwen2.5:3b")
os.environ.setdefault("SHIMS_ROUTER_MODEL", "qwen2.5:3b")
os.environ.setdefault("SHIMS_FAST_MODEL", "qwen2.5:3b")
os.environ.setdefault("SHIMS_SMART_MODEL", "qwen2.5:3b")
os.environ.setdefault("SHIMS_HEAVY_MODEL", "qwen2.5:7b")
os.environ.setdefault("SHIMS_RESEARCH_MODEL", "qwen2.5:7b")
os.environ.setdefault("SHIMS_CODER_MODEL", "qwen2.5-coder:14b")
os.environ.setdefault("SHIMS_CHEMISTRY_MODEL", "chemdfm")
os.environ.setdefault("CHEMDFM_OLLAMA_TAG", "chemdfm")

# Shared peer configuration lives in config/peers.json.
os.environ.setdefault("SHIMS_PEERS_FILE", str(ROOT_DIR / "config" / "peers.json"))

# Re-execute the main starter as a subprocess so it loads .env.local before importing.
import subprocess

cmd = [str(ROOT_DIR / ".venv" / "Scripts" / "python.exe"), str(ROOT_DIR / "scripts" / "start_shims.py")] + sys.argv[1:]
raise SystemExit(subprocess.call(cmd))
