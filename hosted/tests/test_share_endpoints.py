from django.test import Client

from hosted import sharing
from hosted.tests.share_fixture import ShareFixture

URL = "/api/auth/share"


class ShareEndpointTests(ShareFixture):
    def setUp(self):
        super().setUp()
        sharing.revoke_link(self.alice)
        self.anon = Client()
        self.alice_client = self.signed_in(self.alice)
        self.bob_client = self.signed_in(self.bob)
        self.client_no_token = Client(enforce_csrf_checks=True)
        self.client_no_token.force_login(self.alice)

    def signed_in(self, user):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/auth/me")
        client.force_login(user)
        return client

    def call(self, client, method):
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        return getattr(client, method)(URL, content_type="application/json", **headers)

    def get(self):
        return self.call(self.alice_client, "get")

    def post(self):
        return self.call(self.alice_client, "post")

    def delete(self):
        return self.call(self.alice_client, "delete")

    def test_signed_out_is_401(self):
        for method in ("get", "post", "delete"):
            self.assertEqual(getattr(self.anon, method)(URL).status_code, 401)

    def test_lifecycle(self):
        self.assertIsNone(self.get().json()["url"])
        first = self.post().json()["url"]
        self.assertEqual(self.get().json()["url"], first)
        second = self.post().json()["url"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.anon.get(first).status_code, 404)
        self.assertEqual(self.anon.get(second).status_code, 200)
        self.assertIsNone(self.delete().json()["url"])
        self.assertEqual(self.anon.get(second).status_code, 404)

    def test_write_without_csrf_token_is_403(self):
        for method in ("post", "delete"):
            response = getattr(self.client_no_token, method)(URL)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {"error": "CSRF check failed"})
        self.assertIsNone(sharing.current_link(self.alice))

    def test_links_are_per_user(self):
        self.post()
        self.assertIsNone(self.call(self.bob_client, "get").json()["url"])
        self.call(self.bob_client, "delete")
        self.assertIsNotNone(self.get().json()["url"])

    def test_other_methods_are_405(self):
        response = self.call(self.alice_client, "put")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "DELETE, GET, POST")
