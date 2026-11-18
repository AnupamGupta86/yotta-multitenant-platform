import json
import unittest
from unittest.mock import patch

from src.consumer import (
    dispatch_teardown,
    dispatch_website,
    handle_event,
    notify_outcome,
)
from src.store import _split_image, database_url


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
            "GH_WORKFLOW_NAME": "website_cd.yml",
            "GH_REF": "master",
        }
        with patch("src.consumer.urlopen", return_value=Response()) as open_url:
            dispatch_website(self.event, environ)

        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/example/platform/actions/workflows/website_cd.yml/dispatches")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(request.get_header("User-agent"), "yotta-website-orchestrator")
        payload = json.loads(request.data)
        self.assertEqual(payload["ref"], "master")
        self.assertEqual(payload["inputs"]["tenant"], "user2")
        self.assertEqual(payload["inputs"]["correlation_id"], "event-123")
        self.assertEqual(payload["inputs"]["image_tag"], "")
        # every input sent must exist in website_cd.yml, or GitHub rejects the dispatch (HTTP 422)
        self.assertEqual(set(payload["inputs"]), {"tenant", "domain", "image_tag", "correlation_id"})

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

    def test_every_handled_event_logs_one_website_event_line(self):
        event = {
            "event_type": "WebsiteDeploymentFailed",
            "event_id": "event-789",
            "tenant": "user3",
            "domain": "user3.example.com",
        }
        with self.assertLogs("website-orchestrator", level="INFO") as captured:
            handle_event(event, dispatch=lambda _event: None, notify=lambda _event: None)
        self.assertIn(
            "WEBSITE_EVENT event_type=WebsiteDeploymentFailed tenant=user3 domain=user3.example.com event_id=event-789",
            captured.output[0],
        )

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


class RecordingStore:
    def __init__(self):
        self.calls = []

    def record_dispatch(self, event):
        self.calls.append(("dispatch", event["tenant"]))

    def record_outcome(self, event):
        self.calls.append(("outcome", event["event_type"]))

    def remove_website(self, event):
        self.calls.append(("remove", event["tenant"]))


class DatabaseAndTeardownTests(unittest.TestCase):
    def event(self, event_type):
        return {"event_type": event_type, "event_id": "e-1", "tenant": "user2", "domain": "user2.myyotta.com"}

    def test_request_is_recorded_before_the_workflow_is_started(self):
        store, order = RecordingStore(), []
        handle_event(self.event("WebsiteProvisioningRequested"), dispatch=lambda _e: order.append(list(store.calls)),
                     notify=lambda _e: None, store=store)
        self.assertEqual(order, [[("dispatch", "user2")]])

    def test_outcomes_are_recorded_and_notified(self):
        for event_type in ("WebsiteCreated", "WebsiteDeploymentFailed"):
            store, notified = RecordingStore(), []
            handle_event(self.event(event_type), dispatch=lambda _e: self.fail("no dispatch"),
                         notify=notified.append, store=store)
            self.assertEqual(store.calls, [("outcome", event_type)])
            self.assertEqual(len(notified), 1)

    def test_deletion_request_starts_teardown_only(self):
        store, started = RecordingStore(), []
        handle_event(self.event("WebsiteDeletionRequested"), dispatch=lambda _e: self.fail("no deploy"),
                     notify=lambda _e: self.fail("no notify"), teardown=started.append, store=store)
        self.assertEqual(len(started), 1)
        self.assertEqual(store.calls, [])

    def test_deleted_event_removes_the_record_and_notifies(self):
        store, notified = RecordingStore(), []
        handle_event(self.event("WebsiteDeleted"), notify=notified.append, store=store)
        self.assertEqual(store.calls, [("remove", "user2")])
        self.assertEqual(len(notified), 1)

    def test_without_a_database_events_are_still_processed(self):
        started = []
        handle_event(self.event("WebsiteProvisioningRequested"), dispatch=started.append, store=None)
        self.assertEqual(len(started), 1)

    def test_teardown_dispatch_sends_only_inputs_the_teardown_workflow_declares(self):
        class Response:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        environ = {"GH_TOKEN": "t", "GH_REPOSITORY": "example/platform", "GH_REF": "master"}
        with patch("src.consumer.urlopen", return_value=Response()) as open_url:
            dispatch_teardown(self.event("WebsiteDeletionRequested"), environ)
        request = open_url.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/actions/workflows/website_teardown.yml/dispatches"))
        self.assertEqual(set(json.loads(request.data)["inputs"]), {"tenant", "domain", "correlation_id"})

    def test_deleted_notification_text(self):
        with self.assertLogs("website-orchestrator", level="INFO") as captured:
            notify_outcome(self.event("WebsiteDeleted"), {})
        self.assertIn("NOTIFICATION: Website user2 (user2.myyotta.com) was deleted", captured.output[0])


class StoreHelperTests(unittest.TestCase):
    def test_database_url_is_optional(self):
        self.assertIsNone(database_url({}))
        self.assertEqual(
            database_url({"DB_HOST": "h", "DB_NAME": "n", "DB_USER": "u", "DB_PASSWORD": "p w"}),
            "postgresql://u:p%20w@h:5432/n",
        )

    def test_image_is_split_into_name_and_tag(self):
        self.assertEqual(_split_image("ghcr.io/o/website:1.0.0-20261118000000"),
                         ("ghcr.io/o/website", "1.0.0-20261118000000"))
        self.assertEqual(_split_image("localhost:5000/app"), ("localhost:5000/app", "unknown"))
        self.assertEqual(_split_image(""), ("unknown", "unknown"))

if __name__ == "__main__":
    unittest.main()
