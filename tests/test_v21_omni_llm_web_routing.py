import asyncio
import json

from fastapi.testclient import TestClient

import backend.app.main as main
from backend.app.main import ChatRequest, app


def _run(coro):
    return asyncio.run(coro)


def _tool_calling_stream(tool_name: str, tool_args: dict, answer: str):
    """Fake brain-model streamer: calls the given tool on the first turn
    (when tools are offered and no tool results are in the conversation yet),
    then streams the synthesized answer on the follow-up turn."""

    async def fake_stream(model, messages, tools, on_delta, **_):
        if tools and not any("Tool results" in str(msg.get("content", "")) for msg in messages):
            return {"content": "", "tool_calls": [{"function": {"name": tool_name, "arguments": tool_args}}]}
        if on_delta:
            await on_delta(answer)
        return {"content": answer, "tool_calls": []}

    return fake_stream


def test_chat_search_uses_model_tool_call_then_synthesizes_answer(monkeypatch):
    calls = {"search_queries": []}

    async def fake_search(query, max_results=6, provider=None, planned_query=None):
        calls["search_queries"].append(query)
        return {
            "ok": True,
            "query": query,
            "original_query": (planned_query or {}).get("original_query", query),
            "provider": "fixture",
            "results": [{"title": "Official GST update", "url": "https://example.test/gst", "snippet": "Official update text."}],
            "query_plan": planned_query or {"primary_query": query, "variants": [query]},
        }

    monkeypatch.setattr(main, "_web_search", fake_search)
    # Native-only routing: provider=ollama resolves to the native engine.
    monkeypatch.setattr(main, "_kick_native_load", lambda *a, **k: None)
    monkeypatch.setattr(
        "shared.agent_loop._native_chat_stream",
        _tool_calling_stream(
            "web.search",
            {"query": "GST e invoice India rules 2026"},
            "Current GST e-invoice requirements depend on turnover and official CBIC/GSTN updates [1].",
        ),
    )

    c = TestClient(app)
    raw = "hey shims can you search the internet for what is the latest GST e invoice rule in India today"
    with c.stream("POST", "/brain/turn", json={"message": raw, "web_mode": True, "provider": "ollama", "source": "typed"}) as resp:
        body = "".join(resp.iter_text())

    assert calls["search_queries"] == ["GST e invoice India rules 2026"]
    assert raw not in calls["search_queries"]
    assert "Current GST e-invoice requirements" in body
    assert "GST e invoice India rules 2026" in body


def test_llm_search_planner_can_veto_heuristic_latest_trigger(monkeypatch):
    async def ready(provider, model):
        return True

    async def fake_llm(provider, model, messages, allow_provider_web_search=False):
        return (
            json.dumps(
                {
                    "should_search": False,
                    "primary_query": "",
                    "queries": [],
                    "intent": "none",
                    "user_task": "The user is asking for a writing preference, not fresh public facts.",
                }
            ),
            "fixture-planner",
        )

    monkeypatch.setattr(main, "_provider_ready_for_llm", ready)
    monkeypatch.setattr(main, "_run_llm", fake_llm)

    plan = _run(main._understand_search_turn(ChatRequest(message="tell me the latest way you prefer to structure our chat", web_mode=True, provider="ollama")))

    assert plan is None


def test_legacy_api_chat_uses_same_search_router(monkeypatch):
    calls = {"search_queries": []}

    async def fake_search(query, max_results=6, provider=None, planned_query=None):
        calls["search_queries"].append(query)
        return {
            "ok": True,
            "query": query,
            "provider": "fixture",
            "results": [{"title": "Supplier quote", "url": "https://example.test/quote", "snippet": "Fixture price source"}],
            "query_plan": planned_query or {"primary_query": query, "variants": [query]},
        }

    monkeypatch.setattr(main, "_web_search", fake_search)
    # Native-only routing: provider=ollama resolves to the native engine.
    monkeypatch.setattr(main, "_kick_native_load", lambda *a, **k: None)
    monkeypatch.setattr(
        "shared.agent_loop._native_chat_stream",
        _tool_calling_stream(
            "web.search",
            {"query": "fluconazole API India price"},
            "Fluconazole API pricing needs supplier/date verification; the source below is only evidence [1].",
        ),
    )

    c = TestClient(app)
    data = c.post("/api/chat", json={"message": "search the web for latest fluconazole API price India", "web_mode": True, "provider": "ollama"}).json()

    assert calls["search_queries"] == ["fluconazole API India price"]
    assert "Fluconazole API pricing" in data["answer"]
    assert data["route"].endswith("-unified")
