import itertools
import unittest

from support import make_db, own

from bt import builder, formations


def brute_force_best(pool, size, target, mix=None):
    models = []
    for item in pool:
        models.extend([item] * item.copies)
    best = None
    for combo in itertools.combinations(range(len(models)), size):
        picked = [models[i] for i in combo]
        total = sum(p.unit["bv"] for p in picked)
        if total > target:
            continue
        counts = {}
        for p in picked:
            counts[p.unit["kind"]] = counts.get(p.unit["kind"], 0) + 1
        ok = True
        for kind, (lo, hi) in (mix or {}).items():
            n = counts.get(kind, 0)
            if n < lo or (hi is not None and n > hi):
                ok = False
        if ok and (best is None or total > best):
            best = total
    return best


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.ids = {}
        for uuid, qty in [
            ("u-atlas", 1), ("u-locust", 2), ("u-wolfhound", 1), ("u-shadow", 1),
            ("u-condor", 1), ("u-scorpion", 1), ("u-elemental", 2), ("u-marauder", 1),
        ]:
            self.ids[uuid] = own(self.conn, uuid, qty=qty, painted=1)["id"]

    def build(self, **kwargs):
        kwargs.setdefault("seed", 1)
        return builder.build(self.conn, **kwargs)["builds"][0]

    def test_matches_brute_force_without_slack(self):
        pool, _ = builder.make_pool(self.conn)
        for target in (2000, 3000, 4500, 6000):
            expected = brute_force_best(pool, 4, target)
            got = self.build(target_bv=target, formation="lance", slack_pct=0)
            self.assertEqual(got["total_bv"], expected, f"target {target}")

    def test_plain_request_gets_closest_fit_despite_slack(self):
        pool, _ = builder.make_pool(self.conn)
        for target in (2500, 4000, 5200):
            expected = brute_force_best(pool, 4, target)
            for seed in range(4):
                got = self.build(target_bv=target, formation="lance", slack_pct=10, seed=seed)
                self.assertEqual(got["total_bv"], expected, f"target {target} seed {seed}")

    def test_variants_stay_near_best(self):
        result = builder.build(
            self.conn, target_bv=4000, formation="lance", slack_pct=10, variants=3, seed=3
        )
        self.assertGreaterEqual(len(result["builds"]), 2)
        for b in result["builds"]:
            self.assertLessEqual(b["total_bv"], 4000)
            self.assertTrue(b["in_window"])

    def test_never_exceeds_target_or_owned_copies(self):
        result = self.build(target_bv=2500, formation="lance")
        self.assertLessEqual(result["total_bv"], 2500)
        self.assertEqual(sum(u["qty"] for u in result["units"]), 4)
        owned = {1: None}
        for u in result["units"]:
            row = self.conn.execute(
                "SELECT qty FROM collection WHERE id = ?", (u["collection_id"],)
            ).fetchone()
            self.assertLessEqual(u["qty"], row["qty"])
        del owned

    def test_formation_sizes(self):
        for key, size in (("lance", 4), ("star", 5), ("level2", 6)):
            result = self.build(target_bv=9000, formation=key)
            self.assertEqual(sum(u["qty"] for u in result["units"]), size, key)

    def test_mix_exact_counts(self):
        result = self.build(
            target_bv=4000, formation="lance", mix=formations.parse_mix("mech=2,vehicle=1,ba=1")
        )
        self.assertEqual(result["composition"], {"mech": 2, "vehicle": 1, "ba": 1})

    def test_mix_matches_brute_force(self):
        pool, _ = builder.make_pool(self.conn)
        mix = formations.parse_mix("mech=2,vehicle>=1")
        expected = brute_force_best(pool, 4, 3500, mix)
        got = self.build(target_bv=3500, formation="lance", mix=mix, slack_pct=0)
        self.assertEqual(got["total_bv"], expected)

    def test_augmented_lance_is_four_mechs_plus_two_others(self):
        result = self.build(target_bv=8000, formation="augmented-lance")
        self.assertEqual(result["composition"]["mech"], 4)
        self.assertEqual(sum(result["composition"].values()), 6)

    def test_weights_steer_choice_inside_window(self):
        favored = self.ids["u-locust"]
        result = self.build(
            target_bv=3300, formation="lance", slack_pct=40, weights={favored: 50}
        )
        names = {u["name"]: u["qty"] for u in result["units"]}
        self.assertEqual(names.get("Locust LCT-1V"), 2)

    def test_exclude_removes_unit(self):
        atlas = self.ids["u-atlas"]
        for seed in range(5):
            result = self.build(target_bv=9000, formation="lance", exclude=[atlas], seed=seed)
            self.assertNotIn("Atlas AS7-D", [u["name"] for u in result["units"]])

    def test_painted_only_uses_painted_copies(self):
        locust = self.ids["u-locust"]
        pool, _ = builder.make_pool(self.conn, painted_only=True)
        item = next(p for p in pool if p.collection_id == locust)
        self.assertEqual(item.copies, 1)

    def test_faction_and_era_filter(self):
        pool, info = builder.make_pool(self.conn, faction="Federated Suns", era="Clan Invasion")
        names = {p.unit["name"] for p in pool}
        self.assertIn("Atlas AS7-D", names)
        self.assertNotIn("Elemental Laser", names)
        self.assertEqual(info["faction"]["name"], "Federated Suns")

    def test_box_filter_limits_pool_and_builds(self):
        from bt import collection

        for name in ("u-atlas", "u-locust", "u-wolfhound", "u-shadow", "u-marauder"):
            collection.update(self.conn, self.ids[name], box="Mech Box")
        pool, info = builder.make_pool(self.conn, box="mech box")
        self.assertEqual({p.unit["kind"] for p in pool}, {"mech"})
        self.assertEqual(info["box"], "mech box")
        result = self.build(target_bv=9000, formation="lance", box="Mech Box")
        self.assertEqual(set(result["composition"]), {"mech"})
        self.assertTrue(all(u["box"] == "Mech Box" for u in result["units"]))

    def test_faction_only_unions_eras(self):
        pool, _ = builder.make_pool(self.conn, faction="Federated Suns")
        self.assertIn("Atlas AS7-D", {p.unit["name"] for p in pool})

    def test_unknown_availability_excluded_when_filtering(self):
        own(self.conn, "u-nomul")
        pool, info = builder.make_pool(self.conn, faction="Federated Suns")
        self.assertNotIn("Mystery Prototype", {p.unit["name"] for p in pool})
        self.assertEqual(info["excluded_unknown_availability"], 1)
        pool, info = builder.make_pool(self.conn)
        self.assertIn("Mystery Prototype", {p.unit["name"] for p in pool})

    def test_ambiguous_faction_reports_candidates(self):
        with self.assertRaises(ValueError):
            builder.make_pool(self.conn, faction="n")

    def test_impossible_budget_raises(self):
        with self.assertRaises(builder.BuildError):
            self.build(target_bv=500, formation="lance")

    def test_mix_without_enough_units_raises(self):
        with self.assertRaises(builder.BuildError):
            self.build(target_bv=9000, formation="lance", mix=formations.parse_mix("proto=1"))

    def test_variants_are_distinct(self):
        result = builder.build(
            self.conn, target_bv=4000, formation="lance", slack_pct=25, variants=3, seed=7
        )
        prints = {tuple(sorted((u["collection_id"], u["qty"]) for u in b["units"])) for b in result["builds"]}
        self.assertEqual(len(prints), len(result["builds"]))
        self.assertGreaterEqual(len(result["builds"]), 2)

    def test_custom_size_ignores_formation_mix(self):
        result = self.build(target_bv=9000, formation="augmented-lance", size=3)
        self.assertEqual(sum(u["qty"] for u in result["units"]), 3)


class MixParsingTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(
            formations.parse_mix("mech=2, vehicles>=1, ba<=1"),
            {"mech": (2, 2), "vehicle": (1, None), "ba": (0, 1)},
        )

    def test_bad_term(self):
        with self.assertRaises(ValueError):
            formations.parse_mix("mech two")

    def test_bad_kind(self):
        with self.assertRaises(ValueError):
            formations.parse_mix("dragon=1")


if __name__ == "__main__":
    unittest.main()
