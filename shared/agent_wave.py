# Wave engine v3 — agent-loop self-modification tested.
"""Wave-based agent execution engine — SHIMS Agent OS v3.

A wave is a set of independent tool calls emitted by the model in a single
turn and executed in parallel. This replaces the step-by-step loop and cuts
multi-step task latency from O(n) to roughly O(wave_count).

Wave format (JSON emitted by model):
    {
      "wave": [
        {"tool": "fs.list", "args": {"path": "."}, "purpose": "List files"},
        {"tool": "web.search", "args": {"query": "..."}, "purpose": "Find docs"}
      ],
      "reasoning": "why these tools in parallel",
      "final": null
    }

If the model sets "final" to a string, the agent loop stops and returns that
as the answer without calling more tools.
"""
# Wave engine v3 runtime tested with Anthropic router + Ollama executor on 2026-06-10.
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Callable

from . import agent_tools

# Global concurrency cap for parallel tool execution inside a wave.
# This protects local/cloud providers from being swamped when many agents/tools run.
_MAX_PARALLEL_TOOLS = int(os.getenv("SHIMS_MAX_PARALLEL_TOOLS", "4"))
_wave_semaphore = asyncio.Semaphore(_MAX_PARALLEL_TOOLS)

_WAVE_SYSTEM = """You are SHIMS Omni Wave Router. Your job is to decide which tools to call RIGHT NOW.

RULES:
1. Call MULTIPLE tools in parallel when they are independent.
2. NEVER call the same tool with the same args twice.
3. If the task is complete or needs no tools, set "final" to your answer.
4. Keep tool arguments minimal and correct.
5. NEVER output prose, explanations, or sentences describing what you will do. ONLY emit JSON.
6. DO NOT claim to have performed an action (search, read, run) without emitting the matching tool call. If the user asked for a search, include web.search or browser.search in the wave.

AVAILABLE TOOLS (use exact names):
{tool_list}

RESPOND WITH VALID JSON ONLY — no markdown, no backticks, no commentary:
{{
  "reasoning": "one-line strategy",
  "wave": [
    {{"tool": "tool.name", "args": {{...}}, "purpose": "one-line why"}}
  ],
  "final": null
}}

If no tools are needed:
{{
  "reasoning": "direct answer",
  "wave": [],
  "final": "Your concise answer here."
}}"""


class WaveCall:
    """Single tool call inside a wave."""

    def __init__(self, name: str, args: dict[str, Any], purpose: str = ""):
        self.name = name
        self.args = args
        self.purpose = purpose
        self.result: dict[str, Any] | None = None
        self.approval: dict[str, Any] | None = None
        self.skipped_duplicate = False


class WaveResult:
    """Result of executing one wave."""

    def __init__(self, calls: list[WaveCall], stop: bool = False, answer: str = "", stop_reason: str = ""):
        self.calls = calls
        self.stop = stop
        self.answer = answer
        self.stop_reason = stop_reason


def _parse_wave(content: str, valid_names: set[str]) -> tuple[list[WaveCall], str | None]:
    """Extract wave calls and optional final answer from model JSON output."""
    content = content.strip()
    if not content:
        return [], None

    # Strip markdown fences
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(content)
    except Exception:
        # Try to find JSON object in the text
        try:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(content[start : end + 1])
            else:
                return [], None
        except Exception:
            return [], None

    final = data.get("final")
    if final and isinstance(final, str) and final.strip():
        return [], final.strip()

    calls: list[WaveCall] = []
    for item in data.get("wave", []) or []:
        if not isinstance(item, dict):
            continue
        name = item.get("tool") or item.get("name") or ""
        if name not in valid_names:
            continue
        args = item.get("args") or item.get("arguments") or {}
        purpose = item.get("purpose") or item.get("reason") or ""
        calls.append(WaveCall(name, args, purpose))
    return calls, None


async def plan_wave(
    messages: list[dict[str, Any]],
    chat_fn: Callable[[list[dict[str, Any]]], asyncio.Future[dict[str, Any]]],
    valid_names: set[str],
    on_heartbeat: Callable[[], Any] | None = None,
    heartbeat_interval: float = 5.0,
) -> tuple[list[WaveCall], str | None]:
    """Ask the model for the next wave of parallel tool calls.

    Returns ([calls], final_answer). If final_answer is not None, no tools
    should be called and the turn is complete.
    """
    # Put the router instruction LAST so it dominates earlier system messages
    # (capability preamble, brain addendum, etc.) that might otherwise invite
    # prose explanations instead of the required JSON tool calls.
    tool_list = "\n".join(f"- {name}" for name in sorted(valid_names))
    wave_system = _WAVE_SYSTEM.format(tool_list=tool_list)
    wave_messages = messages + [{"role": "system", "content": wave_system}]
    start = time.time()
    task = asyncio.create_task(chat_fn(wave_messages))

    # Heartbeat: call on_heartbeat periodically while model thinks
    if on_heartbeat:
        last_beat = time.time()
        while not task.done():
            await asyncio.sleep(0.05)
            if time.time() - last_beat >= heartbeat_interval:
                try:
                    on_heartbeat()
                except Exception:
                    pass
                last_beat = time.time()

    raw = await task
    content = raw.get("content", "") if isinstance(raw, dict) else str(raw)
    calls, final = _parse_wave(content, valid_names)
    # Some APIs (Anthropic) return native tool_calls even when we asked for a
    # JSON wave plan. Convert them to WaveCalls as a fallback.
    if not calls and not final and isinstance(raw, dict):
        for tc in raw.get("tool_calls") or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else tc.function
            name = fn.get("name", "") if isinstance(fn, dict) else getattr(fn, "name", "")
            args = fn.get("arguments", {}) if isinstance(fn, dict) else getattr(fn, "arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if name in valid_names:
                calls.append(WaveCall(name, args, purpose=""))
    return calls, final


def _exec_sync(name: str, args: dict[str, Any], session_id: str = "") -> dict[str, Any]:
    """Synchronous tool executor wrapper."""
    return agent_tools.run_tool(name, args, allow_gated=False, session_id=session_id)


async def execute_wave(
    calls: list[WaveCall],
    *,
    seen: dict[str, int],
    session_id: str = "",
    on_tool_start: Callable[[WaveCall], Any] | None = None,
    on_tool_done: Callable[[WaveCall], Any] | None = None,
) -> WaveResult:
    """Run all calls in a wave in parallel, enforcing duplicate skipping and
    approval gates."""

    # Filter duplicates before execution
    to_run: list[WaveCall] = []
    for call in calls:
        sig = call.name + "::" + json.dumps(call.args, sort_keys=True, default=str)
        if seen.get(sig, 0) >= 1:
            call.skipped_duplicate = True
            call.result = {"ok": True, "note": "duplicate call skipped — use prior result"}
            continue
        seen[sig] = seen.get(sig, 0) + 1
        to_run.append(call)

    async def _run_one(call: WaveCall) -> None:
        async with _wave_semaphore:
            if on_tool_start:
                try:
                    on_tool_start(call)
                except Exception:
                    pass
            try:
                call.result = await asyncio.to_thread(_exec_sync, call.name, call.args, session_id)
            except Exception as exc:
                call.result = {"ok": False, "error": str(exc)[:260]}
            if on_tool_done:
                try:
                    on_tool_done(call)
                except Exception:
                    pass

    # Execute all non-duplicate calls in parallel, capped by SHIMS_MAX_PARALLEL_TOOLS.
    if to_run:
        await asyncio.gather(*[_run_one(c) for c in to_run])

    # Check for approval gates — any approval stops the whole turn
    for call in calls:
        if call.result and call.result.get("needs_approval"):
            return WaveResult(calls, stop=True, answer="", stop_reason="approval")

    # Check for runaway loops — if every call failed or all are duplicates, signal
    live_calls = [c for c in calls if not c.skipped_duplicate]
    if live_calls and all(not c.result.get("ok", True) for c in live_calls):
        return WaveResult(calls, stop=True, answer="", stop_reason="all_failed")

    return WaveResult(calls)


def build_wave_context(calls: list[WaveCall]) -> list[dict[str, Any]]:
    """Build conversation messages from wave results for the next planning turn."""
    msgs: list[dict[str, Any]] = []
    assistant_content_parts: list[str] = []
    tool_calls_json: list[dict[str, Any]] = []

    for i, call in enumerate(calls):
        purpose = call.purpose or f"call {call.name}"
        assistant_content_parts.append(f"[{i+1}] {purpose}")
        tool_calls_json.append({"tool": call.name, "args": call.args})

    msgs.append({
        "role": "assistant",
        "content": "\n".join(assistant_content_parts),
        "tool_calls": tool_calls_json,
    })

    for i, call in enumerate(calls):
        res = call.result or {"ok": False, "error": "no result"}
        # Trim large fields before putting into context
        trimmed = dict(res) if isinstance(res, dict) else {"result": str(res)}
        for k in list(trimmed.keys()):
            v = trimmed[k]
            if isinstance(v, str) and len(v) > 2000:
                trimmed[k] = v[:2000] + f"\n…[+{len(v)-2000} chars]"
        try:
            trimmed_str = json.dumps(trimmed, default=str)[:4000]
        except Exception:
            trimmed_str = str(trimmed)[:4000]
        msgs.append({
            "role": "tool",
            "name": call.name,
            "content": trimmed_str,
        })

    return msgs
