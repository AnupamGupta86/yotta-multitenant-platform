import json
import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import uvicorn
from kafka import KafkaProducer

from src.app import RequestConflict, create_app

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("website-provisioning")


def ping_database():
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as connection:
        connection.execute("SELECT 1")


def initialize_database():
    for attempt in range(30):
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS website_requests (
                        tenant TEXT PRIMARY KEY,
                        domain TEXT NOT NULL UNIQUE,
                        event_id UUID NOT NULL UNIQUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        published_at TIMESTAMPTZ
                    )"""
                )
            return
        except psycopg.OperationalError:
            if attempt == 29:
                raise
            logger.info("Waiting for provisioning PostgreSQL to become ready")
            time.sleep(2)


def create_submitter(producer, topic):
    def submit(tenant, domain):
        event_id = str(uuid4())
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as connection:
                connection.execute(
                    """INSERT INTO website_requests (tenant, domain, event_id)
                       VALUES (%s, %s, %s) ON CONFLICT (tenant) DO NOTHING""",
                    (tenant, domain, event_id),
                )
        except psycopg.errors.UniqueViolation as error:
            raise RequestConflict from error

        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as connection:
            row = connection.execute(
                """SELECT domain, event_id, published_at, created_at
                   FROM website_requests WHERE tenant = %s FOR UPDATE""",
                (tenant,),
            ).fetchone()
            if row[0] != domain:
                raise RequestConflict
            event = {
                "event_type": "WebsiteProvisioningRequested",
                "event_id": str(row[1]),
                "schema_version": 1,
                "tenant": tenant,
                "domain": domain,
                "requested_at": row[3].isoformat(),
            }
            if row[2] is not None:
                logger.info("request already accepted event_id=%s tenant=%s", event["event_id"], tenant)
                return event
            producer.send(
                topic,
                key=tenant.encode("utf-8"),
                value=event,
            ).get(timeout=20)
            connection.execute(
                "UPDATE website_requests SET published_at = now() WHERE event_id = %s",
                (event["event_id"],),
            )
        logger.info("accepted website request event_id=%s tenant=%s", event["event_id"], tenant)
        return event

    return submit


@asynccontextmanager
async def lifespan(app):
    required = ("DATABASE_URL", "KAFKA_BOOTSTRAP_SERVERS")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Required environment variables are missing: {', '.join(missing)}")

    initialize_database()
    producer = KafkaProducer(
        bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"].split(","),
        value_serializer=lambda value: json.dumps(value, separators=(",", ":")).encode("utf-8"),
        acks="all",
        retries=5,
    )
    app.state.ping_database = ping_database
    app.state.submit_website = create_submitter(
        producer,
        os.environ.get("KAFKA_TOPIC", "website-events"),
    )
    logger.info("Website provisioning service is ready")
    try:
        yield
    finally:
        producer.close(timeout=10)


app = create_app(lifespan=lifespan)


def main():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "3000")))


if __name__ == "__main__":
    main()
