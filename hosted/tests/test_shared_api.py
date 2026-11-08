from unittest import mock

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

    def test_build_accepts_the_largest_formation_and_target(self):
        body = {"target_bv": shared.MAX_BUILD_TARGET_BV, "size": shared.MAX_BUILD_SIZE}
        response = self.client.post(f"{self.base}/api/build", body, content_type="application/json")
        # Alice's one Atlas cannot fill a company, so the builder answers; the ceiling must not.
        self.assertIn("pool cannot fill", response.json()["error"])

    def test_build_rejects_an_oversized_target(self):
        for target in (shared.MAX_BUILD_TARGET_BV + 1, "1e400"):
            with self.subTest(target=target):
                raw = f'{{"target_bv": {target}, "size": 1}}'
                response = self.client.post(f"{self.base}/api/build", raw, content_type="application/json")
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("target_bv", response.json()["error"])

    def test_build_rejects_an_oversized_size(self):
        body = {"target_bv": 2000, "size": shared.MAX_BUILD_SIZE + 1}
        response = self.client.post(f"{self.base}/api/build", body, content_type="application/json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("size", response.json()["error"])

    def test_owner_build_route_has_no_shared_limits(self):
        self.client.force_login(self.alice)
        body = {"target_bv": 2000, "size": shared.MAX_BUILD_SIZE + 1}
        response = self.client.post("/api/build", body, content_type="application/json")
        self.assertNotIn("at most", response.content.decode())

    def test_a_second_concurrent_build_gets_429(self):
        self.assertTrue(shared.build_slot.acquire(blocking=False))
        try:
            response = self.client.post(f"{self.base}/api/build", BUILD, content_type="application/json")
        finally:
            shared.build_slot.release()
        self.assertEqual(response.status_code, 429)
        self.assertIn("error", response.json())
        self.assertTrue(response["Retry-After"].isdigit())
        self.assertEqual(self.client.post(f"{self.base}/api/build", BUILD, content_type="application/json").status_code, 200)

    def test_build_slot_is_released_when_the_build_raises(self):
        with mock.patch.object(shared.api, "dispatch", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.client.post(f"{self.base}/api/build", BUILD, content_type="application/json")
        self.assertTrue(shared.build_slot.acquire(blocking=False))
        shared.build_slot.release()

    def test_a_burst_of_api_requests_is_not_throttled(self):
        for _ in range(150):
            response = self.client.get(f"{self.base}/api/minis/{self.mini}/photo")
            self.assertEqual(response.status_code, 200)

    def flood(self, ip):
        for _ in range(shared.API_REQUESTS_PER_WINDOW):
            shared.api_limiter.record_failure(ip)

    def test_a_flood_from_one_address_gets_429(self):
        self.flood("10.0.0.1")
        response = self.client.get(f"{self.base}/api/collection", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(response.status_code, 429)
        self.assertIn("error", response.json())
        self.assertGreater(int(response["Retry-After"]), 0)

    def test_the_limit_counts_requests_up_to_the_ceiling(self):
        for _ in range(shared.API_REQUESTS_PER_WINDOW - 1):
            shared.api_limiter.record_failure("10.0.0.1")
        self.assertEqual(self.client.get(f"{self.base}/api/collection", REMOTE_ADDR="10.0.0.1").status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}/api/collection", REMOTE_ADDR="10.0.0.1").status_code, 429)

    def test_a_flood_does_not_throttle_another_address(self):
        self.flood("10.0.0.1")
        self.assertEqual(self.client.get(f"{self.base}/api/collection", REMOTE_ADDR="10.0.0.2").status_code, 200)

    def test_page_and_assets_are_not_throttled_or_counted(self):
        self.flood("10.0.0.1")
        for rel in ("", "app.js", "style.css"):
            with self.subTest(rel=rel):
                self.assertEqual(self.client.get(f"{self.base}/{rel}", REMOTE_ADDR="10.0.0.1").status_code, 200)
        for _ in range(200):
            self.client.get(f"{self.base}/app.js", REMOTE_ADDR="10.0.0.3")
        self.assertEqual(shared.api_limiter.retry_after("10.0.0.3"), 0)
        self.assertNotIn("10.0.0.3", shared.api_limiter._entries)

    def test_owner_api_is_not_throttled(self):
        self.flood("127.0.0.1")
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get("/api/collection").status_code, 200)

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
