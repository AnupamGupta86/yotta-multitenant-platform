import unittest

from fastapi.testclient import TestClient

from src.app import create_app
from test.fakes import FakeStore


class ProvisioningApiTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.published = []
        self.kafka_up = True
        self.app = create_app(store=self.store, publish=self.published.append, kafka_ready=lambda: self.kafka_up)
        self.client = TestClient(self.app)

    def register(self, username="alice", email="Alice@Example.com", password="correct-horse-1"):
        return self.client.post("/api/v1/users/register",
                                json={"username": username, "email": email, "password": password})

    def request(self, tenant="user2", domain="user2.myyotta.com", email="alice@example.com"):
        return self.client.post("/api/v1/websites", json={"tenant": tenant, "domain": domain, "email": email})

    # ---- health
    def test_liveness_does_not_check_dependencies(self):
        self.store.healthy = False
        self.assertEqual(self.client.get("/health/live").json(), {"status": "ok"})

    def test_readiness_checks_the_database(self):
        self.assertEqual(self.client.get("/health/ready").json(), {"status": "ready"})
        self.store.healthy = False
        self.assertEqual(self.client.get("/health/ready").status_code, 503)

    def test_health_reports_database_and_kafka(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"], {"database": "ok", "kafka": "ok"})
        self.kafka_up = False
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")
        self.store.healthy = False
        self.assertEqual(self.client.get("/api/v1/health").json()["checks"], {"database": "unavailable", "kafka": "unavailable"})

    def test_there_is_no_login_endpoint(self):
        self.assertEqual(self.client.post("/api/v1/auth/login", json={}).status_code, 404)

    # ---- register
    def test_register_creates_a_user_without_returning_the_password(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual((body["username"], body["email"], body["status"]), ("alice", "alice@example.com", "ACTIVE"))
        self.assertNotIn("password", str(body).lower())
        stored = next(iter(self.store.users.values()))["password_hash"]
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn("correct-horse-1", stored)

    def test_register_rejects_duplicates_and_weak_input(self):
        self.register()
        self.assertEqual(self.register(username="alice2").status_code, 409)          # same e-mail
        self.assertEqual(self.register(email="other@example.com").status_code, 409)  # same username
        self.assertEqual(self.register(username="bob", email="bob@example.com", password="short").status_code, 422)
        self.assertEqual(self.register(username="bob", email="not-an-email").status_code, 422)

    # ---- websites (no login)
    def test_request_website_records_and_publishes_the_event(self):
        self.register()
        response = self.request(domain="User2.MyYotta.COM", email="ALICE@example.com")
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual((body["status"], body["tenant"], body["domain"]), ("accepted", "user2", "user2.myyotta.com"))
        self.assertEqual(len(self.published), 1)
        event = self.published[0]
        self.assertEqual((event["event_type"], event["tenant"], event["event_id"]),
                         ("WebsiteProvisioningRequested", "user2", body["eventId"]))
        self.assertEqual(self.store.events[body["eventId"]]["published_status"], "PUBLISHED")

    def test_owner_must_be_a_registered_active_user(self):
        self.assertEqual(self.request(email="nobody@example.com").status_code, 404)
        self.assertEqual(self.client.post("/api/v1/websites", json={"tenant": "user2", "domain": "a.example.com"}).status_code, 422)
        self.register()
        next(iter(self.store.users.values()))["status"] = "SUSPENDED"
        self.assertEqual(self.request().status_code, 403)
        self.assertEqual(self.published, [])

    def test_repeating_a_request_is_idempotent(self):
        self.register()
        first, second = self.request(), self.request()
        self.assertEqual(first.json()["eventId"], second.json()["eventId"])
        self.assertEqual(len(self.published), 1)

    def test_kafka_failure_returns_503_and_a_retry_publishes(self):
        self.register()

        def broken(_event):
            raise RuntimeError("kafka down")

        self.app.state.publish = broken
        self.assertEqual(self.request().status_code, 503)
        self.app.state.publish = self.published.append
        self.assertEqual(self.request().status_code, 202)
        self.assertEqual(len(self.published), 1)

    def test_tenant_or_domain_of_another_user_is_a_conflict(self):
        self.register()
        self.register(username="bob", email="bob@example.com")
        self.request()
        taken_tenant = self.request(domain="b.myyotta.com", email="bob@example.com")
        taken_domain = self.request(tenant="user7", email="bob@example.com")
        self.assertEqual((taken_tenant.status_code, taken_domain.status_code), (409, 409))

    def test_invalid_tenant_or_domain_is_rejected(self):
        self.register()
        self.assertEqual(self.request(tenant="../../admin").status_code, 422)
        self.assertEqual(self.request(domain="bad domain").status_code, 422)
        self.assertEqual(self.published, [])

    def test_list_all_websites_or_one_users(self):
        self.register()
        self.register(username="bob", email="bob@example.com")
        self.request()
        self.request(tenant="user3", domain="user3.myyotta.com", email="bob@example.com")
        everything = self.client.get("/api/v1/websites").json()["websites"]
        self.assertEqual(sorted(w["tenant"] for w in everything), ["user2", "user3"])
        mine = self.client.get("/api/v1/websites", params={"email": "Bob@Example.com"}).json()["websites"]
        self.assertEqual([(w["tenant"], w["ownerEmail"]) for w in mine], [("user3", "bob@example.com")])
        self.assertEqual(self.client.get("/api/v1/websites", params={"email": "bad"}).status_code, 422)

    def test_delete_requests_teardown_once(self):
        self.register()
        self.request()
        first = self.client.delete("/api/v1/websites", params={"tenant": "user2"})
        second = self.client.delete("/api/v1/websites", params={"tenant": "user2"})
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()["eventId"], second.json()["eventId"])
        deletions = [e for e in self.published if e["event_type"] == "WebsiteDeletionRequested"]
        self.assertEqual(len(deletions), 1)
        self.assertEqual(self.store.websites["user2"]["status"], "DELETING")
        # a website being deleted cannot be requested again until it is gone
        self.assertEqual(self.request().status_code, 409)

    def test_delete_unknown_or_invalid_tenant(self):
        self.assertEqual(self.client.delete("/api/v1/websites", params={"tenant": "nosuch"}).status_code, 404)
        self.assertEqual(self.client.delete("/api/v1/websites", params={"tenant": "Bad_Name"}).status_code, 422)
        self.assertEqual(self.client.delete("/api/v1/websites").status_code, 422)


if __name__ == "__main__":
    unittest.main()
