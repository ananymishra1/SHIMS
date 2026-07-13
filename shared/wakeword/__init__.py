"""SHIMS Wake Word Detection — custom wake words across all models."""
from .detector import WakeWordDetector, get_detector
from .trainer import WakeWordTrainer

__all__ = ['WakeWordDetector', 'get_detector', 'WakeWordTrainer']
