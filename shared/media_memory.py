"""Native audio/video/screen memory ingestion.

Turns media files into searchable knowledge in the Omni Brain:
- images / screenshots  → vision description
- audio                 → Whisper transcription
- video                 → keyframe extraction + vision descriptions

All extracted text is embedded and blended into vector search.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .omni_brain import ingest_knowledge
from .vision import describe_image as vision_describe


def _transcribe_audio(path: str, *, model_size: str | None = None) -> str:
    """Best-effort local audio transcription via faster-whisper."""
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise RuntimeError("faster-whisper not installed") from exc

    size = model_size or "base"
    # Use CPU by default for compatibility; device can be overridden by caller env.
    whisper = WhisperModel(size, device="cpu", compute_type="int8")
    segments, _ = whisper.transcribe(path, language=None)
    return " ".join(s.text.strip() for s in segments)


def _extract_video_keyframes(path: str, interval_seconds: int = 5) -> list[str]:
    """Extract keyframe paths from a video using ffmpeg. Returns empty if ffmpeg unavailable."""
    probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
    try:
        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        duration = float(result.stdout.strip())
    except Exception:
        return []

    out_dir = Path(path).parent / f".{Path(path).stem}_frames"
    out_dir.mkdir(exist_ok=True)
    frames: list[str] = []
    for t in range(0, int(duration), interval_seconds):
        out = out_dir / f"frame_{t:05d}.jpg"
        cmd = [
            "ffmpeg", "-y", "-ss", str(t), "-i", path,
            "-vframes", "1", "-q:v", "2", "-vf", "scale='min(1280,iw)':-1",
            str(out),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
            if out.exists():
                frames.append(str(out))
        except Exception:
            continue
    return frames


def ingest_media(
    path: str,
    kind: str,
    title: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    *,
    audio_model_size: str | None = None,
    video_frame_interval: int = 5,
) -> dict[str, Any]:
    """Extract text from a media file and store it in the Omni Brain.

    kind: image | audio | video | screen
    """
    p = Path(path)
    kind = (kind or "").lower().strip()
    if kind not in {"image", "audio", "video", "screen"}:
        raise ValueError(f"unsupported media kind: {kind}")
    if not p.exists():
        raise FileNotFoundError(path)

    title = title or f"{kind} {p.name}"
    tags = list(tags or [kind, "media"])
    meta = {"source_path": str(p.resolve()), "kind": kind, **(metadata or {})}
    chunks: list[str] = []

    if kind in {"image", "screen"}:
        try:
            result = vision_describe(str(p), prompt="Describe this image concisely for a memory archive.")
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        if result.get("ok"):
            chunks.append(f"Visual description: {result.get('description', '')}")
        meta["vision_backend"] = result.get("backend")

    elif kind == "audio":
        try:
            transcript = _transcribe_audio(str(p), model_size=audio_model_size)
            if transcript:
                chunks.append(f"Audio transcript: {transcript}")
                tags.append("transcript")
        except Exception as exc:
            chunks.append(f"Audio transcription unavailable: {exc}")

    elif kind == "video":
        frames = _extract_video_keyframes(str(p), interval_seconds=video_frame_interval)
        descriptions: list[str] = []
        for frame in frames:
            try:
                result = vision_describe(frame, prompt="Describe this video frame concisely.")
                if result.get("ok"):
                    descriptions.append(result.get("description", ""))
            except Exception:
                continue
        if descriptions:
            chunks.append("Video scene descriptions:\n" + "\n".join(f"- {d}" for d in descriptions))
        meta["keyframes"] = len(frames)
        tags.append("video")

    content = "\n\n".join(chunks).strip()
    if not content:
        return {"ok": False, "error": f"no content extracted from {kind}", "metadata": meta}

    doc_id = ingest_knowledge(
        title=title,
        content=content,
        source_type=f"media:{kind}",
        source_uri=f"file:{path}",
        tags=tags,
        importance=0.7,
        metadata=meta,
    )
    return {"ok": True, "doc_id": doc_id, "kind": kind, "title": title, "metadata": meta}
