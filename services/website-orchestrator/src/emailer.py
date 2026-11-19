"""E-mail one notification per website event over SMTP (standard library only).

Configured through environment variables (the chart fills them from the Secret orchestrator-smtp):
  SMTP_HOST (default smtp.gmail.com), SMTP_PORT (default 587; 465 = implicit TLS, otherwise STARTTLS),
  SMTP_USERNAME, SMTP_PASSWORD, NOTIFY_EMAIL_TO (comma-separated), NOTIFY_EMAIL_FROM (default: the username).
Without SMTP_USERNAME, SMTP_PASSWORD and NOTIFY_EMAIL_TO, e-mail is off and events are only logged.
Plain-text SMTP is never used, and a failed e-mail never fails the event (it is logged instead).
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger("website-orchestrator")

SUBJECTS = {
    "WebsiteProvisioningRequested": "Website requested: {tenant}",
    "WebsiteCreated": "Website ready: {tenant}",
    "WebsiteDeploymentFailed": "Website deployment FAILED: {tenant}",
    "WebsiteDeletionRequested": "Website deletion requested: {tenant}",
    "WebsiteDeleted": "Website deleted: {tenant}",
}
SUMMARIES = {
    "WebsiteProvisioningRequested": "A new website was requested through the hosting-platform API. "
    "Website Deployment has been started.",
    "WebsiteCreated": "The website is deployed and healthy: https://{domain}",
    "WebsiteDeploymentFailed": "The deployment failed and was rolled back. Check the Website Deployment run.",
    "WebsiteDeletionRequested": "Deletion was requested through the hosting-platform API. "
    "Website Teardown has been started and waits for approval.",
    "WebsiteDeleted": "The website, its Helm release and its namespace were removed.",
}
FIELDS = ("event_type", "tenant", "domain", "event_id", "correlation_id", "image", "occurred_at")


def email_settings(environ=None):
    """The SMTP settings, or None when e-mail is not configured."""
    environ = environ or os.environ
    username = environ.get("SMTP_USERNAME", "").strip()
    password = environ.get("SMTP_PASSWORD", "")
    recipients = [a.strip() for a in environ.get("NOTIFY_EMAIL_TO", "").split(",") if a.strip()]
    if not (username and password and recipients):
        return None
    return {
        "host": environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(environ.get("SMTP_PORT", "587")),
        "username": username,
        "password": password,
        "sender": environ.get("NOTIFY_EMAIL_FROM", "").strip() or username,
        "to": recipients,
    }


def build_message(event, sender, recipients):
    event_type = event.get("event_type", "")
    values = {"tenant": event.get("tenant", "?"), "domain": event.get("domain", "?")}
    message = EmailMessage()
    message["Subject"] = "[myyotta] " + SUBJECTS.get(event_type, event_type + ": {tenant}").format(**values)
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    lines = [SUMMARIES.get(event_type, "A website event was published.").format(**values), ""]
    lines += [f"{name}: {event[name]}" for name in FIELDS if event.get(name)]
    message.set_content("\n".join(lines) + "\n")
    return message


def send_event_email(event, environ=None, smtp_factory=None, timeout=20.0):
    """Send the notification e-mail for one event. Never raises: returns True when sent."""
    settings = email_settings(environ)
    if settings is None:
        return False
    message = build_message(event, settings["sender"], settings["to"])
    context = ssl.create_default_context()
    try:
        if smtp_factory is not None:
            client = smtp_factory(settings["host"], settings["port"])
        elif settings["port"] == 465:
            client = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=timeout, context=context)
        else:
            client = smtplib.SMTP(settings["host"], settings["port"], timeout=timeout)
        with client:
            if settings["port"] != 465:
                client.starttls(context=context)
            client.login(settings["username"], settings["password"])
            client.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        # The error text never contains the password
        logger.error("E-mail for event_id=%s to %s failed: %s", event.get("event_id"), ", ".join(settings["to"]), error)
        return False
    logger.info("E-mail sent for event_id=%s to %s", event.get("event_id"), ", ".join(settings["to"]))
    return True
