"""Tests for streaming the wave-router's "final" field in real time.

Background: the wave engine asks the model for strict JSON
(``{"reasoning": "...", "wave": [...], "final": "..."}``). When the model
decides no more tools are needed, the answer sits inside that JSON's
"final" field. Without FinalFieldStreamer, the whole JSON has to finish and
get parsed before a single word reaches the user — even for a plain
conversational reply that never needed a tool call. These tests cover the
text-processing class in isolation and its wiring into run_agent_loop.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from shared.agent_loop import FinalFieldStreamer
from shared import agent_loop
from shared import agent_tools


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# FinalFieldStreamer — pure text processing, no LLM involved.
# ---------------------------------------------------------------------------
def test_streamer_reveals_final_field_progressively():
    streamer = FinalFieldStreamer()
    full = '{"reasoning":"direct","wave":[],"final":"Hello there, how can I help?"}'
    emitted = ""
    for i in range(0, len(full), 3):
        emitted += streamer.feed(full[i:i + 3])
    assert emitted == "Hello there, how can I help?"


def test_streamer_never_fires_for_tool_call_wave():
    """final: null (a real tool-call wave) must never leak partial JSON."""
    streamer = FinalFieldStreamer()
    full = json.dumps({"reasoning": "search web", "wave": [{"tool": "web.search", "args": {"query": "x"}}], "final": None})
    emitted = ""
    for i in range(0, len(full), 4):
        emitted += streamer.feed(full[i:i + 4])
    assert emitted == ""


def test_streamer_handles_escaped_quotes_and_newlines():
    streamer = FinalFieldStreamer()
    raw_answer = 'Line one\nHe said "hi"\\ok'
    full = json.dumps({"reasoning": "x", "wave": [], "final": raw_answer})
    emitted = ""
    for i in range(0, len(full), 2):
        emitted += streamer.feed(full[i:i + 2])
    assert emitted == raw_answer


def test_streamer_handles_split_escape_across_chunks():
    """A \\n split exactly between two feed() calls must not corrupt output."""
    streamer = FinalFieldStreamer()
    prefix = '{"reasoning":"x","wave":[],"final":"a'
    # Feed up to and including the lone backslash, then the rest separately.
    out1 = streamer.feed(prefix + "\\")
    out2 = streamer.feed('n' + 'b"}')
    assert out1 + out2 == "a\nb"


def test_streamer_ignores_plain_prose_without_final_key():
    """Non-JSON prose (model ignored the wave format) never matches — the
    caller's own prose-mode branch handles that case instead."""
    streamer = FinalFieldStreamer()
    emitted = streamer.feed("Sure, here is the answer without any JSON wrapper.")
    assert emitted == ""


def test_streamer_stops_after_closing_quote():
    streamer = FinalFieldStreamer()
    full = '{"reasoning":"x","wave":[],"final":"done"} trailing garbage that must not leak'
    emitted = streamer.feed(full)
    assert emitted == "done"


# ---------------------------------------------------------------------------
# Integration: run_agent_loop streams the router's direct "final" answer.
# ---------------------------------------------------------------------------
def _dummy_create_pending(**kwargs):
    return {}


def test_run_agent_loop_streams_direct_answer_progressively(monkeypatch):
    """When the router decides no tools are needed, its "final" text must
    arrive as multiple progressive token events, not one blob at the end."""
    async def fake_stream(model, messages, tools, on_delta):
        full = '{"reasoning":"direct","wave":[],"final":"Hello there, how can I help you today?"}'
        content = ""
        streamer = None
        mode = None
        for i in range(0, len(full), 4):
            chunk = full[i:i + 4]
            content += chunk
            if mode is None:
                stripped = content.lstrip()
                if stripped:
                    mode = "json" if stripped[0] == "{" else "prose"
                    if mode == "json":
                        streamer = FinalFieldStreamer()
                        emitted = streamer.feed(content)
                        if emitted:
                            await on_delta(emitted)
            elif mode == "json" and streamer is not None:
                emitted = streamer.feed(chunk)
                if emitted:
                    await on_delta(emitted)
        return {"content": content, "tool_calls": []}

    monkeypatch.setattr(agent_loop, "_ollama_chat_stream", fake_stream)

    async def collect():
        events = []
        async for ev in agent_loop.run_agent_loop(
            message="hi, how are you?",
            messages=[{"role": "user", "content": "hi, how are you?"}],
            model="llama3.2:latest",
            provider="ollama",
            session_id="test-wave-stream",
            create_pending=_dummy_create_pending,
        ):
            events.append(ev)
        return events

    events = _run(collect())
    token_events = [e for e in events if e.get("type") == "token"]
    final = next(e for e in events if "__final__" in e)["__final__"]

    assert len(token_events) > 1, "answer should stream as multiple chunks, not one blob"
    streamed_text = "".join(e["content"] for e in token_events)
    assert streamed_text == "Hello there, how can I help you today?"
    assert final["answer"] == "Hello there, how can I help you today?"
    # No duplicate: the streamed text must equal the final answer exactly
    # once, not be followed by a second full copy.
    assert streamed_text.count("Hello there, how can I help you today?") == 1


def test_run_agent_loop_tool_wave_unaffected_by_streaming(monkeypatch):
    """A genuine tool-call wave (final: null) must execute exactly as
    before — the streamer must never leak partial tool-call JSON as tokens."""
    calls = {"n": 0}

    def _mock_tool(args):
        calls["n"] += 1
        return {"ok": True, "output": "mock tool ran"}

    agent_tools.register_ephemeral_tool("mock_echo_tool", "Mock echo tool for tests", _mock_tool)

    async def fake_reflection(*args, **kwargs):
        # Avoid a real (unreachable) network call for the post-tool reflection step.
        return {"content": "", "tool_calls": []}, "ollama", "llama3.2:latest"

    monkeypatch.setattr(agent_loop, "_chat_with_fallback", fake_reflection)

    async def fake_stream(model, messages, tools, on_delta):
        calls["n"] += 1
        if calls["n"] == 1:
            content = json.dumps({
                "reasoning": "use the tool",
                "wave": [{"tool": "mock_echo_tool", "args": {}, "purpose": "test"}],
                "final": None,
            })
        else:
            content = '{"reasoning":"done","wave":[],"final":"Tool ran successfully."}'
        # Simulate: json-mode content is never forwarded via on_delta unless
        # a "final" string key is found (matches real streaming functions).
        streamer = FinalFieldStreamer()
        emitted = streamer.feed(content)
        if emitted:
            await on_delta(emitted)
        return {"content": content, "tool_calls": []}

    monkeypatch.setattr(agent_loop, "_ollama_chat_stream", fake_stream)

    async def collect():
        events = []
        async for ev in agent_loop.run_agent_loop(
            message="run the mock tool",
            messages=[{"role": "user", "content": "run the mock tool"}],
            model="llama3.2:latest",
            provider="ollama",
            session_id="test-wave-tool",
            create_pending=_dummy_create_pending,
            tool_names=["mock_echo_tool"],
        ):
            events.append(ev)
        return events

    events = _run(collect())
    tool_call_events = [e for e in events if e.get("type") == "tool_call"]
    token_events_before_final = [e for e in events if e.get("type") == "token"]
    final = next(e for e in events if "__final__" in e)["__final__"]

    assert len(tool_call_events) == 1
    assert tool_call_events[0]["tool"] == "mock_echo_tool"
    assert "Tool ran successfully." in final["answer"]
    # The first wave's tool-call JSON (final: null) must never have leaked
    # as a token — only the second wave's genuine "final" text should appear.
    streamed = "".join(e["content"] for e in token_events_before_final)
    assert '"wave"' not in streamed and '"tool"' not in streamed
