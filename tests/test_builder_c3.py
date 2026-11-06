import unittest

from support import make_db, own

from batchall import builder, cli


class C3FilterTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.atlas = own(self.conn, "u-atlas")["id"]
        self.wolfhound = own(self.conn, "u-wolfhound")["id"]
        self.shadow = own(self.conn, "u-shadow")["id"]
        with self.conn:
            self.conn.execute("UPDATE units SET c3 = 'C3i' WHERE uuid IN ('u-atlas', 'u-shadow')")
            self.conn.execute("UPDATE units SET c3 = 'C3' WHERE uuid = 'u-wolfhound'")

    def build(self, target=9000, size=2, **kw):
        return builder.build(self.conn, target, formation=None, size=size, seed=1, **kw)["builds"][0]

    def names(self, build):
        return sorted(u["name"] for u in build["units"])

    def test_only_units_with_that_network_are_used(self):
        b = self.build(c3="C3i")
        self.assertEqual(self.names(b), ["Atlas AS7-D", "Shadow Hawk SHD-2H"])

    def test_matching_ignores_case(self):
        self.assertEqual(self.names(self.build(c3="c3I")), ["Atlas AS7-D", "Shadow Hawk SHD-2H"])

    def test_plain_c3_does_not_match_c3i(self):
        with self.assertRaises(builder.BuildError):
            self.build(c3="C3")  # one unit carries C3, and a force of two cannot be filled

    def test_without_the_filter_every_unit_is_used(self):
        self.assertEqual(len(builder.make_pool(self.conn)[0]), 3)

    def test_proxy_fields_a_variant_that_carries_the_network(self):
        with self.conn:
            self.conn.execute(
                "INSERT INTO units (uuid, mul_id, chassis, model, kind, type, subtype, tons, weight_class, "
                "bv, pv, year, tech_base, role, c3) VALUES ('u-wolf-c3i', 30, 'Wolfhound', 'WLF-3I', 'mech', "
                "'t', 's', 35, 'w', 1300, 10, 3025, 'Inner Sphere', 'Striker', 'C3i')"
            )
        b = self.build(target=1400, size=1, c3="C3i", proxy=True, include=None, exclude=[self.atlas, self.shadow])
        unit = b["units"][0]
        self.assertEqual(unit["name"], "Wolfhound WLF-3I")
        self.assertEqual(unit["owned_as"], "Wolfhound WLF-2")

    def test_included_unit_must_carry_the_network(self):
        with self.assertRaises(builder.BuildError):
            self.build(c3="C3i", include=[self.wolfhound])

    def test_included_unit_with_the_network_is_accepted(self):
        b = self.build(c3="C3i", include=[self.atlas])
        self.assertIn("Atlas AS7-D", self.names(b))

    def test_a_catalog_without_c3_data_gives_a_clear_error(self):
        with self.conn:
            self.conn.execute("UPDATE units SET c3 = NULL")
        with self.assertRaises(builder.BuildError) as ctx:
            self.build(c3="C3i")
        self.assertIn("catalog import", str(ctx.exception))

    def test_pool_info_and_text_name_the_network(self):
        result = builder.build(self.conn, 9000, formation=None, size=2, seed=1, c3="C3i")
        self.assertEqual(result["pool"]["c3"], "c3i")
        self.assertIn("c3i only", cli._render_build(result))


class C3ImportTests(unittest.TestCase):
    def import_units(self, units):
        import json
        import tempfile
        from pathlib import Path

        from batchall import catalog, db

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        (folder / "units.json").write_text(json.dumps({"version": 1, "units": units}), encoding="utf-8")
        (folder / "factions.json").write_text(json.dumps({"factions": []}), encoding="utf-8")
        (folder / "eras.json").write_text(json.dumps({"eras": []}), encoding="utf-8")
        conn = db.connect(":memory:")
        catalog.load(conn, tmp.name)
        return conn

    def test_import_stores_the_c3_field(self):
        from test_details import MAD_CAT

        with_c3 = {**MAD_CAT, "uuid": "mc-c3", "model": "C3", "c3": "C3i"}
        conn = self.import_units([MAD_CAT, with_c3])
        got = {r["uuid"]: r["c3"] for r in conn.execute("SELECT uuid, c3 FROM units")}
        self.assertEqual(got["mc-c3"], "C3i")
        self.assertIsNone(got["mc"])

    def test_old_database_without_the_column_is_upgraded(self):
        import sqlite3

        from batchall import db

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA.replace(",\n    c3 TEXT", ""))
        self.assertNotIn("c3", {r["name"] for r in conn.execute("PRAGMA table_info(units)")})
        db._migrate(conn)
        self.assertIn("c3", {r["name"] for r in conn.execute("PRAGMA table_info(units)")})


if __name__ == "__main__":
    unittest.main()
