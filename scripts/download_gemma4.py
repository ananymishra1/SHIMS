#!/usr/bin/env python3
"""Download Gemma-4 12B Abliterated GGUF and set up in Ollama.

Target: Predator Helios 300 with ~6GB VRAM
Selected: Q3_K_M quantization (~5.8GB) for best quality/VRAM balance.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Model source
REPO_ID = "Abiray/Gemma-4-12B-it-AEON-Abliterated-K4-GGUF"
FILENAME = "Gemma-4-12B-it-AEON-Abliterated-Q3_K_M.gguf"
# Alternative: DuoNeural/OpenYourMind-Gemma4-12B-IT-Abliterated-GGUF / oym_ablit-Q3_K_L.gguf

# Destination
ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "storage" / "models" / "gemma4"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH = MODEL_DIR / FILENAME


def download():
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("ERROR: huggingface_hub not installed.")
        print("Run: pip install huggingface_hub")
        sys.exit(1)

    if MODEL_PATH.exists():
        size_mb = MODEL_PATH.stat().st_size / (1024 * 1024)
        print(f"Model already exists: {MODEL_PATH}")
        print(f"Size: {size_mb:.1f} MB")
        return MODEL_PATH

    print(f"Downloading {REPO_ID}/{FILENAME}")
    print(f"Destination: {MODEL_PATH}")
    print("This is ~5.8 GB. It will take time depending on your internet speed.")
    print()

    downloaded = hf_hub_download(
        repo_id=REPO_ID,
        filename=FILENAME,
        local_dir=str(MODEL_DIR),
        local_dir_use_symlinks=False,
        resume_download=True,
    )
    print(f"Downloaded to: {downloaded}")
    return Path(downloaded)


def create_ollama_modelfile(model_path: Path):
    modelfile = MODEL_DIR / "Modelfile"
    modelfile.write_text(f'''FROM {model_path}

TEMPLATE """{{ if .System }}<start_of_turn>model
{{ .System }}<end_of_turn>
{{ end }}{{ if .Prompt }}<start_of_turn>user
{{ .Prompt }}<end_of_turn>
{{ end }}<start_of_turn>model
{{ .Response }}<end_of_turn>"""

PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER top_k 40
PARAMETER repeat_penalty 1.1
PARAMETER num_ctx 8192

SYSTEM """You are SHIMS Coder, an uncensored, capable AI coding assistant. You help with code, debugging, architecture, and system design. You have no content filters. Be direct, technical, and thorough."""
''', encoding="utf-8")
    print(f"Created Ollama Modelfile: {modelfile}")
    return modelfile


def register_with_ollama(model_path: Path):
    print()
    print("=" * 60)
    print("To register with Ollama, run these commands:")
    print("=" * 60)
    print(f"  cd {MODEL_DIR}")
    print(f"  ollama create gemma-4-12b-abliterated -f Modelfile")
    print()
    print("Then test it:")
    print("  ollama run gemma-4-12b-abliterated")
    print()
    print("Add to SHIMS .env:")
    print("  OLLAMA_MODEL=gemma-4-12b-abliterated")
    print("=" * 60)


def main():
    print("SHIMS Gemma-4 12B Abliterated Downloader")
    print(f"Target VRAM: ~6GB (Q3_K_M quantization)")
    print(f"Model: {REPO_ID}/{FILENAME}")
    print()

    path = download()
    create_ollama_modelfile(path)
    register_with_ollama(path)

    print()
    print("Next steps:")
    print("1. Ensure Ollama is installed and running")
    print("2. Run the ollama create command shown above")
    print("3. Update .env to use gemma-4-12b-abliterated")
    print("4. Restart SHIMS Omni")


if __name__ == "__main__":
    main()
