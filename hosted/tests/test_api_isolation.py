import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase, override_settings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from support import seed_catalog  # noqa: E402

from batchall import collection, colors, db, forces, logos, photos, server  # noqa: E402
from hosted import accounts  # noqa: E402

PASSWORD = "correct horse battery"
JPEG = b"\xff\xd8\xff\xe0" + b"fake photo"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake"
PHOTO_BODY = {"image": base64.b64encode(JPEG).decode(), "width": 800, "height": 600}


class ApiIsolationTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.shared = Path(self._tmp.name) / "batchall.db"
        override = override_settings(BATCHALL_SHARED_DB=str(self.shared))
        override.enable()
        self.addCleanup(override.disable)
        saved_cache = server.art_cache_dir
        server.art_cache_dir = Path(self._tmp.name) / "art"
        self.addCleanup(setattr, server, "art_cache_dir", saved_cache)

        shared = db.connect_shared(self.shared)
        seed_catalog(shared)
        shared.close()

        self.user_a = accounts.create_user("alice", PASSWORD)
        self.user_b = accounts.create_user("bob", PASSWORD)
        self.seed_a()
        self.client_b = Client(enforce_csrf_checks=False)
        self.client_b.force_login(self.user_b)

    def seed_a(self):
        conn = db.connect_user(self.user_a.id, self.shared)
        try:
            self.entry = collection.add(conn, "u-atlas", qty=2)
            self.minis = [m["id"] for m in collection.get(conn, self.entry["id"])["minis"]]
            photos.save(conn, self.minis[0], PHOTO_BODY["image"], 800, 600)
            self.force = forces.save(
                conn, "Alice lance", "lance",
                [{"collection_id": self.entry["id"], "qty": 1}],
            )
            logos.save(conn, "Alice Co", "https://sarna.net/wiki/images/a.png", fetch=lambda url: PNG)
            colors.save(conn, "Alice Co", "#112233")
        finally:
            conn.close()

    def snapshot_a(self):
        conn = db.connect_user(self.user_a.id, self.shared)
        try:
            return {
                "copies": [tuple(r) for r in conn.execute("SELECT * FROM copies ORDER BY id")],
                "collection": [tuple(r) for r in conn.execute("SELECT * FROM collection ORDER BY id")],
                "forces": [tuple(r) for r in conn.execute("SELECT * FROM forces ORDER BY id")],
                "photos": conn.execute("SELECT COUNT(*) FROM mini_photos").fetchone()[0],
            }
        finally:
            conn.close()

    def send(self, client, method, path, body=None):
        kwargs = {}
        if method in ("post", "put", "patch", "delete"):
            kwargs = {"data": json.dumps(body if body is not None else {}), "content_type": "application/json"}
        return getattr(client, method)(path, **kwargs)

    def test_every_id_route_in_routes_is_classified(self):
        for method, pattern, handler in server.ROUTES:
            first = pattern.pattern.find("(")
            if first == -1 or not pattern.pattern.startswith("(\\d+)", first):
                continue
            with self.subTest(route=pattern.pattern, method=method):
                self.assertTrue(
                    handler in server.OWNED_ID_ROUTES or handler in server.SHARED_ID_ROUTES,
                    f"{handler.__name__} takes an id but is not classified",
                )

    def test_other_users_ids_answer_404_on_every_owned_route(self):
        ids = {
            "collection": self.entry["id"],
            "copies": self.minis[0],
            "forces": self.force["id"],
        }
        before = self.snapshot_a()
        checked = set()
        for method, pattern, handler in server.ROUTES:
            table = server.OWNED_ID_ROUTES.get(handler)
            if table is None:
                continue
            checked.add(handler)
            path = pattern.pattern[1:-1].replace("(\\d+)", str(ids[table]))
            with self.subTest(method=method, path=path):
                body = PHOTO_BODY if handler is server.put_mini_photo else {"name": "x", "zoom": 2}
                response = self.send(self.client_b, method.lower(), path, body)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"error": "not found"})
        self.assertEqual(checked, set(server.OWNED_ID_ROUTES))
        self.assertEqual(self.snapshot_a(), before)

    def test_other_users_data_is_not_listed(self):
        for path in ("/api/collection", "/api/forces", "/api/company-logos", "/api/company-colors", "/api/copy-tags"):
            with self.subTest(path=path):
                response = self.client_b.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json())

    def test_bulk_routes_with_foreign_ids_change_nothing(self):
        before = self.snapshot_a()
        response = self.send(self.client_b, "patch", "/api/minis", {"ids": self.minis, "note": "mine now"})
        self.assertEqual(response.status_code, 400)
        response = self.send(self.client_b, "delete", "/api/minis", {"ids": self.minis})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot_a(), before)

    def test_same_id_in_two_accounts_is_two_rows(self):
        response = self.send(self.client_b, "post", "/api/collection", {"unit_uuid": "u-locust", "qty": 1})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["id"], self.entry["id"])
        before = self.snapshot_a()
        response = self.send(self.client_b, "patch", f"/api/collection/{self.entry['id']}", {"qty": 3})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["qty"], 3)
        self.assertEqual(self.snapshot_a(), before)
        listed = self.client_b.get("/api/collection").json()
        self.assertEqual([e["unit_uuid"] if "unit_uuid" in e else e["uuid"] for e in listed], ["u-locust"])

    def test_unknown_path_is_404_before_the_sign_in_check(self):
        anon = Client(enforce_csrf_checks=False)
        self.assertEqual(anon.get("/api/nope").status_code, 404)
        self.assertEqual(anon.get("/api/collection").status_code, 401)
        self.assertEqual(anon.get("/api/collection").json(), {"error": "sign in required"})
        self.assertEqual(self.send(anon, "post", "/api/build", {}).status_code, 401)
        self.assertEqual(self.send(anon, "post", "/api/meta", {}).status_code, 405)

    def test_shared_reference_routes_work_when_signed_in(self):
        meta = self.client_b.get("/api/meta")
        self.assertEqual(meta.status_code, 200)
        self.assertTrue(meta.json())
        found = self.client_b.get("/api/catalog/search?q=atlas")
        self.assertEqual(found.status_code, 200)
        self.assertIn("Atlas", json.dumps(found.json()))

    def test_raw_responses_carry_image_headers(self):
        client_a = Client(enforce_csrf_checks=False)
        client_a.force_login(self.user_a)
        response = client_a.get(f"/api/minis/{self.minis[0]}/photo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(
            response["Content-Security-Policy"], "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        )
        self.assertEqual(response["Cache-Control"], "private, max-age=86400")
        self.assertEqual(b"".join(response.streaming_content) if response.streaming else response.content, JPEG)

    def test_photo_upload_works_for_the_owner(self):
        client_a = Client(enforce_csrf_checks=False)
        client_a.force_login(self.user_a)
        response = self.send(client_a, "put", f"/api/minis/{self.minis[1]}/photo", PHOTO_BODY)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(client_a.get(f"/api/minis/{self.minis[1]}/photo").status_code, 200)

    def test_body_rules(self):
        response = self.client_b.post("/api/collection", data="unit_uuid=u-locust", content_type="text/plain")
        self.assertEqual(response.status_code, 415)
        response = self.client_b.post("/api/collection", data="[1]", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        response = self.client_b.post("/api/collection", data="{nope", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        big = b" " * (server.MAX_BODY_BYTES + 1)
        response = self.client_b.post("/api/collection", data=big, content_type="application/json")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"error": "request body is too large"})

    def test_pages_are_public_and_safe(self):
        anon = Client()
        self.assertEqual(anon.get("/index.html").status_code, 200)
        self.assertEqual(anon.get("/").status_code, 200)
        self.assertEqual(anon.get("/index.html")["Cache-Control"], "no-cache")
        self.assertEqual(anon.get("/../manage.py").status_code, 404)
        self.assertEqual(anon.get("/%2e%2e/manage.py").status_code, 404)
        self.assertEqual(anon.get("/nothing-here.html").status_code, 404)
        self.assertEqual(anon.post("/index.html").status_code, 405)

    @unittest.skip("login.html arrives in Task 8")
    def test_login_page_is_public(self):
        self.assertEqual(Client().get("/login.html").status_code, 200)

    def test_mutating_api_call_without_csrf_token_gets_json_403(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user_b)
        response = client.post("/api/collection", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())

    def test_replayed_session_cookie_after_logout_is_rejected(self):
        client = Client(enforce_csrf_checks=False)
        self.assertTrue(client.login(username="alice", password=PASSWORD))
        cookie_name = settings.SESSION_COOKIE_NAME
        saved = client.cookies[cookie_name].value
        self.assertEqual(client.get("/api/collection").status_code, 200)
        client.post("/api/auth/logout", data="{}", content_type="application/json")
        replay = Client(enforce_csrf_checks=False)
        replay.cookies[cookie_name] = saved
        self.assertEqual(replay.get("/api/collection").status_code, 401)
