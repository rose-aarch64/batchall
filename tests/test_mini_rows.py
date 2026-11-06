"""Every owned mini is a row in ``copies``. ``qty`` and ``painted`` on the entry always match those rows."""

import unittest

from support import make_db, name_mini, own

from batchall import collection, copies


def rows(conn, entry_id):
    return conn.execute(
        "SELECT id, painted, note FROM copies WHERE collection_id = ? ORDER BY id", (entry_id,)
    ).fetchall()


def painted_rows(conn, entry_id):
    return sum(r["painted"] for r in rows(conn, entry_id))


class MiniRowTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        with self.conn:
            self.conn.execute(
                "INSERT INTO units (uuid, mul_id, chassis, model, kind, bv) "
                "VALUES ('u-atlas-k', 99, 'Atlas', 'AS7-K', 'mech', 1900)"
            )

    def test_owning_three_makes_three_rows(self):
        entry = own(self.conn, "u-atlas", qty=3)
        self.assertEqual(len(rows(self.conn, entry["id"])), 3)
        self.assertEqual(entry["qty"], 3)

    def test_painted_count_becomes_painted_rows(self):
        entry = own(self.conn, "u-atlas", qty=3, painted=2)
        self.assertEqual(painted_rows(self.conn, entry["id"]), 2)
        self.assertEqual(entry["painted"], 2)

    def test_adding_more_of_an_owned_unit_adds_rows(self):
        entry = own(self.conn, "u-atlas", qty=2, painted=1)
        entry = collection.add(self.conn, "u-atlas", qty=2, painted=1)
        self.assertEqual((entry["qty"], entry["painted"]), (4, 2))
        self.assertEqual(len(rows(self.conn, entry["id"])), 4)
        self.assertEqual(painted_rows(self.conn, entry["id"]), 2)

    def test_raising_qty_adds_plain_rows(self):
        entry = own(self.conn, "u-atlas", qty=1)
        entry = collection.update(self.conn, entry["id"], qty=4)
        self.assertEqual(len(rows(self.conn, entry["id"])), 4)

    def test_lowering_qty_removes_plain_rows(self):
        entry = own(self.conn, "u-atlas", qty=4)
        entry = collection.update(self.conn, entry["id"], qty=2)
        self.assertEqual((entry["qty"], len(rows(self.conn, entry["id"]))), (2, 2))

    def test_lowering_qty_below_painted_lowers_painted_too(self):
        entry = own(self.conn, "u-atlas", qty=4, painted=4)
        entry = collection.update(self.conn, entry["id"], qty=2)
        self.assertEqual((entry["qty"], entry["painted"]), (2, 2))
        self.assertEqual(painted_rows(self.conn, entry["id"]), 2)

    def test_setting_painted_flips_plain_rows(self):
        entry = own(self.conn, "u-atlas", qty=3)
        entry = collection.update(self.conn, entry["id"], painted=2)
        self.assertEqual(entry["painted"], 2)
        self.assertEqual(painted_rows(self.conn, entry["id"]), 2)
        entry = collection.update(self.conn, entry["id"], painted=1)
        self.assertEqual(painted_rows(self.conn, entry["id"]), 1)
        self.assertEqual(len(rows(self.conn, entry["id"])), 3)

    def test_giving_a_mini_a_note_adds_no_row(self):
        entry = own(self.conn, "u-atlas", qty=3)
        mini = name_mini(self.conn, entry["id"], "Black Widow lead")
        self.assertEqual(len(rows(self.conn, entry["id"])), 3)
        self.assertEqual([r["note"] for r in rows(self.conn, entry["id"]) if r["id"] == mini["id"]], ["Black Widow lead"])
        self.assertEqual(collection.get(self.conn, entry["id"])["qty"], 3)

    def test_naming_an_unpainted_mini_leaves_painted_rows_alone(self):
        entry = own(self.conn, "u-atlas", qty=3, painted=2)
        name_mini(self.conn, entry["id"], "unpainted lead")
        entry = collection.get(self.conn, entry["id"])
        self.assertEqual(entry["painted"], 2)
        self.assertEqual(painted_rows(self.conn, entry["id"]), 2)

    def test_naming_a_painted_mini_paints_an_unpainted_one(self):
        entry = own(self.conn, "u-atlas", qty=3, painted=2)
        copy = name_mini(self.conn, entry["id"], "painted lead", painted=True)
        self.assertTrue(copy["painted"])
        self.assertEqual(collection.get(self.conn, entry["id"])["painted"], 3)

    def test_naming_an_unpainted_mini_in_a_fully_painted_stack_lowers_the_total(self):
        entry = own(self.conn, "u-atlas", qty=3, painted=3)
        name_mini(self.conn, entry["id"], "stripped lead")
        self.assertEqual(collection.get(self.conn, entry["id"])["painted"], 2)

    def test_painting_a_named_mini_raises_the_entry_total(self):
        entry = own(self.conn, "u-atlas", qty=3)
        copy = name_mini(self.conn, entry["id"], "lead")
        copies.update(self.conn, copy["id"], painted=True)
        self.assertEqual(collection.get(self.conn, entry["id"])["painted"], 1)
        copies.update(self.conn, copy["id"], painted=False)
        self.assertEqual(collection.get(self.conn, entry["id"])["painted"], 0)

    def test_clearing_a_minis_details_keeps_the_mini_and_its_paint(self):
        entry = own(self.conn, "u-atlas", qty=3)
        copy = name_mini(self.conn, entry["id"], "lead", tag="t", faction="f", company="c", painted=True)
        copies.update(self.conn, copy["id"], note="", tag="", faction="", company="")
        self.assertEqual(len(rows(self.conn, entry["id"])), 3)
        entry = collection.get(self.conn, entry["id"])
        self.assertEqual((entry["qty"], entry["painted"]), (3, 1))

    def test_cannot_lower_qty_below_named_copies(self):
        entry = own(self.conn, "u-atlas", qty=3)
        name_mini(self.conn, entry["id"], "a")
        name_mini(self.conn, entry["id"], "b")
        with self.assertRaises(collection.CollectionError):
            collection.update(self.conn, entry["id"], qty=1)
        self.assertEqual(len(rows(self.conn, entry["id"])), 3)

    def test_lowering_qty_keeps_named_rows(self):
        entry = own(self.conn, "u-atlas", qty=3)
        copy = name_mini(self.conn, entry["id"], "keeper")
        collection.update(self.conn, entry["id"], qty=1)
        self.assertEqual([r["id"] for r in rows(self.conn, entry["id"])], [copy["id"]])

    def test_named_painted_mini_keeps_the_painted_floor(self):
        entry = own(self.conn, "u-atlas", qty=3)
        copy = name_mini(self.conn, entry["id"], "painted lead", painted=True)
        entry = collection.update(self.conn, entry["id"], painted=0)
        self.assertEqual(entry["painted"], 1)
        self.assertTrue(copies.get(self.conn, copy["id"])["painted"])

    def test_switching_a_named_mini_moves_its_row(self):
        entry = own(self.conn, "u-atlas", qty=2)
        copy = name_mini(self.conn, entry["id"], "variant swap")
        moved = copies.swap(self.conn, copy["id"], "u-atlas-k")
        target = collection.get(self.conn, moved["collection_id"])
        self.assertEqual((target["unit_uuid"], target["qty"]), ("u-atlas-k", 1))
        self.assertEqual(len(rows(self.conn, target["id"])), 1)
        source = collection.get(self.conn, entry["id"])
        self.assertEqual(source["qty"], 1)
        self.assertEqual(len(rows(self.conn, entry["id"])), 1)

    def test_switching_the_last_mini_drops_the_old_entry(self):
        entry = own(self.conn, "u-atlas", qty=1)
        copy = name_mini(self.conn, entry["id"], "only one")
        copies.swap(self.conn, copy["id"], "u-atlas-k")
        with self.assertRaises(collection.CollectionError):
            collection.get(self.conn, entry["id"])

    def test_switching_a_painted_mini_moves_its_paint(self):
        entry = own(self.conn, "u-atlas", qty=2, painted=2)
        copy = name_mini(self.conn, entry["id"], "painted one", painted=True)
        moved = copies.swap(self.conn, copy["id"], "u-atlas-k")
        self.assertEqual(collection.get(self.conn, moved["collection_id"])["painted"], 1)
        self.assertEqual(collection.get(self.conn, entry["id"])["painted"], 1)

    def test_switching_a_whole_entry_onto_an_owned_variant_adds_the_rows(self):
        entry = own(self.conn, "u-atlas", qty=2, painted=1)
        target = own(self.conn, "u-atlas-k", qty=1, painted=1)
        merged = collection.swap(self.conn, entry["id"], "u-atlas-k")
        self.assertEqual(merged["id"], target["id"])
        self.assertEqual((merged["qty"], merged["painted"]), (3, 2))
        self.assertEqual(len(rows(self.conn, target["id"])), 3)
        self.assertEqual(painted_rows(self.conn, target["id"]), 2)

    def test_removing_an_entry_removes_its_rows(self):
        entry = own(self.conn, "u-atlas", qty=3)
        collection.remove(self.conn, entry["id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
