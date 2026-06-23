"""PEFT/LoRA fine-tuning worker for the SHIMS Local Factory.

This script is meant to be invoked by ``train_local_factory_model.py`` as a
subprocess so that the slow torch/transformers imports do not block the main
SHIMS runtime.  It trains a LoRA adapter on top of qwen2.5:3b (or another
Ollama base) using the JSONL dataset in storage_local/training/dataset.jsonl.

Environment variables:
    SHIMS_FACTORY_BASE_MODEL     base model ID or path (default qwen2.5:3b)
    SHIMS_FACTORY_OUTPUT_DIR     adapter output dir
    SHIMS_FACTORY_EPOCHS         default 1
    SHIMS_FACTORY_MAX_STEPS      default 0 (use epochs)
    SHIMS_FACTORY_BATCH_SIZE     default 1
    SHIMS_FACTORY_LEARNING_RATE  default 2e-4
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
STORAGE_DIR = Path(os.getenv("SHIMS_STORAGE_DIR", ROOT_DIR / "storage")).resolve()
TRAINING_DIR = STORAGE_DIR / "training"
MODELS_DIR = STORAGE_DIR / "models"


def _load_dataset(path: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not path.exists():
        return items
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "messages" in obj:
                    # messages format -> text
                    text = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in obj["messages"])
                elif "text" in obj:
                    text = str(obj["text"])
                else:
                    instruction = str(obj.get("instruction", ""))
                    inp = str(obj.get("input", ""))
                    out = str(obj.get("output", ""))
                    text = f"Instruction: {instruction}\nInput: {inp}\nOutput: {out}" if instruction else out
                if text.strip():
                    items.append({"text": text.strip()})
            except Exception:
                continue
    return items


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(TRAINING_DIR / "dataset.jsonl"))
    parser.add_argument("--base-model", default=os.getenv("SHIMS_FACTORY_BASE_MODEL", "qwen2.5:3b"))
    parser.add_argument("--output-dir", default=os.getenv("SHIMS_FACTORY_OUTPUT_DIR", str(MODELS_DIR / "qwen2.5-3b-factory-lora")))
    parser.add_argument("--merged-dir", default=str(MODELS_DIR / "qwen2.5-3b-factory-merged"))
    parser.add_argument("--epochs", type=int, default=int(os.getenv("SHIMS_FACTORY_EPOCHS", "1")))
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("SHIMS_FACTORY_MAX_STEPS", "0")))
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("SHIMS_FACTORY_BATCH_SIZE", "1")))
    parser.add_argument("--lr", type=float, default=float(os.getenv("SHIMS_FACTORY_LEARNING_RATE", "2e-4")))
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    examples = _load_dataset(dataset_path)
    if not examples:
        print(f"[train] no training examples in {dataset_path}")
        return 1

    print(f"[train] loaded {len(examples)} examples")
    print(f"[train] base model: {args.base_model}")
    print(f"[train] output dir: {args.output_dir}")

    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model, PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, DataCollatorForLanguageModeling
        from trl import SFTTrainer
    except Exception as exc:
        print(f"[train] missing dependency: {exc}")
        return 2

    device = "cpu"
    torch_dtype = torch.float32

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch_dtype,
        device_map=device,
        trust_remote_code=True,
    )

    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=["q_proj", "v_proj", "k_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)

    dataset = Dataset.from_list(examples)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=args.epochs,
        max_steps=args.max_steps if args.max_steps > 0 else -1,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=4,
        learning_rate=args.lr,
        logging_steps=10,
        save_steps=200,
        save_total_limit=2,
        fp16=False,
        bf16=False,
        optim="adamw_torch",
        report_to=[],
        remove_unused_columns=False,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        tokenizer=tokenizer,
        dataset_text_field="text",
        max_seq_length=512,
    )
    trainer.train()
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)

    # Merge adapter into base model for easier Ollama ingestion later.
    try:
        merged_dir = Path(args.merged_dir)
        merged_dir.mkdir(parents=True, exist_ok=True)
        base = AutoModelForCausalLM.from_pretrained(
            args.base_model,
            torch_dtype=torch_dtype,
            device_map=device,
            trust_remote_code=True,
        )
        merged = PeftModel.from_pretrained(base, str(output_dir))
        merged = merged.merge_and_unload()
        merged.save_pretrained(merged_dir)
        tokenizer.save_pretrained(merged_dir)
        print(f"[train] merged model saved to {merged_dir}")
    except Exception as exc:
        print(f"[train] merge skipped: {exc}")

    print("[train] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
