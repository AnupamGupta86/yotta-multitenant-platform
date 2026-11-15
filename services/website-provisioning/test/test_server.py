import os
import unittest
from unittest.mock import MagicMock, patch

from src.server import initialize_database


class ProvisioningServerTests(unittest.TestCase):
    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://test"})
    @patch("src.server.psycopg.connect")
    def test_serializes_concurrent_schema_initialization(self, connect):
        connection = MagicMock()
        connect.return_value.__enter__.return_value = connection

        initialize_database()

        statements = [call.args[0] for call in connection.execute.call_args_list]
        self.assertIn("pg_advisory_xact_lock", statements[0])
        self.assertIn("CREATE TABLE IF NOT EXISTS website_requests", statements[1])


if __name__ == "__main__":
    unittest.main()
