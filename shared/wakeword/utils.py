"""Audio preprocessing utilities for wake word detection."""
import io
import struct
import wave
from typing import Optional

import numpy as np
import python_speech_features

try:
    import webrtcvad
except Exception:  # pragma: no cover - optional dependency, no wheel on Python 3.14+
    webrtcvad = None


def load_wav_bytes(audio_bytes: bytes) -> tuple[np.ndarray, int]:
    """Load WAV from bytes, return (pcm_array, sample_rate)."""
    with io.BytesIO(audio_bytes) as buf:
        with wave.open(buf, 'rb') as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
    if sampwidth == 2:
        pcm = np.frombuffer(raw, dtype=np.int16)
    elif sampwidth == 1:
        pcm = np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128
    elif sampwidth == 4:
        pcm = np.frombuffer(raw, dtype=np.int32).astype(np.int16)
    else:
        raise ValueError(f'Unsupported sample width: {sampwidth}')
    if n_channels > 1:
        pcm = pcm.reshape(-1, n_channels).mean(axis=1).astype(np.int16)
    return pcm, rate


def resample(pcm: np.ndarray, src_rate: int, dst_rate: int = 16000) -> np.ndarray:
    """Simple linear interpolation resampling."""
    if src_rate == dst_rate:
        return pcm
    ratio = dst_rate / src_rate
    new_len = int(len(pcm) * ratio)
    old_indices = np.linspace(0, len(pcm) - 1, new_len)
    indices = old_indices.astype(np.int32)
    frac = old_indices - indices
    indices_next = np.clip(indices + 1, 0, len(pcm) - 1)
    return (pcm[indices] * (1 - frac) + pcm[indices_next] * frac).astype(np.int16)


def normalize(pcm: np.ndarray) -> np.ndarray:
    """Normalize to [-1, 1] float32."""
    pcm = pcm.astype(np.float32)
    max_val = np.max(np.abs(pcm))
    if max_val > 0:
        pcm = pcm / max_val
    return pcm


def float_to_int16(pcm: np.ndarray) -> np.ndarray:
    """Convert float [-1,1] to int16."""
    return np.clip(pcm * 32767, -32768, 32767).astype(np.int16)


def frame_generator(frame_duration_ms: int, audio: np.ndarray, sample_rate: int):
    """Yield audio frames of given duration in ms."""
    n = int(sample_rate * (frame_duration_ms / 1000.0) * 2)
    offset = 0
    while offset + n <= len(audio):
        yield audio[offset:offset + n]
        offset += n


def vad_filter(pcm: np.ndarray, sample_rate: int, aggressiveness: int = 2, frame_ms: int = 30) -> np.ndarray:
    """Remove non-speech frames using WebRTC VAD. Returns speech segments concatenated."""
    if webrtcvad is None:
        return pcm
    if sample_rate not in (8000, 16000, 32000, 48000):
        pcm = resample(pcm, sample_rate, 16000)
        sample_rate = 16000
    vad = webrtcvad.Vad(aggressiveness)
    frames = list(frame_generator(frame_ms, pcm.tobytes(), sample_rate))
    speech_bytes = b''.join(f for f in frames if vad.is_speech(f, sample_rate))
    if not speech_bytes:
        return np.array([], dtype=np.int16)
    return np.frombuffer(speech_bytes, dtype=np.int16)


def extract_mfcc(pcm: np.ndarray, sample_rate: int = 16000, num_cepstral: int = 13,
                 num_filters: int = 26, fft_size: int = 512, winlen: float = 0.025,
                 winstep: float = 0.01) -> np.ndarray:
    """Extract MFCC features from PCM audio."""
    pcm_float = normalize(pcm)
    mfcc = python_speech_features.mfcc(
        pcm_float, samplerate=sample_rate, numcep=num_cepstral,
        nfilt=num_filters, nfft=fft_size, winlen=winlen, winstep=winstep
    )
    return mfcc


def preprocess_audio(audio_bytes: bytes, target_rate: int = 16000,
                     use_vad: bool = True) -> Optional[np.ndarray]:
    """Full preprocessing pipeline: load → resample → VAD → normalize."""
    try:
        pcm, rate = load_wav_bytes(audio_bytes)
    except Exception:
        return None
    if rate != target_rate:
        pcm = resample(pcm, rate, target_rate)
    if use_vad:
        pcm = vad_filter(pcm, target_rate)
    if len(pcm) < target_rate * 0.3:  # less than 300ms after VAD
        return None
    return normalize(pcm)


def pcm_to_wav_bytes(pcm: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Convert float PCM to WAV bytes."""
    pcm_int = float_to_int16(pcm)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_int.tobytes())
    return buf.getvalue()
