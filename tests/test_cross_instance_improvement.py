from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from shared.cross_instance_improvement import (
    list_received_proposals,
    _save_received_proposals,
)
from shared.inter_instance_bridge import _save_peer_proposals, _load_peer_proposals


def test_save_and_load_peer_proposals(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.inter_instance_bridge.STORAGE_DIR", tmp_path)
    proposals = [
        {"type": "skill", "name": "test-skill", "summary": "demo"},
        {"type": "patch", "relative_path": "shared/demo.py", "reason": "fix"},
    ]
    ingested = _save_peer_proposals(proposals)
    assert ingested == 2
    loaded = _load_peer_proposals(limit=10)
    assert len(loaded) == 2
    assert loaded[0]["proposal"]["type"] == "skill"
    assert loaded[1]["proposal"]["type"] == "patch"


def test_save_received_proposals(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.cross_instance_improvement.SYNC_DIR", tmp_path / "sync")
    proposals = [{"type": "prompt_variant", "reason": "better concise prompt"}]
    count = _save_received_proposals("local", proposals)
    assert count == 1
    items = list_received_proposals("local", limit=10)
    assert len(items) == 1
    assert items[0]["proposal"]["type"] == "prompt_variant"
    assert items[0]["source_peer"] == "local"


def test_default_peer_id(monkeypatch):
    from shared.cross_instance_improvement import default_peer_id
    monkeypatch.setenv("SHIMS_INSTANCE_ID", "primary")
    assert default_peer_id() == "local"
    monkeypatch.setenv("SHIMS_INSTANCE_ID", "local")
    assert default_peer_id() == "primary"
