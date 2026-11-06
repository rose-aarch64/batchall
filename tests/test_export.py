import unittest
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse

from support import make_db, own

from batchall import export, forces


def make_force(conn, name="Test Lance", picks=(("u-atlas", 1), ("u-locust", 2))):
    units = []
    for uuid, qty in picks:
        entry = own(conn, uuid, qty=qty)
        units.append({"collection_id": entry["id"], "qty": qty})
    return forces.save(conn, name, "lance", units)


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_link_points_at_mekbay_and_lists_each_unit_once_per_copy(self):
        force = make_force(self.conn)
        url = urlparse(export.mekbay_link(force))
        self.assertEqual((url.scheme, url.netloc), ("https", "mekbay.com"))
        units = parse_qs(url.query)["units"][0].split(",")
        self.assertEqual(sorted(units), ["u-atlas", "u-locust", "u-locust"])

    def test_the_force_name_travels_in_the_link_and_survives_special_characters(self):
        force = make_force(self.conn, name="Wolf & Co: 100% #1")
        self.assertEqual(parse_qs(urlparse(export.mekbay_link(force)).query)["name"], ["Wolf & Co: 100% #1"])

    def test_an_empty_force_cannot_be_exported(self):
        empty = forces.save(self.conn, "Nothing", "lance", [])
        with self.assertRaises(export.ExportError):
            export.mekbay_link(empty)
        with self.assertRaises(export.ExportError):
            export.mul_file(empty)


class MulTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def entities(self, force):
        return ET.fromstring(export.mul_file(force)["content"]).findall("entity")

    def test_root_is_unit_with_one_entity_per_copy(self):
        force = make_force(self.conn)
        root = ET.fromstring(export.mul_file(force)["content"])
        self.assertEqual(root.tag, "unit")
        self.assertEqual(len(root.findall("entity")), 3)

    def test_entities_carry_chassis_and_model_the_way_the_catalog_names_them(self):
        pairs = sorted((e.get("chassis"), e.get("model")) for e in self.entities(make_force(self.conn)))
        self.assertEqual(pairs, [("Atlas", "AS7-D"), ("Locust", "LCT-1V"), ("Locust", "LCT-1V")])

    def test_each_entity_has_a_default_pilot(self):
        for entity in self.entities(make_force(self.conn)):
            pilot = entity.find("pilot")
            self.assertEqual((pilot.get("gunnery"), pilot.get("piloting")), ("4", "5"))

    def test_odd_characters_in_unit_names_are_escaped(self):
        self.conn.execute("UPDATE units SET chassis = 'Odd <&> \"Mech\"' WHERE uuid = 'u-atlas'")
        force = make_force(self.conn, picks=(("u-atlas", 1),))
        self.assertEqual(self.entities(force)[0].get("chassis"), 'Odd <&> "Mech"')

    def test_filename_is_safe(self):
        force = make_force(self.conn, name="../Wolf: Pack?/1")
        name = export.mul_file(force)["filename"]
        self.assertTrue(name.endswith(".mul"))
        for bad in ("/", "\\", ":", "?", ".."):
            self.assertNotIn(bad, name)

    def test_blank_looking_name_still_gets_a_filename(self):
        force = make_force(self.conn, name="???")
        self.assertEqual(export.mul_file(force)["filename"], "force.mul")


if __name__ == "__main__":
    unittest.main()
