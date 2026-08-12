"""Native-only routing + trivial-turn normalization tests.

Covers:
- `_is_simple_query` punctuation normalization (and that substantive short
  turns keep the full lane).
- `_resolve_provider_model`: every local provider alias resolves to the
  native engine — Ollama/LM Studio/etc. are never probed or returned.
- `_provider_ready_for_llm` understands the native engine.
"""
from __future__ import annotations

import asyncio

import pytest

import backend.app.main as main


# --------------------------------------------------------------------------- #
# _is_simple_query normalization
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("message", [
    "Hello?",
    "hello.",
    "thanks!",
    "Hi!!",
    "good morning.",
    "ok",
])
def test_simple_query_normalization_trivial(message):
    assert main._is_simple_query(message) is True


@pytest.mark.parametrize("message", [
    "?????",
    "website exists",
    "what does their pricing page say",
    "whyd you websearch?",
    # Short but substantive: may want tools — full lane. The RAG-noise fix is
    # the strong-hit gate in omni_brain, not a word-count shortcut here.
    "be useful",
])
def test_simple_query_keeps_substantive_turns_full_lane(message):
    assert main._is_simple_query(message) is False


# --------------------------------------------------------------------------- #
# _resolve_provider_model — native-only local routing
# --------------------------------------------------------------------------- #

@pytest.fixture
def native_state(monkeypatch):
    """Controlled native-engine state: one loaded model, two servable GGUFs."""
    state = {"loaded": "Qwen3.6-35B-A3B-Q8_0", "servable": {"Qwen3.6-35B-A3B-Q8_0", "Llama-3.1-8B-Instruct-Q4_K_M"}, "kicked": []}
    monkeypatch.setattr(main, "_native_loaded_model", lambda: state["loaded"])
    monkeypatch.setattr(main, "_native_can_serve", lambda m: bool(m) and m in state["servable"])
    monkeypatch.setattr(main, "_kick_native_load", lambda m="": state["kicked"].append(m))
    monkeypatch.delenv("SHIMS_CHAT_PROVIDER", raising=False)
    monkeypatch.delenv("SHIMS_CHAT_MODEL", raising=False)
    return state


def _resolve(provider, model=None, **kw):
    return asyncio.run(main._resolve_provider_model(provider, model, **kw))


def test_ollama_alias_resolves_to_native(native_state):
    provider, model, reason = _resolve("ollama", "qwen2.5:7b")
    assert provider == "native"
    assert model == "Qwen3.6-35B-A3B-Q8_0"


def test_auto_resolves_to_native(native_state):
    provider, model, reason = _resolve("auto", None)
    assert provider == "native"
    assert model == "Qwen3.6-35B-A3B-Q8_0"


def test_lmstudio_alias_never_probed(native_state):
    provider, model, reason = _resolve("lmstudio", "some-lm-model")
    assert provider == "native"
    assert model == "Qwen3.6-35B-A3B-Q8_0"


def test_explicit_native_gguf_pick_switches(native_state):
    provider, model, reason = _resolve("native", "Llama-3.1-8B-Instruct-Q4_K_M")
    assert provider == "native"
    assert model == "Llama-3.1-8B-Instruct-Q4_K_M"
    assert reason == "native-model-switching"
    assert native_state["kicked"] == ["Llama-3.1-8B-Instruct-Q4_K_M"]


def test_settings_pin_fills_missing_request_model(native_state, monkeypatch):
    monkeypatch.setenv("SHIMS_CHAT_MODEL", "Llama-3.1-8B-Instruct-Q4_K_M")
    provider, model, reason = _resolve("auto", None)
    assert provider == "native"
    assert model == "Llama-3.1-8B-Instruct-Q4_K_M"
    assert native_state["kicked"] == ["Llama-3.1-8B-Instruct-Q4_K_M"]


def test_settings_pin_beats_request_pick(native_state, monkeypatch):
    """Pin means pin: SHIMS_CHAT_MODEL (Settings) wins over whatever the
    frontend puts in req.model. Regression guard for the reported bug where a
    pinned 40B was silently overridden because the frontend cached a stale
    request-model. To use a different model per-turn, clear the pin first."""
    monkeypatch.setenv("SHIMS_CHAT_MODEL", "Qwen3.6-35B-A3B-Q8_0")
    provider, model, reason = _resolve("auto", "Llama-3.1-8B-Instruct-Q4_K_M")
    assert provider == "native"
    assert model == "Qwen3.6-35B-A3B-Q8_0"


def test_engine_down_returns_native_loading(native_state):
    native_state["loaded"] = ""
    provider, model, reason = _resolve("auto", None)
    assert provider == "native"
    assert reason == "native-loading"
    assert native_state["kicked"] == [""]


def test_explicit_cloud_provider_still_honored(native_state, monkeypatch):
    monkeypatch.setattr(main, "_provider_configured", lambda p: p == "anthropic")
    provider, model, reason = _resolve("anthropic", None)
    assert provider == "anthropic"
    assert reason == "explicit-cloud-provider"


def test_unconfigured_cloud_falls_back_to_native(native_state, monkeypatch):
    monkeypatch.setattr(main, "_provider_configured", lambda p: False)
    provider, model, reason = _resolve("anthropic", None)
    assert provider == "native"


# --------------------------------------------------------------------------- #
# _provider_ready_for_llm — native support
# --------------------------------------------------------------------------- #

def test_provider_ready_native_loaded(native_state):
    assert asyncio.run(main._provider_ready_for_llm("native", "")) is True


def test_provider_ready_native_engine_down(native_state, monkeypatch):
    native_state["loaded"] = ""
    import shared.native_engine as ne
    monkeypatch.setattr(ne, "get_engine", lambda: type("E", (), {"health": lambda self: {"ready": False}})())
    assert asyncio.run(main._provider_ready_for_llm("native", "")) is False
