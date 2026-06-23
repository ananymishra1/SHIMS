"""Wave execution latency eval harness.

Measures the execution speedup from wave-based parallel tool calling vs
sequential step execution. Uses deterministic mock tools so the numbers are
comparable across runs, plus an optional real-tool smoke path.

Run:
    .venv/Scripts/python -m pytest tests/test_wave_latency.py -v -s
    .venv/Scripts/python tests/test_wave_latency.py
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared import agent_tools, agent_wave


# ---------------------------------------------------------------------------
# Mock slow tools (deterministic sleeps so the harness is reproducible)
# ---------------------------------------------------------------------------
def _register_mock_tools() -> None:
    """Inject ephemeral mock tools for latency measurement."""
    import time

    def _slow_a(args: dict[str, Any]) -> dict[str, Any]:
        time.sleep(args.get("delay", 0.5))
        return {"ok": True, "tool": "mock_slow_a", "result": args.get("x", 1) * 2}

    def _slow_b(args: dict[str, Any]) -> dict[str, Any]:
        time.sleep(args.get("delay", 0.5))
        return {"ok": True, "tool": "mock_slow_b", "result": args.get("y", 2) * 3}

    def _slow_c(args: dict[str, Any]) -> dict[str, Any]:
        time.sleep(args.get("delay", 0.5))
        return {"ok": True, "tool": "mock_slow_c", "result": args.get("z", 3) * 4}

    agent_tools.register_ephemeral_tool("mock_slow_a", "Mock slow tool A", _slow_a)
    agent_tools.register_ephemeral_tool("mock_slow_b", "Mock slow tool B", _slow_b)
    agent_tools.register_ephemeral_tool("mock_slow_c", "Mock slow tool C", _slow_c)


# ---------------------------------------------------------------------------
# Core measurements
# ---------------------------------------------------------------------------
def _run_sequential(calls: list[agent_wave.WaveCall], session_id: str = "") -> float:
    """Run the same calls one at a time (simulates old step loop)."""

    async def _seq() -> None:
        for call in calls:
            result = agent_tools.run_tool(call.name, call.args, allow_gated=False, session_id=session_id)
            call.result = result

    start = time.perf_counter()
    asyncio.run(_seq())
    return time.perf_counter() - start


def _run_wave(calls: list[agent_wave.WaveCall], session_id: str = "") -> float:
    """Run calls as one wave (parallel)."""

    async def _wave() -> None:
        await agent_wave.execute_wave(calls, seen={}, session_id=session_id)

    start = time.perf_counter()
    asyncio.run(_wave())
    return time.perf_counter() - start


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
def test_wave_vs_sequential_mock_3_tools() -> None:
    """3 mock tools each sleep 0.5s. Wave should take ~0.5s, sequential ~1.5s."""
    _register_mock_tools()
    calls = [
        agent_wave.WaveCall("mock_slow_a", {"delay": 0.5, "x": 1}, "mock task a"),
        agent_wave.WaveCall("mock_slow_b", {"delay": 0.5, "y": 2}, "mock task b"),
        agent_wave.WaveCall("mock_slow_c", {"delay": 0.5, "z": 3}, "mock task c"),
    ]
    seq_t = _run_sequential(list(calls))
    wave_t = _run_wave(list(calls))
    speedup = seq_t / wave_t if wave_t > 0 else 0.0

    print(f"\n[mock 3 tools] sequential={seq_t:.2f}s wave={wave_t:.2f}s speedup={speedup:.1f}x")
    assert wave_t < seq_t * 0.6, f"wave not faster: {wave_t:.2f}s vs {seq_t:.2f}s"
    assert speedup >= 2.0, f"expected >=2x speedup, got {speedup:.1f}x"


def test_wave_vs_sequential_mixed_delays() -> None:
    """Mix of 0.2s, 0.4s, 0.6s sleeps. Wave dominated by slowest."""
    _register_mock_tools()
    calls = [
        agent_wave.WaveCall("mock_slow_a", {"delay": 0.2}, "fast"),
        agent_wave.WaveCall("mock_slow_b", {"delay": 0.4}, "medium"),
        agent_wave.WaveCall("mock_slow_c", {"delay": 0.6}, "slow"),
    ]
    seq_t = _run_sequential(list(calls))
    wave_t = _run_wave(list(calls))
    speedup = seq_t / wave_t if wave_t > 0 else 0.0

    print(f"\n[mixed delays] sequential={seq_t:.2f}s wave={wave_t:.2f}s speedup={speedup:.1f}x")
    assert wave_t < 1.0, f"wave took too long: {wave_t:.2f}s"
    assert speedup >= 1.8, f"expected >=1.8x speedup, got {speedup:.1f}x"


def test_wave_no_duplicate_calls() -> None:
    """Wave engine should skip exact duplicate calls within a wave."""
    _register_mock_tools()
    calls = [
        agent_wave.WaveCall("mock_slow_a", {"delay": 0.3}, "dup 1"),
        agent_wave.WaveCall("mock_slow_a", {"delay": 0.3}, "dup 2"),
    ]
    wave_t = _run_wave(calls)
    assert calls[1].skipped_duplicate is True
    assert calls[1].result is not None
    print(f"\n[duplicate skip] wave={wave_t:.2f}s skipped={calls[1].skipped_duplicate}")


# ---------------------------------------------------------------------------
# CLI entry point for ad-hoc reporting
# ---------------------------------------------------------------------------
def _report() -> None:
    print("=" * 60)
    print("SHIMS Wave Engine Latency Eval")
    print("=" * 60)
    test_wave_vs_sequential_mock_3_tools()
    test_wave_vs_sequential_mixed_delays()
    test_wave_no_duplicate_calls()
    print("\n" + "=" * 60)
    print("All wave latency evals passed.")
    print("=" * 60)


if __name__ == "__main__":
    _report()
