import re
from pathlib import Path

from django.test import Client, SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


def read(name):
    return (WEB / name).read_text(encoding="utf-8")


def inline_script_tags(html):
    return [m for m in re.findall(r"<script\b[^>]*>", html, re.I) if not re.search(r"\bsrc\s*=", m, re.I)]


class WebPageTests(SimpleTestCase):
    def test_login_page_is_public_and_has_no_inline_script(self):
        response = Client().get("/login.html")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(inline_script_tags(html), [])
        self.assertNotIn(' style="', html)
        self.assertIsNone(re.search(r"\son\w+\s*=", html, re.I))

    def test_index_page_has_no_inline_script_or_style_attribute(self):
        html = read("index.html")
        self.assertEqual(inline_script_tags(html), [])
        self.assertNotIn(' style="', html)
        self.assertIn('id="account"', html)

    def test_login_page_references_existing_files(self):
        html = read("login.html")
        refs = re.findall(r'\b(?:src|href)="([^"]+)"', html)
        self.assertTrue(refs)
        client = Client()
        for ref in refs:
            if ref.startswith("data:"):
                continue
            with self.subTest(ref=ref):
                self.assertEqual(client.get("/" + ref.lstrip("/")).status_code, 200)

    def test_login_page_carries_the_security_headers(self):
        response = Client().get("/login.html")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])

    def test_app_script_sends_the_csrf_header_and_redirects_on_401(self):
        js = read("app.js")
        self.assertIn("X-CSRFToken", js)
        self.assertIn("csrftoken", js)
        self.assertIn("/login.html", js)
        self.assertIn("/api/auth/me", js)
        self.assertIn("/api/auth/logout", js)

    def test_login_script_fetches_the_csrf_cookie_first(self):
        js = read("login.js")
        self.assertLess(js.index("/api/auth/me"), js.index("/api/auth/login"))
        self.assertIn("X-CSRFToken", js)
        self.assertIn("/api/auth/signup", js)
