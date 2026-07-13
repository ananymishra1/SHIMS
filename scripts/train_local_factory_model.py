"""Overnight training pipeline for the SHIMS Local Factory instance.

Modes (env ``SHIMS_FACTORY_TRAIN_MODE``):
    ollama   (default, fast)  - create an Ollama model tag with a factory system prompt
    peft                    - run a PEFT/LoRA fine-tune worker (CPU, slow, overnight)
    export                  - only export the dataset; do not train

Examples:
    .venv/Scripts/python scripts/train_local_factory_model.py
    .venv/Scripts/python scripts/train_local_factory_model.py --mode peft
    .venv/Scripts/python scripts/train_local_factory_model.py --mode export
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from shared.local_factory_config import default_model, models_dir, training_dir
from shared.local_factory_corpus import build_corpus, corpus_stats


TRAIN_MODE = os.getenv("SHIMS_FACTORY_TRAIN_MODE", "ollama").strip().lower()
BASE_MODEL = os.getenv("SHIMS_FACTORY_BASE_MODEL", default_model())
FACTORY_TAG = os.getenv("SHIMS_FACTORY_OLLAMA_TAG", "qwen2.5-3b-factory")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not path.exists():
        return items
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue
    return items


def _summarize_corpus_for_system_prompt(max_facts: int = 30) -> str:
    """Build a compact system prompt from the training dataset."""
    dataset_path = training_dir() / "dataset.jsonl"
    examples = _load_jsonl(dataset_path)[:max_facts]
    facts: list[str] = []
    for ex in examples:
        instruction = ex.get("instruction") or ex.get("input") or ""
        output = ex.get("output") or ex.get("text", "")
        if instruction:
            facts.append(f"- {instruction} -> {output[:180]}")
        elif output:
            facts.append(f"- {output[:200]}")
    if not facts:
        return "You are the SHIMS Local Factory assistant, specialised in pharma manufacturing, chemistry, and enterprise operations."
    return (
        "You are the SHIMS Local Factory assistant, specialised in pharma manufacturing, chemistry, and enterprise operations. "
        "Use the following facts from the SHIMS corpus when answering:\n\n"
        + "\n".join(facts)
    )


def _create_ollama_factory_model() -> dict[str, Any]:
    """Create a new Ollama tag with a factory-tuned system prompt."""
    modelfile_path = models_dir() / "Modelfile"
    system_prompt = _summarize_corpus_for_system_prompt()
    content = (
        f'FROM "{BASE_MODEL}"\n'
        'SYSTEM """\n'
        f'{system_prompt}\n'
        '"""\n'
        'PARAMETER temperature 0.25\n'
        'PARAMETER num_ctx 4096\n'
    )
    modelfile_path.write_text(content, encoding="utf-8")

    ollama_bin = os.getenv("OLLAMA_BIN", "ollama")
    try:
        result = subprocess.run(
            [ollama_bin, "create", FACTORY_TAG, "-f", str(modelfile_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        ok = result.returncode == 0
        return {
            "ok": ok,
            "tag": FACTORY_TAG,
            "modelfile": str(modelfile_path),
            "stdout": result.stdout[-500:],
            "stderr": result.stderr[-500:],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def _run_peft_training() -> dict[str, Any]:
    """Run the PEFT worker as a subprocess so slow imports do not block SHIMS."""
    worker = ROOT_DIR / "scripts" / "_train_peft_worker.py"
    python = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    cmd = [str(python), str(worker)]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=86400,  # overnight
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-800:],
            "stderr": result.stderr[-800:],
        }
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": "training timed out after 24h", "partial_stderr": (exc.stderr or "")[-500:]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def train_factory_model(*, mode: str | None = None, build_corpus_first: bool = True) -> dict[str, Any]:
    mode = (mode or TRAIN_MODE).lower()
    report: dict[str, Any] = {
        "started_at": _now(),
        "mode": mode,
        "base_model": BASE_MODEL,
        "factory_tag": FACTORY_TAG,
    }

    if build_corpus_first:
        report["corpus"] = build_corpus(force=False)
    else:
        report["corpus"] = {"stats": corpus_stats()}

    if mode == "export":
        report["dataset_path"] = str(training_dir() / "dataset.jsonl")
        report["ok"] = True
        return report

    if mode == "peft":
        report["training"] = _run_peft_training()
    elif mode == "ollama":
        report["training"] = _create_ollama_factory_model()
    else:
        report["training"] = {"ok": False, "error": f"unknown mode {mode}"}

    report["ok"] = report["training"].get("ok", False)
    report["finished_at"] = _now()

    # Persist report.
    report_path = models_dir() / "last_train_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Train the SHIMS Local Factory model")
    parser.add_argument("--mode", default=TRAIN_MODE, choices=["ollama", "peft", "export"])
    parser.add_argument("--no-build-corpus", action="store_true", help="skip corpus refresh")
    parser.add_argument("--base-model", default=BASE_MODEL)
    args = parser.parse_args()

    os.environ.setdefault("SHIMS_FACTORY_BASE_MODEL", args.base_model)
    result = train_factory_model(mode=args.mode, build_corpus_first=not args.no_build_corpus)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
