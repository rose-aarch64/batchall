import os
import unicodedata
from io import StringIO
from unittest import mock

from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.test import TestCase

from hosted import accounts

User = get_user_model()
PASSWORD = "correct horse battery"
NFC = unicodedata.normalize("NFC", "pässwörd-éééé")
NFD = unicodedata.normalize("NFD", NFC)


class CommandResolutionTests(TestCase):
    def test_hosted_provides_the_password_commands(self):
        commands = get_commands()
        self.assertEqual(commands["changepassword"], "hosted")
        self.assertEqual(commands["createsuperuser"], "hosted")


class CreateSuperuserTests(TestCase):
    def run_noinput(self, password, handle="root"):
        env = {"DJANGO_SUPERUSER_PASSWORD": password}
        with mock.patch.dict(os.environ, env):
            call_command(
                "createsuperuser", interactive=False, username=handle, email="", stdout=StringIO()
            )

    def test_the_decomposed_form_authenticates_in_composed_form(self):
        self.assertNotEqual(NFC, NFD)
        self.run_noinput(NFD)
        user = authenticate(username="root", password=NFC)
        self.assertIsNotNone(user)
        self.assertTrue(user.is_superuser)

    def test_a_300_character_password_is_refused(self):
        with self.assertRaises(CommandError):
            self.run_noinput("a" * 300)
        self.assertFalse(User.objects.filter(username="root").exists())

    def test_a_short_password_is_refused(self):
        with self.assertRaises(CommandError):
            self.run_noinput("short")
        self.assertFalse(User.objects.filter(username="root").exists())

    def test_the_environment_is_restored(self):
        with mock.patch.dict(os.environ, {"DJANGO_SUPERUSER_PASSWORD": NFD}):
            call_command(
                "createsuperuser", interactive=False, username="root", email="", stdout=StringIO()
            )
            self.assertEqual(os.environ["DJANGO_SUPERUSER_PASSWORD"], NFD)

    def test_interactive_prompts_are_normalized(self):
        typed = iter([NFD, NFD])
        with mock.patch("getpass.getpass", side_effect=lambda *a, **k: next(typed)):
            with mock.patch("sys.stdin") as stdin:
                stdin.isatty.return_value = True
                call_command(
                    "createsuperuser", interactive=True, username="root", email="a@example.com",
                    stdin=stdin, stdout=StringIO(),
                )
        self.assertIsNotNone(authenticate(username="root", password=NFC))

    def test_interactive_prompt_refuses_a_300_character_password(self):
        with mock.patch("getpass.getpass", return_value="a" * 300):
            with mock.patch("sys.stdin") as stdin:
                stdin.isatty.return_value = True
                with self.assertRaises(CommandError):
                    call_command(
                        "createsuperuser", interactive=True, username="root", email="a@example.com",
                        stdin=stdin, stdout=StringIO(),
                    )
        self.assertFalse(User.objects.filter(username="root").exists())


class ChangePasswordTests(TestCase):
    def setUp(self):
        accounts.create_user("rose", PASSWORD)

    def change(self, typed):
        answers = iter(typed)
        with mock.patch("getpass.getpass", side_effect=lambda *a, **k: next(answers)):
            call_command("changepassword", "rose", stdout=StringIO(), stderr=StringIO())

    def test_the_decomposed_form_authenticates_in_composed_form(self):
        self.change([NFD, NFD])
        self.assertIsNotNone(authenticate(username="rose", password=NFC))

    def test_a_300_character_password_is_refused(self):
        with self.assertRaises(CommandError):
            self.change(["a" * 300, "a" * 300])
        self.assertIsNotNone(authenticate(username="rose", password=PASSWORD))
