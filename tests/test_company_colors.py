"""Company strip colors: validation, storage and the API."""

import http.client
import json
import os
import tempfile
import threading
import unittest

from batchall import colors, db, server


class ColorTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_normalize_accepts_hex_forms_and_returns_capitals(self):
        self.assertEqual(colors.normalize("#8a1c2b"), "#8A1C2B")
        self.assertEqual(colors.normalize("8a1c2b"), "#8A1C2B")
        self.assertEqual(colors.normalize(" #abc "), "#AABBCC")

    def test_normalize_refuses_anything_else(self):
        for bad in ("", "#12", "#12345", "#1234567", "red", "#gggggg", "url(x)", "#fff; x", None, 5):
            with self.assertRaises(colors.ColorError, msg=repr(bad)):
                colors.normalize(bad)

    def test_save_replaces_and_matches_names_ignoring_case_and_space(self):
        colors.save(self.conn, "Deneb Command Lance", "#c9a227")
        colors.save(self.conn, "  deneb command LANCE ", "#112233")
        self.assertEqual(colors.list_all(self.conn),
                         [{"key": "deneb command lance", "name": "deneb command LANCE", "color": "#112233"}])

    def test_blank_name_is_refused_and_nothing_is_stored(self):
        for name in ("", "   ", None):
            with self.assertRaises(colors.ColorError):
                colors.save(self.conn, name, "#112233")
        self.assertEqual(colors.list_all(self.conn), [])

    def test_remove(self):
        colors.save(self.conn, "A", "#112233")
        self.assertTrue(colors.remove(self.conn, " a "))
        self.assertFalse(colors.remove(self.conn, "a"))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.httpd = server.make_server(port=0, db_path=os.path.join(cls.tmp.name, "t.db"), art_dir=cls.tmp.name)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        res = conn.getresponse()
        data = json.loads(res.read())
        conn.close()
        return res.status, data

    def test_save_list_and_remove(self):
        status, saved = self.request("PUT", "/api/company-colors", {"name": "Black Widow Company", "color": "2f5d3a"})
        self.assertEqual((status, saved["color"]), (200, "#2F5D3A"))
        self.assertEqual(self.request("GET", "/api/company-colors")[1][0]["key"], "black widow company")
        self.assertEqual(self.request("DELETE", "/api/company-colors?name=Black+Widow+Company"), (200, {"removed": True}))
        self.assertEqual(self.request("DELETE", "/api/company-colors?name=Black+Widow+Company")[0], 404)
        self.assertEqual(self.request("GET", "/api/company-colors"), (200, []))

    def test_bad_color_or_name_is_a_400(self):
        self.assertEqual(self.request("PUT", "/api/company-colors", {"name": "X", "color": "javascript:alert(1)"})[0], 400)
        self.assertEqual(self.request("PUT", "/api/company-colors", {"color": "#112233"})[0], 400)


if __name__ == "__main__":
    unittest.main()
