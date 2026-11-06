"""Company logos: Sarna lookup, the confirm-then-save step, storage and the API."""

import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from batchall import db, logos, server

PNG = b"\x89PNG\r\n\x1a\n" + b"fake logo"
JPEG = b"\xff\xd8\xff\xe0" + b"fake photo"
THUMB = "https://cfw.sarna.net/wiki/images/thumb/e/e7/Eridani_Light_Horse_logo_3062.jpg/256px-Eridani_Light_Horse_logo_3062.jpg"
THUMB2 = "https://cfw.sarna.net/wiki/images/thumb/0/0a/Other.png/256px-Other.png"


def page(title, thumb=None, index=None):
    p = {"title": title}
    if thumb:
        p["thumbnail"] = {"source": thumb}
    if index is not None:
        p["index"] = index
    return p


class FakeSarna:
    """Answers the two lookups: one by exact title, one by search."""

    def __init__(self, exact=(), hits=()):
        self.exact, self.hits, self.urls = list(exact), list(hits), []

    def __call__(self, url):
        self.urls.append(url)
        pages = self.hits if "generator=search" in url else self.exact
        return {"query": {"pages": {str(i): p for i, p in enumerate(pages)}}}


class CandidateTests(unittest.TestCase):
    def test_exact_page_comes_first_then_search_hits_without_repeats(self):
        sarna = FakeSarna(
            exact=[page("Eridani Light Horse", THUMB)],
            hits=[page("Eridani Light Horse (3062)", THUMB2, index=2), page("Eridani Light Horse", THUMB, index=1)])
        found = logos.candidates("Eridani Light Horse", fetch=sarna)
        self.assertEqual([c["title"] for c in found], ["Eridani Light Horse", "Eridani Light Horse (3062)"])
        self.assertEqual(found[0]["image_url"], THUMB)
        self.assertEqual(found[0]["page_url"], "https://www.sarna.net/wiki/Eridani_Light_Horse")

    def test_pages_without_an_image_or_with_a_foreign_image_are_dropped(self):
        sarna = FakeSarna(
            exact=[page("No image"), page("Elsewhere", "https://evil.example/wiki/images/x.png")],
            hits=[page("Wrong folder", "https://cfw.sarna.net/other/x.png")])
        self.assertEqual(logos.candidates("anything", fetch=sarna), [])

    def test_search_hits_are_ordered_best_first_and_capped(self):
        hits = [page(f"Hit {i}", THUMB2, index=i) for i in (5, 3, 4, 1, 2, 6)]
        found = logos.candidates("hit", fetch=FakeSarna(hits=hits))
        self.assertEqual([c["title"] for c in found], ["Hit 1", "Hit 2", "Hit 3", "Hit 4"])

    def test_the_name_is_url_encoded_and_a_blank_name_is_refused(self):
        sarna = FakeSarna()
        logos.candidates("Wolf's Dragoons & Co", fetch=sarna)
        self.assertTrue(all("Wolf%27s+Dragoons+%26+Co" in u for u in sarna.urls))
        # without action=query the wiki answers with its HTML help page
        self.assertTrue(all(u.startswith(logos.SARNA_API + "?") and "action=query" in u and "format=json" in u for u in sarna.urls))
        with self.assertRaises(logos.LogoError):
            logos.candidates("   ", fetch=sarna)


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def save(self, data=PNG, name="Eridani Light Horse", url=THUMB):
        return logos.save(self.conn, name, url, source_title="Eridani Light Horse", fetch=lambda u: data)

    def test_saved_logo_is_found_by_name_ignoring_case_and_space(self):
        self.save()
        self.assertEqual(logos.get(self.conn, "  eridani LIGHT horse "), (PNG, "image/png"))
        self.assertIsNone(logos.get(self.conn, "Raptor Keshik"))

    def test_saving_again_replaces_the_logo(self):
        self.save(PNG)
        self.save(JPEG)
        self.assertEqual(logos.get(self.conn, "Eridani Light Horse"), (JPEG, "image/jpeg"))
        self.assertEqual(len(logos.list_all(self.conn)), 1)

    def test_list_marks_jpeg_logos_opaque(self):
        self.save(JPEG, name="A")
        self.save(PNG, name="B")
        flags = {r["key"]: r["opaque"] for r in logos.list_all(self.conn)}
        self.assertEqual(flags, {"a": True, "b": False})

    def test_png_without_alpha_is_opaque_and_png_with_alpha_is_not(self):
        def png(color_type):  # signature, IHDR length and name, 4+4 size bytes, bit depth, color type
            return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + b"\x00\x00\x00\x10\x00\x00\x00\x10" + b"\x08" + bytes([color_type]) + b"\x00" * 8
        for name, color_type in (("gray", 0), ("rgb", 2), ("palette", 3), ("gray alpha", 4), ("rgba", 6)):
            self.save(png(color_type), name=name)
        flags = {r["key"]: r["opaque"] for r in logos.list_all(self.conn)}
        self.assertEqual(flags, {"gray": True, "rgb": True, "palette": False, "gray alpha": False, "rgba": False})

    def test_non_images_and_unsafe_addresses_are_refused(self):
        with self.assertRaises(logos.LogoError):
            self.save(b"<html>not an image</html>")
        for url in ("http://cfw.sarna.net/wiki/images/x.png", "https://evil.example/wiki/images/x.png",
                    "https://cfw.sarna.net/wiki/images/../x.png", "https://sarna.net.evil.example/wiki/images/x.png", None):
            with self.assertRaises(logos.LogoError, msg=url):
                logos.save(self.conn, "X", url, fetch=lambda u: PNG)
        with self.assertRaises(logos.LogoError):
            self.save(name="  ")
        self.assertEqual(logos.list_all(self.conn), [])

    def test_plain_svg_is_kept_and_scripted_svg_is_refused(self):
        plain = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>'
        self.save(plain)
        self.assertEqual(logos.get(self.conn, "Eridani Light Horse"), (plain, "image/svg+xml"))
        for bad in (b'<svg xmlns="x"><script>alert(1)</script></svg>', b'<svg onload="alert(1)"></svg>',
                    b'<svg><foreignObject/></svg>', b'<svg><a href="javascript:alert(1)"/></svg>', b"<html><svg></svg></html>"):
            with self.assertRaises(logos.LogoError, msg=bad):
                self.save(bad, name="Other")

    def test_remove(self):
        self.save()
        self.assertTrue(logos.remove(self.conn, "eridani light horse"))
        self.assertFalse(logos.remove(self.conn, "eridani light horse"))
        self.assertIsNone(logos.get(self.conn, "Eridani Light Horse"))


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
        data = res.read()
        conn.close()
        try:
            return res.status, json.loads(data)
        except ValueError:
            return res.status, data

    def test_find_confirm_show_and_remove(self):
        sarna = FakeSarna(exact=[page("Eridani Light Horse", THUMB)])
        with mock.patch.object(logos, "fetch_json", sarna):
            status, found = self.request("GET", "/api/company-logos/candidates?name=Eridani+Light+Horse")
        self.assertEqual((status, found[0]["image_url"]), (200, THUMB))
        # looking things up saves nothing
        self.assertEqual(self.request("GET", "/api/company-logos"), (200, []))
        self.assertEqual(self.request("GET", "/api/company-logos/image?name=Eridani+Light+Horse")[0], 404)

        with mock.patch.object(logos, "fetch_image", lambda url: PNG):
            status, saved = self.request("PUT", "/api/company-logos", {"name": "Eridani Light Horse", "image_url": THUMB})
        self.assertEqual((status, saved["key"]), (200, "eridani light horse"))
        self.assertEqual(self.request("GET", "/api/company-logos/image?name=eridani+light+horse"), (200, PNG))
        self.assertEqual(len(self.request("GET", "/api/company-logos")[1]), 1)

        self.assertEqual(self.request("DELETE", "/api/company-logos?name=Eridani+Light+Horse"), (200, {"removed": True}))
        self.assertEqual(self.request("DELETE", "/api/company-logos?name=Eridani+Light+Horse")[0], 404)

    def test_bad_requests_and_a_down_wiki(self):
        self.assertEqual(self.request("GET", "/api/company-logos/candidates")[0], 400)
        self.assertEqual(self.request("PUT", "/api/company-logos", {"name": "X", "image_url": "https://evil.example/a.png"})[0], 400)
        self.assertEqual(self.request("PUT", "/api/company-logos", {"image_url": THUMB})[0], 400)

        def down(url):
            raise OSError("no route to host")

        with mock.patch.object(logos, "fetch_json", down):
            self.assertEqual(self.request("GET", "/api/company-logos/candidates?name=X")[0], 502)


if __name__ == "__main__":
    unittest.main()
