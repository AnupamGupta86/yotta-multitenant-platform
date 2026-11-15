import json
import logging
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("deployment-orchestrator")
REQUEST_EVENT = "WebsiteProvisioningRequested"
OUTCOME_EVENTS = {"WebsiteCreated", "WebsiteDeploymentFailed"}


def dispatch_website(event, environ=None):
    environ = environ or os.environ
    if environ.get("ORCHESTRATOR_DRY_RUN", "false").lower() == "true":
        logger.info("Dry run: would deploy tenant=%s domain=%s", event["tenant"], event["domain"])
        return

    token = environ.get("GH_TOKEN")
    repository = environ.get("GH_REPOSITORY")
    workflow = environ.get("GH_WORKFLOW_NAME", "website-app-deploy.yml")
    ref = environ.get("GH_REF", "master")
    if not token or not repository or "/" not in repository:
        raise RuntimeError("GH_TOKEN and GH_REPOSITORY are required to start a deployment")

    endpoint = (
        "https://api.github.com/repos/"
        f"{repository}/actions/workflows/{quote(workflow, safe='')}/dispatches"
    )
    body = {
        "ref": ref,
        "inputs": {
            "tenant": event["tenant"],
            "domain": event["domain"],
            "image": event.get("image", ""),
            "correlation_id": event["event_id"],
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "yotta-deployment-orchestrator",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        # This request always targets the fixed HTTPS GitHub API endpoint.
        with urlopen(request, timeout=20) as response:  # nosec B310
            if response.status != 204:
                raise RuntimeError(f"GitHub accepted an unexpected status: {response.status}")
    except HTTPError as error:
        raise RuntimeError(f"GitHub workflow dispatch failed with HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError("GitHub workflow dispatch could not reach the API") from error
    logger.info(
        "Started deployment workflow=%s ref=%s tenant=%s event_id=%s",
        workflow,
        ref,
        event["tenant"],
        event["event_id"],
    )


def notify_outcome(event, environ=None):
    environ = environ or os.environ
    if event["event_type"] == "WebsiteCreated":
        message = f"Website {event['tenant']} is ready at https://{event['domain']}"
    else:
        message = f"Deployment failed for {event['tenant']} ({event['domain']})"
    webhook_url = environ.get("NOTIFICATION_WEBHOOK_URL")
    if not webhook_url:
        logger.info("NOTIFICATION: %s", message)
        return
    parsed_webhook_url = urlsplit(webhook_url)
    if parsed_webhook_url.scheme != "https" or not parsed_webhook_url.netloc:
        raise RuntimeError("NOTIFICATION_WEBHOOK_URL must be an absolute HTTPS URL")

    request = Request(
        webhook_url,
        data=json.dumps({"text": message, "event": event}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "yotta-event-notifier"},
        method="POST",
    )
    try:
        # The webhook URL is restricted to HTTPS above.
        with urlopen(request, timeout=10) as response:  # nosec B310
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Notification webhook returned HTTP {response.status}")
    except HTTPError as error:
        raise RuntimeError(f"Notification webhook returned HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError("Notification webhook could not be reached") from error
    logger.info("Notification sent for event_id=%s", event.get("event_id"))


def handle_event(event, dispatch=dispatch_website, notify=notify_outcome):
    if not isinstance(event, dict):
        raise TypeError("Kafka event must be a JSON object")
    event_type = event.get("event_type")
    if event_type not in ({REQUEST_EVENT} | OUTCOME_EVENTS):
        logger.info("Ignoring event_type=%s", event_type or "missing")
        return

    required = ("event_id", "tenant", "domain")
    missing = [name for name in required if not isinstance(event.get(name), str) or not event[name]]
    if missing:
        raise ValueError(f"{event_type} event is missing fields: {', '.join(missing)}")

    if event_type == REQUEST_EVENT:
        logger.info(
            "Received %s event_id=%s tenant=%s domain=%s",
            event_type,
            event["event_id"],
            event["tenant"],
            event["domain"],
        )
        dispatch(event)
    else:
        notify(event)


def main():
    from kafka import KafkaConsumer
    from kafka.structs import OffsetAndMetadata, TopicPartition

    servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    topic = os.environ.get("KAFKA_TOPIC", "website-events")
    group_id = os.environ.get("KAFKA_GROUP_ID", "deployment-orchestrator")
    while True:
        consumer = None
        try:
            consumer = KafkaConsumer(
                bootstrap_servers=servers.split(","),
                group_id=group_id,
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                max_poll_records=1,
                value_deserializer=lambda value: json.loads(value.decode("utf-8")),
            )
            consumer.subscribe([topic])
            logger.info("Listening for website lifecycle events topic=%s group=%s", topic, group_id)
            while True:
                records = consumer.poll(timeout_ms=1000, max_records=1)
                for messages in records.values():
                    if not messages:
                        continue
                    message = messages[0]
                    handle_event(message.value)
                    partition = TopicPartition(message.topic, message.partition)
                    consumer.commit({partition: OffsetAndMetadata(message.offset + 1, "")})
        except Exception:
            logger.exception("Could not process Kafka message; retrying without committing it")
            time.sleep(3)
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:
                    logger.exception("Error closing Kafka consumer")


if __name__ == "__main__":
    main()
