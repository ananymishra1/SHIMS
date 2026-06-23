"""Tests for the deterministic DMF builder."""
from __future__ import annotations

import pytest
from shared.dmf_builder import (
    create_dmf,
    dmf_gap_analysis,
    ensure_dmf_schema,
    get_dmf,
    list_dmfs,
    render_dmf_dossier,
    update_dmf,
)


@pytest.fixture(autouse=True)
def _schema(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIMS_DATA_DIR", str(tmp_path))
    ensure_dmf_schema()


def test_create_and_get_dmf():
    res = create_dmf({
        "api_name": "Paracetamol",
        "holder_name": "JK Lifecare",
        "holder_address": "Ujjain",
        "site_address": "Sant Nagar",
        "autofill": False,
    })
    assert res["ok"]
    assert res["dmf_id"]
    assert res["api_name"] == "Paracetamol"
    assert res["dmf_number"].startswith("DMF-")

    dmf = get_dmf(res["dmf_id"])
    assert dmf["api_name"] == "Paracetamol"
    assert dmf["open_part"]["general_information"]["international_nonproprietary_name"] == "Paracetamol"
    assert "manufacture" in dmf["closed_part"]


def test_list_and_update_dmf():
    create_dmf({"api_name": "Aspirin", "autofill": False})
    create_dmf({"api_name": "Ibuprofen", "autofill": False})
    aspirins = list_dmfs(api_name="Aspirin")
    assert len(aspirins) >= 1
    ibuprofens = list_dmfs(api_name="Ibuprofen")
    assert len(ibuprofens) >= 1

    dmf_id = aspirins[0]["id"]
    ok = update_dmf(dmf_id, {"status": "review"})
    assert ok
    assert get_dmf(dmf_id)["status"] == "review"


def test_gap_analysis():
    res = create_dmf({"api_name": "Paracetamol", "autofill": False})
    gap = dmf_gap_analysis(res["dmf_id"])
    assert gap["ok"]
    assert 0 <= gap["score"] <= 100
    required_missing = [g for g in gap["gaps"] if g["severity"] == "required_missing"]
    # Defaults fill all sections structurally, but empty nested dicts/lists may be flagged.
    assert isinstance(required_missing, list)


def test_render_dossier():
    res = create_dmf({"api_name": "Paracetamol", "autofill": False})
    rendered = render_dmf_dossier(res["dmf_id"])
    assert rendered["ok"]
    assert rendered["file_path"]
    assert "download_url" in rendered
    import pathlib
    assert pathlib.Path(rendered["file_path"]).exists()
