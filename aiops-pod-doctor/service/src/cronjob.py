"""One scheduled check (run by the Kubernetes CronJob every 5 minutes).

1. Find the pods that are not healthy.
2. Pick ONE failure that has not been reported yet.
3. Do the root-cause analysis (pod details, events, logs, Grok; built-in rules if Grok is down).
4. E-mail the report and remember it in a ConfigMap, so the same failure is not e-mailed every
   5 minutes. When the pod recovers it is forgotten, so a later failure is reported again.

Exit code: 0 = done (report sent, or nothing new); 1 = the report could not be e-mailed
(the CronJob run then shows as failed and the next run tries again).
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from typing import Protocol

from src.config import Settings
from src.emailer import EmailError, send_report
from src.kube import KubeClient, KubeError
from src.llm import GrokClient
from src.main import Analyst, PodDoctor, format_report

log = logging.getLogger("aiops-pod-doctor.cronjob")


class Mailer(Protocol):
    def __call__(self, settings: Settings, report: dict) -> None: ...


def state_key(failure_key: str) -> str:
    """ConfigMap keys may only contain letters, digits, '-', '_' and '.'."""
    return "".join(char if char.isalnum() or char in "-_." else "." for char in failure_key)


def run_once(settings: Settings, kube: KubeClient, analyst: Analyst, mailer: Mailer = send_report) -> int:
    doctor = PodDoctor(settings, kube, analyst)
    failing = doctor.failing_pods()

    saved = kube.get_configmap(settings.state_namespace, settings.state_configmap)
    exists = saved is not None
    reported = dict(saved or {})
    # Forget failures that are gone (pod healthy again or deleted)
    current = {state_key(failure.key) for failure, _ in failing}
    pruned = {key: value for key, value in reported.items() if key in current}

    new = [(failure, pod) for failure, pod in failing if state_key(failure.key) not in pruned]
    log.info("%d failing pod(s), %d not reported yet", len(failing), len(new))
    exit_code = 0
    if new:
        failure, pod = new[0]  # one pod per run
        report = doctor.diagnose(failure, pod)
        log.info(format_report(report))
        if settings.email_enabled:
            try:
                mailer(settings, report)
                log.info("Report e-mailed to %s", ", ".join(settings.email_to))
                pruned[state_key(failure.key)] = datetime.now(UTC).isoformat(timespec="seconds")
            except EmailError as error:
                log.error("%s; will retry on the next run", error)
                exit_code = 1
        else:
            log.warning("E-mail is not configured: report only written to this log")
            pruned[state_key(failure.key)] = datetime.now(UTC).isoformat(timespec="seconds")

    if pruned != reported or not exists:
        kube.save_configmap(settings.state_namespace, settings.state_configmap, pruned, exists)
    return exit_code


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    analyst = GrokClient(
        settings.grok_api_key, settings.grok_model, settings.grok_api_url, settings.grok_timeout_seconds
    )
    try:
        code = run_once(settings, KubeClient.in_cluster(), analyst)
    except KubeError as error:
        log.error("Kubernetes API not available: %s", error)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
