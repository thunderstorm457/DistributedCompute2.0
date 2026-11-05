import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from server.main import Base, Node


ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        self.database_url = f"sqlite:///{self.database_path}"

    def tearDown(self):
        self.temp_dir.cleanup()

    def upgrade(self, revision="head"):
        config = Config(str(ROOT / "alembic.ini"))
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": self.database_url,
                "APP_ENV": "development",
                "RATE_LIMIT_STORAGE_URI": "memory://",
            },
        ):
            command.upgrade(config, revision)

    def test_upgrade_creates_fresh_schema_and_is_repeatable(self):
        self.upgrade()
        self.upgrade()

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertTrue(inspector.has_table("nodes"))
            self.assertTrue(inspector.has_table("challenges"))
            self.assertTrue(inspector.has_table("test_runs"))
            self.assertTrue(inspector.has_table("test_shards"))
            self.assertTrue(inspector.has_table("alembic_version"))
            self.assertIn(
                "ix_nodes_public_key",
                {index["name"] for index in inspector.get_indexes("nodes")},
            )
            self.assertIn(
                "ix_nodes_role_enrollment_status",
                {index["name"] for index in inspector.get_indexes("nodes")},
            )
            self.assertIn(
                "ix_test_shards_node_status_run",
                {index["name"] for index in inspector.get_indexes("test_shards")},
            )
        finally:
            engine.dispose()

    def test_upgrade_approves_and_preserves_legacy_node_data(self):
        self.upgrade("0001_initial")
        engine = create_engine(self.database_url)
        with engine.begin() as connection:
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                text(
                    "INSERT INTO nodes "
                    "(id, public_key, role, hardware, registered_at, last_seen) "
                    "VALUES (:id, :public_key, :role, :hardware, :registered_at, :last_seen)"
                ),
                {
                    "id": "node-1",
                    "public_key": "test-public-key",
                    "role": "compute",
                    "hardware": '{"cpu_cores": 4}',
                    "registered_at": now,
                    "last_seen": now,
                },
            )
        engine.dispose()

        self.upgrade()

        engine = create_engine(self.database_url)
        try:
            with engine.connect() as connection:
                node = connection.execute(
                    text(
                        "SELECT enrollment_status, hardware FROM nodes WHERE id = 'node-1'"
                    )
                ).one()
                self.assertEqual(node.enrollment_status, "approved")
                self.assertEqual(node.hardware, '{"cpu_cores": 4}')
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()