from pathlib import Path

import shared.mailbox as mb
import shared.omni_brain as ob


def test_capture_saves_to_mailbox_and_brain(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "MAILBOX_DB", tmp_path / "mailbox.sqlite3")
    monkeypatch.setattr(ob, "BRAIN_DB", tmp_path / "brain.sqlite3")

    result = mb.save_capture(
        title="Google collection share",
        url="https://www.google.com/collections/s/list/example",
        text="Useful shared research list.",
        source="bluetooth_share",
    )

    assert result["ok"] is True
    status = mb.mailbox_status()
    assert status["counts"]["captures"] == 1
    context = ob.retrieve_context("shared research list", limit=5)
    assert context["rag_hits"]


def test_mailbox_import_creates_digest_action_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "MAILBOX_DB", tmp_path / "mailbox.sqlite3")
    monkeypatch.setattr(ob, "BRAIN_DB", tmp_path / "brain.sqlite3")

    saved = mb.save_mail_message(
        provider="local",
        sender="buyer@example.com",
        subject="Urgent RFQ for fluconazole API",
        snippet="Please quote by Friday.",
    )

    assert saved["ok"] is True
    digest = mb.mailbox_digest()
    assert digest["counts"]["messages"] == 1
    assert digest["action_candidates"]


def test_gmail_oauth_requires_explicit_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "MAILBOX_DB", tmp_path / "mailbox.sqlite3")
    monkeypatch.delenv("SHIMS_GMAIL_CLIENT_ID", raising=False)

    auth = mb.gmail_auth_url()

    assert auth["ok"] is False
    assert auth["config"]["mode"] == "explicit_oauth_consent_required"
    assert "gmail.metadata" in auth["config"]["scopes"][0]
