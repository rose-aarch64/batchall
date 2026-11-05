import unittest
from unittest import mock

from support import make_db

from bt import boxes, collection, forces

PACK_HTML = """
<h2><span class="mw-headline" id="Description">Description</span></h2><p>ignored</p>
<h2><span class="mw-headline" id="Contents">Contents</span></h2>
<ul><li>Four plastic miniatures
<ul><li><i><a href="/wiki/Locust">Locust</a></i></li>
<li>Shadow Hawk</li>
<li>Atlas</li>
<li>Mystery Mech</li></ul></li>
<li>Eight dry-erase cards (dual-sided)
<ul><li>Four Alpha Strike cards
<ul><li><i>LCT-1V Locust</i> / <i>LCT-1E Locust</i></li>
<li>SHD-2H <i>Shadow Hawk</i> / SHD-2D <i>Shadow Hawk</i></li>
<li>AS7-D <i>Atlas</i> / AS7-K <i>Atlas</i></li></ul></li>
<li>Four MechWarrior pilot cards
<ul><li>John Ward/Treh Carns</li></ul></li></ul></li></ul>
<h2><span class="mw-headline" id="Gallery">Gallery</span></h2><ul><li>Stray / item</li></ul>
"""

CLAN_HTML = """
<h2><span class="mw-headline" id="Contents">Contents</span></h2>
<ul><li>Two plastic miniatures
<ul><li><i>Hellbringer (Loki)</i></li><li><i>Mad Dog (Vulture)</i></li></ul></li>
<li>Four dry-erase cards
<ul><li><i>(Loki) Hellbringer Prime</i>/<i>(Loki) Hellbringer A</i></li>
<li><i>(Vulture) Mad Dog Prime</i>/<i>(Vulture) Mad Dog A</i></li></ul></li></ul>
<h2><span class="mw-headline" id="Gallery">Gallery</span></h2>
"""

DD_HTML = """
<h2><span class="mw-headline" id="Contents">Contents</span></h2>
<ul><li>Two high-quality, fully assembled (unpainted) <a href="/m">miniatures</a>
<dl><dd><i><a href="/wiki/Awesome">Awesome</a></i></dd>
<dd><i>Locust</i></dd></dl></li></ul>
<ul><li>One full-color rulebook</li>
<li>Two dry erase cards
<ul><li>LCT-1V <i>Locust</i> / LCT-1E <i>Locust</i></li>
<li>BLR-1G <i>Battlemaster</i> / AWS-8Q <i>Awesome</i></li></ul></li></ul>
<h2><span class="mw-headline" id="Gallery">Gallery</span></h2>
"""

VEHICLE_HTML = """
<h2><span class="mw-headline" id="Contents">Contents</span></h2>
<ul><li>Eight plastic miniatures
<ul><li>(2) Heavy Tank <a href="/w/Demolisher">Demolisher</a></li>
<li>two Galleon Light Tanks</li>
<li>Hatchetman, with optional jump jet exhaust</li>
<li>Tank LRM Carrier (with 2 additional LRM turrets for SRM chassis)</li>
<li>Each miniature is a Point of Elementals, i.e. five Elementals on a single base. One box represents 25.</li></ul></li>
<li>Two Points (Bases) of Elemental Battle Armor (unlocked in Kickstarter)</li>
<li>Eight dry erase cards
<ul><li><i>Demolisher Heavy Tank</i> / <i>Demolisher Heavy Tank</i> (Cell)</li></ul></li></ul>
<h2><span class="mw-headline" id="Gallery">Gallery</span></h2>
"""

LIST_HTML = """
<h2><span id="Released_and_Announced_ForcePacks">x</span></h2>
<h3><span class="mw-headline" id="Inner_Sphere_Lance_ForcePacks">IS</span></h3>
<ul><li><a href="/wiki/BattleTech:_Recon_Lance" title="t">Recon Lance</a></li>
<li><a href="/wiki/BattleTech:_Empty_Pack" title="t">Empty Pack</a></li>
<li><a href="/wiki/BattleTech:_Broken_Pack" title="t">Broken Pack</a></li></ul>
<h3><span class="mw-headline" id="Clan_Star_ForcePacks">Clan</span></h3>
<ul><li><a href="/wiki/BattleTech:_Clan_Pair" title="t">Clan Pair</a></li>
<li><a href="/wiki/BattleTech:_Recon_Lance" title="t">Recon Lance again</a></li></ul>
<h2><span id="References">References</span></h2>
<ul><li><a href="/wiki/Somewhere">not a pack</a></li></ul>
"""

EMPTY_HTML = '<h2><span class="mw-headline" id="Contents">Contents</span></h2><ul><li>Rulebook only</li></ul>'


def add_units(conn, rows):
    for uuid, chassis, model, kind in rows:
        conn.execute(
            "INSERT INTO units (uuid, mul_id, chassis, model, kind, type, subtype, tons, weight_class, "
            "bv, pv, year, tech_base, role) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, None, chassis, model, kind, "t", "s", 50, "w", 1000, 10, 3025, "Inner Sphere", "r"),
        )
    conn.commit()


EXTRA_UNITS = [
    ("x-loc-e", "Locust", "LCT-1E", "mech"),
    ("x-shd-d", "Shadow Hawk", "SHD-2D", "mech"),
    ("x-atlas-k", "Atlas", "AS7-K", "mech"),
    ("x-hell", "Hellbringer (Loki)", "Prime", "mech"),
    ("x-mad", "Mad Dog (Vulture)", "Prime", "mech"),
    ("x-awesome", "Awesome", "AWS-8Q", "mech"),
    ("x-awesome-b", "Awesome", "AWS-9M", "mech"),
    ("x-dem-std", "Demolisher Heavy Tank", "(Standard)", "vehicle"),
    ("x-dem-gauss", "Demolisher Heavy Tank", "(Gauss)", "vehicle"),
    ("x-galleon", "Galleon Light Tank", "GAL-100", "vehicle"),
    ("x-fire-1", "Firestarter", "FS9-A", "mech"),
    ("x-fire-2", "Firestarter", "FS9-H", "mech"),
    ("x-solo", "Lonely Mech", "LM-1", "mech"),
    ("x-elem-4", "Elemental Battle Armor", "(Fire)(Sqd4)", "ba"),
    ("x-elem-5", "Elemental Battle Armor", "(Fire)(Sqd5)", "ba"),
]


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def get(self, path):
        self.requested.append(path)
        value = self.pages[path]
        if isinstance(value, Exception):
            raise value
        return value


class ParsingTests(unittest.TestCase):
    def test_pack_contents(self):
        parsed = boxes.parse_contents(PACK_HTML)
        self.assertEqual([m["name"] for m in parsed["minis"]], ["Locust", "Shadow Hawk", "Atlas", "Mystery Mech"])
        self.assertEqual(parsed["cards"][:3], ["LCT-1V Locust", "SHD-2H Shadow Hawk", "AS7-D Atlas"])
        self.assertIn("LCT-1E Locust", parsed["cards"])  # second sides are kept, after the first sides

    def test_card_name_in_a_link_with_no_space_before_it(self):
        html = (
            '<span class="mw-headline" id="Contents">Contents</span></h2>'
            "<ul><li>Four plastic miniatures<ul><li><i><a>Banshee</a></i></li></ul></li>"
            "<li>Four Alpha Strike cards<ul>"
            "<li>BNC-3S/BNC-12S<i><a>Banshee</a></i></li></ul></li></ul>"
        )
        self.assertIn("BNC-12S Banshee", boxes.parse_contents(html)["cards"])

    def test_second_sides_come_after_all_first_sides(self):
        cards = boxes.parse_contents(PACK_HTML)["cards"]
        self.assertLess(cards.index("AS7-D Atlas"), cards.index("LCT-1E Locust"))

    def test_clan_cards_without_spaces_around_slash(self):
        parsed = boxes.parse_contents(CLAN_HTML)
        self.assertEqual(parsed["cards"][:2], ["(Loki) Hellbringer Prime", "(Vulture) Mad Dog Prime"])

    def test_definition_list_minis(self):
        parsed = boxes.parse_contents(DD_HTML)
        self.assertEqual([m["name"] for m in parsed["minis"]], ["Awesome", "Locust"])

    def test_quantities_notes_and_prose(self):
        minis = {m["name"]: m for m in boxes.parse_contents(VEHICLE_HTML)["minis"]}
        self.assertEqual(minis["Heavy Tank Demolisher"]["qty"], 2)
        self.assertEqual(minis["Galleon Light Tank"]["qty"], 2)
        self.assertEqual(minis["Hatchetman"]["note"], "with optional jump jet exhaust")
        self.assertEqual(minis["Tank LRM Carrier"]["note"], "with 2 additional LRM turrets for SRM chassis")
        self.assertEqual(minis["Elemental Battle Armor"]["qty"], 2)
        self.assertEqual(minis["Elemental Battle Armor"]["note"], "unlocked in Kickstarter")
        self.assertFalse(any(name.startswith("Each miniature") for name in minis))

    def test_no_contents_section(self):
        self.assertIsNone(boxes.parse_contents("<h2>Nothing</h2>"))

    def test_pack_list(self):
        packs = boxes.parse_pack_list(LIST_HTML)
        self.assertEqual([p["name"] for p in packs], ["Recon Lance", "Empty Pack", "Broken Pack", "Clan Pair"])
        self.assertEqual(packs[0]["section"], "Inner Sphere Lance")
        self.assertEqual(packs[3]["section"], "Clan Star")

    def test_pack_list_layout_change_is_reported(self):
        with self.assertRaises(boxes.BoxError):
            boxes.parse_pack_list("<html>nothing useful</html>")

    def test_tokens_ignore_order_and_punctuation(self):
        self.assertEqual(boxes.tokens("(Loki) Hellbringer Prime"), boxes.tokens("Hellbringer (Loki) Prime"))


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_units(self.conn, EXTRA_UNITS)
        self.index = boxes.UnitIndex(self.conn)

    def match(self, mini, cards):
        status, unit, card = boxes.match_item(self.index, mini, cards)
        return status, unit["name"] if unit else None

    def test_exact_card_match(self):
        self.assertEqual(self.match("Locust", ["LCT-1V Locust"]), ("resolved", "Locust LCT-1V"))

    def test_clan_name_order_difference(self):
        self.assertEqual(
            self.match("Hellbringer (Loki)", ["(Loki) Hellbringer Prime"]),
            ("resolved", "Hellbringer (Loki) Prime"),
        )

    def test_trailing_parenthetical_is_approximate(self):
        self.assertEqual(
            self.match("Atlas", ["AS7-D Atlas (Some Pilot)"]), ("approx", "Atlas AS7-D")
        )

    def test_standard_model_rule(self):
        self.assertEqual(
            self.match("Heavy Tank Demolisher", ["Demolisher Heavy Tank"]),
            ("resolved", "Demolisher Heavy Tank (Standard)"),
        )

    def test_chassis_with_several_variants_and_no_card_needs_review(self):
        self.assertEqual(self.match("Firestarter", []), ("chassis_only", None))

    def test_chassis_with_one_variant_and_no_card_is_resolved(self):
        self.assertEqual(self.match("Lonely Mech", []), ("resolved", "Lonely Mech LM-1"))

    def test_unknown_mini(self):
        self.assertEqual(self.match("Mystery Mech", ["LCT-1V Locust"]), ("no_match", None))

    def test_card_for_a_different_mini_is_not_used(self):
        self.assertEqual(self.match("Firestarter", ["LCT-1V Locust"]), ("chassis_only", None))

    def test_candidates_put_the_card_variant_first(self):
        names = [c["name"] for c in boxes.candidates(self.index, "Awesome", "AWS-9M Awesome")]
        self.assertEqual(names[0], "Awesome AWS-9M")
        self.assertEqual(set(names), {"Awesome AWS-8Q", "Awesome AWS-9M"})

    def test_candidates_fall_back_to_first_word(self):
        names = [c["name"] for c in boxes.candidates(self.index, "Firestarter Sneede", "")]
        self.assertEqual(set(names), {"Firestarter FS9-A", "Firestarter FS9-H"})


class ImportAndAddTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_units(self.conn, EXTRA_UNITS)
        self.pages = {
            "/wiki/ForcePack": LIST_HTML,
            "/wiki/BattleTech:_Recon_Lance": PACK_HTML,
            "/wiki/BattleTech:_Empty_Pack": EMPTY_HTML,
            "/wiki/BattleTech:_Broken_Pack": OSError("HTTP 500"),
            "/wiki/BattleTech:_Clan_Pair": CLAN_HTML,
            "/wiki/Core": DD_HTML,
        }
        patcher = mock.patch.object(boxes, "CORE_BOXES", (("Core Box", "/wiki/Core"),))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.summary = boxes.import_boxes(self.conn, FakeFetcher(self.pages))

    def box_id(self, name):
        return next(b["id"] for b in boxes.search_boxes(self.conn, name) if b["name"] == name)

    def test_summary(self):
        self.assertEqual(self.summary["boxes"], 3)
        self.assertEqual(self.summary["skipped_no_minis"], ["Empty Pack"])
        self.assertEqual([f["name"] for f in self.summary["failed"]], ["Broken Pack"])
        self.assertEqual(self.summary["by_status"]["no_match"], 1)

    def test_duplicate_links_are_fetched_once(self):
        fetcher = FakeFetcher(self.pages)
        boxes.import_boxes(self.conn, fetcher)
        self.assertEqual(fetcher.requested.count("/wiki/BattleTech:_Recon_Lance"), 1)

    def test_reimport_replaces_rows_and_leaves_collection_alone(self):
        collection.add(self.conn, "u-atlas", qty=2)
        boxes.import_boxes(self.conn, FakeFetcher(self.pages))
        self.assertEqual(len(boxes.search_boxes(self.conn, "")), 3)
        self.assertEqual(collection.list_collection(self.conn)[0]["qty"], 2)

    def test_needs_the_unit_catalog(self):
        from bt import db

        with self.assertRaises(boxes.BoxError):
            boxes.import_boxes(db.connect(":memory:"), FakeFetcher(self.pages))

    def test_search_core_first_and_by_all_words(self):
        names = [b["name"] for b in boxes.search_boxes(self.conn, "")]
        self.assertEqual(names[0], "Core Box")
        self.assertEqual([b["name"] for b in boxes.search_boxes(self.conn, "clan pa")], ["Clan Pair"])
        found = boxes.search_boxes(self.conn, "recon")[0]
        self.assertEqual((found["items"], found["matched"]), (4, 3))

    def test_get_box_includes_units_and_source(self):
        box = boxes.get_box(self.conn, self.box_id("Recon Lance"))
        self.assertTrue(box["source_url"].endswith("/wiki/BattleTech:_Recon_Lance"))
        by_name = {i["mini_name"]: i for i in box["items"]}
        self.assertEqual(by_name["Locust"]["unit"]["name"], "Locust LCT-1V")
        self.assertIsNone(by_name["Mystery Mech"]["unit"])

    def card_units(self, box_name, mini):
        import json

        row = self.conn.execute(
            "SELECT i.card_units FROM box_items i JOIN boxes b ON b.id = i.box_id "
            "WHERE b.name = ? AND i.mini_name = ?",
            (box_name, mini),
        ).fetchone()
        return json.loads(row["card_units"])

    def test_every_card_side_is_stored(self):
        self.assertEqual(self.card_units("Recon Lance", "Locust"), ["u-locust", "x-loc-e"])
        self.assertEqual(self.card_units("Recon Lance", "Atlas"), ["u-atlas", "x-atlas-k"])
        self.assertEqual(self.card_units("Clan Pair", "Hellbringer (Loki)"), ["x-hell"])
        self.assertEqual(self.card_units("Recon Lance", "Mystery Mech"), [])

    def test_second_side_only_card_is_stored(self):
        # The Awesome has no first-side card in this box, only the second side of the BattleMaster's.
        self.assertEqual(self.card_units("Core Box", "Awesome"), ["x-awesome"])

    def test_for_unit_lists_boxes_with_the_mini_or_its_card(self):
        mini = boxes.for_unit(self.conn, "u-atlas")
        self.assertEqual([(b["name"], b["relation"]) for b in mini], [("Recon Lance", "mini")])
        card_only = boxes.for_unit(self.conn, "x-atlas-k")
        self.assertEqual([(b["name"], b["relation"]) for b in card_only], [("Recon Lance", "card")])
        self.assertEqual(boxes.for_unit(self.conn, "u-timber"), [])

    def test_variant_picker_lists_card_variants_first(self):
        add_units(self.conn, [("x-loc-s", "Locust", "LCT-1S", "mech")])
        boxes.add_box(self.conn, self.box_id("Recon Lance"))
        locust = next(e for e in collection.list_collection(self.conn) if e["name"] == "Locust LCT-1V")
        listed = collection.variants(self.conn, locust["id"])
        self.assertEqual(
            [(v["name"], v["on_card"]) for v in listed],
            [("Locust LCT-1E", True), ("Locust LCT-1V", True), ("Locust LCT-1S", False)],
        )
        self.assertTrue(next(v for v in listed if v["current"])["on_card"])

    def test_no_card_flags_for_a_hand_typed_box_label(self):
        add_units(self.conn, [("x-loc-s", "Locust", "LCT-1S", "mech")])
        entry = collection.add(self.conn, "u-locust", box="My own label")
        self.assertFalse(any(v["on_card"] for v in collection.variants(self.conn, entry["id"])))
        entry = collection.add(self.conn, "u-atlas")  # no label at all
        self.assertFalse(any(v["on_card"] for v in collection.variants(self.conn, entry["id"])))

    def test_card_flags_follow_the_chassis_not_the_whole_box(self):
        boxes.add_box(self.conn, self.box_id("Recon Lance"))
        atlas = next(e for e in collection.list_collection(self.conn) if e["name"] == "Atlas AS7-D")
        flagged = {v["name"] for v in collection.variants(self.conn, atlas["id"]) if v["on_card"]}
        self.assertEqual(flagged, {"Atlas AS7-D", "Atlas AS7-K"})

    def test_add_box_adds_matched_and_returns_the_rest_for_review(self):
        result = boxes.add_box(self.conn, self.box_id("Recon Lance"))
        self.assertEqual(sorted(a["name"] for a in result["added"]), ["Atlas AS7-D", "Locust LCT-1V", "Shadow Hawk SHD-2H"])
        self.assertEqual([r["mini_name"] for r in result["review"]], ["Mystery Mech"])
        owned = {e["name"]: e for e in collection.list_collection(self.conn)}
        self.assertEqual(owned["Locust LCT-1V"]["box"], "Recon Lance")
        self.assertEqual(len(owned), 3)

    def test_review_choices_add_only_the_chosen_items_and_never_repeat_matched_ones(self):
        box_id = self.box_id("Recon Lance")
        first = boxes.add_box(self.conn, box_id)
        item_id = first["review"][0]["item_id"]
        second = boxes.add_box(self.conn, box_id, only={str(item_id): "x-solo"})
        self.assertEqual([a["name"] for a in second["added"]], ["Lonely Mech LM-1"])
        owned = {e["name"]: e["qty"] for e in collection.list_collection(self.conn)}
        self.assertEqual(owned["Locust LCT-1V"], 1)  # not added a second time
        self.assertEqual(owned["Lonely Mech LM-1"], 1)

    def test_null_choice_skips(self):
        box_id = self.box_id("Recon Lance")
        item_id = boxes.add_box(self.conn, box_id)["review"][0]["item_id"]
        result = boxes.add_box(self.conn, box_id, only={item_id: None})
        self.assertEqual((result["added"], result["skipped"]), ([], ["Mystery Mech"]))

    def test_adding_a_box_twice_adds_quantities_again(self):
        box_id = self.box_id("Core Box")
        boxes.add_box(self.conn, box_id)
        boxes.add_box(self.conn, box_id)
        self.assertEqual({e["qty"] for e in collection.list_collection(self.conn)}, {2})

    def test_bad_choices_are_rejected_before_anything_is_written(self):
        box_id = self.box_id("Recon Lance")
        review = boxes.add_box(self.conn, box_id)["review"]
        before = len(collection.list_collection(self.conn))
        with self.assertRaises(boxes.BoxError):
            boxes.add_box(self.conn, box_id, only={review[0]["item_id"]: "x-solo", 999999: "x-solo"})
        with self.assertRaises(boxes.BoxError):
            boxes.add_box(self.conn, box_id, only={review[0]["item_id"]: "not-a-unit"})
        self.assertEqual(len(collection.list_collection(self.conn)), before)
        with self.assertRaises(boxes.BoxError):
            boxes.add_box(self.conn, 424242)

    def test_unmatched_candidates_come_from_the_first_word(self):
        box_id = self.box_id("Recon Lance")
        review = boxes.add_box(self.conn, box_id)["review"][0]
        self.assertEqual(review["status"], "no_match")
        # "Mystery Mech" is unknown, but the fixture catalog has a "Mystery Prototype" chassis.
        self.assertEqual([c["name"] for c in review["candidates"]], ["Mystery Prototype"])


class VariantSwapTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_units(self.conn, EXTRA_UNITS)

    def test_variants_list_same_chassis_only(self):
        entry = collection.add(self.conn, "x-fire-1")
        names = [(v["name"], v["current"]) for v in collection.variants(self.conn, entry["id"])]
        self.assertEqual(names, [("Firestarter FS9-A", True), ("Firestarter FS9-H", False)])

    def test_swap_to_unowned_variant_keeps_entry_and_forces(self):
        entry = collection.add(self.conn, "x-fire-1", qty=2, painted=1, notes="n", box="Pack")
        force = forces.save(self.conn, "F", "lance", [{"collection_id": entry["id"], "qty": 1}])
        swapped = collection.swap(self.conn, entry["id"], "x-fire-2")
        self.assertEqual((swapped["id"], swapped["name"], swapped["qty"], swapped["painted"], swapped["box"]),
                         (entry["id"], "Firestarter FS9-H", 2, 1, "Pack"))
        self.assertEqual(forces.get(self.conn, force["id"])["units"][0]["name"], "Firestarter FS9-H")

    def test_swap_merges_into_an_owned_variant(self):
        a = collection.add(self.conn, "x-fire-1", qty=2, painted=1, notes="a")
        b = collection.add(self.conn, "x-fire-2", qty=1, painted=1, notes="b", box="Other")
        f1 = forces.save(self.conn, "only a", "lance", [{"collection_id": a["id"], "qty": 1}])
        f2 = forces.save(
            self.conn, "both", "lance",
            [{"collection_id": a["id"], "qty": 1}, {"collection_id": b["id"], "qty": 1}],
        )
        merged = collection.swap(self.conn, a["id"], "x-fire-2")
        self.assertEqual((merged["id"], merged["qty"], merged["painted"], merged["notes"]), (b["id"], 3, 2, "b; a"))
        self.assertEqual([e["name"] for e in collection.list_collection(self.conn)], ["Firestarter FS9-H"])
        self.assertEqual(forces.get(self.conn, f1["id"])["units"][0]["collection_id"], b["id"])
        both = forces.get(self.conn, f2["id"])["units"]
        self.assertEqual([(u["collection_id"], u["qty"]) for u in both], [(b["id"], 2)])

    def test_swap_to_another_chassis_is_refused(self):
        entry = collection.add(self.conn, "x-fire-1")
        with self.assertRaises(collection.CollectionError):
            collection.swap(self.conn, entry["id"], "x-solo")

    def test_swap_to_same_unit_is_a_no_op(self):
        entry = collection.add(self.conn, "x-fire-1")
        self.assertEqual(collection.swap(self.conn, entry["id"], "x-fire-1")["id"], entry["id"])


if __name__ == "__main__":
    unittest.main()
