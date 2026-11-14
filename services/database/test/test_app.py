import unittest

from src.app import route


class DatabaseApiTests(unittest.TestCase):
    def test_readiness_pings_database(self):
        calls = []
        status, body = route("GET", "/health/ready", lambda: calls.append(True))
        self.assertEqual((status, body), (200, {"status": "ready"}))
        self.assertEqual(calls, [True])

    def test_liveness_is_independent_of_database(self):
        status, body = route("GET", "/health/live", lambda: self.fail("unexpected ping"))
        self.assertEqual((status, body), (200, {"status": "ok"}))

    def test_unknown_route_returns_not_found(self):
        status, body = route("GET", "/unknown", lambda: None)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")


if __name__ == "__main__":
    unittest.main()
