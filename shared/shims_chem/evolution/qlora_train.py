"""
Nightly QLoRA training for the smart brain (or the fast brain).

Trains a small LoRA adapter on episodic memory: every (user_text, intent,
verifier-grounded answer) tuple becomes a training example. The adapter is
written to a versioned directory and registered as an evolution Candidate
of kind='lora_adapter'. Promotion is human-gated.

Usage:
    python -m shims_chem.evolution.qlora_train \
        --base unsloth/Qwen2.5-14B-Instruct-bnb-4bit \
        --episodes ~/.shims_chem/memory/memory.sqlite \
        --out ~/.shims_chem/adapters/$(date +%Y%m%d) \
        --epochs 1 --batch 2 --lr 2e-4 --maxlen 4096

This is intentionally small. The Helios's 12 GB VRAM handles QLoRA on a 14B
model fine with batch 2 + gradient accumulation. For the 70B+ smart-brain
adapters, run on the home-base desktop with more VRAM.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


def _load_episodes(memory_path: str, *, min_ok: bool = True) -> list[dict]:
    if not Path(memory_path).exists():
        return []
    conn = sqlite3.connect(memory_path)
    rows = conn.execute(
        "SELECT user_text, intent, final_summary, ok FROM episodes WHERE final_summary <> ''"
    ).fetchall()
    conn.close()
    examples = []
    for user_text, intent, summary, ok in rows:
        if min_ok and not ok:
            continue
        # Format as a single conversation turn pair.
        examples.append({
            "user": user_text,
            "assistant": summary,
            "intent": intent,
        })
    return examples


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="Base model name on HF or local path")
    ap.add_argument("--episodes", required=True, help="Path to memory.sqlite")
    ap.add_argument("--out", required=True, help="Output adapter dir")
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--accum", type=int, default=8)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--maxlen", type=int, default=4096)
    ap.add_argument("--rank", type=int, default=16)
    args = ap.parse_args()

    try:
        from unsloth import FastLanguageModel
        from datasets import Dataset
        import torch
        from trl import SFTTrainer
        from transformers import TrainingArguments
    except ImportError:
        print("Training deps missing. pip install 'shims-chem[train]'", file=sys.stderr)
        return 2

    examples = _load_episodes(args.episodes)
    if not examples:
        print(f"No training examples in {args.episodes}; skipping.", file=sys.stderr)
        return 1
    print(f"Loaded {len(examples)} examples from episodic memory.")

    model, tok = FastLanguageModel.from_pretrained(
        model_name=args.base,
        max_seq_length=args.maxlen,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.rank,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                         "gate_proj", "up_proj", "down_proj"],
        lora_alpha=args.rank * 2,
        lora_dropout=0.05,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    def fmt(ex):
        prompt = (f"<|im_start|>system\nYou are Shims Chem.<|im_end|>\n"
                  f"<|im_start|>user\n{ex['user']}<|im_end|>\n"
                  f"<|im_start|>assistant\n{ex['assistant']}<|im_end|>")
        return {"text": prompt}

    ds = Dataset.from_list(examples).map(fmt)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    trainer = SFTTrainer(
        model=model, tokenizer=tok, train_dataset=ds, dataset_text_field="text",
        max_seq_length=args.maxlen,
        args=TrainingArguments(
            output_dir=str(out_dir),
            per_device_train_batch_size=args.batch,
            gradient_accumulation_steps=args.accum,
            warmup_ratio=0.03,
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=10, save_steps=200, save_total_limit=2,
            optim="adamw_8bit", weight_decay=0.01, lr_scheduler_type="linear",
            seed=42,
        ),
    )
    trainer.train()
    model.save_pretrained(out_dir)
    tok.save_pretrained(out_dir)
    (out_dir / "shims_meta.json").write_text(json.dumps({
        "base": args.base, "n_examples": len(examples), "rank": args.rank,
    }, indent=2))
    print(f"\nAdapter saved to {out_dir}")
    print("Register it with the archive via: shims-chem evo register-adapter <path>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
