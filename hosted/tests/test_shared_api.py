from django.test import Client

from hosted.tests.share_fixture import PASSWORD, ShareFixture

from batchall import db, server
from hosted import accounts, shared, sharing

# Alice owns one Atlas (1897 BV), so a one-unit build is the one the pool can fill.
BUILD = {"target_bv": 2000, "size": 1}


class SharedApiTests(ShareFixture):
    def test_page_and_assets_load_under_the_prefix(self):
        for rel in ("", "app.js", "style.css"):
            with self.subTest(rel=rel):
                self.assertEqual(self.client.get(f"{self.base}/{rel}").status_code, 200)

    def test_collection_is_the_owners(self):
        data = self.client.get(f"{self.base}/api/collection").json()
        self.assertEqual([e["unit_uuid"] for e in data], ["u-atlas"])

    def test_photo_and_forces_are_served(self):
        self.assertEqual(self.client.get(f"{self.base}/api/minis/{self.mini}/photo").status_code, 200)
        self.assertEqual([f["name"] for f in self.client.get(f"{self.base}/api/forces").json()], ["Alice lance"])

    def test_me_reports_shared_mode_without_private_fields(self):
        me = self.client.get(f"{self.base}/api/auth/me").json()
        self.assertEqual(set(me), {"mode", "owner"})
        self.assertEqual(me["mode"], "shared")
        self.assertEqual(me["owner"], "alice")

    def test_me_uses_the_display_name_when_set(self):
        self.alice.first_name = "Alice A."
        self.alice.save()
        self.assertEqual(self.client.get(f"{self.base}/api/auth/me").json()["owner"], "Alice A.")

    def test_bad_revoked_and_replaced_tokens_give_the_same_404(self):
        old = self.link.token
        sharing.create_link(self.alice)  # replaces
        answers = {
            self.client.get(f"/s/{t}/api/collection").content
            for t in (old, "nope", "1.x", "9" * 5000, "é")
        }
        self.assertEqual(len(answers), 1)
        self.assertEqual(self.client.get(f"/s/{old}/").status_code, 404)

    def test_revoked_token_is_404(self):
        sharing.revoke_link(self.alice)
        response = self.client.get(f"{self.base}/api/collection")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "not found"})

    def test_signed_in_visitor_still_sees_only_the_token_owner(self):
        self.client.force_login(self.bob)
        data = self.client.get(f"{self.base}/api/collection").json()
        self.assertEqual([e["unit_uuid"] for e in data], ["u-atlas"])
        self.assertEqual(self.client.get(f"{self.base}/api/auth/me").json()["owner"], "alice")

    def test_other_users_mini_id_is_404(self):
        self.assertEqual(self.client.get(f"{self.base}/api/minis/{self.bob_mini}/photo").status_code, 404)

    def test_every_other_route_is_refused_and_nothing_changes(self):
        before = self.snapshot_alice()
        checked = 0
        for method, pattern, handler in server.ROUTES:
            if (method, handler) in shared.ALLOWED:
                continue
            if handler is server.get_auth_me:
                continue  # answered by shared_view itself, see test_me_reports_shared_mode_without_private_fields
            path = pattern.pattern.strip("^$").replace(r"(\d+)", str(self.mini)).replace(r"([^/]+)", "u-atlas")
            with self.subTest(method=method, path=path):
                response = self.client.generic(method, f"{self.base}{path}", b"{}", content_type="application/json")
                self.assertIn(response.status_code, (404, 405), f"{method} {path}")
                checked += 1
        self.assertGreater(checked, 20)
        self.assertEqual(self.snapshot_alice(), before)

    def test_signed_in_owner_cannot_write_through_the_link(self):
        self.client.force_login(self.alice)
        before = self.snapshot_alice()
        response = self.client.delete(f"{self.base}/api/forces/{self.force['id']}")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.snapshot_alice(), before)

    def test_build_works_without_a_session_and_stores_nothing(self):
        before = self.snapshot_alice()
        response = self.client.post(f"{self.base}/api/build", BUILD, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.snapshot_alice(), before)

    def test_build_needs_no_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(f"{self.base}/api/build", BUILD, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_owner_without_a_data_file_gets_404_and_no_file(self):
        carol = accounts.create_user("carol", PASSWORD)
        token = sharing.create_link(carol).token
        response = self.client.get(f"/s/{token}/api/collection")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, self.client.get("/s/nope/api/collection").content)
        self.assertEqual(self.client.get(f"/s/{token}/").status_code, 404)
        self.assertFalse(db.user_db_path(self.shared, carol.id).exists())

    def test_malformed_token_in_a_page_url_is_404_not_500(self):
        for t in ("", "%00", "a" * 5000):
            with self.subTest(token=t[:10]):
                self.assertEqual(self.client.get(f"/s/{t}/").status_code, 404)

    def test_every_allowed_get_runs_on_the_read_only_connection(self):
        # A handler that writes would fail on the mode=ro connection with a 500 ("readonly database").
        ids = {"collection": self.entry["id"], "copies": self.mini, "forces": self.force["id"]}
        before = self.snapshot_alice()
        checked = set()
        for method, pattern, handler in server.ROUTES:
            if method != "GET" or (method, handler) not in shared.ALLOWED:
                continue
            row_id = ids.get(server.OWNED_ID_ROUTES.get(handler), 1)
            path = pattern.pattern.strip("^$").replace(r"(\d+)", str(row_id)).replace(r"([^/]+)", "u-atlas")
            with self.subTest(path=path), self.assertNoLogs("hosted.api", "ERROR"):
                response = self.client.get(f"{self.base}{path}?name=Alice+Co&q=atlas")
                self.assertIn(response.status_code, (200, 404), response.content)
                checked.add(handler)
        self.assertEqual(checked, {h for m, h in shared.ALLOWED if m == "GET"})
        self.assertEqual(self.snapshot_alice(), before)

    def test_allowed_is_get_only_apart_from_build(self):
        self.assertEqual({m for m, h in shared.ALLOWED if h is not server.post_build}, {"GET"})
        self.assertIn(("POST", server.post_build), shared.ALLOWED)
        for handler in (server.get_auth_me, server.get_company_logo_candidates, server.get_box_catalog):
            self.assertNotIn(("GET", handler), shared.ALLOWED)
