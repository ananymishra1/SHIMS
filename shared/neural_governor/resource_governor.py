"""Resource governor — monitors and throttles system resources."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import psutil

RESOURCE_DB = Path(__file__).resolve().parent.parent.parent / "data" / "state" / "governor_resources.sqlite3"
RESOURCE_DB.parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(RESOURCE_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            cpu_percent REAL,
            ram_used_gb REAL,
            ram_total_gb REAL,
            vram_used_gb REAL,
            vram_total_gb REAL,
            disk_used_gb REAL,
            disk_total_gb REAL,
            active_requests INTEGER,
            queue_depth INTEGER
        )
        """
    )
    con.commit()
    return con


@dataclass
class ResourceSnapshot:
    cpu_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    vram_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    active_requests: int = 0
    queue_depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_percent": self.cpu_percent,
            "ram_used_gb": self.ram_used_gb,
            "ram_total_gb": self.ram_total_gb,
            "vram_used_gb": self.vram_used_gb,
            "vram_total_gb": self.vram_total_gb,
            "disk_used_gb": self.disk_used_gb,
            "disk_total_gb": self.disk_total_gb,
            "active_requests": self.active_requests,
            "queue_depth": self.queue_depth,
        }


# In-memory counters
_active_requests = 0
_queue_depth = 0


def take_snapshot() -> ResourceSnapshot:
    """Capture current system resources."""
    mem = psutil.virtual_memory()
    du = psutil.disk_usage("/" if Path("/").exists() else "C:/")

    vram_total = 0.0
    vram_used = 0.0
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
            text=True, timeout=3, stderr=subprocess.DEVNULL
        )
        lines = out.strip().split("\n")
        for line in lines:
            parts = line.split(",")
            if len(parts) >= 2:
                vram_total += float(parts[0].strip()) / 1024
                vram_used += float(parts[1].strip()) / 1024
    except Exception:
        pass

    snap = ResourceSnapshot(
        cpu_percent=psutil.cpu_percent(interval=0.1),
        ram_used_gb=round(mem.used / (1024**3), 2),
        ram_total_gb=round(mem.total / (1024**3), 2),
        vram_used_gb=round(vram_used, 2),
        vram_total_gb=round(vram_total, 2),
        disk_used_gb=round(du.used / (1024**3), 2),
        disk_total_gb=round(du.total / (1024**3), 2),
        active_requests=_active_requests,
        queue_depth=_queue_depth,
    )

    with _connect() as con:
        con.execute(
            """
            INSERT INTO resource_snapshots
            (timestamp, cpu_percent, ram_used_gb, ram_total_gb, vram_used_gb, vram_total_gb,
             disk_used_gb, disk_total_gb, active_requests, queue_depth)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (time.time(), snap.cpu_percent, snap.ram_used_gb, snap.ram_total_gb,
             snap.vram_used_gb, snap.vram_total_gb, snap.disk_used_gb, snap.disk_total_gb,
             snap.active_requests, snap.queue_depth),
        )
        con.commit()

    return snap


def request_start() -> ResourceSnapshot:
    """Call when an AI request starts."""
    global _active_requests
    _active_requests += 1
    return take_snapshot()


def request_end() -> None:
    """Call when an AI request ends."""
    global _active_requests
    _active_requests = max(0, _active_requests - 1)


def should_throttle() -> bool:
    """Returns True if system is under too much load."""
    snap = take_snapshot()
    if snap.cpu_percent > 90:
        return True
    if snap.ram_used_gb / max(snap.ram_total_gb, 1) > 0.92:
        return True
    if snap.vram_total_gb > 0 and snap.vram_used_gb / snap.vram_total_gb > 0.95:
        return True
    return False


def recommend_downgrade() -> Optional[str]:
    """Recommend a smaller model if resources are tight."""
    snap = take_snapshot()
    if snap.vram_total_gb > 0:
        free_vram = snap.vram_total_gb - snap.vram_used_gb
        if free_vram < 2:
            return "gemma3:1b"
        if free_vram < 4:
            return "qwen2.5:1.5b"
    else:
        free_ram = snap.ram_total_gb - snap.ram_used_gb
        if free_ram < 3:
            return "gemma3:1b"
    return None


def get_recent_snapshots(limit: int = 60) -> list[dict[str, Any]]:
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM resource_snapshots ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "timestamp": r["timestamp"],
            "cpu_percent": r["cpu_percent"],
            "ram_used_gb": r["ram_used_gb"],
            "ram_total_gb": r["ram_total_gb"],
            "vram_used_gb": r["vram_used_gb"],
            "vram_total_gb": r["vram_total_gb"],
            "active_requests": r["active_requests"],
            "queue_depth": r["queue_depth"],
        }
        for r in rows
    ]
