"""Settings, read once from environment variables and mounted Kubernetes Secret files.

Secrets (the Grok API key and the e-mail login) are mounted by the Helm chart as files from the
Kubernetes Secrets `aiops-grok-api-key` and `aiops-smtp`; the app reads them from those files.
An environment variable with the same name is only a fallback for running the code locally.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

GROK_DEFAULT_URL = "https://api.x.ai/v1/chat/completions"
GROK_KEY_FILE = "/etc/aiops/grok/api-key"  # Secret aiops-grok-api-key, key api-key
# Secret aiops-smtp, keys username, password, to (a folder path, not a password)
SMTP_SECRET_DIR = "/etc/aiops/smtp"  # nosec B105


def _names(value: str) -> tuple[str, ...]:
    """'a, b,,c' -> ('a', 'b', 'c')"""
    return tuple(part.strip() for part in value.split(",") if part.strip())


def read_secret_file(path: str) -> str:
    """Contents of a mounted Secret file, or '' when it is not there."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _secret(env: Mapping[str, str], name: str, path: str) -> str:
    """Secret value: mounted file first, environment variable as local fallback."""
    return read_secret_file(path) or env.get(name, "").strip()


@dataclass(frozen=True)
class Settings:
    poll_interval_seconds: float = 5.0
    # Empty = watch every namespace
    watch_namespaces: tuple[str, ...] = ()
    ignore_namespaces: tuple[str, ...] = ()
    log_tail_lines: int = 100
    grok_api_key: str = ""
    grok_model: str = "grok-4"
    grok_api_url: str = GROK_DEFAULT_URL
    grok_timeout_seconds: float = 60.0
    port: int = 3000
    # E-mail reports (CronJob)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: tuple[str, ...] = ()
    # Where the CronJob remembers which failures it already e-mailed
    state_namespace: str = "aiops"
    state_configmap: str = "aiops-pod-doctor-state"

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.email_to and self.email_from)

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        smtp_dir = env.get("SMTP_SECRET_DIR", SMTP_SECRET_DIR)
        smtp_username = _secret(env, "SMTP_USERNAME", os.path.join(smtp_dir, "username"))
        settings = cls(
            poll_interval_seconds=float(env.get("POLL_INTERVAL_SECONDS", "5")),
            watch_namespaces=_names(env.get("WATCH_NAMESPACES", "")),
            ignore_namespaces=_names(env.get("IGNORE_NAMESPACES", "")),
            log_tail_lines=int(env.get("LOG_TAIL_LINES", "100")),
            grok_api_key=_secret(env, "GROK_API_KEY", env.get("GROK_API_KEY_FILE", GROK_KEY_FILE)),
            grok_model=env.get("GROK_MODEL", "").strip() or "grok-4",
            grok_api_url=env.get("GROK_API_URL", "").strip() or GROK_DEFAULT_URL,
            grok_timeout_seconds=float(env.get("GROK_TIMEOUT_SECONDS", "60")),
            port=int(env.get("PORT", "3000")),
            smtp_host=env.get("SMTP_HOST", "").strip(),
            smtp_port=int(env.get("SMTP_PORT", "587")),
            smtp_username=smtp_username,
            smtp_password=_secret(env, "SMTP_PASSWORD", os.path.join(smtp_dir, "password")),
            email_from=env.get("EMAIL_FROM", "").strip() or smtp_username,
            email_to=_names(_secret(env, "EMAIL_TO", os.path.join(smtp_dir, "to"))),
            state_namespace=env.get("POD_NAMESPACE", "").strip() or "aiops",
            state_configmap=env.get("STATE_CONFIGMAP", "").strip() or "aiops-pod-doctor-state",
        )
        if settings.poll_interval_seconds <= 0:
            raise ValueError("POLL_INTERVAL_SECONDS must be greater than 0")
        if settings.log_tail_lines <= 0:
            raise ValueError("LOG_TAIL_LINES must be greater than 0")
        if not settings.grok_api_url.startswith("https://"):
            raise ValueError("GROK_API_URL must be an https:// URL")
        if not 0 < settings.smtp_port < 65536:
            raise ValueError("SMTP_PORT must be a TCP port number")
        return settings
