from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.omni_brain import ingest_knowledge


DEFAULT_FILES = [
    "deep-research-report.md",
    "GEMINI_REVIEW_REQUEST_2026-05-22.md",
    "PATCH_NOTES.md",
    "README.md",
    "README0001.md",
    "Research Report (1).md",
    "Research Report.md",
]


def ingest_folder(folder: Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for name in DEFAULT_FILES:
        path = folder / name
        if not path.exists():
            results.append({"ok": False, "title": name, "chunks": 0, "message": "missing"})
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        result = ingest_knowledge(
            title=f"User research: {path.stem}",
            text=text,
            source_type="user_research",
            source_uri=str(path),
            tags=["user-research", "omni-blueprint", "personalization", "self-evolution"],
            importance=1.3,
        )
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest the user's Omni research notes into SHIMS Omni Brain RAG.")
    parser.add_argument("folder", nargs="?", default=r"E:\New folder", help="Folder containing the research markdown files.")
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        print(f"Research folder not found: {folder}")
        return 1

    total_chunks = 0
    for result in ingest_folder(folder):
        total_chunks += int(result.get("chunks") or 0)
        status = "ok" if result.get("ok") else "skip"
        print(f"{status}: {result.get('title')} chunks={result.get('chunks')} {result.get('message', '')}")
    print(f"total_chunks={total_chunks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
