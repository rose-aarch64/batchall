import unittest

from support import make_db, own

from batchall import collection


class FactionGroupTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def groups(self, uuid):
        own(self.conn, uuid)
        return {i["unit_uuid"]: i["faction_groups"] for i in collection.list_collection(self.conn)}[uuid]

    def test_lists_every_faction_group_that_fields_the_unit(self):
        self.assertEqual(self.groups("u-atlas"), ["IS"])
        self.assertEqual(self.groups("u-timber"), ["Clan"])

    def test_groups_come_from_the_first_era_the_unit_appears_in(self):
        import json

        # Clans field the Timber Wolf in 3050. Inner Sphere factions only get it later, by capture.
        self.conn.execute("INSERT INTO eras (id, name, year_from, year_to) VALUES (14, 'Jihad', 3068, 3080)")
        self.conn.execute("INSERT INTO availability VALUES (1, 14, ?)", (json.dumps([5]),))
        self.assertEqual(self.groups("u-timber"), ["Clan"])

    def add_clans(self):
        self.conn.execute("INSERT INTO factions (id, name, grp) VALUES (3, 'Clan Jade Falcon', 'IS Clan')")
        self.conn.execute("INSERT INTO factions (id, name, grp) VALUES (4, 'Clan Star Adder', 'HW Clan')")

    def test_clan_tech_unit_joins_every_clan_group_that_ever_fields_it(self):
        import json

        # Two invading Clans field the Elemental first. A homeworld Clan picks it up an era later.
        self.add_clans()
        self.conn.execute("INSERT INTO availability VALUES (3, 10, ?)", (json.dumps([8]),))
        self.conn.execute("INSERT INTO availability VALUES (4, 13, ?)", (json.dumps([8]),))
        self.assertEqual(self.groups("u-elemental"), ["HW Clan", "IS Clan"])

    def test_clan_tech_unit_still_ignores_later_inner_sphere_captures(self):
        import json

        self.add_clans()
        self.conn.execute("INSERT INTO eras (id, name, year_from, year_to) VALUES (14, 'Jihad', 3068, 3080)")
        self.conn.execute("INSERT INTO availability VALUES (3, 10, ?)", (json.dumps([5]),))
        self.conn.execute("INSERT INTO availability VALUES (1, 14, ?)", (json.dumps([5]),))
        self.assertEqual(self.groups("u-timber"), ["IS Clan"])

    def test_inner_sphere_tech_unit_does_not_gain_clan_groups_later(self):
        import json

        self.add_clans()
        self.conn.execute("INSERT INTO availability VALUES (3, 13, ?)", (json.dumps([1]),))
        self.assertEqual(self.groups("u-atlas"), ["IS"])

    def test_eras_are_ordered_by_year_not_by_id(self):
        import json

        # Era id 99 is the earliest by year, so its groups win even though its id is the largest.
        self.conn.execute("INSERT INTO eras (id, name, year_from, year_to) VALUES (99, 'Early', 2005, 2570)")
        self.conn.execute("INSERT INTO availability VALUES (2, 99, ?)", (json.dumps([1]),))
        self.assertEqual(self.groups("u-atlas"), ["Clan"])

    def test_unit_fielded_by_several_groups_lists_each_once_in_order(self):
        import json

        self.conn.execute("INSERT INTO availability VALUES (2, 10, ?)", (json.dumps([1]),))
        self.assertEqual(self.groups("u-atlas"), ["Clan", "IS"])

    def test_unit_with_no_availability_or_no_mul_id_has_no_groups(self):
        self.assertEqual(self.groups("u-nomul"), [])
        self.assertEqual(self.groups("u-marauder"), ["IS"])
        self.conn.execute("DELETE FROM availability")
        self.assertEqual(self.groups("u-marauder"), [])

    def test_get_returns_the_field_too(self):
        entry = own(self.conn, "u-atlas")
        self.assertEqual(collection.get(self.conn, entry["id"])["faction_groups"], ["IS"])


if __name__ == "__main__":
    unittest.main()
