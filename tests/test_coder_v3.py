from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from shared.coder_v3 import ai_apply, ai_assist

client = TestClient(app)


@pytest.fixture
def temp_coder_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr("shared.coder_v2.CODER_DIR", tmp_path)
        monkeypatch.setattr("shared.coder_v3.CODER_DIR", tmp_path)
        yield tmp_path


def _make_project(project_id: str, coder_dir: Path) -> Path:
    project_dir = coder_dir / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    meta_path = project_dir / "_project.json"
    meta_path.write_text(json.dumps({"id": project_id, "name": "test"}), encoding="utf-8")
    return project_dir


@pytest.mark.anyio
async def test_ai_assist_awaits_governor_chat_directly(temp_coder_dir):
    """ai_assist must await gov.chat directly without nested event loops."""
    project_id = "test_ai_assist"
    project_dir = _make_project(project_id, temp_coder_dir)
    (project_dir / "main.py").write_text("x = 1", encoding="utf-8")

    with patch("shared.neural_governor.governor.NeuralGovernor") as MockGov:
        instance = MockGov.return_value
        instance.chat = AsyncMock(return_value={"output": "refactored code", "model": "mock"})
        result = await ai_assist(project_id, "refactor", "main.py")

    assert result["ok"] is True
    assert result["response"] == "refactored code"
    assert result["model_used"] == "mock"
    instance.chat.assert_awaited_once()


def test_ai_apply_extracts_and_writes_single_code_block(temp_coder_dir):
    project_id = "test_ai_apply_single"
    project_dir = _make_project(project_id, temp_coder_dir)
    response = '```python\ndef hello():\n    return "world"\n```'

    result = ai_apply(project_id, "hello.py", response)

    assert result["ok"] is True
    assert result["path"] == "hello.py"
    assert result["blocks_found"] == 1
    assert (project_dir / "hello.py").read_text(encoding="utf-8") == 'def hello():\n    return "world"'


def test_ai_apply_no_code_block_returns_error(temp_coder_dir):
    project_id = "test_ai_apply_none"
    _make_project(project_id, temp_coder_dir)

    result = ai_apply(project_id, "hello.py", "Just some explanation without code.")

    assert result["ok"] is False
    assert "No code found" in result["error"]


def test_ai_apply_multiple_blocks_uses_first(temp_coder_dir):
    project_id = "test_ai_apply_multi"
    project_dir = _make_project(project_id, temp_coder_dir)
    response = '```python\nfirst = 1\n```\n\n```python\nsecond = 2\n```'

    result = ai_apply(project_id, "multi.py", response)

    assert result["ok"] is True
    assert (project_dir / "multi.py").read_text(encoding="utf-8") == "first = 1"
    assert result["blocks_found"] == 2


def test_endpoint_ai_apply_via_assist_action(temp_coder_dir):
    project_id = "test_endpoint_assist_apply"
    _make_project(project_id, temp_coder_dir)

    response = client.post(
        f"/coder/v3/project/{project_id}/ai/assist",
        json={
            "action": "apply",
            "file_path": "app.py",
            "ai_response": "```python\nprint('ok')\n```",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["path"] == "app.py"
    assert (temp_coder_dir / project_id / "app.py").read_text(encoding="utf-8") == "print('ok')"


def test_endpoint_ai_apply_dedicated(temp_coder_dir):
    project_id = "test_endpoint_apply"
    _make_project(project_id, temp_coder_dir)

    response = client.post(
        f"/coder/v3/project/{project_id}/ai/apply",
        json={
            "file_path": "app.py",
            "ai_response": "```python\nprint('hello')\n```",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["path"] == "app.py"
    assert (temp_coder_dir / project_id / "app.py").read_text(encoding="utf-8") == "print('hello')"


def test_endpoint_ai_assist_uses_await(temp_coder_dir):
    """The /assist endpoint should return generated output without nested-loop crashes."""
    project_id = "test_endpoint_assist"
    _make_project(project_id, temp_coder_dir)

    with patch("shared.neural_governor.governor.NeuralGovernor") as MockGov:
        instance = MockGov.return_value
        instance.chat = AsyncMock(return_value={"output": "done", "model": "mock"})
        response = client.post(
            f"/coder/v3/project/{project_id}/ai/assist",
            json={"action": "explain", "file_path": "main.py"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["response"] == "done"
