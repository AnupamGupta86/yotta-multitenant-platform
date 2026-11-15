import json
import unittest
from unittest.mock import patch

from src.consumer import dispatch_website, handle_event, notify_outcome


class DeploymentOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "event_type": "WebsiteProvisioningRequested",
            "event_id": "event-123",
            "schema_version": 1,
            "tenant": "user2",
            "domain": "shop.example.com",
        }

    def test_provisioning_request_dispatches_the_helm_workflow(self):
        class Response:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        environ = {
            "GH_TOKEN": "test-token",
            "GH_REPOSITORY": "example/platform",
            "GH_WORKFLOW_NAME": "website_app_cd.yml",
            "GH_REF": "master",
        }
        with patch("src.consumer.urlopen", return_value=Response()) as open_url:
            dispatch_website(self.event, environ)

        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/example/platform/actions/workflows/website_app_cd.yml/dispatches")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(request.get_header("User-agent"), "yotta-deployment-orchestrator")
        payload = json.loads(request.data)
        self.assertEqual(payload["ref"], "master")
        self.assertEqual(payload["inputs"]["tenant"], "user2")
        self.assertEqual(payload["inputs"]["correlation_id"], "event-123")
        self.assertEqual(payload["inputs"]["image"], "")

    def test_missing_dispatch_credentials_leave_request_unprocessed(self):
        with self.assertRaisesRegex(RuntimeError, "GH_TOKEN"):
            dispatch_website(self.event, {})

    def test_created_outcome_notifies_without_dispatch(self):
        event = {
            "event_type": "WebsiteCreated",
            "event_id": "event-456",
            "tenant": "user2",
            "domain": "shop.example.com",
        }
        notifications = []
        handle_event(
            event,
            dispatch=lambda _event: self.fail("outcomes do not start deployments"),
            notify=notifications.append,
        )
        self.assertEqual(notifications, [event])

    def test_failed_outcome_notifies_without_dispatch(self):
        event = {
            "event_type": "WebsiteDeploymentFailed",
            "event_id": "event-456",
            "tenant": "user2",
            "domain": "shop.example.com",
        }
        notifications = []
        handle_event(
            event,
            dispatch=lambda _event: self.fail("outcomes do not start deployments"),
            notify=notifications.append,
        )
        self.assertEqual(notifications, [event])

    def test_outcome_logs_a_notification_without_webhook_configuration(self):
        event = {
            "event_type": "WebsiteCreated",
            "event_id": "event-456",
            "tenant": "user2",
            "domain": "shop.example.com",
        }
        with self.assertLogs("website-orchestrator", level="INFO") as captured:
            notify_outcome(event, {})
        self.assertIn("NOTIFICATION: Website user2 is ready", captured.output[0])

    def test_outcome_posts_to_configured_webhook(self):
        class Response:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        event = {
            "event_type": "WebsiteDeploymentFailed",
            "event_id": "event-456",
            "tenant": "user2",
            "domain": "shop.example.com",
        }
        with patch("src.consumer.urlopen", return_value=Response()) as open_url:
            notify_outcome(event, {"NOTIFICATION_WEBHOOK_URL": "https://hooks.example.test/events"})
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://hooks.example.test/events")
        self.assertIn(b"Deployment failed for user2", request.data)

    def test_outcome_rejects_non_https_webhook_url(self):
        with patch("src.consumer.urlopen") as open_url, self.assertRaisesRegex(
            RuntimeError, "absolute HTTPS URL"
        ):
            notify_outcome(
                {"event_type": "WebsiteCreated", "tenant": "user2", "domain": "shop.example.com"},
                {"NOTIFICATION_WEBHOOK_URL": "file:///tmp/webhook"},
            )
        open_url.assert_not_called()

    def test_unknown_event_is_acknowledged_without_notification(self):
        handle_event(
            {"event_type": "OtherEvent"},
            dispatch=lambda _event: self.fail("unexpected event must not dispatch"),
            notify=lambda _event: self.fail("unexpected event must not notify"),
        )

    def test_provisioning_request_requires_event_contract_fields(self):
        with self.assertRaisesRegex(ValueError, "missing fields"):
            handle_event({"event_type": "WebsiteProvisioningRequested", "tenant": "user2"})


if __name__ == "__main__":
    unittest.main()
