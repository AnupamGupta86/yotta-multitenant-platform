"""A very small Kubernetes API client (standard library only).

Inside a pod it uses the mounted service-account token. The Helm chart grants that account read
access to pods, pod logs and events, plus write access to one ConfigMap in its own namespace
(where the CronJob remembers which failures it already reported).
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

SERVICE_ACCOUNT_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


class KubeError(RuntimeError):
    """The Kubernetes API could not be reached or refused the request."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class KubeClient:
    def __init__(self, base_url: str, token: str, ca_file: str | None = None, timeout: float = 10.0):
        if not base_url.startswith("https://"):
            raise KubeError("Kubernetes API URL must use https://")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.context = ssl.create_default_context(cafile=ca_file)

    @classmethod
    def in_cluster(cls, service_account_dir: str = SERVICE_ACCOUNT_DIR) -> KubeClient:
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        token_file = os.path.join(service_account_dir, "token")
        if not host or not os.path.exists(token_file):
            raise KubeError("Not running inside Kubernetes (no service-account token found)")
        with open(token_file, encoding="utf-8") as handle:
            token = handle.read().strip()
        if ":" in host:  # IPv6 address
            host = f"[{host}]"
        return cls(f"https://{host}:{port}", token, os.path.join(service_account_dir, "ca.crt"))

    def _request(
        self, path: str, params: dict[str, str | int] | None = None, method: str = "GET", body: dict | None = None
    ) -> bytes:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)  # nosec B310
        try:
            # URL scheme is checked in __init__ (https only)
            with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response:  # nosec B310
                return response.read()
        except urllib.error.HTTPError as error:
            raise KubeError(f"{method} {path} failed: HTTP {error.code}", status=error.code) from error
        except (urllib.error.URLError, OSError) as error:
            raise KubeError(f"{method} {path} failed: {error}") from error

    def _get(self, path: str, params: dict[str, str | int] | None = None) -> bytes:
        return self._request(path, params)

    def list_pods(self, namespace: str | None = None) -> list[dict]:
        path = f"/api/v1/namespaces/{namespace}/pods" if namespace else "/api/v1/pods"
        return json.loads(self._get(path)).get("items", [])

    def pod_logs(self, namespace: str, pod: str, container: str, tail_lines: int, previous: bool = False) -> str:
        params: dict[str, str | int] = {"tailLines": tail_lines}
        if container and container != "-":
            params["container"] = container
        if previous:
            params["previous"] = "true"
        try:
            return self._get(f"/api/v1/namespaces/{namespace}/pods/{pod}/log", params).decode("utf-8", "replace")
        except KubeError:
            # No logs yet (image never pulled, or no previous run): not an error for us
            return ""

    def pod_events(self, namespace: str, pod: str) -> list[dict]:
        selector = f"involvedObject.kind=Pod,involvedObject.name={pod}"
        items = json.loads(self._get(f"/api/v1/namespaces/{namespace}/events", {"fieldSelector": selector}))
        return sorted(items.get("items", []), key=lambda event: event.get("lastTimestamp") or "")

    def get_configmap(self, namespace: str, name: str) -> dict[str, str] | None:
        """The ConfigMap's data, or None when it does not exist yet."""
        try:
            return json.loads(self._get(f"/api/v1/namespaces/{namespace}/configmaps/{name}")).get("data") or {}
        except KubeError as error:
            if error.status == 404:
                return None
            raise

    def save_configmap(self, namespace: str, name: str, data: dict[str, str], exists: bool) -> None:
        body = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": namespace},
            "data": data,
        }
        if exists:
            self._request(f"/api/v1/namespaces/{namespace}/configmaps/{name}", method="PUT", body=body)
        else:
            self._request(f"/api/v1/namespaces/{namespace}/configmaps", method="POST", body=body)
