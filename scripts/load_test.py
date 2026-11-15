"""Generate concurrent website requests to demonstrate HPA scaling; run with python3 scripts/load_test.py [--tenant NAME] [--seconds N] [--workers N]."""

import argparse
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEFAULT_TENANT = "user1"
SUPPORTED_TENANTS = ("user1", "user2", "user3")
DEFAULT_HTTPS_PORT = 8443
DEFAULT_DURATION_SECONDS = 180
DEFAULT_WORKERS = 40
DEFAULT_DOMAIN_SUFFIX = "localtest.me"
WORKLOAD_PATH = "/api/v1/workload"
REQUEST_TIMEOUT_SECONDS = 10
SUCCESS_STATUS = 200

def main():
    parser = argparse.ArgumentParser(description="Generate CPU load to demonstrate website HPA scaling")
    parser.add_argument("--tenant", choices=SUPPORTED_TENANTS, default=DEFAULT_TENANT)
    parser.add_argument("--domain")
    parser.add_argument("--port", type=int, default=DEFAULT_HTTPS_PORT)
    parser.add_argument("--seconds", type=int, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()
    domain = args.domain or f"{args.tenant}.{DEFAULT_DOMAIN_SUFFIX}"
    request_url = f"https://{domain}:{args.port}{WORKLOAD_PATH}"
    context = ssl._create_unverified_context()
    deadline = time.monotonic() + args.seconds
    counts = {"ok": 0, "errors": 0}
    lock = threading.Lock()

    def request_until_deadline():
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(request_url, context=context, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    response.read()
                    key = "ok" if response.status == SUCCESS_STATUS else "errors"
            except (urllib.error.URLError, TimeoutError):
                key = "errors"
            with lock:
                counts[key] += 1

    print(f"Sending CPU workload to {request_url} for {args.seconds}s with {args.workers} workers")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(request_until_deadline) for _ in range(args.workers)]
        for future in futures:
            future.result()
    print(f"completed={counts['ok']} failed={counts['errors']}")
    if counts["ok"] == 0:
        raise SystemExit("No requests succeeded; check deployment and domain mapping")


if __name__ == "__main__":
    main()
