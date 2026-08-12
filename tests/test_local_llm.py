"""Tests for shared/local_llm.py — the native-only feature chat helper."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import shared.local_llm as local_llm
from shared.local_llm import feature_chat


def _fake_engine(*, ready: bool = True, model: str = "test-gguf") -> MagicMock:
    engine = MagicMock()
    engine.health.return_value = {
        "ready": ready,
        "running": ready,
        "model": model if ready else "",
    }
    return engine


def _fake_client(content: str) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.post.return_value = response
    return client


def test_feature_chat_success_returns_content() -> None:
    engine = _fake_engine()
    with patch.object(local_llm, "get_engine", return_value=engine), \
         patch.object(local_llm.httpx, "Client", return_value=_fake_client("hello there")):
        out = feature_chat([{"role": "user", "content": "hi"}])
    assert out == "hello there"
    engine.ensure_loaded.assert_not_called()


def test_feature_chat_engine_down_returns_empty() -> None:
    engine = _fake_engine(ready=False)
    with patch.object(local_llm, "get_engine", return_value=engine):
        out = feature_chat([{"role": "user", "content": "hi"}])
    assert out == ""


def test_feature_chat_ensure_loaded_called_when_model_given() -> None:
    engine = _fake_engine(model="coder-gguf")
    client = _fake_client("ok")
    with patch.object(local_llm, "get_engine", return_value=engine), \
         patch.object(local_llm.httpx, "Client", return_value=client):
        out = feature_chat([{"role": "user", "content": "hi"}], model="coder-gguf")
    assert out == "ok"
    engine.ensure_loaded.assert_called_once_with("coder-gguf")


def test_feature_chat_http_error_returns_empty() -> None:
    engine = _fake_engine()
    client = _fake_client("")
    client.post.side_effect = Exception("connection refused")
    with patch.object(local_llm, "get_engine", return_value=engine), \
         patch.object(local_llm.httpx, "Client", return_value=client):
        out = feature_chat([{"role": "user", "content": "hi"}])
    assert out == ""


def test_feature_chat_bad_json_returns_empty() -> None:
    engine = _fake_engine()
    response = MagicMock()
    response.json.return_value = {"unexpected": True}
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.post.return_value = response
    with patch.object(local_llm, "get_engine", return_value=engine), \
         patch.object(local_llm.httpx, "Client", return_value=client):
        out = feature_chat([{"role": "user", "content": "hi"}])
    assert out == ""
