"""Tests for the growth workstreams: behavior learning, licensing,
skill marketplace, and the cortex self-evolution layer.

These exercise the modules directly (no heavy app import), so they run in
minimal environments.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

import shared.behavior_engine as be
import shared.licensing as lic
import shared.skills as sk
import shared.skill_marketplace as mk
import shared.cortex as cx
import shared.agent_scratchpad as scratch


# --------------------------------------------------------------------------- #
# Scratchpad persistence (the "agent loses memory on restart" bug fix)
# --------------------------------------------------------------------------- #

class TestScratchpadPersistence:
    def setup_method(self):
        scratch._SCRATCHPAD_DIR = Path(tempfile.mkdtemp())

    def test_full_state_survives_restart(self):
        pad = scratch.AgentScratchpad("sess1")
        pad.goal = "Ship the feature"
        pad.set_plan([
            {"tool": "fs.read", "args": {"path": "a"}, "reason": "inspect"},
            {"tool": "shell.run", "args": {"command": "pytest"}, "reason": "test"},
        ])
        pad.mark_step_done(0, "looks good")
        pad.observe(0, "fs.read", {"path": "a"}, {"ok": True, "content": "hello"})
        pad.note("watch out for the edge case")

        # Simulate a server restart: brand-new instance, same session id.
        reloaded = scratch.AgentScratchpad("sess1")
        assert reloaded.goal == "Ship the feature"
        assert len(reloaded.plan_steps) == 2
        assert reloaded.plan_steps[0].status == "done"
        assert reloaded.plan_steps[0].result_summary == "looks good"
        assert len(reloaded.observations) == 1
        assert reloaded.observations[0].tool == "fs.read"
        assert "watch out for the edge case" in reloaded.notes

    def test_prompt_rebuilt_after_reload(self):
        pad = scratch.AgentScratchpad("sess2")
        pad.goal = "Investigate"
        pad.set_plan([{"tool": "fs.list", "args": {}, "reason": "scan"}])
        reloaded = scratch.AgentScratchpad("sess2")
        prompt = reloaded.to_prompt()
        assert "Investigate" in prompt and "fs.list" in prompt


# --------------------------------------------------------------------------- #
# Behavior engine
# --------------------------------------------------------------------------- #

class TestBehaviorEngine:
    def setup_method(self):
        be._STATE_DIR = Path(tempfile.mkdtemp())

    def test_learns_sequence(self):
        eng = be.BehaviorEngine("seq")
        base = time.time()
        for i in range(6):
            eng.record("a", ts=base + i * 100)
            eng.record("b", ts=base + i * 100 + 50)
        # After "a", "b" should be the top prediction.
        eng._last_action = "a"
        preds = eng.predict()
        assert preds and preds[0].action == "b"
        assert preds[0].confidence > 0

    def test_confidence_tiers(self):
        p = be.Prediction("x", 0.9)
        assert p.tier == "auto"
        assert be.Prediction("x", 0.75).tier == "suggest"
        assert be.Prediction("x", 0.55).tier == "rank"
        assert be.Prediction("x", 0.2).tier == "silent"

    def test_feedback_reinforcement(self):
        eng = be.BehaviorEngine("fb")
        for _ in range(5):
            eng.record("deploy")
        before = eng.predict(top_k=1)[0].confidence
        eng.reinforce("deploy", positive=True)
        after = eng.predict(top_k=1)[0].confidence
        assert after >= before

    def test_persistence_round_trip(self):
        eng = be.BehaviorEngine("persist")
        for _ in range(4):
            eng.record("task1")
        eng2 = be.BehaviorEngine("persist")
        assert eng2.totals.get("task1", 0) >= 4

    def test_to_context_format(self):
        eng = be.BehaviorEngine("ctx")
        for _ in range(8):
            eng.record("open_editor")
        ctx = eng.to_context()
        # Either a signals block or empty (if below threshold) — never raises.
        assert ctx == "" or "BEHAVIOR SIGNALS" in ctx

    def test_reset(self):
        eng = be.BehaviorEngine("rst")
        eng.record("x")
        eng.reset()
        assert eng.predict() == []


# --------------------------------------------------------------------------- #
# Licensing
# --------------------------------------------------------------------------- #

class TestLicensing:
    def teardown_method(self):
        os.environ.pop("SHIMS_LICENSE_KEY", None)

    def test_issue_and_verify(self):
        key = lic.issue_license("pro", "user@x.com", valid_days=30)
        L = lic.verify_license(key)
        assert L is not None and L.tier == lic.Tier.PRO and L.valid

    def test_tamper_detection(self):
        key = lic.issue_license("enterprise")
        assert lic.verify_license(key[:-1] + ("y" if key[-1] != "y" else "z")) is None

    def test_expiry(self):
        key = lic.issue_license("pro", valid_days=-1)  # already expired
        L = lic.verify_license(key)
        assert L is not None and L.is_expired and not L.valid

    def test_entitlement_gating(self):
        key = lic.issue_license("pro")
        os.environ["SHIMS_LICENSE_KEY"] = key
        assert lic.is_entitled("session_export")      # pro
        assert lic.is_entitled("core_agent")          # community
        assert not lic.is_entitled("sso")             # enterprise

    def test_default_is_community(self):
        assert lic.current_tier() == lic.Tier.COMMUNITY
        assert not lic.is_entitled("priority_routing")

    def test_require_upsell_payload(self):
        allowed, payload = lic.require("sso")
        assert not allowed
        assert payload["required_tier"] == "enterprise"
        assert "upgrade_url" in payload

    def test_durable_key_survives_without_env(self, tmp_path, monkeypatch):
        # Activated key persists to a file and is honoured when env is unset.
        store = tmp_path / "license.key"
        monkeypatch.setattr(lic, "_license_file", lambda: store)
        key = lic.issue_license("pro", "team@x.com", valid_days=10)
        assert lic.save_license_key(key)
        monkeypatch.delenv("SHIMS_LICENSE_KEY", raising=False)
        assert lic.current_tier() == lic.Tier.PRO
        assert lic.is_entitled("session_export")


# --------------------------------------------------------------------------- #
# Skill marketplace
# --------------------------------------------------------------------------- #

class TestMarketplace:
    def setup_method(self):
        sk.SKILLS_DIR = Path(tempfile.mkdtemp())

    def test_catalog_and_categories(self):
        cats = mk.categories()
        assert "All" in cats
        assert len(mk.list_catalog()) >= 5

    def test_install(self):
        r = mk.install("concise-engineer")
        assert r["ok"]
        names = {s["name"] for s in sk.list_skills()}
        assert "Concise Engineer" in names

    def test_install_unknown(self):
        assert not mk.install("does-not-exist")["ok"]

    def test_export_import_round_trip(self):
        mk.install("safe-shell")
        pack = mk.export_pack()
        assert pack["format"] == "shims-skill-pack"
        # Fresh store, import the pack.
        sk.SKILLS_DIR = Path(tempfile.mkdtemp())
        res = mk.import_pack(pack)
        assert res["ok"] and res["imported"] >= 1

    def test_import_rejects_bad_pack(self):
        assert not mk.import_pack({"format": "nope"})["ok"]


# --------------------------------------------------------------------------- #
# Cortex
# --------------------------------------------------------------------------- #

class TestCortex:
    def setup_method(self):
        sk.SKILLS_DIR = Path(tempfile.mkdtemp())

    def test_kernel_paths_protected(self):
        assert cx.is_kernel_path("shims_core/self_evolution.py")
        assert cx.is_kernel_path("backend/app/main.py")
        assert cx.is_kernel_path("shared/guardians.py")
        assert not cx.is_kernel_path("data/state/cortex/x.json")
        assert not cx.is_kernel_path("frontend/landing.html")

    def test_code_change_requires_approval(self):
        res = cx.apply_change([{"path": "backend/app/main.py", "content": "x"}], approved=False)
        assert not res["ok"]

    def test_evolve_skill_confidence_gate(self):
        # Low confidence → suggest/hold, no write.
        low = cx.evolve_skill("S", "s", confidence=0.4)
        assert low["decision"] in ("suggest", "hold")
        # High confidence → applied.
        high = cx.evolve_skill("S2", "s2", confidence=0.95)
        assert high["decision"] == "applied"

    def test_prompt_overlay_round_trip(self):
        cx._PROMPT_OVERLAY = Path(tempfile.mkdtemp()) / "overlay.json"
        cx.set_prompt_overlay("Always be brief.", reason="test")
        assert "brief" in cx.get_prompt_overlay()

    def test_status_shape(self):
        st = cx.status()
        assert st["architecture"] == "kernel/cortex"
        assert "cortex_auto_apply_confidence" in st["gates"]
