"""Pod context for the root-cause analysis: what a person would read in `kubectl describe pod`.

Only names and settings are included. Environment variable VALUES are never read here, so no
secret from the pod spec can reach the report or the LLM.
"""

from __future__ import annotations


def _resources(resources: dict) -> str:
    requests = resources.get("requests") or {}
    limits = resources.get("limits") or {}
    if not requests and not limits:
        return "none set"

    def fmt(values: dict) -> str:
        return ", ".join(f"{key}={value}" for key, value in sorted(values.items())) or "-"

    return f"requests {fmt(requests)}; limits {fmt(limits)}"


def _probe(probe: dict | None) -> str:
    if not probe:
        return "none"
    if "httpGet" in probe:
        target = f"HTTP GET {probe['httpGet'].get('path', '/')} port {probe['httpGet'].get('port')}"
    elif "tcpSocket" in probe:
        target = f"TCP port {probe['tcpSocket'].get('port')}"
    elif "exec" in probe:
        target = "exec command"
    else:
        target = "other"
    return f"{target}, delay {probe.get('initialDelaySeconds', 0)}s, every {probe.get('periodSeconds', 10)}s"


def _referenced_config(spec: dict, container: dict) -> list[str]:
    """Secrets and ConfigMaps the container depends on (a missing one blocks the start)."""
    names = set()
    for env in container.get("env") or []:
        source = env.get("valueFrom") or {}
        if "secretKeyRef" in source:
            names.add(f"Secret {source['secretKeyRef'].get('name')} (key {source['secretKeyRef'].get('key')})")
        if "configMapKeyRef" in source:
            names.add(f"ConfigMap {source['configMapKeyRef'].get('name')} (key {source['configMapKeyRef'].get('key')})")
    for env_from in container.get("envFrom") or []:
        if "secretRef" in env_from:
            names.add(f"Secret {env_from['secretRef'].get('name')} (all keys)")
        if "configMapRef" in env_from:
            names.add(f"ConfigMap {env_from['configMapRef'].get('name')} (all keys)")
    for volume in spec.get("volumes") or []:
        if "secret" in volume:
            names.add(f"Secret {volume['secret'].get('secretName')} (volume)")
        if "configMap" in volume:
            names.add(f"ConfigMap {volume['configMap'].get('name')} (volume)")
        if "persistentVolumeClaim" in volume:
            names.add(f"PVC {volume['persistentVolumeClaim'].get('claimName')}")
    return sorted(names)


def describe_pod(pod: dict, container_name: str) -> str:
    """Short, secret-free description of the pod and its failing container."""
    metadata = pod.get("metadata") or {}
    spec = pod.get("spec") or {}
    status = pod.get("status") or {}
    owners = ", ".join(f"{owner.get('kind')}/{owner.get('name')}" for owner in metadata.get("ownerReferences") or [])
    lines = [
        f"Node: {spec.get('nodeName', 'not scheduled')}",
        f"Owner: {owners or 'none (bare pod)'}",
        f"Restart policy: {spec.get('restartPolicy', 'Always')}",
        f"Pod start time: {status.get('startTime', '-')}",
    ]
    containers = (spec.get("initContainers") or []) + (spec.get("containers") or [])
    container = next((item for item in containers if item.get("name") == container_name), None)
    if container is None and containers:
        container = containers[0]
    if container:
        env_names = [env.get("name") for env in container.get("env") or [] if env.get("name")]
        lines += [
            f"Container: {container.get('name')}",
            f"Image: {container.get('image')} (pull policy {container.get('imagePullPolicy', 'default')})",
            f"Resources: {_resources(container.get('resources') or {})}",
            f"Liveness probe: {_probe(container.get('livenessProbe'))}",
            f"Readiness probe: {_probe(container.get('readinessProbe'))}",
            f"Environment variable names: {', '.join(env_names) or 'none'}",
            f"Depends on: {', '.join(_referenced_config(spec, container)) or 'nothing'}",
        ]
    statuses = (status.get("initContainerStatuses") or []) + (status.get("containerStatuses") or [])
    for item in statuses:
        if item.get("name") == container_name:
            terminated = (item.get("lastState") or {}).get("terminated") or {}
            if terminated:
                lines.append(
                    f"Last termination: reason {terminated.get('reason')}, exit code {terminated.get('exitCode')}, "
                    f"finished {terminated.get('finishedAt')}"
                )
    return "\n".join(lines)
