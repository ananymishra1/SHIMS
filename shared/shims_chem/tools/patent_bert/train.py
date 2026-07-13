"""
Training script for the patent-claim classifier.

Input: a JSONL file with one object per row:
    {"smiles": "...", "claim": "...", "label": 0 or 1}
where label=1 means "this claim covers a process that produces the SMILES" and
label=0 means it doesn't. Build the dataset by sampling SureChEMBL + USPTO
patent claims paired against ground-truth molecule-patent links.

Usage:
    python -m shims_chem.tools.patent_bert.train \
        --train data/claims_train.jsonl \
        --val   data/claims_val.jsonl \
        --base  microsoft/deberta-v3-small \
        --out   ./patent_claim_clf \
        --epochs 3 --batch 16 --lr 2e-5
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Fine-tune a BERT-style classifier on (SMILES, claim) -> covered.")
    ap.add_argument("--train", required=True)
    ap.add_argument("--val",   required=True)
    ap.add_argument("--base",  default="microsoft/deberta-v3-small")
    ap.add_argument("--out",   default="./patent_claim_clf")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch",  type=int, default=16)
    ap.add_argument("--lr",     type=float, default=2e-5)
    ap.add_argument("--maxlen", type=int, default=512)
    args = ap.parse_args()

    try:
        import torch
        from torch.utils.data import Dataset, DataLoader
        from transformers import (AutoTokenizer, AutoModelForSequenceClassification,
                                   get_linear_schedule_with_warmup)
    except ImportError:
        print("transformers + torch required. pip install 'shims-chem[train]'", file=sys.stderr)
        return 2

    class JsonlDS(Dataset):
        def __init__(self, path, tok, maxlen):
            self.rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
            self.tok = tok; self.maxlen = maxlen
        def __len__(self): return len(self.rows)
        def __getitem__(self, i):
            r = self.rows[i]
            enc = self.tok(f"[SMILES] {r['smiles']} [SEP] {r['claim']}",
                            truncation=True, padding="max_length", max_length=self.maxlen,
                            return_tensors="pt")
            return {k: v.squeeze(0) for k, v in enc.items()} | {"labels": torch.tensor(int(r["label"]))}

    tok = AutoTokenizer.from_pretrained(args.base)
    train_ds = JsonlDS(args.train, tok, args.maxlen)
    val_ds   = JsonlDS(args.val,   tok, args.maxlen)
    train_dl = DataLoader(train_ds, batch_size=args.batch, shuffle=True, num_workers=2)
    val_dl   = DataLoader(val_ds,   batch_size=args.batch)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModelForSequenceClassification.from_pretrained(args.base, num_labels=2).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    sched = get_linear_schedule_with_warmup(opt, num_warmup_steps=int(0.1 * len(train_dl) * args.epochs),
                                              num_training_steps=len(train_dl) * args.epochs)

    best_acc = 0.0
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    for epoch in range(1, args.epochs + 1):
        model.train()
        train_loss = 0.0
        for batch in train_dl:
            batch = {k: v.to(device) for k, v in batch.items()}
            opt.zero_grad()
            out_step = model(**batch)
            out_step.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step()
            train_loss += float(out_step.loss.item())

        model.eval()
        correct = total = 0
        with torch.no_grad():
            for batch in val_dl:
                labels = batch["labels"]
                batch = {k: v.to(device) for k, v in batch.items()}
                logits = model(**batch).logits
                pred = logits.argmax(dim=-1).cpu()
                correct += (pred == labels).sum().item()
                total += labels.size(0)
        acc = correct / max(1, total)
        print(f"[epoch {epoch}] train_loss={train_loss/len(train_dl):.4f}  val_acc={acc:.4f}")
        if acc > best_acc:
            best_acc = acc
            model.save_pretrained(out)
            tok.save_pretrained(out)
            print(f"  ↳ saved to {out}")

    print(f"Best val acc: {best_acc:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
