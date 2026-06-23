from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional


@dataclass
class ToolResult:
    tool: str
    status: str
    message: str
    artifact_path: Optional[str] = None
    artifact_url: Optional[str] = None
    artifact_sha256: Optional[str] = None
    verified: bool = False
    latency_ms: int = 0
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def sha256_file(path: str | Path) -> str:
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_artifact(tool: str, start_time: float, path: str | Path, message: str, url_prefix: str = "/media/generated") -> ToolResult:
    p = Path(path)
    latency_ms = int((time.time() - start_time) * 1000)
    if not p.exists() or not p.is_file() or p.stat().st_size <= 0:
        return ToolResult(tool=tool, status="error", message=f"{tool} failed verification: artifact missing or empty.", artifact_path=str(p), verified=False, latency_ms=latency_ms)
    return ToolResult(tool=tool, status="success", message=message, artifact_path=str(p), artifact_url=f"{url_prefix}/{p.name}", artifact_sha256=sha256_file(p), verified=True, latency_ms=latency_ms)
