"""All reads and writes of the Provisioning API against the platform database (myyotta_platform)."""

import logging
import os
from urllib.parse import quote

logger = logging.getLogger("hosting-platform")
REQUEST_EVENT = "WebsiteProvisioningRequested"
DELETION_EVENT = "WebsiteDeletionRequested"
UNIQUE_VIOLATION = "23505"


class Conflict(Exception):
    """The tenant (namespace), domain, e-mail or username is already taken."""


class NotFound(Exception):
    """No such website for this user."""


def database_url(environ=None):
    """DATABASE_URL if set (CI), otherwise built from DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD (Kubernetes)."""
    environ = environ if environ is not None else os.environ
    if environ.get("DATABASE_URL"):
        return environ["DATABASE_URL"]
    missing = [name for name in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD") if not environ.get(name)]
    if missing:
        raise RuntimeError(f"Set DATABASE_URL or the platform database settings: {', '.join(missing)} missing")
    return "postgresql://{user}:{password}@{host}:{port}/{name}".format(
        user=quote(environ["DB_USER"], safe=""),
        password=quote(environ["DB_PASSWORD"], safe=""),
        host=environ["DB_HOST"],
        port=environ.get("DB_PORT", "5432"),
        name=quote(environ["DB_NAME"], safe=""),
    )


def _is_unique_violation(error):
    return getattr(error, "sqlstate", None) == UNIQUE_VIOLATION


def _event(event_type, event_id, created_at, website):
    return {
        "event_type": event_type,
        "event_id": str(event_id),
        "schema_version": 1,
        "tenant": website["tenant"],
        "domain": website["domain"],
        "website_id": str(website["website_id"]),
        "user_id": str(website["user_id"]),
        "requested_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
    }


class PlatformStore:
    """`connect` returns a new psycopg connection; `with connection:` commits, or rolls back on error."""

    def __init__(self, connect):
        self._connect = connect

    def ping(self):
        with self._connect() as connection:
            connection.execute("SELECT 1")

    # ---- users
    def create_user(self, username, email, password_hash):
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)
                       RETURNING user_id, username, email, status, created_at""",
                    (username, email, password_hash),
                ).fetchone()
        except Exception as error:
            if _is_unique_violation(error):
                raise Conflict("username_or_email_already_registered") from error
            raise
        return {"user_id": str(row[0]), "username": row[1], "email": row[2], "status": row[3], "created_at": row[4]}

    def list_users(self):
        """Every registered user with the number of websites they own. Never returns the password hash."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT u.user_id, u.username, u.email, u.status, u.created_at, count(w.website_id)
                   FROM users u
                   LEFT JOIN websites w ON w.user_id = u.user_id
                   GROUP BY u.user_id
                   ORDER BY u.created_at, u.username"""
            ).fetchall()
        return [
            {
                "userId": str(row[0]),
                "username": row[1],
                "email": row[2],
                "status": row[3],
                "createdAt": _iso(row[4]),
                "websites": row[5],
            }
            for row in rows
        ]

    def find_user(self, email):
        """The registered user with this e-mail, or None."""
        with self._connect() as connection:
            row = connection.execute("SELECT user_id, status FROM users WHERE email = %s", (email,)).fetchone()
        return None if row is None else {"user_id": str(row[0]), "status": row[1]}

    # ---- websites
    def request_website(self, user_id, tenant, domain, name):
        """Create the website, its platform domain, default config and a request event, in one transaction.

        Returns (event, needs_publish). Repeating the same request returns the same event (idempotent);
        an unpublished event is published again.
        """
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    """SELECT w.website_id, w.user_id, w.status, d.domain_name
                       FROM websites w
                       LEFT JOIN domains d ON d.website_id = w.website_id AND d.domain_type = 'PLATFORM'
                       WHERE w.namespace = %s FOR UPDATE OF w""",
                    (tenant,),
                ).fetchone()
                if existing is not None:
                    website_id, owner, status, existing_domain = existing
                    if str(owner) != str(user_id) or existing_domain != domain or status == "DELETING":
                        raise Conflict("tenant_or_domain_already_exists")
                    row = connection.execute(
                        """SELECT event_id, created_at, published_status FROM deployment_events
                           WHERE website_id = %s AND event_type = %s ORDER BY created_at DESC LIMIT 1""",
                        (website_id, REQUEST_EVENT),
                    ).fetchone()
                    website = {"tenant": tenant, "domain": domain, "website_id": website_id, "user_id": user_id}
                    return _event(REQUEST_EVENT, row[0], row[1], website), row[2] != "PUBLISHED"

                website_id = connection.execute(
                    """INSERT INTO websites (user_id, website_name, namespace) VALUES (%s, %s, %s)
                       RETURNING website_id""",
                    (user_id, name, tenant),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO domains (website_id, domain_name) VALUES (%s, %s)", (website_id, domain)
                )
                connection.execute("INSERT INTO website_configs (website_id) VALUES (%s)", (website_id,))
                event_id, created_at = connection.execute(
                    """INSERT INTO deployment_events (website_id, event_type) VALUES (%s, %s)
                       RETURNING event_id, created_at""",
                    (website_id, REQUEST_EVENT),
                ).fetchone()
        except Exception as error:
            if _is_unique_violation(error):
                raise Conflict("tenant_or_domain_already_exists") from error
            raise
        website = {"tenant": tenant, "domain": domain, "website_id": website_id, "user_id": user_id}
        return _event(REQUEST_EVENT, event_id, created_at, website), True

    def list_websites(self, email=None):
        """All websites, or only those of the user with this e-mail."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT w.namespace, w.website_name, d.domain_name, w.status, w.created_at, w.updated_at,
                          last.status, last.image_tag, last.completed_at, u.email
                   FROM websites w
                   JOIN users u ON u.user_id = w.user_id
                   LEFT JOIN domains d ON d.website_id = w.website_id AND d.domain_type = 'PLATFORM'
                   LEFT JOIN LATERAL (
                       SELECT status, image_tag, completed_at FROM deployments
                       WHERE website_id = w.website_id ORDER BY created_at DESC LIMIT 1
                   ) AS last ON true
                   WHERE %s::text IS NULL OR u.email = %s
                   ORDER BY w.created_at""",
                (email, email),
            ).fetchall()
        return [
            {
                "tenant": row[0],
                "name": row[1],
                "ownerEmail": row[9],
                "domain": row[2],
                "status": row[3],
                "createdAt": _iso(row[4]),
                "updatedAt": _iso(row[5]),
                "lastDeployment": None
                if row[6] is None
                else {"status": row[6], "imageTag": row[7], "completedAt": _iso(row[8])},
            }
            for row in rows
        ]

    def request_deletion(self, tenant):
        """Mark the website DELETING and record a deletion event. Returns (event, needs_publish)."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT w.website_id, w.status, d.domain_name, w.user_id
                   FROM websites w
                   LEFT JOIN domains d ON d.website_id = w.website_id AND d.domain_type = 'PLATFORM'
                   WHERE w.namespace = %s FOR UPDATE OF w""",
                (tenant,),
            ).fetchone()
            if row is None:
                raise NotFound(tenant)
            website_id, status, domain, user_id = row
            website = {"tenant": tenant, "domain": domain, "website_id": website_id, "user_id": user_id}
            if status == "DELETING":
                event_row = connection.execute(
                    """SELECT event_id, created_at, published_status FROM deployment_events
                       WHERE website_id = %s AND event_type = %s ORDER BY created_at DESC LIMIT 1""",
                    (website_id, DELETION_EVENT),
                ).fetchone()
                if event_row is not None:
                    return _event(DELETION_EVENT, event_row[0], event_row[1], website), event_row[2] != "PUBLISHED"
            connection.execute("UPDATE websites SET status = 'DELETING' WHERE website_id = %s", (website_id,))
            event_id, created_at = connection.execute(
                """INSERT INTO deployment_events (website_id, event_type) VALUES (%s, %s)
                   RETURNING event_id, created_at""",
                (website_id, DELETION_EVENT),
            ).fetchone()
        return _event(DELETION_EVENT, event_id, created_at, website), True

    def mark_published(self, event_id, published):
        with self._connect() as connection:
            connection.execute(
                "UPDATE deployment_events SET published_status = %s WHERE event_id = %s",
                ("PUBLISHED" if published else "FAILED", event_id),
            )


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value
