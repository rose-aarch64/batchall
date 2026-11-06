import threading
import time
import unicodedata

from django.contrib import admin as django_admin
from django.contrib.auth import authenticate, get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase, TransactionTestCase

from hosted import accounts
from hosted.accounts import AccountError
from hosted.models import Invite

User = get_user_model()
PASSWORD = "correct horse battery"


class HandleAndPasswordTests(TestCase):
    def test_handle_rules(self):
        self.assertEqual(accounts.normalize_handle("Rose"), "rose")
        self.assertEqual(accounts.normalize_handle("rose\n"), "rose")
        for bad in ["ab", "a" * 31, "has space", "under_score", "../x"]:
            with self.subTest(bad=bad), self.assertRaises(AccountError):
                accounts.normalize_handle(bad)
        for reserved in ["api", "admin", "login", "u"]:
            with self.subTest(reserved=reserved), self.assertRaises(AccountError):
                accounts.normalize_handle(reserved)

    def test_handles_are_unique_ignoring_case(self):
        accounts.create_user("rose", PASSWORD)
        with self.assertRaises(AccountError):
            accounts.create_user("ROSE", PASSWORD)

    def test_handle_taken_by_a_user_created_another_way(self):
        User.objects.create_user("Rose", password=PASSWORD)
        with self.assertRaises(AccountError):
            accounts.create_user("rose", PASSWORD)

    def test_password_rules(self):
        with self.assertRaises(AccountError):
            accounts.check_password("a" * 9)
        self.assertEqual(accounts.check_password("a" * 10), "a" * 10)
        with self.assertRaises(AccountError):
            accounts.check_password("a" * 257)
        start = time.monotonic()
        with self.assertRaises(AccountError):
            accounts.check_password("a" * 100_000)
        self.assertLess(time.monotonic() - start, 1.0)
        with self.assertRaises(AccountError):
            accounts.check_password("\ud800" * 12)

        plain = "pässwörd-ünï"
        self.assertEqual(len(plain), 12)
        self.assertEqual(accounts.check_password(plain), plain)

        composed = unicodedata.normalize("NFC", "pässwörd-ünï")
        decomposed = unicodedata.normalize("NFD", composed)
        self.assertNotEqual(composed, decomposed)
        accounts.create_user("rose", composed)
        self.assertIsNotNone(authenticate(username="rose", password=accounts.check_password(composed)))
        self.assertIsNotNone(authenticate(username="rose", password=accounts.check_password(decomposed)))

    def test_users_get_scrypt_hashes_and_no_password_in_output(self):
        user = accounts.create_user("rose", PASSWORD)
        self.assertTrue(user.password.startswith("scrypt$"))
        self.assertNotIn(PASSWORD, user.password)
        self.assertEqual(user.first_name, "rose")

    def test_display_name_limit(self):
        with self.assertRaises(AccountError):
            accounts.create_user("rose", PASSWORD, display_name="x" * 61)


class SignUpTests(TestCase):
    def test_sign_up_requires_a_valid_unused_invite(self):
        code = accounts.make_invite()
        for bad in [None, "", "wrong", "\ud800"]:
            with self.subTest(bad=bad):
                with self.assertRaises(AccountError) as ctx:
                    accounts.sign_up("rose", PASSWORD, bad)
                self.assertEqual(str(ctx.exception), "invalid invite code")
        user = accounts.sign_up("rose", PASSWORD, code)
        self.assertEqual(Invite.objects.get().used_by, user)
        with self.assertRaises(AccountError) as ctx:
            accounts.sign_up("other", PASSWORD, code)
        self.assertEqual(str(ctx.exception), "invalid invite code")
        self.assertFalse(User.objects.filter(username="other").exists())

    def test_failed_sign_up_leaves_the_invite_unused(self):
        accounts.create_user("rose", PASSWORD)
        code = accounts.make_invite()
        with self.assertRaises(AccountError):
            accounts.sign_up("Rose", PASSWORD, code)
        self.assertIsNone(Invite.objects.get().used_by)

    def test_invite_code_is_not_stored(self):
        code = accounts.make_invite()
        self.assertNotIn(code, Invite.objects.get().code_hash)
        self.assertEqual(len(Invite.objects.get().code_hash), 64)


class SignUpRaceTests(TransactionTestCase):
    def test_two_sign_ups_race_for_one_invite(self):
        code = accounts.make_invite()
        results = {}
        barrier = threading.Barrier(2)

        def attempt(handle):
            try:
                barrier.wait(timeout=10)
                accounts.sign_up(handle, PASSWORD, code)
                results[handle] = "ok"
            except AccountError as exc:
                results[handle] = str(exc)
            except Exception as exc:  # noqa: BLE001
                results[handle] = f"error: {exc!r}"
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt, args=(h,)) for h in ("alpha", "bravo")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(sorted(results.values()), ["invalid invite code", "ok"], results)
        self.assertEqual(User.objects.count(), 1)
        self.assertIsNotNone(Invite.objects.get().used_by)


class AdminTests(TestCase):
    def test_admin_create_form_normalizes_and_refuses_reserved(self):
        from hosted.admin import HandleUserCreationForm

        form = HandleUserCreationForm(
            {"username": "Rose", "password1": PASSWORD, "password2": PASSWORD}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().username, "rose")

        bad = HandleUserCreationForm(
            {"username": "admin", "password1": PASSWORD, "password2": PASSWORD}
        )
        self.assertFalse(bad.is_valid())

    def _create_form(self, password):
        from hosted.admin import HandleUserCreationForm

        return HandleUserCreationForm(
            {"username": "rose", "password1": password, "password2": password}
        )

    def _change_form(self, password):
        from hosted.admin import HandlePasswordChangeForm

        user, _ = User.objects.get_or_create(username="target")
        user.set_password(PASSWORD)
        user.save()
        return user, HandlePasswordChangeForm(
            user, {"password1": password, "password2": password}
        )

    def test_admin_forms_refuse_oversized_and_unencodable_passwords(self):
        for name, build in (
            ("create", lambda p: self._create_form(p)),
            ("change", lambda p: self._change_form(p)[1]),
        ):
            for bad in ("a" * 100_000, "\ud800" * 12):
                with self.subTest(form=name, length=len(bad)):
                    form = build(bad)
                    start = time.monotonic()
                    self.assertFalse(form.is_valid())
                    self.assertLess(time.monotonic() - start, 1.0)
                    self.assertTrue(form.errors)

    def test_admin_forms_store_the_nfc_password(self):
        composed = unicodedata.normalize("NFC", "pässwörd-ünï")
        decomposed = unicodedata.normalize("NFD", composed)
        form = self._create_form(decomposed)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertIsNotNone(authenticate(username="rose", password=composed))

        user, change = self._change_form(decomposed)
        self.assertTrue(change.is_valid(), change.errors)
        change.save()
        user.refresh_from_db()
        self.assertTrue(user.check_password(composed))

    def test_user_admin_uses_the_handle_forms(self):
        from hosted.admin import HandlePasswordChangeForm

        self.assertIs(
            django_admin.site._registry[User].change_password_form, HandlePasswordChangeForm
        )

    def test_user_admin_uses_the_handle_form(self):
        from hosted.admin import HandleUserCreationForm

        self.assertIs(django_admin.site._registry[User].add_form, HandleUserCreationForm)

    def test_invite_admin_never_shows_the_hash(self):
        model_admin = django_admin.site._registry[Invite]
        request = RequestFactory().get("/")
        request.user = User.objects.create_superuser("boss", password=PASSWORD)
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertTrue(model_admin.has_delete_permission(request))
        shown = list(model_admin.get_list_display(request))
        shown += list(model_admin.get_fields(request))
        self.assertNotIn("code_hash", shown)
        self.assertEqual(set(model_admin.get_fields(request)) & {"code_hash"}, set())
