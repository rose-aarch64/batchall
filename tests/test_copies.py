import unittest

from support import make_db, name_mini, own

from batchall import collection, copies


def minis_of(conn, entry_id):
    return collection.get(conn, entry_id)["minis"]


class MiniTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.entry = own(self.conn, "u-atlas", qty=3)

    def test_a_mini_keeps_the_note_and_tag_it_was_given(self):
        mini = name_mini(self.conn, self.entry["id"], "Wolf's Dragoons, Black Widow Company", tag="Black Widow Co")
        self.assertEqual(mini["note"], "Wolf's Dragoons, Black Widow Company")
        self.assertEqual(mini["tag"], "Black Widow Co")
        self.assertEqual(mini["collection_id"], self.entry["id"])
        self.assertIn(mini["id"], [m["id"] for m in minis_of(self.conn, self.entry["id"])])

    def test_tag_is_optional(self):
        mini = name_mini(self.conn, self.entry["id"], "Plain black")
        self.assertEqual(mini["tag"], "")

    def test_update_changes_note_and_tag(self):
        mini = name_mini(self.conn, self.entry["id"], "old", tag="a")
        mini = copies.update(self.conn, mini["id"], note="new")
        self.assertEqual((mini["note"], mini["tag"]), ("new", "a"))
        mini = copies.update(self.conn, mini["id"], tag="")
        self.assertEqual((mini["note"], mini["tag"]), ("new", ""))

    def test_updating_an_unknown_mini_is_rejected(self):
        with self.assertRaises(copies.CopyError):
            copies.update(self.conn, 9999, note="ghost")

    def test_removing_the_entry_removes_its_minis(self):
        name_mini(self.conn, self.entry["id"], "tied to the entry")
        collection.remove(self.conn, self.entry["id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0], 0)

    def test_swap_keeps_the_mini_and_its_details(self):
        mini = name_mini(self.conn, self.entry["id"], "survives a variant swap")
        variants = {v["uuid"] for v in collection.variants(self.conn, self.entry["id"])}
        self.assertEqual(variants, {"u-atlas"})  # the synthetic catalog has one Atlas, so add a second
        with self.conn:
            self.conn.execute(
                "INSERT INTO units (uuid, mul_id, chassis, model, kind, bv) "
                "VALUES ('u-atlas-k', 99, 'Atlas', 'AS7-K', 'mech', 1900)"
            )
        swapped = collection.swap(self.conn, self.entry["id"], "u-atlas-k")
        moved = [m for m in minis_of(self.conn, swapped["id"]) if m["id"] == mini["id"]]
        self.assertEqual([m["note"] for m in moved], ["survives a variant swap"])

    def test_swap_merge_moves_minis_to_the_target(self):
        with self.conn:
            self.conn.execute(
                "INSERT INTO units (uuid, mul_id, chassis, model, kind, bv) "
                "VALUES ('u-atlas-k', 99, 'Atlas', 'AS7-K', 'mech', 1900)"
            )
        target = own(self.conn, "u-atlas-k", qty=1)
        mini = name_mini(self.conn, self.entry["id"], "moves on merge")
        merged = collection.swap(self.conn, self.entry["id"], "u-atlas-k")
        self.assertEqual(merged["id"], target["id"])
        self.assertEqual(merged["qty"], 4)
        self.assertIn(mini["id"], [m["id"] for m in minis_of(self.conn, target["id"])])

    def test_roster_rows_list_every_mini_with_its_fields(self):
        locust = own(self.conn, "u-locust", qty=1)
        first = name_mini(self.conn, self.entry["id"], "black", tag="Black Widow Co")
        second = name_mini(self.conn, self.entry["id"], "red")
        rows = {i["name"]: i["minis"] for i in collection.list_collection(self.conn)}
        self.assertEqual(
            rows["Atlas AS7-D"],
            [
                {"id": first["id"], "note": "black", "tag": "Black Widow Co", "faction": "", "company": "",
                 "material": "", "painted": False, "box": ""},
                {"id": second["id"], "note": "red", "tag": "", "faction": "", "company": "",
                 "material": "", "painted": False, "box": ""},
                {"id": rows["Atlas AS7-D"][2]["id"], "note": "", "tag": "", "faction": "", "company": "",
                 "material": "", "painted": False, "box": ""},
            ],
        )
        self.assertEqual(len(rows["Locust LCT-1V"]), 1)
        self.assertEqual(minis_of(self.conn, locust["id"])[0]["note"], "")

    def test_filter_roster_by_tag(self):
        locust = own(self.conn, "u-locust", qty=1)
        name_mini(self.conn, self.entry["id"], "black", tag="Black Widow Co")
        name_mini(self.conn, locust["id"], "red", tag="Delta Company")
        found = collection.list_collection(self.conn, tag="black widow")
        self.assertEqual([i["name"] for i in found], ["Atlas AS7-D"])

    def test_list_tags_counts_minis_and_skips_blank(self):
        locust = own(self.conn, "u-locust", qty=2)
        name_mini(self.conn, self.entry["id"], "a", tag="Black Widow Co")
        name_mini(self.conn, locust["id"], "b", tag="Black Widow Co")
        name_mini(self.conn, locust["id"], "c")
        self.assertEqual(copies.list_tags(self.conn), [{"tag": "Black Widow Co", "copies": 2}])


if __name__ == "__main__":
    unittest.main()
