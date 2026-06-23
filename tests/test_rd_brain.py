"""Tests for R&D Brain module."""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from shared.rd_brain import (
    RDBrain, PatentResult, ProcessStep, SynthesizedProcess,
    RMPricing, YieldPrediction, PurityTestMethod,
    DEEPSEEK_API_URL,
)


def _run(coro):
    """Helper to run async coroutines in sync tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestRDBrainInit:
    def test_auto_selects_deepseek_when_key_present(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        brain = RDBrain()
        assert brain.provider == "deepseek"
        assert brain.model == "deepseek-reasoner"

    def test_auto_selects_ollama_when_no_key(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_KEY", raising=False)
        brain = RDBrain()
        assert brain.provider == "ollama"

    def test_explicit_provider(self):
        brain = RDBrain(provider="ollama", model="llama3.2")
        assert brain.provider == "ollama"
        assert brain.model == "llama3.2"


class TestRDBrainPatentSearch:
    def test_patent_search_returns_results(self):
        mock_response = json.dumps([
            {"patent_number": "US1234567", "title": "Test Patent", "assignee": "Test Co",
             "filing_date": "2024-01-01", "abstract": "Test abstract", "relevance_score": 0.95}
        ])
        with patch("shared.rd_brain.RDBrain._call_ai", new_callable=AsyncMock, return_value=mock_response), \
             patch("shared.rd_brain.RDBrain._search_serpapi_patents", new_callable=AsyncMock, return_value=[]), \
             patch("shared.rd_brain.RDBrain._search_uspto_ppub", new_callable=AsyncMock, return_value=[]), \
             patch("shared.rd_brain.RDBrain._search_cnipa", new_callable=AsyncMock, return_value=[]):
            brain = RDBrain(provider="ollama")
            results = _run(brain.patent_search("fluconazole synthesis"))
            assert len(results) == 1
            assert results[0].patent_number == "US1234567"
            assert results[0].relevance_score == 0.95

    def test_patent_search_fallback_on_bad_json(self):
        with patch("shared.rd_brain.RDBrain._call_ai", new_callable=AsyncMock, return_value="not json"), \
             patch("shared.rd_brain.RDBrain._search_serpapi_patents", new_callable=AsyncMock, return_value=[]), \
             patch("shared.rd_brain.RDBrain._search_uspto_ppub", new_callable=AsyncMock, return_value=[]), \
             patch("shared.rd_brain.RDBrain._search_cnipa", new_callable=AsyncMock, return_value=[]):
            brain = RDBrain(provider="ollama")
            results = _run(brain.patent_search("test"))
            assert len(results) == 1
            assert results[0].patent_number == "N/A"

    def test_patent_search_uses_raw_api_results_when_json_fails(self):
        raw = [
            {"patent_id": "CN123456", "title": "Chinese Patent", "assignee": "CN Pharma",
             "filing_date": "2023-05-01", "abstract": "Abstract text", "url": "http://example.com"}
        ]
        with patch("shared.rd_brain.RDBrain._call_ai", new_callable=AsyncMock, return_value="not json"), \
             patch("shared.rd_brain.RDBrain._search_serpapi_patents", new_callable=AsyncMock, return_value=[]), \
             patch("shared.rd_brain.RDBrain._search_uspto_ppub", new_callable=AsyncMock, return_value=[]), \
             patch("shared.rd_brain.RDBrain._search_cnipa", new_callable=AsyncMock, return_value=raw):
            brain = RDBrain(provider="ollama")
            results = _run(brain.patent_search("test"))
            assert len(results) == 1
            assert results[0].patent_number == "CN123456"
            assert results[0].url == "http://example.com"


class TestRDBrainProcessSynthesis:
    def test_synthesize_process_returns_structure(self):
        mock_response = json.dumps({
            "target_product": "Fluconazole",
            "raw_materials": ["A", "B"],
            "overall_yield_pct": 85.0,
            "steps": [
                {"step_number": 1, "description": "Mix A and B", "raw_materials": ["A"], "conditions": "RT",
                 "equipment": "Reactor", "time_hours": 2.0, "temperature_c": 25.0, "pressure_bar": 1.0,
                 "expected_yield_pct": 95.0, "notes": ""}
            ],
            "safety_notes": "Wear PPE",
            "environmental_notes": "Dispose properly",
            "reference_patents": ["US123"],
            "reference_literature": ["J Med Chem 2024"],
        })
        with patch("shared.rd_brain.RDBrain._call_ai", new_callable=AsyncMock, return_value=mock_response):
            brain = RDBrain(provider="ollama")
            process = _run(brain.synthesize_process("Fluconazole", ["A", "B"]))
            assert process.target_product == "Fluconazole"
            assert len(process.steps) == 1
            assert process.steps[0].step_number == 1
            assert process.safety_notes == "Wear PPE"

    def test_synthesize_process_fallback_on_bad_json(self):
        with patch("shared.rd_brain.RDBrain._call_ai", new_callable=AsyncMock, return_value="raw text response"):
            brain = RDBrain(provider="ollama")
            process = _run(brain.synthesize_process("Fluconazole", ["A", "B"]))
            assert process.target_product == "Fluconazole"
            assert len(process.steps) == 1
            assert "parse failed" in process.steps[0].notes


class TestRDBrainPricing:
    def test_raw_material_pricing(self):
        mock_response = json.dumps([
            {"material": "A", "price_per_kg_inr": 500.0, "supplier_region": "India",
             "price_date": "2026-05-29", "trend": "stable", "notes": ""}
        ])
        with patch("shared.rd_brain.RDBrain._call_ai", new_callable=AsyncMock, return_value=mock_response):
            brain = RDBrain(provider="ollama")
            pricing = _run(brain.raw_material_pricing(["A"]))
            assert len(pricing) == 1
            assert pricing[0].material == "A"
            assert pricing[0].price_per_kg_inr == 500.0


class TestRDBrainYieldPrediction:
    def test_predict_yield(self):
        mock_response = json.dumps({
            "predicted_yield_pct": 88.5,
            "confidence": "high",
            "key_variables": ["temperature", "time"],
            "optimization_suggestions": ["Increase temp"],
        })
        with patch("shared.rd_brain.RDBrain._call_ai", new_callable=AsyncMock, return_value=mock_response):
            brain = RDBrain(provider="ollama")
            pred = _run(brain.predict_yield("Mix A and B at 80C"))
            assert pred.predicted_yield_pct == 88.5
            assert pred.confidence == "high"


class TestRDBrainPurityMethods:
    def test_purity_testing_methods(self):
        mock_response = json.dumps([
            {"test_name": "Assay", "method": "HPLC", "specification": "98.0-102.0%",
             "reference_standard": "USP", "notes": ""}
        ])
        with patch("shared.rd_brain.RDBrain._call_ai", new_callable=AsyncMock, return_value=mock_response):
            brain = RDBrain(provider="ollama")
            methods = _run(brain.purity_testing_methods("Fluconazole"))
            assert len(methods) == 1
            assert methods[0].test_name == "Assay"
            assert methods[0].method == "HPLC"


class TestRDBrainResearchBrief:
    def test_generate_research_brief_creates_pdf(self):
        mock_process = json.dumps({
            "target_product": "Fluconazole",
            "raw_materials": ["A", "B"],
            "overall_yield_pct": 85.0,
            "steps": [
                {"step_number": 1, "description": "Mix", "raw_materials": ["A"], "conditions": "RT",
                 "equipment": "Reactor", "time_hours": 2.0, "temperature_c": 25.0, "pressure_bar": 1.0,
                 "expected_yield_pct": 95.0, "notes": ""}
            ],
            "safety_notes": "", "environmental_notes": "",
            "reference_patents": [], "reference_literature": [],
        })
        mock_pricing = json.dumps([{"material": "A", "price_per_kg_inr": 100.0, "supplier_region": "India",
                                      "price_date": "2026-05-29", "trend": "stable", "notes": ""}])
        mock_tests = json.dumps([{"test_name": "Assay", "method": "HPLC", "specification": "98%",
                                   "reference_standard": "USP", "notes": ""}])
        mock_yield = json.dumps({"predicted_yield_pct": 90.0, "confidence": "high",
                                  "key_variables": ["temp"], "optimization_suggestions": ["none"]})

        with patch("shared.rd_brain.RDBrain._call_ai", new_callable=AsyncMock) as mock_ai:
            mock_ai.side_effect = [mock_process, mock_pricing, mock_tests, mock_yield]
            brain = RDBrain(provider="ollama")
            path = _run(brain.generate_research_brief(
                title="Test Brief",
                objective="Test",
                background="Test",
                target_product="Fluconazole",
                raw_materials=["A", "B"],
            ))
            assert path.exists()
            assert path.stat().st_size > 1000
            path.unlink(missing_ok=True)


class TestDeepSeekCall:
    def test_deepseek_call_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_KEY", raising=False)
        brain = RDBrain(provider="deepseek")
        with pytest.raises(RuntimeError, match="DeepSeek API key not configured"):
            _run(brain._call_deepseek("test", "", 0.2))

    def test_deepseek_call_success(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        mock_response = {"choices": [{"message": {"content": "Hello from DeepSeek"}}]}

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, url, headers, json):
                class Resp:
                    def raise_for_status(self): pass
                    def json(self): return mock_response
                return Resp()

        with patch("httpx.AsyncClient", return_value=MockClient()):
            brain = RDBrain(provider="deepseek")
            result = _run(brain._call_deepseek("test", "sys", 0.2))
            assert result == "Hello from DeepSeek"
