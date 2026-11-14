import json
import logging
import os
from http.server import BaseHTTPRequestHandler
from threading import Lock
from uuid import uuid4

logger = logging.getLogger(__name__)
_metrics_lock = Lock()
_requests = {}


def route(method, path, ping):
    if method == "GET" and path == "/health/live":
        return 200, {"status": "ok"}
    if method == "GET" and path == "/health/ready":
        ping()
        return 200, {"status": "ready"}
    if method == "GET" and path == "/api/v1":
        return 200, {"service": "website-api", "version": "v1"}
    if method == "GET" and path == "/api/v1/workload":
        return 200, {"result": sum(value * value for value in range(400_000))}
    return 404, {"error": "not_found"}


def metrics_text():
    lines = ["# HELP yotta_http_requests_total Total HTTP requests", "# TYPE yotta_http_requests_total counter"]
    with _metrics_lock:
        values = sorted(_requests.items())
    lines.extend(
        f'yotta_http_requests_total{{service="website-api",tenant="{os.environ.get("TENANT_NAME", "local")}",endpoint="{endpoint}",status="{status}"}} {count}'
        for (endpoint, status), count in values
    )
    return "\n".join(lines) + "\n"


def create_handler(ping):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            request_id = str(uuid4())
            if self.path == "/metrics":
                payload = metrics_text().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            try:
                status, body = route(self.command, self.path, ping)
            except Exception:
                logger.exception("website database check failed; request_id=%s", request_id)
                status, body = 503, {"error": "service_unavailable"}
            endpoint = self.path if self.path in {"/health/live", "/health/ready", "/api/v1", "/api/v1/workload"} else "other"
            with _metrics_lock:
                key = (endpoint, status)
                _requests[key] = _requests.get(key, 0) + 1
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Request-ID", request_id)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):
            logger.info("%s - %s", self.client_address[0], fmt % args)

    return Handler
