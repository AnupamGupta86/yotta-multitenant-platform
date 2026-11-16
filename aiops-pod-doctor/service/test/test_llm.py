import io
import json
import unittest
import urllib.error
from unittest import mock

from src.detector import PodFailure
from src.llm import GrokClient, GrokError, build_prompt, fallback_analysis, redact

FAILURE = PodFailure("demo", "web-1", "app", "CrashLoopBackOff", "last exit: Error (exit code 1)", 3, "Running")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def grok_answer(text):
    return FakeResponse(json.dumps({"choices": [{"message": {"content": text}}]}).encode())


class RedactTests(unittest.TestCase):
    def test_secrets_are_masked(self):
        text = redact("password=hunter2 Authorization: Bearer abc.def postgres://app:s3cret@db:5432/x api_key: xyz")
        for secret in ("hunter2", "abc.def", "s3cret", "xyz"):
            self.assertNotIn(secret, text)
        self.assertIn("postgres://app:***@db", text)


class PromptTests(unittest.TestCase):
    def test_prompt_has_status_events_and_logs(self):
        events = [{"type": "Warning", "reason": "BackOff", "count": 5, "message": "Back-off restarting"}]
        prompt = build_prompt(FAILURE, "current log", "FATAL: no DB password=abc", events)
        self.assertIn("Pod: demo/web-1", prompt)
        self.assertIn("Failure reason: CrashLoopBackOff", prompt)
        self.assertIn("Warning BackOff (x5): Back-off restarting", prompt)
        self.assertIn("FATAL: no DB", prompt)
        self.assertNotIn("abc", prompt.split("FATAL")[1])  # redacted

    def test_long_logs_keep_the_end(self):
        prompt = build_prompt(FAILURE, "x" * 10000 + "THE END", "", [])
        self.assertIn("(truncated)", prompt)
        self.assertIn("THE END", prompt)


class GrokClientTests(unittest.TestCase):
    def client(self, key="xai-test"):
        return GrokClient(key, "grok-4", "https://api.x.ai/v1/chat/completions")

    def test_sends_model_prompt_and_bearer_token(self):
        with mock.patch("urllib.request.urlopen", return_value=grok_answer(" Root cause: bad env ")) as urlopen:
            self.assertEqual(self.client().analyse("the prompt"), "Root cause: bad env")
        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer xai-test")
        self.assertEqual(body["model"], "grok-4")
        self.assertEqual(body["messages"][1]["content"], "the prompt")

    def test_missing_key_is_reported(self):
        with self.assertRaisesRegex(GrokError, "GROK_API_KEY"):
            self.client(key="").analyse("x")

    def test_http_error_is_reported_without_the_key(self):
        error = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))
        with (
            mock.patch("urllib.request.urlopen", side_effect=error),
            self.assertRaisesRegex(GrokError, "HTTP 401") as raised,
        ):
            self.client().analyse("x")
        self.assertNotIn("xai-test", str(raised.exception))

    def test_network_error_is_reported(self):
        with (
            mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")),
            self.assertRaises(GrokError),
        ):
            self.client().analyse("x")

    def test_unexpected_answer_is_reported(self):
        with (
            mock.patch("urllib.request.urlopen", return_value=FakeResponse(b'{"choices": []}')),
            self.assertRaisesRegex(GrokError, "no message"),
        ):
            self.client().analyse("x")

    def test_plain_http_url_is_refused(self):
        with self.assertRaises(ValueError):
            GrokClient("k", "grok-4", "http://api.x.ai/v1/chat/completions")


class FallbackTests(unittest.TestCase):
    def test_known_reason_has_specific_commands(self):
        text = fallback_analysis(FAILURE)
        self.assertIn("Workaround now:", text)
        self.assertIn("kubectl logs web-1 -n demo -c app --previous", text)

    def test_out_of_memory_crash_gets_memory_advice(self):
        failure = PodFailure("demo", "web-1", "app", "CrashLoopBackOff", "last exit: OOMKilled (exit code 137)", 2, "")
        self.assertIn("memory", fallback_analysis(failure))

    def test_unknown_reason_still_gets_steps(self):
        failure = PodFailure("demo", "web-1", "-", "Evicted", "", 0, "Failed")
        self.assertIn("kubectl describe pod web-1 -n demo", fallback_analysis(failure))


if __name__ == "__main__":
    unittest.main()
