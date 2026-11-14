import unittest

from src.app import metrics_text, route


class WebsiteApiTests(unittest.TestCase):
    def test_liveness_does_not_require_database(self):
        status, body = route("GET", "/health/live", lambda: self.fail("unexpected ping"))
        self.assertEqual((status, body), (200, {"status": "ok"}))

    def test_readiness_checks_website_database(self):
        calls = []
        status, body = route("GET", "/health/ready", lambda: calls.append(True))
        self.assertEqual((status, body), (200, {"status": "ready"}))
        self.assertEqual(calls, [True])

    def test_unknown_route_returns_404(self):
        status, body = route("GET", "/unknown", lambda: None)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")

    def test_metrics_endpoint_exposes_prometheus_format(self):
        self.assertIn("# TYPE yotta_http_requests_total counter", metrics_text())

    def test_workload_route_supports_scaling_demo(self):
        status, body = route("GET", "/api/v1/workload", lambda: None)
        self.assertEqual(status, 200)
        self.assertGreater(body["result"], 0)


if __name__ == "__main__":
    unittest.main()
