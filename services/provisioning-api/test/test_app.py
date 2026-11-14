import unittest

from src.app import route


class ProvisioningApiTests(unittest.TestCase):
    def test_liveness_does_not_check_database(self):
        status, body = route("GET", "/health/live", lambda: self.fail("unexpected database check"))
        self.assertEqual((status, body), (200, {"status": "ok"}))

    def test_readiness_checks_database(self):
        calls = []
        status, body = route("GET", "/health/ready", lambda: calls.append(True))
        self.assertEqual((status, body), (200, {"status": "ready"}))
        self.assertEqual(calls, [True])

    def test_unrecognized_route_is_not_found(self):
        status, body = route("GET", "/missing", lambda: None)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")


if __name__ == "__main__":
    unittest.main()
