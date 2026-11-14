import argparse
import json
import os
from datetime import UTC, datetime
from uuid import uuid4


def build_event(tenant, domain, image, deployed_at=None):
    return {
        "event_type": "WebsiteCreated",
        "event_id": str(uuid4()),
        "schema_version": 1,
        "tenant": tenant,
        "domain": domain,
        "image": image,
        "deployed_at": deployed_at or datetime.now(UTC).isoformat(),
    }


def main():
    from kafka import KafkaProducer

    parser = argparse.ArgumentParser(description="Publish a successful website deployment event")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--bootstrap-servers", default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
    parser.add_argument("--topic", default=os.environ.get("KAFKA_TOPIC", "website-events"))
    args = parser.parse_args()
    event = build_event(args.tenant, args.domain, args.image)
    producer = KafkaProducer(
        bootstrap_servers=args.bootstrap_servers.split(","),
        value_serializer=lambda value: json.dumps(value, separators=(",", ":")).encode("utf-8"),
        acks="all",
        retries=5,
    )
    try:
        producer.send(args.topic, key=args.tenant.encode("utf-8"), value=event).get(timeout=20)
        producer.flush(timeout=20)
        print(json.dumps(event, sort_keys=True))
    finally:
        producer.close(timeout=20)


if __name__ == "__main__":
    main()
