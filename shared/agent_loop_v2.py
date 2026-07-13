"""SHIMS Agent Loop v2 — omnipotent multi-step execution with memory.

New capabilities:
- Persistent scratchpad memory across steps
- Automatic context summarization for long sessions
- Explicit plan generation with dependency graph
- Model fallback chain (Claude → OpenAI → Google → Ollama)
- Replan on tool failure with reflection
- Telemetry recording for every call
- Tool results feed back into scratchpad
"""
from __future__ import annotations

import json
import asyncio
import random
import time
import traceback
from typing import Any, AsyncGenerator, Callable

from . import config, agent_tools, agent_scratchpad, context_manager
from .agent_scratchpad import AgentScratchpad
from .context_manager import ContextManager
from .agent_telemetry import (
    record_tool_call,
    record_model_call,
    record_replan,
    record_unhandled_pattern,
)

logger = config.get_logger("agent_loop_v2")

# ------------------------------------------------------------------ #
# Plan generation
# ------------------------------------------------------------------ #

PLAN_SYSTEM = (
    "You are SHIMS Planner. The user wants a task done. Break it into explicit steps. "
    "Each step MUST have: tool name, arguments, and purpose. "
    "Use only these tools: file_search, read_file, write_file, list_directory, run_shell_command, "
    "web_search, code_review, analyze_image, read_pdf, send_email, browser_navigate, browser_read, "
    "create_patch. "
    "If no tool is needed, return a single 'respond' step. "
    "Respond with valid JSON: {\"plan\": [{\"tool\":\"...\",\"args\":{...},\"purpose\":\"...\"}]}"
)

REPLAN_SYSTEM = (
    "You are SHIMS Replan Agent. A previous step failed. Given the scratchpad context, "
    "decide: skip the step, try an alternative tool, or ask the user. "
    "Respond with valid JSON: {\"action\":\"skip|retry|alternative|ask\",\"reason\":\"...\",\"new_step\":{...}}"
)

REFLECTION_SYSTEM = (
    "You are SHIMS Reflection Agent. Review the completed plan and scratchpad. "
    "What went wrong? What could be improved? What knowledge should be remembered? "
    "Respond concisely."
)


async def _llm_chat(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    timeout: float = 60.0,
    is_fallback: bool = False,
) -> tuple[str, bool, float, str]:
    """Unified LLM call. Returns (content, success, latency_ms, error)."""
    start = time.time()
    success = False
    content = ""
    error = ""

    try:
        if provider == "anthropic":
            content = await _call_anthropic(model, messages, temperature, timeout)
        elif provider == "openai":
            content = await _call_openai(model, messages, temperature, timeout)
        elif provider == "google":
            content = await _call_google(model, messages, temperature, timeout)
        elif provider == "ollama":
            content = await _call_ollama(model, messages, temperature, timeout)
        else:
            raise ValueError(f"Unknown provider: {provider}")
        success = True
    except asyncio.TimeoutError:
        error = "timeout"
    except Exception as exc:
        error = str(exc)[:200]
        logger.warning(f"LLM call failed: {provider}/{model} — {error}")
    finally:
        latency = (time.time() - start) * 1000
        record_model_call(provider, model, success, latency, error=error, is_fallback=is_fallback)
        return content, success, latency, error


async def _call_anthropic(model: str, messages: list[dict[str, str]], temperature: float, timeout: float) -> str:
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise RuntimeError("anthropic not installed")
    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    # Convert messages to Anthropic format
    system_text = ""
    anthropic_messages = []
    for m in messages:
        if m["role"] == "system":
            system_text += m["content"] + "\n"
        else:
            anthropic_messages.append({"role": m["role"], "content": m["content"]})
    resp = await asyncio.wait_for(
        client.messages.create(
            model=model,
            max_tokens=4000,
            temperature=temperature,
            system=system_text.strip(),
            messages=anthropic_messages,
        ),
        timeout=timeout,
    )
    return resp.content[0].text if resp.content else ""


async def _call_openai(model: str, messages: list[dict[str, str]], temperature: float, timeout: float) -> str:
    try:
        import openai
    except ImportError:
        raise RuntimeError("openai not installed")
    client = openai.AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    resp = await asyncio.wait_for(
        client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=4000,
        ),
        timeout=timeout,
    )
    return resp.choices[0].message.content or ""


async def _call_google(model: str, messages: list[dict[str, str]], temperature: float, timeout: float) -> str:
    try:
        import google.generativeai as genai
    except ImportError:
        raise RuntimeError("google-generativeai not installed")
    genai.configure(api_key=config.GOOGLE_API_KEY)
    client = genai.GenerativeModel(model)
    convo = client.start_chat(history=[])
    # Flatten to single prompt for simplicity
    prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages if m["role"] != "system")
    resp = await asyncio.wait_for(
        asyncio.to_thread(convo.send_message, prompt),
        timeout=timeout,
    )
    return resp.text or ""


async def _call_ollama(model: str, messages: list[dict[str, str]], temperature: float, timeout: float) -> str:
    import aiohttp
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": 4000},
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{config.OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            data = await resp.json()
            return data.get("message", {}).get("content", "")


# ------------------------------------------------------------------ #
# Model fallback chain
# ------------------------------------------------------------------ #

FALLBACK_CHAIN: list[tuple[str, str]] = [
    ("anthropic", "claude-sonnet-4-6"),
    ("openai", "gpt-4o"),
    ("google", "gemini-1.5-pro"),
    ("ollama", "llama3.2:latest"),
]


async def _chat_with_fallback(
    messages: list[dict[str, str]],
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    temperature: float = 0.2,
    timeout: float = 60.0,
) -> tuple[str, str, str]:
    """Try LLM with fallback chain. Returns (content, provider, model)."""
    chain = FALLBACK_CHAIN[:]
    if preferred_provider and preferred_model:
        chain.insert(0, (preferred_provider, preferred_model))

    for idx, (provider, model) in enumerate(chain):
        content, success, _, error = await _llm_chat(
            provider, model, messages, temperature=temperature, timeout=timeout,
            is_fallback=(idx > 0),
        )
        if success and content.strip():
            return content, provider, model
        logger.warning(f"Fallback {idx+1}/{len(chain)} failed: {provider}/{model} — {error}")

    raise RuntimeError("All models in fallback chain failed")


# ------------------------------------------------------------------ #
# Tool execution
# ------------------------------------------------------------------ #

def _get_tool_fn(tool_name: str) -> Callable | None:
    """Resolve a tool name to its function."""
    # Map common aliases
    aliases = {
        "file_search": "file_search",
        "search_files": "file_search",
        "read_file": "read_file",
        "write_file": "write_file",
        "list_directory": "list_directory",
        "run_shell": "run_shell_command",
        "shell": "run_shell_command",
        "web_search": "web_search",
        "browser_navigate": "browser_navigate",
        "browser_read": "browser_read",
        "code_review": "code_review",
        "analyze_image": "analyze_image",
        "read_pdf": "read_pdf",
        "create_patch": "create_patch",
        "patch": "create_patch",
    }
    name = aliases.get(tool_name, tool_name)
    fn = getattr(agent_tools, name, None)
    return fn


async def _execute_tool(
    tool_name: str,
    args: dict[str, Any],
    session_id: str,
    req_context: dict[str, Any],
) -> tuple[Any, bool, str]:
    """Execute a single tool. Returns (result, success, error)."""
    fn = _get_tool_fn(tool_name)
    if fn is None:
        return None, False, f"Unknown tool: {tool_name}"

    start = time.time()
    try:
        # Inject session_id and req_context if function accepts them
        import inspect
        sig = inspect.signature(fn)
        if "session_id" in sig.parameters:
            args = dict(args)
            args["session_id"] = session_id
        if "req" in sig.parameters:
            args = dict(args)
            args["req"] = req_context

        if asyncio.iscoroutinefunction(fn):
            result = await fn(**args)
        else:
            result = await asyncio.to_thread(fn, **args)

        latency = (time.time() - start) * 1000
        record_tool_call(tool_name, True, latency, session_id=session_id)
        return result, True, ""
    except Exception as exc:
        latency = (time.time() - start) * 1000
        error = f"{type(exc).__name__}: {exc}"
        record_tool_call(tool_name, False, latency, error=error, session_id=session_id)
        return None, False, error


# ------------------------------------------------------------------ #
# Plan generation
# ------------------------------------------------------------------ #

async def generate_plan(
    user_message: str,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Ask the LLM to generate a plan. Returns list of step dicts."""
    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": f"Create a plan for: {user_message}"},
    ]
    content, prov, mdl = await _chat_with_fallback(messages, provider, model, temperature=0.1)

    # Try to extract JSON
    plan = []
    try:
        # Find JSON in markdown code blocks or raw
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

    # Fallback: single respond step
    if not plan:
        plan = [{"tool": "respond", "args": {"message": user_message}, "purpose": "Direct response"}]

    return plan


# ------------------------------------------------------------------ #
# Main agent loop
# ------------------------------------------------------------------ #

async def run_agent_loop_v2(
    req: Any,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    max_steps: int = 10,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run the omnipotent agent loop v2.

    Yields event dicts:
    - {type:"thought", step:N, content:"..."}
    - {type:"tool_call", step:N, tool:"...", args:{...}}
    - {type:"tool_result", step:N, result:...}
    - {type:"plan", steps:[...]}
    - {type:"replan", failed_step:N, reason:"...", new_plan:[...]}
    - {type:"final", content:"..."}
    - {type:"error", content:"..."}
    """
    session_id = getattr(req, "session_id", "default")
    user_message = getattr(req, "message", "")
    privacy = getattr(req, "privacy", "low")

    # Determine provider based on privacy
    if privacy == "high" or not config.CLOUD_AVAILABLE:
        preferred_provider = "ollama"
        preferred_model = preferred_model or "llama3.2:latest"

    # Initialize scratchpad and context manager
    scratchpad = AgentScratchpad(session_id)
    ctx = ContextManager(max_tokens=12000, summary_trigger=8000)
    ctx.set_original_request(user_message)

    yield {"type": "thought", "step": 0, "content": f"🧠 Analyzing: {user_message[:80]}..."}

    # Generate plan
    try:
        plan = await generate_plan(user_message, preferred_provider, preferred_model)
    except Exception as exc:
        yield {"type": "error", "content": f"Plan generation failed: {exc}"}
        return

    scratchpad.set_plan(plan)
    yield {"type": "plan", "steps": plan}

    # Execute plan steps
    step_idx = 0
    replan_count = 0
    max_replans = 3

    while step_idx < len(plan) and step_idx < max_steps:
        step = plan[step_idx]
        tool_name = step.get("tool", "respond")
        args = step.get("args", {})
        purpose = step.get("purpose", "")

        yield {"type": "thought", "step": step_idx + 1, "content": f"Step {step_idx+1}: {purpose}"}
        yield {"type": "tool_call", "step": step_idx + 1, "tool": tool_name, "args": args}

        # Execute tool
        if tool_name == "respond":
            # Final response step
            final_content = args.get("message", user_message)
            ctx.add_turn("assistant", final_content)
            scratchpad.observe(step_idx, tool_name, args, final_content)
            yield {"type": "final", "content": final_content}
            break

        result, success, error = await _execute_tool(
            tool_name, args, session_id, {"message": user_message, "privacy": privacy}
        )

        scratchpad.observe(step_idx, tool_name, args, {"success": success, "result": result, "error": error})
        ctx.add_turn("tool", f"[{tool_name}] {json.dumps(result, default=str)[:500]}")

        if success:
            yield {"type": "tool_result", "step": step_idx + 1, "result": result}
            step_idx += 1
        else:
            yield {"type": "tool_result", "step": step_idx + 1, "result": None, "error": error}

            # Replan if we haven't exceeded max replans
            if replan_count < max_replans:
                yield {"type": "thought", "step": step_idx + 1, "content": f"⚠️ Step failed: {error}. Replanning..."}

                replan_messages = [
                    {"role": "system", "content": REPLAN_SYSTEM},
                    {"role": "user", "content": f"Scratchpad:\n{scratchpad.to_prompt()}\n\nFailed step: {step}\nError: {error}"},
                ]
                try:
                    replan_content, _, _ = await _chat_with_fallback(replan_messages, preferred_provider, preferred_model)
                    replan_data = json.loads(replan_content)
                    action = replan_data.get("action", "skip")
                    if action == "skip":
                        step_idx += 1
                    elif action == "retry":
                        pass  # Retry same step
                    elif action == "alternative":
                        new_step = replan_data.get("new_step", {})
                        plan[step_idx] = new_step
                    elif action == "ask":
                        yield {"type": "final", "content": f"I need your help: {replan_data.get('reason', 'Step failed')}"}
                        return
                    replan_count += 1
                    record_replan(step_idx, tool_name, error, replan_data.get("reason", ""), session_id)
                except Exception:
                    # Default: skip the failed step
                    step_idx += 1
                    replan_count += 1
            else:
                yield {"type": "thought", "step": step_idx + 1, "content": f"❌ Step failed too many times: {error}"}
                step_idx += 1

    else:
        # Loop exhausted without hitting 'respond'
        # Generate final summary from scratchpad
        summary_messages = [
            {"role": "system", "content": "Summarize what was accomplished based on the scratchpad."},
            {"role": "user", "content": scratchpad.to_prompt()},
        ]
        try:
            summary, _, _ = await _chat_with_fallback(summary_messages, preferred_provider, preferred_model)
            yield {"type": "final", "content": summary}
        except Exception:
            yield {"type": "final", "content": "I completed the requested tasks. See the results above."}

    # Reflection / learning
    reflection_messages = [
        {"role": "system", "content": REFLECTION_SYSTEM},
        {"role": "user", "content": scratchpad.to_prompt()},
    ]
    try:
        reflection, _, _ = await _chat_with_fallback(reflection_messages, preferred_provider, preferred_model, temperature=0.3)
        scratchpad.add_note(f"Reflection: {reflection}")
        yield {"type": "thought", "step": -1, "content": f"📝 Reflection: {reflection}"}
    except Exception:
        pass

    scratchpad.save()

    # Record if no tool matched anything useful
    if not any(step.get("tool") != "respond" for step in plan):
        record_unhandled_pattern(user_message[:100])


# ------------------------------------------------------------------ #
# Compatibility shim for old agent_loop
# ------------------------------------------------------------------ #

async def run_agent_loop(
    req: Any,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Delegate to v2. Maintains backward compatibility."""
    async for event in run_agent_loop_v2(req, preferred_provider, preferred_model):
        yield event
