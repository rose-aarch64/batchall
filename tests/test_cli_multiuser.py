import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support

from batchall import accountsdb, db, multiuser

ROOT = Path(__file__).resolve().parent.parent


def make_accounts(path, users):
    """Create the minimal auth_user table. users is a list of (id, username, is_active, is_superuser)."""
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "CREATE TABLE auth_user (id INTEGER PRIMARY KEY, username TEXT, is_active INTEGER, is_superuser INTEGER)"
        )
        conn.executemany("INSERT INTO auth_user VALUES (?,?,?,?)", users)
    conn.close()


class CliMultiuserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "batchall.db")
        self.accounts = str(Path(self.tmp.name) / "accounts.db")
        conn = db.connect(self.path)
        support.seed_catalog(conn)
        with conn:
            conn.execute("INSERT INTO collection (id, unit_uuid, qty, painted) VALUES (1, 'u-atlas', 1, 1)")
            conn.execute("INSERT INTO collection (id, unit_uuid, qty) VALUES (2, 'u-locust', 1)")
            conn.execute("INSERT INTO collection (id, unit_uuid, qty) VALUES (3, 'u-wolfhound', 1)")
            conn.execute("INSERT INTO collection (id, unit_uuid, qty) VALUES (4, 'u-shadow', 1)")
            conn.execute("INSERT INTO copies (collection_id, note, painted) VALUES (1, '', 1)")
            for cid in (2, 3, 4):
                conn.execute("INSERT INTO copies (collection_id, note, painted) VALUES (?, '', 0)", (cid,))
        conn.close()

    def run_cli(self, *args, with_accounts=True):
        env = dict(os.environ, BATCHALL_DB=self.path, BATCHALL_DATA_DIR=self.tmp.name)
        env["BATCHALL_ACCOUNTS_DB"] = self.accounts
        return subprocess.run(
            [sys.executable, "-m", "batchall", *args],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )

    def adopt(self, users=((1, "rose", 1, 1),)):
        multiuser.adopt_data(self.path, 1)
        make_accounts(self.accounts, list(users))

    def listing(self, *extra):
        result = self.run_cli(*extra, "collection", "list")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_collection_list_output_is_identical_after_adopt(self):
        before = self.listing()
        self.adopt()
        after = self.listing()
        self.assertEqual(before, after)
        self.assertTrue(before)

    def test_build_works_against_a_multiuser_database(self):
        args = ("build", "--target", "4600", "--formation", "lance", "--seed", "1")
        before = self.run_cli(*args)
        self.assertEqual(before.returncode, 0, before.stderr)
        self.adopt()
        after = self.run_cli(*args)
        self.assertEqual(after.returncode, 0, after.stderr)
        units = lambda r: [b["units"] for b in json.loads(r.stdout)["builds"]]  # noqa: E731
        self.assertEqual(units(before), units(after))

    def test_user_flag_selects_the_account(self):
        self.adopt(((1, "rose", 1, 1), (2, "Bob", 1, 0)))
        conn = db.connect_user(2, self.path)
        with conn:
            conn.execute("INSERT INTO collection (id, unit_uuid, qty) VALUES (1, 'u-timber', 1)")
            conn.execute("INSERT INTO copies (collection_id, note) VALUES (1, '')")
        conn.close()
        default = json.dumps(self.listing())
        bob = json.dumps(self.listing("--user", "bob"))
        self.assertIn("Atlas", default)
        self.assertNotIn("Timber", default)
        self.assertIn("Timber", bob)
        self.assertNotIn("Atlas", bob)

    def test_user_flag_on_a_single_user_database_errors(self):
        result = self.run_cli("--user", "bob", "collection", "list")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {"error": "database is not in multi-user mode"})

    def test_unknown_user_and_missing_accounts_file_give_clear_errors(self):
        multiuser.adopt_data(self.path, 1)
        missing = self.run_cli("collection", "list")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("manage.py adopt", json.loads(missing.stdout)["error"])
        make_accounts(self.accounts, [(1, "rose", 1, 1), (2, "gone", 0, 0)])
        for handle in ("nobody", "gone"):
            result = self.run_cli("--user", handle, "collection", "list")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {"error": "no such user"})

    def test_no_administrator_is_an_error(self):
        multiuser.adopt_data(self.path, 1)
        make_accounts(self.accounts, [(1, "bob", 1, 0)])
        result = self.run_cli("collection", "list")
        self.assertEqual(json.loads(result.stdout), {"error": "no administrator account exists"})

    def test_missing_table_gives_the_adopt_advice(self):
        sqlite3.connect(self.accounts).close()
        with self.assertRaises(ValueError) as ctx:
            accountsdb.lookup_user_id(None, self.accounts)
        self.assertIn("manage.py adopt", str(ctx.exception))

    def test_catalog_import_from_dir_works_on_a_multiuser_database(self):
        self.adopt()
        src = Path(self.tmp.name) / "catalog"
        src.mkdir()
        unit = {
            "uuid": "u-new", "mul1id": 99, "chassis": "Newbie", "model": "N-1", "type": "Mek",
            "subtype": "BattleMek Omni", "tons": 50, "weightClass": "Heavy", "bv": 1000,
        }
        (src / "units.json").write_text(json.dumps({"version": 1, "units": [unit]}), encoding="utf-8")
        (src / "factions.json").write_text(json.dumps({"factions": []}), encoding="utf-8")
        (src / "eras.json").write_text(json.dumps({"eras": []}), encoding="utf-8")
        result = self.run_cli("catalog", "import", "--from-dir", str(src))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        shared = sqlite3.connect(self.path)
        try:
            rows = shared.execute("SELECT uuid FROM units").fetchall()
        finally:
            shared.close()
        self.assertEqual(rows, [("u-new",)])

    def test_serve_refuses_a_multiuser_database(self):
        self.adopt()
        result = self.run_cli("serve", "--port", "0")
        self.assertEqual(result.returncode, 1)
        message = json.loads(result.stdout)["error"]
        self.assertIn("manage.py runserver", message)
        self.assertIn("gunicorn hosted.wsgi", message)


if __name__ == "__main__":
    unittest.main()
