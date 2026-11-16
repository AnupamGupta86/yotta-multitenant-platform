"""Send a report by e-mail over SMTP (standard library only).

Port 465 uses implicit TLS; any other port (normally 587) uses STARTTLS. Plain-text SMTP is never used.
"""

from __future__ import annotations

import smtplib
import ssl

from src.config import Settings
from src.report import build_email


class EmailError(RuntimeError):
    """The report could not be e-mailed."""


def send_report(settings: Settings, report: dict, timeout: float = 30.0) -> None:
    if not settings.email_enabled:
        raise EmailError("e-mail is not configured (SMTP_HOST, sender or recipient missing)")
    message = build_email(report, settings.email_from, settings.email_to)
    context = ssl.create_default_context()
    try:
        if settings.smtp_port == 465:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=timeout, context=context
            )
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout)
        with client:
            if settings.smtp_port != 465:
                client.starttls(context=context)
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        # The error text never contains the password
        raise EmailError(
            f"sending to {', '.join(settings.email_to)} via {settings.smtp_host} failed: {error}"
        ) from error
