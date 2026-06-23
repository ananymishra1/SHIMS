from __future__ import annotations

import os

import pytest

from shared.model_capabilities import is_tool_capable, filter_tool_capable, mark_tool_capable


@pytest.mark.parametrize("name,expected", [
    ("qwen2.5:7b", True),
    ("qwen2.5-coder:14b", True),
    ("llama3.1:latest", True),
    ("llama3.2:latest", True),
    ("mistral-nemo", True),
    ("mistral-small:latest", True),
    ("claude-sonnet-4-6", True),
    ("gpt-4o-mini", True),
    ("gemini-2.5-flash", True),
    ("deepseek-chat", True),
    ("kimi-k2.6", True),
    ("glm-5.2", True),
    ("llama3.2:latest", True),
    ("gemma3:4b", False),
    ("gemma-4-12b-abliterated:latest", False),
    ("nomic-embed-text", False),
    ("moondream:latest", False),
    ("", False),
])
def test_is_tool_capable(name: str, expected: bool) -> None:
    assert is_tool_capable(name) is expected


def test_show_all_env_bypass() -> None:
    os.environ["SHIMS_SHOW_ALL_MODELS"] = "1"
    try:
        assert is_tool_capable("gemma3:4b") is True
    finally:
        os.environ.pop("SHIMS_SHOW_ALL_MODELS", None)


def test_mark_and_filter() -> None:
    models = [
        {"name": "qwen2.5:7b"},
        {"name": "gemma3:4b"},
        {"name": "llama3.2:latest"},
    ]
    marked = mark_tool_capable(models)
    assert marked[0]["tool_capable"] is True
    assert marked[1]["tool_capable"] is False
    assert marked[2]["tool_capable"] is True

    filtered = filter_tool_capable(models)
    assert [m["name"] for m in filtered] == ["qwen2.5:7b", "llama3.2:latest"]
