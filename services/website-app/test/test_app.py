import unittest

from src.app import create_app, metrics_text


def client(ping=lambda: None):
    return create_app(ping).test_client()


class WebsiteAppTests(unittest.TestCase):
    def test_liveness_does_not_require_database(self):
        def unexpected():
            raise AssertionError("unexpected database ping")

        response = client(unexpected).get("/health/live")
        self.assertEqual((response.status_code, response.json), (200, {"status": "ok"}))

    def test_readiness_checks_website_database(self):
        calls = []
        response = client(lambda: calls.append(True)).get("/health/ready")
        self.assertEqual((response.status_code, response.json), (200, {"status": "ready"}))
        self.assertEqual(calls, [True])

    def test_readiness_fails_when_database_is_down(self):
        def down():
            raise OSError("connection refused")

        response = client(down).get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json, {"error": "service_unavailable"})

    def test_unknown_route_returns_json_404(self):
        response = client().get("/unknown")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["error"], "not_found")

    def test_wrong_method_returns_json_405(self):
        response = client().post("/api/v1")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json["error"], "method_not_allowed")

    def test_api_identifies_service_as_website_app(self):
        response = client().get("/api/v1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["service"], "website-app")

    def test_workload_route_supports_scaling_demo(self):
        response = client().get("/api/v1/workload")
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json["result"], 0)

    def test_security_headers_and_request_id(self):
        response = client().get("/api/v1")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(len(response.headers["X-Request-ID"]), 36)

    def test_metrics_count_requests_per_endpoint_and_status(self):
        app_client = client()
        app_client.get("/api/v1")
        app_client.get("/nowhere")
        response = app_client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.content_type)
        body = response.get_data(as_text=True)
        self.assertIn("# TYPE yotta_http_requests_total counter", body)
        self.assertIn('endpoint="/api/v1",status="200"', body)
        self.assertIn('endpoint="other",status="404"', body)
        self.assertNotIn('endpoint="/metrics"', metrics_text())


if __name__ == "__main__":
    unittest.main()
