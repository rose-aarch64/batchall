import unittest

from support import make_db, own

from bt import collection, forces


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_add_increments_existing(self):
        own(self.conn, "u-atlas", qty=1, painted=1)
        entry = own(self.conn, "u-atlas", qty=2, painted=0)
        self.assertEqual((entry["qty"], entry["painted"]), (3, 1))

    def test_painted_never_exceeds_qty(self):
        entry = own(self.conn, "u-atlas", qty=1, painted=5)
        self.assertEqual(entry["painted"], 1)
        entry = collection.update(self.conn, entry["id"], qty=0)
        self.assertEqual((entry["qty"], entry["painted"]), (0, 0))

    def test_unknown_unit_rejected(self):
        with self.assertRaises(collection.CollectionError):
            collection.add(self.conn, "nope")

    def test_filters(self):
        own(self.conn, "u-atlas", painted=1)
        own(self.conn, "u-condor")
        self.assertEqual([i["name"] for i in collection.list_collection(self.conn, kind="vehicle")], ["Condor Heavy Tank"])
        self.assertEqual(len(collection.list_collection(self.conn, painted_only=True)), 1)
        self.assertEqual(len(collection.list_collection(self.conn, q="atl")), 1)
        self.assertEqual(
            [i["name"] for i in collection.list_collection(self.conn, faction="Clan Wolf")], []
        )

    def test_box_set_update_and_filter(self):
        a = collection.add(self.conn, "u-atlas", box="  Clan Invasion Pack ")
        self.assertEqual(a["box"], "Clan Invasion Pack")
        own(self.conn, "u-locust")
        collection.update(self.conn, own(self.conn, "u-condor")["id"], box="Ground Crew")
        names = [i["name"] for i in collection.list_collection(self.conn, box="invasion")]
        self.assertEqual(names, ["Atlas AS7-D"])
        self.assertEqual(
            collection.list_boxes(self.conn),
            [{"box": "Clan Invasion Pack", "entries": 1}, {"box": "Ground Crew", "entries": 1}],
        )

    def test_update_without_box_keeps_it_and_empty_string_clears_it(self):
        entry = collection.add(self.conn, "u-atlas", box="Starter")
        entry = collection.update(self.conn, entry["id"], qty=2)
        self.assertEqual(entry["box"], "Starter")
        entry = collection.update(self.conn, entry["id"], box="")
        self.assertEqual(entry["box"], "")

    def test_adding_again_keeps_box_unless_new_one_given(self):
        collection.add(self.conn, "u-atlas", box="Starter")
        self.assertEqual(collection.add(self.conn, "u-atlas")["box"], "Starter")
        self.assertEqual(collection.add(self.conn, "u-atlas", box="Other")["box"], "Other")

    def test_import_applies_box(self):
        collection.import_text(self.conn, "Atlas AS7-D\n2x Locust LCT-1V", box="Force Pack")
        self.assertEqual({i["box"] for i in collection.list_collection(self.conn)}, {"Force Pack"})

    def test_remove(self):
        entry = own(self.conn, "u-atlas")
        collection.remove(self.conn, entry["id"])
        self.assertEqual(collection.list_collection(self.conn), [])

    def test_import_text(self):
        text = "Atlas AS7-D\n2x Locust LCT-1V\nShadow Hawk SHD-2H x3\n# comment\nTotally Fake Mech\nlocust lct1v"
        result = collection.import_text(self.conn, text)
        self.assertEqual([a["qty"] for a in result["added"]], [1, 2, 3, 1])
        self.assertEqual(result["unmatched"], ["Totally Fake Mech"])
        qty = {i["name"]: i["qty"] for i in collection.list_collection(self.conn)}
        self.assertEqual(qty["Locust LCT-1V"], 3)

    def test_import_mul_ids_and_links(self):
        text = "1\n2x MUL 2\nhttps://masterunitlist.info/Unit/Details/4/shadow-hawk\n999\n# note\nnope"
        result = collection.import_text(self.conn, text, mode="mul")
        self.assertEqual([(a["name"], a["qty"]) for a in result["added"]],
                         [("Atlas AS7-D", 1), ("Locust LCT-1V", 2), ("Shadow Hawk SHD-2H", 1)])
        self.assertEqual(result["unmatched"], ["999", "nope"])

    def test_import_mul_csv_rows_with_leading_id(self):
        text = 'ID,Name\n"1","Atlas AS7-D"\n2,Locust LCT-1V,extra'
        result = collection.import_text(self.conn, text, mode="mul")
        self.assertEqual([a["name"] for a in result["added"]], ["Atlas AS7-D", "Locust LCT-1V"])
        self.assertEqual(result["unmatched"], ["ID,Name"])

    def test_import_mul_shared_id_is_ambiguous(self):
        self.conn.execute("UPDATE units SET mul_id = 1 WHERE uuid = 'u-locust'")
        result = collection.import_text(self.conn, "1", mode="mul")
        self.assertEqual(result["added"], [])
        self.assertEqual(sorted(result["ambiguous"][0]["candidates"]), ["Atlas AS7-D", "Locust LCT-1V"])

    def test_import_rejects_unknown_mode(self):
        with self.assertRaises(collection.CollectionError):
            collection.import_text(self.conn, "1", mode="csv")

    def test_import_near_match_is_suggested_not_added(self):
        result = collection.import_text(self.conn, "Atlass AS7-D")
        self.assertEqual(result["added"], [])
        self.assertEqual(result["ambiguous"][0]["candidates"][0], "Atlas AS7-D")


class ForceTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.atlas = own(self.conn, "u-atlas")["id"]
        self.locust = own(self.conn, "u-locust", qty=2)["id"]

    def test_save_and_summarize(self):
        force = forces.save(
            self.conn,
            "Scouts",
            "lance",
            [{"collection_id": self.atlas, "qty": 1}, {"collection_id": self.locust, "qty": 2}],
            target_bv=3000,
        )
        self.assertEqual(force["total_bv"], 1897 + 2 * 432)
        self.assertEqual(force["count"], 3)
        self.assertEqual(force["composition"], {"mech": 3})
        self.assertEqual(force["warnings"], [])

    def test_warns_when_overusing_owned_copies(self):
        force = forces.save(
            self.conn, "Too many", "lance", [{"collection_id": self.atlas, "qty": 2}]
        )
        self.assertEqual(len(force["warnings"]), 1)

    def test_replace_and_delete(self):
        force = forces.save(self.conn, "A", "star", [{"collection_id": self.atlas, "qty": 1}])
        forces.save(
            self.conn, "B", "star", [{"collection_id": self.locust, "qty": 1}], force_id=force["id"]
        )
        updated = forces.get(self.conn, force["id"])
        self.assertEqual((updated["name"], updated["total_bv"]), ("B", 432))
        forces.delete(self.conn, force["id"])
        self.assertEqual(forces.list_forces(self.conn), [])

    def test_removing_collection_entry_removes_it_from_forces(self):
        force = forces.save(
            self.conn, "A", "lance",
            [{"collection_id": self.atlas, "qty": 1}, {"collection_id": self.locust, "qty": 1}],
        )
        collection.remove(self.conn, self.atlas)
        self.assertEqual(forces.get(self.conn, force["id"])["count"], 1)

    def test_name_required(self):
        with self.assertRaises(forces.ForceError):
            forces.save(self.conn, "  ", "lance", [])


if __name__ == "__main__":
    unittest.main()
