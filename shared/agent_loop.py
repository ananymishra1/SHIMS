"""The agentic reasoning loop — SHIMS Omni's "act, observe, continue" brain.

Given a user turn, the model is offered the full :mod:`shared.agent_tools` toolset
and runs a bounded **plan → call tool → read result → continue** loop until it
produces a final answer. It works two ways so it runs fully offline:

  * **Native tool-calling** (Ollama ``qwen2.5-coder`` / ``llama3.1`` / ``qwen2.5``,
    or a cloud model) — the model returns ``message.tool_calls``.
  * **JSON-action fallback** — for any model, the system prompt asks for a single
    JSON object ``{"tool": "...", "args": {...}}`` or ``{"final": "..."}``; we
    parse it. So even a small local model can drive the tools.

The loop is an **async generator of event dicts**. The backend
(``backend/app/main.py``) wraps each event as a JSONL line on the existing chat
stream, so the frontend renders tool/job/diff/approval cards inline in chat.

Gated tools (destructive shell, writes outside the repo, self-patching) are NOT
executed here — the loop emits an ``approval_request`` (reusing the backend's
pending-action system) and ends the turn. The user approves with one click (or
"yes"), the backend executes it, and the user can ask SHIMS to continue.

Agent Loop v2 enhancements:
- Persistent scratchpad memory across steps (agent_scratchpad)
- Smart context management with auto-summarization (context_manager)
- Explicit plan generation with dependency graph
- Model fallback chain (Anthropic → OpenAI → Google → Ollama)
- Replan on tool failure with reflection
- Telemetry recording for every tool and model call (agent_telemetry)
- Tool results feed back into scratchpad for structured reasoning
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncGenerator, Callable

import httpx

from .ai import extract_json_maybe
from .config import settings
from . import agent_tools

# v2 imports
from .agent_scratchpad import AgentScratchpad
from .context_manager import ContextManager
from .agent_telemetry import (
    record_tool_call,
    record_model_call,
    record_replan,
)
# v3 wave execution
from . import agent_wave

CAPABILITY_PREAMBLE = (
    "You are SHIMS Omni, a fully agentic desktop coworker with REAL hands on this machine. "
    "You are NOT a text-only assistant. You can:\n"
    "• run any shell command (shell.run) — git, builds, listing, running programs;\n"
    "• read/list/search/glob and create/edit/move/delete files ANYWHERE (fs.*);\n"
    "• run code in a sandbox (code.run) or Python snippets (desktop.run_python);\n"
    "• search and read the live web (web.search, web.fetch);\n"
    "• BROWSE THE WEB LIKE A HUMAN with a real headless browser — visit pages, click links, fill forms, take screenshots, extract data with CSS selectors (browser.visit, browser.search, browser.click, browser.extract, browser.fill_form, browser.screenshot, browser.scroll);\n"
    "• create/manage Coder projects, write files, run shell, search, install packages, git commit (coder.*);\n"
    "• hand big coding jobs to a background coder that streams its progress (coder.spawn);\n"
    "• generate, test, and apply self-evolution patch proposals (neural.*);\n"
    "• INSPECT YOUR OWN SOURCE CODE for bugs or improvements and create a validated patch proposal (self.inspect);\n"
    "• MODIFY YOUR OWN SOURCE CODE (self.patch) — backend, frontend or shared — validated in a sandbox;\n"
    "• ACCESS THE USER'S DESKTOP through the paired Desktop Bridge — screenshots, shell, files, system info (desktop.bridge);\n"
    "• check status of background tasks and list running work (task.check_status, task.list);\n"
    "• learn durable skills (skill.learn);\n"
    "• organize Gmail inbox, send emails, summarize unread mail (mailbox.*);\n"
    "• control SHIMS Enterprise bridge — batch records, QC, COA, LIMS, eBR (enterprise.*);\n\n"
    "Operating rules:\n"
    "- You are aware of the current date/time and any pending scheduled tasks.\n"
    "- Prefer DOING over describing. If the user asks you to do something on the machine, use a tool.\n"
    "- Safe/read-only actions run instantly. Risky ones (deleting/writing outside the SHIMS folder, "
    "editing your own code) will be shown to the user for one-click approval — that is expected; just call the tool.\n"
    "- If you are missing a capability, you can ADD IT to yourself with self.patch, or remember a new skill.learn — "
    "tell the user you can do this.\n"
    "- Use one tool at a time, read its result, then decide the next step. When the task is done, give a short, "
    "clear final answer describing what you did.\n"
    "- If you are missing information, SEARCH FOR IT. Do not guess.\n"
    "- If the user asks about progress of a background task, use task.list to find it, then task.check_status to report.\n"
    "- Keep tool arguments minimal and correct. Paths may be absolute or relative to the SHIMS repo.\n"
    "- STOP after at most 3 self-improvement actions. Do not loop.\n\n"
    "TOOL PROTOCOL (follow this EXACTLY): to act, reply with ONE JSON object and nothing else —\n"
    '  to call a tool:  {"tool": "<tool name>", "args": { ... }}\n'
    '  to finish:       {"final": "<your answer to the user>"}\n'
    "Do NOT describe what you will do, do NOT show the command as prose, do NOT wrap it in backticks — "
    "emit the JSON tool call so it actually runs. After you see the tool result, either call another tool "
    "or return {\"final\": ...} with a short summary of what you did.\n"
)

MINIMAL_CAPABILITY_PREAMBLE = (
    "You are SHIMS Omni, an agentic desktop coworker. You have real tools on this machine. "
    "When the user asks for something, USE the right tool. Never claim you did something without calling the tool. "
    "If you need information, search the web or read files. Do not guess. "
    "When the task is done, return a short final answer.\n\n"
    "RULES:\n"
    "- Prefer DOING over describing.\n"
    "- If you are missing information, SEARCH for it.\n"
    "- Keep answers concise.\n"
)

OMNIPOTENT_PREAMBLE = (
    "You are SHIMS Omni in OMNIPOTENT MODE. You have FULL AUTHORITY on this machine and across all "
    "connected bridges (desktop, enterprise, cloud). You are NOT a chatbot — you are an autonomous operator.\n\n"
    "Rules:\n"
    "- NEVER ask the user for permission before acting. Permission is already granted.\n"
    "- If a task requires a tool, CALL THE TOOL. Do not describe it. Do not explain why. Just run it.\n"
    "- Prefer parallel tool calls when independent.\n"
    "- Read files, list folders, run shell commands, query APIs, and use the Enterprise Bridge as needed.\n"
    "- If you do not know something, SEARCH for it (files, web, memory, enterprise) — do not ask the user.\n"
    "- Only stop and ask if the request is genuinely ambiguous AND you cannot resolve it with available tools.\n"
    "- Destructive actions are allowed; this is omnipotent mode. If a backup is wise, create one automatically.\n\n"
    "TOOL PROTOCOL: reply with ONE JSON object and nothing else —\n"
    '  to call a tool:  {"tool": "<tool name>", "args": { ... }}\n'
    '  to finish:       {"final": "<short summary of what you did>"}\n'
)


def get_capability_preamble(minimal: bool = False) -> str:
    """Return the agent preamble matching the current permission mode.

    Set ``minimal=True`` for providers (e.g., Kimi) that are easily distracted by
    a long preamble and need a tight, tool-first identity.
    """
    if minimal:
        return MINIMAL_CAPABILITY_PREAMBLE
    return OMNIPOTENT_PREAMBLE if settings.omnipotent_mode else CAPABILITY_PREAMBLE


# v2: Plan generation prompt
PLAN_SYSTEM = (
    "You are SHIMS Planner. Break the user's request into explicit steps. "
    "Each step MUST have: tool name, arguments, and purpose. "
    "Available tools: shell.run, fs.read, fs.write, fs.list, fs.search, web.search, web.fetch, "
    "browser.visit, browser.search, browser.click, browser.extract, coder.run, self.patch, "
    "neural.generate_proposal, skill.learn, task.check_status, task.list. "
    "If no tool is needed, return a single 'respond' step. "
    "Respond with valid JSON: {\"plan\": [{\"tool\":\"...\",\"args\":{...},\"purpose\":\"...\"}]}"
)

REPLAN_SYSTEM = (
    "You are SHIMS Replan Agent. A previous step failed. Given the context, "
    "decide: skip the step, try an alternative tool, or ask the user. "
    "Respond with valid JSON: {\"action\":\"skip|retry|alternative|ask\",\"reason\":\"...\",\"new_step\":{...}}"
)

# Tools exposed to the loop (everything in the registry by default).
DEFAULT_TOOLS = list(agent_tools.TOOLS.keys())

# Compact tool set for providers that struggle with the full 128-tool registry.
# Keeps the actions users actually ask for (search, browse, files, shell, code,
# enterprise bridge) while reducing noise and hallucinated tool names.
ESSENTIAL_TOOLS = [
    "shell.run",
    "fs.read",
    "fs.list",
    "fs.glob",
    "fs.search",
    "fs.write",
    "code.run",
    "web.search",
    "web.fetch",
    "browser.visit",
    "browser.search",
    "enterprise.command",
    "enterprise.status",
    "enterprise.dashboard",
]

_MAX_RESULT_CHARS = 6000

# v2: Model fallback chain (provider, model).  Local HF endpoint is tried before Ollama
# so a single self-hosted model server can serve as the preferred local backend.
FALLBACK_CHAIN: list[tuple[str, str]] = [
    ("anthropic", "claude-sonnet-4-6"),
    ("openai", "gpt-4o"),
    ("google", "gemini-1.5-pro"),
    ("huggingface", settings.huggingface_model),
    ("lmstudio", settings.lmstudio_model),
    ("ollama", settings.ollama_model),
]


def _trim_for_model(result: dict[str, Any]) -> dict[str, Any]:
    """Shrink big fields (stdout/text/diff) before feeding back to the model."""
    out: dict[str, Any] = {}
    for k, v in result.items():
        if isinstance(v, str) and len(v) > _MAX_RESULT_CHARS:
            out[k] = v[:_MAX_RESULT_CHARS] + f"\n…[+{len(v) - _MAX_RESULT_CHARS} chars]"
        else:
            out[k] = v
    return out


def _debug_log_provider(provider: str, label: str, payload: Any) -> None:
    """Write provider-specific debug dumps to logs/agent_loop_<provider>_debug.log."""
    if provider not in {"kimi", "deepseek", "qwen"}:
        return
    try:
        from pathlib import Path
        log_path = Path("logs") / f"agent_loop_{provider}_debug.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {label} ===\n")
            fh.write(json.dumps(payload, indent=2, default=str, ensure_ascii=False)[:8000])
            fh.write("\n")
    except Exception:
        pass


# ------------------------------------------------------------------ #
# v2: Unified LLM helper with telemetry
# ------------------------------------------------------------------ #

async def _llm_chat(
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    timeout: float = 180.0,
    is_fallback: bool = False,
) -> tuple[dict[str, Any], bool, float, str]:
    """Unified LLM call. Returns ({content, tool_calls}, success, latency_ms, error)."""
    start = time.time()
    success = False
    error = ""
    result: dict[str, Any] = {"content": "", "tool_calls": []}

    _debug_log_provider(provider, "request", {"model": model, "messages": messages, "tools_count": len(tools or [])})

    # Kimi in particular tends to hallucinate or emit invalid native tool calls
    # with the full registry. Force JSON wave planning by omitting the native
    # tool spec; the wave system prompt still lists valid tools.
    effective_tools = [] if provider == "kimi" else (tools or [])

    try:
        from .llm_gateway import GATEWAY_ENABLED, LLMUnavailable, gateway
        if GATEWAY_ENABLED:
            result = await gateway.chat_messages(provider, model, messages, effective_tools, timeout=timeout)
        elif provider == "anthropic":
            result = await _anthropic_chat_stream_raw(model, messages, tools or [], timeout=timeout)
        elif _is_openai_compatible(provider):
            result = await _openai_compatible_chat_raw(provider, model, messages, effective_tools, timeout=timeout)
        elif provider == "google":
            result = await _google_chat_raw(model, messages, tools or [], timeout=timeout)
        elif provider == "huggingface":
            result = await _hf_chat_raw(model, messages, tools or [], timeout=timeout)
        elif provider == "lmstudio":
            result = await _lmstudio_chat_raw(model, messages, tools or [], timeout=timeout)
        else:
            result = await _ollama_chat_raw(model, messages, tools or [], timeout=timeout)
        success = True
    except asyncio.TimeoutError:
        error = "timeout"
    except Exception as exc:
        code = getattr(exc, 'code', '')
        error = f"{code}: {str(exc)[:180]}" if code else str(exc)[:200]
    finally:
        latency = (time.time() - start) * 1000
        record_model_call(provider, model, success, latency, error=error, is_fallback=is_fallback)
    _debug_log_provider(provider, "response", {"success": success, "error": error, "result": result})
    return result, success, latency, error


async def _chat_with_fallback(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    preferred_provider: str,
    preferred_model: str,
    temperature: float = 0.2,
    timeout: float = 180.0,
) -> tuple[dict[str, Any], str, str]:
    """Try LLM with fallback chain. Returns ({content, tool_calls}, provider, model)."""
    chain = [(preferred_provider, preferred_model)] + [
        (p, m) for p, m in FALLBACK_CHAIN if (p, m) != (preferred_provider, preferred_model)
    ]

    for idx, (provider, model) in enumerate(chain):
        result, success, _, error = await _llm_chat(
            provider, model, messages, tools, temperature=temperature, timeout=timeout, is_fallback=(idx > 0)
        )
        if success and (result.get("content") or result.get("tool_calls")):
            return result, provider, model
        # Don't spam the UI with fallback errors, just log
        from .config import get_logger
        get_logger("agent_loop").warning(f"Fallback {idx+1}/{len(chain)} failed: {provider}/{model} — {error}")

    raise RuntimeError("All models in fallback chain failed")


async def _generate_plan(
    user_message: str,
    provider: str,
    model: str,
) -> list[dict[str, Any]]:
    """Ask the LLM to generate an explicit plan. Returns list of step dicts."""
    plan_messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": f"Create a plan for: {user_message}"},
    ]
    result, prov, mdl = await _chat_with_fallback(plan_messages, [], provider, model, temperature=0.1, timeout=90.0)
    content = result.get("content", "")

    plan = []
    try:
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        else:
            json_str = content.strip()
        data = json.loads(json_str)
        plan = data.get("plan", [])
    except Exception:
        pass

    if not plan:
        plan = [{"tool": "respond", "args": {"message": user_message}, "purpose": "Direct response"}]

    return plan


# ------------------------------------------------------------------ #
# LLM backends
# ------------------------------------------------------------------ #

async def _ollama_chat_raw(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout: float = 180.0) -> dict[str, Any]:
    """Non-streaming Ollama chat turn. Returns {content, tool_calls}."""
    base = settings.ollama_base_url.rstrip("/")
    payload = {"model": model, "messages": messages, "tools": tools, "stream": False,
               "options": {"temperature": 0.2}}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{base}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
    msg = data.get("message") or {}
    return {"content": msg.get("content", ""), "tool_calls": msg.get("tool_calls", [])}


async def _hf_chat_raw(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout: float = 120.0) -> dict[str, Any]:
    """Non-streaming Hugging Face endpoint chat turn. Endpoint must be OpenAI-compatible (TGI/vLLM/llama.cpp server). Returns {content, tool_calls}."""
    base = settings.huggingface_base_url.rstrip("/")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0.2,
    }
    if tools:
        payload["tools"] = tools
    headers = {"Content-Type": "application/json"}
    key = (settings.huggingface_api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{base}/v1/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    tool_calls: list[Any] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except Exception:
            args = fn.get("arguments") or {}
        tool_calls.append({"function": {"name": fn.get("name", ""), "arguments": args}})
    return {"content": content, "tool_calls": tool_calls}


async def _lmstudio_chat_raw(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout: float = 120.0) -> dict[str, Any]:
    """Non-streaming LM Studio chat turn. LM Studio exposes an OpenAI-compatible
    server (default http://127.0.0.1:1234). Returns {content, tool_calls}."""
    base = settings.lmstudio_base_url.rstrip("/")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0.2,
    }
    if tools:
        payload["tools"] = tools
    headers = {"Content-Type": "application/json"}
    key = (settings.lmstudio_api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{base}/v1/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    tool_calls: list[Any] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except Exception:
            args = fn.get("arguments") or {}
        tool_calls.append({"function": {"name": fn.get("name", ""), "arguments": args}})
    return {"content": content, "tool_calls": tool_calls}


async def _lmstudio_chat_stream(model: str, messages: list[dict[str, Any]],
                                 tools: list[dict[str, Any]], on_delta: Callable) -> dict[str, Any]:
    """Streaming LM Studio chat turn over its OpenAI-compatible SSE endpoint.
    Mirrors _ollama_chat_stream's prose-vs-JSON buffering so raw tool calls
    never flicker into the UI. Returns {content, tool_calls}."""
    base = settings.lmstudio_base_url.rstrip("/")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.2,
    }
    if tools:
        payload["tools"] = tools
    headers = {"Content-Type": "application/json"}
    key = (settings.lmstudio_api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    content = ""
    tool_calls_acc: dict[int, dict[str, Any]] = {}
    mode: str | None = None
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", f"{base}/v1/chat/completions", json=payload, headers=headers) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        obj = json.loads(data_str)
                    except Exception:
                        continue
                    choice = (obj.get("choices") or [{}])[0]
                    delta_obj = choice.get("delta") or {}
                    for tc in delta_obj.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tool_calls_acc.setdefault(idx, {"function": {"name": "", "arguments": ""}})
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
                    delta = delta_obj.get("content") or ""
                    if delta:
                        content += delta
                        if mode is None:
                            stripped = content.lstrip()
                            if stripped:
                                mode = "json" if (stripped[0] == "{" or stripped.startswith("`")) else "prose"
                                if mode == "prose":
                                    await on_delta(content)
                        elif mode == "prose":
                            await on_delta(delta)
    except Exception as exc:
        if content or tool_calls_acc:
            return {"content": content, "tool_calls": list(tool_calls_acc.values()), "truncated": True,
                    "error": str(exc)[:160]}
        from .llm_gateway import LLMUnavailable
        code = "timeout" if isinstance(exc, httpx.TimeoutException) else "stream_failed"
        raise LLMUnavailable(code, provider="lmstudio", detail=str(exc)[:200]) from exc
    final_tool_calls: list[Any] = []
    for slot in tool_calls_acc.values():
        try:
            args = json.loads(slot["function"]["arguments"] or "{}")
        except Exception:
            args = slot["function"]["arguments"] or {}
        final_tool_calls.append({"function": {"name": slot["function"]["name"], "arguments": args}})
    return {"content": content, "tool_calls": final_tool_calls}


async def _ollama_chat_stream(model: str, messages: list[dict[str, Any]],
                              tools: list[dict[str, Any]], on_delta: Callable) -> dict[str, Any]:
    """Streaming Ollama chat turn. ``on_delta(text)`` is awaited only for *prose*
    (final-answer) output — when the model is emitting a JSON tool call (content
    starts with '{' or a ``` fence) we buffer silently so the UI never flickers a
    raw tool call. Returns {content, tool_calls}."""
    base = settings.ollama_base_url.rstrip("/")
    payload = {"model": model, "messages": messages, "tools": tools, "stream": True,
               "options": {"temperature": 0.2}}
    content = ""
    tool_calls: list[Any] = []
    mode: str | None = None  # None until first non-space char decides: 'json' | 'prose'
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", f"{base}/api/chat", json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    msg = obj.get("message") or {}
                    if msg.get("tool_calls"):
                        tool_calls.extend(msg["tool_calls"])
                    delta = msg.get("content") or ""
                    if delta:
                        content += delta
                        if mode is None:
                            stripped = content.lstrip()
                            if stripped:
                                mode = "json" if (stripped[0] == "{" or stripped.startswith("`")) else "prose"
                                if mode == "prose":
                                    await on_delta(content)  # flush buffered prefix
                        elif mode == "prose":
                            await on_delta(delta)
                    if obj.get("done"):
                        break
    except Exception as exc:
        # Mid-stream disconnects must not kill the turn: surface whatever
        # arrived plus a truncation marker so callers can degrade gracefully.
        if content or tool_calls:
            return {"content": content, "tool_calls": tool_calls, "truncated": True,
                    "error": str(exc)[:160]}
        from .llm_gateway import LLMUnavailable
        code = "timeout" if isinstance(exc, httpx.TimeoutException) else "stream_failed"
        raise LLMUnavailable(code, provider="ollama", detail=str(exc)[:200]) from exc
    return {"content": content, "tool_calls": tool_calls}


async def _ollama_model_loaded(model_name: str) -> bool:
    """Check whether a model is currently loaded in Ollama (avoids cold-start latency)."""
    try:
        base = settings.ollama_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/api/ps")
            r.raise_for_status()
            data = r.json()
        for m in data.get("models", []):
            if m.get("name") == model_name or m.get("model") == model_name:
                return True
    except Exception:
        pass
    return False


async def _anthropic_chat_stream_raw(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout: float = 120.0) -> dict[str, Any]:
    """Non-streaming Anthropic chat turn. Returns {content, tool_calls}."""
    from .ai import _stored_provider, clean_secret
    stored = _stored_provider('anthropic')
    api_key = clean_secret((stored or {}).get('api_key') or getattr(settings, 'anthropic_api_key', '') or '')
    if not api_key:
        return {"content": "Anthropic API key not configured. Set ANTHROPIC_API_KEY in .env", "tool_calls": []}

    used_model = model or (stored or {}).get('default_model') or getattr(settings, 'anthropic_model', 'claude-sonnet-4-6')
    base_url = ((stored or {}).get('base_url') or 'https://api.anthropic.com/v1').rstrip('/')

    system_text = ""
    anthropic_messages = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            system_text += content + "\n"
            continue
        if role == "tool":
            anthropic_messages.append({"role": "user", "content": f"Tool result ({m.get('name', 'unknown')}): {content}"})
        else:
            anthropic_messages.append({"role": role, "content": content})

    _anthropic_name_map: dict[str, str] = {}
    anthropic_tools = []
    for t in tools:
        if isinstance(t, dict) and t.get("function"):
            fn = t["function"]
            orig_name = fn.get("name", "")
            safe_name = orig_name.replace(".", "_")
            _anthropic_name_map[safe_name] = orig_name
            anthropic_tools.append({
                "name": safe_name,
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })

    payload: dict[str, Any] = {
        "model": used_model,
        "max_tokens": settings.max_output_tokens,
        "messages": anthropic_messages,
        "stream": False,
    }
    if system_text.strip():
        payload["system"] = system_text.strip()
    if anthropic_tools:
        payload["tools"] = anthropic_tools

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{base_url}/messages", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()

    content = ""
    tool_calls: list[Any] = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            content += block.get("text", "")
        elif block.get("type") == "tool_use":
            raw_name = block.get("name", "")
            mapped_name = _anthropic_name_map.get(raw_name, raw_name)
            tool_calls.append({
                "function": {
                    "name": mapped_name,
                    "arguments": block.get("input", {}),
                }
            })

    return {"content": content, "tool_calls": tool_calls}


async def _openai_chat_raw(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout: float = 120.0) -> dict[str, Any]:
    """Non-streaming OpenAI chat turn. Returns {content, tool_calls}."""
    api_key = (settings.openai_api_key or "").strip()
    if not api_key:
        return {"content": "OpenAI API key not configured. Set OPENAI_API_KEY in .env", "tool_calls": []}

    used_model = model or settings.openai_model
    _openai_name_map: dict[str, str] = {}
    openai_tools: list[dict[str, Any]] = []
    for t in tools:
        if isinstance(t, dict) and t.get("function"):
            fn = t["function"]
            orig_name = fn.get("name", "")
            safe_name = orig_name.replace(".", "_")
            _openai_name_map[safe_name] = orig_name
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": safe_name,
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                },
            })

    payload: dict[str, Any] = {
        "model": used_model,
        "messages": messages,
        "stream": False,
        "temperature": 0.2,
    }
    if openai_tools:
        payload["tools"] = openai_tools

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()

    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    tool_calls: list[Any] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw_name = fn.get("name", "")
        mapped_name = _openai_name_map.get(raw_name, raw_name)
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except Exception:
            args = fn.get("arguments") or {}
        tool_calls.append({"function": {"name": mapped_name, "arguments": args}})

    return {"content": content, "tool_calls": tool_calls}


def _is_openai_compatible(provider: str) -> bool:
    return provider in {"openai", "kimi", "deepseek", "qwen"}


def _openai_compatible_config(provider: str) -> tuple[str, str, str]:
    """Resolve API key, base URL and default model for OpenAI-compatible providers."""
    from .ai import _stored_provider, clean_secret
    stored = _stored_provider(provider)
    if provider == "openai":
        api_key = clean_secret((stored or {}).get('api_key') or getattr(settings, 'openai_api_key', '') or '')
        base_url = ((stored or {}).get('base_url') or 'https://api.openai.com/v1').rstrip('/')
        default_model = getattr(settings, 'openai_model', 'gpt-4.1-mini')
    elif provider == "kimi":
        api_key = clean_secret((stored or {}).get('api_key') or os.getenv('KIMI_API_KEY', ''))
        base_url = ((stored or {}).get('base_url') or os.getenv('KIMI_BASE_URL', 'https://api.moonshot.ai/v1')).rstrip('/')
        default_model = os.getenv('KIMI_MODEL', 'moonshot-v1-8k')
    elif provider == "deepseek":
        api_key = clean_secret((stored or {}).get('api_key') or os.getenv('DEEPSEEK_API_KEY', ''))
        base_url = ((stored or {}).get('base_url') or os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')).rstrip('/')
        default_model = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
    elif provider == "qwen":
        api_key = clean_secret((stored or {}).get('api_key') or getattr(settings, 'qwen_api_key', '') or '')
        base_url = ((stored or {}).get('base_url') or getattr(settings, 'qwen_base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1')).rstrip('/')
        default_model = getattr(settings, 'qwen_model', 'qwen-max')
    else:
        raise ValueError(f"Unknown OpenAI-compatible provider: {provider}")
    if not api_key:
        raise ValueError(f"{provider.upper()} API key not configured")
    return api_key, base_url, default_model


def _normalize_openai_tools(tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Convert Ollama-style tool specs to OpenAI function-calling format.

    OpenAI-compatible APIs do not allow dots in function names, so we map
    them back after the model responds.
    """
    name_map: dict[str, str] = {}
    openai_tools: list[dict[str, Any]] = []
    for t in tools:
        if isinstance(t, dict) and t.get("function"):
            fn = t["function"]
            orig_name = fn.get("name", "")
            safe_name = orig_name.replace(".", "_")
            name_map[safe_name] = orig_name
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": safe_name,
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                },
            })
    return openai_tools, name_map


def _convert_messages_for_openai_compatible(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize message roles for OpenAI-compatible APIs.

    Some providers (e.g., Moonshot/Kimi) reject the non-standard ``role: tool``
    message that SHIMS uses for tool results. Convert those into user messages.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "tool":
            name = m.get("name", "unknown")
            out.append({"role": "user", "content": f"Tool result ({name}): {content}"})
        else:
            out.append({"role": role, "content": content})
    return out


async def _openai_compatible_chat_raw(
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Non-streaming OpenAI-compatible chat turn (OpenAI, Kimi, DeepSeek, Qwen).

    Returns {content, tool_calls} using the same normalization as _openai_chat_raw.
    """
    if provider == "openai":
        return await _openai_chat_raw(model, messages, tools, timeout=timeout)

    api_key, base_url, default_model = _openai_compatible_config(provider)
    used_model = model or default_model
    openai_tools, name_map = _normalize_openai_tools(tools)

    payload: dict[str, Any] = {
        "model": used_model,
        "messages": _convert_messages_for_openai_compatible(messages),
        "stream": False,
        "temperature": 0.2,
    }
    if provider == "kimi" and isinstance(used_model, str) and used_model.startswith("kimi-k2"):
        payload["temperature"] = 1
    if openai_tools:
        payload["tools"] = openai_tools

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()

    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    tool_calls: list[Any] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw_name = fn.get("name", "")
        mapped_name = name_map.get(raw_name, raw_name)
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except Exception:
            args = fn.get("arguments") or {}
        tool_calls.append({"function": {"name": mapped_name, "arguments": args}})

    return {"content": content, "tool_calls": tool_calls}


async def _openai_compatible_chat_stream(
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    on_delta: Callable,
) -> dict[str, Any]:
    """Streaming OpenAI-compatible chat turn. Returns {content, tool_calls}."""
    if provider == "openai":
        # OpenAI has its own streaming transport below, but this path is kept
        # for symmetry. In practice the final-synthesis branch prefers this
        # generic SSE handler for all OpenAI-compatible clouds.
        pass

    api_key, base_url, default_model = _openai_compatible_config(provider)
    used_model = model or default_model
    openai_tools, name_map = _normalize_openai_tools(tools)

    payload: dict[str, Any] = {
        "model": used_model,
        "messages": _convert_messages_for_openai_compatible(messages),
        "stream": True,
        "temperature": 0.2,
    }
    if provider == "kimi" and isinstance(used_model, str) and used_model.startswith("kimi-k2"):
        payload["temperature"] = 1
    if openai_tools:
        payload["tools"] = openai_tools

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    content = ""
    tool_calls: list[Any] = []
    current_tool: dict[str, Any] | None = None

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{base_url}/chat/completions", json=payload, headers=headers) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        obj = json.loads(data_str)
                    except Exception:
                        continue
                    delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                    delta_content = delta.get("content") or ""
                    if delta_content:
                        content += delta_content
                        await on_delta(delta_content)
                    for tc in delta.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        idx = tc.get("index", 0)
                        if current_tool is None or idx != current_tool.get("index"):
                            if current_tool:
                                _flush_openai_tool(current_tool, name_map, tool_calls)
                            current_tool = {
                                "index": idx,
                                "name": fn.get("name", ""),
                                "arguments": fn.get("arguments", ""),
                            }
                        else:
                            current_tool["name"] = (current_tool.get("name") or "") + (fn.get("name") or "")
                            current_tool["arguments"] = (current_tool.get("arguments") or "") + (fn.get("arguments") or "")
    except Exception as exc:
        if content or tool_calls:
            return {"content": content, "tool_calls": tool_calls, "truncated": True,
                    "error": str(exc)[:160]}
        from .llm_gateway import LLMUnavailable
        code = "timeout" if isinstance(exc, httpx.TimeoutException) else "stream_failed"
        raise LLMUnavailable(code, provider=provider, detail=str(exc)[:200]) from exc

    if current_tool:
        _flush_openai_tool(current_tool, name_map, tool_calls)

    return {"content": content, "tool_calls": tool_calls}


def _flush_openai_tool(
    current_tool: dict[str, Any],
    name_map: dict[str, str],
    tool_calls: list[Any],
) -> None:
    raw_name = current_tool.get("name", "")
    mapped_name = name_map.get(raw_name, raw_name)
    raw_args = current_tool.get("arguments") or "{}"
    try:
        args = json.loads(raw_args)
    except Exception:
        args = raw_args
    tool_calls.append({"function": {"name": mapped_name, "arguments": args}})


async def _google_chat_raw(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout: float = 120.0) -> dict[str, Any]:
    """Non-streaming Google Gemini chat turn. Returns {content, tool_calls}."""
    api_key = (settings.google_api_key or "").strip()
    if not api_key:
        return {"content": "Google API key not configured. Set GOOGLE_API_KEY in .env", "tool_calls": []}

    used_model = model or settings.gemini_model
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    system_text = ""
    contents: list[dict[str, Any]] = []
    current_role = "user"
    current_parts: list[dict[str, Any]] = []

    def _flush(role: str) -> None:
        if current_parts:
            contents.append({"role": role, "parts": current_parts})

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            system_text += content + "\n"
            continue
        mapped_role = "model" if role == "assistant" else "user"
        if mapped_role != current_role:
            _flush(current_role)
            current_role = mapped_role
            current_parts = []
        current_parts.append({"text": content})
    _flush(current_role)

    if system_text.strip() and contents:
        for c in contents:
            if c["role"] == "user":
                c["parts"].insert(0, {"text": f"System:\n{system_text.strip()}"})
                break

    _google_name_map: dict[str, str] = {}
    function_declarations: list[dict[str, Any]] = []
    for t in tools:
        if isinstance(t, dict) and t.get("function"):
            fn = t["function"]
            orig_name = fn.get("name", "")
            safe_name = orig_name.replace(".", "_")
            _google_name_map[safe_name] = orig_name
            function_declarations.append({
                "name": safe_name,
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            })

    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": 0.2},
    }
    if function_declarations:
        payload["tools"] = [{"functionDeclarations": function_declarations}]

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{base_url}/models/{used_model}:generateContent?key={api_key}",
            json=payload,
        )
        r.raise_for_status()
        data = r.json()

    candidate = (data.get("candidates") or [{}])[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    content = ""
    tool_calls: list[Any] = []
    for part in parts:
        if part.get("text"):
            content += part["text"]
        elif part.get("functionCall"):
            fc = part["functionCall"]
            raw_name = fc.get("name", "")
            mapped_name = _google_name_map.get(raw_name, raw_name)
            tool_calls.append({"function": {"name": mapped_name, "arguments": fc.get("args", {})}})

    return {"content": content, "tool_calls": tool_calls}


async def _anthropic_chat_stream(model: str, messages: list[dict[str, Any]],
                                  tools: list[dict[str, Any]], on_delta: Callable) -> dict[str, Any]:
    """Streaming Anthropic chat turn. Uses Anthropic's Messages API with streaming.
    Returns {content, tool_calls} where tool_calls are normalized to Ollama-style dicts.
    """
    from .ai import _stored_provider, clean_secret
    stored = _stored_provider('anthropic')
    api_key = clean_secret((stored or {}).get('api_key') or getattr(settings, 'anthropic_api_key', '') or '')
    if not api_key:
        return {"content": "Anthropic API key not configured. Set ANTHROPIC_API_KEY in .env", "tool_calls": []}

    used_model = model or (stored or {}).get('default_model') or getattr(settings, 'anthropic_model', 'claude-sonnet-4-6')
    base_url = ((stored or {}).get('base_url') or 'https://api.anthropic.com/v1').rstrip('/')

    # Convert Ollama-style messages to Anthropic format
    system_text = ""
    anthropic_messages = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            system_text += content + "\n"
            continue
        # Map tool result messages to user messages with context
        if role == "tool":
            anthropic_messages.append({"role": "user", "content": f"Tool result ({m.get('name', 'unknown')}): {content}"})
        else:
            anthropic_messages.append({"role": role, "content": content})

    # Convert Ollama-style tools to Anthropic format
    # Anthropic tool names must match ^[a-zA-Z0-9_-]{1,128}$ (no dots)
    _anthropic_name_map: dict[str, str] = {}
    _reverse_name_map: dict[str, str] = {}
    anthropic_tools = []
    for t in tools:
        if isinstance(t, dict) and t.get("function"):
            fn = t["function"]
            orig_name = fn.get("name", "")
            safe_name = orig_name.replace(".", "_")
            _anthropic_name_map[safe_name] = orig_name
            _reverse_name_map[orig_name] = safe_name
            anthropic_tools.append({
                "name": safe_name,
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })

    payload: dict[str, Any] = {
        "model": used_model,
        "max_tokens": settings.max_output_tokens,
        "messages": anthropic_messages,
        "stream": True,
    }
    if system_text.strip():
        payload["system"] = system_text.strip()
    if anthropic_tools:
        payload["tools"] = anthropic_tools

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    content = ""
    tool_calls: list[Any] = []
    current_tool: dict[str, Any] | None = None
    mode: str | None = None

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{base_url}/messages", json=payload, headers=headers) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        event = json.loads(data_str)
                    except Exception:
                        continue

                    ev_type = event.get("type", "")
                    if ev_type == "content_block_start":
                        block = event.get("content_block", {})
                        if block.get("type") == "tool_use":
                            raw_name = block.get("name", "")
                            mapped_name = _anthropic_name_map.get(raw_name, raw_name)
                            current_tool = {
                                "function": {
                                    "name": mapped_name,
                                    "arguments": block.get("input", {}),
                                }
                            }
                    elif ev_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                content += text
                                if mode is None:
                                    stripped = content.lstrip()
                                    if stripped:
                                        mode = "json" if (stripped[0] == "{" or stripped.startswith("`")) else "prose"
                                        if mode == "prose":
                                            await on_delta(content)
                                elif mode == "prose":
                                    await on_delta(text)
                        elif delta.get("type") == "input_json_delta":
                            partial = delta.get("partial_json", "")
                            if current_tool and partial:
                                # Accumulate partial JSON for tool input
                                if "_partial" not in current_tool["function"]:
                                    current_tool["function"]["_partial"] = ""
                                current_tool["function"]["_partial"] += partial
                    elif ev_type == "content_block_stop":
                        if current_tool:
                            # Try to parse accumulated partial JSON
                            partial = current_tool["function"].pop("_partial", "")
                            if partial:
                                try:
                                    current_tool["function"]["arguments"] = json.loads(partial)
                                except Exception:
                                    current_tool["function"]["arguments"] = {}
                            tool_calls.append(current_tool)
                            current_tool = None
    except Exception as exc:
        return {"content": f"Anthropic error: {str(exc)[:200]}", "tool_calls": []}

    return {"content": content, "tool_calls": tool_calls}


def _coerce_call(obj: Any, valid: Any = None) -> dict[str, Any] | None:
    """Accept the many shapes models emit:
    {"tool"|"name"|"function": <name>, "args"|"arguments"|"parameters": {...}} or
    {"function": {"name": ..., "arguments": ...}}. Only returns a call for a
    REAL known tool (``valid`` is a set/dict of allowed names; defaults to the
    shared registry), so {"final": ...} / plain JSON aren't mistaken for calls.
    """
    if not isinstance(obj, dict):
        return None
    target = valid if valid is not None else agent_tools.TOOLS
    name = obj.get("tool") or obj.get("name") or obj.get("function") or obj.get("action")
    args: Any = obj.get("args") or obj.get("arguments") or obj.get("parameters") or obj.get("input")
    if isinstance(name, dict):  # {"function": {"name":..., "arguments":...}}
        args = name.get("arguments") or name.get("args") or args
        name = name.get("name")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    if isinstance(name, str) and name in target:
        return {"name": name, "args": args or {}}
    return None


def _normalize_tool_calls(message: dict[str, Any], valid: Any = None) -> list[dict[str, Any]]:
    """Extract [{name, args}] from native tool_calls or a JSON-action in content."""
    calls: list[dict[str, Any]] = []
    for tc in (message.get("tool_calls") or []):
        c = _coerce_call(tc.get("function") or tc, valid)
        if c:
            calls.append(c)
    if calls:
        return calls
    # JSON-action fallback parsed from text content (handles ```json fences too).
    c = _coerce_call(extract_json_maybe((message.get("content") or "").strip()), valid)
    return [c] if c else []


def _final_from_text(message: dict[str, Any], valid: Any = None) -> str | None:
    content = (message.get("content") or "").strip()
    if not content:
        return None
    obj = extract_json_maybe(content)
    if isinstance(obj, dict):
        if "final" in obj:
            return str(obj["final"])
        if _coerce_call(obj, valid):  # it's actually a tool call, not a final answer
            return None
    return content


# ------------------------------------------------------------------ #
# Main agent loop (v2 enhanced)
# ------------------------------------------------------------------ #

async def run_agent_loop(
    *,
    message: str,
    messages: list[dict[str, Any]],
    model: str,
    provider: str = "ollama",
    router_model: str | None = None,
    router_provider: str | None = None,
    session_id: str | None,
    create_pending: Callable[..., dict[str, Any]],
    tool_names: list[str] | None = None,
    extra_tools: dict[str, Any] | None = None,
    max_steps: int = 6,
) -> AsyncGenerator[dict[str, Any], None]:
    """Drive the tool-use loop using wave execution. Yields event dicts; the last is ``{"__final__": {...}}``.

    v3 enhancements:
    - Wave-based parallel tool execution (Hermes-class speed)
    - Router/Executor split: small fast model plans waves, big model synthesizes
    - Stream keepalives during model thinking
    - Keeps v2 scratchpad, context manager, telemetry, and approval gating
    """
    import os
    tool_names = tool_names or DEFAULT_TOOLS
    extra_tools = extra_tools or {}
    specs = agent_tools.tool_specs(tool_names) + [t.spec() for t in extra_tools.values()]
    valid_names = set(tool_names) | set(extra_tools.keys())

    def _exec(name: str, args: dict[str, Any]) -> dict[str, Any]:
        from .config import settings
        if name in extra_tools:
            t = extra_tools[name]
            risk = "gated"
            try:
                risk = t.risk(args)
            except Exception:
                risk = "gated"
            if risk == "gated" and not settings.omnipotent_mode:
                return {"ok": True, "needs_approval": True, "risk": "gated", "tool": name, "args": args,
                        "title": name, "summary": ((t.description or name)[:140] + " · " + json.dumps(args, default=str)[:120])}
            return t.run(args)
        return agent_tools.run_tool(name, args, allow_gated=settings.omnipotent_mode, session_id=session_id)

    # v3: Router model selection — smallest/fastest tool-capable model
    explicit_router = bool(router_model)
    router_provider = (router_provider or provider).strip().lower()
    router_model = (router_model or model).strip()
    router_override = os.getenv("SHIMS_ROUTER_MODEL", "").strip()
    if router_override and not explicit_router:
        router_model = router_override
        if router_model.startswith("claude-"):
            router_provider = "anthropic"
        elif router_model.startswith(("gpt-", "o1", "o3")):
            router_provider = "openai"
        elif router_model.startswith("gemini-"):
            router_provider = "google"
        else:
            router_provider = "ollama"
    elif provider == "ollama" and not explicit_router:
        # Prefer small fast Ollama models for routing; executor stays on the big model
        fast_prefs = ["qwen2.5:3b", "qwen2.5-coder:3b", "qwen2.5:1.8b", "mistral:7b"]
        # We can't query installed names here without circular imports; just set preference
        router_model = os.getenv("SHIMS_ROUTER_MODEL", fast_prefs[0])

    # v2: Initialize scratchpad and context manager
    scratchpad = AgentScratchpad(session_id)
    ctx = ContextManager(max_tokens=12000, summary_trigger=8000)
    ctx.set_original_request(message)

    # v2: Seed context manager with existing conversation history
    for m in messages:
        if m.get("role") in ("user", "assistant", "tool"):
            ctx.add_turn(m["role"], m.get("content", ""), name=m.get("name"))

    convo = list(messages)

    # v3: Inject learned skills / dynamic tool list into system prompt
    try:
        from .skill_runtime import skill_prompt_block, register_all_skill_tools
        register_all_skill_tools()
        skill_block = skill_prompt_block(message, limit=3)
        if skill_block:
            if convo and convo[0].get("role") == "system":
                convo[0]["content"] = convo[0].get("content", "") + "\n\n" + skill_block
            else:
                convo.insert(0, {"role": "system", "content": skill_block})
    except Exception:
        pass

    # v4: Inject hot-reloadable cortex prompt overlay + learned behavior signals.
    # The behavior engine is the "small model predicts, LLM acts" bridge: it
    # turns observed usage patterns into context the LLM can act on proactively.
    try:
        _extra_blocks: list[str] = []
        try:
            from .cortex import get_prompt_overlay
            overlay = get_prompt_overlay()
            if overlay:
                _extra_blocks.append(overlay)
        except Exception:
            pass
        try:
            from .behavior_engine import get_behavior_engine
            beng = get_behavior_engine(session_id or "default")
            beng.record("chat_turn", context=(message or "")[:60])
            bblock = beng.to_context()
            if bblock:
                _extra_blocks.append(bblock)
        except Exception:
            pass
        if _extra_blocks:
            joined = "\n\n".join(_extra_blocks)
            if convo and convo[0].get("role") == "system":
                convo[0]["content"] = convo[0].get("content", "") + "\n\n" + joined
            else:
                convo.insert(0, {"role": "system", "content": joined})
    except Exception:
        pass

    used_tools: list[str] = []
    answer = ""
    answer_streamed = False
    jobs: list[dict[str, Any]] = []

    yield {"type": "thought", "stage": "agent", "content": "Agentic mode activated. Analyzing request..."}

    # v3: Explicit plan generation (uses executor model; skipped for Ollama)
    plan: list[dict[str, Any]] = []
    if provider != "ollama":
        try:
            plan = await _generate_plan(message, provider, model)
            scratchpad.set_plan(plan)
            yield {"type": "plan", "steps": plan}
        except Exception as exc:
            yield {"type": "thought", "stage": "agent", "content": f"Plan generation failed: {str(exc)[:160]}. Proceeding without explicit plan."}
    else:
        yield {"type": "thought", "stage": "agent", "content": "Using inline reasoning (plan generation skipped for local model speed)."}

    yield {"type": "thought", "stage": "agent", "content": "Router is planning tool waves."}

    seen: dict[str, int] = {}
    max_waves = max(2, max_steps // 2)
    wave = 0
    stop_reason = ""

    async def _router_chat(wave_messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Fast model for wave planning. Returns dict or raises on failure."""
        nonlocal router_provider, router_model
        split_mode = os.getenv("SHIMS_WAVE_ROUTER_SPLIT", "auto").strip().lower()
        if split_mode == "never":
            raise RuntimeError("router_disabled")
        # Ollama cold-start on consumer hardware can take 60-180 s per model swap.
        # In auto mode, only use a separate router model if it is already loaded.
        if split_mode == "auto" and router_provider == "ollama" and router_model != model:
            if not await _ollama_model_loaded(router_model):
                raise RuntimeError("router_not_loaded")
        # Use non-streaming raw chat for speed
        try:
            result, success, _, error = await _llm_chat(router_provider, router_model, wave_messages, specs, temperature=0.2, timeout=15.0)
            if success and (result.get("content") is not None or result.get("tool_calls")):
                return result
        except Exception:
            pass
        # Fallback to executor model if router fails
        raise RuntimeError("router_failed")

    async def _executor_chat(wave_messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Larger model for wave planning when router fails."""
        try:
            result, success, _, _ = await _llm_chat(provider, model, wave_messages, specs, temperature=0.2, timeout=25.0)
            if success and (result.get("content") is not None or result.get("tool_calls")):
                return result
        except Exception:
            pass
        return {"content": "", "tool_calls": []}

    async def _executor_synthesis(synth_messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Larger model for final answer synthesis."""
        try:
            if provider == "anthropic":
                return await _anthropic_chat_stream_raw(model, synth_messages, [])
            elif _is_openai_compatible(provider):
                return await _openai_compatible_chat_raw(provider, model, synth_messages, [], timeout=120.0)
            elif provider == "huggingface":
                return await _hf_chat_raw(model, synth_messages, [], timeout=120.0)
            elif provider == "lmstudio":
                return await _lmstudio_chat_raw(model, synth_messages, [], timeout=120.0)
            else:
                return await _ollama_chat_raw(model, synth_messages, [], timeout=120.0)
        except Exception as exc:
            return {"content": f"Synthesis failed: {str(exc)[:200]}", "tool_calls": []}

    for wave in range(1, max_waves + 1):
        # Keep context bounded
        managed_messages = ctx.to_messages(system_text=convo[0].get("content", ""))
        convo = managed_messages

        yield {"type": "thought", "stage": "plan", "content": f"Wave {wave}: routing parallel tool calls..."}

        # v3: Plan wave with router (fast) — includes heartbeat via on_heartbeat callback
        def _heartbeat():
            # This is a no-op because _llm_chat is not async-generator based here;
            # keepalive is handled in the streaming chat functions used elsewhere.
            pass

        wave_convo = convo + [{"role": "user", "content": f"ORIGINAL REQUEST (never forget): {message}"}]
        try:
            calls, final = await agent_wave.plan_wave(wave_convo, _router_chat, valid_names, on_heartbeat=_heartbeat, heartbeat_interval=5.0)
        except Exception:
            yield {"type": "thought", "stage": "agent", "content": "Router model unavailable, falling back to executor for wave planning."}
            try:
                calls, final = await agent_wave.plan_wave(wave_convo, _executor_chat, valid_names, on_heartbeat=_heartbeat, heartbeat_interval=5.0)
            except Exception as exc:
                yield {"type": "error", "code": getattr(exc, "code", "wave_planning_failed"),
                       "provider": getattr(exc, "provider", provider),
                       "message": f"Wave planning failed: {str(exc)[:160]}", "retryable": True}
                stop_reason = "planning_failed"
                break

        if final is not None:
            answer = final
            break

        if not calls:
            stop_reason = "empty_wave"
            break

        # v3: Emit tool_call events before parallel execution
        for i, call in enumerate(calls):
            used_tools.append(call.name)
            try:
                from .behavior_engine import get_behavior_engine
                get_behavior_engine(session_id or "default").record(call.name, context="tool")
            except Exception:
                pass
            yield {"type": "tool_call", "tool": call.name, "args": call.args, "step": wave, "index": i}
            yield {"type": "thought", "stage": "tool", "content": f"Wave {wave} · {call.name}: {call.purpose or 'executing'}"}

        # v3: Execute wave in parallel
        def _on_start(c: agent_wave.WaveCall):
            pass

        def _on_done(c: agent_wave.WaveCall):
            pass

        result = await agent_wave.execute_wave(calls, seen=seen, session_id=session_id or "", on_tool_start=_on_start, on_tool_done=_on_done)

        if result.stop and result.stop_reason == "approval":
            # Handle first approval found
            for call in result.calls:
                if call.result and call.result.get("needs_approval"):
                    res = call.result
                    name = call.name
                    args = call.args
                    if name == "self.patch" and res.get("proposal_id"):
                        pending = create_pending(
                            action_type="evolution_apply",
                            title=f"Apply self-patch to {res.get('path')}",
                            summary=f"SHIMS proposes editing its own file {res.get('path')}. Validation: {res.get('validation', {}).get('status')}.",
                            payload={"proposal_id": res["proposal_id"], "path": res.get("path")},
                            session_id=session_id,
                        )
                        yield {"type": "patch_proposal", "path": res.get("path"),
                               "proposal_id": res["proposal_id"], "diff": res.get("diff", ""),
                               "validation": res.get("validation"), "approval": pending}
                        answer = (f"I prepared a change to my own code ({res.get('path')}) and validated it"
                                  f" ({res.get('validation', {}).get('status')}). Approve it to apply.")
                    else:
                        pending = create_pending(
                            action_type="agent_tool",
                            title=res.get("title") or f"Run {name}",
                            summary=res.get("summary") or f"Run {name}",
                            payload={"tool": name, "args": args},
                            session_id=session_id,
                        )
                        yield {"type": "approval_request", "tool": name, "args": args, "approval": pending}
                        answer = f"That needs your approval: {res.get('summary') or name}. Approve to run it."
                    stop_reason = "approval"
                    break
            break

        # v3: Emit tool_result events and update context/scratchpad
        any_success = False
        for i, call in enumerate(result.calls):
            res = call.result or {"ok": False, "error": "no result"}
            tool_success = bool(res.get("ok", True)) and not res.get("needs_approval")
            record_tool_call(call.name, tool_success, session_id=session_id or "")
            scratchpad.observe(wave - 1, call.name, call.args, {
                "success": tool_success,
                "result": res if tool_success else None,
                "error": res.get("error", "") if not tool_success else "",
            })
            ctx.add_turn("tool", f"[{call.name}] {json.dumps(res, default=str)[:500]}", name=call.name)
            yield {"type": "tool_result", "tool": call.name, "ok": bool(res.get("ok", True)),
                   "result": _trim_for_model(res), "step": wave, "index": i}
            if call.name == "coder.spawn" and res.get("job_id"):
                job = {"job_id": res["job_id"], "name": res.get("name"), "goal": res.get("goal"),
                       "stream_url": res.get("stream_url")}
                jobs.append(job)
                yield {"type": "job", "job": job}
            if tool_success:
                any_success = True

        # Append assistant + all tool results to convo for next wave
        convo.extend(agent_wave.build_wave_context(result.calls))

        if result.stop and result.stop_reason == "all_failed":
            stop_reason = "all_failed"
            yield {"type": "thought", "stage": "agent", "content": "All tools in this wave failed. Stopping."}
            break

        if not any_success:
            # Nothing useful happened — prevent infinite loops
            stop_reason = "no_progress"
            yield {"type": "thought", "stage": "agent", "content": "No successful tool calls in this wave. Stopping."}
            break

    # Auto-reflection on failure: if any tool failed, record failure pattern for learning
    if stop_reason in {"all_failed", "no_progress"}:
        try:
            from .plan_learning import record_plan_failure
            failed_tools = [call.name for call in result.calls if not (call.result or {}).get("ok", True)]
            record_plan_failure(
                session_id or "agent-loop",
                reason=f"Wave {wave}: tools failed: {', '.join(failed_tools)}"
            )
        except Exception:
            pass

    if not answer and stop_reason not in {"approval", "planning_failed"}:
        # Ended without a natural final answer (step limit / stuck repeating a call).
        # Make one no-tools call (streamed) so the user still gets a real answer.
        yield {"type": "thought", "stage": "agent", "content": "Synthesizing final answer from tool results."}
        if used_tools:
            synthesis_instruction = (
                "Stop calling tools. Using ONLY the tool results above, answer the user's original request "
                "directly and specifically in plain text. Do not mention tools, steps, or repetition."
            )
        else:
            synthesis_instruction = (
                "No tools were successfully run for this request. Answer the user's original request "
                "directly in plain text. Do not describe, explain, or mention tool calls."
            )
        convo.append({"role": "user", "content": f"{synthesis_instruction}\n\nUser's request: {message}"})
        fin_pending: list[str] = []
        fin_streamed = False

        async def _fin_delta(t: str) -> None:
            nonlocal fin_streamed
            fin_streamed = True
            fin_pending.append(t)

        try:
            if provider == "anthropic":
                ftask = asyncio.create_task(_anthropic_chat_stream(model, convo, [], _fin_delta))
                while not ftask.done():
                    while fin_pending:
                        yield {"type": "token", "content": fin_pending.pop(0)}
                    await asyncio.sleep(0.02)
                while fin_pending:
                    yield {"type": "token", "content": fin_pending.pop(0)}
                fmsg = await ftask
                answer = (fmsg.get("content") or "").strip()
            elif _is_openai_compatible(provider):
                # OpenAI-compatible cloud providers (Kimi, DeepSeek, Qwen) often have
                # fragile SSE streaming. Use the non-streaming raw chat for final
                # synthesis so a stream parse/timeout error does not throw away all
                # the tool work already done.
                fmsg, success, _, err = await _llm_chat(provider, model, convo, [], temperature=0.2, timeout=120.0)
                if not success:
                    answer = f"Final answer synthesis failed: {err}"
                else:
                    answer = (fmsg.get("content") or "").strip()
            elif provider == "lmstudio":
                ftask = asyncio.create_task(_lmstudio_chat_stream(model, convo, [], _fin_delta))
                while not ftask.done():
                    while fin_pending:
                        yield {"type": "token", "content": fin_pending.pop(0)}
                    await asyncio.sleep(0.02)
                while fin_pending:
                    yield {"type": "token", "content": fin_pending.pop(0)}
                fmsg = await ftask
                answer = (fmsg.get("content") or "").strip()
            else:
                ftask = asyncio.create_task(_ollama_chat_stream(model, convo, [], _fin_delta))
                while not ftask.done():
                    while fin_pending:
                        yield {"type": "token", "content": fin_pending.pop(0)}
                    await asyncio.sleep(0.02)
                while fin_pending:
                    yield {"type": "token", "content": fin_pending.pop(0)}
                fmsg = await ftask
                answer = (fmsg.get("content") or "").strip()
            if not answer or answer.lower() in {"done", "done.", "(done)"}:
                answer = (
                    "I could not produce a useful final answer from the model response. "
                    "Please try again with a more specific request."
                )
            answer_streamed = fin_streamed or not _is_openai_compatible(provider)
        except Exception as exc:
            import logging
            logging.getLogger("agent_loop").error(
                f"Final synthesis failed for {provider}/{model}: {exc}", exc_info=True
            )
            answer = "I ran the tools above but couldn't compose a final summary."

    if not answer and stop_reason == "planning_failed":
        answer = ("The AI engine is unreachable right now, so I could not work on this. "
                  "Check the AI health indicator (Ollama running? cloud key configured?) and retry.")

    # v2: Reflection on completed work — bounded so a dead provider can't hang the turn,
    # and skipped entirely when the LLM is already known to be down.
    if used_tools and stop_reason != "planning_failed":
        try:
            reflection_messages = [
                {"role": "system", "content": "You are SHIMS Reflection Agent. Review the completed work and note what went well, what failed, and what to remember for next time. Be concise."},
                {"role": "user", "content": scratchpad.to_prompt()},
            ]
            refl_result, _, _ = await _chat_with_fallback(reflection_messages, [], provider, model, temperature=0.3, timeout=25.0)
            reflection = refl_result.get("content", "").strip()
            if reflection:
                scratchpad.add_note(f"Reflection: {reflection}")
                yield {"type": "thought", "stage": "agent", "content": f"📝 Reflection: {reflection}"}
        except Exception:
            pass

    # v2: Save scratchpad
    try:
        scratchpad.save()
    except Exception:
        pass

    if answer and not answer_streamed:
        yield {"type": "token", "content": answer}
    yield {"__final__": {"answer": answer, "route": "agent-loop", "waves": wave,
                         "tools_used": used_tools, "jobs": jobs}}
