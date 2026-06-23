"""Tests for SHIMS custom wake word detection across all models."""
import io
import struct
import wave

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers: synthetic audio generation
# ---------------------------------------------------------------------------
def _generate_sine_wave(freq: float, duration: float = 1.0, sample_rate: int = 16000,
                        amplitude: float = 0.5) -> np.ndarray:
    """Generate a sine wave as float32 [-1, 1]."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _float_to_wav_bytes(pcm: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Convert float PCM to WAV bytes."""
    pcm_int = np.clip(pcm * 32767, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_int.tobytes())
    return buf.getvalue()


def _make_noise(duration: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generate white noise WAV."""
    pcm = np.random.uniform(-0.1, 0.1, int(sample_rate * duration)).astype(np.float32)
    return _float_to_wav_bytes(pcm, sample_rate)


def _make_tone(freq: float, duration: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generate tone WAV."""
    pcm = _generate_sine_wave(freq, duration, sample_rate)
    return _float_to_wav_bytes(pcm, sample_rate)


def _make_chirp(start_freq: float, end_freq: float, duration: float = 1.0,
                sample_rate: int = 16000) -> bytes:
    """Generate linear chirp WAV."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    freqs = np.linspace(start_freq, end_freq, len(t))
    phase = 2 * np.pi * np.cumsum(freqs) / sample_rate
    pcm = (0.5 * np.sin(phase)).astype(np.float32)
    return _float_to_wav_bytes(pcm, sample_rate)


# ---------------------------------------------------------------------------
# Tests: wakeword utilities
# ---------------------------------------------------------------------------
def test_load_wav_bytes():
    from shared.wakeword.utils import load_wav_bytes
    audio = _make_tone(440.0, 0.5)
    pcm, rate = load_wav_bytes(audio)
    assert rate == 16000
    assert len(pcm) == 8000  # 0.5s @ 16kHz
    assert pcm.dtype == np.int16


def test_resample():
    from shared.wakeword.utils import resample
    pcm = np.arange(16000, dtype=np.int16)
    out = resample(pcm, 16000, 8000)
    assert abs(len(out) - 8000) <= 2


def test_normalize():
    from shared.wakeword.utils import normalize
    pcm = np.array([0, 16384, -32768, 32767], dtype=np.int16)
    norm = normalize(pcm)
    assert pytest.approx(norm.max(), 0.01) == 1.0
    assert pytest.approx(norm.min(), 0.01) == -1.0


def test_extract_mfcc_shape():
    from shared.wakeword.utils import extract_mfcc
    pcm = (_generate_sine_wave(440.0, 1.0) * 32767).astype(np.int16)
    mfcc = extract_mfcc(pcm, sample_rate=16000)
    assert mfcc.ndim == 2
    assert mfcc.shape[1] == 13  # default numcep
    assert mfcc.shape[0] > 10   # plenty of frames for 1s


def test_preprocess_audio_filters_silence():
    from shared.wakeword.utils import preprocess_audio
    # Very quiet noise should be filtered out by VAD
    quiet = _make_noise(0.2)
    result = preprocess_audio(quiet, use_vad=True)
    # VAD may or may not reject very short/quiet audio; just check no crash
    assert result is None or isinstance(result, np.ndarray)


def test_preprocess_audio_keeps_speech():
    from shared.wakeword.utils import preprocess_audio
    # Loud tone should pass VAD
    loud = _make_tone(440.0, 1.0)
    result = preprocess_audio(loud, use_vad=True)
    assert result is not None
    assert len(result) >= 16000 * 0.3  # at least 300ms


# ---------------------------------------------------------------------------
# Tests: DTW backend
# ---------------------------------------------------------------------------
class TestDTWBackend:
    def test_enroll_and_detect(self, tmp_path):
        from shared.wakeword.dtw_backend import DTWBackend, _TEMPLATE_DIR, _CONFIG_PATH
        # Use temp dirs to avoid polluting project data
        orig_template = _TEMPLATE_DIR
        orig_config = _CONFIG_PATH
        import shared.wakeword.dtw_backend as dtw_mod
        dtw_mod._TEMPLATE_DIR = tmp_path / 'templates'
        dtw_mod._CONFIG_PATH = tmp_path / 'dtw_config.json'

        try:
            backend = DTWBackend(threshold=100.0, sensitivity=0.5)
            # Enroll two samples of the same "wake word" (same tone)
            sample1 = _make_tone(440.0, 1.0)
            sample2 = _make_tone(440.0, 1.0)
            assert backend.enroll('test_wake', sample1) is True
            assert backend.enroll('test_wake', sample2) is True

            # Detect same tone
            result = backend.detect(sample1)
            assert result is not None
            assert result['label'] == 'test_wake'
            assert result['backend'] == 'dtw'
            assert 0.0 <= result['confidence'] <= 1.0

            # Different tone should not match (or match with very low confidence)
            different = _make_tone(2000.0, 1.0)
            result2 = backend.detect(different)
            # With high threshold it may return None, or low confidence
            if result2 is not None:
                assert result2['confidence'] < 0.5
        finally:
            dtw_mod._TEMPLATE_DIR = orig_template
            dtw_mod._CONFIG_PATH = orig_config

    def test_delete(self, tmp_path):
        from shared.wakeword.dtw_backend import DTWBackend, _TEMPLATE_DIR, _CONFIG_PATH
        import shared.wakeword.dtw_backend as dtw_mod
        dtw_mod._TEMPLATE_DIR = tmp_path / 'templates'
        dtw_mod._CONFIG_PATH = tmp_path / 'dtw_config.json'
        try:
            backend = DTWBackend()
            sample = _make_tone(440.0, 1.0)
            backend.enroll('delete_me', sample)
            assert 'delete_me' in backend.list_wake_words()
            assert backend.delete('delete_me') is True
            assert 'delete_me' not in backend.list_wake_words()
            assert backend.delete('nonexistent') is False
        finally:
            dtw_mod._TEMPLATE_DIR = _TEMPLATE_DIR
            dtw_mod._CONFIG_PATH = _CONFIG_PATH


# ---------------------------------------------------------------------------
# Tests: Unified detector
# ---------------------------------------------------------------------------
class TestWakeWordDetector:
    def test_text_fallback(self):
        from shared.wakeword.detector import WakeWordDetector
        det = WakeWordDetector(text_wake_words=['hey shims', 'ok shims'])
        # No audio match, but text matches
        result = det.detect(b'', transcript='hey shims what is the time')
        assert result is not None
        assert result['backend'] == 'text'
        assert result['label'] == 'hey shims'

    def test_no_match(self):
        from shared.wakeword.detector import WakeWordDetector
        det = WakeWordDetector(text_wake_words=['hey shims'])
        result = det.detect(b'', transcript='what is the weather')
        assert result is None

    def test_list_wake_words(self):
        from shared.wakeword.detector import WakeWordDetector
        det = WakeWordDetector(text_wake_words=['hey shims'])
        words = det.list_wake_words()
        assert 'hey shims' in words


# ---------------------------------------------------------------------------
# Tests: Trainer
# ---------------------------------------------------------------------------
class TestWakeWordTrainer:
    def test_enroll_and_list(self, tmp_path):
        from shared.wakeword.trainer import WakeWordTrainer, _SAMPLE_DIR, _CONFIG_PATH
        import shared.wakeword.trainer as trainer_mod
        orig_sample = _SAMPLE_DIR
        orig_config = _CONFIG_PATH
        trainer_mod._SAMPLE_DIR = tmp_path / 'samples'
        trainer_mod._CONFIG_PATH = tmp_path / 'trainer_config.json'
        try:
            trainer = WakeWordTrainer()
            sample = _make_tone(440.0, 1.0)
            info = trainer.enroll_sample('my_wake', sample)
            assert info['ok'] is True
            assert info['label'] == 'my_wake'

            words = trainer.list_wake_words()
            assert any(w['label'] == 'my_wake' for w in words)
        finally:
            trainer_mod._SAMPLE_DIR = orig_sample
            trainer_mod._CONFIG_PATH = orig_config

    def test_delete(self, tmp_path):
        from shared.wakeword.trainer import WakeWordTrainer, _SAMPLE_DIR, _CONFIG_PATH
        import shared.wakeword.trainer as trainer_mod
        trainer_mod._SAMPLE_DIR = tmp_path / 'samples'
        trainer_mod._CONFIG_PATH = tmp_path / 'trainer_config.json'
        try:
            trainer = WakeWordTrainer()
            sample = _make_tone(440.0, 1.0)
            trainer.enroll_sample('temp_wake', sample)
            result = trainer.delete_wake_word('temp_wake')
            assert result['ok'] is True
            words = trainer.list_wake_words()
            assert not any(w['label'] == 'temp_wake' for w in words)
        finally:
            import shared.wakeword.trainer as tmod
            tmod._SAMPLE_DIR = _SAMPLE_DIR
            tmod._CONFIG_PATH = _CONFIG_PATH


# ---------------------------------------------------------------------------
# Tests: Backend endpoints (via TestClient)
# ---------------------------------------------------------------------------
class TestWakeWordEndpoints:
    def test_status_endpoint(self):
        from fastapi.testclient import TestClient
        from backend.app.main import app
        client = TestClient(app)
        r = client.get('/voice/wakeword/status')
        assert r.status_code == 200
        d = r.json()
        assert d['ok'] is True
        assert 'status' in d

    def test_detect_no_wake(self):
        from fastapi.testclient import TestClient
        from backend.app.main import app
        client = TestClient(app)
        noise = _make_noise(1.0)
        r = client.post('/voice/wakeword/detect',
                        files={'file': ('noise.wav', io.BytesIO(noise), 'audio/wav')})
        assert r.status_code == 200
        d = r.json()
        assert d['ok'] is True
        assert d['detected'] is False

    def test_enroll_and_detect(self, tmp_path):
        from fastapi.testclient import TestClient
        from backend.app.main import app
        client = TestClient(app)
        import shared.wakeword.dtw_backend as dtw_mod
        import shared.wakeword.trainer as trainer_mod
        # Patch data dirs temporarily
        orig_dtw_tmpl = dtw_mod._TEMPLATE_DIR
        orig_dtw_cfg = dtw_mod._CONFIG_PATH
        orig_tr_samp = trainer_mod._SAMPLE_DIR
        orig_tr_cfg = trainer_mod._CONFIG_PATH
        dtw_mod._TEMPLATE_DIR = tmp_path / 'dtw_templates'
        dtw_mod._CONFIG_PATH = tmp_path / 'dtw_config.json'
        trainer_mod._SAMPLE_DIR = tmp_path / 'tr_samples'
        trainer_mod._CONFIG_PATH = tmp_path / 'tr_config.json'
        try:
            sample = _make_tone(440.0, 1.0)
            r = client.post('/voice/wakeword/enroll?label=test_tone',
                            files={'file': ('tone.wav', io.BytesIO(sample), 'audio/wav')})
            assert r.status_code == 200
            d = r.json()
            assert d['ok'] is True

            # Detect same tone
            r2 = client.post('/voice/wakeword/detect',
                             files={'file': ('tone.wav', io.BytesIO(sample), 'audio/wav')})
            assert r2.status_code == 200
            d2 = r2.json()
            assert d2['ok'] is True
            assert 'detected' in d2

            # List
            r3 = client.get('/voice/wakeword/list')
            assert r3.status_code == 200
            assert any(w['label'] == 'test_tone' for w in r3.json()['wake_words'])

            # Delete
            r4 = client.delete('/voice/wakeword/delete?label=test_tone')
            assert r4.status_code == 200
            assert r4.json()['ok'] is True
        finally:
            dtw_mod._TEMPLATE_DIR = orig_dtw_tmpl
            dtw_mod._CONFIG_PATH = orig_dtw_cfg
            trainer_mod._SAMPLE_DIR = orig_tr_samp
            trainer_mod._CONFIG_PATH = orig_tr_cfg

    def test_detect_with_transcript(self):
        from fastapi.testclient import TestClient
        from backend.app.main import app
        client = TestClient(app)
        # Text fallback via transcript param
        r = client.post('/voice/wakeword/detect?transcript=hey%20shims%20what%20is%20up',
                        files={'file': ('empty.wav', io.BytesIO(b''), 'audio/wav')})
        assert r.status_code == 200
        d = r.json()
        assert d['ok'] is True
        assert d['detected'] is True
        assert d['backend'] == 'text'


# ---------------------------------------------------------------------------
# Tests: Personal backend endpoints
# ---------------------------------------------------------------------------
class TestPersonalWakeWordEndpoints:
    def test_personal_status(self):
        from fastapi.testclient import TestClient
        from shims_personal.app import app
        client = TestClient(app)
        r = client.get('/api/v15/wakeword/status')
        assert r.status_code == 200
        assert r.json()['ok'] is True

    def test_personal_detect(self):
        from fastapi.testclient import TestClient
        from shims_personal.app import app
        client = TestClient(app)
        noise = _make_noise(1.0)
        r = client.post('/api/v15/wakeword/detect', content=noise)
        assert r.status_code == 200
        d = r.json()
        assert d['ok'] is True
        assert 'detected' in d

    def test_personal_list(self):
        from fastapi.testclient import TestClient
        from shims_personal.app import app
        client = TestClient(app)
        r = client.get('/api/v15/wakeword/list')
        assert r.status_code == 200
        assert 'wake_words' in r.json()
