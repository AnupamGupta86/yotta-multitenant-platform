"""Ask Grok (xAI) what went wrong. Falls back to built-in rules when Grok is not available."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from src.detector import PodFailure

MAX_LOG_CHARS = 6000

SYSTEM_PROMPT = (
    "You are a senior Kubernetes site reliability engineer. "
    "You get the status, events and logs of one failing pod. Answer in plain text with exactly these sections:\n"
    "Root cause: one or two sentences.\n"
    "Evidence: the log or event lines that prove it.\n"
    "Workaround now: numbered kubectl commands or steps to get the pod running again today.\n"
    "Permanent fix: what to change in the image, manifest or Helm values.\n"
    "Be specific to the data given. Do not invent resources that are not mentioned."
)

# Things that look like secrets are masked before anything is sent outside the cluster.
_SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)(\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(://[^:/\s]+:)[^@\s]+(@)"),  # user:password@host in URLs
]


def redact(text: str) -> str:
    text = _SECRET_PATTERNS[0].sub(r"\1***", text)
    text = _SECRET_PATTERNS[1].sub(r"\1\2***", text)
    return _SECRET_PATTERNS[2].sub(r"\1***\2", text)


def _tail(text: str, limit: int = MAX_LOG_CHARS) -> str:
    return text if len(text) <= limit else "...(truncated)...\n" + text[-limit:]


def build_prompt(failure: PodFailure, logs: str, previous_logs: str, events: list[dict]) -> str:
    event_lines = [
        f"{event.get('type', '')} {event.get('reason', '')} (x{event.get('count', 1)}): {event.get('message', '')}"
        for event in events[-15:]
    ]
    sections = [
        f"Pod: {failure.namespace}/{failure.pod}",
        f"Container: {failure.container}",
        f"Phase: {failure.phase}",
        f"Failure reason: {failure.reason}",
        f"Status message: {failure.message or '-'}",
        f"Restart count: {failure.restart_count}",
        "",
        "Events (oldest first):",
        "\n".join(event_lines) or "(none)",
        "",
        "Logs from the previous (crashed) run:",
        _tail(previous_logs) or "(none)",
        "",
        "Logs from the current run:",
        _tail(logs) or "(none)",
    ]
    return redact("\n".join(sections))


class GrokError(RuntimeError):
    """Grok could not be asked (no key, network or API error)."""


class GrokClient:
    def __init__(self, api_key: str, model: str, api_url: str, timeout: float = 60.0):
        if not api_url.startswith("https://"):
            raise ValueError("Grok API URL must use https://")
        self.api_key = api_key
        self.model = model
        self.api_url = api_url
        self.timeout = timeout

    def analyse(self, prompt: str) -> str:
        if not self.api_key:
            raise GrokError("GROK_API_KEY is not set")
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            }
        ).encode()
        request = urllib.request.Request(  # nosec B310
            self.api_url,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            # URL scheme is checked in __init__ (https only)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # nosec B310
                payload = json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read()[:300].decode("utf-8", "replace")
            raise GrokError(f"Grok API returned HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise GrokError(f"Grok API call failed: {error}") from error
        try:
            return payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise GrokError("Grok API answer had no message content") from error


# Used when Grok cannot be asked, so the report always contains a first workaround.
FALLBACK_ADVICE = {
    "CrashLoopBackOff": (
        "The container starts and exits again.",
        (
            "kubectl logs {pod} -n {namespace} -c {container} --previous   # read why it exited\n"
            "kubectl describe pod {pod} -n {namespace}   # check exit code (137 = out of memory)\n"
            "Fix the config/env the logs complain about, or roll back: helm rollback <release> -n {namespace}"
        ),
    ),
    "ImagePullBackOff": (
        "Kubernetes cannot pull the container image.",
        (
            "kubectl describe pod {pod} -n {namespace}   # see the exact image and pull error\n"
            "Check the image name and tag exist; for kind: kind load docker-image <image> --name <cluster>\n"
            "For a private registry, add an imagePullSecret"
        ),
    ),
    "CreateContainerConfigError": (
        "A Secret or ConfigMap the pod refers to is missing.",
        (
            "kubectl describe pod {pod} -n {namespace}   # names the missing Secret/ConfigMap key\n"
            "kubectl get secret,configmap -n {namespace}\nCreate the missing object, then delete the pod to retry"
        ),
    ),
    "OOMKilled": (
        "The container used more memory than its limit.",
        "Raise resources.limits.memory in the Helm values, or find the memory leak",
    ),
    "Unschedulable": (
        "No node can run the pod (resources, taints or node selector).",
        (
            "kubectl describe pod {pod} -n {namespace}   # read the scheduler message\n"
            "kubectl describe nodes | grep -A5 Allocated   # check free CPU/memory\n"
            "Lower the resource requests or add capacity"
        ),
    ),
}
FALLBACK_ADVICE["ErrImagePull"] = FALLBACK_ADVICE["ImagePullBackOff"]
FALLBACK_ADVICE["InvalidImageName"] = FALLBACK_ADVICE["ImagePullBackOff"]


def fallback_analysis(failure: PodFailure) -> str:
    reason = "OOMKilled" if "OOMKilled" in failure.message else failure.reason
    cause, steps = FALLBACK_ADVICE.get(
        reason,
        (
            f"The pod failed with reason {failure.reason}.",
            "kubectl describe pod {pod} -n {namespace}\nkubectl logs {pod} -n {namespace} --all-containers",
        ),
    )
    values = {"pod": failure.pod, "namespace": failure.namespace, "container": failure.container}
    return (
        f"Root cause: {cause}\nEvidence: {failure.message or failure.reason}\nWorkaround now:\n{steps.format(**values)}"
    )
