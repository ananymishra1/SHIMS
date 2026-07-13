from __future__ import annotations

import asyncio
import json

import pytest

from backend.app.main import _extract_durable_facts_llm


@pytest.fixture
def patch_ollama_chat(monkeypatch):
    def _patch(response_text: str):
        async def _fake(*args, **kwargs):
            return response_text

        monkeypatch.setattr("backend.app.main._ollama_chat", _fake)

    return _patch


def test_extract_durable_facts_llm_filters_and_normalizes(patch_ollama_chat):
    patch_ollama_chat(
        json.dumps(
            [
                {"fact": "User prefers dark mode", "tags": ["preference", "user"]},
                {"fact": "User works at SHIMS Labs", "tags": ["user", "work"]},
                {"fact": "Goal: build futuristic brain", "tags": ["goal", "project"]},
                {"fact": "ok", "tags": ["user"]},
            ]
        )
    )
    user = "I prefer dark mode for the interface."
    assistant = "I have noted your preference for dark mode. I will apply it to the UI."
    facts = asyncio.run(_extract_durable_facts_llm(user, assistant))
    assert len(facts) == 3
    texts = {f[0] for f in facts}
    assert "User prefers dark mode" in texts
    assert "User works at SHIMS Labs" in texts
    assert "Goal: build futuristic brain" in texts
    allowed = {"preference", "user", "goal", "project", "tool_result", "code", "plan", "assistant_note"}
    for _, tags in facts:
        assert all(t in allowed for t in tags)


def test_extract_durable_facts_llm_returns_empty_for_short_input():
    assert asyncio.run(_extract_durable_facts_llm("hi", "hello")) == []


def test_extract_durable_facts_llm_handles_non_json(patch_ollama_chat):
    patch_ollama_chat("not json")
    user = "I am a software developer working on AI agents."
    assistant = "Understood, you build AI agents. I will remember that."
    assert asyncio.run(_extract_durable_facts_llm(user, assistant)) == []


def test_extract_durable_facts_llm_squashes_non_list(patch_ollama_chat):
    patch_ollama_chat(json.dumps({"fact": "bad shape"}))
    user = "I am a software developer working on AI agents."
    assistant = "Understood, you build AI agents. I will remember that."
    assert asyncio.run(_extract_durable_facts_llm(user, assistant)) == []


def test_extract_durable_facts_llm_ignores_empty_facts(patch_ollama_chat):
    patch_ollama_chat(json.dumps([{"fact": "", "tags": ["user"]}, {"fact": "   ", "tags": ["user"]}]))
    user = "I am a software developer working on AI agents."
    assistant = "Understood, you build AI agents. I will remember that."
    assert asyncio.run(_extract_durable_facts_llm(user, assistant)) == []
