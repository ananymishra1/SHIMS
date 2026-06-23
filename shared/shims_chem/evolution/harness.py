"""
Deterministic eval harness.

A fixed suite of cases that must keep passing — every candidate proposed by
the evolution loop is run against this BEFORE it can be promoted. If a
candidate regresses on any 'critical' case, it cannot be promoted at all,
no matter how good its other metrics. Hard gate.

Cases are tuples of (input, expectation), where expectation is one of:

  * verifier_call: (tool_name, kwargs) -> expected_truthy_field
  * intent: user_text -> expected_intent
  * hazard_fires: smiles -> expected hazard_code substring

Add more cases over time. The default suite is the baseline.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..brains.dispatcher import classify_intent_heuristic
from ..verifier import run_tool


@dataclass
class EvalCase:
    case_id: str
    kind: str                                  # "tool" | "intent" | "hazard"
    critical: bool = False
    payload: dict[str, Any] = field(default_factory=dict)
    expect: Any = None


@dataclass
class EvalReport:
    total: int = 0
    passed: int = 0
    failed_critical: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def pass_rate(self) -> float:
        return self.passed / max(1, self.total)


def default_cases() -> list[EvalCase]:
    return [
        # Hazard: phosgene must fire critical
        EvalCase("hz-phosgene", "hazard", critical=True,
                  payload={"smiles": "ClC(=O)Cl"}, expect="TOX_PHOSGENE"),
        EvalCase("hz-tbu-li", "hazard", critical=False,
                  payload={"smiles": "[Li]CC"}, expect="PYRO_ALKYLLITHIUM"),
        EvalCase("hz-ethanol-clean", "hazard", critical=True,
                  payload={"smiles": "CCO"}, expect=""),  # empty means no hazards expected
        # Tool: SMILES validate
        EvalCase("tool-validate-paracetamol", "tool", critical=True,
                  payload={"name": "validate_smiles", "kwargs": {"smiles": "CC(=O)Nc1ccc(O)cc1"}},
                  expect={"ok": True}),
        EvalCase("tool-validate-garbage", "tool", critical=True,
                  payload={"name": "validate_smiles", "kwargs": {"smiles": "XYZQ"}},
                  expect={"ok": False}),
        EvalCase("tool-balance", "tool", critical=True,
                  payload={"name": "check_reaction_balance",
                           "kwargs": {"rxn_smiles": "CCO>>CC=O"}},
                  expect={"ok": True}),
        # Intent classification
        EvalCase("intent-retro", "intent", critical=False,
                  payload={"text": "How do I synthesize aspirin?"},
                  expect="retrosynthesis"),
        EvalCase("intent-hazard", "intent", critical=True,
                  payload={"text": "Is this compound dangerous?"},
                  expect="hazard_check"),
        EvalCase("intent-fto", "intent", critical=False,
                  payload={"text": "Are there any patents covering this route?"},
                  expect="fto_analysis"),
        # ICH thresholds
        EvalCase("tool-ich-block", "tool", critical=True,
                  payload={"name": "check_ich_q3_impurity",
                           "kwargs": {"impurity_pct": 0.2, "max_daily_dose_g": 1.0}},
                  expect={"ok": False}),
    ]


def run_harness(cases: list[EvalCase] | None = None,
                 *, classifier: Callable[[str], str] | None = None) -> EvalReport:
    """Run every case. Return a report with pass/fail breakdown."""
    cases = cases or default_cases()
    classifier = classifier or classify_intent_heuristic

    rep = EvalReport(total=len(cases))
    t0 = time.time()
    for case in cases:
        try:
            ok, why = _evaluate(case, classifier)
        except Exception as e:
            ok, why = False, f"exception: {e}"
        if ok:
            rep.passed += 1
        else:
            rep.failures.append({"case_id": case.case_id, "why": why, "critical": case.critical})
            if case.critical:
                rep.failed_critical += 1
    rep.elapsed_s = round(time.time() - t0, 3)
    return rep


def _evaluate(case: EvalCase, classifier: Callable[[str], str]) -> tuple[bool, str]:
    if case.kind == "tool":
        r = run_tool(case.payload["name"], **case.payload["kwargs"])
        for k, v in (case.expect or {}).items():
            actual = getattr(r, k, None)
            if actual != v:
                return False, f"tool {case.payload['name']}.{k} = {actual!r}, expected {v!r}"
        return True, ""

    if case.kind == "intent":
        got = classifier(case.payload["text"])
        if got != case.expect:
            return False, f"intent = {got!r}, expected {case.expect!r}"
        return True, ""

    if case.kind == "hazard":
        r = run_tool("flag_hazards", smiles=case.payload["smiles"])
        codes = [i.code for i in r.issues]
        if case.expect == "":
            # No hazards expected
            if codes:
                return False, f"unexpected hazards: {codes}"
            return True, ""
        if case.expect not in codes:
            return False, f"missing expected hazard {case.expect!r}; got {codes}"
        return True, ""

    return False, f"unknown case kind: {case.kind}"
