"""Faction logos: stored from the catalog, downloaded from MekBay's main site once, and cached on disk."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import make_db

from batchall import art, availability, catalog, config, db

ROOT = Path(__file__).resolve().parent.parent
PNG = b"\x89PNG\r\n\x1a\n" + b"fake logo"
GOOD = "/images/factions/wolfs-dragoons-100.png"


class FakeFetch:
    def __init__(self, data=PNG, error=None):
        self.data, self.error, self.calls = data, error, []

    def __call__(self, path):
        self.calls.append(path)
        if self.error:
            raise self.error
        return self.data


def set_img(conn, faction_id, img):
    conn.execute("UPDATE factions SET img = ? WHERE id = ?", (img, faction_id))
    conn.commit()


def write_factions(folder, records):
    (Path(folder) / "factions.json").write_text(json.dumps({"factions": records}), encoding="utf-8")


class StoredPathTests(unittest.TestCase):
    def write_catalog(self, folder, factions):
        folder = Path(folder)
        (folder / "units.json").write_text(json.dumps({"version": 1, "units": []}), encoding="utf-8")
        (folder / "eras.json").write_text(json.dumps({"eras": []}), encoding="utf-8")
        write_factions(folder, factions)

    def test_import_stores_each_factions_logo_path_and_drops_unsafe_ones(self):
        factions = [
            {"id": 1, "name": "Good", "group": "IS", "img": GOOD},
            {"id": 2, "name": "No image", "group": "IS"},
            {"id": 3, "name": "Dots", "group": "IS", "img": "/images/factions/../secret.png"},
            {"id": 4, "name": "Elsewhere", "group": "IS", "img": "/etc/passwd"},
            {"id": 5, "name": "Other host", "group": "IS", "img": "https://evil.example/x.png"},
            {"id": 6, "name": "Not a png", "group": "IS", "img": "/images/factions/x.svg"},
            {"id": 7, "name": "Wrong type", "group": "IS", "img": 5},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            self.write_catalog(tmp, factions)
            conn = db.connect(":memory:")
            catalog.load(conn, tmp)
        stored = {r["id"]: r["img"] for r in conn.execute("SELECT id, img FROM factions")}
        self.assertEqual(stored, {1: GOOD, 2: "", 3: "", 4: "", 5: "", 6: "", 7: ""})

    def test_listed_factions_say_whether_they_have_a_logo_but_never_the_path(self):
        conn = make_db()
        set_img(conn, 1, GOOD)
        listed = {f["id"]: f for f in availability.list_factions(conn)}
        self.assertTrue(listed[1]["has_icon"])
        self.assertFalse(listed[2]["has_icon"])
        self.assertNotIn("img", listed[1])
        self.assertNotIn(GOOD, json.dumps(listed))


class FillMissingTests(unittest.TestCase):
    """Databases from before logos were stored get their paths from the downloaded factions.json."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = make_db()  # factions 1 and 2 with no logo path yet

    def fill(self):
        return catalog.fill_missing_faction_icons(self.conn, self.tmp.name)

    def test_column_exists_and_starts_empty_on_old_databases(self):
        old = tempfile.TemporaryDirectory()
        self.addCleanup(old.cleanup)
        path = os.path.join(old.name, "old.db")
        raw = sqlite3.connect(path)
        raw.executescript("CREATE TABLE factions (id INTEGER PRIMARY KEY, name TEXT NOT NULL, grp TEXT);"
                          "INSERT INTO factions VALUES (1, 'Federated Suns', 'IS');")
        raw.commit()
        raw.close()
        for _ in range(2):  # the second open must not add the column again
            conn = db.connect(path)
            self.assertIsNone(conn.execute("SELECT img FROM factions").fetchone()["img"])
            conn.close()

    def test_fills_paths_from_the_catalog_file(self):
        write_factions(self.tmp.name, [{"id": 1, "name": "Federated Suns", "img": GOOD},
                                       {"id": 2, "name": "Clan Wolf"}])
        self.assertEqual(self.fill(), 2)
        stored = {r["id"]: r["img"] for r in self.conn.execute("SELECT id, img FROM factions")}
        self.assertEqual(stored, {1: GOOD, 2: ""})  # empty means checked, and it has none

    def test_does_not_change_a_path_that_is_already_set(self):
        set_img(self.conn, 1, "/images/factions/mine.png")
        write_factions(self.tmp.name, [{"id": 1, "name": "x", "img": GOOD}, {"id": 2, "name": "y", "img": GOOD}])
        self.fill()
        self.assertEqual(self.conn.execute("SELECT img FROM factions WHERE id = 1").fetchone()[0],
                         "/images/factions/mine.png")

    def test_without_a_catalog_file_it_does_nothing(self):
        self.assertEqual(self.fill(), 0)
        self.assertIsNone(self.conn.execute("SELECT img FROM factions WHERE id = 1").fetchone()[0])

    def test_a_second_call_does_not_read_the_file_again(self):
        write_factions(self.tmp.name, [{"id": 1, "name": "x", "img": GOOD}, {"id": 2, "name": "y", "img": GOOD}])
        self.fill()
        (Path(self.tmp.name) / "factions.json").write_text("not json at all", encoding="utf-8")
        self.assertEqual(self.fill(), 0)  # nothing is missing, so the broken file is never opened

    def test_a_broken_file_is_ignored_not_fatal(self):
        (Path(self.tmp.name) / "factions.json").write_text("not json at all", encoding="utf-8")
        self.assertEqual(self.fill(), 0)


class PathCheckTests(unittest.TestCase):
    def test_only_plain_faction_png_paths_are_accepted(self):
        for ok in (GOOD, "/images/factions/mercenary.png", "/images/factions/Clan_Wolf-2.png"):
            self.assertEqual(art.safe_faction_icon_path(ok), ok)
        for bad in ("", None, 5, "images/factions/x.png", "/images/factions/", "/images/factions/x.jpg",
                    "/images/factions/../x.png", "/images/factions/a/b.png", "/images/fluff/x.png",
                    "/images/factions/x.png\n", "https://mekbay.com/images/factions/x.png", "/images/factions/\\x.png"):
            with self.assertRaises(art.ArtError, msg=repr(bad)):
                art.safe_faction_icon_path(bad)

    def test_download_rejects_a_bad_path_before_any_request(self):
        with self.assertRaises(art.ArtError):
            art.download_faction_icon("/etc/passwd")

    def test_logos_come_from_the_main_site_not_the_data_host(self):
        self.assertEqual(config.MEKBAY_SITE_URL, "https://mekbay.com")


class IconCacheTests(unittest.TestCase):
    def setUp(self):
        art.forget_failures()
        self.conn = make_db()
        set_img(self.conn, 1, GOOD)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = self.tmp.name

    def test_downloads_once_then_serves_from_disk(self):
        fetch = FakeFetch()
        self.assertEqual(art.get_faction_icon(self.conn, 1, self.cache, fetch), (PNG, "image/png"))
        self.assertEqual(art.get_faction_icon(self.conn, 1, self.cache, fetch), (PNG, "image/png"))
        self.assertEqual(fetch.calls, [GOOD])

    def test_logos_are_kept_in_their_own_folder_apart_from_unit_art(self):
        art.get_faction_icon(self.conn, 1, self.cache, FakeFetch())
        self.assertEqual([p.name for p in Path(self.cache).glob("*") if p.is_dir()], ["factions"])
        self.assertEqual([p for p in Path(self.cache).glob("*") if p.is_file()], [])
        self.assertEqual(art.status(self.conn, self.cache)["cache_bytes"], 0)  # unit art counts are unchanged

    def test_a_faction_with_no_logo_or_an_unknown_id_has_none(self):
        fetch = FakeFetch()
        self.assertIsNone(art.get_faction_icon(self.conn, 2, self.cache, fetch))  # no path stored
        self.assertIsNone(art.get_faction_icon(self.conn, 999, self.cache, fetch))
        self.assertEqual(fetch.calls, [])

    def test_a_failed_download_is_remembered_then_retried_later(self):
        failing = FakeFetch(error=OSError("offline"))
        self.assertIsNone(art.get_faction_icon(self.conn, 1, self.cache, failing))
        self.assertIsNone(art.get_faction_icon(self.conn, 1, self.cache, failing))
        self.assertEqual(len(failing.calls), 1)
        art.forget_failures()
        self.assertEqual(art.get_faction_icon(self.conn, 1, self.cache, FakeFetch())[1], "image/png")

    def test_a_response_that_is_not_a_png_is_not_cached(self):
        self.assertIsNone(art.get_faction_icon(self.conn, 1, self.cache, FakeFetch(b"<html>nope</html>")))
        self.assertEqual(list((Path(self.cache) / "factions").glob("*")) if (Path(self.cache) / "factions").exists() else [], [])

    def test_a_stored_path_that_is_unsafe_is_never_fetched(self):
        set_img(self.conn, 1, "/etc/passwd")
        fetch = FakeFetch()
        self.assertIsNone(art.get_faction_icon(self.conn, 1, self.cache, fetch))
        self.assertEqual(fetch.calls, [])


class IconStatusAndPrefetchTests(unittest.TestCase):
    def setUp(self):
        art.forget_failures()
        self.conn = make_db()
        set_img(self.conn, 1, GOOD)
        set_img(self.conn, 2, "/images/factions/clan-wolf-100.png")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = self.tmp.name

    def test_status_counts_factions_with_a_logo_and_how_many_are_downloaded(self):
        self.assertEqual(art.faction_icon_status(self.conn, self.cache),
                         {"factions": 2, "with_icon": 2, "images": 2, "cached": 0, "missing": 2})
        art.get_faction_icon(self.conn, 1, self.cache, FakeFetch())
        self.assertEqual(art.faction_icon_status(self.conn, self.cache)["cached"], 1)

    def test_factions_that_share_a_logo_count_once_as_an_image(self):
        set_img(self.conn, 2, GOOD)  # both factions now use the same file
        fetch = FakeFetch()
        status = art.faction_icon_status(self.conn, self.cache)
        self.assertEqual((status["with_icon"], status["images"], status["missing"]), (2, 1, 1))
        art.prefetch_faction_icons(self.conn, self.cache, fetch)
        self.assertEqual(fetch.calls, [GOOD])  # one download serves both
        self.assertEqual(art.faction_icon_status(self.conn, self.cache)["missing"], 0)

    def test_prefetch_downloads_each_logo_once_and_reports_failures(self):
        fetch = FakeFetch()
        seen = []
        result = art.prefetch_faction_icons(self.conn, self.cache, fetch, progress=seen.append)
        self.assertEqual((result["requested"], result["fetched"], result["failed"]), (2, 2, []))
        self.assertEqual(sorted(fetch.calls), ["/images/factions/clan-wolf-100.png", GOOD])
        self.assertEqual(len(seen), 2)
        again = FakeFetch()
        self.assertEqual(art.prefetch_faction_icons(self.conn, self.cache, again)["requested"], 0)
        self.assertEqual(again.calls, [])

    def test_prefetch_names_what_failed(self):
        result = art.prefetch_faction_icons(self.conn, self.cache, FakeFetch(error=OSError("offline")))
        self.assertEqual((result["fetched"], sorted(result["failed"])), (0, ["Clan Wolf", "Federated Suns"]))


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, BATCHALL_DB=os.path.join(tmp, "c.db"), BATCHALL_DATA_DIR=tmp)
            return subprocess.run([sys.executable, "-m", "batchall", *args], cwd=ROOT, env=env,
                                  capture_output=True, text=True, timeout=30)

    def test_art_status_reports_the_logos_beside_the_unit_art(self):
        result = self.run_cli("art", "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertIn("owned_units", out)  # the unit art numbers are still there, unchanged
        self.assertEqual(out["faction_icons"], {"factions": 0, "with_icon": 0, "images": 0, "cached": 0, "missing": 0})

    def test_art_prefetch_reports_the_logos_beside_the_unit_art(self):
        result = self.run_cli("art", "prefetch")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertIn("requested", out)
        self.assertEqual(out["faction_icons"], {"requested": 0, "fetched": 0, "failed": []})


if __name__ == "__main__":
    unittest.main()
