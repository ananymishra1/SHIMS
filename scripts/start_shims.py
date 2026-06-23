#!/usr/bin/env python3
"""
SHIMS clean starter — Enterprise + Omni + Desktop Bridge.

Usage:
    scripts/start_shims.py                  # start everything
    scripts/start_shims.py --no-bridge      # start omni + enterprise only
    scripts/start_shims.py --dry-run        # print what would run
    scripts/start_shims.py --no-verify      # skip port health checks

Configuration is read from the project .env file:
    SHIMS_OMNI_PORT              default 8010
    SHIMS_ENTERPRISE_PORT        default 8020
    SHIMS_BRIDGE_PORT            default 9876
    SHIMS_BRIDGE_TOKEN           required for the desktop bridge
    SHIMS_DESKTOP_BRIDGE_URI     default ws://localhost:<bridge_port>/bridge
    SHIMS_DESKTOP_BRIDGE_TOKEN   default <SHIMS_BRIDGE_TOKEN>
    SHIMS_ENTERPRISE_URL         default http://127.0.0.1:<enterprise_port>

Each service opens in its own terminal window on Windows; on Unix it runs
under nohup and writes to logs/start_shims_<service>.log.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Load .env before reading configuration.
ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = Path(os.getenv("SHIMS_ENV_FILE", ROOT_DIR / ".env")).expanduser().resolve()
if not ENV_PATH.exists():
    ENV_PATH = ROOT_DIR / ".env"
try:
    from dotenv import load_dotenv

    # The selected env file must win over any stale environment inherited from the parent shell.
    load_dotenv(ENV_PATH, override=True)
except Exception:
    pass


DEFAULT_OMNI_PORT = 8010
DEFAULT_ENTERPRISE_PORT = 8020
DEFAULT_BRIDGE_PORT = 9876


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _log_dir() -> Path:
    d = ROOT_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _python() -> Path:
    candidates = [
        ROOT_DIR / ".venv" / "Scripts" / "python.exe",
        ROOT_DIR / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return Path("python")


def _windows_service_batch(
    title: str,
    cwd: Path,
    command: list[str],
    env: dict[str, str] | None = None,
) -> Path:
    """Write a temporary batch file that launches a service in a new window.

    Using a real .bat file avoids the nested-quote problems that make
    ``start ... cmd /k "cd ... && ..."`` fragile when paths contain spaces.
    If the service exits with an error, the window pauses so the error is visible.
    """
    log_dir = _log_dir()
    instance_id = (os.getenv("SHIMS_INSTANCE_ID") or "").strip()
    suffix = f"_{instance_id}" if instance_id else ""
    batch_path = log_dir / f"start_shims_{title.lower().replace(' ', '_')}{suffix}.bat"
    cmd = " ".join(str(c) for c in command)
    lines = ["@echo off", f"title {title}", f'cd /d "{cwd}"']
    for k, v in (env or {}).items():
        lines.append(f'set {k}={v}')
    lines.append(f"echo [{title}] Starting ...")
    lines.append(cmd)
    lines.append("if errorlevel 1 pause")
    batch_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return batch_path


def _find_pids_on_port(port: int) -> list[int]:
    """Return PIDs listening on the given TCP port."""
    system = platform.system()
    pids: list[int] = []
    if system == "Windows":
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        try:
                            pids.append(int(parts[-1]))
                        except ValueError:
                            pass
        except Exception:
            pass
    else:
        if shutil.which("lsof"):
            try:
                result = subprocess.run(
                    ["lsof", "-ti", f"tcp:{port}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line.isdigit():
                        pids.append(int(line))
            except Exception:
                pass
    return list(set(pids))


def _kill_pids(pids: list[int]) -> None:
    system = platform.system()
    for pid in pids:
        print(f"  killing PID {pid}")
        if system == "Windows":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, capture_output=True)
        else:
            subprocess.run(["kill", "-9", str(pid)], check=False, capture_output=True)


def _free_port(port: int) -> None:
    pids = _find_pids_on_port(port)
    if pids:
        print(f"[starter] freeing port {port} (PIDs: {pids})")
        _kill_pids(pids)
        time.sleep(1)


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def _wait_for_port(port: int, label: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_is_open(port):
            return True
        time.sleep(0.5)
    print(f"[starter] timeout waiting for {label} on port {port}")
    return False


def _start_bridge(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    port = cfg["bridge_port"]
    token = cfg["bridge_token"]
    if not token or token == "change-me-bridge-token":
        print("[starter] WARNING: SHIMS_BRIDGE_TOKEN is not set in .env")

    print(f"[starter] starting Desktop Bridge on port {port}")
    env = {
        "HF_HUB_ENABLE_HF_TRANSFER": "",
        "HF_XET_HIGH_PERFORMANCE": "1",
    }
    cmd = [
        str(_python()),
        str(ROOT_DIR / "desktop_bridge" / "bridge_server.py"),
        "--host", "0.0.0.0",
        "--port", str(port),
        "--token", token,
    ]

    if args.dry_run:
        print("  env:", env)
        print("  ", " ".join(cmd))
        return

    _free_port(port)
    _run_service("SHIMS Bridge", ROOT_DIR / "desktop_bridge", cmd, env=env)


def _start_omni(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    port = cfg["omni_port"]
    print(f"[starter] starting SHIMS Omni on port {port}")
    env = {
        "SHIMS_DESKTOP_BRIDGE_URI": cfg["bridge_uri"],
        "SHIMS_DESKTOP_BRIDGE_TOKEN": cfg["bridge_token"],
        "SHIMS_ENTERPRISE_URL": cfg["enterprise_url"],
        "HF_HUB_ENABLE_HF_TRANSFER": "",
        "HF_XET_HIGH_PERFORMANCE": "1",
    }
    cmd = [
        str(_python()),
        "-m", "uvicorn",
        "backend.app.main:app",
        "--host", "0.0.0.0",
        "--port", str(port),
        "--no-access-log",
    ]

    if args.dry_run:
        print("  env:", env)
        print("  ", " ".join(cmd))
        return

    _free_port(port)
    _run_service("SHIMS Omni", ROOT_DIR, cmd, env=env)


def _start_enterprise(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    port = cfg["enterprise_port"]
    print(f"[starter] starting SHIMS Enterprise on port {port}")
    env = {
        "SHIMS_DESKTOP_BRIDGE_URI": cfg["bridge_uri"],
        "SHIMS_DESKTOP_BRIDGE_TOKEN": cfg["bridge_token"],
        "HF_HUB_ENABLE_HF_TRANSFER": "",
        "HF_XET_HIGH_PERFORMANCE": "1",
    }
    cmd = [
        str(_python()),
        "-m", "uvicorn",
        "shims_enterprise.app:app",
        "--host", "0.0.0.0",
        "--port", str(port),
        "--no-access-log",
    ]

    if args.dry_run:
        print("  env:", env)
        print("  ", " ".join(cmd))
        return

    _free_port(port)
    _run_service("SHIMS Enterprise", ROOT_DIR, cmd, env=env)


def _run_service(
    title: str,
    cwd: Path,
    command: list[str],
    env: dict[str, str] | None = None,
) -> None:
    system = platform.system()
    full_env = {**os.environ, **(env or {})}

    if system == "Windows":
        batch_path = _windows_service_batch(title, cwd, command, env=env)
        # Use CREATE_NEW_CONSOLE to open a dedicated window for this service.
        subprocess.Popen(
            ["cmd", "/k", str(batch_path)],
            cwd=cwd,
            env=full_env,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        print(f"  launcher: {batch_path}")
    else:
        log = _log_dir() / f"start_shims_{title.lower().replace(' ', '_')}.log"
        with open(log, "ab") as fh:
            prefix_cmd = ["stdbuf", "-oL"] if shutil.which("stdbuf") else []
            subprocess.Popen(
                prefix_cmd + command,
                cwd=cwd,
                env=full_env,
                stdout=fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        print(f"  logging to {log}")


def _build_config() -> dict[str, Any]:
    omni_port = _env_int("SHIMS_OMNI_PORT", DEFAULT_OMNI_PORT)
    enterprise_port = _env_int("SHIMS_ENTERPRISE_PORT", DEFAULT_ENTERPRISE_PORT)
    bridge_port = _env_int("SHIMS_BRIDGE_PORT", DEFAULT_BRIDGE_PORT)
    bridge_token = _env_str("SHIMS_BRIDGE_TOKEN", "")
    bridge_uri = _env_str(
        "SHIMS_DESKTOP_BRIDGE_URI",
        f"ws://localhost:{bridge_port}/bridge",
    )
    enterprise_url = _env_str(
        "SHIMS_ENTERPRISE_URL",
        f"http://127.0.0.1:{enterprise_port}",
    )
    return {
        "omni_port": omni_port,
        "enterprise_port": enterprise_port,
        "bridge_port": bridge_port,
        "bridge_token": bridge_token,
        "bridge_uri": bridge_uri,
        "enterprise_url": enterprise_url,
    }


def _print_summary(cfg: dict[str, Any]) -> None:
    print()
    print("=" * 50)
    print(" SHIMS is running")
    print("=" * 50)
    print(f"  Omni:       http://localhost:{cfg['omni_port']}")
    print(f"  Enterprise: http://localhost:{cfg['enterprise_port']}")
    print(f"  Bridge:     {cfg['bridge_uri']}")
    print("=" * 50)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start the SHIMS stack: Desktop Bridge, Omni, and Enterprise.",
    )
    parser.add_argument(
        "--no-bridge",
        action="store_true",
        help="do not start the desktop bridge",
    )
    parser.add_argument(
        "--no-omni",
        action="store_true",
        help="do not start SHIMS Omni",
    )
    parser.add_argument(
        "--no-enterprise",
        action="store_true",
        help="do not start SHIMS Enterprise",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip port health checks after starting",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print commands instead of running them",
    )
    args = parser.parse_args()

    if not ENV_PATH.exists():
        print(f"[starter] WARNING: {ENV_PATH} not found. Using defaults.")

    cfg = _build_config()

    print("[starter] SHIMS stack launcher")
    print(f"  root: {ROOT_DIR}")
    print(f"  python: {_python()}")

    if not args.no_bridge:
        _start_bridge(args, cfg)
        if not args.dry_run:
            time.sleep(2)

    if not args.no_omni:
        _start_omni(args, cfg)
        if not args.dry_run:
            time.sleep(1)

    if not args.no_enterprise:
        _start_enterprise(args, cfg)
        if not args.dry_run:
            time.sleep(1)

    if args.dry_run:
        return 0

    if not args.no_verify:
        print("[starter] waiting for services to come online ...")
        ok = True
        if not args.no_bridge:
            ok &= _wait_for_port(cfg["bridge_port"], "bridge", timeout=20)
        if not args.no_omni:
            ok &= _wait_for_port(cfg["omni_port"], "omni", timeout=60)
        if not args.no_enterprise:
            ok &= _wait_for_port(cfg["enterprise_port"], "enterprise", timeout=60)
        if not ok:
            print("[starter] ERROR: one or more services failed to start in time.")
            return 1

    _print_summary(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
