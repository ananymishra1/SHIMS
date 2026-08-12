"""Relocate GGUF models into SHIMS-owned storage/models, off the LM Studio dir.

SHIMS never used the LM Studio *server* for inference (the native engine runs its
own koboldcpp), but the model files still live under ~/.lmstudio/models. This
script moves them into storage/models so SHIMS fully owns them and no longer
depends on that folder.

Same-volume moves are instant and use no extra disk (an atomic rename), so this
works even though the library is larger than the free space. It is REVERSIBLE:
move the folders back to restore the previous layout.

SAFETY:
  * Refuses to run while SHIMS / the native engine is live (files would be locked).
  * Dry-run by default — prints the plan and changes nothing. Pass --apply to move.

Usage:
    # 1. Stop SHIMS first (close the app / stop the backend + engine).
    .venv/Scripts/python scripts/relocate_models.py            # preview
    .venv/Scripts/python scripts/relocate_models.py --apply    # do it
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "storage" / "models"
LM_STUDIO_MODELS = Path.home() / ".lmstudio" / "models"
# LM Studio bookkeeping folders that hold no servable GGUFs.
SKIP_NAMES = {"blobs", "manifests", ".internal", ".cache"}


def _engine_is_live() -> bool:
    """True if the SHIMS backend or native engine is up (moves would lock-fail)."""
    try:
        import psutil
    except Exception:
        print("! psutil unavailable — cannot verify SHIMS is stopped; aborting for safety.")
        return True
    for p in psutil.process_iter(["name"]):
        if "kobold" in (p.info.get("name") or "").lower():
            return True
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == "LISTEN" and c.laddr and c.laddr.port in (8010, 5115, 5001):
                return True
    except Exception:
        pass
    return False


def _dir_size_gb(path: Path) -> float:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total / (1024 ** 3)


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    if not LM_STUDIO_MODELS.is_dir():
        print(f"Nothing to do: {LM_STUDIO_MODELS} does not exist.")
        return 0
    if _engine_is_live():
        print("ABORT: SHIMS / the native engine appears to be running. Stop it first, then re-run.")
        return 1

    DEST.mkdir(parents=True, exist_ok=True)
    candidates = [
        d for d in LM_STUDIO_MODELS.iterdir()
        if d.is_dir() and d.name not in SKIP_NAMES and any(d.rglob("*.gguf"))
    ]
    if not candidates:
        print("No GGUF-bearing model folders found under the LM Studio dir.")
        return 0

    print(f"{'APPLYING' if apply else 'DRY-RUN'} — relocating into {DEST}\n")
    moved = 0
    for src in candidates:
        target = DEST / src.name
        size = _dir_size_gb(src)
        if target.exists():
            print(f"  SKIP  {src.name}  ({size:.1f} GB) — target already exists at {target}")
            continue
        print(f"  MOVE  {src.name}  ({size:.1f} GB)  ->  {target}")
        if apply:
            shutil.move(str(src), str(target))
            moved += 1

    if not apply:
        print("\nDry-run only. Re-run with --apply to move.")
        return 0

    print(f"\nMoved {moved} folder(s). Verifying discovery finds the configured brain model...")
    try:
        sys.path.insert(0, str(ROOT))
        from shared.native_engine import discovery
        import os
        model = (os.getenv("SHIMS_CHAT_MODEL") or "Qwen3.6-35B-A3B-Q8_0").strip()
        found = discovery.find_model(model)
        print(f"  discovery.find_model({model!r}) -> {'OK' if found else 'NOT FOUND'}")
    except Exception as exc:
        print(f"  (could not verify discovery: {exc})")
    print("Done. Start SHIMS again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
