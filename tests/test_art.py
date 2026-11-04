import json
import os
import tempfile
import unittest
from pathlib import Path

from support import make_db

from bt import art, catalog, collection

PNG = b"\x89PNG\r\n\x1a\n" + b"fake png body"
JPG = b"\xff\xd8\xff\xe0" + b"fake jpeg body"


class FakeFetch:
    def __init__(self, data=PNG, error=None):
        self.data, self.error, self.calls = data, error, []

    def __call__(self, path):
        self.calls.append(path)
        if self.error:
            raise self.error
        return self.data


def set_art(conn, uuid, path):
    conn.execute("UPDATE units SET art = ? WHERE uuid = ?", (path, uuid))
    conn.commit()


class CatalogArtTests(unittest.TestCase):
    def write_catalog(self, folder, units):
        folder = Path(folder)
        (folder / "units.json").write_text(json.dumps({"version": 1, "units": units}), encoding="utf-8")
        (folder / "factions.json").write_text(json.dumps({"factions": []}), encoding="utf-8")
        (folder / "eras.json").write_text(json.dumps({"eras": []}), encoding="utf-8")

    def unit(self, uuid, fluff=None):
        d = {"uuid": uuid, "chassis": "C" + uuid, "model": "M", "type": "Mek", "subtype": "BattleMek", "bv": 100}
        if fluff is not None:
            d["fluff"] = fluff
        return d

    def test_art_path_is_stored_and_unsafe_ones_are_dropped(self):
        units = [
            self.unit("ok", {"img": "Mek/Atlas.png"}),
            self.unit("none"),
            self.unit("empty", {"img": "  "}),
            self.unit("dots", {"img": "../secret.png"}),
            self.unit("nested-dots", {"img": "Mek/../../x.png"}),
            self.unit("absolute", {"img": "/etc/passwd"}),
            self.unit("backslash", {"img": "Mek\\x.png"}),
            self.unit("wrong-type", {"img": 5}),
            self.unit("not-a-dict", "Mek/Atlas.png"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            self.write_catalog(tmp, units)
            from bt import db

            conn = db.connect(":memory:")
            catalog.load(conn, tmp)
        stored = {r["uuid"]: r["art"] for r in conn.execute("SELECT uuid, art FROM units")}
        self.assertEqual(stored["ok"], "Mek/Atlas.png")
        for key in ("none", "empty", "dots", "nested-dots", "absolute", "backslash", "wrong-type", "not-a-dict"):
            self.assertIsNone(stored[key], key)

    def test_callers_see_has_art_but_never_the_path(self):
        conn = make_db()
        set_art(conn, "u-atlas", "Mek/Atlas.png")
        entry = collection.add(conn, "u-atlas")
        self.assertIs(entry["has_art"], True)
        self.assertNotIn("art", entry)
        plain = collection.add(conn, "u-locust")
        self.assertIs(plain["has_art"], False)
        found = catalog.search(conn, "atlas")[0]
        self.assertIs(found["has_art"], True)
        self.assertNotIn("art", found)


class PathRulesTests(unittest.TestCase):
    def test_safe_paths(self):
        self.assertEqual(art.safe_path(" Mek/Atlas.png "), "Mek/Atlas.png")
        for bad in ("", "  ", "/abs.png", "../x.png", "a/../b.png", "a\\b.png", "a\x00b.png", None, 5):
            with self.assertRaises(art.ArtError, msg=repr(bad)):
                art.safe_path(bad)

    def test_download_rejects_unsafe_paths_before_any_request(self):
        with self.assertRaises(art.ArtError):
            art.download("../../etc/passwd")

    def test_sniff(self):
        self.assertEqual(art.sniff(PNG), (".png", "image/png"))
        self.assertEqual(art.sniff(JPG), (".jpg", "image/jpeg"))
        self.assertIsNone(art.sniff(b"<html>not an image</html>"))
        self.assertIsNone(art.sniff(b""))


class CacheTests(unittest.TestCase):
    def setUp(self):
        art.forget_failures()
        self.conn = make_db()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = self.tmp.name

    def test_downloads_once_then_serves_from_disk(self):
        set_art(self.conn, "u-atlas", "Mek/Atlas.png")
        fetch = FakeFetch()
        self.assertEqual(art.get_art(self.conn, "u-atlas", self.cache, fetch), (PNG, "image/png"))
        self.assertEqual(art.get_art(self.conn, "u-atlas", self.cache, fetch), (PNG, "image/png"))
        self.assertEqual(fetch.calls, ["Mek/Atlas.png"])

    def test_variants_sharing_a_picture_share_one_download_and_file(self):
        set_art(self.conn, "u-atlas", "Mek/Atlas.png")
        set_art(self.conn, "u-marauder", "Mek/Atlas.png")
        fetch = FakeFetch()
        art.get_art(self.conn, "u-atlas", self.cache, fetch)
        art.get_art(self.conn, "u-marauder", self.cache, fetch)
        self.assertEqual(len(fetch.calls), 1)
        self.assertEqual(len(list(Path(self.cache).glob("*"))), 1)

    def test_jpeg_keeps_its_content_type(self):
        set_art(self.conn, "u-atlas", "Mek/Atlas.jpg")
        self.assertEqual(art.get_art(self.conn, "u-atlas", self.cache, FakeFetch(JPG)), (JPG, "image/jpeg"))
        self.assertEqual(art.get_art(self.conn, "u-atlas", self.cache, FakeFetch(error=OSError()))[1], "image/jpeg")

    def test_unit_without_art_or_unknown_unit(self):
        fetch = FakeFetch()
        self.assertIsNone(art.get_art(self.conn, "u-atlas", self.cache, fetch))
        self.assertIsNone(art.get_art(self.conn, "no-such-unit", self.cache, fetch))
        self.assertEqual(fetch.calls, [])

    def test_failure_is_remembered_then_retried_later(self):
        set_art(self.conn, "u-atlas", "Mek/Atlas.png")
        broken = FakeFetch(error=OSError("offline"))
        self.assertIsNone(art.get_art(self.conn, "u-atlas", self.cache, broken))
        self.assertIsNone(art.get_art(self.conn, "u-atlas", self.cache, broken))
        self.assertEqual(len(broken.calls), 1)  # the second request did not hit the network
        art.forget_failures()
        self.assertEqual(art.get_art(self.conn, "u-atlas", self.cache, FakeFetch()), (PNG, "image/png"))

    def test_non_image_response_is_not_cached(self):
        set_art(self.conn, "u-atlas", "Mek/Atlas.png")
        self.assertIsNone(art.get_art(self.conn, "u-atlas", self.cache, FakeFetch(b"<html>oops</html>")))
        self.assertEqual(list(Path(self.cache).glob("*")), [])

    def test_no_partial_files_are_left_behind(self):
        set_art(self.conn, "u-atlas", "Mek/Atlas.png")
        art.get_art(self.conn, "u-atlas", self.cache, FakeFetch())
        self.assertEqual([p.suffix for p in Path(self.cache).glob("*")], [".png"])

    def test_cache_file_names_do_not_depend_on_the_path_text(self):
        set_art(self.conn, "u-atlas", "Mek/Atlas Prime (v2).png")
        art.get_art(self.conn, "u-atlas", self.cache, FakeFetch())
        name = next(Path(self.cache).glob("*")).name
        self.assertRegex(name, r"^[0-9a-f]{40}\.png$")


class StatusAndPrefetchTests(unittest.TestCase):
    def setUp(self):
        art.forget_failures()
        self.conn = make_db()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for uuid, path in (("u-atlas", "Mek/Atlas.png"), ("u-marauder", "Mek/Atlas.png"), ("u-locust", "Mek/Locust.png")):
            set_art(self.conn, uuid, path)
            collection.add(self.conn, uuid)
        collection.add(self.conn, "u-condor")  # owned, no art

    def test_status_counts_distinct_images(self):
        self.assertEqual(
            {k: v for k, v in art.status(self.conn, self.tmp.name).items() if k != "cache_bytes"},
            {"owned_units": 4, "units_with_art": 3, "images": 2, "cached": 0, "missing": 2},
        )

    def test_prefetch_downloads_each_image_once_and_reports_failures(self):
        fetch = FakeFetch()
        result = art.prefetch(self.conn, self.tmp.name, fetch)
        self.assertEqual((result["requested"], result["fetched"], result["failed"]), (2, 2, []))
        self.assertEqual(sorted(fetch.calls), ["Mek/Atlas.png", "Mek/Locust.png"])
        self.assertEqual(art.status(self.conn, self.tmp.name)["missing"], 0)
        again = FakeFetch()
        self.assertEqual(art.prefetch(self.conn, self.tmp.name, again)["requested"], 0)
        self.assertEqual(again.calls, [])

    def test_prefetch_lists_what_failed(self):
        result = art.prefetch(self.conn, self.tmp.name, FakeFetch(error=OSError("offline")))
        self.assertEqual((result["fetched"], sorted(result["failed"])), (0, ["Atlas", "Locust"]))


if __name__ == "__main__":
    unittest.main()
