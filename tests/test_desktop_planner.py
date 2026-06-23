from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from shared.desktop_planner import _fallback_plan, _llm_plan_steps, plan_from_goal


def test_fallback_plan_splits_comma_separated_goal():
    goal = "search web, summarize results, save to memory"
    steps = _fallback_plan(goal)
    assert len(steps) == 3
    assert steps[0]["description"] == "search web"
    assert steps[1]["depends_on"] == ["s1"]


def test_fallback_plan_single_goal():
    steps = _fallback_plan("do one thing")
    assert len(steps) == 1
    assert steps[0]["tool_hint"] == "agent.run"


def test_llm_plan_steps_parses_and_filters_steps():
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "message": {
            "content": json.dumps([
                {"step_id": "s1", "description": "Search web", "tool_hint": "web.search", "depends_on": []},
                {"step_id": "s2", "description": "Summarize", "tool_hint": "agent.run", "depends_on": ["s1"]},
                {"description": "Bad tool", "tool_hint": "invalid.tool", "depends_on": []},
            ])
        }
    }
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.post.return_value = fake_response

    with patch("shared.desktop_planner.httpx.Client", return_value=fake_client):
        steps = _llm_plan_steps("research a topic and summarize")

    assert len(steps) == 3
    assert steps[0]["tool_hint"] == "web.search"
    assert steps[1]["depends_on"] == ["s1"]
    assert steps[2]["tool_hint"] == "agent.run"  # invalid normalized to default


def test_llm_plan_steps_returns_empty_on_connection_error():
    with patch("shared.desktop_planner.httpx.Client") as mock_cls:
        mock_cls.side_effect = Exception("connection refused")
        steps = _llm_plan_steps("some goal")
    assert steps == []


def test_llm_plan_steps_returns_empty_for_short_goal():
    assert _llm_plan_steps("hi") == []


def test_plan_from_goal_uses_llm_then_fallback(monkeypatch):
    called_with = []

    def fake_llm_steps(goal: str):
        called_with.append(goal)
        return [{"step_id": "s1", "description": "LLM step", "tool_hint": "agent.run"}]

    monkeypatch.setattr("shared.desktop_planner._llm_plan_steps", fake_llm_steps)
    plan = plan_from_goal("test goal")
    assert len(plan.steps) == 1
    assert plan.steps[0].description == "LLM step"


def test_plan_from_goal_falls_back_when_llm_returns_empty(monkeypatch):
    monkeypatch.setattr("shared.desktop_planner._llm_plan_steps", lambda g: [])
    plan = plan_from_goal("a, b, c")
    assert len(plan.steps) == 3
    assert plan.steps[0].description == "a"
