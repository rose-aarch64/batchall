import json
import os
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

from batchall import db, multiuser  # noqa: E402

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
