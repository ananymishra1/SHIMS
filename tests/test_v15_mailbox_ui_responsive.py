from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


def test_omni_exposes_mailbox_and_capture_ui():
    html = _read("frontend/shims_omni.html")
    js = _read("frontend/js/shims_omni.js")

    assert 'data-view="mailbox"' in html
    assert 'id="pane-mailbox"' in html
    assert "function loadMailboxPane" in js
    assert "/capture/share" in js
    assert "/mailbox/oauth/start" in js
    assert "/mailbox/gmail/sync" in js
    assert "drawer-open" in js
    assert "mobile-menu-btn" in js


def test_enterprise_exposes_mailbox_page_and_nav():
    app = _read("shims_enterprise/app.py")
    template = _read("shims_enterprise/templates/mailbox.html")

    assert "'/mailbox'" in app
    assert "'/api/capture/share'" in app
    assert "'/api/mailbox/import'" in app
    assert "Mailbox & Capture" in template
    assert "OAuth consent required" in template


def test_half_screen_responsive_rules_exist():
    omni_css = _read("frontend/css/shims_omni.css")
    enterprise_css = _read("shims_enterprise/static/style.css")
    app_js = _read("shims_enterprise/static/app.js")

    assert "@media(max-width:1100px) and (min-width:481px)" in omni_css
    # dock is now single-row (compact redesign); verify dock + forge responsive rules exist
    assert ".dock{" in omni_css
    assert ".forge-wrap{flex-direction:column" in omni_css
    assert "@media (max-width: 1100px) and (min-width: 769px)" in enterprise_css
    assert ".table-wrap" in enterprise_css
    assert "document.getElementById('inventoryChart')" in app_js
