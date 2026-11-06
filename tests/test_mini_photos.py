"""Mini photos: storage, the crop, how a photo ties to its mini, and the API."""

import base64
import http.client
import json
import os
import tempfile
import threading
import unittest

from support import make_db, own

from batchall import collection, copies, db, photos, server

JPEG = b"\xff\xd8\xff\xe0" + b"fake photo"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake"
B64 = base64.b64encode(JPEG).decode()


class PhotoTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.entry = own(self.conn, "u-atlas", qty=2)
        self.mini = collection.get(self.conn, self.entry["id"])["minis"][0]["id"]

    def save(self, **kw):
        args = dict(image=B64, width=800, height=600)
        args.update(kw)
        return photos.save(self.conn, self.mini, **args)

    def test_save_keeps_the_photo_and_its_crop(self):
        saved = self.save(zoom=2.5, cx=0.25, cy=0.75)
        self.assertEqual((saved["width"], saved["height"], saved["zoom"], saved["cx"], saved["cy"]), (800, 600, 2.5, 0.25, 0.75))
        self.assertEqual(photos.get(self.conn, self.mini), (JPEG, "image/jpeg"))

    def test_a_data_url_is_accepted_and_the_crop_defaults_to_the_center(self):
        saved = self.save(image="data:image/jpeg;base64," + B64)
        self.assertEqual((saved["zoom"], saved["cx"], saved["cy"]), (1.0, 0.5, 0.5))

    def test_saving_again_replaces_the_photo(self):
        self.save()
        self.save(image=base64.b64encode(PNG).decode(), width=10, height=10)
        self.assertEqual(photos.get(self.conn, self.mini), (PNG, "image/png"))
        self.assertEqual(len(photos.summaries(self.conn)), 1)

    def test_set_crop_changes_only_the_crop(self):
        self.save()
        photos.set_crop(self.conn, self.mini, 3, 0.1, 0.9)
        summary = photos.get_summary(self.conn, self.mini)
        self.assertEqual((summary["zoom"], summary["cx"], summary["cy"], summary["width"]), (3.0, 0.1, 0.9, 800))
        self.assertEqual(photos.get(self.conn, self.mini), (JPEG, "image/jpeg"))

    def test_set_crop_without_a_photo_is_an_error(self):
        with self.assertRaises(LookupError):
            photos.set_crop(self.conn, self.mini, 1, 0.5, 0.5)

    def test_bad_input_is_refused_and_nothing_is_stored(self):
        bad = [
            dict(image="not base64 !!"), dict(image=""), dict(image=None),
            dict(image=base64.b64encode(b"<html>nope</html>").decode()),
            dict(image=base64.b64encode(b"<svg xmlns='x'></svg>").decode()),
            dict(image=base64.b64encode(b"GIF89a....").decode()),
            dict(zoom=0.5), dict(zoom=9), dict(cx=1.5), dict(cy=-0.1), dict(zoom="2"), dict(zoom=True),
            dict(width=0), dict(height=30000), dict(width="800"),
        ]
        for kw in bad:
            with self.assertRaises(photos.PhotoError, msg=str(kw)[:60]):
                self.save(**kw)
        with self.assertRaises(photos.PhotoError):
            self.save(image=base64.b64encode(JPEG + b"x" * photos.MAX_BYTES).decode())
        self.assertEqual(photos.summaries(self.conn), {})

    def test_a_photo_needs_an_existing_mini(self):
        with self.assertRaises(photos.PhotoError):
            photos.save(self.conn, 99999, B64, 10, 10)

    def test_the_collection_lists_each_minis_photo(self):
        self.save(zoom=2)
        minis = collection.get(self.conn, self.entry["id"])["minis"]
        self.assertEqual(minis[0]["photo"]["zoom"], 2.0)
        self.assertIsNone(minis[1]["photo"])

    def test_removing_a_mini_removes_its_photo(self):
        self.save()
        copies.delete_mini(self.conn, self.mini)
        self.assertEqual(photos.summaries(self.conn), {})

    def test_lowering_the_quantity_keeps_a_mini_that_has_a_photo(self):
        self.save()
        collection.update(self.conn, self.entry["id"], qty=1)
        minis = collection.get(self.conn, self.entry["id"])["minis"]
        self.assertEqual([m["id"] for m in minis], [self.mini])
        self.assertIsNotNone(minis[0]["photo"])

    def test_remove(self):
        self.save()
        self.assertTrue(photos.remove(self.conn, self.mini))
        self.assertFalse(photos.remove(self.conn, self.mini))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "t.db")
        conn = db.connect(path)
        with conn:
            conn.execute(
                "INSERT INTO units (uuid, mul_id, chassis, model, kind, type, subtype, tons, weight_class, bv, pv, year, tech_base, role) "
                "VALUES ('u-1', 1, 'Atlas', 'AS7-D', 'mech', 't', 's', 100, 'w', 1897, 10, 3025, 'Inner Sphere', 'r')")
        entry = collection.add(conn, "u-1", qty=1)
        cls.mini = collection.get(conn, entry["id"])["minis"][0]["id"]
        conn.close()
        cls.httpd = server.make_server(port=0, db_path=path, art_dir=cls.tmp.name)
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
        data = res.read()
        conn.close()
        try:
            return res.status, json.loads(data)
        except ValueError:
            return res.status, data

    def test_upload_crop_show_and_remove(self):
        url = f"/api/minis/{self.mini}/photo"
        self.assertEqual(self.request("GET", url)[0], 404)
        status, saved = self.request("PUT", url, {"image": B64, "width": 800, "height": 600, "zoom": 2, "cx": 0.3, "cy": 0.6})
        self.assertEqual((status, saved["zoom"]), (200, 2.0))
        self.assertEqual(self.request("GET", url), (200, JPEG))
        status, saved = self.request("PATCH", url, {"zoom": 1.5, "cx": 0.5, "cy": 0.5})
        self.assertEqual((status, saved["zoom"]), (200, 1.5))
        listed = self.request("GET", "/api/collection")[1][0]["minis"][0]
        self.assertEqual(listed["photo"]["zoom"], 1.5)
        self.assertEqual(self.request("DELETE", url), (200, {"removed": True}))
        self.assertEqual(self.request("DELETE", url)[0], 404)
        self.assertEqual(self.request("PATCH", url, {"zoom": 1, "cx": 0.5, "cy": 0.5})[0], 404)

    def test_bad_requests(self):
        url = f"/api/minis/{self.mini}/photo"
        self.assertEqual(self.request("PUT", url, {"image": "zzz", "width": 1, "height": 1})[0], 400)
        self.assertEqual(self.request("PUT", url, {"image": B64, "width": 1, "height": 1, "zoom": 99})[0], 400)
        self.assertEqual(self.request("PUT", "/api/minis/99999/photo", {"image": B64, "width": 1, "height": 1})[0], 400)

    def test_an_oversized_body_is_refused(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("PUT", f"/api/minis/{self.mini}/photo", body=b"{}", headers={
            "Content-Type": "application/json", "Content-Length": str(server.MAX_BODY_BYTES + 1)})
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()


if __name__ == "__main__":
    unittest.main()
