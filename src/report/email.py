# DESIGN RULE: this is the one place in src/report/ that does network I/O
# (an SMTP connection), the same role src/category/embedder.py's cache and
# src/extractors/gemini.py's API calls play for their stages. send_report()
# takes its SMTP config as a plain argument rather than reading
# config.settings itself, so it stays testable without an environment --
# only smtp_config_from_settings() (the boundary function) touches
# config.settings.
"""Opt-in email delivery for a finished report.

USER-INITIATED AND USER-ADDRESSED ONLY. There is no default recipient
anywhere in this module or in config.py, and send_report() never fires on
its own -- the caller (app.py) must collect a recipient from an explicit
user action (typing an address and pressing Send) every time. This module
does not remember a previous recipient between calls.

SMTP credentials are read from .env as five OPTIONAL settings
(SMTP_HOST/PORT/USER/PASSWORD/FROM, see config.py) -- if any is absent,
smtp_config_from_settings() returns None and the caller shows "email is not
configured" instead of a form that would fail. Settings() itself must never
fail to construct just because nobody set up outbound email.
"""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from config import settings


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content: bytes
    mime_type: str = "application/octet-stream"


def smtp_config_from_settings() -> SmtpConfig | None:
    """None when any of the five SMTP settings is missing from .env -- the
    caller uses this to decide whether to show the email section at all."""
    if not (
        settings.SMTP_HOST
        and settings.SMTP_PORT
        and settings.SMTP_USER
        and settings.SMTP_PASSWORD
        and settings.SMTP_FROM
    ):
        return None
    return SmtpConfig(
        host=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        user=settings.SMTP_USER,
        password=settings.SMTP_PASSWORD,
        sender=settings.SMTP_FROM,
    )


def test_connection(smtp_config: SmtpConfig) -> None:
    """Open the SMTP connection and authenticate, without sending anything --
    lets the UI verify freshly-typed credentials before a real send. Raises
    on any failure, exactly like send_report; the caller is responsible for
    catching it and reporting the failure."""
    with smtplib.SMTP(smtp_config.host, smtp_config.port) as server:
        server.starttls()
        server.login(smtp_config.user, smtp_config.password)


def send_report(
    smtp_config: SmtpConfig, recipient: str, subject: str, body: str, attachment: EmailAttachment
) -> None:
    """Send `body` (plain text) to `recipient`, with one file attached, over
    STARTTLS. Raises on any SMTP failure -- the caller is responsible for
    catching it and telling the user the send failed; this function never
    swallows an error silently, since a report that appears sent but wasn't
    is worse than a visible failure.
    """
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_config.sender
    message["To"] = recipient
    message.set_content(body)

    maintype, _, subtype = attachment.mime_type.partition("/")
    message.add_attachment(
        attachment.content,
        maintype=maintype or "application",
        subtype=subtype or "octet-stream",
        filename=attachment.filename,
    )

    with smtplib.SMTP(smtp_config.host, smtp_config.port) as server:
        server.starttls()
        server.login(smtp_config.user, smtp_config.password)
        server.send_message(message)
