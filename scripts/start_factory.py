"""Start SHIMS Local Factory in the foreground."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

def main() -> None:
    env = os.environ.copy()
    env.update({
        "SHIMS_INSTANCE_ID": "local",
        "SHIMS_ENV_FILE": env.get("SHIMS_ENV_FILE", str(ROOT / ".env.local")),
        "SHIMS_PEERS_FILE": str(ROOT / "config" / "peers.json"),
        "INTER_INSTANCE_TOKEN": env.get("INTER_INSTANCE_TOKEN", "local-factory-shared-token-2026"),
        "OLLAMA_BASE_URL": env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        "SHIMS_APP_NAME": "factory",
        "SHIMS_STORAGE_DIR": env.get("SHIMS_STORAGE_DIR", str(ROOT / "storage" / "factory")),
        "SHIMS_DB_PATH": env.get("SHIMS_DB_PATH", str(ROOT / "storage" / "factory" / "shims.sqlite3")),
    })
    cmd = [
        PYTHON, "-u", "-m", "uvicorn", "backend.app.main:app",
        "--host", "127.0.0.1", "--port", env.get("SHIMS_FACTORY_PORT", "8030"), "--no-access-log",
    ]
    print("[start_factory]", " ".join(cmd))
    sys.exit(subprocess.run(cmd, cwd=ROOT, env=env).returncode)


if __name__ == "__main__":
    main()
