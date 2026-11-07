import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from batchall import db
from hosted import accounts, authviews
from hosted.tests.limiters import reset_limiters

User = get_user_model()
PASSWORD = "correct horse battery"
NEW_PASSWORD = "another long passphrase"


class AuthViewTests(TestCase):
    def setUp(self):
        reset_limiters()
        self.addCleanup(reset_limiters)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.shared = Path(self._tmp.name) / "batchall.db"
        override = override_settings(BATCHALL_SHARED_DB=str(self.shared))
        override.enable()
        self.addCleanup(override.disable)

    def new_client(self):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/auth/me")
        return client

    def call(self, client, method, path, body=None, raw=None, **extra):
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        headers.update(extra)
        data = raw if raw is not None else json.dumps(body if body is not None else {})
        return getattr(client, method)(
            path, data=data, content_type="application/json", **headers
        )

    def post(self, client, path, body=None, **extra):
        return self.call(client, "post", path, body, **extra)

    def sign_up(self, client, handle="rose", password=PASSWORD):
        return self.post(client, "/api/auth/signup", {
            "handle": handle,
            "password": password,
            "invite_code": accounts.make_invite(),
        })

    def test_signup_login_logout_round_trip(self):
        client = self.new_client()
        response = self.sign_up(client)
        self.assertEqual(response.status_code, 200)
        cookie = response.cookies["batchall_session"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        me = client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["handle"], "rose")
        self.assertNotIn("password", me.json())
        self.assertEqual(set(me.json()), {"id", "handle", "display_name", "is_admin"})
        self.assertEqual(self.post(client, "/api/auth/logout").status_code, 200)
        self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_post_without_csrf_token_gets_a_json_403(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            "/api/auth/login",
            data=json.dumps({"handle": "rose", "password": PASSWORD}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "CSRF check failed"})
        self.assertIn("application/json", response["Content-Type"])

    def test_login_failures_are_indistinguishable(self):
        accounts.create_user("rose", PASSWORD)
        client = self.new_client()
        unknown = self.post(client, "/api/auth/login", {"handle": "nobody", "password": PASSWORD})
        wrong = self.post(client, "/api/auth/login", {"handle": "rose", "password": "wrong password!"})
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(unknown.status_code, wrong.status_code)
        self.assertEqual(unknown.content, wrong.content)
        self.assertEqual(wrong.json(), {"error": "invalid handle or password"})

    def test_surrogate_and_oversized_passwords_never_500(self):
        client = self.new_client()
        response = self.sign_up(client, "bob", "\ud800" * 12)
        self.assertEqual(response.status_code, 400)
        accounts.create_user("rose", PASSWORD)
        for password in ("\ud800" * 12, "x" * 100_000):
            with self.subTest(length=len(password)):
                response = self.post(
                    client, "/api/auth/login", {"handle": "rose", "password": password}
                )
                self.assertEqual(response.status_code, 401)

    def test_sixth_failure_is_rate_limited(self):
        accounts.create_user("rose", PASSWORD)
        client = self.new_client()
        for _ in range(5):
            response = self.post(client, "/api/auth/login", {"handle": "rose", "password": "wrong password!"})
            self.assertEqual(response.status_code, 401)
        response = self.post(client, "/api/auth/login", {"handle": "rose", "password": PASSWORD})
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)

    def login_from(self, ip, handle="rose", password="wrong password!"):
        client = self.new_client()
        return self.post(
            client, "/api/auth/login", {"handle": handle, "password": password}, REMOTE_ADDR=ip
        )

    def test_five_failures_lock_only_that_handle_and_ip_pair(self):
        accounts.create_user("rose", PASSWORD)
        for _ in range(5):
            self.assertEqual(self.login_from("10.0.0.1").status_code, 401)
        self.assertEqual(self.login_from("10.0.0.1", password=PASSWORD).status_code, 429)
        self.assertEqual(self.login_from("10.0.0.2", password=PASSWORD).status_code, 200)

    def test_thirty_failures_across_addresses_lock_the_handle_for_everyone(self):
        accounts.create_user("rose", PASSWORD)
        for number in range(29):
            self.assertEqual(self.login_from(f"10.1.0.{number}").status_code, 401)
        self.assertEqual(self.login_from("10.1.0.100").status_code, 401)
        response = self.login_from("10.1.0.200", password=PASSWORD)
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)

    def test_login_issues_a_new_session_key(self):
        accounts.create_user("rose", PASSWORD)
        client = self.new_client()
        body = {"handle": "rose", "password": PASSWORD}
        first = self.post(client, "/api/auth/login", body).cookies["batchall_session"].value
        second = self.post(client, "/api/auth/login", body).cookies["batchall_session"].value
        self.assertNotEqual(first, second)

        fixated = self.new_client()
        fixated.cookies["batchall_session"] = "fixatedkey0123456789abcdef01234567"
        response = self.post(fixated, "/api/auth/login", body)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(
            response.cookies["batchall_session"].value, "fixatedkey0123456789abcdef01234567"
        )

    def test_garbage_cookie_gives_401(self):
        client = Client()
        client.cookies["batchall_session"] = "garbage!!"
        response = client.get("/api/auth/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "sign in required"})

    def test_json_errors_for_bad_requests(self):
        client = self.new_client()
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        response = client.post("/api/auth/login", data="a=b",
                               content_type="application/x-www-form-urlencoded", **headers)
        self.assertEqual(response.status_code, 415)
        response = self.call(client, "post", "/api/auth/login", raw="{nope")
        self.assertEqual(response.status_code, 400)
        response = self.call(client, "post", "/api/auth/login", raw="[1]")
        self.assertEqual(response.status_code, 400)
        response = client.get("/api/auth/login")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json(), {"error": "method not allowed"})

    def test_change_password_keeps_this_session_and_ends_others(self):
        accounts.create_user("rose", PASSWORD)
        body = {"handle": "rose", "password": PASSWORD}
        a, b = self.new_client(), self.new_client()
        self.assertEqual(self.post(a, "/api/auth/login", body).status_code, 200)
        self.assertEqual(self.post(b, "/api/auth/login", body).status_code, 200)
        response = self.post(a, "/api/auth/password", {"current": PASSWORD, "new": NEW_PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(a.get("/api/auth/me").status_code, 200)
        self.assertEqual(b.get("/api/auth/me").status_code, 401)
        c = self.new_client()
        self.assertEqual(
            self.post(c, "/api/auth/login", {"handle": "rose", "password": NEW_PASSWORD}).status_code, 200
        )
        d = self.new_client()
        self.assertEqual(self.post(d, "/api/auth/login", body).status_code, 401)

    def test_change_password_checks_current_and_new(self):
        accounts.create_user("rose", PASSWORD)
        client = self.new_client()
        self.post(client, "/api/auth/login", {"handle": "rose", "password": PASSWORD})
        response = self.post(client, "/api/auth/password", {"current": "not it at all", "new": NEW_PASSWORD})
        self.assertEqual(response.status_code, 400)
        response = self.post(client, "/api/auth/password", {"current": PASSWORD, "new": "short"})
        self.assertEqual(response.status_code, 400)

    def test_delete_account_needs_password_and_confirmation(self):
        user = accounts.create_user("rose", PASSWORD)
        db.connect_shared(self.shared).close()
        db.connect_user(user.id, self.shared).close()
        data_file = db.user_db_path(self.shared, user.id)
        self.assertTrue(data_file.exists())
        client = self.new_client()
        self.post(client, "/api/auth/login", {"handle": "rose", "password": PASSWORD})

        response = self.call(client, "delete", "/api/auth/account",
                             {"password": "wrong password!", "confirm": "rose"})
        self.assertEqual(response.status_code, 400)
        response = self.call(client, "delete", "/api/auth/account",
                             {"password": PASSWORD, "confirm": "someone-else"})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(data_file.exists())

        response = self.call(client, "delete", "/api/auth/account",
                             {"password": PASSWORD, "confirm": "rose"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=user.pk).exists())
        self.assertFalse(data_file.exists())
        self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_admin_deletion_also_removes_the_data_file(self):
        user = accounts.create_user("rose", PASSWORD)
        db.connect_shared(self.shared).close()
        db.connect_user(user.id, self.shared).close()
        data_file = db.user_db_path(self.shared, user.id)
        self.assertTrue(data_file.exists())
        user.delete()
        self.assertFalse(data_file.exists())

    def test_deleting_a_user_without_a_data_file_is_fine(self):
        accounts.create_user("rose", PASSWORD).delete()


@override_settings(BATCHALL_TRUSTED_PROXY=True)
class ClientIpTests(TestCase):
    def test_trusted_proxy_uses_last_forwarded_entry(self):
        from django.test import RequestFactory
        request = RequestFactory().get(
            "/", HTTP_X_FORWARDED_FOR="1.1.1.1, 2.2.2.2 ", REMOTE_ADDR="9.9.9.9"
        )
        self.assertEqual(authviews.client_ip(request), "2.2.2.2")
        with override_settings(BATCHALL_TRUSTED_PROXY=False):
            self.assertEqual(authviews.client_ip(request), "9.9.9.9")
