from __future__ import annotations

import os
from typing import Generator

import pytest

from shared import agent_model_router
from shared.agent_model_router import _parse_model_env, resolve_role, resolve_agent


@pytest.fixture(autouse=True)
def clean_env() -> Generator[None, None, None]:
    keys = [
        "SHIMS_ROUTER_MODEL",
        "SHIMS_FAST_MODEL",
        "SHIMS_SMART_MODEL",
        "SHIMS_CODER_MODEL",
        "SHIMS_CREATIVE_MODEL",
        "SHIMS_CHEMISTRY_MODEL",
        "SHIMS_RESEARCH_MODEL",
    ]
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_parse_model_env_prefix() -> None:
    assert _parse_model_env("openai:gpt-4o-mini") == ("openai", "gpt-4o-mini")
    assert _parse_model_env("ollama:qwen2.5-coder:14b") == ("ollama", "qwen2.5-coder:14b")
    assert _parse_model_env("huggingface:meta-llama/Llama-3.1-8B-Instruct") == ("huggingface", "meta-llama/Llama-3.1-8B-Instruct")


def test_resolve_role_from_env() -> None:
    os.environ["SHIMS_CODER_MODEL"] = "qwen2.5-coder:14b"
    provider, model, reason = resolve_role("coder")
    assert provider == "ollama"
    assert model == "qwen2.5-coder:14b"
    assert "SHIMS_CODER_MODEL" in reason


def test_resolve_role_cloud_prefix() -> None:
    os.environ["SHIMS_SMART_MODEL"] = "anthropic:claude-sonnet-4-6"
    provider, model, reason = resolve_role("smart")
    assert provider == "anthropic"
    assert model == "claude-sonnet-4-6"


def test_resolve_agent_code() -> None:
    os.environ["SHIMS_CODER_MODEL"] = "gpt-4o-mini"
    provider, model, reason = resolve_agent("code")
    assert provider == "openai"
    assert model == "gpt-4o-mini"


def test_resolve_agent_chemistry() -> None:
    os.environ["SHIMS_CHEMISTRY_MODEL"] = "chemdfm"
    provider, model, reason = resolve_agent("chemistry")
    assert provider == "chemdfm"
    assert model == "chemdfm"


def test_resolve_agent_defaults_when_no_env() -> None:
    provider, model, reason = resolve_agent("memory")
    assert provider in {"ollama", "openai", "anthropic", "gemini", "huggingface", "kimi", "deepseek", "qwen"}
    assert model
