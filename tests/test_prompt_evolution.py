"""Tests for the Prompt Evolution Lab."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.prompt_evolution import (
    PromptVariant,
    ensure_control_variant,
    generate_mutations,
    list_variants,
    run_eval_suite,
    score_run,
    compare_variants,
    promote_variant,
    EvalResult,
)


def test_ensure_control() -> None:
    v = ensure_control_variant("You are a helpful assistant.", name="test-control")
    assert v.name == "test-control"
    assert "helpful" in v.prompt
    assert v.active is True


def test_mutate_generates_children() -> None:
    parent = PromptVariant(id="p_test", name="test", prompt="You are helpful.", generation=0)
    children = generate_mutations(parent, n=2)
    assert len(children) >= 1
    for c in children:
        assert c.parent_id == "p_test"
        assert c.generation == 1


def test_score_run() -> None:
    results = [
        EvalResult(case="a", ok=True, latency_ms=100.0, score=1.0, message="ok"),
        EvalResult(case="b", ok=False, latency_ms=200.0, score=0.0, message="fail"),
    ]
    s = score_run(results)
    assert s["score"] == 0.5
    assert s["pass_rate"] == 0.5
    assert s["avg_latency_ms"] == 150.0


def test_run_eval_suite_updates_stats() -> None:
    v = ensure_control_variant("You are a test assistant.", name="test-suite")
    cases = [
        ("always_pass", lambda: (True, "pass", {})),
        ("always_fail", lambda: (False, "fail", {})),
    ]
    run = run_eval_suite(v.id, cases)
    assert run.summary["cases"] == 2
    assert run.summary["pass_rate"] == 0.5
    updated = list_variants()[0]
    assert updated.runs >= 1


def test_compare_and_promote() -> None:
    ensure_control_variant("Control prompt.", name="compare-control")
    board = compare_variants()
    assert len(board) >= 1
    winner_id = board[0]["id"]
    promoted = promote_variant(winner_id)
    assert promoted is not None
    assert promoted.active is True


if __name__ == "__main__":
    test_ensure_control()
    test_mutate_generates_children()
    test_score_run()
    test_run_eval_suite_updates_stats()
    test_compare_and_promote()
    print("prompt evolution tests passed")
