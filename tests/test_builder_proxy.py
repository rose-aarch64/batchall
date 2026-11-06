import json
import unittest

from support import make_db, own

from batchall import builder, cli


def add_unit(conn, uuid, mul, chassis, model, bv, tech="Inner Sphere", kind="mech"):
    with conn:
        conn.execute(
            "INSERT INTO units (uuid, mul_id, chassis, model, kind, type, subtype, tons, "
            "weight_class, bv, pv, year, tech_base, role) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, mul, chassis, model, kind, "t", "s", 100, "w", bv, 10, 3025, tech, "Brawler"),
        )


class ProxyBuildTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        # The fixture's Atlas AS7-D is 1897 BV. These are the other variants of that chassis.
        add_unit(self.conn, "u-atlas-k", 20, "Atlas", "AS7-K", 1700)
        add_unit(self.conn, "u-atlas-s", 21, "Atlas", "AS7-S", 2100)
        add_unit(self.conn, "u-atlas-c", 22, "Atlas", "C", 2500, tech="Clan")
        add_unit(self.conn, "u-other-tank", 23, "Atlas", "Tank", 100, kind="vehicle")
        with self.conn:  # Federated Suns fields the AS7-S in the Clan Invasion era, but not the AS7-K
            self.conn.execute("UPDATE availability SET mul_ids = ? WHERE faction_id = 1 AND era_id = 13",
                              (json.dumps([1, 2, 3, 4, 6, 7, 9, 21]),))

    def build(self, target, size=1, **kw):
        return builder.build(self.conn, target, formation=None, size=size, seed=1, **kw)["builds"][0]

    def test_without_proxy_the_owned_variant_is_used(self):
        own(self.conn, "u-atlas")
        b = self.build(2200)
        self.assertEqual([u["name"] for u in b["units"]], ["Atlas AS7-D"])
        self.assertNotIn("owned_as", b["units"][0])

    def test_proxy_picks_the_variant_that_fits_best(self):
        entry = own(self.conn, "u-atlas")
        b = self.build(2600, proxy=True, slack_pct=0)
        unit = b["units"][0]
        self.assertEqual(unit["name"], "Atlas C")
        self.assertEqual(unit["owned_as"], "Atlas AS7-D")
        self.assertEqual(unit["collection_id"], entry["id"])
        self.assertEqual(b["total_bv"], 2500)

    def test_proxy_never_picks_a_variant_of_another_kind(self):
        own(self.conn, "u-atlas")
        names = set()
        for target in (1800, 2200, 9000):
            names |= {u["name"] for u in self.build(target, proxy=True)["units"]}
        self.assertNotIn("Atlas Tank", names)

    def test_two_minis_can_stand_in_for_different_variants(self):
        own(self.conn, "u-atlas", qty=2)
        b = self.build(3800, size=2, proxy=True, slack_pct=0)
        self.assertEqual(b["total_bv"], 3800)
        self.assertEqual(sorted(u["name"] for u in b["units"]), ["Atlas AS7-K", "Atlas AS7-S"])
        self.assertEqual(sum(u["qty"] for u in b["units"]), 2)

    def test_one_mini_is_never_used_twice(self):
        own(self.conn, "u-atlas")
        with self.assertRaises(builder.BuildError):
            self.build(9000, size=2, proxy=True)

    def test_tech_base_filter_applies_to_the_variants(self):
        own(self.conn, "u-atlas")
        b = self.build(2600, proxy=True, tech_base="Inner Sphere", slack_pct=0)
        self.assertEqual(b["units"][0]["name"], "Atlas AS7-S")

    def test_faction_and_era_filter_applies_to_the_variants(self):
        own(self.conn, "u-atlas")
        clan_invasion = self.build(2600, proxy=True, faction="Federated Suns", era="Clan Invasion", slack_pct=0)
        self.assertEqual(clan_invasion["units"][0]["name"], "Atlas AS7-S")
        star_league = self.build(2600, proxy=True, faction="Federated Suns", era="Star League", slack_pct=0)
        self.assertEqual(star_league["units"][0]["name"], "Atlas AS7-D")

    def test_variants_requested_are_distinct_by_fielded_unit(self):
        own(self.conn, "u-atlas")
        result = builder.build(self.conn, 2200, formation=None, size=1, seed=1, proxy=True, variants=3)
        names = [b["units"][0]["name"] for b in result["builds"]]
        self.assertEqual(len(names), len(set(names)))

    def test_coarse_search_for_big_targets_stays_under_target_and_in_slack(self):
        from unittest import mock

        own(self.conn, "u-atlas", qty=2)
        with mock.patch.object(builder, "COARSE_FROM_BV", 1000), mock.patch.object(builder, "COARSE_BUCKETS", 100):
            b = self.build(3800, size=2, proxy=True)
        self.assertLessEqual(b["total_bv"], 3800)
        self.assertTrue(b["in_window"])
        self.assertEqual(b["total_bv"], sum(u["bv"] * u["qty"] for u in b["units"]))
        self.assertEqual(sum(u["qty"] for u in b["units"]), 2)

    def test_pool_info_says_proxy_and_text_names_the_mini(self):
        own(self.conn, "u-atlas")
        result = builder.build(self.conn, 2200, formation=None, size=1, seed=1, proxy=True, slack_pct=0)
        self.assertTrue(result["pool"]["proxy"])
        text = cli._render_build(result)
        self.assertIn("proxying variants", text)
        self.assertIn("(your Atlas AS7-D)", text)


if __name__ == "__main__":
    unittest.main()
