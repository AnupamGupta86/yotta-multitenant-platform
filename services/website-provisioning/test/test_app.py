import unittest

from fastapi.testclient import TestClient
from src.app import RequestConflict, create_app


class ProvisioningApiTests(unittest.TestCase):
    def setUp(self):
        self.submissions = []
        self.app = create_app(
            ping=lambda: None,
            submit=lambda tenant, domain: self.submissions.append((tenant, domain))
            or {"event_id": "request-1"},
        )
        self.client = TestClient(self.app)

    def test_liveness_does_not_check_database(self):
        self.app.state.ping_database = lambda: self.fail("unexpected database check")
        self.assertEqual(self.client.get("/health/live").json(), {"status": "ok"})

    def test_readiness_checks_database(self):
        calls = []
        self.app.state.ping_database = lambda: calls.append(True)
        response = self.client.get("/health/ready")
        self.assertEqual(response.json(), {"status": "ready"})
        self.assertEqual(calls, [True])

    def test_valid_request_is_accepted(self):
        response = self.client.post(
            "/api/v1/websites",
            json={"tenant": "user2", "domain": "Shop.Example.COM"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {
            "status": "accepted",
            "eventId": "request-1",
            "tenant": "user2",
            "domain": "shop.example.com",
        })
        self.assertEqual(self.submissions, [("user2", "shop.example.com")])

    def test_invalid_tenant_is_rejected(self):
        response = self.client.post(
            "/api/v1/websites",
            json={"tenant": "../../admin", "domain": "shop.example.com"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.submissions, [])

    def test_invalid_domain_is_rejected(self):
        response = self.client.post(
            "/api/v1/websites",
            json={"tenant": "user2", "domain": "bad domain"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.submissions, [])

    def test_database_conflict_is_reported_to_the_user(self):
        self.app.state.submit_website = lambda *_: (_ for _ in ()).throw(RequestConflict())
        response = self.client.post(
            "/api/v1/websites",
            json={"tenant": "user2", "domain": "shop.example.com"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "tenant_or_domain_already_exists")


if __name__ == "__main__":
    unittest.main()
