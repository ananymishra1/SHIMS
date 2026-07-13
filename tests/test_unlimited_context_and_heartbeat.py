"""Tests for unlimited virtual context and stream heartbeats.

Unlimited context = recent turns verbatim in the prompt window + every older
turn archived append-only into the brain (background) + session-scoped
recall of the relevant archived turns on demand. Heartbeats = keepalive
status events whenever a model sits silent, so streams never look dead.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from shared.omni_brain import remember_turn, recall_conversation


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Archive is append-only (regression: the old shared source_uri made every
# new turn delete the previous turn's archive — memory capped at one turn).
# ---------------------------------------------------------------------------
def test_conversation_archive_is_append_only():
    sid = f"test-ctx-{uuid.uuid4().hex[:10]}"
    remember_turn(sid, "My anniversary gift idea is a vintage star projector for Meera",
                  "Noted — a vintage star projector for Meera. Lovely anniversary choice.")
    remember_turn(sid, "Also remind me the wifi password of the lab router is duck-bolt-42",
                  "Saved: the lab router wifi password is duck-bolt-42.")

    hits_first = recall_conversation(sid, "what anniversary gift did I plan for Meera")
    hits_second = recall_conversation(sid, "lab router wifi password")

    assert any("star projector" in h["text"] for h in hits_first), \
        "turn 1 must survive after turn 2 is archived (append-only archive)"
    assert any("duck-bolt-42" in h["text"] for h in hits_second)


def test_recall_conversation_is_session_scoped():
    sid_a = f"test-ctx-{uuid.uuid4().hex[:10]}"
    sid_b = f"test-ctx-{uuid.uuid4().hex[:10]}"
    remember_turn(sid_a, "The launch codename for our secret pharma project is BLUEHERON",
                  "Understood, codename BLUEHERON recorded for this discussion.")

    assert any("BLUEHERON" in h["text"] for h in recall_conversation(sid_a, "launch codename secret project"))
    assert not any("BLUEHERON" in h["text"] for h in recall_conversation(sid_b, "launch codename secret project"))


def test_recall_conversation_empty_inputs_are_safe():
    assert recall_conversation("", "anything") == []
    assert recall_conversation("some-session", "") == []


# ---------------------------------------------------------------------------
# Fast lane: recall injection + background archiving + heartbeat
# ---------------------------------------------------------------------------
def _fake_collector(reply: str, *, delay: float = 0.0, capture: dict | None = None):
    async def fake_collect(model, messages, *, realtime=False, max_tokens=None, on_delta=None, first_token_timeout=60.0):
        if capture is not None:
            capture["messages"] = messages
        if delay:
            await asyncio.sleep(delay)
        if on_delta:
            await on_delta(reply)
        return reply

    return fake_collect


async def _collect_stream(m, req, settle: float = 0.0):
    chunks = []
    async for chunk in m._safe_brain_stream(req):
        chunks.append(json.loads(chunk.decode("utf-8")))
        if len(chunks) > 40:
            break
    if settle:
        await asyncio.sleep(settle)
    return chunks


def test_fast_lane_injects_recalled_context(monkeypatch):
    import backend.app.main as m

    def fake_recall(session_id, query, *, limit=4, max_rows=800):
        return [{"title": "Conversation", "text": "User: my dog's name is Biscuit\nAssistant: Noted, Biscuit!", "score": 5.0}]

    capture: dict = {}
    monkeypatch.setattr(m, "recall_conversation", fake_recall)
    monkeypatch.setattr(m, "_collect_ollama_stream", _fake_collector("Your dog is Biscuit.", capture=capture))

    req = m.ChatRequest(message="what is my dog called again?", session_id="test-recall-inject",
                        provider="ollama", model="llama3.2:latest", conversation_mode=True)
    chunks = _run(_collect_stream(m, req))

    system_msg = capture["messages"][0]
    assert system_msg["role"] == "system"
    assert "Relevant earlier conversation" in system_msg["content"]
    assert "Biscuit" in system_msg["content"]
    meta = next(c for c in chunks if c["type"] == "meta")
    assert meta["memory_hits"] == 1


def test_fast_lane_recall_failure_never_blocks_reply(monkeypatch):
    import backend.app.main as m

    def broken_recall(*args, **kwargs):
        raise RuntimeError("recall store unavailable")

    monkeypatch.setattr(m, "recall_conversation", broken_recall)
    monkeypatch.setattr(m, "_collect_ollama_stream", _fake_collector("Still fine!"))

    req = m.ChatRequest(message="hello there my friend", session_id="test-recall-broken",
                        provider="ollama", model="llama3.2:latest", conversation_mode=True)
    chunks = _run(_collect_stream(m, req))
    answer = "".join(c.get("content", "") for c in chunks if c["type"] == "token")
    assert "Still fine!" in answer


def test_fast_lane_archives_turn_in_background(monkeypatch):
    import backend.app.main as m

    recorded: dict = {}

    def fake_remember_turn(session_id, user_text, assistant_text, **kwargs):
        recorded.update({"session_id": session_id, "user": user_text, "assistant": assistant_text})
        return {"ok": True}

    monkeypatch.setattr(m, "remember_turn", fake_remember_turn)
    monkeypatch.setattr(m, "_collect_ollama_stream", _fake_collector("Archived reply."))

    req = m.ChatRequest(message="please remember this important fact forever", session_id="test-archive-bg",
                        provider="ollama", model="llama3.2:latest", conversation_mode=True)
    _run(_collect_stream(m, req, settle=0.15))

    assert recorded.get("session_id") == "test-archive-bg"
    assert "important fact" in recorded.get("user", "")
    assert recorded.get("assistant") == "Archived reply."


def test_fast_lane_heartbeat_while_model_is_silent(monkeypatch):
    import backend.app.main as m

    monkeypatch.setenv("SHIMS_STREAM_HEARTBEAT_SECONDS", "0.05")
    monkeypatch.setattr(m, "_collect_ollama_stream", _fake_collector("Late answer.", delay=0.35))

    req = m.ChatRequest(message="think hard about this one", session_id="test-heartbeat-fast",
                        provider="ollama", model="llama3.2:latest", conversation_mode=False)
    chunks = _run(_collect_stream(m, req))

    heartbeats = [c for c in chunks if c.get("heartbeat")]
    token_idx = next(i for i, c in enumerate(chunks) if c["type"] == "token")
    assert heartbeats, "a silent model must produce keepalive status events"
    assert chunks.index(heartbeats[0]) < token_idx, "heartbeat should arrive before the first token"
    answer = "".join(c.get("content", "") for c in chunks if c["type"] == "token")
    assert "Late answer." in answer


# ---------------------------------------------------------------------------
# Agent loop: wave-planning heartbeat + LM Studio synthesis streaming
# ---------------------------------------------------------------------------
def _dummy_create_pending(**kwargs):
    return {}


def test_wave_planning_heartbeat_keeps_stream_alive(monkeypatch):
    from shared import agent_loop

    monkeypatch.setenv("SHIMS_STREAM_HEARTBEAT_SECONDS", "0.05")

    async def slow_stream(model, messages, tools, on_delta):
        await asyncio.sleep(0.4)
        return {"content": '{"reasoning":"direct","wave":[],"final":"Answer after a long think."}', "tool_calls": []}

    monkeypatch.setattr(agent_loop, "_ollama_chat_stream", slow_stream)

    async def collect():
        events = []
        async for ev in agent_loop.run_agent_loop(
            message="ponder deeply",
            messages=[{"role": "user", "content": "ponder deeply"}],
            model="llama3.2:latest",
            provider="ollama",
            session_id="test-wave-heartbeat",
            create_pending=_dummy_create_pending,
        ):
            events.append(ev)
        return events

    events = _run(collect())
    heartbeats = [e for e in events if e.get("heartbeat")]
    final = next(e for e in events if "__final__" in e)["__final__"]
    assert heartbeats, "wave planning must emit keepalive events while the model thinks"
    assert "still planning" in heartbeats[0]["content"]
    assert final["answer"] == "Answer after a long think."


def test_lmstudio_synthesis_streams_tokens(monkeypatch):
    """Regression: lmstudio is in _is_openai_compatible, so the old branch
    order sent LM Studio synthesis down the NON-streaming cloud path and the
    streaming branch was unreachable."""
    from shared import agent_loop

    async def fake_lm_stream(model, messages, tools, on_delta):
        if tools:
            # Wave-planning call: no tools needed, no final -> forces synthesis.
            return {"content": '{"reasoning":"nothing to do","wave":[],"final":null}', "tool_calls": []}
        # Synthesis call: stream prose in two chunks.
        await on_delta("Here is ")
        await on_delta("the streamed answer.")
        return {"content": "Here is the streamed answer.", "tool_calls": []}

    async def fake_plan(*args, **kwargs):
        return []

    monkeypatch.setattr(agent_loop, "_lmstudio_chat_stream", fake_lm_stream)
    monkeypatch.setattr(agent_loop, "_generate_plan", fake_plan)

    async def collect():
        events = []
        async for ev in agent_loop.run_agent_loop(
            message="say something nice",
            messages=[{"role": "user", "content": "say something nice"}],
            model="google/gemma-4-e4b",
            provider="lmstudio",
            session_id="test-lms-synth",
            create_pending=_dummy_create_pending,
        ):
            events.append(ev)
        return events

    events = _run(collect())
    token_events = [e for e in events if e.get("type") == "token"]
    final = next(e for e in events if "__final__" in e)["__final__"]

    assert len(token_events) >= 2, "LM Studio synthesis must stream progressively"
    streamed = "".join(e["content"] for e in token_events)
    assert streamed == "Here is the streamed answer."
    assert final["answer"] == "Here is the streamed answer."
    # And no duplicated full answer after the streamed copy.
    assert streamed.count("the streamed answer.") == 1
