import json
import logging
from http.server import BaseHTTPRequestHandler
from uuid import uuid4

logger = logging.getLogger(__name__)


def route(method, path, ping):
    if method == "GET" and path == "/health/live":
        return 200, {"status": "ok"}
    if method == "GET" and path == "/health/ready":
        ping()
        return 200, {"status": "ready"}
    if method == "GET" and path == "/api/v1":
        return 200, {"service": "database-api", "version": "v1"}
    return 404, {"error": "not_found"}


def create_handler(ping):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            request_id = str(uuid4())
            try:
                status, body = route(self.command, self.path, ping)
            except Exception:
                logger.exception("database check failed; request_id=%s", request_id)
                status, body = 503, {"error": "database_unavailable"}
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
