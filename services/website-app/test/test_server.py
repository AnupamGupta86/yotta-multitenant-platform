import os
import unittest
from unittest.mock import patch

import psycopg

from src.server import check_settings, wait_for_database


class WebsiteServerTests(unittest.TestCase):
    @patch("src.server.time.sleep")
    @patch("src.server.ping_database")
    def test_waits_for_database_before_starting_server(self, ping, sleep):
        ping.side_effect = [psycopg.OperationalError("database starting"), None]

        wait_for_database(attempts=3, interval_seconds=1)

        self.assertEqual(ping.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("src.server.time.sleep")
    @patch("src.server.ping_database", side_effect=psycopg.OperationalError("unavailable"))
    def test_stops_retrying_when_database_never_becomes_ready(self, ping, sleep):
        with self.assertRaises(psycopg.OperationalError):
            wait_for_database(attempts=2, interval_seconds=1)

        self.assertEqual(ping.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_refuses_to_start_without_database_settings(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            check_settings()

    def test_accepts_database_url_or_separate_settings(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x"}, clear=True):
            check_settings()
        settings = {"DB_HOST": "h", "DB_NAME": "n", "DB_USER": "u", "DB_PASSWORD": "p"}
        with patch.dict(os.environ, settings, clear=True):
            check_settings()


if __name__ == "__main__":
    unittest.main()
