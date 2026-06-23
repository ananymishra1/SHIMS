"""Tests for DuoBot Council of the Wises mode."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from shared import omni_duobot


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _patch_ai_module(monkeypatch):
    """Prevent heavy model loading."""
    monkeypatch.setattr(omni_duobot, "ai_module", object())


@pytest.fixture
def council_conv(tmp_path, monkeypatch):
    """Create a council-mode conversation backed by temp storage."""
    monkeypatch.setattr(omni_duobot, "DUOBOT_DIR", tmp_path / "duobot")
    omni_duobot.DUOBOT_DIR.mkdir(parents=True, exist_ok=True)
    omni_duobot.CONVERSATIONS_PATH = omni_duobot.DUOBOT_DIR / "conversations.jsonl"
    omni_duobot.VOTES_PATH = omni_duobot.DUOBOT_DIR / "proposal_votes.jsonl"
    omni_duobot.SETTINGS_PATH = omni_duobot.DUOBOT_DIR / "settings.json"
    # Make sure omnipotent mode does not force auto-execute in tests.
    monkeypatch.setenv("SHIMS_OMNIPOTENT_MODE", "false")

    conv = omni_duobot.create_conversation(topic="test council", mode="council")
    assert conv["ok"]
    conv_obj = conv["conversation"]
    conv_obj["council_settings"]["auto_execute"] = False
    # Persist the change.
    convs = omni_duobot._load_all_conversations()
    convs[conv_obj["id"]]["council_settings"]["auto_execute"] = False
    omni_duobot._rewrite_conversations(convs)
    return conv_obj


class TestCouncilMode:
    def test_create_council_conversation(self, council_conv):
        assert council_conv["mode"] == "council"
        assert "council_settings" in council_conv
        assert "personas" in council_conv
        ids = {p["id"] for p in council_conv["personas"]}
        assert ids >= {"primary", "gemini", "anthropic", "openai", "local"}

    def test_set_mode_to_council(self, tmp_path, monkeypatch):
        monkeypatch.setattr(omni_duobot, "DUOBOT_DIR", tmp_path / "duobot2")
        omni_duobot.DUOBOT_DIR.mkdir(parents=True, exist_ok=True)
        omni_duobot.CONVERSATIONS_PATH = omni_duobot.DUOBOT_DIR / "conversations.jsonl"
        omni_duobot.VOTES_PATH = omni_duobot.DUOBOT_DIR / "proposal_votes.jsonl"
        omni_duobot.SETTINGS_PATH = omni_duobot.DUOBOT_DIR / "settings.json"

        conv = omni_duobot.create_conversation(topic="switch test", mode="free")
        result = omni_duobot.set_mode(conv["conversation"]["id"], "council")
        assert result["ok"]
        assert result["conversation"]["mode"] == "council"
        assert "council_settings" in result["conversation"]

    def test_council_turn_with_mocked_members(self, council_conv, monkeypatch):
        conv_id = council_conv["id"]
        member_outputs = {
            "primary": "Primary opinion",
            "gemini": "Gemini opinion",
            "anthropic": "Claude opinion",
            "openai": "OpenAI opinion",
            "local": "Factory opinion",
        }

        async def fake_member_say(member, conv):
            return {
                "role": member["id"],
                "content": member_outputs[member["id"]],
                "ts": 1.0,
                "metadata": {"persona": member["id"]},
            }

        async def fake_chair_decide(conv, responses):
            return {
                "final_answer": "Chair says hello",
                "actions": [{"tool": "shell.run", "args": {"command": "echo hi"}, "reason": "greet"}],
            }

        monkeypatch.setattr(omni_duobot, "_member_say", fake_member_say)
        monkeypatch.setattr(omni_duobot, "_chair_decide", fake_chair_decide)

        executed = []

        def fake_run_tool(tool, args, allow_gated=False):
            executed.append((tool, args, allow_gated))
            return {"ok": True, "output": "hi"}

        monkeypatch.setattr("shared.agent_tools.run_tool", fake_run_tool)

        result = _run(omni_duobot.run_council_turn(conv_id))
        assert result["ok"]
        conv = omni_duobot.get_conversation(conv_id)
        roles = [m["role"] for m in conv["messages"]]
        assert "primary" in roles
        assert "gemini" in roles
        assert "anthropic" in roles
        assert "openai" in roles
        assert "local" in roles
        assert "chair" in roles
        assert executed == [("shell.run", {"command": "echo hi"}, False)]

    def test_council_action_gated_awaits_approval(self, council_conv, monkeypatch):
        conv_id = council_conv["id"]

        async def fake_member_say(member, conv):
            return {"role": member["id"], "content": "ok", "ts": 1.0, "metadata": {}}

        async def fake_chair_decide(conv, responses):
            return {
                "final_answer": "Do it",
                "actions": [
                    {"tool": "fs.write", "args": {"path": "test.txt", "content": "x"}, "reason": "write"},
                ],
            }

        monkeypatch.setattr(omni_duobot, "_member_say", fake_member_say)
        monkeypatch.setattr(omni_duobot, "_chair_decide", fake_chair_decide)

        def fake_run_tool(tool, args, allow_gated=False):
            if not allow_gated:
                return {"ok": True, "needs_approval": True, "tool": tool, "args": args}
            return {"ok": True}

        monkeypatch.setattr("shared.agent_tools.run_tool", fake_run_tool)

        result = _run(omni_duobot.run_council_turn(conv_id))
        assert result["ok"]
        conv = omni_duobot.get_conversation(conv_id)
        pending = conv.get("pending_council_actions", [])
        assert len(pending) == 1
        assert pending[0]["tool"] == "fs.write"
        # A second turn should be blocked until approval.
        result2 = _run(omni_duobot.run_council_turn(conv_id))
        assert not result2["ok"]
        assert "pending" in result2["error"].lower()

    def test_approve_council_action(self, council_conv, monkeypatch):
        conv_id = council_conv["id"]

        async def fake_member_say(member, conv):
            return {"role": member["id"], "content": "ok", "ts": 1.0, "metadata": {}}

        async def fake_chair_decide(conv, responses):
            return {
                "final_answer": "Do it",
                "actions": [{"tool": "shell.run", "args": {"command": "echo hi"}, "reason": "greet"}],
            }

        monkeypatch.setattr(omni_duobot, "_member_say", fake_member_say)
        monkeypatch.setattr(omni_duobot, "_chair_decide", fake_chair_decide)

        calls = []

        def fake_run_tool(tool, args, allow_gated=False):
            calls.append((tool, args, allow_gated))
            if not allow_gated:
                return {"ok": True, "needs_approval": True}
            return {"ok": True, "output": "hi"}

        monkeypatch.setattr("shared.agent_tools.run_tool", fake_run_tool)

        _run(omni_duobot.run_council_turn(conv_id))
        conv = omni_duobot.get_conversation(conv_id)
        approval_id = conv["pending_council_actions"][0]["approval_id"]

        result = omni_duobot.approve_council_action(conv_id, approval_id)
        assert result["ok"]
        assert any(allow_gated for _, _, allow_gated in calls)
        assert omni_duobot.get_conversation(conv_id)["pending_council_actions"] == []


class TestCouncilSettingsAndRAG:
    def test_save_ai_settings_with_council_personas_and_rag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(omni_duobot, "DUOBOT_DIR", tmp_path / "duobot")
        omni_duobot.DUOBOT_DIR.mkdir(parents=True, exist_ok=True)
        omni_duobot.SETTINGS_PATH = omni_duobot.DUOBOT_DIR / "settings.json"

        personas = {
            "gemini": {"enabled": True, "provider": "google", "model": "gemini-2.5-flash", "temperature": 0.5, "system_prompt": "Be bold."},
            "anthropic": {"enabled": False, "provider": "anthropic", "model": "claude-sonnet-4-6", "temperature": 0.2, "system_prompt": "Be safe."},
        }
        result = omni_duobot.save_settings({
            "primary_provider": "anthropic",
            "council_rag_enabled": False,
            "council_rag_limit": 6,
            "council_personas": personas,
        })
        assert result["primary_provider"] == "anthropic"
        assert result["council_rag_enabled"] is False
        assert result["council_rag_limit"] == 6
        assert result["council_personas"]["gemini"]["model"] == "gemini-2.5-flash"
        assert result["council_personas"]["anthropic"]["enabled"] is False

        loaded = omni_duobot.load_settings()
        assert loaded["council_rag_enabled"] is False

    def test_feed_council_context_once_per_turn(self, council_conv, monkeypatch):
        conv_id = council_conv["id"]
        omni_duobot.add_message(conv_id, "user", "How does the wave planner work in SHIMS?")

        calls = []

        def fake_retrieve_context(query, limit=4):
            calls.append((query, limit))
            return {
                "hits": [
                    {"source": "shims_source", "title": "agent_wave.py", "content": "Plans waves."},
                    {"source": "shims_source", "title": "agent_loop.py", "content": "Runs waves."},
                ]
            }

        monkeypatch.setattr("shared.omni_brain.retrieve_context", fake_retrieve_context)

        async def fake_member_say(member, conv):
            return {"role": member["id"], "content": "ok", "ts": 1.0, "metadata": {}}

        async def fake_chair_decide(conv, responses):
            return {"final_answer": "Done", "actions": []}

        monkeypatch.setattr(omni_duobot, "_member_say", fake_member_say)
        monkeypatch.setattr(omni_duobot, "_chair_decide", fake_chair_decide)
        monkeypatch.setattr("shared.agent_tools.run_tool", lambda tool, args, allow_gated=False: {"ok": True})

        _run(omni_duobot.run_council_turn(conv_id))

        assert len(calls) == 1, "retrieve_context should run once per council turn"
        conv = omni_duobot.get_conversation(conv_id)
        context_msgs = [m for m in conv["messages"] if m["role"] == "context"]
        assert len(context_msgs) == 1
        assert context_msgs[0].get("metadata", {}).get("rag") is True
        assert context_msgs[0]["metadata"]["hits"] == 2

    def test_feed_council_context_skipped_when_disabled(self, council_conv, monkeypatch):
        conv_id = council_conv["id"]
        omni_duobot.add_message(conv_id, "user", "Explain the wave planner.")

        convs = omni_duobot._load_all_conversations()
        convs[conv_id]["council_settings"]["rag_enabled"] = False
        omni_duobot._rewrite_conversations(convs)

        calls = []
        monkeypatch.setattr("shared.omni_brain.retrieve_context", lambda q, limit=4: (calls.append((q, limit)) or {"hits": []}))

        async def fake_member_say(member, conv):
            return {"role": member["id"], "content": "ok", "ts": 1.0, "metadata": {}}

        async def fake_chair_decide(conv, responses):
            return {"final_answer": "Done", "actions": []}

        monkeypatch.setattr(omni_duobot, "_member_say", fake_member_say)
        monkeypatch.setattr(omni_duobot, "_chair_decide", fake_chair_decide)
        monkeypatch.setattr("shared.agent_tools.run_tool", lambda tool, args, allow_gated=False: {"ok": True})

        _run(omni_duobot.run_council_turn(conv_id))

        assert len(calls) == 0, "retrieve_context should not run when RAG is disabled"


class TestCouncilGeneralTask:
    """Council should handle arbitrary user tasks, not only SHIMS improvement."""

    def test_member_prompt_is_task_agnostic(self, council_conv):
        member = council_conv["personas"][0]
        system = omni_duobot._council_member_system(member, council_conv)
        assert "not only SHIMS self-improvement" in system
        assert "ANY topic" in system

    def test_chair_prompt_accepts_general_requests(self, council_conv, monkeypatch):
        captured = {}

        class FakeAI:
            async def ask_ai(self, prompt, system=None, provider=None, model=None, **kwargs):
                captured["system"] = system
                captured["prompt"] = prompt
                return type("R", (object,), {"text": '{"final_answer": "ok", "actions": []}'})()

        monkeypatch.setattr(omni_duobot, "ai_module", FakeAI())

        _run(omni_duobot._chair_decide(council_conv, []))
        assert "ANY topic" in captured.get("system", "")
        assert "SHIMS tools can help" in captured.get("system", "")


class TestProposalLifecycle:
    @pytest.fixture
    def prop_storage(self, tmp_path, monkeypatch):
        storage = tmp_path / "storage"
        storage.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(omni_duobot, "STORAGE_DIR", storage)
        (storage / "peer_sync").mkdir(exist_ok=True)
        (storage / "improvement_loop").mkdir(exist_ok=True)
        omni_duobot.VOTES_PATH = tmp_path / "duobot" / "proposal_votes.jsonl"
        omni_duobot.VOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
        return storage

    def _make_peer_proposal(self, storage, pid, title="Test"):
        peer_path = storage / "peer_sync" / "proposals.jsonl"
        entry = {
            "proposal": {
                "patch_id": pid,
                "type": "self.patch",
                "meta": {
                    "title": title,
                    "why_proposal": "because",
                    "problem_statement": "problem",
                    "solution_proposed": "fix it",
                    "options_considered": ["a", "b"],
                    "files_to_change": ["shared/x.py"],
                    "risk": "low",
                    "expected_benefit": "better",
                },
            },
            "received_at": time.time(),
        }
        peer_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        return pid

    def test_delete_proposal(self, prop_storage):
        pid = self._make_peer_proposal(prop_storage, "patch-001")
        before = omni_duobot.get_pending_proposals(limit=50)
        assert any(p["id"] == pid for p in before)

        result = omni_duobot.delete_proposal(pid)
        assert result["ok"]

        after = omni_duobot.get_pending_proposals(limit=50)
        assert not any(p["id"] == pid for p in after)

    def test_rethink_proposal(self, prop_storage):
        pid = self._make_peer_proposal(prop_storage, "patch-002")
        assert any(p["id"] == pid for p in omni_duobot.get_pending_proposals(limit=50))

        result = omni_duobot.rethink_proposal(pid, feedback="Need more unit tests.")
        assert result["ok"]
        assert result["feedback"] == "Need more unit tests."

        assert not any(p["id"] == pid for p in omni_duobot.get_pending_proposals(limit=50))
        rethink = [e for e in omni_duobot._jsonl_read(omni_duobot.VOTES_PATH) if e.get("action") == "rethink"]
        assert any(e["proposal_id"] == pid for e in rethink)
