import asyncio
import json

from fastapi.testclient import TestClient

import backend.app.main as main
from backend.app.main import ChatRequest, app


def _run(coro):
    return asyncio.run(coro)


def test_chat_search_uses_llm_focused_query_then_synthesizes_answer(monkeypatch):
    calls = {"search_queries": [], "llm_prompts": []}

    async def ready(provider, model):
        return True

    async def fake_llm(provider, model, messages, allow_provider_web_search=False):
        calls["llm_prompts"].append(messages[0]["content"])
        if "web-search planner" in messages[0]["content"]:
            return (
                json.dumps(
                    {
                        "should_search": True,
                        "primary_query": "GST e invoice India rules 2026",
                        "queries": ["GST e invoice India rules 2026", "GST e invoice official India"],
                        "intent": "regulatory",
                        "user_task": "Explain the current GST e-invoice rule in India.",
                    }
                ),
                "fixture-planner",
            )
        return ("Current GST e-invoice requirements depend on turnover and official CBIC/GSTN updates [1].", "fixture-answer")

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

    monkeypatch.setattr(main, "_provider_ready_for_llm", ready)
    monkeypatch.setattr(main, "_run_llm", fake_llm)
    monkeypatch.setattr(main, "_web_search", fake_search)

    c = TestClient(app)
    raw = "hey shims can you search the internet for what is the latest GST e invoice rule in India today"
    with c.stream("POST", "/brain/turn", json={"message": raw, "web_mode": True, "provider": "ollama", "source": "typed"}) as resp:
        body = "".join(resp.iter_text())

    assert calls["search_queries"] == ["GST e invoice India rules 2026"]
    assert raw not in calls["search_queries"]
    assert "fixture-answer" in body
    assert "GST e invoice India rules 2026" in body
    assert "web-search-synthesized" in body


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

    async def ready(provider, model):
        return True

    async def fake_llm(provider, model, messages, allow_provider_web_search=False):
        if "web-search planner" in messages[0]["content"]:
            return (
                json.dumps(
                    {
                        "should_search": True,
                        "primary_query": "fluconazole API India price",
                        "queries": ["fluconazole API India price"],
                        "intent": "market",
                        "user_task": "Summarize current fluconazole API price evidence for India.",
                    }
                ),
                "fixture-planner",
            )
        return ("Fluconazole API pricing needs supplier/date verification; the source below is only evidence [1].", "fixture-answer")

    async def fake_search(query, max_results=6, provider=None, planned_query=None):
        calls["search_queries"].append(query)
        return {
            "ok": True,
            "query": query,
            "provider": "fixture",
            "results": [{"title": "Supplier quote", "url": "https://example.test/quote", "snippet": "Fixture price source"}],
            "query_plan": planned_query or {"primary_query": query, "variants": [query]},
        }

    monkeypatch.setattr(main, "_provider_ready_for_llm", ready)
    monkeypatch.setattr(main, "_run_llm", fake_llm)
    monkeypatch.setattr(main, "_web_search", fake_search)

    c = TestClient(app)
    data = c.post("/api/chat", json={"message": "search the web for latest fluconazole API price India", "web_mode": True, "provider": "ollama"}).json()

    assert data["route"].startswith("web-search-synthesized")
    assert calls["search_queries"] == ["fluconazole API India price"]
    assert "Fluconazole API pricing" in data["answer"]
