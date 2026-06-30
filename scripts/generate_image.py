#!/usr/bin/env python3
"""Local image generation with FLUX or Stable Diffusion."""
import sys, argparse, torch
from pathlib import Path
from diffusers import FluxPipeline, StableDiffusionXLPipeline

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "generated" / "images"
OUT.mkdir(parents=True, exist_ok=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("prompt", help="Image prompt")
    p.add_argument("--model", default="black-forest-labs/FLUX.1-schnell", help="HuggingFace model ID")
    p.add_argument("--steps", type=int, default=4, help="Inference steps")
    p.add_argument("--out", default=str(OUT), help="Output directory")
    args = p.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[IMAGE] Loading {args.model} on {device} ...")

    if "flux" in args.model.lower():
        pipe = FluxPipeline.from_pretrained(args.model, torch_dtype=torch.bfloat16)
    else:
        pipe = StableDiffusionXLPipeline.from_pretrained(args.model, torch_dtype=torch.float16)
    pipe = pipe.to(device)

    image = pipe(args.prompt, num_inference_steps=args.steps, guidance_scale=0.0 if "schnell" in args.model else 7.5).images[0]
    path = Path(args.out) / f"generated_{hash(args.prompt) % 100000:05d}.png"
    image.save(path)
    print(f"[IMAGE] Saved: {path}")

if __name__ == "__main__":
    main()
