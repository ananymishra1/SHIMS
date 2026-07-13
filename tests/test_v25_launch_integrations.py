from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


def test_omni_settings_has_single_active_surface_and_save_path():
    html = _read("frontend/shims_omni.html")
    js = _read("frontend/js/shims_omni.js")

    assert html.count('id="pane-settings"') == 1
    assert 'id="pane-settings-legacy"' not in html
    for control_id in ["set-provider", "set-model", "set-voice-lang", "set-stt-model", "stt-model-status"]:
        assert html.count(f'id="{control_id}"') == 1

    assert len(re.findall(r"\b(?:async\s+)?function\s+saveSettings\s*\(", js)) == 1
    assert len(re.findall(r"\bfunction\s+setBubbleMeta\s*\(", js)) == 1
    save_block = js[js.index("async function saveSettings"): js.index("function resetSettings")]
    assert "saveProviderKeys()" in save_block
    assert "saveMediaSettings()" in save_block
    assert "state.provider" in save_block
    assert "state.privacyMode" in save_block


def test_omni_media_generation_renders_real_media_and_uses_pills():
    html = _read("frontend/shims_omni.html")
    js = _read("frontend/js/shims_omni.js")

    assert 'class="mf-pill on" data-type="image"' in html
    assert "$$('#mf-types .mf-pill')" in js
    assert "document.querySelector('.mf-pill.on')" in js
    assert "renderMediaCard = function" in js
    assert "result.url || result.file_url || result.download_url" in js
    assert "ledger proof" in js
    assert 'alt="generated image"' in js
    assert ".mf-type" not in js


def test_omni_launch_is_chat_first_without_onboarding_gate():
    html = _read("frontend/shims_omni.html")
    js = _read("frontend/js/shims_omni.js")

    assert 'id="onboarding-overlay"' not in html
    assert "Welcome to SHIMS" not in html
    assert "Get Started" not in html
    onboarding_block = js[js.index("function checkOnboarding"): js.index("function dismissOnboarding")]
    assert "style.display='flex'" not in onboarding_block
    assert "shimsOnboardingDone='true'" in onboarding_block


def test_android_is_shims_omni_with_voice_and_backend_fallback():
    java = _read("android_app/app/src/main/java/com/jklifecare/shimsmobile/MainActivity.java")
    manifest = _read("android_app/app/src/main/AndroidManifest.xml")
    app_js = _read("android_app/app/src/main/assets/shims_personal/js/app.js")
    model_manager = _read("android_app/app/src/main/java/com/jklifecare/shimsmobile/ModelManager.java")

    assert "android_asset/shims_personal/index.html" in java
    assert "sheena_wellness" not in java.lower()
    assert 'android:label="SHIMS Omni"' in manifest
    assert "Sheena Wellness" not in manifest
    assert "onShimsNativeTtsDone" in java
    assert "onShimsNativeTtsError" in java
    assert "ttsSpeaking" in app_js
    assert "Duplicate voice turn ignored" in app_js
    assert "getBackend() || await autoDetectBackend(false)" in app_js
    assert "mediaUrl = data.url || data.file_url || data.download_url" in app_js
    assert "downloadStatus()" in model_manager
    assert "runReplySmokeTest" in model_manager
    assert "window.onModelSmokeTest" in app_js


def test_enterprise_launch_ui_and_copilot_workflows_are_present():
    base = _read("shims_enterprise/templates/base.html")
    css = _read("shims_enterprise/static/style.css")
    agent_js = _read("shims_enterprise/static/enterprise_agent.js")
    agent_css = _read("shims_enterprise/static/enterprise_agent.css")

    assert 'class="topbar-status"' in base
    assert "GxP gated" in base
    assert "--radius: 8px" in css
    assert ".topbar-status" in css
    assert "Release blockers" in agent_js
    assert "CAPA draft" in agent_js
    assert "Vendor review" in agent_js
    assert "Inventory risk" in agent_js
    assert "/api/copilot/approve" in agent_js
    assert ".ec-quick" in agent_css
    assert "border-radius: 8px" in agent_css
