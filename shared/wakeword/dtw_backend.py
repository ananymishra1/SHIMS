"""DTW-based custom wake word detection using MFCC templates."""
import json
import os
import pathlib
from typing import Optional

import numpy as np
from scipy.spatial.distance import cdist

from .utils import extract_mfcc, preprocess_audio

_DATA_DIR = pathlib.Path(__file__).resolve().parents[2] / 'data' / 'wakeword'
_TEMPLATE_DIR = _DATA_DIR / 'templates'
_CONFIG_PATH = _DATA_DIR / 'dtw_config.json'


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        return json.loads(_CONFIG_PATH.read_text(encoding='utf-8'))
    return {}


def _save_config(cfg: dict) -> None:
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding='utf-8')


def _dtw_distance(x: np.ndarray, y: np.ndarray) -> float:
    """Compute DTW distance between two MFCC sequences."""
    # x: (n, d), y: (m, d)
    dist = cdist(x, y, metric='euclidean')
    n, m = dist.shape
    dtw = np.full((n + 1, m + 1), np.inf)
    dtw[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = dist[i - 1, j - 1]
            dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])
    return dtw[n, m]


class DTWBackend:
    """Custom wake word detection via Dynamic Time Warping on MFCC features."""

    def __init__(self, threshold: float = 35.0, sensitivity: float = 0.5):
        self.threshold = threshold
        self.sensitivity = max(0.0, min(1.0, sensitivity))
        self.templates: dict[str, list[np.ndarray]] = {}
        self._load_templates()

    def _load_templates(self) -> None:
        cfg = _load_config()
        for label, info in cfg.items():
            paths = info.get('paths', [])
            self.templates[label] = []
            for p in paths:
                full = _TEMPLATE_DIR / p
                if full.exists():
                    data = np.load(str(full))
                    self.templates[label].append(data['mfcc'])

    def enroll(self, label: str, audio_bytes: bytes) -> bool:
        """Add a new template sample for a wake word label."""
        pcm = preprocess_audio(audio_bytes, use_vad=True)
        if pcm is None or len(pcm) < 4800:  # 300ms @ 16kHz
            return False
        mfcc = extract_mfcc((pcm * 32767).astype(np.int16))
        _TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        idx = len(self.templates.get(label, []))
        filename = f'{label}_{idx}.npz'
        path = _TEMPLATE_DIR / filename
        np.savez(str(path), mfcc=mfcc, label=label)
        if label not in self.templates:
            self.templates[label] = []
        self.templates[label].append(mfcc)
        cfg = _load_config()
        if label not in cfg:
            cfg[label] = {'paths': []}
        cfg[label]['paths'].append(filename)
        _save_config(cfg)
        return True

    def delete(self, label: str) -> bool:
        """Remove all templates for a label."""
        cfg = _load_config()
        if label not in cfg:
            return False
        for p in cfg[label].get('paths', []):
            try:
                (_TEMPLATE_DIR / p).unlink()
            except FileNotFoundError:
                pass
        del cfg[label]
        _save_config(cfg)
        self.templates.pop(label, None)
        return True

    def detect(self, audio_bytes: bytes) -> Optional[dict]:
        """Detect wake word in audio. Returns {'label': str, 'score': float, 'confidence': float} or None."""
        if not self.templates:
            return None
        pcm = preprocess_audio(audio_bytes, use_vad=True)
        if pcm is None:
            return None
        mfcc = extract_mfcc((pcm * 32767).astype(np.int16))
        best_label = None
        best_score = float('inf')
        for label, templates in self.templates.items():
            for tmpl in templates:
                d = _dtw_distance(mfcc, tmpl)
                if d < best_score:
                    best_score = d
                    best_label = label
        if best_label is None:
            return None
        # Normalize score to confidence 0..1 based on threshold
        # Lower distance = higher confidence
        adaptive_threshold = self.threshold * (1.5 - self.sensitivity)
        confidence = max(0.0, min(1.0, 1.0 - (best_score / adaptive_threshold)))
        if best_score > adaptive_threshold:
            return None
        return {
            'label': best_label,
            'score': float(best_score),
            'confidence': float(confidence),
            'backend': 'dtw'
        }

    def list_wake_words(self) -> list[str]:
        return list(self.templates.keys())

    def status(self) -> dict:
        cfg = _load_config()
        return {
            'backend': 'dtw',
            'wake_words': self.list_wake_words(),
            'template_count': {k: len(v) for k, v in self.templates.items()},
            'threshold': self.threshold,
            'sensitivity': self.sensitivity,
        }
