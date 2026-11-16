import argparse
import json
import os
from datetime import datetime, timezone
from uuid import uuid4


def build_event(
    tenant,
    domain,
    image,
    occurred_at=None,
    event_type="WebsiteCreated",
    correlation_id=None,
    error=None,
):
    event = {
        "event_type": event_type,
        "event_id": str(uuid4()),
        "schema_version": 1,
        "tenant": tenant,
        "domain": domain,
        "image": image,
        # timezone.utc, not datetime.UTC: this also runs with the runner Mac's own python3 (< 3.11)
        "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }
    if correlation_id:
        event["correlation_id"] = correlation_id
    if error:
        event["error"] = error
    return event


def main():
    from kafka import KafkaProducer

    parser = argparse.ArgumentParser(description="Publish a website lifecycle event")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument(
        "--event-type",
        choices=("WebsiteProvisioningRequested", "WebsiteCreated", "WebsiteDeploymentFailed"),
        default="WebsiteCreated",
    )
    parser.add_argument("--correlation-id")
    parser.add_argument("--error")
    parser.add_argument("--bootstrap-servers", default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
    parser.add_argument("--topic", default=os.environ.get("KAFKA_TOPIC", "website-events"))
    args = parser.parse_args()
    event = build_event(
        args.tenant,
        args.domain,
        args.image,
        event_type=args.event_type,
        correlation_id=args.correlation_id,
        error=args.error,
    )
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
