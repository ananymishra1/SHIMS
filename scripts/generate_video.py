#!/usr/bin/env python3
"""Local video generation with HunyuanVideo or SVD."""
import sys, argparse, torch
from pathlib import Path
from diffusers import HunyuanVideoPipeline, DiffusionPipeline

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "generated" / "videos"
OUT.mkdir(parents=True, exist_ok=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("prompt", help="Video prompt")
    p.add_argument("--model", default="tencent/HunyuanVideo", help="Model ID")
    p.add_argument("--frames", type=int, default=61, help="Number of frames")
    p.add_argument("--out", default=str(OUT), help="Output directory")
    args = p.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[VIDEO] Loading {args.model} on {device} ...")

    if "hunyuan" in args.model.lower():
        pipe = HunyuanVideoPipeline.from_pretrained(args.model, torch_dtype=torch.float16)
    else:
        pipe = DiffusionPipeline.from_pretrained(args.model, torch_dtype=torch.float16)
    pipe = pipe.to(device)

    video = pipe(args.prompt, num_frames=args.frames, num_inference_steps=30).frames[0]
    path = Path(args.out) / f"video_{hash(args.prompt) % 100000:05d}.mp4"
    # Save via imageio
    import imageio
    imageio.mimsave(path, video, fps=8)
    print(f"[VIDEO] Saved: {path}")

if __name__ == "__main__":
    main()
