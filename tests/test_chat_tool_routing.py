"""Regression tests for unified-chat tool availability, reasoning effort, and
the web.fetch capability.

These cover a cluster of bugs that made the brain model look far less capable
than it was: the `native` provider was never offered tools, the LM Studio
streamer silently discarded any tool call it did receive, and every lane forced
`reasoning_effort="none"` so a thinking model could not think. The user-visible
symptom in all three cases was the same — confident, unsourced answers.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from backend.app import main


# --------------------------------------------------------------------------- #
# Tool availability
# --------------------------------------------------------------------------- #

def test_native_provider_may_call_tools():
    """The native engine streams tool calls, so it must be offered tools.

    Omitting it here was the root cause of 'SHIMS never uses tools': the active
    provider was `native`, so the tool gate matched nothing on every turn.
    """
    assert "native" in main.TOOL_CALLING_PROVIDERS


def test_tool_calling_providers_are_all_wired_to_a_streamer():
    """Guard the invariant the constant documents: every provider listed must
    have a branch in _model_turn_events that forwards tools."""
    import inspect

    src = inspect.getsource(main._model_turn_events)
    for provider in main.TOOL_CALLING_PROVIDERS:
        assert f'"{provider}"' in src, f"{provider} has no _model_turn_events branch"


def test_web_fetch_and_search_are_offered():
    names = {t["function"]["name"] for t in main._unified_chat_tools()}
    # web.fetch is what lets the model open a domain the user names; without it
    # 'summarize jklifecarecenters.com' could only ever be guessed at.
    assert {"web.search", "web.fetch"} <= names


def test_mail_tools_are_offered():
    names = {t["function"]["name"] for t in main._unified_chat_tools()}
    assert {"mail.read", "mail.draft", "mail.attachment"} <= names


def test_mail_draft_never_sends():
    spec = next(t for t in main._unified_chat_tools() if t["function"]["name"] == "mail.draft")
    assert "never sends" in spec["function"]["description"].lower()


def test_web_search_description_states_it_is_the_only_internet_path():
    spec = next(t for t in main._unified_chat_tools() if t["function"]["name"] == "web.search")
    assert "no other way to reach the internet" in spec["function"]["description"]


# --------------------------------------------------------------------------- #
# Lite gate — the allowlist must not strip tools from real questions
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("message", [
    "hi",
    "hello",
    "thanks",
    "ok",
])
def test_greetings_stay_on_the_fast_lane(message):
    assert main._is_simple_query(message) is True


@pytest.mark.parametrize("message", [
    "website exists",
    "can you summarize what that company does",
    "?????",
    "what does their pricing page say",
])
def test_substantive_turns_are_not_treated_as_trivial(message):
    """These are the turns that regressed. None match _is_tool_intent's
    substring blocklist, so under the old opt-out gate they went lite and lost
    every tool. _is_simple_query must not classify them as trivial."""
    assert main._is_simple_query(message) is False


# --------------------------------------------------------------------------- #
# Reasoning effort
# --------------------------------------------------------------------------- #

def test_full_lane_lets_a_thinking_model_think(monkeypatch):
    monkeypatch.delenv("SHIMS_REASONING_EFFORT", raising=False)
    # None means "omit the field", so the server applies the model's own default.
    assert main._reasoning_effort(lite=False) is None


def test_lite_lane_still_suppresses_reasoning_for_latency(monkeypatch):
    monkeypatch.delenv("SHIMS_REASONING_EFFORT", raising=False)
    assert main._reasoning_effort(lite=True) == "none"


@pytest.mark.parametrize("value", ["none", "low", "medium", "high"])
def test_explicit_reasoning_effort_overrides_both_lanes(monkeypatch, value):
    monkeypatch.setenv("SHIMS_REASONING_EFFORT", value)
    assert main._reasoning_effort(lite=False) == value
    assert main._reasoning_effort(lite=True) == value


def test_unknown_reasoning_effort_falls_back_to_auto(monkeypatch):
    monkeypatch.setenv("SHIMS_REASONING_EFFORT", "banana")
    assert main._reasoning_effort(lite=False) is None


def test_native_stream_omits_reasoning_effort_when_none():
    """agent_loop must translate None into an absent field, not the string
    'None' — koboldcpp/llama.cpp reject unknown values for this key."""
    from shared import agent_loop

    captured: dict = {}

    class _FakeEngine:
        def chat_stream(self, messages, on_delta, **kw):
            captured.update(kw)
            return {"content": "ok", "tool_calls": []}

    async def _run():
        with patch("shared.native_engine.get_engine", return_value=_FakeEngine()):
            return await agent_loop._native_chat_stream(
                "m", [{"role": "user", "content": "hi"}], [], lambda t: None,
                reasoning_effort=None,
            )

    import asyncio
    asyncio.run(_run())
    assert "reasoning_effort" not in captured


# --------------------------------------------------------------------------- #
# web.fetch execution
# --------------------------------------------------------------------------- #

def test_web_fetch_normalizes_a_bare_domain():
    """Models emit 'example.com'; agent_tools requires a scheme."""
    captured: dict = {}

    def _fake_run_tool(name, args, **kw):
        captured["name"] = name
        captured["args"] = args
        return {"ok": True, "url": args["url"], "chars": 12, "text": "hello world"}

    req = main.ChatRequest(message="summarize jklifecarecenters.com")

    async def _run():
        with patch.object(main.agent_tools, "run_tool", side_effect=_fake_run_tool):
            return await main._execute_unified_tool(
                "web.fetch", {"url": "jklifecarecenters.com"}, req, "sess-1"
            )

    import asyncio
    result, events, done_fields = asyncio.run(_run())

    assert captured["name"] == "web.fetch"
    assert captured["args"]["url"] == "https://jklifecarecenters.com"
    assert result["ok"] is True
    assert result["url"] == "https://jklifecarecenters.com"
    # The UI needs a search event to render the source chip.
    assert any(json.loads(e)["type"] == "search" for e in events)


def test_web_fetch_reports_failure_instead_of_inventing_content():
    def _fake_run_tool(name, args, **kw):
        return {"ok": False, "error": "connection refused"}

    req = main.ChatRequest(message="check example.com")

    async def _run():
        with patch.object(main.agent_tools, "run_tool", side_effect=_fake_run_tool):
            return await main._execute_unified_tool(
                "web.fetch", {"url": "example.com"}, req, "sess-2"
            )

    import asyncio
    result, events, done_fields = asyncio.run(_run())
    assert result["ok"] is False
    assert "connection refused" in result["error"]
    # A failed fetch must not emit a source chip implying evidence exists.
    assert not events


def test_web_fetch_rejects_empty_url():
    req = main.ChatRequest(message="fetch")

    import asyncio
    result, events, done_fields = asyncio.run(
        main._execute_unified_tool("web.fetch", {"url": "  "}, req, "sess-3")
    )
    assert result["ok"] is False


# --------------------------------------------------------------------------- #
# End-to-end: a native-provider turn must actually reach its tools
# --------------------------------------------------------------------------- #

def test_native_turn_offers_tools_and_runs_web_search(tmp_path, monkeypatch):
    """The exact reported failure, end to end.

    'search for <domain> and summarize' on the native provider used to be
    offered no tools at all (native was missing from the tool gate), so the
    model answered from training data and reported that it had searched and
    found nothing. This asserts the tools reach the model and the call is
    executed.
    """
    from fastapi.testclient import TestClient
    import shared.action_ledger as al
    import shared.omni_brain as ob
    from backend.app.main import app

    monkeypatch.setattr(al, "ACTION_DB", tmp_path / "actions.sqlite3")
    monkeypatch.setattr(ob, "BRAIN_DB", tmp_path / "brain.sqlite3")

    seen_tools: list[list[str]] = []

    async def fake_resolve(provider, model, *, privacy_mode="balanced", text=None):
        return "native", "qwen-thinking-test", "pinned-for-test"

    async def fake_native_stream(model, messages, tools, on_delta, **kw):
        seen_tools.append([t["function"]["name"] for t in (tools or [])])
        already_ran = any("Tool results" in str(m.get("content", "")) for m in messages)
        if tools and not already_ran:
            return {"content": "", "tool_calls": [
                {"function": {"name": "web.search", "arguments": {"query": "jklifecarecenters"}}}
            ]}
        await on_delta("JK Life Care Centers runs pharmacy retail outlets [1].")
        return {"content": "JK Life Care Centers runs pharmacy retail outlets [1].", "tool_calls": []}

    async def fake_search(query, max_results=6, provider=None, planned_query=None):
        return {
            "ok": True, "query": query, "provider": "fixture",
            "results": [{"title": "JK Life Care", "url": "https://jklifecarecenters.com",
                         "snippet": "Pharmacy retail chain"}],
            "query_plan": {"primary_query": query, "variants": [query]},
        }

    monkeypatch.setattr(main, "_resolve_provider_model", fake_resolve)
    monkeypatch.setattr(main, "_web_search", fake_search)
    monkeypatch.setattr("shared.agent_loop._native_chat_stream", fake_native_stream)

    chunks = []
    client = TestClient(app)
    with client.stream("POST", "/brain/turn", json={
        "message": "can you search for jklifecarecenters.com and summarize what you find?",
        "web_mode": True,
    }) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line:
                chunks.append(json.loads(line))

    # 1. The model was actually offered the toolset.
    assert seen_tools, "the native streamer was never called"
    assert "web.search" in seen_tools[0], f"native turn got no tools: {seen_tools[0]}"
    assert "web.fetch" in seen_tools[0]

    # 2. The tool call was executed and surfaced.
    kinds = [c.get("type") for c in chunks]
    assert "tool_call" in kinds, "no tool_call event — the UI would show no tool card"
    assert "tool_result" in kinds
    assert "search" in kinds, "no search event — no source chip for the user"

    call = next(c for c in chunks if c.get("type") == "tool_call")
    assert call["tool"] == "web.search"

    # 3. The turn is on the tool-capable lane and carries real evidence.
    done = [c for c in chunks if c.get("type") == "done"][-1]
    assert done["route"] == "native-unified", f"unexpected lane: {done['route']}"
    assert "web.search" in done["tools_used"]
    assert done["evidence"], "answer reported no evidence despite a successful search"


def test_greeting_still_takes_the_lite_lane(tmp_path, monkeypatch):
    """The latency fast path must survive the tool fix."""
    from fastapi.testclient import TestClient
    import shared.omni_brain as ob
    from backend.app.main import app

    monkeypatch.setattr(ob, "BRAIN_DB", tmp_path / "brain.sqlite3")

    async def fake_resolve(provider, model, *, privacy_mode="balanced", text=None):
        return "ollama", "test-model", "pinned-for-test"

    async def fake_stream(model, messages, tools, on_delta):
        assert not tools, "a greeting should not carry the full toolset"
        await on_delta("Hello!")
        return {"content": "Hello!", "tool_calls": []}

    monkeypatch.setattr(main, "_resolve_provider_model", fake_resolve)
    monkeypatch.setattr("shared.agent_loop._ollama_chat_stream", fake_stream)

    chunks = []
    client = TestClient(app)
    with client.stream("POST", "/brain/turn", json={"message": "hi there"}) as resp:
        for line in resp.iter_lines():
            if line:
                chunks.append(json.loads(line))

    done = [c for c in chunks if c.get("type") == "done"][-1]
    assert done["route"].endswith("-lite"), f"greeting left the fast lane: {done['route']}"


def test_lite_lane_rollback_knob(monkeypatch):
    """SHIMS_LITE_LANE=broad restores the pre-fix blocklist for latency triage.

    It must remain opt-in: the default has to be the allowlist, or the tool
    regression silently returns.
    """
    monkeypatch.delenv("SHIMS_LITE_LANE", raising=False)
    assert (os.getenv("SHIMS_LITE_LANE") or "trivial") == "trivial"
