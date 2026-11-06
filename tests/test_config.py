"""Settings use BATCHALL_* names and still accept the BT_* ones. The old database file is adopted by copy."""

import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (puts the project root on sys.path)

from batchall import config, db

SETTINGS = ("BATCHALL_DB", "BT_DB", "BATCHALL_DATA_DIR", "BT_DATA_DIR")


def load_config(**env):
    """Reload config as if only these settings were in the environment."""
    with mock.patch.dict(os.environ):
        for name in SETTINGS:
            os.environ.pop(name, None)
        os.environ.update(env)
        return importlib.reload(config)


class SettingTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(importlib.reload, config)  # put the real environment back for the other tests

    def test_defaults_use_the_new_database_name_and_count_as_default(self):
        cfg = load_config()
        self.assertEqual(cfg.DB_PATH.name, "batchall.db")
        self.assertEqual(cfg.LEGACY_DB_PATH.name, "battletech.db")
        self.assertEqual(cfg.DB_PATH.parent, cfg.DATA_DIR)
        self.assertTrue(cfg.DB_IS_DEFAULT)

    def test_new_database_setting_is_used_and_is_not_the_default(self):
        cfg = load_config(BATCHALL_DB="/somewhere/mine.db")
        self.assertEqual(cfg.DB_PATH, Path("/somewhere/mine.db"))
        self.assertFalse(cfg.DB_IS_DEFAULT)

    def test_old_database_setting_still_works(self):
        cfg = load_config(BT_DB="/somewhere/old.db")
        self.assertEqual(cfg.DB_PATH, Path("/somewhere/old.db"))
        self.assertFalse(cfg.DB_IS_DEFAULT)

    def test_new_database_setting_wins_over_the_old_one(self):
        cfg = load_config(BATCHALL_DB="/new.db", BT_DB="/old.db")
        self.assertEqual(cfg.DB_PATH, Path("/new.db"))

    def test_data_dir_settings_move_every_path(self):
        cfg = load_config(BATCHALL_DATA_DIR="/data/new")
        self.assertEqual(cfg.DATA_DIR, Path("/data/new"))
        self.assertEqual(cfg.DB_PATH, Path("/data/new/batchall.db"))
        self.assertEqual(cfg.ART_DIR, Path("/data/new/art"))
        self.assertEqual(load_config(BT_DATA_DIR="/data/old").DATA_DIR, Path("/data/old"))
        self.assertEqual(load_config(BATCHALL_DATA_DIR="/n", BT_DATA_DIR="/o").DATA_DIR, Path("/n"))

    def test_user_agent_uses_the_new_name(self):
        self.assertTrue(load_config().USER_AGENT.startswith("batchall/"))


def make_legacy(path, note="from the old file"):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('note', ?)", (note,))
    conn.commit()
    conn.close()


def read_note(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT value FROM meta WHERE key = 'note'").fetchone()[0]
    finally:
        conn.close()


class AdoptLegacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.legacy = Path(self.tmp.name) / "battletech.db"
        self.target = Path(self.tmp.name) / "batchall.db"

    def test_copies_the_old_database_when_the_new_one_is_missing(self):
        make_legacy(self.legacy)
        self.assertTrue(db.adopt_legacy_database(self.target, self.legacy))
        self.assertEqual(read_note(self.target), "from the old file")

    def test_the_old_file_is_left_exactly_as_it_was(self):
        make_legacy(self.legacy)
        before = self.legacy.read_bytes()
        db.adopt_legacy_database(self.target, self.legacy)
        self.assertEqual(self.legacy.read_bytes(), before)

    def test_does_nothing_when_the_new_database_already_exists(self):
        make_legacy(self.legacy, "old")
        make_legacy(self.target, "new")
        self.assertFalse(db.adopt_legacy_database(self.target, self.legacy))
        self.assertEqual(read_note(self.target), "new")

    def test_does_nothing_without_an_old_database(self):
        self.assertFalse(db.adopt_legacy_database(self.target, self.legacy))
        self.assertFalse(self.target.exists())

    def test_a_second_call_does_not_copy_again(self):
        make_legacy(self.legacy)
        db.adopt_legacy_database(self.target, self.legacy)
        make_legacy_changed = sqlite3.connect(self.legacy)
        make_legacy_changed.execute("UPDATE meta SET value = 'changed later'")
        make_legacy_changed.commit()
        make_legacy_changed.close()
        self.assertFalse(db.adopt_legacy_database(self.target, self.legacy))
        self.assertEqual(read_note(self.target), "from the old file")

    def test_no_partial_file_is_left_behind(self):
        make_legacy(self.legacy)
        db.adopt_legacy_database(self.target, self.legacy)
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), ["batchall.db", "battletech.db"])


class ConnectAdoptsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.legacy = Path(self.tmp.name) / "battletech.db"
        self.target = Path(self.tmp.name) / "batchall.db"
        make_legacy(self.legacy)

    def patched(self, is_default=True, db_path=None):
        stack = [
            mock.patch.object(config, "DB_PATH", db_path or self.target),
            mock.patch.object(config, "LEGACY_DB_PATH", self.legacy),
            mock.patch.object(config, "DB_IS_DEFAULT", is_default),
        ]
        for p in stack:
            p.start()
            self.addCleanup(p.stop)

    def test_opening_the_default_database_adopts_the_old_one_first(self):
        self.patched()
        db.connect().close()
        self.assertEqual(read_note(self.target), "from the old file")

    def test_an_explicit_path_is_never_replaced_by_the_old_database(self):
        self.patched()
        explicit = Path(self.tmp.name) / "elsewhere.db"
        db.connect(explicit).close()
        self.assertFalse(self.target.exists())
        self.assertNotIn("note", [r[0] for r in sqlite3.connect(explicit).execute("SELECT key FROM meta")])

    def test_a_database_chosen_by_a_setting_is_never_replaced_by_the_old_one(self):
        chosen = Path(self.tmp.name) / "chosen.db"
        self.patched(is_default=False, db_path=chosen)
        db.connect().close()
        self.assertTrue(chosen.exists())
        self.assertNotIn("note", [r[0] for r in sqlite3.connect(chosen).execute("SELECT key FROM meta")])
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
