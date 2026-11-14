import os
from http.server import ThreadingHTTPServer

import psycopg

from src.app import create_handler


def ping_database():
    database = os.environ.get("DATABASE_URL")
    if database:
        connection = psycopg.connect(database, connect_timeout=3)
    else:
        connection = psycopg.connect(
            host=os.environ["DB_HOST"],
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            connect_timeout=3,
        )
    with connection:
        connection.execute("SELECT 1")


def main():
    if not os.environ.get("DATABASE_URL") and not all(
        os.environ.get(key) for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    ):
        raise RuntimeError("Set DATABASE_URL or the website database settings")
    port = int(os.environ.get("PORT", "3000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), create_handler(ping_database))
    print(f"website-api listening on {port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
