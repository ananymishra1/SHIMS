#!/usr/bin/env python3
"""
SHIMS Beast-Mode Setup Script
For: AMD Ryzen AI MAX+ 395 w/ Radeon 8060S + 128GB Unified RAM
Installs Ollama, pulls best models, configures .env, sets up media generation.
Run: .venv\Scripts\python scripts/setup_beast_mode.py
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BASE_DIR / ".env"
OLLAMA_URL = "https://ollama.com/download/OllamaSetup.exe"
OLLAMA_EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
OLLAMA_PORTABLE = Path("C:/Program Files/Ollama/ollama.exe")

MODELS = {
    "primary": "llama3.3:70b",
    "fast": "qwen3:32b",
    "coder": "deepseek-coder-v2:16b",
    "vision": "gemma3:27b",
    "instruct": "command-r-plus:104b",
}

ENV_TEMPLATE = """# SHIMS Environment Configuration — Optimized for AMD Ryzen AI MAX+ 395 (128GB Unified RAM)
# Generated: Auto-configured for beast-mode local AI

# ============================================================
# Core Settings
# ============================================================
SHIMS_SECRET_KEY=shims-beast-mode-secure-key-2026
ENTERPRISE_BRIDGE_TOKEN=shims-local-bridge-token

# Company defaults (customize as needed)
COMPANY_NAME=J K Lifecare Centers Private Limited
COMPANY_GST=23AAECJ6427F1ZS
COMPANY_ADDRESS=Plot No. 97, DMIC VUL, Ujjain, M.P., India 456664
COMPANY_PHONE=+917000452122
COMPANY_EMAIL=info@jklifecarecenters.com

# Paths
DATA_DIR=./data
GENERATED_DIR=./generated
WORKSPACE_DIR=./workspace

# Database (SQLite for local-first)
OMNI_DATABASE_URL=sqlite:///./data/shims_omni.db
ENTERPRISE_DATABASE_URL=sqlite:///./data/shims_enterprise.db

# ============================================================
# LLM Configuration — Local-first with Ollama
# ============================================================
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434

# Primary model: Llama 3.3 70B — best quality for 128GB RAM
SHIMS_OLLAMA_MODEL=llama3.3:70b

# Fallback fast model
SHIMS_OLLAMA_FAST_MODEL=qwen3:32b

# Coding specialist
SHIMS_OLLAMA_CODER=deepseek-coder-v2:16b

# Vision/multimodal model
SHIMS_OLLAMA_VISION=gemma3:27b

# Optional: Cloud API keys (leave empty to use local-only)
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini

GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-pro

# ============================================================
# Media Generation Models
# ============================================================
SHIMS_IMAGE_MODEL=flux.1-schnell
SHIMS_IMAGE_DEVICE=auto
SHIMS_VIDEO_MODEL=hunyuanvideo
SHIMS_WHISPER_MODEL=large-v3
SHIMS_TTS_MODEL=bark

# ============================================================
# Self-Evolution & Advanced Features
# ============================================================
SELF_EVOLUTION_ENABLED=true
SELF_EVOLUTION_REQUIRE_TESTS=false
SELF_EVOLUTION_ALLOWED_PATHS=apps,shims_core,tests,docs

# Neural Governor (system monitoring)
SHIMS_NEURAL_GOVERNOR=true
SHIMS_MAX_CPU_PERCENT=80
SHIMS_MAX_RAM_PERCENT=85

# ============================================================
# Optional: Gmail OAuth (for mail features)
# ============================================================
SHIMS_GMAIL_CLIENT_ID=
SHIMS_GMAIL_CLIENT_SECRET=
SHIMS_GMAIL_REDIRECT_URI=http://localhost:8010/oauth/gmail/callback

# Optional: Web Search
SERPAPI_KEY=
BRAVE_API_KEY=

# Optional: Browser automation
PLAYWRIGHT_BROWSERS_PATH=0
"""

def run(cmd, **kw):
    print(f"[RUN] {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    return subprocess.run(cmd, shell=isinstance(cmd, str), check=False, **kw)

def find_ollama():
    """Find ollama executable on Windows."""
    paths = [
        OLLAMA_EXE,
        OLLAMA_PORTABLE,
        Path("C:/Users/direc/AppData/Local/Programs/Ollama/ollama.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "ollama.exe",
    ]
    for p in paths:
        if p.exists():
            return p
    # Check PATH
    oll = shutil.which("ollama")
    return Path(oll) if oll else None

def install_ollama():
    print("\n[SHIMS] Checking for Ollama...")
    ollama = find_ollama()
    if ollama:
        print(f"[SHIMS] Ollama found: {ollama}")
        return ollama
    print("[SHIMS] Ollama not found. Please install it from https://ollama.com/download")
    print("[SHIMS] Or run this in PowerShell (Admin):")
    print('    winget install Ollama.Ollama')
    print("[SHIMS] After installing, re-run this script.")
    return None

def pull_models(ollama: Path):
    print("\n[SHIMS] Pulling recommended models for your 128GB beast...")
    for role, model in MODELS.items():
        print(f"\n[SHIMS] Pulling {role} model: {model} ...")
        run([str(ollama), "pull", model])
    print("\n[SHIMS] All models pulled!")

def create_env():
    if ENV_PATH.exists():
        print(f"[SHIMS] {ENV_PATH} already exists. Skipping creation.")
        return
    ENV_PATH.write_text(ENV_TEMPLATE, encoding="utf-8")
    print(f"[SHIMS] Created {ENV_PATH}")

def install_media_deps():
    print("\n[SHIMS] Installing optional media generation dependencies...")
    req = BASE_DIR / "requirements-optional-media.txt"
    if req.exists():
        run([sys.executable, "-m", "pip", "install", "-r", str(req)])
    else:
        # Install key packages manually
        packages = [
            "torch", "torchvision", "torchaudio",
            "accelerate", "safetensors", "transformers", "diffusers",
            "xformers",
            "imageio-ffmpeg", "opencv-python",
        ]
        run([sys.executable, "-m", "pip", "install"] + packages)

def create_media_scripts():
    """Create helper scripts for image/video/audio generation."""
    scripts_dir = BASE_DIR / "scripts"

    # Image generation script
    image_script = scripts_dir / "generate_image.py"
    image_script.write_text('''#!/usr/bin/env python3
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
''', encoding="utf-8")

    # Video generation script
    video_script = scripts_dir / "generate_video.py"
    video_script.write_text('''#!/usr/bin/env python3
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
''', encoding="utf-8")

    # Audio generation script
    audio_script = scripts_dir / "generate_audio.py"
    audio_script.write_text('''#!/usr/bin/env python3
"""Local audio generation with Bark, MusicGen, or Whisper STT."""
import sys, argparse, torch
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "generated" / "audio"
OUT.mkdir(parents=True, exist_ok=True)

def tts_bark(prompt: str, out: Path):
    from transformers import AutoProcessor, BarkModel
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[AUDIO] Loading Bark on {device} ...")
    processor = AutoProcessor.from_pretrained("suno/bark-small")
    model = BarkModel.from_pretrained("suno/bark-small").to(device)
    inputs = processor(prompt, voice_preset="v2/en_speaker_6")
    speech = model.generate(**inputs.to(device))
    import scipy.io.wavfile as wavfile
    path = out / f"bark_{hash(prompt) % 100000:05d}.wav"
    wavfile.write(path, rate=24000, data=speech.cpu().numpy().squeeze())
    print(f"[AUDIO] Saved: {path}")
    return path

def stt_whisper(audio_path: str, model_size: str = "large-v3"):
    from faster_whisper import WhisperModel
    print(f"[AUDIO] Loading Whisper {model_size} ...")
    m = WhisperModel(model_size, device="cuda" if torch.cuda.is_available() else "cpu", compute_type="float16")
    segments, info = m.transcribe(audio_path, beam_size=5)
    text = " ".join([s.text for s in segments])
    print(f"[AUDIO] Transcription ({info.language}): {text}")
    return text

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tts", help="Text to speak")
    p.add_argument("--stt", help="Audio file to transcribe")
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args()
    if args.tts:
        tts_bark(args.tts, Path(args.out))
    if args.stt:
        stt_whisper(args.stt)

if __name__ == "__main__":
    main()
''', encoding="utf-8")

    print("[SHIMS] Created media generation scripts:")
    print(f"  - {image_script}")
    print(f"  - {video_script}")
    print(f"  - {audio_script}")

def main():
    print("=" * 60)
    print("  SHIMS Beast-Mode Setup")
    print("  Target: AMD Ryzen AI MAX+ 395 + 128GB RAM")
    print("=" * 60)

    create_env()
    ollama = install_ollama()
    if ollama:
        pull_models(ollama)
    else:
        print("[SHIMS] WARNING: Ollama not installed. Please install it manually.")

    install_media_deps()
    create_media_scripts()

    print("\n" + "=" * 60)
    print("  Setup Complete!")
    print("=" * 60)
    print(f"\nNext steps:")
    print(f"  1. Start Ollama (if not running): ollama serve")
    print(f"  2. Launch SHIMS: .venv\\Scripts\\python scripts\\start_shims.py")
    print(f"  3. Open browser: http://127.0.0.1:8010")
    print(f"\nModel recommendations for your 128GB machine:")
    for role, model in MODELS.items():
        print(f"  [{role:10s}] {model}")
    print(f"\nMedia generation:")
    print(f"  - Image: .venv\\Scripts\\python scripts\\generate_image.py 'a cat in space'")
    print(f"  - Video: .venv\\Scripts\\python scripts\\generate_video.py 'a robot dancing'")
    print(f"  - Audio: .venv\\Scripts\\python scripts\\generate_audio.py --tts 'Hello world'")

if __name__ == "__main__":
    main()
