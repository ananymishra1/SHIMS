#!/usr/bin/env python3
"""Auto-detect hardware and install the best Ollama models for this machine."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Add repo root to path so we can import shared
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.neural_governor.hardware_profiler import profile_hardware
from shared.neural_governor.model_registry import find_model


OLLAMA_EXE = os.getenv("OLLAMA_EXE", "C:/Users/direc/AppData/Local/Programs/Ollama/ollama.exe")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
ENV_PATH = ROOT / ".env"


# Model recommendations by RAM tier
TIER_MODELS = {
    "<8GB": ["liquid-lfm2.5-230m", "gemma3:1b"],
    "8-16GB": ["liquid-lfm2.5-1.2b", "qwen3:8b", "gemma3:4b"],
    "16-32GB": ["qwen3:14b", "llama3.3:8b", "gemma3:12b"],
    "32-64GB": ["llama3.3:70b", "qwen3:32b", "deepseek-coder-v2:16b"],
    "64-128GB": ["llama3.3:70b", "qwen3:32b", "command-r-plus:104b"],
    "128GB+": ["llama3.3:70b", "qwen3:32b", "command-r-plus:104b"],
}


def _run_ollama(args: list[str], ollama_exe: str | None = None) -> str:
    """Run the Ollama CLI and return stdout."""
    exe = ollama_exe or OLLAMA_EXE
    try:
        result = subprocess.run(
            [exe] + args,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        return result.stdout + result.stderr
    except Exception as exc:
        return f"ERROR: {exc}"


def _ollama_list(ollama_exe: str | None = None) -> list[str]:
    """Return installed model names from Ollama."""
    try:
        import httpx
        resp = httpx.get(f"{OLLAMA_BASE_URL.rstrip('/')}/api/tags", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        # Fallback to CLI
        out = _run_ollama(["list"], ollama_exe)
        models = []
        for line in out.splitlines():
            parts = line.split()
            if parts and not parts[0].startswith("NAME"):
                models.append(parts[0])
        return models


def _pull_model(model: str, ollama_exe: str | None = None) -> bool:
    """Pull a model via Ollama with progress reported to stdout."""
    exe = ollama_exe or OLLAMA_EXE
    print(f"[INSTALL] Pulling {model} ...")
    try:
        # Use subprocess with streaming output
        proc = subprocess.Popen(
            [exe, "pull", model],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            sys.stdout.write(line)
            sys.stdout.flush()
        proc.wait()
        if proc.returncode == 0:
            print(f"[OK] {model} installed successfully.")
            return True
        else:
            print(f"[FAIL] {model} exited with code {proc.returncode}.")
            return False
    except Exception as exc:
        print(f"[FAIL] {model} error: {exc}")
        return False


def _detect_tier(ram_gb: float) -> str:
    if ram_gb < 8:
        return "<8GB"
    elif ram_gb < 16:
        return "8-16GB"
    elif ram_gb < 32:
        return "16-32GB"
    elif ram_gb < 64:
        return "32-64GB"
    elif ram_gb < 128:
        return "64-128GB"
    return "128GB+"


def _update_env_default(model: str) -> None:
    """Update .env with the recommended default model."""
    if not ENV_PATH.exists():
        print("[WARN] .env not found, skipping env update.")
        return
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out = []
    updated = False
    for line in lines:
        if line.strip().startswith("OLLAMA_MODEL="):
            out.append(f"OLLAMA_MODEL={model}")
            updated = True
        elif line.strip().startswith("SHIMS_OLLAMA_MODEL="):
            out.append(f"SHIMS_OLLAMA_MODEL={model}")
            updated = True
        else:
            out.append(line)
    if not updated:
        out.append(f"OLLAMA_MODEL={model}")
        out.append(f"SHIMS_OLLAMA_MODEL={model}")
    ENV_PATH.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    print(f"[ENV] Set OLLAMA_MODEL={model} in .env")


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-install best Ollama models for this hardware")
    parser.add_argument("--dry-run", action="store_true", help="Only recommend, do not install")
    parser.add_argument("--ollama-exe", default=OLLAMA_EXE, help="Path to ollama.exe")
    parser.add_argument("--force", action="store_true", help="Re-install even if present")
    args = parser.parse_args()

    ollama_exe = args.ollama_exe

    # Check Ollama is running
    try:
        import httpx
        httpx.get(f"{OLLAMA_BASE_URL.rstrip('/')}/api/tags", timeout=5).raise_for_status()
        print("[OK] Ollama is running.")
    except Exception:
        print(f"[WARN] Ollama does not appear to be running at {OLLAMA_BASE_URL}. Will attempt CLI anyway.")

    # Detect hardware
    hw = profile_hardware()
    ram_gb = hw.total_ram_gb
    tier = _detect_tier(ram_gb)
    print(f"[HW] RAM: {ram_gb} GB | VRAM: {hw.vram_gb} GB | Cores: {hw.cpu_cores} | Platform: {hw.platform}")
    print(f"[TIER] Detected RAM tier: {tier}")

    recommendations = TIER_MODELS.get(tier, TIER_MODELS["128GB+"])
    print(f"[RECOMMEND] Models for this tier: {recommendations}")

    installed = _ollama_list(ollama_exe)
    print(f"[INSTALLED] Already have: {installed}")

    best_model = None
    for model in recommendations:
        if model in installed and not args.force:
            print(f"[SKIP] {model} already installed.")
            if best_model is None:
                best_model = model
            continue
        if args.dry_run:
            print(f"[DRY-RUN] Would pull {model}")
            if best_model is None:
                best_model = model
            continue
        if _pull_model(model, ollama_exe):
            if best_model is None:
                best_model = model

    if best_model:
        print(f"[DEFAULT] Best model for this machine: {best_model}")
        _update_env_default(best_model)
    else:
        print("[WARN] No model could be installed. Check Ollama connectivity.")
        sys.exit(1)


if __name__ == "__main__":
    main()
