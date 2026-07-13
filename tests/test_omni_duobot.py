from __future__ import annotations

import pytest

from shared.omni_duobot import (
    _is_duplicate,
    _domain_profile,
    _local_model_for,
    _similarity,
    _system_prompt,
    create_conversation,
    get_conversation,
    add_message,
    set_mode,
)


def test_create_conversation_defaults_to_free(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.omni_duobot.CONVERSATIONS_PATH", tmp_path / "conv.jsonl")
    result = create_conversation()
    assert result["ok"] is True
    assert result["conversation"]["mode"] == "free"


def test_create_conversation_improvement_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.omni_duobot.CONVERSATIONS_PATH", tmp_path / "conv.jsonl")
    result = create_conversation(topic="peer bridge", mode="improvement")
    assert result["conversation"]["mode"] == "improvement"


def test_set_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.omni_duobot.CONVERSATIONS_PATH", tmp_path / "conv.jsonl")
    cid = create_conversation()["conversation"]["id"]
    result = set_mode(cid, "improvement")
    assert result["ok"] is True
    assert get_conversation(cid)["mode"] == "improvement"


def test_similarity_and_duplicate_detection():
    a = "Implement a caching layer to reduce latency."
    b = "Implement a caching layer to reduce latency."
    c = "Implement a caching layer to reduce the overall latency."
    assert _similarity(a, b) == 1.0
    assert _similarity(a, c) > 0.72

    conv = {"messages": [{"role": "primary", "content": a}]}
    assert _is_duplicate(conv, a, "primary") is True
    assert _is_duplicate(conv, c, "primary") is True  # high overlap
    assert _is_duplicate(conv, "A completely unrelated sentence about cats.", "primary") is False


def test_add_message_and_duplicate_conv(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.omni_duobot.CONVERSATIONS_PATH", tmp_path / "conv.jsonl")
    cid = create_conversation()["conversation"]["id"]
    r1 = add_message(cid, "primary", "hello")
    assert r1["ok"] is True
    r2 = add_message(cid, "primary", "hello")
    assert r2["ok"] is True  # add_message itself does not block duplicates; run_turn does


def test_domain_profile_routes_industrial_chemistry():
    conv = {
        "mode": "improvement",
        "topic": "industrial manufacturing chemistry latency",
        "messages": [
            {"role": "user", "content": "Discuss BMR route scale-up, solvent recovery, impurity risk, and voice latency."}
        ],
    }
    domain = _domain_profile(conv)
    assert domain["chemistry"] is True
    assert domain["manufacturing"] is True
    assert domain["latency"] is True
    assert "industrial manufacturing" in domain["focus"]
    assert "chemistry" in domain["focus"]


def test_local_model_selection_prefers_specialists(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.omni_duobot.SETTINGS_PATH", tmp_path / "duosettings.json")
    monkeypatch.setenv("SHIMS_FACTORY_CHEMISTRY_MODEL", "chemdfm")
    monkeypatch.setenv("SHIMS_FACTORY_HEAVY_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("SHIMS_FACTORY_DEFAULT_MODEL", "qwen2.5:3b")
    assert _local_model_for({"chemistry": True, "latency": False}) == ("chemdfm", "chemistry")
    assert _local_model_for({"manufacturing": True}) == ("qwen2.5:7b", "heavy")
    assert _local_model_for({"latency": True}) == ("qwen2.5:3b", "fast")
    monkeypatch.delenv("SHIMS_FACTORY_CHEMISTRY_MODEL", raising=False)
    assert _local_model_for(
        {"chemistry": True, "latency": False},
        capabilities={"local": {"capabilities": {"chemistry": False}, "role_models": {"heavy": "qwen2.5:7b"}}},
    ) == ("qwen2.5:7b", "heavy")


def test_system_prompt_includes_capabilities_and_domain():
    prompt = _system_prompt(
        "primary",
        "improvement",
        domain={"focus": ["industrial manufacturing", "chemistry"], "manufacturing": True, "chemistry": True},
        capabilities={
            "primary": {"provider": "kimi", "model": "kimi-k2.6"},
            "local": {"capabilities": {"chemistry": True, "heavy_reasoning": True}, "role_models": {"chemistry": "chemdfm"}},
        },
    )
    assert "industrial manufacturing" in prompt
    assert "ChemDFM" in prompt
    assert "kimi/kimi-k2.6" in prompt
