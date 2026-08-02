"""Tests for src/report/email.py -- no real SMTP connection, no network."""

from typing import ClassVar

import pytest

from src.report import email as email_module
from src.report.email import EmailAttachment, SmtpConfig, send_report, smtp_config_from_settings

# Aliased on import -- pytest would otherwise try to collect email.py's
# test_connection() itself as a test case, since its name matches the
# test_* discovery pattern.
from src.report.email import test_connection as check_smtp_connection


def _clear_smtp_settings(monkeypatch):
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"):
        monkeypatch.setattr(email_module.settings, name, None)


def test_smtp_config_from_settings_is_none_when_unconfigured(monkeypatch):
    _clear_smtp_settings(monkeypatch)
    assert smtp_config_from_settings() is None


def test_smtp_config_from_settings_is_none_when_partially_configured(monkeypatch):
    _clear_smtp_settings(monkeypatch)
    monkeypatch.setattr(email_module.settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_module.settings, "SMTP_PORT", 587)
    # user/password/from still unset -- must stay disabled.
    assert smtp_config_from_settings() is None


def test_smtp_config_from_settings_builds_config_when_fully_set(monkeypatch):
    monkeypatch.setattr(email_module.settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_module.settings, "SMTP_PORT", 587)
    monkeypatch.setattr(email_module.settings, "SMTP_USER", "user@example.com")
    monkeypatch.setattr(email_module.settings, "SMTP_PASSWORD", "secret")
    monkeypatch.setattr(email_module.settings, "SMTP_FROM", "reports@example.com")

    config = smtp_config_from_settings()

    assert config == SmtpConfig(
        host="smtp.example.com", port=587, user="user@example.com", password="secret", sender="reports@example.com"
    )


class _FakeSmtp:
    """Records calls instead of opening a real socket."""

    instances: ClassVar[list["_FakeSmtp"]] = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.calls = []
        _FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send_message", message))


def test_send_report_sends_to_exactly_the_given_recipient(monkeypatch):
    _FakeSmtp.instances = []
    monkeypatch.setattr(email_module.smtplib, "SMTP", _FakeSmtp)

    config = SmtpConfig(host="smtp.example.com", port=587, user="me@example.com", password="pw", sender="reports@example.com")
    attachment = EmailAttachment(filename="report.pdf", content=b"%PDF-fake", mime_type="application/pdf")

    send_report(config, "recipient@example.com", "Your compliance report", "See attached.", attachment)

    assert len(_FakeSmtp.instances) == 1
    smtp = _FakeSmtp.instances[0]
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert "starttls" in smtp.calls
    assert ("login", "me@example.com", "pw") in smtp.calls

    sent_message = next(call[1] for call in smtp.calls if isinstance(call, tuple) and call[0] == "send_message")
    assert sent_message["To"] == "recipient@example.com"
    assert sent_message["From"] == "reports@example.com"
    assert sent_message["Subject"] == "Your compliance report"

    attachments = list(sent_message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "report.pdf"
    assert attachments[0].get_content() == b"%PDF-fake"


def test_connection_check_authenticates_but_never_sends(monkeypatch):
    _FakeSmtp.instances = []
    monkeypatch.setattr(email_module.smtplib, "SMTP", _FakeSmtp)
    config = SmtpConfig(host="smtp.example.com", port=587, user="me@example.com", password="pw", sender="reports@example.com")

    check_smtp_connection(config)

    assert len(_FakeSmtp.instances) == 1
    smtp = _FakeSmtp.instances[0]
    assert "starttls" in smtp.calls
    assert ("login", "me@example.com", "pw") in smtp.calls
    assert not any(isinstance(call, tuple) and call[0] == "send_message" for call in smtp.calls)


def test_connection_check_raises_on_failure(monkeypatch):
    class _FailingSmtp(_FakeSmtp):
        def login(self, user, password):
            raise RuntimeError("bad credentials")

    monkeypatch.setattr(email_module.smtplib, "SMTP", _FailingSmtp)
    config = SmtpConfig(host="smtp.example.com", port=587, user="me@example.com", password="wrong", sender="reports@example.com")

    with pytest.raises(RuntimeError, match="bad credentials"):
        check_smtp_connection(config)
