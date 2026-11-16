import unittest

from src.detector import find_failure


def pod(phase="Running", statuses=None, conditions=None, **metadata):
    return {
        "metadata": {"namespace": "demo", "name": "web-1", **metadata},
        "status": {"phase": phase, "containerStatuses": statuses or [], "conditions": conditions or []},
    }


def waiting(reason, message="", restarts=0, last_exit=None):
    status = {"name": "app", "restartCount": restarts, "state": {"waiting": {"reason": reason, "message": message}}}
    if last_exit:
        status["lastState"] = {"terminated": last_exit}
    return status


class FindFailureTests(unittest.TestCase):
    def test_running_pod_is_healthy(self):
        self.assertIsNone(find_failure(pod(statuses=[{"name": "app", "state": {"running": {}}}])))

    def test_starting_pod_is_not_a_failure(self):
        self.assertIsNone(find_failure(pod("Pending", [waiting("ContainerCreating")])))

    def test_finished_job_pod_is_not_a_failure(self):
        self.assertIsNone(find_failure(pod("Succeeded")))

    def test_pod_being_deleted_is_ignored(self):
        self.assertIsNone(
            find_failure(pod(statuses=[waiting("CrashLoopBackOff")], deletionTimestamp="2026-11-16T00:00:00Z"))
        )

    def test_crash_loop_includes_last_exit(self):
        failure = find_failure(
            pod(statuses=[waiting("CrashLoopBackOff", "back-off", 4, {"reason": "OOMKilled", "exitCode": 137})])
        )
        self.assertEqual(failure.reason, "CrashLoopBackOff")
        self.assertEqual(failure.container, "app")
        self.assertEqual(failure.restart_count, 4)
        self.assertIn("OOMKilled (exit code 137)", failure.message)
        self.assertEqual(failure.key, "demo/web-1/app/CrashLoopBackOff")

    def test_image_pull_failure(self):
        failure = find_failure(pod("Pending", [waiting("ImagePullBackOff", "not found")]))
        self.assertEqual(failure.reason, "ImagePullBackOff")

    def test_container_that_just_exited_is_left_to_restart(self):
        exited = {"name": "app", "state": {"terminated": {"exitCode": 1, "reason": "Error"}}}
        self.assertIsNone(find_failure(pod("Running", [exited])))

    def test_failed_pod_names_the_container_that_failed(self):
        data = pod("Failed")
        data["status"]["initContainerStatuses"] = [
            {"name": "migrate", "state": {"terminated": {"exitCode": 1, "reason": "Error"}}}
        ]
        failure = find_failure(data)
        self.assertEqual((failure.container, failure.reason), ("migrate", "Error"))

    def test_unschedulable_pod(self):
        conditions = [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable", "message": "0/1 nodes"}]
        failure = find_failure(pod("Pending", conditions=conditions))
        self.assertEqual((failure.reason, failure.message), ("Unschedulable", "0/1 nodes"))

    def test_evicted_pod(self):
        data = pod("Failed")
        data["status"].update(reason="Evicted", message="low on memory")
        self.assertEqual(find_failure(data).reason, "Evicted")

    def test_missing_fields_do_not_crash(self):
        self.assertIsNone(find_failure({}))


if __name__ == "__main__":
    unittest.main()
