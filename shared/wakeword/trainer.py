"""Wake word enrollment and training management."""
import json
import os
import pathlib
from typing import Optional

import numpy as np

from .dtw_backend import DTWBackend
from .utils import pcm_to_wav_bytes, preprocess_audio

_DATA_DIR = pathlib.Path(__file__).resolve().parents[2] / 'data' / 'wakeword'
_SAMPLE_DIR = _DATA_DIR / 'samples'
_CONFIG_PATH = _DATA_DIR / 'trainer_config.json'


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        return json.loads(_CONFIG_PATH.read_text(encoding='utf-8'))
    return {}


def _save_config(cfg: dict) -> None:
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding='utf-8')


class WakeWordTrainer:
    """Manage enrollment of wake word samples and training DTW templates."""

    def __init__(self):
        _SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
        self.config = _load_config()
        self.dtw = DTWBackend()

    def enroll_sample(self, label: str, audio_bytes: bytes) -> dict:
        """Save a raw audio sample for a wake word label."""
        pcm = preprocess_audio(audio_bytes, use_vad=True)
        if pcm is None:
            return {'ok': False, 'error': 'No speech detected in sample'}
        label_dir = _SAMPLE_DIR / label
        label_dir.mkdir(parents=True, exist_ok=True)
        existing = list(label_dir.glob('*.wav'))
        idx = len(existing)
        path = label_dir / f'{idx:03d}.wav'
        path.write_bytes(pcm_to_wav_bytes(pcm))
        # Also add to DTW templates immediately
        ok = self.dtw.enroll(label, audio_bytes)
        if label not in self.config:
            self.config[label] = {'samples': 0}
        self.config[label]['samples'] = len(list(label_dir.glob('*.wav')))
        _save_config(self.config)
        return {
            'ok': ok,
            'label': label,
            'sample_index': idx,
            'total_samples': self.config[label]['samples'],
            'path': str(path.relative_to(_DATA_DIR)) if str(path).startswith(str(_DATA_DIR)) else str(path)
        }

    def delete_wake_word(self, label: str) -> dict:
        """Remove all samples and templates for a wake word."""
        ok = self.dtw.delete(label)
        label_dir = _SAMPLE_DIR / label
        if label_dir.exists():
            for f in label_dir.glob('*'):
                f.unlink()
            label_dir.rmdir()
        if label in self.config:
            del self.config[label]
            _save_config(self.config)
        return {'ok': ok, 'label': label}

    def list_wake_words(self) -> list[dict]:
        """List all enrolled wake words with sample counts."""
        result = []
        for label in sorted(self.config.keys()):
            label_dir = _SAMPLE_DIR / label
            count = len(list(label_dir.glob('*.wav'))) if label_dir.exists() else 0
            result.append({
                'label': label,
                'samples': count,
                'templates': len(self.dtw.templates.get(label, []))
            })
        return result

    def get_samples(self, label: str) -> list[str]:
        """Return list of sample file paths for a label."""
        label_dir = _SAMPLE_DIR / label
        if not label_dir.exists():
            return []
        return [str(p.relative_to(_DATA_DIR)) for p in sorted(label_dir.glob('*.wav'))]
