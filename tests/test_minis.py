"""Every mini is an item of its own: it has a material and the same fields as a named copy, named or not."""

import os
import sqlite3
import tempfile
import unittest

from support import make_db, name_mini, own

from batchall import collection, copies, db


def minis_of(conn, entry_id):
    return collection.get(conn, entry_id)["minis"]


class MiniFieldTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.entry = own(self.conn, "u-atlas", qty=3, painted=1)
        self.id = self.entry["id"]

    def test_roster_rows_list_every_mini_with_all_fields(self):
        minis = minis_of(self.conn, self.id)
        self.assertEqual(len(minis), 3)
        self.assertEqual(
            set(minis[0]),
            {"id", "note", "tag", "faction", "company", "material", "painted", "box"},
        )
        self.assertEqual([m["painted"] for m in minis], [True, False, False])
        self.assertEqual(minis[0]["material"], "")

    def test_a_plain_mini_can_get_a_material_tag_and_box(self):
        mini = minis_of(self.conn, self.id)[1]
        updated = copies.update(self.conn, mini["id"], material=" Resin ", tag="Trial", box="Shelf")
        self.assertEqual((updated["material"], updated["tag"], updated["box"]), ("Resin", "Trial", "Shelf"))
        again = minis_of(self.conn, self.id)[1]
        self.assertEqual((again["material"], again["tag"], again["box"]), ("Resin", "Trial", "Shelf"))

    def test_one_mini_painted_on_its_own(self):
        minis = minis_of(self.conn, self.id)
        copies.update(self.conn, minis[2]["id"], painted=True)
        self.assertEqual(collection.get(self.conn, self.id)["painted"], 2)
        self.assertEqual([m["painted"] for m in minis_of(self.conn, self.id)], [True, False, True])


class OneKindOfMiniTests(unittest.TestCase):
    """There is no separate named-copy system: every mini is the same kind of thing."""

    def setUp(self):
        self.conn = make_db()
        self.entry = own(self.conn, "u-atlas", qty=2)
        self.mini = copies.update(self.conn, minis_of(self.conn, self.entry["id"])[0]["id"], note="lead", faction="Kell Hounds")

    def test_roster_rows_have_only_a_minis_list(self):
        item = collection.get(self.conn, self.entry["id"])
        self.assertNotIn("copies", item)
        self.assertNotIn("copy_count", item)
        self.assertEqual(len(item["minis"]), 2)

    def test_minis_do_not_carry_a_named_flag(self):
        self.assertNotIn("named", minis_of(self.conn, self.entry["id"])[0])
        self.assertNotIn("named", self.mini)

    def test_there_is_no_separate_way_to_name_or_unname_a_mini(self):
        self.assertFalse(hasattr(copies, "add"))
        self.assertFalse(hasattr(copies, "remove"))
        self.assertFalse(hasattr(copies, "list_for"))
        self.assertFalse(hasattr(copies, "count_for"))

    def test_clearing_a_minis_fields_leaves_a_plain_mini(self):
        copies.update(self.conn, self.mini["id"], note="", faction="")
        again = minis_of(self.conn, self.entry["id"])[0]
        self.assertEqual((again["note"], again["faction"], again["company"]), ("", "", ""))
        self.assertEqual(collection.get(self.conn, self.entry["id"])["qty"], 2)


class DeleteMiniTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.entry = own(self.conn, "u-atlas", qty=3, painted=1)
        self.id = self.entry["id"]

    def test_deleting_a_mini_lowers_qty_and_keeps_the_others(self):
        minis = minis_of(self.conn, self.id)
        copies.delete_mini(self.conn, minis[1]["id"])
        entry = collection.get(self.conn, self.id)
        self.assertEqual((entry["qty"], entry["painted"]), (2, 1))
        self.assertEqual([m["id"] for m in entry["minis"]], [minis[0]["id"], minis[2]["id"]])

    def test_deleting_a_painted_mini_lowers_painted(self):
        copies.delete_mini(self.conn, minis_of(self.conn, self.id)[0]["id"])
        self.assertEqual(collection.get(self.conn, self.id)["painted"], 0)

    def test_deleting_a_mini_with_details_removes_it_and_its_details(self):
        copy = name_mini(self.conn, self.id, "lead")
        copies.delete_mini(self.conn, copy["id"])
        entry = collection.get(self.conn, self.id)
        self.assertEqual(entry["qty"], 2)
        self.assertNotIn("lead", [m["note"] for m in entry["minis"]])

    def test_deleting_the_last_mini_removes_the_entry(self):
        entry = own(self.conn, "u-locust", qty=1)
        copies.delete_mini(self.conn, minis_of(self.conn, entry["id"])[0]["id"])
        with self.assertRaises(collection.CollectionError):
            collection.get(self.conn, entry["id"])

    def test_deleting_unknown_mini_is_rejected(self):
        with self.assertRaises(copies.CopyError):
            copies.delete_mini(self.conn, 9999)


class QuantityKeepsDetailsTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.entry = own(self.conn, "u-atlas", qty=3)
        self.id = self.entry["id"]

    def test_lowering_qty_keeps_minis_that_carry_details(self):
        mini = minis_of(self.conn, self.id)[0]
        copies.update(self.conn, mini["id"], material="Metal")
        collection.update(self.conn, self.id, qty=1)
        self.assertEqual([m["id"] for m in minis_of(self.conn, self.id)], [mini["id"]])

    def test_cannot_lower_qty_below_minis_that_carry_details(self):
        for mini in minis_of(self.conn, self.id)[:2]:
            copies.update(self.conn, mini["id"], tag="keep")
        with self.assertRaises(collection.CollectionError):
            collection.update(self.conn, self.id, qty=1)
        self.assertEqual(len(minis_of(self.conn, self.id)), 3)

    def test_a_box_alone_counts_as_a_detail(self):
        mini = minis_of(self.conn, self.id)[2]
        copies.update(self.conn, mini["id"], box="Shelf")
        collection.update(self.conn, self.id, qty=1)
        self.assertEqual([m["id"] for m in minis_of(self.conn, self.id)], [mini["id"]])


class MaterialFilterTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.atlas = own(self.conn, "u-atlas", qty=2)
        self.locust = own(self.conn, "u-locust", qty=1)
        copies.update(self.conn, minis_of(self.conn, self.atlas["id"])[1]["id"], material="Resin")
        copies.update(self.conn, minis_of(self.conn, self.locust["id"])[0]["id"], material="Metal")

    def test_filter_roster_by_material(self):
        found = collection.list_collection(self.conn, material="resin")
        self.assertEqual([i["name"] for i in found], ["Atlas AS7-D"])

    def test_list_materials_counts_minis(self):
        copies.update(self.conn, minis_of(self.conn, self.atlas["id"])[0]["id"], material="Resin")
        self.assertEqual(
            copies.list_values(self.conn, "material"),
            [{"value": "Metal", "copies": 1}, {"value": "Resin", "copies": 2}],
        )


class MaterialMigrationTests(unittest.TestCase):
    def test_copies_without_material_column_are_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            old = sqlite3.connect(path)
            old.executescript(
                """
                CREATE TABLE collection (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unit_uuid TEXT NOT NULL UNIQUE, qty INTEGER NOT NULL DEFAULT 1,
                    painted INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '',
                    box TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE copies (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_id INTEGER NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
                    note TEXT NOT NULL, tag TEXT NOT NULL DEFAULT '', faction TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL DEFAULT '', painted INTEGER NOT NULL DEFAULT 0,
                    box TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO collection (unit_uuid, qty) VALUES ('u1', 1);
                INSERT INTO copies (collection_id, note) VALUES (1, 'gloss');
                """
            )
            old.commit()
            old.close()
            for _ in range(2):  # the second open must not add the column again
                conn = db.connect(path)
                row = conn.execute("SELECT note, material FROM copies").fetchone()
                self.assertEqual((row["note"], row["material"]), ("gloss", ""))
                conn.close()


if __name__ == "__main__":
    unittest.main()
