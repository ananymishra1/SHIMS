"""Tests for shims_chem verifier, API bridge, and backend routes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shared.shims_chem.verifier import (
    validate_smiles,
    flag_hazards,
    check_reaction_balance,
    check_ich_q3_impurity,
    list_tools,
    run_tool,
)
from shared.shims_chem.verifier.types import Issue, ToolResult, VerifierError
from shared.shims_chem.verifier.hazards import HAZARD_RULES
from shared import shims_chem_api

from shims_enterprise.app import app as enterprise_app
from shims_omni.app import app as omni_app


# ─── Verifier Unit Tests ───────────────────────────────────────────────────

class TestValidateSmiles:
    def test_valid_smiles_paracetamol(self):
        r = validate_smiles("CC(=O)Nc1ccc(O)cc1")
        assert r.ok is True
        assert r.tool == "validate_smiles"
        assert r.data["report"]["valid"] is True
        assert r.data["report"]["mol_weight"] is not None

    def test_valid_smiles_ethanol(self):
        r = validate_smiles("CCO")
        assert r.ok is True
        assert r.data["report"]["canonical_smiles"] is not None

    def test_invalid_empty(self):
        r = validate_smiles("")
        assert r.ok is False
        assert any(i.code == "empty" for i in r.issues)

    def test_invalid_unbalanced_parens(self):
        r = validate_smiles("CC(=O")
        assert r.ok is False
        assert any("paren" in i.code for i in r.issues)

    def test_invalid_ring_closure(self):
        r = validate_smiles("C1CCCCC")
        assert r.ok is False
        assert any("ring_unclosed" in i.code for i in r.issues)


class TestFlagHazards:
    def test_phosgene_critical(self):
        r = flag_hazards("ClC(=O)Cl")
        assert any(i.code == "TOX_PHOSGENE" and i.severity == "critical" for i in r.issues)
        assert r.ok is False  # critical = not ok

    def test_alkyllithium_error(self):
        r = flag_hazards("[Li]CC")
        assert any(i.code == "PYRO_ALKYLLITHIUM" and i.severity == "error" for i in r.issues)

    def test_safe_molecule_no_hazards(self):
        r = flag_hazards("CCO")
        assert r.ok is True
        assert len(r.data["fired"]) == 0

    def test_rule_count_matches(self):
        r = flag_hazards("CCO")
        assert r.data["rule_count"] == len(HAZARD_RULES)


class TestReactionBalance:
    def test_balanced_reaction(self):
        r = check_reaction_balance("CCO>>CC=O")
        assert isinstance(r, ToolResult)
        report = r.data.get("report", r.data)
        assert "reactants" in report or "products" in report or not r.ok

    def test_malformed_reaction(self):
        r = check_reaction_balance("not_a_reaction")
        assert r.ok is False


class TestIchQ3:
    def test_impurity_below_threshold(self):
        r = check_ich_q3_impurity(0.01, max_daily_dose_g=1.0)
        assert r.ok is True
        assert "threshold" in r.data or "q3a" in r.data

    def test_impurity_at_threshold(self):
        r = check_ich_q3_impurity(0.15, max_daily_dose_g=1.0)
        # At qualification threshold returns error
        assert any(i.severity == "error" for i in r.issues)

    def test_impurity_zero(self):
        r = check_ich_q3_impurity(0.0, max_daily_dose_g=1.0)
        assert r.ok is True


class TestToolRegistry:
    def test_list_tools_non_empty(self):
        tools = list_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0
        for t in tools:
            # OpenAI function format: {type: "function", function: {name: ..., ...}}
            assert "function" in t
            assert "name" in t["function"]

    def test_run_tool_validate(self):
        r = run_tool("validate_smiles", smiles="CCO")
        assert r.ok is True
        assert r.tool == "validate_smiles"

    def test_run_tool_unknown_raises(self):
        with pytest.raises(VerifierError):
            run_tool("nonexistent_tool")


# ─── API Bridge Tests ──────────────────────────────────────────────────────

class TestChemApiBridge:
    def test_verify_smiles(self):
        d = shims_chem_api.verify_smiles("CCO")
        assert d["ok"] is True
        assert d["tool"] == "validate_smiles"

    def test_verify_hazards(self):
        d = shims_chem_api.verify_hazards("ClC(=O)Cl")
        assert d["ok"] is False
        assert any(i["code"] == "TOX_PHOSGENE" for i in d["issues"])

    def test_verify_reaction(self):
        d = shims_chem_api.verify_reaction("CCO>>CC=O")
        assert "balance" in d
        assert "atom_map" in d
        assert "thermodynamics" in d

    def test_verify_ich(self):
        d = shims_chem_api.verify_ich(0.01, max_daily_dose_g=1.0)
        assert d["ok"] is True

    def test_plan_retro(self):
        routes = shims_chem_api.plan_retro("CCO", max_routes=3)
        assert isinstance(routes, list)

    def test_get_tool_schemas(self):
        schemas = shims_chem_api.get_tool_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) > 0

    def test_run_verifier_tool(self):
        d = shims_chem_api.run_verifier_tool("validate_smiles", smiles="CCO")
        assert d["ok"] is True


# ─── FastAPI Route Tests ───────────────────────────────────────────────────

class TestEnterpriseChemRoutes:
    @pytest.fixture
    def client(self):
        with TestClient(enterprise_app) as c:
            # Log in to establish session
            c.post("/login", data={"username": "admin", "password": "SHIMS2025!"}, follow_redirects=False)
            yield c

    def test_chem_tools_list(self, client):
        r = client.get("/api/rd/chem/tools")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) > 0

    def test_chem_verify_valid(self, client):
        r = client.post("/api/rd/chem/verify", json={"smiles": "CCO"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    def test_chem_verify_invalid(self, client):
        r = client.post("/api/rd/chem/verify", json={"smiles": ""})
        assert r.status_code == 200
        data = r.json()
        # Top-level ok=True; verifier result nested under 'smiles'
        assert data["smiles"]["ok"] is False

    def test_chem_reaction(self, client):
        r = client.post("/api/rd/chem/reaction", json={"rxn_smiles": "CCO>>CC=O"})
        assert r.status_code == 200
        data = r.json()
        assert "result" in data
        assert "balance" in data["result"]

    def test_chem_retro(self, client):
        r = client.post("/api/rd/chem/retro", json={"target_smiles": "CCO", "max_routes": 3})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert isinstance(data["routes"], list)

    def test_chem_tool_run(self, client):
        r = client.post("/api/rd/chem/tools/validate_smiles", json={"args": {"smiles": "CCO"}})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    def test_chem_tool_run_unknown(self, client):
        r = client.post("/api/rd/chem/tools/nonexistent", json={})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False

    def test_chem_ich(self, client):
        r = client.post("/api/rd/chem/ich", json={"impurity_pct": 0.01, "max_daily_dose_g": 1.0})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "result" in data


class TestOmniChemRoutes:
    @pytest.fixture
    def client(self):
        with TestClient(omni_app) as c:
            yield c

    def test_chem_tools_list(self, client):
        r = client.get("/api/rd/chem/tools")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert isinstance(data["tools"], list)

    def test_chem_verify(self, client):
        r = client.post("/api/rd/chem/verify", json={"smiles": "CC(=O)Nc1ccc(O)cc1"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    def test_chem_reaction(self, client):
        r = client.post("/api/rd/chem/reaction", json={"rxn_smiles": "CCO>>CC=O"})
        assert r.status_code == 200
        data = r.json()
        assert "result" in data
        assert "balance" in data["result"]

    def test_chem_retro(self, client):
        r = client.post("/api/rd/chem/retro", json={"target_smiles": "CCO", "max_routes": 2})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert isinstance(data["routes"], list)

    def test_chem_tool_run(self, client):
        r = client.post("/api/rd/chem/tools/validate_smiles", json={"args": {"smiles": "CCO"}})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    def test_chem_ich(self, client):
        r = client.post("/api/rd/chem/ich", json={"impurity_pct": 0.01, "max_daily_dose_g": 1.0})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "result" in data
