"""Decide whether a pod (as returned by the Kubernetes API) is failing, and why."""

from __future__ import annotations

from dataclasses import dataclass

# A container waiting for one of these reasons will not start without help.
FAILING_WAIT_REASONS = frozenset(
    {
        "CrashLoopBackOff",
        "ImagePullBackOff",
        "ErrImagePull",
        "InvalidImageName",
        "CreateContainerConfigError",
        "CreateContainerError",
        "RunContainerError",
    }
)


@dataclass(frozen=True)
class PodFailure:
    namespace: str
    pod: str
    container: str
    reason: str
    message: str
    restart_count: int
    phase: str

    @property
    def key(self) -> str:
        """One analysis per pod, container and failure reason (not per restart)."""
        return f"{self.namespace}/{self.pod}/{self.container}/{self.reason}"


def _last_exit(status: dict) -> str:
    terminated = (status.get("lastState") or {}).get("terminated") or {}
    if not terminated:
        return ""
    return f"last exit: {terminated.get('reason', 'unknown')} (exit code {terminated.get('exitCode', '?')})"


def _container_failure(status: dict, pod_failed: bool) -> tuple[str, str] | None:
    state = status.get("state") or {}
    waiting = state.get("waiting") or {}
    if waiting.get("reason") in FAILING_WAIT_REASONS:
        details = [waiting.get("message", ""), _last_exit(status)]
        return waiting["reason"], "; ".join(part for part in details if part)
    # A container that just exited is restarted by Kubernetes (and shows up as CrashLoopBackOff
    # if it keeps failing), so an exit only counts on its own once the whole pod has failed.
    terminated = state.get("terminated") or {}
    if pod_failed and terminated and terminated.get("exitCode", 0) != 0:
        reason = terminated.get("reason") or "Error"
        return reason, f"exit code {terminated.get('exitCode')} {terminated.get('message', '')}".strip()
    return None


def find_failure(pod: dict) -> PodFailure | None:
    """Return why this pod is failing, or None when it is healthy, starting normally or finished."""
    metadata = pod.get("metadata") or {}
    status = pod.get("status") or {}
    if metadata.get("deletionTimestamp"):
        return None  # already going away
    phase = status.get("phase", "Unknown")
    if phase == "Succeeded":
        return None

    def failure(container: str, reason: str, message: str, restarts: int = 0) -> PodFailure:
        return PodFailure(
            namespace=metadata.get("namespace", "default"),
            pod=metadata.get("name", "?"),
            container=container,
            reason=reason,
            message=message,
            restart_count=restarts,
            phase=phase,
        )

    statuses = (status.get("initContainerStatuses") or []) + (status.get("containerStatuses") or [])
    for container_status in statuses:
        problem = _container_failure(container_status, phase == "Failed")
        if problem:
            return failure(
                container_status.get("name", "?"), problem[0], problem[1], container_status.get("restartCount", 0)
            )

    if phase == "Pending":
        for condition in status.get("conditions") or []:
            if condition.get("type") == "PodScheduled" and condition.get("reason") == "Unschedulable":
                return failure("-", "Unschedulable", condition.get("message", ""))

    if phase == "Failed":
        return failure("-", status.get("reason") or "Failed", status.get("message", ""))
    return None
