#!/usr/bin/env python3
r"""
Bulk-ingest pharmacopeia PDFs into SHIMS Enterprise.

Usage:
    .venv\Scripts\python scripts\ingest_pharmacopeia.py "C:\Users\anany\OneDrive\Desktop\pharmacoepia" --limit 10
    .venv\Scripts\python scripts\ingest_pharmacopeia.py "C:\Users\anany\OneDrive\Desktop\pharmacoepia" --subset ip
    .venv\Scripts\python scripts\ingest_pharmacopeia.py "C:\Users\anany\OneDrive\Desktop\pharmacoepia" --subset usp-general
    .venv\Scripts\python scripts\ingest_pharmacopeia.py "C:\Users\anany\OneDrive\Desktop\pharmacoepia" --subset usp-monographs --limit 100

Subsets:
    ip              -> INDIAN PHARMACOPOEIA (2022) VOL-*.pdf
    usp-general     -> USP 2024/General/*.pdf
    usp-monographs  -> USP 2024/Monographs/*.pdf
    (none)          -> all supported PDFs under the source folder

Files are imported into the Enterprise BMR corpus (storage/enterprise_bmr_corpus/sources)
and indexed for search. Duplicate file hashes are skipped automatically.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from shared.pharmacopeia_ingest import collect_pharmacopeia_pdfs, run_pharmacopeia_ingest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk-ingest pharmacopeia PDFs into SHIMS Enterprise.")
    parser.add_argument("source", help="path to the pharmacopeia folder")
    parser.add_argument("--subset", choices=["ip", "usp-general", "usp-monographs"], help="ingest only a subset")
    parser.add_argument("--limit", type=int, help="max number of PDFs to ingest")
    parser.add_argument("--user-id", type=int, default=1, help="user ID to record as importer (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="list files that would be ingested without importing")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        print(f"[ingest] source folder not found: {source}")
        return 1

    pdfs = collect_pharmacopeia_pdfs(source, args.subset)
    if args.subset and not pdfs:
        print(f"[ingest] no PDFs found for subset '{args.subset}' in {source}")
        return 1
    print(f"[ingest] found {len(pdfs)} PDF(s) in {source}")

    if args.limit:
        pdfs = pdfs[: args.limit]
        print(f"[ingest] limited to first {len(pdfs)} PDF(s)")

    if args.dry_run:
        for p in pdfs:
            try:
                print(f"  would ingest: {p}")
            except UnicodeEncodeError:
                print(f"  would ingest: {p.encode('utf-8', 'replace').decode('utf-8')}")
        return 0

    if not pdfs:
        print("[ingest] no PDFs to ingest")
        return 0

    print("[ingest] importing into Enterprise BMR corpus ...")
    result = run_pharmacopeia_ingest(source, subset=args.subset, limit=args.limit, user_id=args.user_id)

    stats = result.get("stats", {})
    print()
    print("=" * 50)
    print(" Ingestion complete")
    print("=" * 50)
    print(f"  found:      {stats.get('found', 0)}")
    print(f"  imported:   {stats.get('imported', 0)}")
    print(f"  duplicates: {stats.get('duplicates', 0)}")
    print(f"  failed:     {stats.get('failed', 0)}")
    print(f"  needs OCR:  {stats.get('needs_ocr', 0)}")
    print(f"  needs conv: {stats.get('needs_conversion', 0)}")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
