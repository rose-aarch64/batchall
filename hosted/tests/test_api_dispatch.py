import json

from django.test import RequestFactory, SimpleTestCase

from batchall import server
from hosted import api


class DispatchTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/api/meta")

    def test_missing_database_is_a_json_404(self):
        def open_conn():
            raise FileNotFoundError("gone")

        response = api.dispatch(self.request, "meta", open_conn=open_conn)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.content), {"error": "not found"})

    def test_route_outside_allowed_is_405_and_never_opens_a_connection(self):
        def open_conn():
            self.fail("connection opened")

        allowed = {("GET", server.get_collection)}
        response = api.dispatch(self.request, "meta", open_conn=open_conn, allowed=allowed)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(json.loads(response.content), {"error": "no such endpoint"})
