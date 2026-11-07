import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support

from batchall import config, db, multiuser

PHOTO = bytes(range(256)) * 8  # 2 KB


class AdoptDataTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "batchall.db"

    def build(self, populate=True):
        conn = db.connect(self.path)
        support.seed_catalog(conn)
        if populate:
            with conn:
                conn.execute("INSERT INTO collection (id, unit_uuid, qty, painted) VALUES (3, 'u-atlas', 2, 1)")
                conn.execute("INSERT INTO collection (id, unit_uuid, qty) VALUES (9, 'u-locust', 1)")
                conn.execute("INSERT INTO copies (id, collection_id, note, tag, painted) VALUES (4, 3, 'Big Bertha', 'a', 1)")
                conn.execute("INSERT INTO copies (id, collection_id, note) VALUES (8, 3, '')")
                conn.execute("INSERT INTO copies (id, collection_id, note) VALUES (12, 9, 'Zippy')")
                conn.execute(
                    "INSERT INTO mini_photos (copy_id, content_type, data, width, height) "
                    "VALUES (4, 'image/jpeg', ?, 640, 480)",
                    (PHOTO,),
                )
                conn.execute("INSERT INTO forces (id, name, formation) VALUES (2, 'Strike Lance', 'lance')")
                conn.execute(
                    "INSERT INTO force_units (force_id, collection_id, as_uuid, qty) VALUES (2, 3, 'u-shadow', 1)"
                )
                conn.execute("INSERT INTO force_units (force_id, collection_id, as_uuid, qty) VALUES (2, 9, '', 1)")
                conn.execute(
                    "INSERT INTO company_logos (key, name, content_type, data) VALUES ('wolf', 'Wolf', 'image/png', ?)",
                    (b"png",),
                )
                conn.execute("INSERT INTO company_colors (key, name, color) VALUES ('wolf', 'Wolf', '#aabbcc')")
        return conn

    def counts(self, conn):
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in db.USER_TABLES}

    def test_adopt_preserves_every_row(self):
        original = self.build()
        before = self.counts(original)
        ids = {t: [r[0] for r in original.execute(f"SELECT rowid FROM {t} ORDER BY rowid")] for t in db.USER_TABLES}
        original.close()
        self.assertEqual(before, {t: n for t, n in zip(db.USER_TABLES, (2, 3, 1, 1, 2, 1, 1))})

        result = multiuser.adopt_data(self.path, 5)

        self.assertFalse(result["already"])
        self.assertEqual(result["user_id"], 5)
        self.assertEqual(result["counts"], before)
        user = db.connect_user(5, self.path)
        self.addCleanup(user.close)
        self.assertEqual(self.counts(user), before)
        for table in db.USER_TABLES:
            self.assertEqual([r[0] for r in user.execute(f"SELECT rowid FROM {table} ORDER BY rowid")], ids[table])
        self.assertEqual(user.execute("SELECT data FROM mini_photos").fetchone()[0], PHOTO)
        self.assertEqual(
            [r[0] for r in user.execute("SELECT as_uuid FROM force_units ORDER BY collection_id")], ["u-shadow", ""]
        )
        self.assertEqual(user.execute("SELECT name FROM forces").fetchone()[0], "Strike Lance")
        self.assertEqual(user.execute("SELECT color FROM company_colors").fetchone()[0], "#aabbcc")
        self.assertEqual(user.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_deleted_ids_are_not_reused(self):
        conn = self.build()
        with conn:
            conn.execute("INSERT INTO collection (id, unit_uuid) VALUES (50, 'u-condor')")
            conn.execute("DELETE FROM collection WHERE id = 50")
        conn.close()
        multiuser.adopt_data(self.path, 5)
        user = db.connect_user(5, self.path)
        self.addCleanup(user.close)
        with user:
            cur = user.execute("INSERT INTO collection (unit_uuid) VALUES ('u-scorpion')")
        self.assertEqual(cur.lastrowid, 51)

    def test_adopt_makes_a_backup(self):
        self.build().close()
        result = multiuser.adopt_data(self.path, 5)
        backup = sqlite3.connect(result["backup"])
        self.addCleanup(backup.close)
        self.assertEqual(Path(result["backup"]).name, "batchall.db.pre-multiuser")
        self.assertEqual(backup.execute("SELECT COUNT(*) FROM collection").fetchone()[0], 2)

    def test_existing_backup_name_is_not_overwritten(self):
        self.build().close()
        taken = self.path.with_name("batchall.db.pre-multiuser")
        taken.write_bytes(b"keep me")
        result = multiuser.adopt_data(self.path, 5)
        self.assertEqual(taken.read_bytes(), b"keep me")
        self.assertNotEqual(result["backup"], str(taken))
        self.assertTrue(Path(result["backup"]).is_file())

    def test_shared_file_loses_the_user_tables_and_refuses_connect(self):
        self.build().close()
        multiuser.adopt_data(self.path, 5)
        shared = sqlite3.connect(self.path)
        self.addCleanup(shared.close)
        names = {r[0] for r in shared.execute("SELECT name FROM sqlite_master")}
        for table in db.USER_TABLES:
            self.assertNotIn(table, names)
        self.assertGreater(shared.execute("SELECT COUNT(*) FROM units").fetchone()[0], 0)
        with self.assertRaises(db.MultiUserDatabase):
            db.connect(self.path)
        self.assertTrue(db.is_multiuser(self.path))

    def test_adopt_twice_changes_nothing(self):
        self.build().close()
        first = multiuser.adopt_data(self.path, 5)
        second = multiuser.adopt_data(self.path, 5)
        self.assertTrue(second["already"])
        self.assertIsNone(second["backup"])
        self.assertEqual(second["counts"], first["counts"])

    def test_adopt_on_an_empty_database(self):
        self.build(populate=False).close()
        result = multiuser.adopt_data(self.path, 5)
        self.assertEqual(result["counts"], {t: 0 for t in db.USER_TABLES})
        self.assertTrue(db.is_multiuser(self.path))

    def test_adopt_upgrades_an_old_schema_first(self):
        conn = sqlite3.connect(self.path)
        conn.executescript(
            "CREATE TABLE collection (id INTEGER PRIMARY KEY AUTOINCREMENT, unit_uuid TEXT NOT NULL UNIQUE, "
            "qty INTEGER NOT NULL DEFAULT 1, painted INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '', "
            "added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
            "INSERT INTO collection (unit_uuid, qty) VALUES ('u-atlas', 2);"
        )
        conn.commit()
        conn.close()
        result = multiuser.adopt_data(self.path, 5)
        self.assertEqual(result["counts"]["collection"], 1)
        self.assertEqual(result["counts"]["copies"], 2)  # plain mini rows added by the upgrade

    def test_failed_verification_leaves_the_original_intact(self):
        self.build().close()
        with mock.patch.object(multiuser, "_mismatches", return_value=["collection"]):
            with self.assertRaises(ValueError):
                multiuser.adopt_data(self.path, 5)
        self.assertFalse(db.is_multiuser(self.path))
        self.assertFalse(db.user_db_path(self.path, 5).exists())
        conn = db.connect(self.path)
        self.addCleanup(conn.close)
        self.assertEqual(self.counts(conn)["collection"], 2)

    def test_crash_after_the_copy_is_recoverable(self):
        self.build().close()
        with mock.patch.object(multiuser, "_drop_user_tables", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                multiuser.adopt_data(self.path, 5)
        self.assertFalse(db.is_multiuser(self.path))
        conn = db.connect(self.path)
        self.assertEqual(self.counts(conn)["collection"], 2)
        conn.close()

        # A leftover, partly filled user file from the crashed run must not be trusted.
        leftover = sqlite3.connect(db.user_db_path(self.path, 5))
        leftover.executescript(db.USER_SCHEMA)
        leftover.execute(f"PRAGMA user_version = {getattr(multiuser, 'ADOPT_MARKER', 0)}")
        with leftover:
            leftover.execute("INSERT INTO collection (unit_uuid) VALUES ('u-junk')")
        leftover.close()

        result = multiuser.adopt_data(self.path, 5)
        self.assertFalse(result["already"])
        self.assertTrue(db.is_multiuser(self.path))
        user = db.connect_user(5, self.path)
        self.addCleanup(user.close)
        self.assertEqual(self.counts(user)["collection"], 2)
        self.assertEqual(user.execute("SELECT COUNT(*) FROM collection WHERE unit_uuid = 'u-junk'").fetchone()[0], 0)

    def test_interrupt_after_the_drop_commit_keeps_the_user_file(self):
        self.build().close()
        real_drop = multiuser._drop_user_tables

        def drop_then_interrupt(*args):
            real_drop(*args)
            raise KeyboardInterrupt

        with mock.patch.object(multiuser, "_drop_user_tables", drop_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                multiuser.adopt_data(self.path, 5)
        self.assertTrue(db.is_multiuser(self.path))
        target = db.user_db_path(self.path, 5)
        self.assertTrue(target.is_file())
        user = db.connect_user(5, self.path)
        self.addCleanup(user.close)
        self.assertEqual(self.counts(user), {t: n for t, n in zip(db.USER_TABLES, (2, 3, 1, 1, 2, 1, 1))})
        again = multiuser.adopt_data(self.path, 5)
        self.assertTrue(again["already"])
        self.assertEqual(again["counts"]["collection"], 2)
        self.assertNotIn("user_file_missing", again)

    def test_interrupt_before_the_drop_removes_the_partial_user_file(self):
        self.build().close()
        with mock.patch.object(multiuser, "_drop_user_tables", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                multiuser.adopt_data(self.path, 5)
        self.assertFalse(db.is_multiuser(self.path))
        self.assertFalse(db.user_db_path(self.path, 5).exists())
        self.assertTrue(multiuser.adopt_data(self.path, 5)["counts"]["collection"] == 2)

    def test_already_reports_counts_from_the_user_file(self):
        self.build().close()
        first = multiuser.adopt_data(self.path, 5)
        self.assertEqual(multiuser.adopt_data(self.path, 5)["counts"], first["counts"])

    def test_already_flags_a_missing_user_file(self):
        self.build().close()
        multiuser.adopt_data(self.path, 5)
        db.user_db_path(self.path, 5).unlink()
        again = multiuser.adopt_data(self.path, 5)
        self.assertTrue(again["already"])
        self.assertTrue(again["user_file_missing"])
        self.assertEqual(again["counts"], {})

    def test_shared_file_is_write_locked_during_the_copy(self):
        self.build().close()
        real_copy = multiuser._copy_user_tables
        outcome = {}

        def copy_while_another_writer_tries(path, user_id):
            other = sqlite3.connect(path, timeout=0.2)
            try:
                other.execute("UPDATE copies SET note = 'edited during copy' WHERE id = 4")
                other.commit()
                outcome["wrote"] = True
            except sqlite3.OperationalError as exc:
                outcome["error"] = str(exc)
            finally:
                other.close()
            return real_copy(path, user_id)

        with mock.patch.object(multiuser, "_copy_user_tables", copy_while_another_writer_tries):
            multiuser.adopt_data(self.path, 5)
        self.assertNotIn("wrote", outcome)
        self.assertIn("locked", outcome["error"])
        user = db.connect_user(5, self.path)
        self.addCleanup(user.close)
        self.assertEqual(user.execute("SELECT note FROM copies WHERE id = 4").fetchone()[0], "Big Bertha")

    def test_the_lock_is_released_after_a_failure(self):
        self.build().close()
        with mock.patch.object(multiuser, "_mismatches", return_value=["collection"]):
            with self.assertRaises(ValueError):
                multiuser.adopt_data(self.path, 5)
        other = sqlite3.connect(self.path, timeout=0.2)
        self.addCleanup(other.close)
        with other:
            other.execute("UPDATE copies SET note = 'after' WHERE id = 4")

    def test_an_edit_made_before_adopt_is_in_the_user_file(self):
        conn = self.build()
        with conn:
            conn.execute("UPDATE copies SET note = 'renamed' WHERE id = 4")
        conn.close()
        multiuser.adopt_data(self.path, 5)
        user = db.connect_user(5, self.path)
        self.addCleanup(user.close)
        self.assertEqual(user.execute("SELECT note FROM copies WHERE id = 4").fetchone()[0], "renamed")

    def test_the_default_path_picks_up_the_pre_rename_database(self):
        legacy = Path(self._tmp.name) / "battletech.db"
        conn = sqlite3.connect(legacy)
        conn.executescript(db.SCHEMA)
        conn.execute("INSERT INTO collection (unit_uuid, qty) VALUES ('u-atlas', 1)")
        conn.commit()
        conn.close()
        with mock.patch.multiple(
            config, DB_IS_DEFAULT=True, DB_PATH=self.path, LEGACY_DB_PATH=legacy
        ):
            result = multiuser.adopt_data(self.path, 5)
        self.assertEqual(result["counts"]["collection"], 1)
        self.assertTrue(legacy.is_file())

    def test_a_user_file_adopt_did_not_create_is_never_touched(self):
        self.build().close()
        live = db.connect_user(5, self.path)
        with live:
            live.execute("INSERT INTO collection (unit_uuid, qty) VALUES ('u-condor', 3)")
        live.close()
        target = db.user_db_path(self.path, 5)
        before = sqlite3.connect(target).execute("SELECT * FROM collection").fetchall()
        with self.assertRaises(ValueError) as caught:
            multiuser.adopt_data(self.path, 5)
        self.assertIn(str(target), str(caught.exception))
        after = sqlite3.connect(target).execute("SELECT * FROM collection").fetchall()
        self.assertEqual(after, before)
        self.assertFalse(db.is_multiuser(self.path))
        self.assertEqual(list(self.path.parent.glob("*.pre-multiuser*")), [])
        conn = db.connect(self.path)
        self.addCleanup(conn.close)
        self.assertEqual(self.counts(conn)["collection"], 2)

    def test_a_marked_leftover_from_a_killed_run_is_replaced(self):
        self.build().close()
        multiuser._copy_user_tables(self.path, 5)  # a run killed after the user file committed
        leftover = sqlite3.connect(db.user_db_path(self.path, 5))
        with leftover:
            leftover.execute("INSERT INTO collection (unit_uuid) VALUES ('u-junk')")
        leftover.close()
        result = multiuser.adopt_data(self.path, 5)
        self.assertEqual(result["counts"]["collection"], 2)
        user = db.connect_user(5, self.path)
        self.addCleanup(user.close)
        self.assertEqual(user.execute("SELECT COUNT(*) FROM collection WHERE unit_uuid = 'u-junk'").fetchone()[0], 0)

    KILL_AFTER_INSERTS = (
        "import os, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from batchall import multiuser\n"
        "multiuser._mismatches = lambda source, copied: os._exit(9)\n"
        "multiuser.adopt_data(sys.argv[2], 5)\n"
    )

    def kill_mid_copy(self):
        """Run adopt in a child that dies inside the user-file transaction, leaving a hot journal."""
        conn = self.build()
        with conn:  # one large photo, so the copy spills to the file and a journal is written
            conn.execute("UPDATE mini_photos SET data = zeroblob(20000000)")
        conn.close()
        root = str(Path(__file__).resolve().parent.parent)
        done = subprocess.run(
            [sys.executable, "-I", "-c", self.KILL_AFTER_INSERTS, root, str(self.path)],
            capture_output=True,
        )
        self.assertEqual(done.returncode, 9, done.stderr.decode())
        target = db.user_db_path(self.path, 5)
        journal = Path(str(target) + "-journal")
        self.assertTrue(target.is_file())
        self.assertTrue(journal.is_file(), "the child left no hot journal, so the setup proves nothing")
        return target, journal

    def test_rerun_after_a_hard_kill_during_the_copy_succeeds(self):
        self.kill_mid_copy()
        self.assertFalse(db.is_multiuser(self.path))
        result = multiuser.adopt_data(self.path, 5)
        self.assertFalse(result["already"])
        self.assertEqual(result["counts"], {t: n for t, n in zip(db.USER_TABLES, (2, 3, 1, 1, 2, 1, 1))})
        user = db.connect_user(5, self.path)
        self.addCleanup(user.close)
        self.assertEqual(self.counts(user), result["counts"])
        self.assertEqual(len(user.execute("SELECT data FROM mini_photos").fetchone()[0]), 20000000)

    def test_the_marker_is_read_from_the_header_even_with_a_hot_journal(self):
        target, journal = self.kill_mid_copy()
        self.assertEqual(multiuser._read_marker(target), multiuser.ADOPT_MARKER)
        plain = Path(self._tmp.name) / "plain.db"
        conn = sqlite3.connect(plain)
        conn.execute("CREATE TABLE t (x)")
        conn.commit()
        conn.close()
        self.assertEqual(multiuser._read_marker(plain), 0)
        short = Path(self._tmp.name) / "short.db"
        short.write_bytes(b"SQLite format 3\x00")
        self.assertIsNone(multiuser._read_marker(short))
        other = Path(self._tmp.name) / "other.db"
        other.write_bytes(b"x" * 200)
        self.assertIsNone(multiuser._read_marker(other))
        self.assertIsNone(multiuser._read_marker(Path(self._tmp.name) / "missing.db"))

    def test_the_refusal_has_its_own_exception_type(self):
        self.build().close()
        live = db.connect_user(5, self.path)
        live.close()
        with self.assertRaises(multiuser.UserFileInTheWay):
            multiuser.adopt_data(self.path, 5)

    def test_the_finished_user_file_keeps_working(self):
        self.build().close()
        multiuser.adopt_data(self.path, 5)
        user = db.connect_user(5, self.path)
        self.addCleanup(user.close)
        with user:
            user.execute("INSERT INTO collection (unit_uuid, qty) VALUES ('u-marauder', 1)")
        self.assertEqual(user.execute("SELECT COUNT(*) FROM collection").fetchone()[0], 3)
        self.assertEqual(multiuser.adopt_data(self.path, 5)["counts"]["collection"], 3)

    def test_interrupt_inside_the_drop_transaction_propagates_unchanged(self):
        conn = self.build()
        with conn:
            for i in range(30):
                conn.execute(
                    "INSERT INTO mini_photos (copy_id, content_type, data, width, height) "
                    "SELECT id, 'image/jpeg', zeroblob(300000), 1, 1 FROM copies WHERE id = 8 AND ? = 0",
                    (i,),
                )
            conn.execute("UPDATE mini_photos SET data = zeroblob(3000000) WHERE copy_id = 8")
        conn.close()

        def drop_then_interrupt(lock, counts):
            lock.execute("PRAGMA cache_size = 1")
            for table in reversed(db.USER_TABLES):
                lock.execute(f'DROP TABLE "{table}"')
            raise KeyboardInterrupt

        with mock.patch.object(multiuser, "_drop_user_tables", drop_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                multiuser.adopt_data(self.path, 5)
        self.assertFalse(db.is_multiuser(self.path))
        self.assertFalse(db.user_db_path(self.path, 5).exists())
        check = db.connect(self.path)
        self.addCleanup(check.close)
        self.assertEqual(self.counts(check)["collection"], 2)

    def test_unadopted_does_not_claim_safety_when_the_file_is_locked(self):
        self.build().close()
        self.assertTrue(multiuser._unadopted(self.path))
        holder = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("BEGIN EXCLUSIVE")
        self.assertFalse(multiuser._unadopted(self.path, timeout=0.2))
        holder.execute("ROLLBACK")
        self.assertTrue(multiuser._unadopted(self.path))

    def test_finished_adoption_never_deletes_the_user_file(self):
        self.build().close()
        multiuser.adopt_data(self.path, 5)
        target = db.user_db_path(self.path, 5)
        size = target.stat().st_size
        multiuser.adopt_data(self.path, 5)
        self.assertEqual(target.stat().st_size, size)


if __name__ == "__main__":
    unittest.main()
