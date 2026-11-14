import unittest

from publish_event import build_event


class WebsiteEventTests(unittest.TestCase):
    def test_website_created_event_has_expected_contract(self):
        event = build_event("user2", "user2.example.test", "ghcr.io/example/website:abc", "2026-11-15T00:00:00+00:00")
        self.assertEqual(event["event_type"], "WebsiteCreated")
        self.assertEqual(event["schema_version"], 1)
        self.assertEqual(event["tenant"], "user2")
        self.assertEqual(event["domain"], "user2.example.test")
        self.assertEqual(event["image"], "ghcr.io/example/website:abc")
        self.assertEqual(event["deployed_at"], "2026-11-15T00:00:00+00:00")
        self.assertTrue(event["event_id"])


if __name__ == "__main__":
    unittest.main()
