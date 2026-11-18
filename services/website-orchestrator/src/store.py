"""Records website lifecycle events in the platform database (myyotta_platform)."""

import logging
import os
from urllib.parse import quote

logger = logging.getLogger("website-orchestrator")
STATUS_AFTER = {"WebsiteCreated": ("SUCCEEDED", "ACTIVE"), "WebsiteDeploymentFailed": ("FAILED", "FAILED")}


def database_url(environ=None):
    """DATABASE_URL, or one built from DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD; None if not configured."""
    environ = environ if environ is not None else os.environ
    if environ.get("DATABASE_URL"):
        return environ["DATABASE_URL"]
    if not all(environ.get(name) for name in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")):
        return None
    return "postgresql://{user}:{password}@{host}:{port}/{name}".format(
        user=quote(environ["DB_USER"], safe=""),
        password=quote(environ["DB_PASSWORD"], safe=""),
        host=environ["DB_HOST"],
        port=environ.get("DB_PORT", "5432"),
        name=quote(environ["DB_NAME"], safe=""),
    )


def _split_image(image):
    """'ghcr.io/o/website:1.0.0-2026...' -> ('ghcr.io/o/website', '1.0.0-2026...')."""
    if not image:
        return "unknown", "unknown"
    name, _, tag = image.rpartition(":")
    if not name or "/" in tag:          # no tag (or a registry port only)
        return image, "unknown"
    return name, tag


class EventStore:
    """`connect` returns a new psycopg connection; `with connection:` commits, or rolls back on error.

    Every method is safe to repeat: the orchestrator re-processes a Kafka message if a step fails.
    Events for websites the database does not know (e.g. sample tenants deployed by the setup scripts)
    are logged and skipped.
    """

    def __init__(self, connect):
        self._connect = connect

    def _website_id(self, connection, tenant):
        row = connection.execute("SELECT website_id FROM websites WHERE namespace = %s", (tenant,)).fetchone()
        if row is None:
            logger.info("tenant=%s is not in the platform database; nothing to record", tenant)
        return row[0] if row else None

    def record_dispatch(self, event):
        with self._connect() as connection:
            connection.execute(
                """UPDATE websites SET status = 'PROVISIONING'
                   WHERE namespace = %s AND status IN ('PENDING', 'FAILED')""",
                (event["tenant"],),
            )

    def record_outcome(self, event):
        deployment_status, website_status = STATUS_AFTER[event["event_type"]]
        image_name, image_tag = _split_image(event.get("image"))
        with self._connect() as connection:
            website_id = self._website_id(connection, event["tenant"])
            if website_id is None:
                return
            # The outcome event id is the deployment_events primary key: a repeated message changes nothing
            already = connection.execute(
                "SELECT 1 FROM deployment_events WHERE event_id = %s", (event["event_id"],)
            ).fetchone()
            if already:
                return
            deployment_id = connection.execute(
                """INSERT INTO deployments (website_id, image_name, image_tag, status, completed_at)
                   VALUES (%s, %s, %s, %s, now()) RETURNING deployment_id""",
                (website_id, image_name, image_tag, deployment_status),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO deployment_events (event_id, deployment_id, website_id, event_type, published_status)
                   VALUES (%s, %s, %s, %s, 'PUBLISHED')""",
                (event["event_id"], deployment_id, website_id, event["event_type"]),
            )
            connection.execute(
                "UPDATE websites SET status = %s WHERE website_id = %s AND status <> 'DELETING'",
                (website_status, website_id),
            )

    def remove_website(self, event):
        """Teardown finished: delete the website; its domains, config, deployments and events cascade."""
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM websites WHERE namespace = %s RETURNING website_id", (event["tenant"],)
            ).fetchone()
        if deleted is None:
            logger.info("tenant=%s was not in the platform database; nothing to remove", event["tenant"])
