import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import make_db, own

from batchall import builder, collection, db, export, forces

ROOT = Path(__file__).resolve().parent.parent


def add_unit(conn, uuid, mul, chassis, model, bv, kind="mech"):
    with conn:
        conn.execute(
            "INSERT INTO units (uuid, mul_id, chassis, model, kind, type, subtype, tons, weight_class, "
            "bv, pv, year, tech_base, role) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, mul, chassis, model, kind, "t", "s", 100, "w", bv, 10, 3025, "Inner Sphere", "Brawler"),
        )


class ForceVariantTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_unit(self.conn, "u-atlas-k", 20, "Atlas", "AS7-K", 1700)
        add_unit(self.conn, "u-atlas-s", 21, "Atlas", "AS7-S", 2100)
        add_unit(self.conn, "u-tank", 23, "Atlas", "Tank", 100, kind="vehicle")
        self.atlas = own(self.conn, "u-atlas", qty=2)["id"]
        self.locust = own(self.conn, "u-locust")["id"]

    def save(self, units, **kw):
        return forces.save(self.conn, "Test", "lance", units, **kw)

    def test_a_mini_fielded_as_another_variant_takes_that_variants_stats(self):
        f = self.save([{"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas-s"}])
        u = f["units"][0]
        self.assertEqual(u["name"], "Atlas AS7-S")
        self.assertEqual(u["bv"], 2100)
        self.assertEqual(u["unit_uuid"], "u-atlas-s")
        self.assertEqual(u["owned_as"], "Atlas AS7-D")
        self.assertEqual(u["collection_id"], self.atlas)
        self.assertEqual(f["total_bv"], 2100)

    def test_one_mini_entry_can_appear_as_two_variants(self):
        f = self.save([
            {"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas-s"},
            {"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas-k"},
        ])
        self.assertEqual(sorted(u["name"] for u in f["units"]), ["Atlas AS7-K", "Atlas AS7-S"])
        self.assertEqual(f["total_bv"], 3800)
        self.assertEqual(f["count"], 2)
        self.assertEqual(f["warnings"], [])

    def test_the_variant_owned_is_stored_as_owned(self):
        f = self.save([{"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas"}])
        self.assertNotIn("owned_as", f["units"][0])
        self.assertEqual(self.conn.execute("SELECT as_uuid FROM force_units").fetchone()[0], "")

    def test_leaving_the_variant_out_works_as_before(self):
        f = self.save([{"collection_id": self.atlas, "qty": 2}])
        self.assertEqual((f["units"][0]["name"], f["units"][0]["qty"]), ("Atlas AS7-D", 2))

    def test_same_variant_listed_twice_adds_up(self):
        f = self.save([
            {"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas-s"},
            {"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas-s"},
        ])
        self.assertEqual(len(f["units"]), 1)
        self.assertEqual(f["units"][0]["qty"], 2)

    def test_a_variant_of_another_chassis_is_refused(self):
        with self.assertRaises(forces.ForceError):
            self.save([{"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-wolfhound"}])

    def test_a_variant_of_another_kind_is_refused(self):
        with self.assertRaises(forces.ForceError):
            self.save([{"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-tank"}])

    def test_using_more_minis_than_owned_warns_across_variants(self):
        f = self.save([
            {"collection_id": self.atlas, "qty": 2, "unit_uuid": "u-atlas-s"},
            {"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas-k"},
        ])
        self.assertEqual(len(f["warnings"]), 1)
        self.assertIn("uses 3 but only 2 owned", f["warnings"][0])

    def test_replacing_a_force_replaces_its_variants(self):
        first = self.save([{"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas-s"}])
        again = forces.save(
            self.conn, "Test", "lance", [{"collection_id": self.locust, "qty": 1}], force_id=first["id"]
        )
        self.assertEqual([u["name"] for u in again["units"]], ["Locust LCT-1V"])

    def test_the_mekbay_link_uses_the_variant_fielded(self):
        f = self.save([{"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas-s"}])
        self.assertIn("units=u-atlas-s", export.mekbay_link(f))
        mul = export.mul_file(f)["content"]
        self.assertIn('model="AS7-S"', mul)

    def test_a_variant_removed_from_the_catalog_falls_back_with_a_warning(self):
        f = self.save([{"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas-s"}])
        with self.conn:
            self.conn.execute("PRAGMA foreign_keys = OFF")
            self.conn.execute("DELETE FROM units WHERE uuid = 'u-atlas-s'")
        again = forces.get(self.conn, f["id"])
        self.assertEqual(again["units"][0]["name"], "Atlas AS7-D")
        self.assertTrue(any("gone from the catalog" in w for w in again["warnings"]))

    def test_swapping_the_owned_variant_keeps_the_force_variants(self):
        f = self.save([
            {"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas-s"},
            {"collection_id": self.atlas, "qty": 1},
        ])
        other = own(self.conn, "u-atlas-k")["id"]
        collection.swap(self.conn, self.atlas, "u-atlas-k")  # merges into the entry that owns the AS7-K
        again = forces.get(self.conn, f["id"])
        names = sorted(u["name"] for u in again["units"])
        self.assertEqual(names, ["Atlas AS7-K", "Atlas AS7-S"])
        self.assertTrue(all(u["collection_id"] == other for u in again["units"]))

    def test_swap_into_an_entry_already_in_the_force_adds_quantities(self):
        other = own(self.conn, "u-atlas-k")["id"]
        f = self.save([
            {"collection_id": self.atlas, "qty": 1, "unit_uuid": "u-atlas-s"},
            {"collection_id": other, "qty": 1, "unit_uuid": "u-atlas-s"},
        ])
        collection.swap(self.conn, self.atlas, "u-atlas-k")
        again = forces.get(self.conn, f["id"])
        self.assertEqual([(u["name"], u["qty"]) for u in again["units"]], [("Atlas AS7-S", 2)])


class BuildSaveRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_unit(self.conn, "u-atlas-k", 20, "Atlas", "AS7-K", 1700)
        add_unit(self.conn, "u-atlas-s", 21, "Atlas", "AS7-S", 2100)
        own(self.conn, "u-atlas", qty=2)
        own(self.conn, "u-locust")

    def parse(self, text):
        units = []
        for part in text.split(","):
            cid, _, rest = part.partition(":")
            qty, _, variant = rest.partition(":")
            units.append({"collection_id": int(cid), "qty": int(qty), "unit_uuid": variant})
        return units

    def test_a_proxy_build_saves_exactly_as_built(self):
        result = builder.build(self.conn, 3800, formation=None, size=2, seed=1, proxy=True, slack_pct=0)
        b = result["builds"][0]
        saved = forces.save(self.conn, "Proxy", "lance", self.parse(b["save_units"]), target_bv=3800)
        self.assertEqual(saved["total_bv"], b["total_bv"])
        self.assertEqual(sorted(u["name"] for u in saved["units"]), sorted(u["name"] for u in b["units"]))

    def test_an_ordinary_build_has_no_variant_in_save_units(self):
        b = builder.build(self.conn, 3000, formation=None, size=2, seed=1)["builds"][0]
        self.assertNotIn("u-", b["save_units"])
        self.assertRegex(b["save_units"], r"^\d+:\d+(,\d+:\d+)*$")

    def test_included_units_are_in_save_units(self):
        atlas = self.conn.execute("SELECT id FROM collection WHERE unit_uuid = 'u-atlas'").fetchone()[0]
        b = builder.build(self.conn, 4000, formation=None, size=2, seed=1, include=[atlas])["builds"][0]
        self.assertIn(f"{atlas}:", b["save_units"])


class ForceUnitsMigrationTests(unittest.TestCase):
    def test_an_old_force_units_table_is_rebuilt_and_keeps_its_rows(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "old.db")
        conn = db.connect(path)
        add_unit(conn, "u1", 1, "Atlas", "AS7-D", 1900)
        entry = collection.add(conn, "u1", qty=3)["id"]
        conn.close()
        raw = sqlite3.connect(path)
        raw.executescript(
            "DROP TABLE force_units;"
            "CREATE TABLE force_units (force_id INTEGER NOT NULL, collection_id INTEGER NOT NULL, "
            "qty INTEGER NOT NULL DEFAULT 1 CHECK (qty > 0), PRIMARY KEY (force_id, collection_id));"
            "INSERT INTO forces (name, formation) VALUES ('Old', 'lance');"
            f"INSERT INTO force_units VALUES (1, {entry}, 3);"
            "INSERT INTO force_units VALUES (1, 9999, 1);"  # an orphan, which is dropped
        )
        raw.commit()
        raw.close()
        conn = db.connect(path)
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(force_units)")]
        self.assertIn("as_uuid", columns)
        rows = [tuple(r) for r in conn.execute("SELECT force_id, collection_id, as_uuid, qty FROM force_units")]
        self.assertEqual(rows, [(1, entry, "", 3)])
        conn.close()
        db.connect(path).close()  # a second start changes nothing and does not fail


class ForceCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "cli.db")
        source = make_db()
        target = db.connect(self.db_path)
        with target:
            for row in source.execute("SELECT * FROM units").fetchall():
                target.execute(f"INSERT INTO units VALUES ({','.join('?' for _ in row.keys())})", tuple(row))
        add_unit(target, "u-atlas-s", 21, "Atlas", "AS7-S", 2100)
        self.atlas = collection.add(target, "u-atlas", qty=1)["id"]
        target.close()

    def run_cli(self, *args):
        env = dict(os.environ, BATCHALL_DB=self.db_path, BATCHALL_DATA_DIR=self.tmp.name)
        return subprocess.run(
            [sys.executable, "-m", "batchall", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30
        )

    def test_units_accept_a_variant_after_the_quantity(self):
        result = self.run_cli(
            "forces", "save", "--name", "Proxy", "--formation", "lance", "--units", f"{self.atlas}:1:u-atlas-s"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["units"][0]["name"], "Atlas AS7-S")
        self.assertEqual(out["total_bv"], 2100)

    def test_a_wrong_variant_is_an_error(self):
        result = self.run_cli(
            "forces", "save", "--name", "Bad", "--formation", "lance", "--units", f"{self.atlas}:1:u-locust"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a variant", json.loads(result.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
