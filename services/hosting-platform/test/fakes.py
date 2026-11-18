"""In-memory stand-in for PlatformStore, with the same rules as the database (ownership, uniqueness)."""

from datetime import UTC, datetime
from uuid import uuid4

from src.store import DELETION_EVENT, REQUEST_EVENT, Conflict, NotFound


class FakeStore:
    def __init__(self):
        self.users = {}       # user_id -> dict
        self.websites = {}    # tenant -> dict
        self.events = {}      # event_id -> dict
        self.healthy = True

    def ping(self):
        if not self.healthy:
            raise RuntimeError("database down")

    def create_user(self, username, email, password_hash):
        if any(u["username"] == username or u["email"] == email for u in self.users.values()):
            raise Conflict("taken")
        user = {"user_id": str(uuid4()), "username": username, "email": email, "password_hash": password_hash,
                "status": "ACTIVE", "created_at": datetime.now(UTC)}
        self.users[user["user_id"]] = user
        return user

    def list_users(self):
        return [
            {"userId": u["user_id"], "username": u["username"], "email": u["email"], "status": u["status"],
             "createdAt": u["created_at"].isoformat(),
             "websites": sum(1 for w in self.websites.values() if w["user_id"] == u["user_id"])}
            for u in sorted(self.users.values(), key=lambda u: u["created_at"])
        ]

    def find_user(self, email):
        return next(({"user_id": u["user_id"], "status": u["status"]} for u in self.users.values() if u["email"] == email), None)

    def _new_event(self, event_type, website):
        event = {"event_type": event_type, "event_id": str(uuid4()), "schema_version": 1, "tenant": website["tenant"],
                 "domain": website["domain"], "website_id": website["website_id"], "user_id": website["user_id"],
                 "requested_at": datetime.now(UTC).isoformat(), "published_status": "PENDING"}
        self.events[event["event_id"]] = event
        website["events"].append(event["event_id"])
        return event

    def request_website(self, user_id, tenant, domain, name):
        website = self.websites.get(tenant)
        if website:
            if website["user_id"] != user_id or website["domain"] != domain or website["status"] == "DELETING":
                raise Conflict("taken")
            event = next(self.events[e] for e in reversed(website["events"]) if self.events[e]["event_type"] == REQUEST_EVENT)
            return event, event["published_status"] != "PUBLISHED"
        if any(w["domain"] == domain for w in self.websites.values()):
            raise Conflict("taken")
        website = {"tenant": tenant, "domain": domain, "name": name, "user_id": user_id, "status": "PENDING",
                   "website_id": str(uuid4()), "events": []}
        self.websites[tenant] = website
        return self._new_event(REQUEST_EVENT, website), True

    def list_websites(self, email=None):
        owner = {u["user_id"]: u["email"] for u in self.users.values()}
        return [{"tenant": w["tenant"], "name": w["name"], "domain": w["domain"], "status": w["status"],
                 "ownerEmail": owner[w["user_id"]]}
                for w in self.websites.values() if email is None or owner[w["user_id"]] == email]

    def request_deletion(self, tenant):
        website = self.websites.get(tenant)
        if not website:
            raise NotFound(tenant)
        if website["status"] == "DELETING":
            event = next(self.events[e] for e in reversed(website["events"]) if self.events[e]["event_type"] == DELETION_EVENT)
            return event, event["published_status"] != "PUBLISHED"
        website["status"] = "DELETING"
        return self._new_event(DELETION_EVENT, website), True

    def mark_published(self, event_id, published):
        self.events[event_id]["published_status"] = "PUBLISHED" if published else "FAILED"
