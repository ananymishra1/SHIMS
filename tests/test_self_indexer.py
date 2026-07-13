from __future__ import annotations

from pathlib import Path

import pytest

import shared.omni_brain as ob
import shared.self_indexer as si


@pytest.fixture
def source_root(tmp_path, monkeypatch):
    """Provide an isolated project root and wire self_indexer to use it."""
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(si, "ROOT_DIR", root)
    monkeypatch.setattr(si, "ALLOWED_ROOTS", {"shared", "backend", "frontend"})
    monkeypatch.setattr(si, "BLOCKED_PARTS", {".venv", "__pycache__", "node_modules"})
    monkeypatch.setattr(si, "IMMUTABLE_RELATIVE_PATHS", {"shared/security.py"})
    return root


@pytest.fixture
def fresh_brain(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "BRAIN_DB", tmp_path / "brain.sqlite3")


def _write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


PYTHON_SAMPLE = '''\
"""Module docstring."""

CONST = 1

def helper(x):
    return x + 1

class Greeter:
    def greet(self):
        return "hello"
'''

JS_SAMPLE = '''\
const config = { key: 'value' };

function hello(name) {
    return `hi ${name}`;
}

class Widget {
    render() {
        return '<div></div>';
    }
}
'''

CSS_SAMPLE = '''\
body {
    margin: 0;
}

.card {
    padding: 1rem;
}
'''

HTML_SAMPLE = '''\
<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
<section id="main">Hello</section>
</body>
</html>
'''


def test_python_chunking():
    chunks = si._chunk_python(PYTHON_SAMPLE, "shared/demo.py")
    titles = [c["title"] for c in chunks]
    assert "shared/demo.py:module:header" in titles
    assert "shared/demo.py:function:helper" in titles
    assert "shared/demo.py:class:Greeter" in titles
    helper = next(c for c in chunks if "function:helper" in c["title"])
    assert "def helper" in helper["body"]


def test_js_chunking():
    chunks = si._chunk_js(JS_SAMPLE, "frontend/app.js")
    titles = [c["title"] for c in chunks]
    assert any("function:hello" in t for t in titles)
    assert any("class:Widget" in t for t in titles)


def test_css_chunking():
    chunks = si._chunk_css(CSS_SAMPLE, "frontend/app.css")
    titles = [c["title"] for c in chunks]
    assert any("body" in t for t in titles)
    assert any("card" in t for t in titles)
    assert all("{" in c["body"] for c in chunks)


def test_html_chunking():
    chunks = si._chunk_html(HTML_SAMPLE, "frontend/app.html")
    titles = [c["title"] for c in chunks]
    assert any("html" in t.lower() for t in titles)
    assert any("Hello" in c["body"] for c in chunks)


def test_is_allowed(source_root):
    allowed = _write(source_root, "shared/ok.py", "x = 1")
    blocked = _write(source_root, "shared/.venv/bad.py", "x = 1")
    immutable = _write(source_root, "shared/security.py", "x = 1")
    outside = _write(source_root, "other/file.py", "x = 1")
    binary = source_root / "shared/data.bin"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"\x00")

    assert si._is_allowed(allowed) is True
    assert si._is_allowed(blocked) is False
    assert si._is_allowed(immutable) is False
    assert si._is_allowed(outside) is False
    assert si._is_allowed(binary) is False


def test_index_skips_blocked_and_immutable(source_root, fresh_brain):
    _write(source_root, "shared/allowed.py", PYTHON_SAMPLE)
    _write(source_root, "shared/__pycache__/cached.py", PYTHON_SAMPLE)
    _write(source_root, "shared/security.py", PYTHON_SAMPLE)

    result = si.index_shims_source(force=True)
    assert result["ok"] is True
    assert result["files_indexed"] == 1
    assert result["files_blocked"] >= 1
    assert result["files_immutable"] == 1

    # Verify only allowed source made it into knowledge_chunks.
    rows = ob._knowledge_rows(limit=100)
    titles = [r["title"] for r in rows]
    assert any("shared/allowed.py" in t for t in titles)
    assert not any("security.py" in t for t in titles)
    assert not any("__pycache__" in t for t in titles)
    assert all(r["source_type"] == "shims_source" for r in rows)


def test_index_force_false_skips_recent(source_root, fresh_brain):
    _write(source_root, "shared/demo.py", PYTHON_SAMPLE)
    first = si.index_shims_source(force=True)
    assert first["skipped"] is False

    second = si.index_shims_source(force=False)
    assert second["ok"] is True
    assert second["skipped"] is True
    assert "recently_indexed" in second["reason"]


def test_index_registers_timestamp(source_root, fresh_brain):
    _write(source_root, "backend/app.py", "def main(): pass")
    result = si.index_shims_source(force=True)
    assert result["ok"] is True

    memories = ob.list_memories(namespace="system", query="shims_source")
    assert any(m["key"] == "shims_source_indexed_at" for m in memories)


def test_brain_self_index_tool(monkeypatch):
    from shared import agent_tools

    def _fake_index(*, force: bool = False):
        return {"ok": True, "files_indexed": 3, "chunks_indexed": 7}

    monkeypatch.setattr(si, "index_shims_source", _fake_index)
    result = agent_tools.run_tool("brain.self_index", {"force": True})
    assert result.get("ok") is True
    assert result.get("files_indexed") == 3


def test_brain_self_index_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "BRAIN_DB", tmp_path / "api_brain.sqlite3")
    from fastapi.testclient import TestClient
    from backend.app import main as main_module

    def _fake_index(*, force: bool = False):
        return {"ok": True, "files_indexed": 5, "chunks_indexed": 12}

    monkeypatch.setattr(main_module, "index_shims_source", _fake_index)
    client = TestClient(main_module.app)
    response = client.post("/api/brain/self-index?force=true")
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert data.get("files_indexed") == 5
