import os
import smtplib
import tempfile
import unittest
from unittest import mock

from test_doctor import FakeGrok, FakeKube, crashing

from src.config import Settings
from src.cronjob import run_once, state_key
from src.emailer import EmailError, send_report
from src.rca import describe_pod
from src.report import build_email, subject

EMAIL = {"smtp_host": "smtp.example.com", "email_from": "aiops@example.com", "email_to": ("me@example.com",)}


class StateKube(FakeKube):
    """FakeKube plus the ConfigMap the CronJob keeps its state in."""

    def __init__(self, pods, state=None):
        super().__init__(pods)
        self.state = state
        self.saves = []

    def get_configmap(self, namespace, name):
        return None if self.state is None else dict(self.state)

    def save_configmap(self, namespace, name, data, exists):
        self.saves.append((dict(data), exists))
        self.state = dict(data)


class RunOnceTests(unittest.TestCase):
    def test_emails_one_failing_pod_and_remembers_it(self):
        kube, sent = StateKube([crashing("a"), crashing("b")]), []
        code = run_once(Settings(**EMAIL), kube, FakeGrok(), mailer=lambda s, r: sent.append(r))
        self.assertEqual(code, 0)
        self.assertEqual([report["pod"] for report in sent], ["a"])  # one pod per run
        self.assertEqual(list(kube.state), [state_key("demo/a/app/CrashLoopBackOff")])
        self.assertFalse(kube.saves[0][1])  # ConfigMap created on first run

    def test_next_run_reports_the_next_pod_not_the_same_one_again(self):
        kube, sent = StateKube([crashing("a"), crashing("b")]), []
        settings = Settings(**EMAIL)
        run_once(settings, kube, FakeGrok(), mailer=lambda s, r: sent.append(r))
        run_once(settings, kube, FakeGrok(), mailer=lambda s, r: sent.append(r))
        run_once(settings, kube, FakeGrok(), mailer=lambda s, r: sent.append(r))
        self.assertEqual([report["pod"] for report in sent], ["a", "b"])

    def test_recovered_pod_is_forgotten_and_reported_again_later(self):
        kube, sent = StateKube([crashing("a")]), []
        settings = Settings(**EMAIL)
        run_once(settings, kube, FakeGrok(), mailer=lambda s, r: sent.append(r))
        kube.pods = []
        run_once(settings, kube, FakeGrok(), mailer=lambda s, r: sent.append(r))
        self.assertEqual(kube.state, {})
        kube.pods = [crashing("a")]
        run_once(settings, kube, FakeGrok(), mailer=lambda s, r: sent.append(r))
        self.assertEqual(len(sent), 2)

    def test_failed_email_is_retried_on_the_next_run(self):
        kube = StateKube([crashing("a")])

        def broken(settings, report):
            raise EmailError("smtp down")

        self.assertEqual(run_once(Settings(**EMAIL), kube, FakeGrok(), mailer=broken), 1)
        self.assertEqual(kube.state, {})  # not marked as reported

    def test_without_email_settings_the_report_is_logged_once(self):
        kube = StateKube([crashing("a")])
        mailer = mock.Mock()
        self.assertEqual(run_once(Settings(), kube, FakeGrok(), mailer=mailer), 0)
        mailer.assert_not_called()
        self.assertEqual(len(kube.state), 1)

    def test_healthy_cluster_sends_nothing(self):
        kube, mailer = StateKube([], state={}), mock.Mock()
        self.assertEqual(run_once(Settings(**EMAIL), kube, FakeGrok(), mailer=mailer), 0)
        mailer.assert_not_called()
        self.assertEqual(kube.saves, [])  # nothing changed, nothing written

    def test_report_contains_rca_context(self):
        kube, sent = StateKube([crashing("a")]), []
        run_once(Settings(**EMAIL), kube, FakeGrok(), mailer=lambda s, r: sent.append(r))
        report = sent[0]
        self.assertEqual(report["analysis"], "Root cause: test")
        self.assertIn("previous crash log", report["log_excerpt"])
        self.assertIn("BackOff", report["events"])
        self.assertIn("Owner:", report["pod_details"])


POD = {
    "metadata": {"ownerReferences": [{"kind": "ReplicaSet", "name": "web-5d4"}]},
    "spec": {
        "nodeName": "worker",
        "containers": [
            {
                "name": "app",
                "image": "web:1.0",
                "resources": {"limits": {"memory": "64Mi"}},
                "env": [
                    {"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db", "key": "password"}}},
                    {"name": "API_TOKEN", "value": "super-secret-value"},
                ],
                "readinessProbe": {"httpGet": {"path": "/ready", "port": 3000}},
            }
        ],
        "volumes": [{"name": "cfg", "configMap": {"name": "web-config"}}],
    },
    "status": {
        "containerStatuses": [{"name": "app", "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}}]
    },
}


class DescribePodTests(unittest.TestCase):
    def test_describes_what_matters_for_rca(self):
        text = describe_pod(POD, "app")
        for expected in (
            "Node: worker",
            "ReplicaSet/web-5d4",
            "Image: web:1.0",
            "limits memory=64Mi",
            "HTTP GET /ready port 3000",
            "Secret db (key password)",
            "ConfigMap web-config (volume)",
            "reason OOMKilled, exit code 137",
        ):
            self.assertIn(expected, text)

    def test_never_includes_environment_values(self):
        text = describe_pod(POD, "app")
        self.assertIn("API_TOKEN", text)
        self.assertNotIn("super-secret-value", text)


REPORT = {
    "time": "2026-11-16T05:00:00+00:00",
    "namespace": "demo",
    "pod": "web-1",
    "container": "app",
    "reason": "CrashLoopBackOff",
    "message": "",
    "restart_count": 3,
    "analysed_by": "grok",
    "analysis": "Root cause: <db> down",
    "pod_details": "Node: worker",
    "events": "Warning BackOff",
    "log_excerpt": "FATAL",
}


AI_REPORT = {**REPORT, "ai_generated": True, "ai_model": "grok-4"}
RULES_REPORT = {**REPORT, "analysed_by": "built-in rules", "ai_generated": False, "ai_model": ""}


class AiMarkerTests(unittest.TestCase):
    def test_ai_analysis_is_marked_everywhere(self):
        message = build_email(AI_REPORT, "a@x.com", ("b@x.com",))
        self.assertEqual(message["Subject"], "[AIOps][AI-generated] CrashLoopBackOff in demo/web-1")
        text = message.get_body(("plain",)).get_content()
        html = message.get_body(("html",)).get_content()
        for body in (text, html):
            self.assertIn("AI-GENERATED by Grok (model grok-4)", body)
            self.assertIn("verify before acting", body)
        self.assertIn("BEGIN AI-GENERATED CONTENT", text)
        self.assertIn("END AI-GENERATED CONTENT", text)
        self.assertIn("not AI-generated", text)  # Kubernetes facts are labelled as such

    def test_rule_based_report_is_not_marked_as_ai(self):
        message = build_email(RULES_REPORT, "a@x.com", ("b@x.com",))
        self.assertEqual(message["Subject"], "[AIOps] CrashLoopBackOff in demo/web-1")
        text = message.get_body(("plain",)).get_content()
        self.assertNotIn("BEGIN AI-GENERATED", text)
        self.assertIn("rule-based (not AI-generated", text)

    def test_job_log_marks_ai_content(self):
        from src.main import format_report

        self.assertIn("BEGIN AI-GENERATED CONTENT", format_report(AI_REPORT))
        self.assertNotIn("BEGIN AI-GENERATED CONTENT", format_report(RULES_REPORT))


class EmailTests(unittest.TestCase):
    def test_email_has_subject_text_and_escaped_html(self):
        message = build_email(REPORT, "aiops@example.com", ("me@example.com", "ops@example.com"))
        self.assertEqual(subject(REPORT), "[AIOps] CrashLoopBackOff in demo/web-1")
        self.assertEqual(message["To"], "me@example.com, ops@example.com")
        html = message.get_body(("html",)).get_content()
        self.assertIn("&lt;db&gt;", html)
        self.assertIn("Root cause: <db> down", message.get_body(("plain",)).get_content())

    @mock.patch("smtplib.SMTP")
    def test_sends_with_starttls_and_login_on_587(self, smtp):
        settings = Settings(smtp_port=587, smtp_username="u", smtp_password="p", **EMAIL)
        send_report(settings, REPORT)
        client = smtp.return_value
        client.starttls.assert_called_once()
        client.login.assert_called_once_with("u", "p")
        client.send_message.assert_called_once()

    @mock.patch("smtplib.SMTP_SSL")
    def test_port_465_uses_implicit_tls(self, smtp_ssl):
        send_report(Settings(smtp_port=465, **EMAIL), REPORT)
        smtp_ssl.return_value.send_message.assert_called_once()

    @mock.patch("smtplib.SMTP", side_effect=smtplib.SMTPAuthenticationError(535, b"bad credentials"))
    def test_smtp_errors_become_email_errors_without_the_password(self, _smtp):
        settings = Settings(smtp_username="u", smtp_password="hunter2", **EMAIL)
        with self.assertRaises(EmailError) as raised:
            send_report(settings, REPORT)
        self.assertNotIn("hunter2", str(raised.exception))

    def test_not_configured_is_refused(self):
        with self.assertRaises(EmailError):
            send_report(Settings(), REPORT)


class SecretFileTests(unittest.TestCase):
    def test_secrets_are_read_from_mounted_files(self):
        with tempfile.TemporaryDirectory() as folder:
            key_file = os.path.join(folder, "api-key")
            with open(key_file, "w") as handle:
                handle.write("xai-from-file\n")
            for name, value in (("username", "bot@gmail.com"), ("password", "app-pass"), ("to", "a@x.com, b@x.com")):
                with open(os.path.join(folder, name), "w") as handle:
                    handle.write(value)
            env = {
                "GROK_API_KEY_FILE": key_file,
                "GROK_API_KEY": "from-env",
                "SMTP_SECRET_DIR": folder,
                "SMTP_HOST": "smtp.gmail.com",
            }
            settings = Settings.from_env(env)
        self.assertEqual(settings.grok_api_key, "xai-from-file")  # file wins over env
        self.assertEqual(settings.smtp_username, "bot@gmail.com")
        self.assertEqual(settings.smtp_password, "app-pass")
        self.assertEqual(settings.email_to, ("a@x.com", "b@x.com"))
        self.assertEqual(settings.email_from, "bot@gmail.com")  # defaults to the login
        self.assertTrue(settings.email_enabled)

    def test_environment_is_the_fallback_when_no_file_is_mounted(self):
        env = {"GROK_API_KEY_FILE": "/nonexistent/key", "GROK_API_KEY": "from-env", "SMTP_SECRET_DIR": "/nonexistent"}
        settings = Settings.from_env(env)
        self.assertEqual(settings.grok_api_key, "from-env")
        self.assertFalse(settings.email_enabled)

    def test_state_configmap_lives_in_the_pod_namespace(self):
        self.assertEqual(Settings.from_env({"POD_NAMESPACE": "aiops-x"}).state_namespace, "aiops-x")


if __name__ == "__main__":
    unittest.main()
