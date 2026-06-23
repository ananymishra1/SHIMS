"""Tests for the Hugging Face / local OpenAI-compatible provider route."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared import agent_loop


class _FakeAsyncClient:
    """Minimal async httpx client mock that supports `async with ... as client`."""

    def __init__(self, response: MagicMock):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        self.last_post_args = args
        self.last_post_kwargs = kwargs
        return self._response


@pytest.fixture
def hf_settings(monkeypatch):
    """Point the HF provider at a fake local endpoint."""
    fake_settings = SimpleNamespace(
        huggingface_base_url="http://127.0.0.1:9999",
        huggingface_model="meta-llama/Llama-3.1-8B-Instruct",
        huggingface_api_key="test-token",
    )
    monkeypatch.setattr(agent_loop, "settings", fake_settings)


def _make_response(data: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = data
    response.raise_for_status = MagicMock()
    return response


class TestHuggingFaceProvider:
    def test_hf_chat_raw_posts_to_configured_endpoint(self, hf_settings):
        async def _run():
            response_data = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello from the local HF endpoint!",
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
            fake_client = _FakeAsyncClient(_make_response(response_data))

            with patch("shared.agent_loop.httpx.AsyncClient", side_effect=lambda **kw: fake_client):
                result = await agent_loop._hf_chat_raw(
                    model="meta-llama/Llama-3.1-8B-Instruct",
                    messages=[{"role": "user", "content": "hi"}],
                    tools=[],
                    timeout=30.0,
                )

            args = fake_client.last_post_args
            kwargs = fake_client.last_post_kwargs
            assert args[0] == "http://127.0.0.1:9999/v1/chat/completions"

            payload = kwargs["json"]
            assert payload["model"] == "meta-llama/Llama-3.1-8B-Instruct"
            assert payload["messages"] == [{"role": "user", "content": "hi"}]
            assert payload["stream"] is False

            headers = kwargs["headers"]
            assert headers["Authorization"] == "Bearer test-token"
            assert headers["Content-Type"] == "application/json"

            assert result["content"] == "Hello from the local HF endpoint!"
            assert result["tool_calls"] == []

        asyncio.run(_run())

    def test_hf_chat_raw_parses_tool_calls(self, hf_settings):
        async def _run():
            response_data = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "memory.save",
                                        "arguments": json.dumps({"key": "name", "value": "Ada"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
            fake_client = _FakeAsyncClient(_make_response(response_data))

            with patch("shared.agent_loop.httpx.AsyncClient", side_effect=lambda **kw: fake_client):
                result = await agent_loop._hf_chat_raw(
                    model="Qwen/Qwen2.5-7B-Instruct",
                    messages=[{"role": "user", "content": "remember my name is Ada"}],
                    tools=[],
                    timeout=30.0,
                )

            assert result["content"] == ""
            assert len(result["tool_calls"]) == 1
            tc = result["tool_calls"][0]
            assert tc["function"]["name"] == "memory.save"
            assert tc["function"]["arguments"] == {"key": "name", "value": "Ada"}

        asyncio.run(_run())

    def test_hf_chat_raw_no_auth_when_key_empty(self, monkeypatch):
        fake_settings = SimpleNamespace(
            huggingface_base_url="http://127.0.0.1:7777",
            huggingface_model="phi",
            huggingface_api_key="",
        )
        monkeypatch.setattr(agent_loop, "settings", fake_settings)

        async def _run():
            response_data = {"choices": [{"message": {"content": "Hi"}}]}
            fake_client = _FakeAsyncClient(_make_response(response_data))

            with patch("shared.agent_loop.httpx.AsyncClient", side_effect=lambda **kw: fake_client):
                await agent_loop._hf_chat_raw(
                    model="phi",
                    messages=[{"role": "user", "content": "hi"}],
                    tools=[],
                    timeout=10.0,
                )

            headers = fake_client.last_post_kwargs["headers"]
            assert "Authorization" not in headers

        asyncio.run(_run())
