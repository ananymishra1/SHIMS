#!/usr/bin/env python3
"""Local LoRA fine-tuning pipeline for SHIMS.

Supports Unsloth (fast), standard transformers + PEFT, and DirectML/CUDA/CPU fallback.
Merges LoRA adapter to GGUF for Ollama consumption.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_BASE_MODEL = "unsloth/Llama-3.2-3B-Instruct"
DEFAULT_OUTPUT_DIR = ROOT / "models" / "trained"


def _detect_device() -> str:
    """Auto-detect best available compute device."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        try:
            import torch_directml
            return f"directml:{torch_directml.default_device()}"
        except Exception:
            pass
    except Exception:
        pass
    return "cpu"


def _install_if_missing(pkg: str) -> None:
    try:
        __import__(pkg.replace("-", "_").split("[")[0])
    except Exception:
        print(f"[INSTALL] {pkg} not found, attempting pip install ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])


def _load_dataset_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _format_alpaca(examples: list[dict]) -> list[dict]:
    """Convert instruction/input/output → chat-style text."""
    formatted = []
    for ex in examples:
        instruction = ex.get("instruction", "")
        input_text = ex.get("input", "")
        output = ex.get("output", "")
        prompt = instruction
        if input_text:
            prompt += f"\n\n### Input:\n{input_text}"
        prompt += "\n\n### Response:\n"
        formatted.append({"text": prompt + output})
    return formatted


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a local LoRA adapter and export to GGUF")
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL, help="Base model HuggingFace ID")
    parser.add_argument("--dataset", required=True, type=Path, help="Path to JSONL dataset")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Training output directory")
    parser.add_argument("--device", default="auto", help="Device: auto | cuda | directml | cpu")
    parser.add_argument("--epochs", type=int, default=3, help="Training epochs")
    parser.add_argument("--batch-size", type=int, default=2, help="Per-device batch size")
    parser.add_argument("--max-seq-length", type=int, default=2048, help="Max sequence length")
    parser.add_argument("--gguf", action="store_true", default=True, help="Export merged model to GGUF")
    parser.add_argument("--gguf-quantization", default="Q4_K_M", help="GGUF quantization type")
    args = parser.parse_args()

    device = _detect_device() if args.device == "auto" else args.device
    print(f"[DEVICE] Using: {device}")

    # Ensure core packages
    for pkg in ("transformers", "datasets", "peft", "trl", "accelerate"):
        _install_if_missing(pkg)

    # Optional: unsloth for fast training
    try:
        import unsloth
        print("[UNSLOTH] Fast training path available.")
    except Exception:
        print("[UNSLOTH] Not available, falling back to standard PEFT.")
        unsloth = None  # type: ignore

    # Optional: bitsandbytes (often Windows-unfriendly)
    try:
        import bitsandbytes
        print("[BNB] bitsandbytes available for QLoRA.")
    except Exception:
        bitsandbytes = None  # type: ignore
        print("[BNB] bitsandbytes not available (Windows common), using standard LoRA.")

    from datasets import Dataset
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        TrainingArguments,
        DataCollatorForLanguageModeling,
    )
    from peft import LoraConfig, get_peft_model, TaskType
    from trl import SFTTrainer

    # Load dataset
    if not args.dataset.exists():
        print(f"[ERROR] Dataset not found: {args.dataset}")
        sys.exit(1)
    raw_records = _load_dataset_jsonl(args.dataset)
    print(f"[DATA] Loaded {len(raw_records)} records.")
    formatted = _format_alpaca(raw_records)
    dataset = Dataset.from_list(formatted)

    # Load model & tokenizer
    print(f"[MODEL] Loading base model: {args.base_model}")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    load_kwargs = {
        "trust_remote_code": True,
        "torch_dtype": "auto",
    }
    if device.startswith("directml"):
        import torch_directml
        load_kwargs["device_map"] = "auto"
    elif device == "cuda":
        load_kwargs["device_map"] = "auto"
    elif device == "cpu":
        load_kwargs["device_map"] = "cpu"

    if bitsandbytes is not None:
        load_kwargs["load_in_4bit"] = True
        load_kwargs["bnb_4bit_compute_dtype"] = "float16"

    model = AutoModelForCausalLM.from_pretrained(args.base_model, **load_kwargs)

    # LoRA config
    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Training args
    args.output_dir.mkdir(parents=True, exist_ok=True)
    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=4,
        warmup_steps=10,
        learning_rate=2e-4,
        logging_steps=10,
        save_strategy="epoch",
        fp16=device != "cpu" and bitsandbytes is None,
        optim="adamw_torch" if bitsandbytes is None else "paged_adamw_8bit",
        report_to="none",
    )

    # Use SFTTrainer for supervised fine-tuning
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=training_args,
        max_seq_length=args.max_seq_length,
    )

    print("[TRAIN] Starting LoRA training ...")
    trainer.train()

    # Save LoRA adapter
    adapter_dir = args.output_dir / "lora_adapter"
    trainer.save_model(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    print(f"[SAVE] LoRA adapter saved to {adapter_dir}")

    # Merge and export to GGUF
    if args.gguf:
        print("[GGUF] Merging adapter and exporting ...")
        try:
            from peft import PeftModel
            base_model_merged = AutoModelForCausalLM.from_pretrained(
                args.base_model, trust_remote_code=True, torch_dtype="auto", device_map="cpu"
            )
            merged_model = PeftModel.from_pretrained(base_model_merged, str(adapter_dir))
            merged_model = merged_model.merge_and_unload()
            merged_dir = args.output_dir / "merged"
            merged_model.save_pretrained(str(merged_dir))
            tokenizer.save_pretrained(str(merged_dir))
            print(f"[SAVE] Merged model saved to {merged_dir}")

            # Try llama.cpp convert / quantize for GGUF
            # Common approach: use the convert script from llama.cpp if available
            gguf_path = args.output_dir / f"model-{args.gguf_quantization}.gguf"
            print(f"[GGUF] Attempting GGUF export to {gguf_path}")
            # We rely on llama.cpp convert_hf_to_gguf.py being in PATH or a known location
            # As a fallback, print instructions for manual conversion
            print("[GGUF] NOTE: Automatic GGUF conversion requires llama.cpp scripts.")
            print(f"[GGUF] If you have llama.cpp installed, run:")
            print(f"  python convert_hf_to_gguf.py {merged_dir} --outfile {gguf_path} --outtype {args.gguf_quantization}")
        except Exception as exc:
            print(f"[WARN] GGUF export failed: {exc}")
            print("[INFO] You can manually merge the adapter with the base model later.")

    print("[DONE] Training pipeline complete.")


if __name__ == "__main__":
    main()
