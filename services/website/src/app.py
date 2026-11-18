"""Tenant website: a small Flask app.

Routes
  GET /health/live      liveness (no database call)
  GET /health/ready     readiness (checks the tenant's own PostgreSQL)
  GET /api/v1           service identity
  GET /api/v1/workload  CPU-heavy request, used by the autoscaling demo
  GET /metrics          Prometheus request counter per endpoint and status
"""

import logging
import os
from collections.abc import Callable
from threading import Lock
from uuid import uuid4

from flask import Flask, Response, g, jsonify, request

logger = logging.getLogger(__name__)

KNOWN_ENDPOINTS = {"/health/live", "/health/ready", "/api/v1", "/api/v1/workload"}

_metrics_lock = Lock()
_requests: dict[tuple[str, int], int] = {}


def metrics_text() -> str:
    tenant = os.environ.get("TENANT_NAME", "local")
    lines = ["# HELP yotta_http_requests_total Total HTTP requests", "# TYPE yotta_http_requests_total counter"]
    with _metrics_lock:
        values = sorted(_requests.items())
    lines.extend(
        f'yotta_http_requests_total{{service="website",tenant="{tenant}",endpoint="{endpoint}",status="{status}"}} {count}'
        for (endpoint, status), count in values
    )
    return "\n".join(lines) + "\n"


def create_app(ping_database: Callable[[], None]) -> Flask:
    """Build the app. ping_database raises when the tenant database is not reachable."""
    app = Flask(__name__)

    @app.before_request
    def assign_request_id() -> None:
        g.request_id = str(uuid4())

    @app.get("/health/live")
    def live():
        return jsonify(status="ok")

    @app.get("/health/ready")
    def ready():
        try:
            ping_database()
        except Exception:
            logger.exception("website database check failed; request_id=%s", g.request_id)
            return jsonify(error="service_unavailable"), 503
        return jsonify(status="ready")

    @app.get("/api/v1")
    def identity():
        return jsonify(service="website", version="v1")

    @app.get("/api/v1/workload")
    def workload():
        # Deliberately CPU-heavy so the load test can push the HPA to scale out
        return jsonify(result=sum(value * value for value in range(400_000)))

    @app.get("/metrics")
    def metrics():
        return Response(metrics_text(), content_type="text/plain; version=0.0.4; charset=utf-8")

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify(error="not_found"), 404

    @app.errorhandler(405)
    def method_not_allowed(_error):
        return jsonify(error="method_not_allowed"), 405

    @app.after_request
    def count_and_harden(response: Response) -> Response:
        if request.path != "/metrics":
            endpoint = request.path if request.path in KNOWN_ENDPOINTS else "other"
            with _metrics_lock:
                key = (endpoint, response.status_code)
                _requests[key] = _requests.get(key, 0) + 1
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        return response

    return app
