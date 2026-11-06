import unittest

from support import make_db, own

from bt import collection, copies


class CopiesTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.entry = own(self.conn, "u-atlas", qty=3)

    def test_add_and_list_a_named_copy(self):
        copy = copies.add(self.conn, self.entry["id"], "Wolf's Dragoons, Black Widow Company", tag="Black Widow Co")
        self.assertEqual(copy["note"], "Wolf's Dragoons, Black Widow Company")
        self.assertEqual(copy["tag"], "Black Widow Co")
        self.assertEqual(copy["collection_id"], self.entry["id"])
        self.assertEqual([c["id"] for c in copies.list_for(self.conn, self.entry["id"])], [copy["id"]])

    def test_tag_is_optional(self):
        copy = copies.add(self.conn, self.entry["id"], "Plain black")
        self.assertEqual(copy["tag"], "")

    def test_cannot_name_more_copies_than_owned(self):
        for i in range(3):
            copies.add(self.conn, self.entry["id"], f"copy {i}")
        with self.assertRaises(copies.CopyError):
            copies.add(self.conn, self.entry["id"], "one too many")

    def test_unknown_collection_entry_rejected(self):
        with self.assertRaises(copies.CopyError):
            copies.add(self.conn, 9999, "ghost")

    def test_blank_note_and_tag_rejected(self):
        with self.assertRaises(copies.CopyError):
            copies.add(self.conn, self.entry["id"], "   ")

    def test_update_changes_note_and_tag(self):
        copy = copies.add(self.conn, self.entry["id"], "old", tag="a")
        copy = copies.update(self.conn, copy["id"], note="new")
        self.assertEqual((copy["note"], copy["tag"]), ("new", "a"))
        copy = copies.update(self.conn, copy["id"], tag="")
        self.assertEqual((copy["note"], copy["tag"]), ("new", ""))

    def test_remove_a_copy(self):
        copy = copies.add(self.conn, self.entry["id"], "gone soon")
        copies.remove(self.conn, copy["id"])
        self.assertEqual(copies.list_for(self.conn, self.entry["id"]), [])

    def test_removing_the_entry_removes_its_copies(self):
        copies.add(self.conn, self.entry["id"], "tied to the entry")
        collection.remove(self.conn, self.entry["id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0], 0)

    def test_cannot_lower_qty_below_named_copies(self):
        copies.add(self.conn, self.entry["id"], "a")
        copies.add(self.conn, self.entry["id"], "b")
        with self.assertRaises(collection.CollectionError):
            collection.update(self.conn, self.entry["id"], qty=1)
        self.assertEqual(collection.update(self.conn, self.entry["id"], qty=2)["qty"], 2)

    def test_swap_keeps_named_copies(self):
        copy = copies.add(self.conn, self.entry["id"], "survives a variant swap")
        variants = {v["uuid"] for v in collection.variants(self.conn, self.entry["id"])}
        self.assertEqual(variants, {"u-atlas"})  # the synthetic catalog has one Atlas, so add a second
        with self.conn:
            self.conn.execute(
                "INSERT INTO units (uuid, mul_id, chassis, model, kind, bv) "
                "VALUES ('u-atlas-k', 99, 'Atlas', 'AS7-K', 'mech', 1900)"
            )
        swapped = collection.swap(self.conn, self.entry["id"], "u-atlas-k")
        self.assertEqual([c["id"] for c in copies.list_for(self.conn, swapped["id"])], [copy["id"]])

    def test_swap_merge_moves_copies_to_the_target(self):
        with self.conn:
            self.conn.execute(
                "INSERT INTO units (uuid, mul_id, chassis, model, kind, bv) "
                "VALUES ('u-atlas-k', 99, 'Atlas', 'AS7-K', 'mech', 1900)"
            )
        target = own(self.conn, "u-atlas-k", qty=1)
        copy = copies.add(self.conn, self.entry["id"], "moves on merge")
        merged = collection.swap(self.conn, self.entry["id"], "u-atlas-k")
        self.assertEqual(merged["id"], target["id"])
        self.assertEqual([c["id"] for c in copies.list_for(self.conn, target["id"])], [copy["id"]])

    def test_roster_rows_carry_a_copy_count(self):
        own(self.conn, "u-locust", qty=2)
        copies.add(self.conn, self.entry["id"], "a")
        copies.add(self.conn, self.entry["id"], "b")
        counts = {i["name"]: i["copy_count"] for i in collection.list_collection(self.conn)}
        self.assertEqual(counts, {"Atlas AS7-D": 2, "Locust LCT-1V": 0})

    def test_roster_rows_list_their_copies(self):
        locust = own(self.conn, "u-locust", qty=1)
        first = copies.add(self.conn, self.entry["id"], "black", tag="Black Widow Co")
        second = copies.add(self.conn, self.entry["id"], "red")
        rows = {i["name"]: i["copies"] for i in collection.list_collection(self.conn)}
        self.assertEqual(rows["Locust LCT-1V"], [])
        self.assertEqual(
            rows["Atlas AS7-D"],
            [
                {"id": first["id"], "note": "black", "tag": "Black Widow Co", "faction": "", "company": "",
                 "material": "", "painted": False, "box": "", "named": True},
                {"id": second["id"], "note": "red", "tag": "", "faction": "", "company": "",
                 "material": "", "painted": False, "box": "", "named": True},
            ],
        )
        self.assertEqual(collection.get(self.conn, locust["id"])["copies"], [])

    def test_filter_roster_by_tag(self):
        locust = own(self.conn, "u-locust", qty=1)
        copies.add(self.conn, self.entry["id"], "black", tag="Black Widow Co")
        copies.add(self.conn, locust["id"], "red", tag="Delta Company")
        found = collection.list_collection(self.conn, tag="black widow")
        self.assertEqual([i["name"] for i in found], ["Atlas AS7-D"])

    def test_list_tags_counts_copies_and_skips_blank(self):
        locust = own(self.conn, "u-locust", qty=2)
        copies.add(self.conn, self.entry["id"], "a", tag="Black Widow Co")
        copies.add(self.conn, locust["id"], "b", tag="Black Widow Co")
        copies.add(self.conn, locust["id"], "c")
        self.assertEqual(copies.list_tags(self.conn), [{"tag": "Black Widow Co", "copies": 2}])


if __name__ == "__main__":
    unittest.main()
