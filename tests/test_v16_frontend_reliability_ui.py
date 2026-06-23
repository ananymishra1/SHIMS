from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_omni_frontend_exposes_reliability_operator_ui():
    html = (ROOT / "frontend" / "shims_omni.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "js" / "shims_omni.js").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "css" / "shims_omni.css").read_text(encoding="utf-8")

    assert 'data-view="operator"' in html
    assert 'id="pane-operator"' in html
    assert "renderTrustCard" in js
    assert "/operator/digest" in js
    assert "/campaigns/plan" in js
    assert "/calendar/ics" in js
    assert "/evals/run" in js
    assert ".trust-card" in css
    assert "@media(max-width:1100px)" in css


def test_enterprise_mailbox_has_operator_campaign_and_calendar_controls():
    template = (ROOT / "shims_enterprise" / "templates" / "mailbox.html").read_text(encoding="utf-8")
    app = (ROOT / "shims_enterprise" / "app.py").read_text(encoding="utf-8")

    assert "Operator Digest" in template
    assert "Action Proof" in template
    assert "Campaign Draft" in template
    assert "Calendar ICS" in template
    assert "/api/operator/digest" in app
    assert "/api/campaigns/plan" in app
    assert "/api/calendar/ics" in app
