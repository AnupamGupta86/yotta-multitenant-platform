import json
import logging
import os
from contextlib import asynccontextmanager

import psycopg
import uvicorn
from kafka import KafkaProducer

from src.app import create_app
from src.store import PlatformStore, database_url

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("hosting-platform")


@asynccontextmanager
async def lifespan(app):
    if not os.environ.get("KAFKA_BOOTSTRAP_SERVERS"):
        raise RuntimeError("Required environment variable is missing: KAFKA_BOOTSTRAP_SERVERS")
    url = database_url()  # fails fast when the database settings are missing
    topic = os.environ.get("KAFKA_TOPIC", "website-events")
    producer = KafkaProducer(
        bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"].split(","),
        value_serializer=lambda value: json.dumps(value, separators=(",", ":")).encode("utf-8"),
        acks="all",
        retries=5,
    )

    def publish(event):
        producer.send(topic, key=event["tenant"].encode("utf-8"), value=event).get(timeout=20)

    # The schema belongs to the database image (services/database/init); this service only reads and writes.
    app.state.store = PlatformStore(lambda: psycopg.connect(url, connect_timeout=5))
    app.state.publish = publish
    app.state.kafka_ready = producer.bootstrap_connected
    logger.info("Website provisioning service is ready")
    try:
        yield
    finally:
        producer.close(timeout=10)


app = create_app(lifespan=lifespan)


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT", "3000")))


if __name__ == "__main__":
    main()
