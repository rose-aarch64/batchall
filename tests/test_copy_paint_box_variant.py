import unittest

from support import make_db, name_mini, own

from bt import collection, copies


def named_ids(item):
    """Ids of an entry's minis that have a note, faction or company."""
    return [m["id"] for m in item["minis"] if m["note"] or m["faction"] or m["company"]]


def add_variant(conn, uuid, model):
    with conn:
        conn.execute(
            "INSERT INTO units (uuid, mul_id, chassis, model, kind, type, subtype, tons, weight_class, bv, pv, "
            "year, tech_base, role) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, 99, "Atlas", model, "mech", "t", "s", 100, "w", 1900, 10, 3025, "Inner Sphere", "Juggernaut"),
        )


class PaintedTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.entry = own(self.conn, "u-atlas", qty=4, painted=2)
        self.id = self.entry["id"]

    def painted(self):
        return collection.get(self.conn, self.id)["painted"]

    def test_a_copy_starts_unpainted(self):
        self.assertFalse(name_mini(self.conn, self.id, "a")["painted"])

    def test_marking_a_copy_painted_adds_to_the_entry_total(self):
        copy = name_mini(self.conn, self.id, "a")
        copies.update(self.conn, copy["id"], painted=True)
        self.assertEqual(self.painted(), 3)
        self.assertTrue(copies.get(self.conn, copy["id"])["painted"])

    def test_unmarking_takes_it_back_off(self):
        copy = name_mini(self.conn, self.id, "a", painted=True)
        self.assertEqual(self.painted(), 3)
        copies.update(self.conn, copy["id"], painted=False)
        self.assertEqual(self.painted(), 2)

    def test_the_total_never_goes_past_the_quantity(self):
        entry = own(self.conn, "u-locust", qty=2, painted=2)
        a = name_mini(self.conn, entry["id"], "a")
        copies.update(self.conn, a["id"], painted=True)
        self.assertLessEqual(collection.get(self.conn, entry["id"])["painted"], 2)

    def test_clearing_a_painted_minis_note_keeps_the_mini_painted(self):
        copy = name_mini(self.conn, self.id, "a", painted=True)
        copies.update(self.conn, copy["id"], note="")
        self.assertEqual(self.painted(), 3)

    def test_the_painted_total_cannot_be_set_below_the_painted_copies(self):
        name_mini(self.conn, self.id, "a", painted=True)
        name_mini(self.conn, self.id, "b", painted=True)
        self.assertEqual(collection.update(self.conn, self.id, painted=0)["painted"], 2)

    def test_updating_other_fields_leaves_the_paint_count_alone(self):
        copy = name_mini(self.conn, self.id, "a")
        copies.update(self.conn, copy["id"], note="b")
        self.assertEqual(self.painted(), 2)


class BoxTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.entry = collection.add(self.conn, "u-atlas", qty=2, box="Starter")
        self.id = self.entry["id"]

    def test_a_copy_can_have_its_own_box(self):
        copy = name_mini(self.conn, self.id, "a", box=" Display case ")
        self.assertEqual(copy["box"], "Display case")
        self.assertEqual(collection.get(self.conn, self.id)["box"], "Starter")

    def test_box_can_be_changed_and_cleared(self):
        copy = name_mini(self.conn, self.id, "a")
        self.assertEqual(copies.update(self.conn, copy["id"], box="Shelf 2")["box"], "Shelf 2")
        self.assertEqual(copies.update(self.conn, copy["id"], box="")["box"], "")


class VariantTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        add_variant(self.conn, "u-atlas-k", "AS7-K")
        self.entry = collection.add(self.conn, "u-atlas", qty=3, painted=1, box="Starter")
        self.id = self.entry["id"]

    def test_one_copy_moves_and_the_others_stay(self):
        copy = name_mini(self.conn, self.id, "gloss")
        other = name_mini(self.conn, self.id, "matte")
        copies.swap(self.conn, copy["id"], "u-atlas-k")
        old = collection.get(self.conn, self.id)
        self.assertEqual(old["qty"], 2)
        self.assertEqual(named_ids(old), [other["id"]])
        new = next(i for i in collection.list_collection(self.conn) if i["unit_uuid"] == "u-atlas-k")
        self.assertEqual((new["qty"], named_ids(new)), (1, [copy["id"]]))
        self.assertEqual(new["box"], "Starter")

    def test_the_copy_keeps_its_details_and_paint(self):
        copy = name_mini(self.conn, self.id, "gloss", faction="Kell Hounds", painted=True)
        copies.swap(self.conn, copy["id"], "u-atlas-k")
        moved = copies.get(self.conn, copy["id"])
        self.assertEqual((moved["note"], moved["faction"], moved["painted"]), ("gloss", "Kell Hounds", True))
        new = next(i for i in collection.list_collection(self.conn) if i["unit_uuid"] == "u-atlas-k")
        self.assertEqual(new["painted"], 1)
        self.assertEqual(collection.get(self.conn, self.id)["painted"], 1)

    def test_it_joins_an_entry_that_is_already_owned(self):
        target = own(self.conn, "u-atlas-k", qty=2, painted=1)
        copy = name_mini(self.conn, self.id, "gloss")
        copies.swap(self.conn, copy["id"], "u-atlas-k")
        after = collection.get(self.conn, target["id"])
        self.assertEqual((after["qty"], after["painted"]), (3, 1))
        self.assertEqual(collection.get(self.conn, self.id)["qty"], 2)

    def test_the_old_entry_goes_when_its_last_mini_leaves(self):
        solo = own(self.conn, "u-locust", qty=1)
        only = collection.update(self.conn, self.id, qty=1)
        copy = name_mini(self.conn, only["id"], "gloss")
        copies.swap(self.conn, copy["id"], "u-atlas-k")
        ids = {i["id"] for i in collection.list_collection(self.conn)}
        self.assertNotIn(self.id, ids)
        self.assertIn(solo["id"], ids)

    def test_another_chassis_is_refused(self):
        copy = name_mini(self.conn, self.id, "gloss")
        with self.assertRaises(copies.CopyError):
            copies.swap(self.conn, copy["id"], "u-locust")

    def test_the_same_variant_changes_nothing(self):
        copy = name_mini(self.conn, self.id, "gloss")
        copies.swap(self.conn, copy["id"], "u-atlas")
        self.assertEqual(collection.get(self.conn, self.id)["qty"], 3)


if __name__ == "__main__":
    unittest.main()
