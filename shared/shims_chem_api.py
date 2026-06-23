"""SHIMS Chem API bridge — wraps shims_chem for use by SHIMS backends.

Provides synchronous wrappers around the chemistry copilot so FastAPI
routes can call them without managing async event loops.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from shared.shims_chem.verifier import (
    validate_smiles, flag_hazards, check_reaction_balance,
    atom_map_reaction, check_ich_q3_impurity, estimate_thermodynamics,
    score_route_feasibility, list_tools, run_tool,
)
from shared.shims_chem.retrosynthesis import plan_retrosynthesis
from shared.shims_chem.fto import score_routes


def verify_smiles(smiles: str) -> dict[str, Any]:
    return validate_smiles(smiles).to_dict()


def verify_hazards(smiles: str) -> dict[str, Any]:
    return flag_hazards(smiles).to_dict()


def verify_reaction(rxn_smiles: str) -> dict[str, Any]:
    bal = check_reaction_balance(rxn_smiles)
    atm = atom_map_reaction(rxn_smiles)
    thermo = estimate_thermodynamics(rxn_smiles)
    return {
        "balance": bal.to_dict(),
        "atom_map": atm.to_dict(),
        "thermodynamics": thermo.to_dict(),
    }


def verify_ich(impurity_pct: float, **kwargs) -> dict[str, Any]:
    return check_ich_q3_impurity(impurity_pct, **kwargs).to_dict()


def plan_retro(target_smiles: str, max_routes: int = 5) -> list[dict]:
    routes = plan_retrosynthesis(target_smiles, max_routes=max_routes)
    for r in routes:
        if r.get("steps"):
            r["feasibility"] = score_route_feasibility(r["steps"]).data
            r["feasibility_score"] = r["feasibility"]["score"]
    scored = score_routes(routes, target_smiles=target_smiles)
    return scored


def run_verifier_tool(name: str, **kwargs) -> dict[str, Any]:
    return run_tool(name, **kwargs).to_dict()


def get_tool_schemas() -> list[dict]:
    return list_tools()


# ---------------------------------------------------------------------------
# Optional FastAPI server surface: `python -m uvicorn shared.shims_chem_api:app`
# ---------------------------------------------------------------------------
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - fastapi optional for library use
    app = None  # type: ignore[assignment]
else:
    app = FastAPI(title="SHIMS Chem API", version="1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class _SmilesRequest(BaseModel):
        smiles: str

    class _ReactionRequest(BaseModel):
        rxn_smiles: str

    class _IchRequest(BaseModel):
        impurity_pct: float
        kwargs: dict[str, Any] = Field(default_factory=dict)

    class _RetroRequest(BaseModel):
        target_smiles: str
        max_routes: int = 5

    class _ToolRequest(BaseModel):
        name: str
        kwargs: dict[str, Any] = Field(default_factory=dict)

    @app.get("/")
    def _root() -> dict[str, Any]:
        return {
            "service": "SHIMS Chem API",
            "version": "1.0",
            "endpoints": [
                "GET /health",
                "GET /tools",
                "POST /verify/smiles",
                "POST /verify/hazards",
                "POST /verify/reaction",
                "POST /verify/ich",
                "POST /plan/retro",
                "POST /tool/run",
            ],
        }

    @app.get("/health")
    def _health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/tools")
    def _tools() -> list[dict]:
        return get_tool_schemas()

    @app.post("/verify/smiles")
    def _verify_smiles(req: _SmilesRequest) -> dict[str, Any]:
        try:
            return verify_smiles(req.smiles)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/verify/hazards")
    def _verify_hazards(req: _SmilesRequest) -> dict[str, Any]:
        try:
            return verify_hazards(req.smiles)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/verify/reaction")
    def _verify_reaction(req: _ReactionRequest) -> dict[str, Any]:
        try:
            return verify_reaction(req.rxn_smiles)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/verify/ich")
    def _verify_ich(req: _IchRequest) -> dict[str, Any]:
        try:
            return verify_ich(req.impurity_pct, **req.kwargs)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/plan/retro")
    def _plan_retro(req: _RetroRequest) -> list[dict]:
        try:
            return plan_retro(req.target_smiles, max_routes=req.max_routes)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/tool/run")
    def _run_tool(req: _ToolRequest) -> dict[str, Any]:
        try:
            return run_verifier_tool(req.name, **req.kwargs)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
