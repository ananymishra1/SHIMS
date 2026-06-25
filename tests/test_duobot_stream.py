"""Integration test for the streaming Council turn generator.

Drives ``run_council_turn_stream`` with mocked members + chair so it runs
offline and deterministically, asserting the live event sequence (members
surface as they finish, then the chair, then done).
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

import shared.omni_duobot as d


def _collect(agen):
    async def run():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.get_event_loop().run_until_complete(run())


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    # Point the conversation store at a temp dir if the module exposes one.
    for attr in ("_CONV_DIR", "CONV_DIR", "_STORE_DIR", "_DATA_DIR"):
        if hasattr(d, attr):
            monkeypatch.setattr(d, attr, tmp_path, raising=False)

    async def fake_member(member, conv):
        return {"role": member["id"], "content": f"{member.get('name', member['id'])} view",
                "ts": d._now(), "metadata": {"persona": member.get("name", member["id"])}}

    async def fake_chair(conv, responses):
        return {"final_answer": "Synthesised verdict", "actions": []}

    async def fake_ctx(conv_id):
        return {"ok": True, "hits": 0}

    monkeypatch.setattr(d, "_member_say", fake_member)
    monkeypatch.setattr(d, "_chair_decide", fake_chair)
    monkeypatch.setattr(d, "_feed_council_context", fake_ctx)
    yield


def test_council_stream_event_sequence():
    conv = d.create_conversation(topic="Should we ship on Friday?", mode="council")
    conv_id = conv["conversation"]["id"] if "conversation" in conv else conv.get("id")
    assert conv_id

    events = _collect(d.run_council_turn_stream(conv_id))
    types = [e["type"] for e in events]

    assert "council_start" in types
    assert types[-1] == "done"
    assert "chair_start" in types

    # One message per member + one chair message.
    msgs = [e for e in events if e["type"] == "message"]
    roles = [m["message"]["role"] for m in msgs]
    assert "chair" in roles
    assert len(msgs) >= 2  # at least a couple of members + chair

    # council_start announces the roster.
    start = next(e for e in events if e["type"] == "council_start")
    assert isinstance(start["members"], list) and start["members"]

    # The final 'done' carries the refreshed conversation including the verdict.
    done = events[-1]
    assert done["conversation"]
    contents = [m.get("content", "") for m in done["conversation"].get("messages", [])]
    assert any("Synthesised verdict" in c for c in contents)


def test_council_stream_handles_missing_conversation():
    events = _collect(d.run_council_turn_stream("nonexistent-id"))
    assert events and events[0]["type"] == "error"
