"""Tests for Hugging Face local-endpoint provider integration."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

# Ensure HF env points to a predictable URL for tests.
os.environ.setdefault("HUGGINGFACE_BASE_URL", "http://127.0.0.1:8080")
os.environ.setdefault("HUGGINGFACE_MODEL", "meta-llama/Llama-3.1-8B-Instruct")

from backend.app.main import app, _run_llm, _hf_models_raw  # noqa: E402

client = TestClient(app)


class _MockResponse:
    def __init__(self, json_data: Any, status_code: int = 200, text: str = "") -> None:
        self._json = json_data
        self.status_code = status_code
        self._text = text or json.dumps(json_data)

    def json(self) -> Any:
        return self._json

    @property
    def text(self) -> str:
        return self._text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=httpx.Request("GET", "http://test"),
                response=self,
            )


class _MockAsyncClient:
    """Minimal httpx.AsyncClient mock that handles HF endpoint calls."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._responses: dict[str, _MockResponse] = {}
        self._stream_responses: dict[str, list[str]] = {}

    def add_response(self, url: str, response: _MockResponse) -> None:
        self._responses[url] = response

    async def get(self, url: str, **kwargs: Any) -> _MockResponse:
        return self._responses.get(url, _MockResponse({}, 404))

    async def post(self, url: str, **kwargs: Any) -> _MockResponse:
        return self._responses.get(url, _MockResponse({}, 404))

    async def __aenter__(self) -> "_MockAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def stream(self, method: str, url: str, **kwargs: Any) -> "_MockStreamContext":
        chunks = self._stream_responses.get(url, [])
        return _MockStreamContext(chunks)


class _MockStreamContext:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> "_MockStreamContext":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def aiter_lines(self) -> Any:
        for chunk in self._chunks:
            yield chunk


@pytest.fixture
def mock_hf_client(monkeypatch: pytest.MonkeyPatch) -> _MockAsyncClient:
    """Patch httpx.AsyncClient to return canned HF endpoint responses."""
    mock = _MockAsyncClient()

    def factory(*args: Any, **kwargs: Any) -> _MockAsyncClient:
        return mock

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return mock


def test_run_llm_huggingface_chat(mock_hf_client: _MockAsyncClient) -> None:
    mock_hf_client.add_response(
        "http://127.0.0.1:8080/v1/chat/completions",
        _MockResponse({
            "choices": [
                {"message": {"content": "SHIMS key ok"}}
            ]
        }),
    )
    text, route = asyncio.run(_run_llm(
        "huggingface",
        "meta-llama/Llama-3.1-8B-Instruct",
        [{"role": "user", "content": "Reply with exactly: SHIMS key ok"}],
    ))
    assert "SHIMS key ok" in text
    assert route == "huggingface-local"


def test_hf_models_raw(mock_hf_client: _MockAsyncClient) -> None:
    mock_hf_client.add_response(
        "http://127.0.0.1:8080/v1/models",
        _MockResponse({
            "data": [
                {"id": "meta-llama/Llama-3.1-8B-Instruct", "created": 1234567890, "owned_by": "meta"},
                {"id": "Qwen/Qwen2.5-7B-Instruct", "created": 1234567891, "owned_by": "qwen"},
            ]
        }),
    )
    models = asyncio.run(_hf_models_raw())
    names = {m["name"] for m in models}
    assert "meta-llama/Llama-3.1-8B-Instruct" in names
    assert "Qwen/Qwen2.5-7B-Instruct" in names
    assert all(m["provider"] == "huggingface" for m in models)


def test_chat_models_includes_hf(mock_hf_client: _MockAsyncClient) -> None:
    mock_hf_client.add_response(
        "http://127.0.0.1:8080/v1/models",
        _MockResponse({
            "data": [
                {"id": "meta-llama/Llama-3.1-8B-Instruct", "created": 1234567890, "owned_by": "meta"},
            ]
        }),
    )
    # Ollama will be offline in test env; that's fine.
    r = client.get("/chat/models")
    assert r.status_code == 200
    data = r.json()
    installed_names = {m["name"] for m in data["installed"]}
    assert "meta-llama/Llama-3.1-8B-Instruct" in installed_names
    assert "huggingface" in data["providers"]


def test_providers_includes_hf(mock_hf_client: _MockAsyncClient) -> None:
    mock_hf_client.add_response(
        "http://127.0.0.1:8080/v1/models",
        _MockResponse({
            "data": [
                {"id": "meta-llama/Llama-3.1-8B-Instruct", "created": 1234567890, "owned_by": "meta"},
            ]
        }),
    )
    r = client.get("/system/providers")
    assert r.status_code == 200
    data = r.json()
    provider_ids = {p["id"] for p in data["providers"]}
    assert "huggingface" in provider_ids
    hf_entry = next(p for p in data["providers"] if p["id"] == "huggingface")
    assert hf_entry["status"] == "ready"


def test_provider_test_huggingface(mock_hf_client: _MockAsyncClient) -> None:
    mock_hf_client.add_response(
        "http://127.0.0.1:8080/v1/chat/completions",
        _MockResponse({
            "choices": [
                {"message": {"content": "SHIMS key ok"}}
            ]
        }),
    )
    r = client.post("/system/providers/huggingface/test", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "SHIMS key ok" in data["reply"]
    assert data["model"] == "meta-llama/Llama-3.1-8B-Instruct"
