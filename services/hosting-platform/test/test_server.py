import unittest

from src.store import Conflict, PlatformStore, database_url


class DatabaseSettingsTests(unittest.TestCase):
    def test_database_url_wins(self):
        self.assertEqual(database_url({"DATABASE_URL": "postgresql://ci:ci@db/ci"}), "postgresql://ci:ci@db/ci")

    def test_built_from_db_settings_with_password_escaped(self):
        url = database_url({"DB_HOST": "postgresql.platform-data.svc.cluster.local", "DB_NAME": "myyotta_platform",
                            "DB_USER": "myyotta_app", "DB_PASSWORD": "p@ss/w:rd"})
        self.assertEqual(url, "postgresql://myyotta_app:p%40ss%2Fw%3Ard@postgresql.platform-data.svc.cluster.local:5432/myyotta_platform")

    def test_missing_settings_fail_clearly(self):
        with self.assertRaisesRegex(RuntimeError, "DB_PASSWORD"):
            database_url({"DB_HOST": "h", "DB_NAME": "n", "DB_USER": "u"})


class _UniqueViolation(Exception):
    sqlstate = "23505"


class _Connection:
    def __init__(self, error):
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, *_args):
        raise self.error


class StoreErrorTests(unittest.TestCase):
    def test_unique_violation_becomes_conflict(self):
        store = PlatformStore(lambda: _Connection(_UniqueViolation()))
        with self.assertRaises(Conflict):
            store.create_user("alice", "alice@example.com", "hash")

    def test_other_database_errors_are_not_hidden(self):
        store = PlatformStore(lambda: _Connection(RuntimeError("connection lost")))
        with self.assertRaises(RuntimeError):
            store.create_user("alice", "alice@example.com", "hash")


if __name__ == "__main__":
    unittest.main()
