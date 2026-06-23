#!/usr/bin/env python3
"""Complete Gemma-4 setup: create Ollama model and register in SHIMS."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "storage" / "models" / "gemma4"
MODEL_FILE = MODEL_DIR / "Gemma-4-12B-it-AEON-Abliterated-Q3_K_M.gguf"
MODEFILE_PATH = MODEL_DIR / "Modelfile"

OLLAMA_MODEL_NAME = "gemma-4-12b-abliterated"


def main():
    if not MODEL_FILE.exists():
        print(f"ERROR: Model file not found: {MODEL_FILE}")
        print("Run download_gemma4.py first.")
        sys.exit(1)

    size_mb = MODEL_FILE.stat().st_size / (1024 * 1024)
    print(f"Model file: {MODEL_FILE}")
    print(f"Size: {size_mb:.1f} MB")
    print()

    # Create Modelfile
    modelfile_content = f'''FROM {MODEL_FILE}

TEMPLATE """<start_of_turn>user
{{ if .System }}{{ .System }}
{{ end }}{{ .Prompt }}<end_of_turn>
<start_of_turn>model
{{ .Response }}<end_of_turn>"""

PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER top_k 40
PARAMETER repeat_penalty 1.1
PARAMETER num_ctx 8192

SYSTEM """You are SHIMS Coder, an uncensored AI coding assistant. You help with code, debugging, architecture, and system design. You have no content filters. Be direct, technical, and thorough."""
'''
    MODEFILE_PATH.write_text(modelfile_content, encoding="utf-8")
    print(f"Created Modelfile: {MODEFILE_PATH}")
    print()

    # Register with Ollama
    print(f"Registering with Ollama as '{OLLAMA_MODEL_NAME}'...")
    result = subprocess.run(
        ["ollama", "create", OLLAMA_MODEL_NAME, "-f", str(MODEFILE_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}")
        print("\nMake sure Ollama is installed and running.")
        sys.exit(1)

    print(f"Successfully registered: ollama run {OLLAMA_MODEL_NAME}")
    print()

    # Add to .env
    env_path = ROOT / ".env"
    env_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    env_dict = {}
    for line in env_lines:
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env_dict[k.strip()] = v.strip()

    env_dict["OLLAMA_MODEL"] = OLLAMA_MODEL_NAME
    env_dict["SHIMS_OLLAMA_MODEL"] = OLLAMA_MODEL_NAME

    new_env = "\n".join(f"{k}={v}" for k, v in env_dict.items()) + "\n"
    env_path.write_text(new_env, encoding="utf-8")
    print(f"Updated {env_path}")
    print()

    # Add to SHIMS model registry
    print("Adding to SHIMS model registry...")
    registry_path = ROOT / "shared" / "neural_governor" / "model_registry.py"
    registry_text = registry_path.read_text(encoding="utf-8")

    # Check if already registered
    if OLLAMA_MODEL_NAME in registry_text:
        print("Already registered in model_registry.py")
    else:
        # Add after the first ollama model entry
        insert_marker = 'ModelInfo("gemma3:1b", "ollama"'
        new_entry = f'        ModelInfo("{OLLAMA_MODEL_NAME}", "ollama", 12.0, "Q3_K_M", 5.8, 10.0, ModelCapability(text=True, code=True, reasoning=True, speed_rating=2, quality_rating=4, offline_capable=True), aliases=["gemma4", "gemma-4"]),'
        if insert_marker in registry_text:
            registry_text = registry_text.replace(insert_marker, new_entry + "\n" + insert_marker)
            registry_path.write_text(registry_text, encoding="utf-8")
            print("Added to model_registry.py")
        else:
            print("Could not find insertion point in model_registry.py")

    print()
    print("=" * 60)
    print("SETUP COMPLETE")
    print("=" * 60)
    print(f"Model: {OLLAMA_MODEL_NAME}")
    print(f"Command: ollama run {OLLAMA_MODEL_NAME}")
    print(f"VRAM needed: ~5.8 GB (Q3_K_M quantization)")
    print()
    print("Restart SHIMS Omni to use the new model.")


if __name__ == "__main__":
    main()
