import json
import logging
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("website-orchestrator")
REQUEST_EVENT = "WebsiteProvisioningRequested"
OUTCOME_EVENTS = {"WebsiteCreated", "WebsiteDeploymentFailed"}
DELETION_EVENT = "WebsiteDeletionRequested"
DELETED_EVENT = "WebsiteDeleted"
HANDLED_EVENTS = {REQUEST_EVENT, DELETION_EVENT, DELETED_EVENT} | OUTCOME_EVENTS


def dispatch_website(event, environ=None):
    """Start Website Deployment for a provisioning request."""
    environ = environ or os.environ
    inputs = {
        "tenant": event["tenant"],
        "domain": event["domain"],
        "image_tag": event.get("image_tag", ""),  # empty: the workflow deploys the newest image
        "correlation_id": event["event_id"],
    }
    _dispatch_workflow(event, environ, environ.get("GH_WORKFLOW_NAME", "website_cd.yml"), inputs, "deployment")


def dispatch_teardown(event, environ=None):
    """Start Website Teardown (helm uninstall + namespace delete, after approval) for a deletion request."""
    environ = environ or os.environ
    inputs = {"tenant": event["tenant"], "domain": event["domain"], "correlation_id": event["event_id"]}
    _dispatch_workflow(
        event, environ, environ.get("GH_TEARDOWN_WORKFLOW_NAME", "website_teardown.yml"), inputs, "teardown"
    )


def _dispatch_workflow(event, environ, workflow, inputs, purpose):
    if environ.get("ORCHESTRATOR_DRY_RUN", "false").lower() == "true":
        logger.info("Dry run: would start %s workflow=%s tenant=%s", purpose, workflow, event["tenant"])
        return

    token = environ.get("GH_TOKEN")
    repository = environ.get("GH_REPOSITORY")
    ref = environ.get("GH_REF", "master")
    if not token or not repository or "/" not in repository:
        raise RuntimeError(f"GH_TOKEN and GH_REPOSITORY are required to start a {purpose}")

    endpoint = (
        "https://api.github.com/repos/"
        f"{repository}/actions/workflows/{quote(workflow, safe='')}/dispatches"
    )
    body = {"ref": ref, "inputs": inputs}
    request = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "yotta-website-orchestrator",
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
        "Started %s workflow=%s ref=%s tenant=%s event_id=%s",
        purpose,
        workflow,
        ref,
        event["tenant"],
        event["event_id"],
    )


def notify_outcome(event, environ=None):
    environ = environ or os.environ
    if event["event_type"] == "WebsiteCreated":
        message = f"Website {event['tenant']} is ready at https://{event['domain']}"
    elif event["event_type"] == DELETED_EVENT:
        message = f"Website {event['tenant']} ({event['domain']}) was deleted"
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


def send_email(event):
    from src.emailer import send_event_email

    send_event_email(event)


def handle_event(
    event, dispatch=dispatch_website, notify=notify_outcome, teardown=dispatch_teardown, store=None, email=send_email
):
    """Process one website event. `store` (EventStore) records it in the platform database when configured.
    `email` sends one notification e-mail per event once it was processed (never fails the event).

    Database writes come first and are safe to repeat, so a failed step (exception) leaves the Kafka
    message uncommitted and the whole event is processed again.
    """
    if not isinstance(event, dict):
        raise TypeError("Kafka event must be a JSON object")
    event_type = event.get("event_type")
    if event_type not in HANDLED_EVENTS:
        logger.info("Ignoring event_type=%s", event_type or "missing")
        return

    required = ("event_id", "tenant", "domain")
    missing = [name for name in required if not isinstance(event.get(name), str) or not event[name]]
    if missing:
        raise ValueError(f"{event_type} event is missing fields: {', '.join(missing)}")

    # One line per website event in a fixed format: Grafana counts these in Loki
    # (events by type and tenant on the Yotta Website Platform dashboard).
    logger.info(
        "WEBSITE_EVENT event_type=%s tenant=%s domain=%s event_id=%s",
        event_type,
        event["tenant"],
        event["domain"],
        event["event_id"],
    )
    if event_type == REQUEST_EVENT:
        if store:
            store.record_dispatch(event)
        dispatch(event)
    elif event_type == DELETION_EVENT:
        teardown(event)
    elif event_type == DELETED_EVENT:
        if store:
            store.remove_website(event)
        notify(event)
    else:
        if store:
            store.record_outcome(event)
        notify(event)
    email(event)


def event_store(environ=None):
    """The platform database store, or None (with a warning) when the database is not configured yet."""
    from src.store import EventStore, database_url

    url = database_url(environ)
    if url is None:
        logger.warning("Platform database not configured (DB_* settings); events are not recorded in it")
        return None
    import psycopg

    return EventStore(lambda: psycopg.connect(url, connect_timeout=5))


def main():
    from kafka import KafkaConsumer
    from kafka.structs import OffsetAndMetadata, TopicPartition

    servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    topic = os.environ.get("KAFKA_TOPIC", "website-events")
    group_id = os.environ.get("KAFKA_GROUP_ID", "website-orchestrator")
    store = event_store()
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
                    handle_event(message.value, store=store)
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
