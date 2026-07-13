"""LangGraph-style state machine for SHIMS Omni agent turns.

The graph is intentionally simple so it can be inspected, tested, and iterated
without dragging in LangGraph as a dependency. Nodes are async functions that
mutate a shared AgentState and yield events. Conditional edges are plain Python
functions.

Nodes:
  router       -> classify intent and load context
  memory_load  -> retrieve memories / RAG context
  research     -> web search, fetch, summarize, store facts
  automation   -> ReAct loop using shell/code/coder/self.patch/etc.
  synthesis    -> compose final answer from all gathered context
  memory_save  -> persist durable facts and skills

Usage:
    graph = AgentGraph()
    async for event in graph.run(state):
        yield event
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncGenerator, Callable

from .agent_state import (
    AgentState,
    add_memory_update,
    append_research_summary,
    append_tool_output,
    increment_react_iterations,
    set_research_context,
)
from .agent_reasoning import ReasoningStream


GraphNode = Callable[[AgentState, ReasoningStream], AsyncGenerator[dict[str, Any], None]]
EdgeFn = Callable[[AgentState], str]


class AgentGraph:
    """State-machine graph for one agent turn."""

    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {
            "router": _node_router,
            "memory_load": _node_memory_load,
            "research": _node_research,
            "automation": _node_automation,
            "synthesis": _node_synthesis,
            "memory_save": _node_memory_save,
        }
        self.edges: dict[str, EdgeFn] = {
            "router": _edge_after_router,
            "memory_load": _edge_after_memory_load,
            "research": _edge_after_research,
            "automation": _edge_after_automation,
            "synthesis": _edge_after_synthesis,
        }
        self.tool_runner: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None
        self.chat_runner: Callable[..., Any] | None = None

    def set_tool_runner(
        self,
        runner: Callable[[str, dict[str, Any], str], dict[str, Any]],
    ) -> None:
        """Set the function used to execute agent tools.

        Signature: runner(tool_name, args, session_id) -> dict.
        """
        self.tool_runner = runner

    def set_chat_runner(self, runner: Callable[..., Any]) -> None:
        """Set the async function used for LLM chat calls."""
        self.chat_runner = runner

    async def run(
        self,
        state: AgentState,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute the graph from the router node until END."""
        reasoning = ReasoningStream(state)
        node = "router"
        max_steps = 20
        steps = 0

        async for ev in reasoning.emit("agent", "Agent graph initialized. Routing turn..."):
            yield ev

        while node != "end" and steps < max_steps:
            steps += 1
            fn = self.nodes.get(node)
            if fn is None:
                async for ev in reasoning.emit("agent", f"Unknown graph node: {node}. Stopping."):
                    yield ev
                break

            async for ev in fn(state, reasoning, self):
                yield ev

            edge_fn = self.edges.get(node)
            if edge_fn is None:
                break
            node = edge_fn(state)

        if steps >= max_steps:
            async for ev in reasoning.emit("agent", "Graph step limit reached. Stopping."):
                yield ev

        async for ev in reasoning.emit("agent", "Turn complete."):
            yield ev
        yield {"type": "done", "session_id": state.get("session_id", "")}


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #

async def _node_router(
    state: AgentState,
    reasoning: ReasoningStream,
    graph: AgentGraph,
) -> AsyncGenerator[dict[str, Any], None]:
    """Classify intent and load conversation context."""
    from .agent_intent import classify_intent

    query = state.get("user_query", "")
    started = time.perf_counter()
    async for ev in reasoning.emit("router", f"Classifying intent for: {query[:80]}..."):
        yield ev

    chat_fn = graph.chat_runner
    intent = classify_keywords_only(query)
    if intent == "conversation":
        # For ambiguous short queries, try a tiny LLM if available
        if chat_fn is not None:
            try:
                intent = await classify_intent(query, chat_fn=chat_fn, use_llm=True)
            except Exception:
                intent = "conversation"

    state["intent"] = intent
    async for ev in reasoning.model_thought(
        "router", f"Intent: {intent}", started, model=state.get("model", ""), provider=state.get("provider", "")
    ):
        yield ev

    # Load conversation summary for UI
    messages = state.get("messages", [])
    turn_count = len([m for m in messages if m.get("role") == "user"])
    if turn_count > 1:
        async for ev in reasoning.emit("conversation", f"Conversation context: {turn_count} user turns loaded."):
            yield ev
    else:
        async for ev in reasoning.emit("conversation", "No prior conversation context."):
            yield ev


def classify_keywords_only(query: str) -> str:
    """Keyword-only intent for the router node."""
    from .agent_intent import classify_keywords
    return classify_keywords(query)


async def _node_memory_load(
    state: AgentState,
    reasoning: ReasoningStream,
    graph: AgentGraph,
) -> AsyncGenerator[dict[str, Any], None]:
    """Retrieve memories, RAG chunks, and learned skills."""
    started = time.perf_counter()
    async for ev in reasoning.emit("context", "Loading memory & context..."):
        yield ev

    query = state.get("user_query", "")
    hits: dict[str, int] = {"memory": 0, "rag": 0, "research": 0, "skills": 0}

    try:
        from .omni_brain import retrieve_context
        results = await asyncio.to_thread(retrieve_context, query, limit=8)
        hits = {
            "memory": results.get("memory_hits", 0),
            "rag": results.get("rag_hits", 0),
            "research": results.get("research_hits", 0),
            "vector": results.get("vector_hits", 0),
        }
        addendum = results.get("context_text", "")
        if addendum:
            state["brain_addendum"] = addendum
    except Exception as exc:
        async for ev in reasoning.emit("context", f"Memory load skipped: {str(exc)[:120]}"):
            yield ev

    async for ev in reasoning.model_thought(
        "context",
        f"Loaded {hits['memory']} memories, {hits['rag']} RAG chunks, {hits['research']} research items, {hits['vector']} vector hits.",
        started,
        model=state.get("model", ""),
        provider=state.get("provider", ""),
    ):
        yield ev


async def _node_research(
    state: AgentState,
    reasoning: ReasoningStream,
    graph: AgentGraph,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run web search, fetch pages, and summarize."""
    query = state.get("user_query", "")
    started = time.perf_counter()
    async for ev in reasoning.emit("research", f"Researching: {query[:80]}..."):
        yield ev

    runner = graph.tool_runner
    if runner is None:
        async for ev in reasoning.emit("research", "No tool runner configured; research skipped."):
            yield ev
        return

    # Step 1: web search
    search_started = time.perf_counter()
    search_result = await asyncio.to_thread(runner, "web.search", {"query": query, "max_results": 6}, state.get("session_id", ""))
    async for ev in reasoning.model_thought(
        "research", f"Web search returned {len(search_result.get('results', []))} results.",
        search_started, model=state.get("model", ""), provider=state.get("provider", "")
    ):
        yield ev

    urls = [r.get("url") or r.get("link") for r in search_result.get("results", []) if r.get("url") or r.get("link")]
    urls = [u for u in urls if u][:4]

    # Step 2: parallel fetch top URLs
    fetched: list[dict[str, Any]] = []
    if urls:
        fetch_started = time.perf_counter()
        async def _fetch_one(url: str) -> dict[str, Any]:
            return await asyncio.to_thread(runner, "web.fetch", {"url": url}, state.get("session_id", ""))
        fetched = await asyncio.gather(*[_fetch_one(u) for u in urls])
        async for ev in reasoning.model_thought(
            "research", f"Fetched {len(fetched)} pages.",
            fetch_started, model=state.get("model", ""), provider=state.get("provider", "")
        ):
            yield ev

    # Step 3: summarize with LLM
    if graph.chat_runner is not None and fetched:
        summary_started = time.perf_counter()
        prompt = _build_research_summary_prompt(query, search_result.get("results", []), fetched)
        messages = [
            {"role": "system", "content": "You summarize research findings with citations."},
            {"role": "user", "content": prompt},
        ]
        try:
            result = await graph.chat_runner(messages)
            summary = (result.get("content") if isinstance(result, dict) else str(result)).strip()
            sources = [{"url": u, "title": "", "snippet": ""} for u in urls]
            set_research_context(state, {"query": query, "urls": urls, "sources": sources, "summaries": [summary]})
            append_research_summary(state, summary, sources=sources)
            add_memory_update(state, "fact", f"Research on '{query}': {summary[:500]}", tags=["research", "fact"], source="agent_graph")
            async for ev in reasoning.model_thought(
                "research", "Research summary generated.",
                summary_started, model=state.get("model", ""), provider=state.get("provider", "")
            ):
                yield ev
        except Exception as exc:
            async for ev in reasoning.emit("research", f"Summary failed: {str(exc)[:120]}"):
                yield ev
    elif not fetched:
        async for ev in reasoning.emit("research", "No fetchable results; using search snippets only."):
            yield ev
        snippets = [r.get("snippet", "") for r in search_result.get("results", [])]
        set_research_context(state, {"query": query, "urls": urls, "summaries": snippets})

    async for ev in reasoning.model_thought(
        "research", "Research node complete.",
        started, model=state.get("model", ""), provider=state.get("provider", "")
    ):
        yield ev


def _build_research_summary_prompt(
    query: str,
    results: list[dict[str, Any]],
    fetched: list[dict[str, Any]],
) -> str:
    parts = [f"User query: {query}\n\nSearch results:"]
    for i, r in enumerate(results[:6], 1):
        parts.append(f"{i}. {r.get('title','')} — {r.get('url','')}\n{r.get('snippet','')}")
    parts.append("\nFetched content:")
    for i, f in enumerate(fetched[:4], 1):
        text = f.get("text") or f.get("content") or f.get("markdown") or ""
        parts.append(f"{i}. {text[:1200]}")
    parts.append("\nProvide a concise summary with inline citations (source number).")
    return "\n\n".join(parts)


async def _node_automation(
    state: AgentState,
    reasoning: ReasoningStream,
    graph: AgentGraph,
) -> AsyncGenerator[dict[str, Any], None]:
    """ReAct-style automation loop."""
    query = state.get("user_query", "")
    started = time.perf_counter()
    async for ev in reasoning.emit("automation", f"Planning automation for: {query[:80]}..."):
        yield ev

    runner = graph.tool_runner
    if runner is None:
        async for ev in reasoning.emit("automation", "No tool runner configured; automation skipped."):
            yield ev
        return

    max_steps = state.get("max_react_steps", 5)
    while state.get("react_iterations", 0) < max_steps:
        iter_num = increment_react_iterations(state)
        plan_started = time.perf_counter()
        async for ev in reasoning.emit("automation", f"ReAct iteration {iter_num}: planning next action..."):
            yield ev

        # Simple ReAct: ask LLM for next tool call
        if graph.chat_runner is None:
            break

        tool_names = ["shell.run", "code.run", "fs.read", "fs.write", "fs.list", "browser.visit", "self.inspect"]
        prompt = _build_react_prompt(query, state, tool_names)
        messages = [
            {"role": "system", "content": "You are an automation planner. Reply with exactly one JSON object."},
            {"role": "user", "content": prompt},
        ]
        try:
            result = await graph.chat_runner(messages)
            raw = (result.get("content") if isinstance(result, dict) else str(result)).strip()
            action = _parse_react_action(raw, tool_names)
        except Exception as exc:
            async for ev in reasoning.emit("automation", f"Planner error: {str(exc)[:120]}"):
                yield ev
            break

        if action is None:
            async for ev in reasoning.emit("automation", "Planner returned no valid action. Stopping."):
                yield ev
            break

        if action.get("final"):
            async for ev in reasoning.model_thought(
                "automation", f"Automation finished: {action['final']}",
                plan_started, model=state.get("model", ""), provider=state.get("provider", "")
            ):
                yield ev
            break

        tool = action.get("tool")
        args = action.get("args", {})
        async for ev in reasoning.model_thought(
            "automation", f"Next action: {tool}",
            plan_started, model=state.get("model", ""), provider=state.get("provider", "")
        ):
            yield ev

        exec_started = time.perf_counter()
        tool_result = await asyncio.to_thread(runner, tool, args, state.get("session_id", ""))
        async for ev in reasoning.model_thought(
            "tool", f"Executed {tool}: ok={tool_result.get('ok', False)}",
            exec_started, model=state.get("model", ""), provider=state.get("provider", "")
        ):
            yield ev

        append_tool_output(state, f"{tool}_{iter_num}", {
            "tool": tool,
            "ok": bool(tool_result.get("ok", True)),
            "result": tool_result,
            "error": tool_result.get("error"),
        })

    async for ev in reasoning.model_thought(
        "automation", "Automation node complete.",
        started, model=state.get("model", ""), provider=state.get("provider", "")
    ):
        yield ev


def _build_react_prompt(query: str, state: AgentState, tool_names: list[str]) -> str:
    parts = [
        f"User request: {query}\n",
        "Available tools: " + ", ".join(tool_names),
        "\nTool results so far:",
    ]
    for key, val in (state.get("tool_outputs") or {}).items():
        parts.append(f"- {key}: ok={val.get('ok')}, result={json.dumps(val.get('result',''), default=str)[:400]}")
    if state.get("research_context"):
        ctx = state["research_context"]
        parts.append(f"\nResearch context: {ctx.get('summaries', [])}")
    parts.append(
        "\nReply with exactly one JSON object:\n"
        '{"tool": "tool.name", "args": {...}} to run a tool, or\n'
        '{"final": "short summary of what was accomplished"} if done.'
    )
    return "\n".join(parts)


def _parse_react_action(raw: str, valid_tools: list[str]) -> dict[str, Any] | None:
    """Parse a ReAct JSON action."""
    from .ai import extract_json_maybe
    data = extract_json_maybe(raw)
    if not isinstance(data, dict):
        return None
    if data.get("final"):
        return {"final": str(data["final"])}
    tool = data.get("tool") or data.get("name")
    if not tool:
        return None
    normalized = tool.replace("_", ".")
    if normalized not in valid_tools and tool not in valid_tools:
        return None
    return {"tool": normalized if normalized in valid_tools else tool, "args": data.get("args", {})}


async def _node_synthesis(
    state: AgentState,
    reasoning: ReasoningStream,
    graph: AgentGraph,
) -> AsyncGenerator[dict[str, Any], None]:
    """Compose the final answer from all gathered context."""
    query = state.get("user_query", "")
    started = time.perf_counter()
    async for ev in reasoning.emit("synthesis", "Synthesizing final answer..."):
        yield ev

    if graph.chat_runner is None:
        async for ev in reasoning.emit("synthesis", "No chat runner; synthesis skipped."):
            yield ev
        return

    messages = _build_synthesis_messages(state)
    try:
        result = await graph.chat_runner(messages)
        answer = (result.get("content") if isinstance(result, dict) else str(result)).strip()
        # Strip possible JSON final wrapper
        from .ai import extract_json_maybe
        parsed = extract_json_maybe(answer)
        if isinstance(parsed, dict) and parsed.get("final"):
            answer = str(parsed["final"])
        async for ev in reasoning.model_thought(
            "synthesis", "Final answer synthesized.",
            started, model=state.get("model", ""), provider=state.get("provider", "")
        ):
            yield ev
        yield {"type": "token", "content": answer}
    except Exception as exc:
        async for ev in reasoning.model_thought(
            "synthesis", f"Synthesis failed: {str(exc)[:120]}",
            started, model=state.get("model", ""), provider=state.get("provider", "")
        ):
            yield ev


def _build_synthesis_messages(state: AgentState) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system = state.get("system_prompt", "")
    if state.get("brain_addendum"):
        system += "\n\n" + state["brain_addendum"]
    if system:
        messages.append({"role": "system", "content": system})

    # Conversation history (last 10 turns)
    for m in (state.get("messages") or [])[-20:]:
        messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})

    # Research context
    ctx_parts: list[str] = ["Answer the user's latest request using the gathered context."]
    research = state.get("research_context", {})
    if research.get("summaries"):
        ctx_parts.append("Research summaries:\n" + "\n".join(f"- {s}" for s in research["summaries"]))

    # Tool outputs
    if state.get("tool_outputs"):
        ctx_parts.append("Tool outputs:")
        for key, val in state["tool_outputs"].items():
            ctx_parts.append(f"- {key}: {json.dumps(val.get('result',''), default=str)[:600]}")

    ctx_parts.append(f"User request: {state.get('user_query','')}")
    messages.append({"role": "user", "content": "\n\n".join(ctx_parts)})
    return messages


async def _node_memory_save(
    state: AgentState,
    reasoning: ReasoningStream,
    graph: AgentGraph,
) -> AsyncGenerator[dict[str, Any], None]:
    """Persist queued memory updates."""
    started = time.perf_counter()
    updates = state.get("memory_updates", [])
    if not updates:
        async for ev in reasoning.emit("memory", "No durable memories to save."):
            yield ev
        return

    async for ev in reasoning.emit("memory", f"Saving {len(updates)} memory update(s)..."):
        yield ev

    try:
        from .omni_brain import remember
        for up in updates:
            tags = up.get("tags", [])
            remember(
                namespace="agent",
                key=up.get("content", "")[:80],
                content=up.get("content", ""),
                tags=tags,
                source=up.get("source", "agent_graph"),
            )
    except Exception as exc:
        async for ev in reasoning.emit("memory", f"Memory save failed: {str(exc)[:120]}"):
            yield ev

    async for ev in reasoning.model_thought(
        "memory", "Memory updates saved.",
        started, model=state.get("model", ""), provider=state.get("provider", "")
    ):
        yield ev


# --------------------------------------------------------------------------- #
# Edges
# --------------------------------------------------------------------------- #

def _edge_after_router(state: AgentState) -> str:
    intent = state.get("intent", "conversation")
    if intent == "conversation":
        return "synthesis"
    return "memory_load"


def _edge_after_memory_load(state: AgentState) -> str:
    intent = state.get("intent", "conversation")
    if intent == "research":
        return "research"
    if intent == "automation":
        return "automation"
    if intent == "hybrid":
        return "research"
    return "synthesis"


def _edge_after_research(state: AgentState) -> str:
    intent = state.get("intent", "conversation")
    if intent == "hybrid":
        return "automation"
    return "synthesis"


def _edge_after_automation(state: AgentState) -> str:
    return "synthesis"


def _edge_after_synthesis(state: AgentState) -> str:
    return "memory_save"
