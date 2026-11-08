"""The web UI under a share link, driven in headless Chrome against a live server."""

import json
import sys
import time
import unittest
from pathlib import Path

from django.conf import settings
from django.test import Client, LiveServerTestCase

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from browser import Browser, chrome_path, is_visible  # noqa: E402

from batchall import collection, copies, db  # noqa: E402
from hosted.tests.share_fixture import ShareFixture  # noqa: E402

WAIT_SECONDS = 10

# Edit controls that must not be on screen in the shared view, at any width.
EDIT_CONTROLS = (
    "#toggle-add", "#add-panel", ".tab[data-view=import]", "#tabbar [data-tab=import]", "#view-import",
    "#tray-save", "#tray-name", "#bulkbar", "#bulk-remove", "#select-all-shown", "input.sel-input",
    "#account", "#sign-out", "input.box-input", ".sel-x", ".paint-toggle", ".add-material", ".material-pill",
    ".forces-list .danger",
)


def wait_for(b, expression, seconds=WAIT_SECONDS):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if b.js(expression):
            return True
        time.sleep(0.1)
    return False


def button_texts(b, selector):
    return b.js(f"[...document.querySelectorAll({json.dumps(selector)})].map((e) => e.textContent.trim())")


@unittest.skipUnless(chrome_path(), "Chrome is not installed")
class ShareBrowserTests(LiveServerTestCase):
    # The same data as the share link API tests: alice with an Atlas, a photo, a saved force and a link.
    # LiveServerTestCase commits what setUp writes, so the server thread sees it.
    setUp_fixture = ShareFixture.setUp
    seed_alice = ShareFixture.seed_alice
    seed_bob = ShareFixture.seed_bob
    alice_rows = ShareFixture.alice_rows
    snapshot_alice = ShareFixture.snapshot_alice

    def setUp(self):
        self.setUp_fixture()
        conn = db.connect_user(self.alice.id, self.shared)
        try:
            locust = collection.add(conn, "u-locust", qty=2, box="Starter box")
            first = min(m["id"] for m in collection.get(conn, locust["id"])["minis"])
            copies.update(conn, first, company="Alpha Company", faction="Federated Suns", material="Metal",
                          painted=True, tag="scout")
            collection.add(conn, "u-wolfhound", qty=2)  # with these, the default lance can be built
        finally:
            conn.close()
        self.link_path = f"{self.base}/"

    def open(self, url, width, height, mobile=False):
        b = Browser(width, height, mobile=mobile)
        b.__enter__()
        self.addCleanup(b.__exit__, None, None, None)
        b.open(url)
        return b

    def open_shared(self, width=1280, height=900, mobile=False):
        return self.open(f"{self.live_server_url}{self.link_path}", width, height, mobile)

    def assert_no_edit_controls(self, b):
        for selector in EDIT_CONTROLS:
            with self.subTest(selector=selector):
                self.assertFalse(is_visible(b, selector), selector)

    def test_shared_page_is_read_only(self):
        b = self.open_shared()
        self.assertGreaterEqual(b.js("document.querySelectorAll('#roster tbody tr').length"), 1)
        self.assertEqual(b.console_errors(), [])
        self.assertTrue(b.js("document.body.classList.contains('shared')"))
        self.assert_no_edit_controls(b)
        # The only action left on a row is adding it to the tray.
        self.assertEqual(set(button_texts(b, "#roster td.actions button")), {"+ tray"})
        # Copy fields are text, not inputs.
        self.assertEqual(b.js("document.querySelectorAll('#roster tbody input, #roster tbody select').length"), 0)
        table = b.js("document.querySelector('#roster tbody').textContent")
        for text in ("Starter box", "Metal", "Alpha Company"):
            self.assertIn(text, table)

    def test_owner_name_is_shown_as_the_title(self):
        b = self.open_shared()
        self.assertTrue(is_visible(b, "#owner-name"))
        self.assertIn("alice", b.js("document.querySelector('#owner-name').textContent"))
        self.assertIn("alice", b.js("document.title"))

    def test_cards_view_has_no_edit_controls(self):
        b = self.open_shared()
        b.click("#view-controls button[data-view=cards]")
        self.assertTrue(wait_for(b, "document.querySelectorAll('.mini-card').length >= 3"))
        self.assert_no_edit_controls(b)
        self.assertEqual(set(button_texts(b, ".mini-card .card-actions button")), {"+ tray"})
        self.assertIn("Metal", b.js("document.querySelector('#card-grid').textContent"))

    def test_phone_width_has_no_edit_controls(self):
        b = self.open_shared(390, 844, mobile=True)
        self.assertGreaterEqual(b.js("document.querySelectorAll('.mini-card').length"), 3)
        self.assertEqual(b.console_errors(), [])
        self.assert_no_edit_controls(b)
        self.assertTrue(is_visible(b, "#tabbar [data-tab=forces]"))
        b.click("#tabbar [data-tab=forces]")
        self.assertTrue(is_visible(b, "#tray-build"))
        self.assertTrue(is_visible(b, ".force-item"))
        self.assert_no_edit_controls(b)

    def test_every_request_goes_under_the_link(self):
        b = self.open_shared()
        b.click("#view-controls button[data-view=cards]")
        self.assertTrue(wait_for(b, "document.querySelectorAll('.mini-card img.mini-photo').length >= 1"))
        fetched = b.js("performance.getEntriesByType('resource').map((e) => new URL(e.name).pathname)")
        images = b.js("[...document.querySelectorAll('img')].map((i) => new URL(i.src).pathname)")
        api_urls = [u for u in fetched + images if "/api/" in u]
        self.assertTrue(any(u.endswith("/api/collection") for u in api_urls), api_urls)
        self.assertEqual([u for u in api_urls if not u.startswith(self.base + "/api/")], [])
        self.assertIn(f"{self.base}/api/minis/{self.mini}/photo", images)

    def test_no_write_request_is_sent_while_browsing(self):
        before = self.snapshot_alice()
        b = self.open_shared()
        b.js(
            "window.__writes = []; const f = window.fetch; "
            "window.fetch = (u, o) => { if (o && o.method && o.method !== 'GET' && !/\\/api\\/build$/.test(u)) "
            "window.__writes.push(o.method + ' ' + u); return f(u, o); }; 0"
        )
        b.click("#roster tbody .unit-link")
        self.assertTrue(wait_for(b, "!!document.querySelector('.modal.detail')"))
        b.click(".detail-close")
        b.type_into("#filter-box", "Star")  # box suggestions, then a filtered reload
        self.assertTrue(wait_for(b, "!document.querySelector('#suggest').hidden"))
        self.assertTrue(wait_for(b, "document.querySelectorAll('#roster tbody tr').length === 2"))
        b.type_into("#filter-box", "")
        b.click("#view-controls button[data-view=cards]")
        self.assertTrue(wait_for(b, "document.querySelectorAll('.mini-card').length >= 3"))
        b.click(".mini-card .photo")
        self.assertTrue(wait_for(b, "!!document.querySelector('.modal.detail')"))
        b.click(".detail-close")
        b.click(".force-item")
        self.assertTrue(wait_for(b, "document.querySelectorAll('#tray-list li').length >= 1"))
        time.sleep(0.5)
        self.assertEqual(b.js("window.__writes"), [])
        self.assertTrue(b.js("document.querySelector('#toast').hidden"), b.js("document.querySelector('#toast').textContent"))
        self.assertEqual(b.console_errors(), [])
        self.assertEqual(self.snapshot_alice(), before)

    def test_api_refuses_writes_before_sending(self):
        b = self.open_shared()
        b.js(
            "window.__sent = []; const f = window.fetch; "
            "window.fetch = (u, o) => { window.__sent.push(u); return f(u, o); }; 0"
        )
        for method, path in (("PATCH", f"/api/copies/{self.mini}"), ("POST", "/api/forces"),
                             ("DELETE", f"/api/minis/{self.mini}"), ("PUT", "/api/company-colors"),
                             ("POST", "/api/import")):
            with self.subTest(method=method, path=path):
                result = b.js(f"api({json.dumps(method)}, {json.dumps(path)}, {{}}).then(() => 'sent', (e) => e.message)")
                self.assertEqual(result, "read-only")
        self.assertEqual(b.js("window.__sent"), [])

    def test_build_for_me_works(self):
        before = self.snapshot_alice()
        b = self.open_shared()
        b.click("#roster td.actions button")
        self.assertTrue(wait_for(b, "document.querySelectorAll('#tray-list li').length === 1"))
        b.type_into("#tray-target", "4000")  # the cheapest lance of alice's minis is 3160 BV
        b.click("#tray-build")
        self.assertTrue(wait_for(b, "/^Built/.test(document.querySelector('#toast').textContent)"),
                        b.js("document.querySelector('#toast').textContent"))
        self.assertFalse(b.js("document.querySelector('#toast').classList.contains('error')"))
        self.assertGreaterEqual(b.js("document.querySelectorAll('#tray-list li').length"), 1)
        self.assertEqual(b.console_errors(), [])
        self.assertEqual(self.snapshot_alice(), before)

    def test_owner_view_still_has_its_edit_controls(self):
        client = Client()
        client.force_login(self.alice)
        cookie = settings.SESSION_COOKIE_NAME
        b = Browser(1280, 900, mobile=False)
        b.__enter__()
        self.addCleanup(b.__exit__, None, None, None)
        b.ws.send("Network.setCookie", name=cookie, value=client.cookies[cookie].value, url=self.live_server_url)
        b.open(f"{self.live_server_url}/")
        self.assertFalse(b.js("document.body.classList.contains('shared')"))
        for selector in ("#toggle-add", ".tab[data-view=import]", "#tray-save", "#sign-out", "input.box-input",
                         "#select-all-shown"):
            with self.subTest(selector=selector):
                self.assertTrue(is_visible(b, selector), selector)
        self.assertFalse(is_visible(b, "#owner-name"))
        self.assertIn("✎", button_texts(b, "#roster td.actions button"))
        self.assertEqual(b.console_errors(), [])
