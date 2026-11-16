"""Tenant database access and the WSGI entry point that gunicorn serves (src.server:app)."""

import logging
import os
import time

import psycopg

from src.app import create_app

logger = logging.getLogger("website-app")

DATABASE_SETTINGS = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")


def check_settings() -> None:
    if not os.environ.get("DATABASE_URL") and not all(os.environ.get(key) for key in DATABASE_SETTINGS):
        raise RuntimeError("Set DATABASE_URL or the website database settings")


def ping_database() -> None:
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


def wait_for_database(attempts: int = 30, interval_seconds: float = 2) -> None:
    for attempt in range(attempts):
        try:
            ping_database()
            return
        except psycopg.OperationalError:
            if attempt == attempts - 1:
                raise
            logger.info("Waiting for tenant PostgreSQL to become ready")
            time.sleep(interval_seconds)


# gunicorn imports this module and serves `app`; the startup checks run in gunicorn.conf.py
app = create_app(ping_database)
