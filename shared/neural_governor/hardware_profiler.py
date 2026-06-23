"""Hardware profiler — detects system capabilities for model routing."""
from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from . import HardwareProfile


def _run_cmd(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=5)
    except Exception:
        return ""


def _parse_nvidia_smi() -> dict[str, Any]:
    """Parse nvidia-smi for VRAM and CUDA info."""
    out = _run_cmd(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader"])
    if not out:
        return {}
    parts = [p.strip() for p in out.split(",")]
    if len(parts) < 4:
        return {}
    try:
        total_mb = float(parts[1].replace("MiB", "").strip())
        used_mb = float(parts[2].replace("MiB", "").strip())
    except Exception:
        total_mb = used_mb = 0
    return {
        "gpu_name": parts[0],
        "vram_total_mb": total_mb,
        "vram_used_mb": used_mb,
        "driver_version": parts[3],
    }


def _detect_cuda_version() -> str:
    out = _run_cmd(["nvcc", "--version"])
    if out:
        for line in out.splitlines():
            if "release" in line:
                return line.split("release")[-1].split(",")[0].strip()
    # Fallback: parse from nvidia-smi driver
    nv = _parse_nvidia_smi()
    return nv.get("driver_version", "")


def _detect_internet() -> bool:
    import socket
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except Exception:
        return False


def _detect_battery() -> bool:
    sys = platform.system().lower()
    if sys == "windows":
        try:
            import ctypes
            from ctypes import wintypes
            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", wintypes.BYTE),
                    ("BatteryFlag", wintypes.BYTE),
                    ("BatteryLifePercent", wintypes.BYTE),
                    ("Reserved1", wintypes.BYTE),
                    ("BatteryLifeTime", wintypes.DWORD),
                    ("BatteryFullLifeTime", wintypes.DWORD),
                ]
            sps = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
                return sps.BatteryFlag != 255  # 255 = no battery
        except Exception:
            pass
    elif sys == "linux":
        try:
            bat_path = Path("/sys/class/power_supply/BAT0")
            return bat_path.exists()
        except Exception:
            pass
    elif sys == "darwin":
        out = _run_cmd(["pmset", "-g", "batt"])
        return "Battery" in out or "battery" in out
    return False


def _get_ram_gb() -> float:
    import psutil
    return round(psutil.virtual_memory().total / (1024**3), 2)


def _get_cpu_cores() -> int:
    import os
    return os.cpu_count() or 0


def _get_disk_gb() -> float:
    import psutil
    du = shutil.disk_usage("/" if platform.system() != "Windows" else "C:/")
    return round(du.free / (1024**3), 2)


def profile_hardware() -> HardwareProfile:
    """Build a complete hardware profile of the current machine."""
    nv = _parse_nvidia_smi()
    vram_gb = round(nv.get("vram_total_mb", 0) / 1024, 2) if nv else 0.0

    return HardwareProfile(
        total_ram_gb=_get_ram_gb(),
        vram_gb=vram_gb,
        cpu_cores=_get_cpu_cores(),
        cuda_available=vram_gb > 0,
        cuda_version=_detect_cuda_version(),
        internet_available=_detect_internet(),
        battery_powered=_detect_battery(),
        disk_space_gb=_get_disk_gb(),
        platform=platform.system().lower(),
    )


def quick_profile() -> dict[str, Any]:
    """JSON-serializable quick profile for API responses."""
    return profile_hardware().to_dict()
