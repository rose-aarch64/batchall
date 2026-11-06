import json
import unittest

from support import make_db, own

from batchall import collection


class AvailableErasTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def eras(self, uuid):
        own(self.conn, uuid)
        return {i["unit_uuid"]: i["available_eras"] for i in collection.list_collection(self.conn)}[uuid]

    def test_lists_every_era_the_unit_is_available_in(self):
        # The fixture fields the Atlas in Star League (10) and Clan Invasion (13), the Locust only in 13.
        self.assertEqual(self.eras("u-atlas"), [10, 13])
        self.assertEqual(self.eras("u-locust"), [13])

    def test_eras_come_back_in_year_order_not_id_order(self):
        # Move era 10 after era 13 by year. The ids stay the same, so only the year can decide the order.
        self.conn.execute("UPDATE eras SET year_from = 3200, year_to = 3300 WHERE id = 10")
        self.assertEqual(self.eras("u-atlas"), [13, 10])

    def test_era_is_listed_once_when_several_factions_field_the_unit(self):
        self.conn.execute("INSERT INTO availability VALUES (2, 10, ?)", (json.dumps([1]),))
        self.assertEqual(self.eras("u-atlas"), [10, 13])

    def test_unit_with_no_mul_id_or_no_availability_has_no_eras(self):
        self.assertEqual(self.eras("u-nomul"), [])
        self.conn.execute("DELETE FROM availability")
        self.assertEqual(self.eras("u-atlas"), [])

    def test_get_returns_the_field_too(self):
        entry = own(self.conn, "u-atlas")
        self.assertEqual(collection.get(self.conn, entry["id"])["available_eras"], [10, 13])


if __name__ == "__main__":
    unittest.main()
