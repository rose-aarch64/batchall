import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (puts the repo on sys.path)

from batchall import collection, db


class DbSplitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "batchall.db"
        self.shared = db.connect_shared(self.path)
        self.addCleanup(self.shared.close)
        support.seed_catalog(self.shared)
        self.shared.commit()

    def open_user(self, user_id):
        conn = db.connect_user(user_id, self.path)
        self.addCleanup(conn.close)
        return conn

    def test_schema_parts_add_up(self):
        self.assertEqual(db.SCHEMA, db.SHARED_SCHEMA + db.USER_SCHEMA)

    def test_user_connections_see_only_their_own_rows(self):
        one = self.open_user(1)
        collection.add(one, "u-atlas")
        entries = collection.list_collection(one)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["chassis"], "Atlas")
        self.assertEqual(entries[0]["bv"], 1897)
        two = self.open_user(2)
        self.assertEqual(collection.list_collection(two), [])
        collection.add(two, "u-locust")
        self.assertEqual([e["chassis"] for e in collection.list_collection(two)], ["Locust"])
        self.assertEqual([e["chassis"] for e in collection.list_collection(one)], ["Atlas"])

    def test_user_file_is_created_under_users_dir(self):
        self.open_user(7)
        target = db.user_db_path(self.path, 7)
        self.assertEqual(target, self.path.parent / "users" / "7.db")
        self.assertTrue(target.exists())

    def test_connect_user_refuses_a_missing_shared_file(self):
        missing = Path(self._tmp.name) / "elsewhere" / "batchall.db"
        with self.assertRaises(ValueError) as caught:
            db.connect_user(1, missing)
        self.assertIn("connect_shared", str(caught.exception))
        self.assertFalse(missing.exists())
        self.assertFalse(db.user_db_path(missing, 1).exists())
        self.assertFalse(missing.parent.exists())

    def test_user_connection_cannot_see_shared_tables_as_main(self):
        conn = self.open_user(1)
        names = {r["name"] for r in conn.execute("SELECT name FROM main.sqlite_master")}
        self.assertTrue({"collection", "copies", "forces"} <= names)
        for shared_name in ("units", "users", "sessions"):
            self.assertNotIn(shared_name, names)
        shared_names = {r["name"] for r in conn.execute("SELECT name FROM shared.sqlite_master")}
        self.assertIn("units", shared_names)
        self.assertIn("users", shared_names)

    def test_shared_connection_has_no_user_tables(self):
        names = {r["name"] for r in self.shared.execute("SELECT name FROM sqlite_master")}
        self.assertTrue({"units", "users", "invites", "sessions", "meta"} <= names)
        for user_name in db.USER_TABLES:
            self.assertNotIn(user_name, names)

    def test_connect_refuses_a_multiuser_database(self):
        self.assertFalse(db.is_multiuser(self.path))
        db.set_meta(self.shared, "multiuser", 1)
        self.shared.commit()
        self.assertTrue(db.is_multiuser(self.path))
        with self.assertRaises(db.MultiUserDatabase):
            db.connect(self.path)

    def test_is_multiuser_is_false_for_fresh_and_memory(self):
        fresh = Path(self._tmp.name) / "nothing" / "fresh.db"
        self.assertFalse(db.is_multiuser(fresh))
        self.assertFalse(fresh.exists())
        self.assertFalse(fresh.parent.exists())
        self.assertFalse(db.is_multiuser(":memory:"))

    def test_is_multiuser_is_false_without_a_meta_table(self):
        import sqlite3

        bare = Path(self._tmp.name) / "bare.db"
        sqlite3.connect(bare).close()
        self.assertFalse(db.is_multiuser(bare))

    def test_wal_is_on(self):
        user = self.open_user(1)
        self.assertEqual(self.shared.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(user.execute("PRAGMA main.journal_mode").fetchone()[0], "wal")
        self.assertEqual(user.execute("PRAGMA shared.journal_mode").fetchone()[0], "wal")


if __name__ == "__main__":
    unittest.main()
