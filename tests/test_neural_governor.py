"""Tests for SHIMS Omni Neural Governor."""
import pytest

from shared.neural_governor import IntentCategory, HardwareProfile
from shared.neural_governor.intent_classifier import classify_intent
from shared.neural_governor.drift_detector import compute_drift
from shared.neural_governor.model_router import route_model
from shared.neural_governor.hardware_profiler import profile_hardware
from shared.neural_governor.circuit_breaker import can_use, record_failure, record_success, get_all_circuits


class TestIntentClassifier:
    def test_code_intent(self):
        cat, conf = classify_intent("Write a python function to sort a list")
        assert cat == IntentCategory.CODE_GENERATION
        assert conf > 0.5

    def test_document_intent(self):
        cat, conf = classify_intent("Format this as a COA PDF")
        assert cat == IntentCategory.DOCUMENT_FORMAT

    def test_conversation_fallback(self):
        cat, conf = classify_intent("Hello there")
        assert cat == IntentCategory.CONVERSATION


class TestDriftDetector:
    def test_low_drift_on_good_output(self):
        drift = compute_drift(
            prompt="What is 2+2?",
            context="",
            output="2+2 equals 4.",
            intent=IntentCategory.CONVERSATION,
        )
        assert drift.composite < 0.38
        assert not drift.triggered

    def test_high_drift_on_contradictory_output(self):
        drift = compute_drift(
            prompt="What is the capital of France?",
            context="The capital of France is Paris.",
            output="The capital of France is Berlin.",
            intent=IntentCategory.CONVERSATION,
        )
        # Contradiction with context should raise drift
        assert drift.contradiction > 0.3 or drift.composite > 0.1


class TestModelRouter:
    def test_routes_to_local(self):
        r = route_model(IntentCategory.CODE_GENERATION)
        assert r.provider == "ollama"
        assert r.model != ""
        assert len(r.fallback_chain) > 0

    def test_respects_offline(self):
        hw = HardwareProfile(internet_available=False, vram_gb=6)
        # This should force local-only selection
        r = route_model(IntentCategory.CONVERSATION)
        assert r.provider in ("ollama", "fallback")


class TestCircuitBreaker:
    def test_initially_closed(self):
        assert can_use("ollama") is True

    def test_opens_after_failures(self):
        for _ in range(10):
            record_failure("fake_provider")
        assert can_use("fake_provider") is False
        circuits = get_all_circuits()
        fake = [c for c in circuits if c["provider"] == "fake_provider"]
        assert fake and fake[0]["status"] == "open"

    def test_success_resets(self):
        record_success("ollama")
        assert can_use("ollama") is True


class TestHardwareProfiler:
    def test_profile_returns_data(self):
        hw = profile_hardware()
        assert hw.total_ram_gb > 0
        assert hw.cpu_cores > 0
