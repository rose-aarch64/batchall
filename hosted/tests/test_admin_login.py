import json
from unittest import mock

from django.contrib.auth.backends import ModelBackend
from django.test import Client, TestCase

from hosted import accounts, authviews
from hosted.tests.limiters import reset_limiters

PASSWORD = "correct horse battery"
IP_A = "10.0.0.1"
IP_B = "10.0.0.2"


class AdminLoginRateLimitTests(TestCase):
    def setUp(self):
        reset_limiters()
        self.addCleanup(reset_limiters)
        accounts.create_user("rose", PASSWORD, superuser=True)
        self.client = Client()

    def admin_login(self, username="rose", password="wrong password!", ip=IP_A):
        return self.client.post(
            "/admin/login/",
            {"username": username, "password": password, "next": "/admin/"},
            REMOTE_ADDR=ip,
        )

    def api_login(self, handle="rose", password="wrong password!", ip=IP_A):
        return self.client.post(
            "/api/auth/login",
            data=json.dumps({"handle": handle, "password": password}),
            content_type="application/json",
            REMOTE_ADDR=ip,
        )

    def test_six_wrong_posts_lock_the_handle_and_ip_pair(self):
        for _ in range(5):
            self.assertEqual(self.admin_login().status_code, 200)
        response = self.admin_login()
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)

    def test_correct_password_while_locked_is_refused_before_hashing(self):
        for _ in range(5):
            self.admin_login()
        with mock.patch.object(ModelBackend, "authenticate", return_value=None) as authenticate:
            response = self.admin_login(password=PASSWORD)
        self.assertEqual(response.status_code, 429)
        authenticate.assert_not_called()

    def test_a_good_login_before_lockout_works_and_resets_the_handle(self):
        for _ in range(4):
            self.assertEqual(self.admin_login().status_code, 200)
        good = self.admin_login(password=PASSWORD)
        self.assertEqual(good.status_code, 302)
        self.client.logout()
        for _ in range(4):
            self.assertEqual(self.admin_login().status_code, 200)

    def test_another_ip_is_not_locked_with_the_pair(self):
        for _ in range(5):
            self.admin_login(ip=IP_A)
        self.assertEqual(self.admin_login(ip=IP_A).status_code, 429)
        self.assertEqual(self.admin_login(password=PASSWORD, ip=IP_B).status_code, 302)

    def test_a_different_handle_from_the_same_ip_is_unaffected_until_the_ip_cap(self):
        for _ in range(5):
            self.admin_login()
        self.assertEqual(self.admin_login().status_code, 429)
        self.assertEqual(self.admin_login(username="someone").status_code, 200)
        for number in range(30):
            self.admin_login(username=f"guess-{number}")
        self.assertEqual(self.admin_login(username="another").status_code, 429)

    def test_invalid_handles_share_one_fixed_key(self):
        for _ in range(5):
            self.assertEqual(self.admin_login(username="!").status_code, 200)
        self.assertEqual(self.admin_login(username="?").status_code, 429)

    def test_the_posted_username_is_normalized_like_a_handle(self):
        for _ in range(5):
            self.admin_login(username="  Rose ")
        self.assertEqual(self.admin_login(username="rose").status_code, 429)

    def test_fullwidth_spelling_cannot_get_around_a_lock(self):
        for _ in range(5):
            self.admin_login()
        self.assertEqual(self.admin_login().status_code, 429)
        response = self.admin_login(username="ｒｏｓｅ", password=PASSWORD)
        self.assertEqual(response.status_code, 429)

    def test_fullwidth_failures_count_against_the_plain_handle(self):
        spellings = ["ｒｏｓｅ", "rose", "ｒｏｓｅ", "rose", "ｒｏｓｅ"]
        for username in spellings:
            self.assertEqual(self.admin_login(username=username).status_code, 200)
        self.assertEqual(self.admin_login(username="rose", password=PASSWORD).status_code, 429)

    def test_mixed_spelling_is_handled_the_same_way(self):
        for _ in range(5):
            self.admin_login()
        response = self.admin_login(username="ｒose", password=PASSWORD)
        self.assertEqual(response.status_code, 429)
        self.reset_and_fail_with_mixed()

    def reset_and_fail_with_mixed(self):
        reset_limiters()
        for _ in range(5):
            self.assertEqual(self.admin_login(username="ｒose").status_code, 200)
        self.assertEqual(self.admin_login(username="rose", password=PASSWORD).status_code, 429)

    def test_missing_and_nul_usernames_use_the_fixed_key(self):
        for _ in range(3):
            self.client.post("/admin/login/", {"password": "x", "next": "/admin/"}, REMOTE_ADDR=IP_A)
        self.assertEqual(self.admin_login(username="ro\x00se").status_code, 200)
        self.assertEqual(self.admin_login(username="！").status_code, 200)
        self.assertEqual(self.admin_login(username="!").status_code, 429)
        self.assertEqual(self.admin_login(username="rose", password=PASSWORD, ip=IP_B).status_code, 302)

    def test_locked_callers_do_not_record_failures(self):
        for _ in range(5):
            self.admin_login()
        self.assertEqual(self.admin_login().status_code, 429)
        before = len(authviews.ip_limiter._entries[("ip", IP_A)].failures)
        for _ in range(3):
            self.assertEqual(self.admin_login().status_code, 429)
        self.assertEqual(len(authviews.ip_limiter._entries[("ip", IP_A)].failures), before)

    def test_get_requests_are_never_limited(self):
        for _ in range(5):
            self.admin_login()
        self.assertEqual(self.client.get("/admin/login/", REMOTE_ADDR=IP_A).status_code, 200)

    def test_api_and_admin_logins_share_the_lock(self):
        for _ in range(3):
            self.assertEqual(self.api_login().status_code, 401)
        for _ in range(2):
            self.assertEqual(self.admin_login().status_code, 200)
        self.assertEqual(self.api_login(password=PASSWORD).status_code, 429)
        self.assertEqual(self.admin_login(password=PASSWORD).status_code, 429)
