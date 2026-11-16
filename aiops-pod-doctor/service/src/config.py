"""Settings, read once from environment variables (the Helm chart sets them)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

GROK_DEFAULT_URL = "https://api.x.ai/v1/chat/completions"


def _names(value: str) -> tuple[str, ...]:
    """'a, b,,c' -> ('a', 'b', 'c')"""
    return tuple(part.strip() for part in value.split(",") if part.strip())


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

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        settings = cls(
            poll_interval_seconds=float(env.get("POLL_INTERVAL_SECONDS", "5")),
            watch_namespaces=_names(env.get("WATCH_NAMESPACES", "")),
            ignore_namespaces=_names(env.get("IGNORE_NAMESPACES", "")),
            log_tail_lines=int(env.get("LOG_TAIL_LINES", "100")),
            grok_api_key=env.get("GROK_API_KEY", "").strip(),
            grok_model=env.get("GROK_MODEL", "").strip() or "grok-4",
            grok_api_url=env.get("GROK_API_URL", "").strip() or GROK_DEFAULT_URL,
            grok_timeout_seconds=float(env.get("GROK_TIMEOUT_SECONDS", "60")),
            port=int(env.get("PORT", "3000")),
        )
        if settings.poll_interval_seconds <= 0:
            raise ValueError("POLL_INTERVAL_SECONDS must be greater than 0")
        if settings.log_tail_lines <= 0:
            raise ValueError("LOG_TAIL_LINES must be greater than 0")
        if not settings.grok_api_url.startswith("https://"):
            raise ValueError("GROK_API_URL must be an https:// URL")
        return settings
