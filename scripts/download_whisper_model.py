"""Download the faster-whisper speech model for offline server STT.

Run this ONCE on a network that can reach huggingface.co (e.g. a phone hotspot
if your main network blocks it). After it succeeds, SHIMS server speech-to-text
works fully offline — used automatically when the browser's Google speech
backend returns a 'network' error.

Behaviour:
  * If SHIMS_WHISPER_MODEL (or --target) is a folder path, the model is
    downloaded INTO that folder (this is what SHIMS reads — portable, offline).
  * Otherwise it is treated as a size name (tiny/base/small/medium/large-v3)
    and cached in the default HuggingFace cache.

    python scripts/download_whisper_model.py
    python scripts/download_whisper_model.py --size base
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Files required by faster-whisper / CTranslate2.
_REQUIRED = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]


def _looks_like_path(value: str) -> bool:
    return any(sep in value for sep in ("/", "\\", ":")) or os.path.isdir(value)


def main() -> int:
    ap = argparse.ArgumentParser(description="Download faster-whisper model for offline STT.")
    ap.add_argument("--size", default="small",
                    help="Model size: tiny | base | small | medium | large-v3 (default: small)")
    ap.add_argument("--target", default=os.getenv("SHIMS_WHISPER_MODEL", "small"),
                    help="Folder to download into, or a size name. Defaults to SHIMS_WHISPER_MODEL.")
    args = ap.parse_args()

    repo = f"Systran/faster-whisper-{args.size}"
    target = args.target

    if _looks_like_path(target):
        dest = Path(target)
        dest.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {repo} -> {dest}")
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=repo, local_dir=str(dest), allow_patterns=_REQUIRED + ["*.txt", "*.json"])
        except Exception as exc:
            print(f"FAILED: {exc}")
            _manual_hint(repo, dest)
            return 1
        missing = [f for f in _REQUIRED if not (dest / f).exists()]
        if missing:
            print(f"Incomplete download, missing: {missing}")
            _manual_hint(repo, dest)
            return 1
        print(f"OK — model files present in {dest}. Server STT will work offline.")
        return 0

    # size-name path: load via WhisperModel (caches in the HF cache dir)
    try:
        from faster_whisper import WhisperModel
        print(f"Downloading/loading faster-whisper '{target}' into the HuggingFace cache ...")
        WhisperModel(target, device=os.getenv("SHIMS_WHISPER_DEVICE", "auto"),
                     compute_type=os.getenv("SHIMS_WHISPER_COMPUTE", "int8"))
    except Exception as exc:
        print(f"FAILED: {exc}")
        _manual_hint(f"Systran/faster-whisper-{target}", None)
        return 1
    print(f"OK — model '{target}' cached. Server STT will work offline.")
    return 0


def _manual_hint(repo: str, dest: Path | None) -> None:
    print("\nThis network blocks huggingface.co file downloads. Options:")
    print("  1) Connect to a phone hotspot (cellular bypasses the firewall) and re-run this script.")
    print(f"  2) On any device/browser, download these files from https://huggingface.co/{repo}/tree/main")
    print(f"       {', '.join(_REQUIRED)}")
    if dest is not None:
        print(f"     and place them in:  {dest}")


if __name__ == "__main__":
    raise SystemExit(main())
