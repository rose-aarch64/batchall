import sqlite3
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (puts the repo on sys.path)

from batchall import collection, db


class ReadOnlyConnectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.shared = Path(self._tmp.name) / "batchall.db"
        shared = db.connect_shared(self.shared)
        support.seed_catalog(shared)
        shared.commit()
        shared.close()
        self.uid = 1
        self.owner = db.connect_user(self.uid, self.shared)
        self.addCleanup(self.owner.close)
        collection.add(self.owner, "u-atlas")

    def open_ro(self):
        conn = db.connect_user_readonly(self.uid, self.shared)
        self.addCleanup(conn.close)
        return conn

    def test_reads_user_and_shared_tables(self):
        conn = self.open_ro()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0], 1)
        self.assertGreater(conn.execute("SELECT COUNT(*) FROM units").fetchone()[0], 0)

    def test_writes_are_refused(self):
        conn = self.open_ro()
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM collection")
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM shared.units")

    def test_missing_user_file_raises_and_creates_nothing(self):
        with self.assertRaises(FileNotFoundError):
            db.connect_user_readonly(999, self.shared)
        self.assertFalse(db.user_db_path(self.shared, 999).exists())

    def test_reads_when_wal_and_shm_files_are_absent(self):
        self.owner.close()
        target = db.user_db_path(self.shared, self.uid)
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        conn = self.open_ro()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0], 1)

    def test_sees_committed_changes_while_owner_is_connected(self):
        conn = self.open_ro()
        collection.add(self.owner, "u-locust")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0], 2)
