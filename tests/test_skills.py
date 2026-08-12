"""Tests for the skill CRUD layer and lineage fields."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from shared import skills as sk


@pytest.fixture
def skills_dir(monkeypatch):
    """Use a project-local temp directory to avoid Windows temp path issues."""
    base = Path(__file__).resolve().parents[1] / "storage" / "_agent_test"
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(base), prefix="skills_test_") as d:
        path = Path(d)
        monkeypatch.setattr(sk, "SKILLS_DIR", path)
        yield path


def test_save_skill_persists_lineage(skills_dir):
    skill = sk.save_skill(
        "Lineage Test",
        "A skill with provenance",
        body="body text",
        tags=["test"],
        previous_version_id="skill_old_123",
        created_from="plan_learning",
    )
    loaded = sk.get_skill(skill["id"])
    assert loaded is not None
    assert loaded["previous_version_id"] == "skill_old_123"
    assert loaded["created_from"] == "plan_learning"
    assert loaded["source"] == "user"


def test_save_skill_defaults_created_from_to_source(skills_dir):
    skill = sk.save_skill(
        "Default Provenance",
        "No explicit created_from",
        source="agent_loop",
    )
    loaded = sk.get_skill(skill["id"])
    assert loaded["created_from"] == "agent_loop"
    assert "previous_version_id" not in loaded


def test_save_skill_updates_keep_lineage(skills_dir):
    first = sk.save_skill(
        "Update Test",
        "First version",
        previous_version_id="prev_1",
        created_from="feedback_distillation",
    )
    second = sk.save_skill(
        "Update Test",
        "Updated version",
        skill_id=first["id"],
    )
    assert second["previous_version_id"] == "prev_1"
    assert second["created_from"] == "feedback_distillation"
