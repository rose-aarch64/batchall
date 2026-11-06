import unittest

from support import make_db, own

from batchall import builder, cli


class IncludeTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.ids = {}
        for uuid, qty in [
            ("u-atlas", 1), ("u-locust", 2), ("u-wolfhound", 1), ("u-shadow", 1),
            ("u-timber", 1), ("u-condor", 1), ("u-scorpion", 1), ("u-elemental", 1),
        ]:
            self.ids[uuid] = own(self.conn, uuid, qty=qty, painted=qty)["id"]

    def build(self, target, size=3, **kw):
        return builder.build(self.conn, target, formation=None, size=size, seed=1, **kw)["builds"][0]

    def test_included_unit_is_in_every_build(self):
        atlas = self.ids["u-atlas"]
        result = builder.build(self.conn, 4000, formation=None, size=3, seed=1, variants=3, include=[atlas])
        self.assertTrue(result["builds"])
        for b in result["builds"]:
            row = [u for u in b["units"] if u["collection_id"] == atlas]
            self.assertEqual(sum(u["qty"] for u in row), 1)
            self.assertEqual(row[0]["included"], 1)

    def test_totals_cover_the_whole_force(self):
        b = self.build(4000, include=[self.ids["u-atlas"]])
        self.assertEqual(b["target_bv"], 4000)
        self.assertEqual(b["size"], 3)
        self.assertEqual(sum(u["qty"] for u in b["units"]), 3)
        self.assertEqual(b["total_bv"], sum(u["bv"] * u["qty"] for u in b["units"]))
        self.assertLessEqual(b["total_bv"], 4000)
        self.assertEqual(b["gap"], 4000 - b["total_bv"])
        self.assertEqual(sum(b["composition"].values()), 3)

    def test_included_unit_is_not_used_twice_by_the_search(self):
        atlas = self.ids["u-atlas"]
        b = self.build(5000, size=4, include=[atlas])
        self.assertEqual(sum(u["qty"] for u in b["units"] if u["collection_id"] == atlas), 1)

    def test_two_of_an_owned_pair(self):
        locust = self.ids["u-locust"]
        b = self.build(4000, include=[locust, locust])
        row = [u for u in b["units"] if u["collection_id"] == locust][0]
        self.assertEqual((row["qty"], row["included"]), (2, 2))

    def test_included_copy_and_a_searched_copy_share_a_row(self):
        locust = self.ids["u-locust"]
        b = self.build(1000, size=2, include=[locust], kinds=["mech"])
        row = [u for u in b["units"] if u["collection_id"] == locust]
        self.assertEqual(len(row), 1)
        self.assertEqual((row[0]["qty"], row[0]["included"]), (2, 1))

    def test_filters_do_not_apply_to_included_units(self):
        timber = self.ids["u-timber"]  # Clan, so a tech base filter for Inner Sphere would drop it
        b = self.build(5000, tech_base="Inner Sphere", include=[timber])
        self.assertIn(timber, [u["collection_id"] for u in b["units"]])
        others = [u for u in b["units"] if u["collection_id"] != timber]
        self.assertTrue(all(u["tech_base"] == "Inner Sphere" for u in others))

    def test_include_counts_against_the_mix(self):
        condor = self.ids["u-condor"]
        b = self.build(5000, size=3, mix={"vehicle": (1, 1)}, include=[condor])
        vehicles = sum(u["qty"] for u in b["units"] if u["kind"] == "vehicle")
        self.assertEqual(vehicles, 1)

    def test_mix_ceiling_below_the_included_count_is_an_error(self):
        condor = self.ids["u-condor"]
        with self.assertRaises(builder.BuildError):
            self.build(5000, mix={"vehicle": (0, 0)}, include=[condor])

    def test_over_the_target_is_an_error(self):
        with self.assertRaises(builder.BuildError):
            self.build(1000, include=[self.ids["u-atlas"]])

    def test_more_than_owned_is_an_error(self):
        with self.assertRaises(builder.BuildError):
            self.build(9000, include=[self.ids["u-atlas"]] * 2)

    def test_more_than_the_size_is_an_error(self):
        with self.assertRaises(builder.BuildError):
            self.build(9000, size=1, include=[self.ids["u-atlas"], self.ids["u-wolfhound"]])

    def test_unknown_id_is_an_error(self):
        with self.assertRaises(ValueError):
            self.build(9000, include=[9999])

    def test_filling_every_slot_with_included_units(self):
        include = [self.ids["u-atlas"], self.ids["u-wolfhound"]]
        b = self.build(4000, size=2, include=include)
        self.assertEqual(sorted(u["collection_id"] for u in b["units"]), sorted(include))
        self.assertEqual(b["total_bv"], 1897 + 1148)

    def test_pool_info_lists_the_included_units(self):
        result = builder.build(self.conn, 4000, formation=None, size=3, seed=1, include=[self.ids["u-atlas"]])
        self.assertEqual(result["pool"]["included"][0]["name"], "Atlas AS7-D")

    def test_without_include_nothing_changes(self):
        b = self.build(4000)
        self.assertNotIn("included", b["units"][0])

    def test_text_marks_included_units(self):
        result = builder.build(self.conn, 4000, formation=None, size=3, seed=1, include=[self.ids["u-atlas"]])
        self.assertIn("(included)", cli._render_build(result))

    def test_works_with_proxy(self):
        atlas = self.ids["u-atlas"]
        b = self.build(4000, proxy=True, include=[atlas])
        row = [u for u in b["units"] if u["collection_id"] == atlas][0]
        self.assertEqual(row["name"], "Atlas AS7-D")
        self.assertNotIn("owned_as", row)


if __name__ == "__main__":
    unittest.main()
