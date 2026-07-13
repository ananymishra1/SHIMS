"""Tests for the deterministic EHS balance/compliance engine."""
from __future__ import annotations

import pytest
from shared.ehs_balance import (
    _seed_ec_conditions,
    batch_material_balance,
    ec_limit_check,
    waste_rm_match,
)


@pytest.fixture(autouse=True)
def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIMS_DATA_DIR", str(tmp_path))
    _seed_ec_conditions()


def test_batch_balance_basic():
    rms = [
        {"name": "API starter", "quantity_kg": 10.0, "purity_pct": 98.0, "waste_pct": 2.0},
        {"name": "Methanol", "quantity_kg": 30.0, "purity_pct": 99.9, "waste_pct": 8.0},
    ]
    out = batch_material_balance("Paracetamol", 100.0, rms, outputs=None, recovery_paths=None)
    assert out["ok"]
    assert out["product_name"] == "Paracetamol"
    assert out["batch_size_kg"] == 100.0
    assert out["input_kg"] == pytest.approx(40.0)
    assert out["output_kg"] == 0.0  # no product outputs supplied
    assert out["waste_kg"] == 0.0
    assert out["emission_kg"] > 0
    assert out["effluent_kg"] > 0
    assert out["scrubber_load_kg"] >= 0
    assert out["cetp_load_kg"] >= 0
    assert out["mass_closure_pct"] > 0


def test_batch_balance_with_outputs_and_recovery():
    rms = [{"name": "Solvent-A", "quantity_kg": 50.0, "purity_pct": 99.0, "waste_pct": 10.0}]
    outputs = [{"name": "API", "quantity_kg": 40.0, "type": "product"}]
    recovery = [
        {"name": "Solvent-A distillate", "quantity_kg": 4.0, "type": "recovery"},
        {"name": "Solvent-A bottoms", "quantity_kg": 1.0, "type": "recovery"},
    ]
    out = batch_material_balance("Aspirin", 100.0, rms, outputs=outputs, recovery_paths=recovery)
    assert out["ok"]
    assert out["input_kg"] == pytest.approx(50.0)
    assert out["output_kg"] == pytest.approx(40.0)
    assert out["recovery_kg"] == pytest.approx(5.0)


def test_ec_check_limits():
    ec = ec_limit_check(
        product_mix_count=None,
        total_effluent_kld=0.0,
        fresh_water_kld=120.0,
        high_cod_kld=10.0,
    )
    assert ec["ok"]
    assert ec["conditions"]["Fresh Water Source"] == 160.0
    assert ec["statuses"]["Fresh Water Source"] == "ok"
    assert ec["statuses"]["High COD/TDS process effluent treatment"] == "ok"
    assert ec["within_limits"]


def test_ec_check_over_limits():
    ec = ec_limit_check(
        product_mix_count=None,
        total_effluent_kld=10.0,
        fresh_water_kld=170.0,
        high_cod_kld=90.0,
    )
    assert ec["statuses"]["Fresh Water Source"] == "over"
    assert ec["statuses"]["Effluent Discharge"] == "over"
    assert ec["statuses"]["High COD Waste Stream Treatment"] == "over"
    assert not ec["within_limits"]
    assert len(ec["flags"]) >= 3


def test_waste_rm_match_no_crash():
    # inventory table may not exist in test isolation; ensure no exception
    matches = waste_rm_match("spent methanol")
    assert isinstance(matches, list)
