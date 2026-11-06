import os
import subprocess
import sys
from pathlib import Path

from django.contrib.auth.hashers import ScryptPasswordHasher
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import Client, SimpleTestCase, override_settings

ROOT = Path(__file__).resolve().parents[2]
GOOD_SECRET = "x" * 50


def run_python(code, **env):
    """Run code in a fresh interpreter with a clean BATCHALL_* environment plus env."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("BATCHALL_")}
    clean.pop("DJANGO_SETTINGS_MODULE", None)
    clean.update(env)
    return subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=clean, capture_output=True, text=True, timeout=60
    )


class SettingsImportTests(SimpleTestCase):
    def test_missing_or_short_secret_refuses_to_load(self):
        for env in ({}, {"BATCHALL_SECRET": "x" * 31}):
            with self.subTest(env=env):
                result = run_python("import hosted.settings", **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ImproperlyConfigured", result.stderr)

    def test_allowed_hosts_and_trusted_origins_merge_environment(self):
        result = run_python(
            "import hosted.settings as s; print(s.ALLOWED_HOSTS); print(s.CSRF_TRUSTED_ORIGINS)",
            BATCHALL_SECRET=GOOD_SECRET,
            BATCHALL_ALLOWED_HOSTS="a.example,b.example",
            BATCHALL_TRUSTED_ORIGINS="https://a.example,https://b.example",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        hosts, origins = result.stdout.strip().splitlines()
        self.assertEqual(
            eval(hosts), ["localhost", "127.0.0.1", "[::1]", "a.example", "b.example"]
        )
        self.assertEqual(eval(origins), ["https://a.example", "https://b.example"])

    def test_accounts_db_path_setting(self):
        result = run_python(
            "from batchall import config; print(config.ACCOUNTS_DB_PATH)",
            BATCHALL_ACCOUNTS_DB="/tmp/elsewhere/acct.db",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "/tmp/elsewhere/acct.db")

    def test_https_flag_controls_secure_cookies(self):
        code = "import hosted.settings as s; print(s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE)"
        off = run_python(code, BATCHALL_SECRET=GOOD_SECRET)
        on = run_python(code, BATCHALL_SECRET=GOOD_SECRET, BATCHALL_HTTPS="1")
        self.assertEqual(off.stdout.strip(), "False False", off.stderr)
        self.assertEqual(on.stdout.strip(), "True True", on.stderr)


class SettingsValueTests(SimpleTestCase):
    def test_session_cookie_settings(self):
        from django.conf import settings

        self.assertEqual(settings.SESSION_COOKIE_NAME, "batchall_session")
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertFalse(settings.SESSION_COOKIE_SECURE)

    def test_password_policy(self):
        from django.conf import settings

        self.assertTrue(settings.PASSWORD_HASHERS[0].endswith("ScryptPasswordHasher"))
        self.assertEqual(ScryptPasswordHasher.algorithm, "scrypt")
        with self.assertRaises(ValidationError):
            validate_password("short")
        validate_password("tenchars!!")

    def test_security_headers_on_a_response(self):
        response = Client().get("/admin/login/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        csp = response["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("blob:", csp)

    def test_non_admin_paths_allow_blob_images(self):
        from hosted.middleware import SecurityHeadersMiddleware
        from django.http import HttpResponse
        from django.test import RequestFactory

        middleware = SecurityHeadersMiddleware(lambda request: HttpResponse("ok"))
        response = middleware(RequestFactory().get("/"))
        self.assertIn("img-src 'self' data: blob:", response["Content-Security-Policy"])

    def test_existing_csp_is_kept(self):
        from hosted.middleware import SecurityHeadersMiddleware
        from django.http import HttpResponse
        from django.test import RequestFactory

        def view(request):
            response = HttpResponse("ok")
            response["Content-Security-Policy"] = "default-src 'none'"
            return response

        response = SecurityHeadersMiddleware(view)(RequestFactory().get("/"))
        self.assertEqual(response["Content-Security-Policy"], "default-src 'none'")
