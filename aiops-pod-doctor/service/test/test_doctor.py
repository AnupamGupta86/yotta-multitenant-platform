import json
import threading
import unittest
import urllib.request
from collections import deque
from http.server import ThreadingHTTPServer

from src.config import Settings
from src.kube import KubeError
from src.llm import GrokError
from src.main import PodDoctor, format_report, make_handler


def crashing(name, namespace="demo", restarts=2):
    return {
        "metadata": {"namespace": namespace, "name": name},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {"name": "app", "restartCount": restarts, "state": {"waiting": {"reason": "CrashLoopBackOff"}}}
            ],
        },
    }


class FakeKube:
    def __init__(self, pods):
        self.pods = pods
        self.log_calls = []

    def list_pods(self, namespace=None):
        return [p for p in self.pods if namespace in (None, p["metadata"]["namespace"])]

    def pod_logs(self, namespace, pod, container, tail_lines, previous=False):
        self.log_calls.append((pod, previous))
        return "previous crash log" if previous else "current log"

    def pod_events(self, namespace, pod):
        return [{"type": "Warning", "reason": "BackOff", "message": "Back-off restarting"}]


class FakeGrok:
    def __init__(self, fail=False):
        self.prompts = []
        self.fail = fail

    def analyse(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise GrokError("HTTP 401")
        return "Root cause: test"


class PodDoctorTests(unittest.TestCase):
    def test_healthy_cluster_gives_no_report(self):
        doctor = PodDoctor(Settings(), FakeKube([]), FakeGrok())
        self.assertIsNone(doctor.poll_once())

    def test_one_failed_pod_per_poll_and_each_only_once(self):
        grok = FakeGrok()
        doctor = PodDoctor(Settings(), FakeKube([crashing("b"), crashing("a")]), grok)
        self.assertEqual(doctor.poll_once()["pod"], "a")
        self.assertEqual(doctor.poll_once()["pod"], "b")
        self.assertIsNone(doctor.poll_once())
        self.assertEqual(len(grok.prompts), 2)
        self.assertEqual(len(doctor.reports), 2)

    def test_report_contains_grok_answer_and_previous_logs_were_sent(self):
        kube, grok = FakeKube([crashing("a")]), FakeGrok()
        report = PodDoctor(Settings(), kube, grok).poll_once()
        self.assertEqual(report["analysed_by"], "grok")
        self.assertEqual(report["analysis"], "Root cause: test")
        self.assertIn(("a", True), kube.log_calls)
        self.assertIn("previous crash log", grok.prompts[0])
        self.assertIn("AIOPS REPORT  demo/a", format_report(report))

    def test_grok_failure_falls_back_to_built_in_rules(self):
        report = PodDoctor(Settings(), FakeKube([crashing("a")]), FakeGrok(fail=True)).poll_once()
        self.assertEqual(report["analysed_by"], "built-in rules")
        self.assertIn("Workaround now:", report["analysis"])

    def test_recovered_pod_is_analysed_again_if_it_fails_later(self):
        kube = FakeKube([crashing("a")])
        doctor = PodDoctor(Settings(), kube, FakeGrok())
        self.assertIsNotNone(doctor.poll_once())
        kube.pods = []  # recovered
        self.assertIsNone(doctor.poll_once())
        kube.pods = [crashing("a")]  # fails again
        self.assertIsNotNone(doctor.poll_once())

    def test_namespace_filters(self):
        pods = [crashing("a", "kube-system"), crashing("b", "demo"), crashing("c", "other")]
        doctor = PodDoctor(Settings(watch_namespaces=("demo",)), FakeKube(pods), FakeGrok())
        self.assertEqual(doctor.poll_once()["pod"], "b")
        doctor = PodDoctor(Settings(ignore_namespaces=("demo", "kube-system")), FakeKube(pods), FakeGrok())
        self.assertEqual(doctor.poll_once()["pod"], "c")

    def test_events_error_does_not_stop_the_report(self):
        kube = FakeKube([crashing("a")])

        def broken(*args):
            raise KubeError("HTTP 403")

        kube.pod_events = broken
        self.assertIsNotNone(PodDoctor(Settings(), kube, FakeGrok()).poll_once())


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        settings = Settings.from_env({})
        self.assertEqual(settings.poll_interval_seconds, 5)
        self.assertEqual(settings.grok_model, "grok-4")
        self.assertEqual(settings.watch_namespaces, ())

    def test_values_from_environment(self):
        settings = Settings.from_env(
            {"POLL_INTERVAL_SECONDS": "10", "WATCH_NAMESPACES": "a, b", "GROK_API_KEY": " k ", "GROK_MODEL": "grok-3"}
        )
        self.assertEqual((settings.poll_interval_seconds, settings.watch_namespaces), (10, ("a", "b")))
        self.assertEqual((settings.grok_api_key, settings.grok_model), ("k", "grok-3"))

    def test_bad_values_are_refused(self):
        for env in ({"POLL_INTERVAL_SECONDS": "0"}, {"GROK_API_URL": "http://x"}, {"LOG_TAIL_LINES": "-1"}):
            with self.assertRaises(ValueError):
                Settings.from_env(env)


class HttpTests(unittest.TestCase):
    def test_health_and_reports_endpoints(self):
        reports = deque([{"pod": "a"}])
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(reports))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/health/live") as response:
                self.assertEqual(response.status, 200)
            with urllib.request.urlopen(base + "/reports") as response:
                self.assertEqual(json.loads(response.read()), [{"pod": "a"}])
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
