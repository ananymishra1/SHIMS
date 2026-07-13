from fastapi.testclient import TestClient

from backend.app.main import app, _detect_search_intent
from shared.search_query_planner import plan_search_query


def test_planner_strips_full_chat_turn_to_compact_query():
    plan = plan_search_query("hey SHIMS please search the internet for what is the latest price of fluconazole API in India today")
    assert plan.should_search is True
    assert "hey" not in plan.primary_query.lower()
    assert "please" not in plan.primary_query.lower()
    assert "what" not in plan.primary_query.lower()
    assert "fluconazole" in plan.primary_query.lower()
    assert "price" in " ".join(plan.variants).lower()


def test_planner_preserves_search_operators_and_quotes():
    plan = plan_search_query('search web for site:fda.gov "data integrity" GMP guidance filetype:pdf')
    assert plan.should_search is True
    assert "site:fda.gov" in plan.primary_query
    assert 'filetype:pdf' in plan.primary_query
    assert '"data integrity"' in plan.primary_query


def test_planner_does_not_search_casual_chat():
    plan = plan_search_query("hi", web_mode=True)
    assert plan.should_search is False


def test_backend_search_intent_returns_planned_query():
    # Temporal keywords like "current" are now kept — they matter for freshness.
    assert _detect_search_intent("search the web for current GST e invoice rules") == "current GST e invoice rules"
    assert _detect_search_intent("research fluconazole patent route", web_mode=True) == "fluconazole patent route"
    assert _detect_search_intent("normal chat please", web_mode=True) is None


def test_web_plan_endpoint():
    c = TestClient(app)
    data = c.post("/web/plan", json={"query": "please look up latest CDSCO Schedule M GMP rules India"}).json()
    assert data["ok"] is True
    assert data["plan"]["should_search"] is True
    assert "CDSCO" in data["plan"]["primary_query"] or "Schedule" in data["plan"]["primary_query"]

    get_data = c.get("/web/plan", params={"q": 'search web for site:fda.gov "data integrity"'}).json()
    assert get_data["ok"] is True
    assert "site:fda.gov" in get_data["plan"]["primary_query"]
