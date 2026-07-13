"""Phase 1.1 — Soul, Brain & Swarm frontend/backend wiring tests.

Covers the new v2/v3 coder endpoints, the deterministic swarm module, and the
self-index endpoint.  No local LLMs or cloud keys are required: LLM-facing
calls are monkey-patched.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import app
from shared import swarm
from shared.swarm import run_swarm, swarm_dict

client = TestClient(app)


class TestCoderV3FileEndpoints:
    def test_read_write_delete_file_roundtrip(self, tmp_path: Path):
        from shared.coder_v2 import CODER_DIR

        project_id = "testv3file"
        project_dir = CODER_DIR / project_id
        # Clean slate
        import shutil
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "_project.json").write_text('{"id":"testv3file"}')

        # Write
        r = client.post(
            f"/coder/v3/project/{project_id}/file",
            json={"path": "hello.py", "content": "print('hi')"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Read
        r = client.get(f"/coder/v3/project/{project_id}/file?path=hello.py")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["content"] == "print('hi')"

        # Delete
        r = client.delete(f"/coder/v3/project/{project_id}/file?path=hello.py")
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Read after delete
        r = client.get(f"/coder/v3/project/{project_id}/file?path=hello.py")
        assert r.json()["ok"] is False


class TestCoderV3Run:
    def test_run_project(self, tmp_path: Path):
        from shared.coder_v2 import CODER_DIR

        project_id = "testv3run"
        project_dir = CODER_DIR / project_id
        import shutil
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "_project.json").write_text('{"id":"testv3run","entry_file":"main.py"}')
        (project_dir / "main.py").write_text('print("runner works")')

        r = client.post(
            f"/coder/v3/project/{project_id}/run",
            json={"entry_file": "main.py"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "runner works" in data["stdout"]


class TestCoderV3AiIterate:
    def test_ai_iterate_uses_legacy_iterate(self):
        project_id = "testiterate"
        fake_result = {
            "ok": True,
            "project_id": project_id,
            "steps": [{"step": 1, "explanation": "done", "files_changed": ["main.py"]}],
            "files": [],
            "final_run": {"ok": True, "stdout": "ok"},
        }
        with patch("shared.coder.iterate", return_value=fake_result):
            r = client.post(
                f"/coder/v3/project/{project_id}/ai/iterate",
                json={"instruction": "make it better", "max_steps": 1},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["final_run"]["stdout"] == "ok"


class TestAgentSwarm:
    def test_swarm_endpoint_deterministic(self):
        r = client.post("/agent/swarm", json={"prompt": "write a hello world script", "use_llm": False})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "Swarm synthesis" in data["synthesis"]
        assert len(data["agents"]) == 4
        assert data["strategy"] == "deterministic"

    def test_swarm_module_default_roles(self):
        result = run_swarm("plan a trip")
        assert result.task == "plan a trip"
        roles = {a.role for a in result.agents}
        assert roles == {"planner", "coder", "reviewer", "tester"}
        assert "Plan" in result.synthesis
        assert "Implementation" in result.synthesis

    def test_swarm_dict_json_serializable(self):
        data = swarm_dict("refactor loop", agent_roles=["coder", "reviewer"])
        assert data["ok"] is True
        assert len(data["agents"]) == 2
        assert all("agent_id" in a for a in data["agents"])


class TestBrainSelfIndex:
    def test_self_index_endpoint(self):
        fake_result = {
            "ok": True,
            "skipped": False,
            "files_indexed": 12,
            "chunks_indexed": 34,
            "elapsed_s": 0.05,
        }
        # The endpoint imports index_shims_source at module load time, so patch
        # the copy bound in backend.app.main.
        with patch("backend.app.main.index_shims_source", return_value=fake_result):
            r = client.post("/api/brain/self-index?force=true")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["files_indexed"] == 12
        assert data["chunks_indexed"] == 34
