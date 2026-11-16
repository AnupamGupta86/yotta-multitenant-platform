"""AIOps pod doctor.

Every POLL_INTERVAL_SECONDS (5 by default):
  1. list the pods,
  2. pick ONE failing pod that has not been analysed yet,
  3. collect its status, events and logs,
  4. ask Grok for the root cause and a workaround (built-in rules if Grok is unavailable),
  5. print the report and keep it for GET /reports.

Each failure is analysed once (per pod, container and reason). If the pod recovers and later
fails again, it is analysed again.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol

from src.config import Settings
from src.detector import PodFailure, find_failure
from src.kube import KubeClient, KubeError
from src.llm import GrokClient, GrokError, build_prompt, fallback_analysis

log = logging.getLogger("aiops-pod-doctor")


class Analyst(Protocol):
    def analyse(self, prompt: str) -> str: ...


class PodDoctor:
    def __init__(self, settings: Settings, kube: KubeClient, analyst: Analyst, reports: deque[dict] | None = None):
        self.settings = settings
        self.kube = kube
        self.analyst = analyst
        self.analysed: set[str] = set()
        self.reports: deque[dict] = reports if reports is not None else deque(maxlen=20)

    def _pods(self) -> list[dict]:
        if self.settings.watch_namespaces:
            return [pod for ns in self.settings.watch_namespaces for pod in self.kube.list_pods(ns)]
        return self.kube.list_pods()

    def current_failures(self) -> list[PodFailure]:
        failures = []
        for pod in self._pods():
            failure = find_failure(pod)
            if failure and failure.namespace not in self.settings.ignore_namespaces:
                failures.append(failure)
        return sorted(failures, key=lambda failure: (failure.namespace, failure.pod))

    def poll_once(self) -> dict | None:
        """Analyse at most one new failing pod. Returns its report, or None."""
        failures = self.current_failures()
        # Forget pods that are healthy again (or gone), so a new failure is analysed again
        self.analysed &= {failure.key for failure in failures}
        new = [failure for failure in failures if failure.key not in self.analysed]
        if not new:
            return None
        failure = new[0]
        report = self.diagnose(failure)
        self.analysed.add(failure.key)
        self.reports.append(report)
        return report

    def diagnose(self, failure: PodFailure) -> dict:
        tail = self.settings.log_tail_lines
        logs = self.kube.pod_logs(failure.namespace, failure.pod, failure.container, tail)
        previous_logs = ""
        if failure.restart_count > 0:
            previous_logs = self.kube.pod_logs(failure.namespace, failure.pod, failure.container, tail, previous=True)
        try:
            events = self.kube.pod_events(failure.namespace, failure.pod)
        except KubeError as error:
            log.warning("Could not read events for %s/%s: %s", failure.namespace, failure.pod, error)
            events = []

        prompt = build_prompt(failure, logs, previous_logs, events)
        try:
            analysis, source = self.analyst.analyse(prompt), "grok"
        except GrokError as error:
            log.warning("Grok not used (%s); answering with built-in rules", error)
            analysis, source = fallback_analysis(failure), "built-in rules"

        return {
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "namespace": failure.namespace,
            "pod": failure.pod,
            "container": failure.container,
            "reason": failure.reason,
            "message": failure.message,
            "restart_count": failure.restart_count,
            "analysed_by": source,
            "analysis": analysis,
        }


def format_report(report: dict) -> str:
    line = "=" * 72
    return (
        f"\n{line}\nAIOPS REPORT  {report['namespace']}/{report['pod']}  container={report['container']}\n"
        f"Failure: {report['reason']}  restarts={report['restart_count']}  analysed by: {report['analysed_by']}\n"
        f"{line}\n{report['analysis']}\n{line}"
    )


def make_handler(reports: deque[dict]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # name required by http.server
            if self.path in ("/health/live", "/health/ready"):
                self._send(200, {"status": "ok"})
            elif self.path == "/reports":
                self._send(200, list(reports))
            else:
                self._send(404, {"error": "not found"})

        def _send(self, status: int, body: object) -> None:
            data = json.dumps(body, indent=2).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:
            pass  # keep probe requests out of the logs

    return Handler


def run(settings: Settings) -> None:
    analyst = GrokClient(
        settings.grok_api_key, settings.grok_model, settings.grok_api_url, settings.grok_timeout_seconds
    )
    reports: deque[dict] = deque(maxlen=20)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    # Health endpoints start first: the container is healthy even before Kubernetes answers
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), make_handler(reports))  # nosec B104
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(
        "Watching %s every %ss with Grok model %s (%s)",
        ",".join(settings.watch_namespaces) or "all namespaces",
        settings.poll_interval_seconds,
        settings.grok_model,
        "API key set" if settings.grok_api_key else "NO API KEY: built-in rules only",
    )

    doctor: PodDoctor | None = None
    last_error = ""
    while not stop.is_set():
        try:
            if doctor is None:
                doctor = PodDoctor(settings, KubeClient.in_cluster(), analyst, reports)
            report = doctor.poll_once()
            if report:
                log.info(format_report(report))
            last_error = ""
        except KubeError as error:
            if str(error) != last_error:  # say it once, not every 5 seconds
                log.warning("Kubernetes API not available: %s", error)
                last_error = str(error)
        stop.wait(settings.poll_interval_seconds)
    server.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(Settings.from_env())


if __name__ == "__main__":
    main()
