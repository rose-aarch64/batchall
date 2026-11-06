import os
import sqlite3
import tempfile
import unittest

from support import UNITS  # noqa: F401  (also puts the project root on sys.path)

from batchall import collection, db


class MigrationTests(unittest.TestCase):
    def test_copies_without_painted_and_box_are_upgraded(self):
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
                    company TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO collection (unit_uuid, qty, painted) VALUES ('u1', 2, 1);
                INSERT INTO copies (collection_id, note) VALUES (1, 'gloss');
                """
            )
            old.commit()
            old.close()

            for _ in range(2):  # the second open must not add the columns again
                conn = db.connect(path)
                row = conn.execute("SELECT note, painted, box FROM copies").fetchone()
                self.assertEqual((row["note"], row["painted"], row["box"]), ("gloss", 0, ""))
                conn.close()

    def test_entries_get_a_plain_row_for_each_mini_without_a_name(self):
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
                    company TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO collection (unit_uuid, qty, painted) VALUES ('named', 3, 2);
                INSERT INTO copies (collection_id, note) VALUES (1, 'gloss');
                INSERT INTO collection (unit_uuid, qty, painted) VALUES ('plain', 4, 3);
                INSERT INTO collection (unit_uuid, qty, painted) VALUES ('none', 0, 0);
                """
            )
            old.commit()
            old.close()

            def summary(conn):
                return [
                    tuple(r) for r in conn.execute(
                        "SELECT c.unit_uuid, c.qty, c.painted, COUNT(p.id), COALESCE(SUM(p.painted), 0), "
                        "COALESCE(SUM(p.note != ''), 0) "
                        "FROM collection c LEFT JOIN copies p ON p.collection_id = c.id "
                        "GROUP BY c.id ORDER BY c.id"
                    )
                ]

            expected = [("named", 3, 2, 3, 2, 1), ("plain", 4, 3, 4, 3, 0), ("none", 0, 0, 0, 0, 0)]
            for _ in range(2):  # the second open must not add rows again
                conn = db.connect(path)
                self.assertEqual(summary(conn), expected)
                conn.close()

    def test_database_without_box_column_is_upgraded_and_keeps_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            old = sqlite3.connect(path)
            old.executescript(
                """
                CREATE TABLE units (uuid TEXT PRIMARY KEY, mul_id INTEGER, chassis TEXT NOT NULL,
                    model TEXT NOT NULL, kind TEXT NOT NULL, type TEXT, subtype TEXT, tons REAL,
                    weight_class TEXT, bv INTEGER NOT NULL, pv INTEGER, year INTEGER, tech_base TEXT,
                    role TEXT, squad_size INTEGER, walk INTEGER, jump INTEGER);
                CREATE TABLE collection (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unit_uuid TEXT NOT NULL UNIQUE, qty INTEGER NOT NULL DEFAULT 1,
                    painted INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '',
                    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO units (uuid, chassis, model, kind, bv) VALUES ('u1', 'Atlas', 'AS7-D', 'mech', 1897);
                INSERT INTO collection (unit_uuid, qty, painted) VALUES ('u1', 2, 1);
                """
            )
            old.commit()
            old.close()

            conn = db.connect(path)
            entries = collection.list_collection(conn)
            self.assertEqual([(e["name"], e["qty"], e["painted"], e["box"]) for e in entries],
                             [("Atlas AS7-D", 2, 1, "")])
            collection.update(conn, entries[0]["id"], box="Starter")
            conn.close()

            conn = db.connect(path)  # a second open must not try to add the column again
            self.assertEqual(collection.list_collection(conn)[0]["box"], "Starter")
            conn.close()


    def test_units_without_art_column_is_upgraded_and_keeps_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            old = sqlite3.connect(path)
            old.executescript(
                """
                CREATE TABLE units (uuid TEXT PRIMARY KEY, mul_id INTEGER, chassis TEXT NOT NULL,
                    model TEXT NOT NULL, kind TEXT NOT NULL, type TEXT, subtype TEXT, tons REAL,
                    weight_class TEXT, bv INTEGER NOT NULL, pv INTEGER, year INTEGER, tech_base TEXT,
                    role TEXT, squad_size INTEGER, walk INTEGER, jump INTEGER);
                INSERT INTO units (uuid, chassis, model, kind, bv) VALUES ('u1', 'Atlas', 'AS7-D', 'mech', 1897);
                """
            )
            old.commit()
            old.close()

            conn = db.connect(path)
            row = conn.execute("SELECT chassis, art FROM units").fetchone()
            self.assertEqual((row["chassis"], row["art"]), ("Atlas", None))
            conn.close()
            db.connect(path).close()  # a second open must not try to add the column again

    def test_box_items_without_card_units_column_is_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            old = sqlite3.connect(path)
            old.executescript(
                """
                CREATE TABLE boxes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    kind TEXT NOT NULL, section TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
                    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE box_items (id INTEGER PRIMARY KEY AUTOINCREMENT, box_id INTEGER NOT NULL,
                    position INTEGER NOT NULL, mini_name TEXT NOT NULL, card_variant TEXT NOT NULL DEFAULT '',
                    qty INTEGER NOT NULL DEFAULT 1, unit_uuid TEXT, status TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '');
                INSERT INTO boxes (name, kind) VALUES ('Old Box', 'core');
                INSERT INTO box_items (box_id, position, mini_name, status) VALUES (1, 0, 'Atlas', 'resolved');
                """
            )
            old.commit()
            old.close()

            conn = db.connect(path)
            row = conn.execute("SELECT mini_name, card_units FROM box_items").fetchone()
            self.assertEqual((row["mini_name"], row["card_units"]), ("Atlas", "[]"))
            conn.close()
            db.connect(path).close()  # a second open must not try to add the column again


if __name__ == "__main__":
    unittest.main()
