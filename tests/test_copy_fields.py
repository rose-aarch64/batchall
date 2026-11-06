import os
import sqlite3
import tempfile
import unittest

from support import make_db, name_mini, own

from batchall import collection, copies, db


class CopyFieldTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.entry = own(self.conn, "u-atlas", qty=3)
        self.id = self.entry["id"]

    def test_faction_and_company_are_stored_and_returned(self):
        copy = name_mini(self.conn, self.id, "Gloss black", faction="Wolf's Dragoons", company="Black Widow Company")
        self.assertEqual((copy["faction"], copy["company"]), ("Wolf's Dragoons", "Black Widow Company"))
        stored = [m for m in collection.get(self.conn, self.id)["minis"] if m["company"]]
        self.assertEqual([m["company"] for m in stored], ["Black Widow Company"])

    def test_new_fields_default_to_empty(self):
        copy = name_mini(self.conn, self.id, "Plain black")
        self.assertEqual((copy["faction"], copy["company"]), ("", ""))

    def test_values_are_trimmed(self):
        copy = name_mini(self.conn, self.id, "x", faction="  Kell Hounds ", company=" Delta Lance  ")
        self.assertEqual((copy["faction"], copy["company"]), ("Kell Hounds", "Delta Lance"))

    def test_a_mini_can_have_a_faction_or_a_company_and_no_note(self):
        self.assertEqual(name_mini(self.conn, self.id, "", faction="Kell Hounds")["note"], "")
        self.assertEqual(name_mini(self.conn, self.id, "", company="Delta Lance")["company"], "Delta Lance")

    def test_update_changes_one_field_and_keeps_the_rest(self):
        copy = name_mini(self.conn, self.id, "note", tag="t", faction="Kell Hounds", company="Delta Lance")
        copy = copies.update(self.conn, copy["id"], company="Gamma Lance")
        self.assertEqual(
            (copy["note"], copy["tag"], copy["faction"], copy["company"]),
            ("note", "t", "Kell Hounds", "Gamma Lance"),
        )

    def test_updating_one_field_does_not_overwrite_a_change_that_landed_in_between(self):
        # Tabbing quickly through the edit boxes sends several saves at once. Each one must write only
        # its own field, or a slower save would put an older value back over a field saved just before.
        from unittest import mock

        copy = name_mini(self.conn, self.id, "old note")
        stale = copies.get(self.conn, copy["id"])
        copies.update(self.conn, copy["id"], note="newer note")  # another save lands first
        real_get, calls = copies.get, []

        def get(conn, copy_id):
            calls.append(copy_id)
            return stale if len(calls) == 1 else real_get(conn, copy_id)

        with mock.patch.object(copies, "get", get):
            copies.update(self.conn, copy["id"], faction="Kell Hounds")
        final = real_get(self.conn, copy["id"])
        self.assertEqual((final["note"], final["faction"]), ("newer note", "Kell Hounds"))

    def test_update_can_clear_a_field_while_something_else_remains(self):
        copy = name_mini(self.conn, self.id, "note", faction="Kell Hounds")
        copy = copies.update(self.conn, copy["id"], note="", faction="Kell Hounds")
        self.assertEqual((copy["note"], copy["faction"]), ("", "Kell Hounds"))

    def test_blanking_every_field_leaves_a_plain_mini(self):
        copy = name_mini(self.conn, self.id, "note", faction="Kell Hounds")
        copies.update(self.conn, copy["id"], note="", faction="")
        again = copies.get(self.conn, copy["id"])
        self.assertEqual((again["note"], again["faction"], again["company"]), ("", "", ""))
        self.assertEqual(collection.get(self.conn, self.id)["qty"], 3)

    def test_faction_and_company_values_list_distinct_with_counts(self):
        name_mini(self.conn, self.id, "a", faction="Kell Hounds", company="Delta Lance")
        name_mini(self.conn, self.id, "b", faction="Kell Hounds", company="Gamma Lance")
        name_mini(self.conn, self.id, "c")
        self.assertEqual(copies.list_values(self.conn, "faction"), [{"value": "Kell Hounds", "copies": 2}])
        self.assertEqual(
            copies.list_values(self.conn, "company"),
            [{"value": "Delta Lance", "copies": 1}, {"value": "Gamma Lance", "copies": 1}],
        )
        with self.assertRaises(copies.CopyError):
            copies.list_values(self.conn, "note; DROP TABLE copies")


class CopyFilterTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        atlas = own(self.conn, "u-atlas", qty=2)
        locust = own(self.conn, "u-locust", qty=1)
        own(self.conn, "u-condor", qty=1)
        name_mini(self.conn, atlas["id"], "a", faction="Wolf's Dragoons", company="Black Widow Company")
        name_mini(self.conn, atlas["id"], "b", faction="Kell Hounds", company="Delta Lance")
        name_mini(self.conn, locust["id"], "c", faction="Wolf's Dragoons", company="Zeta Company")

    def names(self, **kw):
        return sorted(i["name"] for i in collection.list_collection(self.conn, **kw))

    def test_filter_by_mini_faction_matches_part_of_the_text_ignoring_case(self):
        self.assertEqual(self.names(copy_faction="dragoons"), ["Atlas AS7-D", "Locust LCT-1V"])
        self.assertEqual(self.names(copy_faction="KELL"), ["Atlas AS7-D"])

    def test_filter_by_mini_company(self):
        self.assertEqual(self.names(copy_company="zeta"), ["Locust LCT-1V"])
        self.assertEqual(self.names(copy_company="company"), ["Atlas AS7-D", "Locust LCT-1V"])

    def test_both_filters_must_match_the_same_copy(self):
        # Dragoons and Delta Lance are on different Atlas copies, so no single copy has both.
        self.assertEqual(self.names(copy_faction="dragoons", copy_company="delta"), [])
        self.assertEqual(self.names(copy_faction="dragoons", copy_company="black widow"), ["Atlas AS7-D"])

    def test_units_with_no_named_copies_never_match(self):
        self.assertNotIn("Condor Heavy Tank", self.names(copy_faction="a"))

    def test_no_filter_returns_everything(self):
        self.assertEqual(len(collection.list_collection(self.conn)), 3)

    def test_wildcard_characters_in_a_filter_are_literal(self):
        self.assertEqual(self.names(copy_faction="%"), [])
        self.assertEqual(self.names(copy_company="_"), [])


class CopyMigrationTests(unittest.TestCase):
    def test_copies_table_without_the_new_columns_is_upgraded_and_keeps_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            old = sqlite3.connect(path)
            old.executescript(
                """
                CREATE TABLE units (uuid TEXT PRIMARY KEY, mul_id INTEGER, chassis TEXT NOT NULL,
                    model TEXT NOT NULL, kind TEXT NOT NULL, type TEXT, subtype TEXT, tons REAL,
                    weight_class TEXT, bv INTEGER NOT NULL, pv INTEGER, year INTEGER, tech_base TEXT,
                    role TEXT, squad_size INTEGER, walk INTEGER, jump INTEGER, art TEXT);
                CREATE TABLE collection (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unit_uuid TEXT NOT NULL UNIQUE, qty INTEGER NOT NULL DEFAULT 1,
                    painted INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '', box TEXT NOT NULL DEFAULT '',
                    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE copies (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_id INTEGER NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
                    note TEXT NOT NULL, tag TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO units (uuid, chassis, model, kind, bv) VALUES ('u1', 'King Crab', 'KGC-001', 'mech', 2221);
                INSERT INTO collection (unit_uuid, qty) VALUES ('u1', 1);
                INSERT INTO copies (collection_id, note) VALUES (1, 'Wolf''s Dragoons Black Widow Company');
                """
            )
            old.commit()
            old.close()

            conn = db.connect(path)
            copy = collection.get(conn, 1)["minis"][0]
            self.assertEqual(copy["note"], "Wolf's Dragoons Black Widow Company")
            self.assertEqual((copy["faction"], copy["company"]), ("", ""))
            copies.update(conn, copy["id"], faction="Wolf's Dragoons")
            conn.close()

            conn = db.connect(path)  # a second open must not try to add the columns again
            self.assertEqual(collection.get(conn, 1)["minis"][0]["faction"], "Wolf's Dragoons")
            conn.close()


if __name__ == "__main__":
    unittest.main()
