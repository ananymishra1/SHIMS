from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from shared.media_memory import ingest_media


def test_ingest_image_uses_vision_and_stores_knowledge(tmp_path: Path) -> None:
    img = tmp_path / "screen.png"
    img.write_bytes(b"fake image bytes")

    with patch("shared.media_memory.vision_describe", return_value={"ok": True, "description": "A SHIMS dashboard screenshot.", "backend": "ollama"}) as mock_vision, \
         patch("shared.media_memory.ingest_knowledge", return_value="doc-123") as mock_ingest:
        result = ingest_media(str(img), "screen", title="Dashboard screenshot", tags=["screen"])

    assert result["ok"] is True
    assert result["doc_id"] == "doc-123"
    assert result["kind"] == "screen"
    mock_vision.assert_called_once()
    mock_ingest.assert_called_once()
    stored_content = mock_ingest.call_args.kwargs["content"]
    assert "Visual description" in stored_content
    assert "SHIMS dashboard" in stored_content


def test_ingest_audio_stores_transcript(tmp_path: Path) -> None:
    audio = tmp_path / "note.wav"
    audio.write_bytes(b"fake audio")

    with patch("shared.media_memory._transcribe_audio", return_value="Remind me to buy fluconazole API samples.") as mock_transcribe, \
         patch("shared.media_memory.ingest_knowledge", return_value="doc-audio") as mock_ingest:
        result = ingest_media(str(audio), "audio", title="Voice note")

    assert result["ok"] is True
    mock_transcribe.assert_called_once()
    stored_content = mock_ingest.call_args.kwargs["content"]
    assert "Audio transcript" in stored_content
    assert "fluconazole" in stored_content


def test_ingest_video_extracts_frames_and_describes(tmp_path: Path) -> None:
    video = tmp_path / "meeting.mp4"
    video.write_bytes(b"fake video")
    frames = [str(tmp_path / "frame_00000.jpg"), str(tmp_path / "frame_00005.jpg")]
    for f in frames:
        Path(f).write_bytes(b"fake frame")

    with patch("shared.media_memory._extract_video_keyframes", return_value=frames) as mock_frames, \
         patch("shared.media_memory.vision_describe", side_effect=[
             {"ok": True, "description": "Whiteboard with plan."},
             {"ok": True, "description": "Team discussing roadmap."},
         ]) as mock_vision, \
         patch("shared.media_memory.ingest_knowledge", return_value="doc-video") as mock_ingest:
        result = ingest_media(str(video), "video", title="Meeting recording")

    assert result["ok"] is True
    mock_frames.assert_called_once()
    assert mock_vision.call_count == 2
    stored_content = mock_ingest.call_args.kwargs["content"]
    assert "Video scene descriptions" in stored_content
    assert "Whiteboard" in stored_content


def test_ingest_media_returns_error_when_no_content(tmp_path: Path) -> None:
    img = tmp_path / "empty.png"
    img.write_bytes(b"fake")

    with patch("shared.media_memory.vision_describe", return_value={"ok": False, "error": "no vision model"}), \
         patch("shared.media_memory.ingest_knowledge") as mock_ingest:
        result = ingest_media(str(img), "image")

    assert result["ok"] is False
    mock_ingest.assert_not_called()


def test_ingest_media_rejects_unsupported_kind(tmp_path: Path) -> None:
    f = tmp_path / "x.bin"
    f.write_bytes(b"x")
    try:
        ingest_media(str(f), "unknown")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "unsupported media kind" in str(exc)
