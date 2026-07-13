#!/usr/bin/env python3
r"""Resume stalled AMUSE video-model downloads directly from Hugging Face.

AMUSE sometimes leaves *.safetensors.download partial files and will not resume.
This script fetches the same Diffusers-format weights from the official
Wan-AI Hugging Face repositories into the AMUSE model folder, reusing any
shards that are already complete and cleaning up stale .download siblings.

Usage:
    cd C:\Users\direc\Desktop\SHIMS
    python tools\download_amuse_models.py --model wan2.2-t2v-a14b
    python tools\download_amuse_models.py --all
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# Make shared/ imports available when running outside the package root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from huggingface_hub import snapshot_download  # type: ignore
from shared.amd_acceleration import (
    _AMUSE_VIDEO_MODELS,
    _is_complete_model,
    _model_download_state,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("download_amuse_models")

# Map SHIMS model keys to official Hugging Face Diffusers repo IDs.
HF_REPO_IDS: dict[str, str] = {
    "wan2.2-t2v-a14b": "Wan-AI/Wan2.2-T2V-A14B-Diffusers",
    "wan2.1-t2v-14b": "Wan-AI/Wan2.1-T2V-14B-Diffusers",
}


def _remove_stale_download_files(model_path: Path) -> int:
    """Delete any *.download partial files under ``model_path``."""
    removed = 0
    for f in model_path.rglob("*.download"):
        try:
            logger.info(f"Removing stale partial file: {f}")
            f.unlink()
            removed += 1
        except OSError as exc:
            logger.warning(f"Could not remove {f}: {exc}")
    return removed


def _state_report(name: str, model_path: Path) -> dict[str, object]:
    """Return a short status dict for a model folder."""
    complete = _is_complete_model(model_path)
    state = _model_download_state(model_path)
    return {
        "name": name,
        "path": str(model_path),
        "complete": complete,
        "has_weights": state["has_weights"],
        "has_partial": state["has_partial"],
        "stale_minutes": state["stale_minutes"],
    }


def download_model(name: str, repo_id: str | None = None) -> dict[str, object]:
    """Download or resume one AMUSE video model from Hugging Face."""
    info = _AMUSE_VIDEO_MODELS.get(name)
    if info is None:
        raise ValueError(
            f"Unknown model '{name}'. Supported: {list(_AMUSE_VIDEO_MODELS.keys())}"
        )

    repo_id = repo_id or HF_REPO_IDS.get(name)
    if not repo_id:
        raise ValueError(f"No Hugging Face repo ID configured for '{name}'")

    model_path = Path(info["path"])
    model_path.mkdir(parents=True, exist_ok=True)

    before = _state_report(name, model_path)
    logger.info(f"Before download: {before}")

    logger.info(
        f"Resuming '{name}' from {repo_id} into {model_path}. "
        "This can take a long time for video models; progress is printed below."
    )

    try:
        # Download every file into the AMUSE folder. Existing complete shards
        # are reused automatically. Symlinks are disabled so AMUSE sees real
        # files it can load directly.
        snapshot_download(
            repo_id=repo_id,
            local_dir=str(model_path),
            local_dir_use_symlinks=False,
            resume_download=True,
        )
    except Exception as exc:
        logger.exception(f"Download failed for '{name}': {exc}")
        return {"name": name, "ok": False, "error": str(exc), "before": before}

    # Remove stale AMUSE .download files once the real shards are in place.
    removed = _remove_stale_download_files(model_path)
    if removed:
        logger.info(f"Removed {removed} stale .download file(s)")

    after = _state_report(name, model_path)
    logger.info(f"After download: {after}")

    return {
        "name": name,
        "ok": after["complete"],
        "complete": after["complete"],
        "removed_partial_files": removed,
        "before": before,
        "after": after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resume AMUSE video-model downloads from Hugging Face"
    )
    parser.add_argument(
        "--model",
        choices=list(_AMUSE_VIDEO_MODELS.keys()),
        help="Which model to download/resume",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Download/resume all supported AMUSE video models",
    )
    parser.add_argument(
        "--repo-id",
        help="Override the Hugging Face repo ID for --model",
    )
    args = parser.parse_args()

    if not args.model and not args.all:
        parser.error("Specify --model or --all")

    models = list(_AMUSE_VIDEO_MODELS.keys()) if args.all else [args.model]

    overall_ok = True
    start = time.time()
    for name in models:
        repo_id = args.repo_id if (args.model == name or not args.model) else None
        result = download_model(name, repo_id=repo_id)
        overall_ok = overall_ok and bool(result.get("ok"))
        print(f"\nResult for {name}:")
        for k, v in result.items():
            print(f"  {k}: {v}")
        print()

    elapsed = time.time() - start
    logger.info(f"Finished in {elapsed/60:.1f} minutes. overall_ok={overall_ok}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
