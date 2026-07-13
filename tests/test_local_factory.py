from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from shared.local_factory_config import (
    chemistry_model,
    coder_model,
    default_model,
    heavy_model,
    is_factory_instance,
    resolve_role_model,
)
from shared.local_factory_corpus import corpus_stats
from shared.factory_evolution_loop import evolution_status, load_state


class TestLocalFactoryConfig:
    def test_factory_instance_false_by_default(self, monkeypatch):
        monkeypatch.setattr("shared.local_factory_config.INSTANCE_ID", "primary")
        monkeypatch.setattr("shared.local_factory_config.FACTORY_MODE", False)
        assert is_factory_instance() is False

    def test_factory_instance_true_when_local(self, monkeypatch):
        monkeypatch.setattr("shared.local_factory_config.INSTANCE_ID", "local")
        monkeypatch.setattr("shared.local_factory_config.FACTORY_MODE", False)
        assert is_factory_instance() is True

    def test_factory_models_primary_fallback(self, monkeypatch):
        monkeypatch.setattr("shared.local_factory_config.INSTANCE_ID", "primary")
        monkeypatch.setattr("shared.local_factory_config.FACTORY_MODE", False)
        monkeypatch.delenv("SHIMS_FACTORY_DEFAULT_MODEL", raising=False)
        monkeypatch.delenv("SHIMS_FACTORY_HEAVY_MODEL", raising=False)
        monkeypatch.delenv("SHIMS_FACTORY_CHEMISTRY_MODEL", raising=False)
        monkeypatch.delenv("SHIMS_FACTORY_CODER_MODEL", raising=False)
        assert default_model() == os.getenv("SHIMS_OLLAMA_MODEL", "llama3.2:latest")
        assert heavy_model() == default_model()
        assert chemistry_model() == "chemdfm"
        assert isinstance(coder_model(), str)

    def test_factory_models_local_defaults(self, monkeypatch):
        monkeypatch.setattr("shared.local_factory_config.INSTANCE_ID", "local")
        monkeypatch.setattr("shared.local_factory_config.FACTORY_MODE", False)
        monkeypatch.delenv("SHIMS_FACTORY_DEFAULT_MODEL", raising=False)
        monkeypatch.delenv("SHIMS_FACTORY_HEAVY_MODEL", raising=False)
        monkeypatch.delenv("SHIMS_FACTORY_CHEMISTRY_MODEL", raising=False)
        monkeypatch.delenv("SHIMS_FACTORY_CODER_MODEL", raising=False)
        assert default_model() == "qwen2.5:3b"
        assert heavy_model() == "qwen2.5:7b"
        assert chemistry_model() == "chemdfm"
        assert coder_model() == "qwen2.5-coder:14b"

    def test_resolve_role_model_mapping(self, monkeypatch):
        monkeypatch.setattr("shared.local_factory_config.INSTANCE_ID", "local")
        monkeypatch.setattr("shared.local_factory_config.FACTORY_MODE", False)
        monkeypatch.delenv("SHIMS_FACTORY_DEFAULT_MODEL", raising=False)
        monkeypatch.delenv("SHIMS_FACTORY_HEAVY_MODEL", raising=False)
        monkeypatch.delenv("SHIMS_FACTORY_CHEMISTRY_MODEL", raising=False)
        assert resolve_role_model("chemistry") == "chemdfm"
        assert resolve_role_model("heavy") == "qwen2.5:7b"
        assert resolve_role_model("fast") == "qwen2.5:3b"
        assert resolve_role_model("unknown") == "qwen2.5:3b"


class TestPeerConfig:
    def test_peers_file_uses_config_dir(self, tmp_path, monkeypatch):
        peers = tmp_path / "peers.json"
        peers.write_text(
            json.dumps(
                {
                    "token": "test-token-123",
                    "instances": [
                        {"id": "primary", "url": "http://127.0.0.1:8010"},
                        {"id": "local", "url": "http://127.0.0.1:8030"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("SHIMS_PEERS_FILE", str(peers))
        from shared.local_factory_config import peers_file

        assert peers_file() == peers


class TestCorpusAndEvolution:
    def test_corpus_stats_returns_shape(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SHIMS_STORAGE_DIR", str(tmp_path / "storage_local"))
        stats = corpus_stats()
        assert stats["ok"] is True
        assert "files" in stats
        assert "total_chunks" in stats

    def test_evolution_status_shape(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SHIMS_STORAGE_DIR", str(tmp_path / "storage_local"))
        monkeypatch.setattr("shared.factory_evolution_loop.INSTANCE_ID", "local")
        status = evolution_status()
        assert status["ok"] is True
        assert status["instance_id"] == "local"
        assert status["status"] in ("idle", "training", "building_corpus", "evaluating", "error")
        assert "best_score" in status
        assert "corpus" in status

    def test_load_state_creates_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SHIMS_STORAGE_DIR", str(tmp_path / "storage_local"))
        monkeypatch.setattr("shared.factory_evolution_loop.INSTANCE_ID", "local")
        state = load_state()
        assert state["best_score"] == 0.0
        assert state["status"] == "idle"


class TestPeerClient:
    def test_peer_client_headers_and_paths(self, tmp_path, monkeypatch):
        peers = tmp_path / "peers.json"
        peers.write_text(
            json.dumps(
                {
                    "token": "peer-token-abc",
                    "instances": [
                        {"id": "local", "url": "http://127.0.0.1:8030"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("SHIMS_PEERS_FILE", str(peers))
        from shared.inter_instance_bridge import PeerClient, get_peer

        peer = get_peer("local")
        assert peer is not None
        client = PeerClient(peer)
        assert client._headers()["X-Peer-Token"] == "peer-token-abc"
        assert client._path("health") == "http://127.0.0.1:8030/api/peer/health"


@pytest.mark.skipif(
    os.getenv("SKIP_LIVE_FACTORY") == "1",
    reason="live factory integration disabled",
)
class TestLiveFactoryIntegration:
    def test_factory_status_endpoint(self):
        import httpx

        r = httpx.get("http://127.0.0.1:8030/api/factory/status", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["instance_id"] == "local"

    def test_peer_health_endpoint(self):
        import httpx

        r = httpx.get("http://127.0.0.1:8030/api/peer/health", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["instance_id"] == "local"
