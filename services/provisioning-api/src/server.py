import os
from http.server import ThreadingHTTPServer

import psycopg

from src.app import create_handler


def ping_database():
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as connection:
        connection.execute("SELECT 1")


def main():
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required (use the provisioning database)")
    port = int(os.environ.get("PORT", "3000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), create_handler(ping_database))
    print(f"provisioning-api listening on {port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
