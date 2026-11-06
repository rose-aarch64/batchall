import json
import tempfile
import unittest
from pathlib import Path

from support import make_db

from batchall import catalog, db, details

MAD_CAT = {
    "name": "BMMadCat_Prime",
    "uuid": "mc",
    "chassis": "Mad Cat (Timber Wolf)",
    "model": "Prime",
    "type": "Mek",
    "subtype": "BattleMek Omni",
    "bv": 2737,
    "tons": 75.0,
    "year": 2945,
    "walk": 5,
    "run": 8,
    "jump": 0,
    "moveType": "Biped",
    "armor": 230,
    "armorType": "Ferro-Fibrous",
    "structureType": "Endo Steel",
    "internal": 114,
    "engine": "XL (Clan)",
    "engineRating": 375.0,
    "heat": 50,
    "dissipation": 34,
    "quirks": ["Improved Targeting (Medium)"],
    "techRating": "F/X-X-E-D",
    "level": "Standard",
    "cost": 24233125,
    "crewSize": 1,
    "comp": [
        {"id": "gyro", "n": "Standard Gyro", "q": 1, "l": "CT"},
        {"id": "CLERLargeLaser", "n": "ER Large Laser", "q": 1, "l": "LA", "r": "8/15/25", "d": "10"},
        {"id": "CLERLargeLaser", "n": "ER Large Laser", "q": 1, "l": "RA", "r": "8/15/25", "d": "10"},
        {"id": "CLMG", "n": "Machine Gun", "q": 1, "l": "CT", "r": "1/2/3", "d": "2"},
        {"id": "CLMG", "n": "Machine Gun", "q": 1, "l": "CT", "r": "1/2/3", "d": "2"},
        {"id": "CLLRM20", "n": "LRM 20", "q": 1, "l": "LT", "r": "7/14/21", "d": "1/Msl"},
        {"id": "Clan Ammo LRM-20", "n": "LRM 20 Ammo", "q": 1, "l": "LT"},
    ],
}


class SpecsTests(unittest.TestCase):
    def test_movement_defense_and_engine_come_through(self):
        specs = details.extract_specs(MAD_CAT)
        self.assertEqual(specs["movement"], {"walk": 5, "run": 8, "jump": 0, "type": "Biped"})
        self.assertEqual(specs["armor"], {"points": 230, "type": "Ferro-Fibrous"})
        self.assertEqual(specs["structure"], {"type": "Endo Steel", "internal": 114})
        self.assertEqual(specs["engine"], {"type": "XL (Clan)", "rating": 375})
        self.assertEqual(specs["heat"], {"generated": 50, "dissipation": 34})
        self.assertEqual(specs["quirks"], ["Improved Targeting (Medium)"])

    def test_weapons_are_the_entries_with_a_range_and_ammo_is_left_out(self):
        names = [w["name"] for w in details.extract_specs(MAD_CAT)["weapons"]]
        self.assertEqual(sorted(names), ["ER Large Laser", "ER Large Laser", "LRM 20", "Machine Gun"])

    def test_same_weapon_in_same_location_is_one_row_with_a_count(self):
        weapons = details.extract_specs(MAD_CAT)["weapons"]
        mg = [w for w in weapons if w["name"] == "Machine Gun"]
        self.assertEqual(mg, [{"name": "Machine Gun", "location": "CT", "qty": 2, "damage": "2", "range": "1/2/3"}])
        lasers = sorted(w["location"] for w in weapons if w["name"] == "ER Large Laser")
        self.assertEqual(lasers, ["LA", "RA"])

    def test_a_record_with_missing_fields_still_works(self):
        specs = details.extract_specs({"uuid": "x"})
        self.assertEqual(specs["weapons"], [])
        self.assertEqual(specs["quirks"], [])
        self.assertIsNone(specs["movement"]["walk"])


class LoreTextTests(unittest.TestCase):
    def test_paragraphs_become_plain_text_with_entities_decoded(self):
        self.assertEqual(details.html_to_paragraphs("<p>Tom &amp; Jerry</p><p>Second</p>"), ["Tom & Jerry", "Second"])

    def test_scripts_styles_and_tags_never_survive(self):
        html = '<p>Safe <b>bold</b></p><script>alert(1)</script><style>p{}</style><p><a href="x">link</a></p>'
        self.assertEqual(details.html_to_paragraphs(html), ["Safe bold", "link"])

    def test_empty_and_non_strings_give_no_paragraphs(self):
        for value in (None, "", "   ", 5, []):
            self.assertEqual(details.html_to_paragraphs(value), [])

    def test_list_items_and_line_breaks_split_paragraphs(self):
        self.assertEqual(details.html_to_paragraphs("<ul><li>One</li><li>Two</li></ul>a<br>b"), ["One", "Two", "a", "b"])

    def test_lore_collects_the_text_sections_and_credits(self):
        entry = {
            "overview": "<p>Big cat.</p>",
            "history": "<p>Old.</p>",
            "manufacturer": "Strana Mechty",
            "primaryFactory": "Strana Mechty",
            "capabilities": "",
            "img": "Mek/x.png",
        }
        lore = details.extract_lore(entry)
        self.assertEqual(lore["overview"], ["Big cat."])
        self.assertEqual(lore["history"], ["Old."])
        self.assertEqual(lore["manufacturer"], "Strana Mechty")
        self.assertNotIn("capabilities", lore)
        self.assertNotIn("img", lore)


class LoadAndGetTests(unittest.TestCase):
    def write_catalog(self, folder, units, fluff=None):
        folder = Path(folder)
        (folder / "units.json").write_text(json.dumps({"version": 1, "units": units}), encoding="utf-8")
        (folder / "factions.json").write_text(json.dumps({"factions": []}), encoding="utf-8")
        (folder / "eras.json").write_text(json.dumps({"eras": []}), encoding="utf-8")
        if fluff is not None:
            (folder / "units-fluff.json").write_text(json.dumps({"version": 1, "fluff": fluff}), encoding="utf-8")

    def load(self, units, fluff=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.write_catalog(tmp.name, units, fluff)
        conn = db.connect(":memory:")
        catalog.load(conn, tmp.name)
        return conn

    def test_lore_is_matched_to_the_unit_by_its_catalog_name(self):
        conn = self.load([MAD_CAT], {"BMMadCat_Prime": {"overview": "<p>Big cat.</p>"}})
        got = details.get(conn, "mc")
        self.assertEqual(got["lore"]["overview"], ["Big cat."])
        self.assertEqual(got["specs"]["movement"]["walk"], 5)
        self.assertEqual(got["name"], "Mad Cat (Timber Wolf) Prime")
        self.assertEqual(got["bv"], 2737)
        self.assertTrue(got["has_details"])

    def test_import_without_the_lore_file_still_gives_specs(self):
        conn = self.load([MAD_CAT])
        got = details.get(conn, "mc")
        self.assertEqual(got["lore"], {})
        self.assertEqual(got["specs"]["armor"]["points"], 230)

    def test_reimport_replaces_old_details(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = db.connect(":memory:")
        self.write_catalog(tmp.name, [MAD_CAT], {"BMMadCat_Prime": {"overview": "<p>Old.</p>"}})
        catalog.load(conn, tmp.name)
        self.write_catalog(tmp.name, [MAD_CAT], {"BMMadCat_Prime": {"overview": "<p>New.</p>"}})
        catalog.load(conn, tmp.name)
        self.assertEqual(details.get(conn, "mc")["lore"]["overview"], ["New."])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM unit_details").fetchone()[0], 1)

    def test_unit_imported_before_this_feature_has_basics_but_no_details(self):
        conn = make_db()
        got = details.get(conn, "u-atlas")
        self.assertEqual(got["name"], "Atlas AS7-D")
        self.assertFalse(got["has_details"])
        self.assertEqual(got["specs"], {})
        self.assertEqual(got["lore"], {})

    def test_unknown_unit_is_none(self):
        self.assertIsNone(details.get(make_db(), "nope"))


class DownloadTests(unittest.TestCase):
    def fake_fetch(self, fail_fluff=False):
        calls = []

        def fetch(url, timeout=300):
            calls.append(url)
            if url.endswith("units-fluff.json"):
                if fail_fluff:
                    raise OSError("lore host down")
                return b'{"version": 1, "fluff": {}}'
            return b'{"units": [], "factions": [], "eras": []}'

        return fetch, calls

    def run_download(self, fail_fluff):
        from unittest import mock

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fetch, calls = self.fake_fetch(fail_fluff)
        messages = []
        with mock.patch.object(catalog, "_fetch", fetch):
            catalog.download(tmp.name, base_url="https://example.test", progress=messages.append)
        return Path(tmp.name), calls, messages

    def test_lore_file_is_downloaded_with_the_others(self):
        folder, calls, _ = self.run_download(fail_fluff=False)
        self.assertTrue((folder / "units-fluff.json").exists())
        self.assertIn("https://example.test/units-fluff.json", calls)

    def test_a_failed_lore_download_does_not_fail_the_import_and_says_so(self):
        folder, _, messages = self.run_download(fail_fluff=True)
        for name in ("units", "factions", "eras"):
            self.assertTrue((folder / f"{name}.json").exists(), name)
        self.assertFalse((folder / "units-fluff.json").exists())
        self.assertTrue(any("lore" in m and "skipp" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
