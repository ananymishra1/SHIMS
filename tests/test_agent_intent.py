"""Tests for shared/agent_intent.py."""
from __future__ import annotations

import pytest

from shared.agent_intent import classify_intent, classify_keywords


def test_classify_keywords_research() -> None:
    assert classify_keywords("Find recent patents on solid state batteries") == "research"
    assert classify_keywords("What is the latest news on AI?") == "research"


def test_classify_keywords_automation() -> None:
    assert classify_keywords("Run a python script to calculate moving averages") == "automation"
    assert classify_keywords("Install docker and build the project") == "automation"


def test_classify_keywords_hybrid() -> None:
    assert classify_keywords("Find recent papers on LLMs and write a summary script") == "hybrid"
    assert classify_keywords("Research competitors and then build a comparison csv") == "hybrid"


def test_classify_keywords_conversation() -> None:
    assert classify_keywords("Hello, how are you?") == "conversation"
    assert classify_keywords("Tell me a joke") == "conversation"


@pytest.mark.asyncio
async def test_classify_intent_uses_keywords() -> None:
    result = await classify_intent("Write a Python function to sort a list")
    assert result == "automation"


@pytest.mark.asyncio
async def test_classify_intent_llm_fallback() -> None:
    async def fake_chat(messages: list) -> dict:
        return {"content": "research"}

    result = await classify_intent("Something about quantum computing", chat_fn=fake_chat)
    assert result == "research"


@pytest.mark.asyncio
async def test_classify_intent_llm_malformed() -> None:
    async def fake_chat(messages: list) -> dict:
        return {"content": "I think this is automation."}

    result = await classify_intent("run a script", chat_fn=fake_chat)
    # keywords should win for clear automation
    assert result == "automation"


@pytest.mark.asyncio
async def test_classify_intent_no_llm() -> None:
    result = await classify_intent("What is AI?", use_llm=False)
    assert result == "research"
