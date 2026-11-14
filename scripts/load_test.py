import argparse
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def main():
    parser = argparse.ArgumentParser(description="Generate CPU load to demonstrate website HPA scaling")
    parser.add_argument("--tenant", choices=("user1", "user2", "user3"), default="user1")
    parser.add_argument("--domain")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--workers", type=int, default=40)
    args = parser.parse_args()
    domain = args.domain or f"{args.tenant}.localtest.me"
    request_url = f"https://{domain}:{args.port}/api/v1/workload"
    context = ssl._create_unverified_context()
    deadline = time.monotonic() + args.seconds
    counts = {"ok": 0, "errors": 0}
    lock = threading.Lock()

    def request_until_deadline():
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(request_url, context=context, timeout=10) as response:
                    response.read()
                    key = "ok" if response.status == 200 else "errors"
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
