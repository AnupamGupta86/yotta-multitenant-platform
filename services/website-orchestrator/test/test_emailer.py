import smtplib
import unittest
from typing import ClassVar

from src.consumer import handle_event
from src.emailer import build_message, email_settings, send_event_email

CONFIG = {
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "587",
    "SMTP_USERNAME": "platform@example.com",
    "SMTP_PASSWORD": "app-password",
    "NOTIFY_EMAIL_TO": "ops@example.com, dev@example.com",
}
EVENT = {
    "event_type": "WebsiteCreated",
    "event_id": "event-1",
    "tenant": "user2",
    "domain": "user2.myyotta.com",
    "correlation_id": "event-0",
}


class FakeSMTP:
    instances: ClassVar[list] = []

    def __init__(self, host, port, fail=False):
        self.host, self.port, self.fail = host, port, fail
        self.calls, self.sent = [], []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append(("login", username))
        if self.fail:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, message):
        self.sent.append(message)


class EmailerTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []

    def test_off_without_username_password_or_recipient(self):
        self.assertIsNone(email_settings({}))
        self.assertIsNone(email_settings({**CONFIG, "NOTIFY_EMAIL_TO": " "}))
        self.assertFalse(send_event_email(EVENT, environ={"SMTP_USERNAME": "x"}, smtp_factory=FakeSMTP))
        self.assertEqual(FakeSMTP.instances, [])

    def test_settings_default_to_gmail_starttls_and_sender_is_the_username(self):
        settings = email_settings({k: v for k, v in CONFIG.items() if k not in ("SMTP_HOST", "SMTP_PORT")})
        self.assertEqual((settings["host"], settings["port"]), ("smtp.gmail.com", 587))
        self.assertEqual(settings["sender"], "platform@example.com")
        self.assertEqual(settings["to"], ["ops@example.com", "dev@example.com"])

    def test_message_names_the_event_and_tenant(self):
        message = build_message(EVENT, "platform@example.com", ["ops@example.com"])
        self.assertEqual(message["Subject"], "[myyotta] Website ready: user2")
        body = message.get_content()
        self.assertIn("https://user2.myyotta.com", body)
        self.assertIn("event_id: event-1", body)
        failed = build_message({**EVENT, "event_type": "WebsiteDeploymentFailed"}, "a@b.c", ["d@e.f"])
        self.assertIn("FAILED", failed["Subject"])

    def test_sends_over_starttls_with_login(self):
        self.assertTrue(send_event_email(EVENT, environ=CONFIG, smtp_factory=FakeSMTP))
        client = FakeSMTP.instances[0]
        self.assertEqual((client.host, client.port), ("smtp.example.com", 587))
        self.assertEqual(client.calls, ["starttls", ("login", "platform@example.com")])
        self.assertEqual(client.sent[0]["To"], "ops@example.com, dev@example.com")

    def test_a_failed_email_is_logged_not_raised(self):
        with self.assertLogs("website-orchestrator", level="ERROR") as logs:
            sent = send_event_email(
                EVENT, environ=CONFIG, smtp_factory=lambda host, port: FakeSMTP(host, port, fail=True)
            )
        self.assertFalse(sent)
        self.assertNotIn("app-password", "\n".join(logs.output))

    def test_every_handled_event_is_emailed_once_after_processing(self):
        emailed = []
        for event_type in (
            "WebsiteProvisioningRequested",
            "WebsiteCreated",
            "WebsiteDeploymentFailed",
            "WebsiteDeletionRequested",
            "WebsiteDeleted",
        ):
            handle_event(
                {**EVENT, "event_type": event_type},
                dispatch=lambda _e: None,
                notify=lambda _e: None,
                teardown=lambda _e: None,
                email=lambda e: emailed.append(e["event_type"]),
            )
        self.assertEqual(len(emailed), 5)
        handle_event({"event_type": "SomethingElse"}, email=lambda e: emailed.append(e))
        self.assertEqual(len(emailed), 5)

    def test_no_email_when_processing_fails(self):
        emailed = []

        def broken_dispatch(_event):
            raise RuntimeError("GitHub down")

        with self.assertRaises(RuntimeError):
            handle_event({**EVENT, "event_type": "WebsiteProvisioningRequested"}, dispatch=broken_dispatch,
                         email=emailed.append)
        self.assertEqual(emailed, [])


if __name__ == "__main__":
    unittest.main()
