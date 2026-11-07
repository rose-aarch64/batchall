import json
import os
import sqlite3
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from support import seed_catalog  # noqa: E402

from batchall import config, db, multiuser  # noqa: E402
from hosted import accounts  # noqa: E402

User = get_user_model()
PASSWORD = "correct horse battery"
ENV = {"ADOPT_TEST_PW": PASSWORD}


class AdoptCommandTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "batchall.db"
        override = override_settings(BATCHALL_SHARED_DB=str(self.path))
        override.enable()
        self.addCleanup(override.disable)
        conn = db.connect(self.path)
        seed_catalog(conn)
        with conn:
            conn.execute("INSERT INTO collection (unit_uuid, qty) VALUES ('u-atlas', 2)")
            conn.execute("INSERT INTO collection (unit_uuid, qty) VALUES ('u-locust', 1)")
        conn.close()

    def run_adopt(self, handle="Rose", **extra):
        out = StringIO()
        with mock.patch.dict(os.environ, ENV):
            call_command("adopt", handle=handle, password_env="ADOPT_TEST_PW", stdout=out, **extra)
        return out.getvalue()

    def test_adopt_creates_a_superuser_and_moves_the_data(self):
        output = self.run_adopt()
        user = User.objects.get(username="rose")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.check_password(PASSWORD))
        result = json.loads(output)
        self.assertEqual(result["user_id"], user.id)
        self.assertEqual(result["counts"]["collection"], 2)
        conn = db.connect_user(user.id, self.path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0], 2)
        self.assertTrue(db.is_multiuser(self.path))

    def test_adopt_refuses_a_reserved_handle_and_changes_nothing(self):
        with self.assertRaises(CommandError):
            self.run_adopt(handle="admin")
        self.assertEqual(User.objects.count(), 0)
        self.assertFalse(db.is_multiuser(self.path))

    def test_adopt_refuses_a_short_password_and_changes_nothing(self):
        out = StringIO()
        with mock.patch.dict(os.environ, {"ADOPT_TEST_PW": "short"}):
            with self.assertRaises(CommandError):
                call_command("adopt", handle="rose", password_env="ADOPT_TEST_PW", stdout=out)
        self.assertEqual(User.objects.count(), 0)
        self.assertFalse(db.is_multiuser(self.path))

    def test_adopt_failure_removes_the_new_user(self):
        with mock.patch.object(multiuser, "adopt_data", side_effect=ValueError("boom")):
            with self.assertRaises(CommandError):
                self.run_adopt()
        self.assertEqual(User.objects.count(), 0)
        self.assertFalse(db.is_multiuser(self.path))

    def test_adopt_twice(self):
        self.run_adopt()
        again = self.run_adopt(handle="someone")
        self.assertIn("already adopted", again)
        self.assertEqual(User.objects.count(), 1)

    def test_interrupt_leaves_no_user_and_a_rerun_succeeds(self):
        with mock.patch.object(multiuser, "adopt_data", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_adopt()
        self.assertEqual(User.objects.count(), 0)
        self.run_adopt()
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(db.is_multiuser(self.path))

    def test_interrupt_after_the_drop_commit_keeps_the_user_and_the_data(self):
        real_drop = multiuser._drop_user_tables

        def drop_then_interrupt(*args):
            real_drop(*args)
            raise KeyboardInterrupt

        with mock.patch.object(multiuser, "_drop_user_tables", drop_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_adopt()
        user = User.objects.get(username="rose")
        self.assertTrue(db.is_multiuser(self.path))
        conn = db.connect_user(user.id, self.path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0], 2)

    def test_adopt_resumes_with_a_superuser_left_by_a_killed_run(self):
        left = accounts.create_user("rose", PASSWORD, superuser=True)
        self.run_adopt()
        self.assertEqual(User.objects.count(), 1)
        conn = db.connect_user(left.id, self.path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0], 2)

    def test_adopt_refuses_an_existing_handle_with_another_password(self):
        accounts.create_user("rose", "some other passphrase", superuser=True)
        with self.assertRaises(CommandError):
            self.run_adopt()
        self.assertFalse(db.is_multiuser(self.path))

    def test_adopt_refuses_an_existing_ordinary_user(self):
        accounts.create_user("rose", PASSWORD)
        with self.assertRaises(CommandError):
            self.run_adopt()
        self.assertFalse(db.is_multiuser(self.path))

    def test_a_failed_resume_keeps_the_existing_superuser(self):
        accounts.create_user("rose", PASSWORD, superuser=True)
        with mock.patch.object(multiuser, "adopt_data", side_effect=ValueError("boom")):
            with self.assertRaises(CommandError):
                self.run_adopt()
        self.assertEqual(User.objects.count(), 1)

    def test_a_reused_account_with_a_live_user_file_keeps_it(self):
        left = accounts.create_user("rose", PASSWORD, superuser=True)
        live = db.connect_user(left.id, self.path)
        with live:
            live.execute("INSERT INTO collection (unit_uuid, qty) VALUES ('u-condor', 3)")
        live.close()
        with self.assertRaises(CommandError):
            self.run_adopt()
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(db.is_multiuser(self.path))
        check = sqlite3.connect(db.user_db_path(self.path, left.id))
        self.addCleanup(check.close)
        self.assertEqual(check.execute("SELECT unit_uuid FROM collection").fetchall(), [("u-condor",)])

    def test_an_interrupt_while_the_database_is_locked_keeps_the_account(self):
        holder = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(holder.close)

        def interrupt_while_locked(path, user_id):
            holder.execute("BEGIN EXCLUSIVE")
            raise KeyboardInterrupt

        with mock.patch.object(multiuser, "LOCK_WAIT", 0.2), mock.patch.object(
            multiuser, "adopt_data", interrupt_while_locked
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.run_adopt()
        self.assertEqual(User.objects.count(), 1)

    def test_adopt_refuses_a_missing_database_and_creates_nothing(self):
        missing = Path(self._tmp.name) / "typo" / "batchall.db"
        with override_settings(BATCHALL_SHARED_DB=str(missing)):
            with self.assertRaises(CommandError) as caught:
                self.run_adopt()
        self.assertIn(str(missing), str(caught.exception))
        self.assertEqual(User.objects.count(), 0)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_adopt_accepts_a_missing_default_file_when_the_old_one_exists(self):
        fresh = Path(self._tmp.name) / "fresh"
        fresh.mkdir()
        target = fresh / "batchall.db"
        legacy = fresh / "battletech.db"
        self.path.replace(legacy)
        with override_settings(BATCHALL_SHARED_DB=str(target)), mock.patch.multiple(
            config, DB_IS_DEFAULT=True, DB_PATH=target, LEGACY_DB_PATH=legacy
        ):
            result = json.loads(self.run_adopt())
        self.assertEqual(result["counts"]["collection"], 2)

    def test_adopt_prompts_twice_without_an_environment_variable(self):
        out = StringIO()
        with mock.patch("getpass.getpass", side_effect=[PASSWORD, PASSWORD]) as prompt:
            call_command("adopt", handle="rose", stdout=out)
        self.assertEqual(prompt.call_count, 2)
        self.assertTrue(User.objects.get(username="rose").check_password(PASSWORD))

    def test_createinvite_prints_a_code_that_signs_up(self):
        out = StringIO()
        call_command("createinvite", stdout=out)
        code = json.loads(out.getvalue())["invite"]
        client = Client(enforce_csrf_checks=False)
        response = client.post(
            "/api/auth/signup",
            data=json.dumps({"handle": "newbie", "password": PASSWORD, "invite_code": code}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(User.objects.filter(username="newbie").exists())
