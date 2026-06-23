"""Main wake word detector with pluggable backends."""
import os
from typing import Optional

from .dtw_backend import DTWBackend
from .utils import preprocess_audio

# Lazy import for optional openwakeword backend
try:
    import openwakeword
    _OPENWAKEWORD_AVAILABLE = True
except Exception:
    _OPENWAKEWORD_AVAILABLE = False


class _OpenWakeWordBackend:
    """Wraps openwakeword models for pre-trained wake word detection."""

    def __init__(self, model_dir: Optional[str] = None, sensitivity: float = 0.5):
        self.sensitivity = max(0.0, min(1.0, sensitivity))
        self.models: dict = {}
        self.model_dir = model_dir or os.path.join('data', 'wakeword', 'models')
        os.makedirs(self.model_dir, exist_ok=True)
        self._load_models()

    def _load_models(self) -> None:
        if not _OPENWAKEWORD_AVAILABLE:
            return
        import openwakeword
        for fname in os.listdir(self.model_dir):
            if fname.endswith('.onnx'):
                label = fname.replace('.onnx', '')
                path = os.path.join(self.model_dir, fname)
                try:
                    self.models[label] = openwakeword.Model(wakeword_model_paths=[path])
                except Exception:
                    pass

    def detect(self, audio_bytes: bytes) -> Optional[dict]:
        if not _OPENWAKEWORD_AVAILABLE or not self.models:
            return None
        pcm = preprocess_audio(audio_bytes, use_vad=False)
        if pcm is None:
            return None
        best_label = None
        best_score = 0.0
        for label, model in self.models.items():
            try:
                # openwakeword expects int16 PCM at 16kHz
                predictions = model.predict(pcm)
                score = predictions.get(label, 0.0)
                if score > best_score:
                    best_score = score
                    best_label = label
            except Exception:
                continue
        threshold = 0.5 + (0.4 * (1.0 - self.sensitivity))
        if best_label and best_score >= threshold:
            return {
                'label': best_label,
                'score': float(best_score),
                'confidence': float(best_score),
                'backend': 'openwakeword'
            }
        return None

    def list_wake_words(self) -> list[str]:
        return list(self.models.keys())

    def status(self) -> dict:
        return {
            'backend': 'openwakeword',
            'wake_words': self.list_wake_words(),
            'available': _OPENWAKEWORD_AVAILABLE,
            'sensitivity': self.sensitivity,
        }


class WakeWordDetector:
    """Unified wake word detector. Tries audio-level backends, falls back to text."""

    def __init__(self, text_wake_words: Optional[list[str]] = None,
                 dtw_threshold: float = 35.0, sensitivity: float = 0.5,
                 model_dir: Optional[str] = None, prefer_audio: bool = True):
        self.prefer_audio = prefer_audio
        self.text_wake_words = [w.lower() for w in (text_wake_words or [])]
        self.dtw = DTWBackend(threshold=dtw_threshold, sensitivity=sensitivity)
        self.oww = _OpenWakeWordBackend(model_dir=model_dir, sensitivity=sensitivity)

    def detect(self, audio_bytes: bytes, transcript: Optional[str] = None) -> Optional[dict]:
        """Detect wake word. If audio backends fail, check transcript text."""
        result = None
        if self.prefer_audio:
            # Try openwakeword first (most accurate if model exists)
            result = self.oww.detect(audio_bytes)
            if result is None:
                result = self.dtw.detect(audio_bytes)
        if result is None and transcript:
            text_lower = transcript.lower().strip()
            for ww in self.text_wake_words:
                if ww in text_lower:
                    result = {
                        'label': ww,
                        'score': 1.0,
                        'confidence': 1.0,
                        'backend': 'text'
                    }
                    break
        return result

    def enroll(self, label: str, audio_bytes: bytes) -> bool:
        """Enroll a new custom wake word via DTW backend."""
        return self.dtw.enroll(label, audio_bytes)

    def delete(self, label: str) -> bool:
        """Delete a wake word from DTW backend."""
        return self.dtw.delete(label)

    def list_wake_words(self) -> list[str]:
        words = set(self.dtw.list_wake_words()) | set(self.oww.list_wake_words())
        words.update(self.text_wake_words)
        return sorted(words)

    def status(self) -> dict:
        return {
            'prefer_audio': self.prefer_audio,
            'text_wake_words': self.text_wake_words,
            'dtw': self.dtw.status(),
            'openwakeword': self.oww.status(),
        }


# Global singleton
_DETECTOR: Optional[WakeWordDetector] = None


def get_detector() -> WakeWordDetector:
    global _DETECTOR
    if _DETECTOR is None:
        _DETECTOR = WakeWordDetector(
            text_wake_words=[
                'hey shims', 'hi shims', 'hello shims', 'ok shims', 'okay shims',
                'suno shims', 'sun rahe ho', 'arre shims', 'shims',
                'excuse me shims', 'listen shims', 'yo shims', 'shims assistant',
                'shims bot', 'shims ai', 'shims system'
            ],
            dtw_threshold=35.0,
            sensitivity=0.5,
        )
    return _DETECTOR
