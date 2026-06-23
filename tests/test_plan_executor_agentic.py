"""Tests for the agentic plan executor (Phase 4.2).

Covers:
- agent.run plan steps executing through the real wave-based agent loop
- direct tool-hint steps with retry/backoff on transient failures
- OpenAI and Google cloud raw transports used by the fallback chain

No local LLMs or cloud API keys are required; all external calls are mocked.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from shared import agent_loop
from shared import agent_tools
from shared import desktop_planner as dp
from shared import plan_executor as pe
from shared.config import Settings


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def disable_router_cold_start(monkeypatch):
    """Make the wave router think the small router model is already loaded."""
    async def _loaded(_model_name: str) -> bool:
        return True
    monkeypatch.setattr("shared.agent_loop._ollama_model_loaded", _loaded)


@pytest.fixture
def patch_llm_chat(monkeypatch):
    """Patch the shared LLM chat helper used by the wave engine."""
    def _patch(fn):
        monkeypatch.setattr("shared.agent_loop._llm_chat", fn)
    return _patch


class _FakeResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._data


def _fake_async_client(response_data: dict[str, Any]):
    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return _FakeResponse(response_data)

    return FakeAsyncClient


# ---------------------------------------------------------------------------
# agent.run via the wave engine
# ---------------------------------------------------------------------------
def test_agent_run_returns_final_answer(disable_router_cold_start, patch_llm_chat):
    async def _chat(*args, **kwargs):
        return (
            {"content": '{"reasoning":"direct","wave":[],"final":"Plan step complete: hello"}', "tool_calls": []},
            True,
            1.0,
            "",
        )

    patch_llm_chat(_chat)

    plan = dp.create_plan("greet", [
        {"step_id": "s1", "description": "Greet the user", "tool_hint": "agent.run"},
    ])
    result = pe.run_plan_wave(plan.plan_id)

    assert result["ok"] is True
    assert result["plan"]["status"] == "completed"
    step_result = result["plan"]["steps"][0]["result"]
    assert "Plan step complete: hello" in step_result["answer"]


def test_agent_run_can_drive_a_tool_wave(disable_router_cold_start, patch_llm_chat, monkeypatch):
    # Force Ollama provider so explicit plan generation is skipped and the
    # first mocked _llm_chat call is the wave router, not the planner.
    from shared.config import Settings
    monkeypatch.setattr("shared.plan_executor.settings", Settings(ai_provider="ollama"))
    call_count = 0

    async def _chat(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (
                {
                    "content": json.dumps({
                        "reasoning": "run echo",
                        "wave": [
                            {
                                "tool": "shell.run",
                                "args": {"command": "echo wave-ok"},
                                "purpose": "echo via wave",
                            },
                        ],
                        "final": None,
                    }),
                    "tool_calls": [],
                },
                True,
                1.0,
                "",
            )
        return (
            {"content": '{"reasoning":"done","wave":[],"final":"Echo completed"}', "tool_calls": []},
            True,
            1.0,
            "",
        )

    patch_llm_chat(_chat)

    plan = dp.create_plan("echo", [
        {"step_id": "s1", "description": "Echo via agent", "tool_hint": "agent.run"},
    ])
    result = pe.run_plan_wave(plan.plan_id)

    assert result["plan"]["status"] == "completed"
    step_result = result["plan"]["steps"][0]["result"]
    assert "Echo completed" in step_result["answer"]
    assert "shell.run" in step_result.get("tools_used", [])


# ---------------------------------------------------------------------------
# Retry with backoff for direct tool-hint steps
# ---------------------------------------------------------------------------
def test_direct_tool_step_retries_on_failure(monkeypatch):
    attempts: list[dict[str, Any]] = []

    def _fake_run_tool(name: str, args: dict[str, Any], allow_gated: bool = False, session_id: str = "") -> dict[str, Any]:
        attempts.append({"name": name, "args": args})
        if len(attempts) < 2:
            return {"ok": False, "error": "transient failure"}
        return {"ok": True, "stdout": "success"}

    monkeypatch.setattr(pe.agent_tools, "run_tool", _fake_run_tool)
    monkeypatch.setattr(pe.time, "sleep", lambda _seconds: None)

    plan = dp.create_plan("retry", [
        {"step_id": "s1", "description": "echo retry-test", "tool_hint": "shell.run"},
    ])
    result = pe.run_plan_wave(plan.plan_id)

    assert result["plan"]["status"] == "completed"
    step = dp.get_plan(plan.plan_id).steps[0]
    assert step.status == "done"
    assert step.result["ok"] is True
    assert step.result["stdout"] == "success"
    assert step.result["attempts"] == 2


# ---------------------------------------------------------------------------
# Cloud raw transports
# ---------------------------------------------------------------------------
def test_openai_chat_raw_parses_content_and_tool_calls(monkeypatch):
    monkeypatch.setattr(agent_loop, "settings", Settings(openai_api_key="sk-test"))
    response = {
        "choices": [
            {
                "message": {
                    "content": "Hello",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "shell_run",
                                "arguments": '{"command":"ls"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    monkeypatch.setattr("shared.agent_loop.httpx.AsyncClient", _fake_async_client(response))

    tools = [
        {
            "type": "function",
            "function": {
                "name": "shell.run",
                "description": "Run a shell command",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    result = asyncio.run(agent_loop._openai_chat_raw("gpt-4o", [{"role": "user", "content": "hi"}], tools))

    assert result["content"] == "Hello"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["function"]["name"] == "shell.run"
    assert result["tool_calls"][0]["function"]["arguments"] == {"command": "ls"}


def test_google_chat_raw_parses_content_and_tool_calls(monkeypatch):
    monkeypatch.setattr(agent_loop, "settings", Settings(google_api_key="g-test"))
    response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Hi"},
                        {
                            "functionCall": {
                                "name": "shell_run",
                                "args": {"command": "ls"},
                            }
                        },
                    ]
                }
            }
        ]
    }
    monkeypatch.setattr("shared.agent_loop.httpx.AsyncClient", _fake_async_client(response))

    tools = [
        {
            "type": "function",
            "function": {
                "name": "shell.run",
                "description": "Run a shell command",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    result = asyncio.run(agent_loop._google_chat_raw("gemini-2.5-flash", [{"role": "user", "content": "hi"}], tools))

    assert result["content"] == "Hi"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["function"]["name"] == "shell.run"
    assert result["tool_calls"][0]["function"]["arguments"] == {"command": "ls"}


def await_coro(coro):
    """Tiny helper to run a coroutine without forcing pytest-asyncio."""
    return asyncio.run(coro)


def test_openai_compatible_chat_raw_works_for_kimi(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "kimi-test")
    response = {
        "choices": [
            {
                "message": {
                    "content": "Kimi reply",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "shell_run",
                                "arguments": '{"command":"ls"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    monkeypatch.setattr("shared.agent_loop.httpx.AsyncClient", _fake_async_client(response))

    tools = [
        {
            "type": "function",
            "function": {
                "name": "shell.run",
                "description": "Run a shell command",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    result = await_coro(agent_loop._openai_compatible_chat_raw("kimi", "moonshot-v1-8k", [{"role": "user", "content": "hi"}], tools))

    assert result["content"] == "Kimi reply"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["function"]["name"] == "shell.run"
    assert result["tool_calls"][0]["function"]["arguments"] == {"command": "ls"}


def test_openai_compatible_converts_tool_role_messages(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "kimi-test")
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}

    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            captured["payload"] = kwargs.get("json") or {}
            return FakeResponse()

    monkeypatch.setattr("shared.agent_loop.httpx.AsyncClient", FakeClient)

    messages = [
        {"role": "system", "content": "you are a tool user"},
        {"role": "user", "content": "search"},
        {"role": "tool", "name": "web.search", "content": "results"},
    ]
    await_coro(agent_loop._openai_compatible_chat_raw("kimi", "moonshot-v1-8k", messages, []))

    payload_messages = captured["payload"]["messages"]
    assert payload_messages[0]["role"] == "system"
    assert payload_messages[1]["role"] == "user"
    assert payload_messages[2]["role"] == "user"
    assert "Tool result (web.search): results" in payload_messages[2]["content"]


def test_llm_chat_passes_empty_tools_for_kimi(monkeypatch):
    """Kimi agent-loop planning forces JSON wave mode by omitting native tool specs."""
    from shared import llm_gateway
    captured: dict[str, Any] = {}

    async def _fake_gateway_chat(provider, model, messages, tools, *, feature="agent", timeout=120.0, user_id=None):
        captured.update({"provider": provider, "tools": tools})
        return {"content": "{\"reasoning\":\"direct\",\"wave\":[],\"final\":\"ok\"}", "tool_calls": []}

    monkeypatch.setattr(llm_gateway, "GATEWAY_ENABLED", True)
    monkeypatch.setattr(llm_gateway.gateway, "chat_messages", _fake_gateway_chat)

    sample_tool = {"type": "function", "function": {"name": "shell.run"}}
    await_coro(agent_loop._llm_chat("kimi", "moonshot-v1-8k", [{"role": "user", "content": "hi"}], [sample_tool], timeout=5))
    assert captured["provider"] == "kimi"
    assert captured["tools"] == []

    # OpenAI should still receive tools
    await_coro(agent_loop._llm_chat("openai", "gpt-4o", [{"role": "user", "content": "hi"}], [sample_tool], timeout=5))
    assert captured["provider"] == "openai"
    assert captured["tools"] == [sample_tool]


def test_cloud_transports_return_key_error_when_unconfigured(monkeypatch):
    # Ensure no real key is used by these unit tests.
    monkeypatch.setattr(agent_loop, "settings", Settings(openai_api_key="", google_api_key=""))
    assert "not configured" in (await_coro(agent_loop._openai_chat_raw("gpt-4o", [], []))["content"]).lower()
    assert "not configured" in (await_coro(agent_loop._google_chat_raw("gemini-x", [], []))["content"]).lower()
