"""gunicorn settings (read automatically from the working directory /app)."""

import os

bind = f"{os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', '3000')}"  # nosec B104
# One process with threads keeps the in-memory /metrics counters in one place;
# scaling out is done by the HPA adding pods.
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = 30
graceful_timeout = 20
# The root filesystem is read-only in Kubernetes; /dev/shm is a writable in-memory folder
worker_tmp_dir = "/dev/shm"  # nosec B108
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()


def on_starting(server):
    """Refuse to start without database settings, and wait until the tenant database answers."""
    from src.server import check_settings, wait_for_database

    check_settings()
    wait_for_database()
    server.log.info("website ready, listening on %s", bind)
