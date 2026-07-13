#!/usr/bin/env python3
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
