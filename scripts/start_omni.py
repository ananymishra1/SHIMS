"""Start SHIMS Omni in the foreground."""
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
        "INTER_INSTANCE_TOKEN": env.get("INTER_INSTANCE_TOKEN", "local-factory-shared-token-2026"),
        "SHIMS_PEERS_FILE": str(ROOT / "config" / "peers.json"),
        "OLLAMA_BASE_URL": env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        "SHIMS_APP_NAME": "omni",
        "SHIMS_STORAGE_DIR": env.get("SHIMS_STORAGE_DIR", str(ROOT / "storage" / "omni")),
        "SHIMS_DB_PATH": env.get("SHIMS_DB_PATH", str(ROOT / "storage" / "omni" / "shims.sqlite3")),
    })
    cmd = [
        PYTHON, "-u", "-m", "uvicorn", "backend.app.main:app",
        "--host", "127.0.0.1", "--port", env.get("SHIMS_OMNI_PORT", "8010"), "--no-access-log",
    ]
    print("[start_omni]", " ".join(cmd))
    sys.exit(subprocess.run(cmd, cwd=ROOT, env=env).returncode)


if __name__ == "__main__":
    main()
