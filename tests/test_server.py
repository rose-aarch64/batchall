import http.client
import json
import os
import tempfile
import threading
import unittest

from support import UNITS, make_db

from batchall import db, server


def seed(path):
    """Copy the synthetic catalog into a file database the server can open."""
    source = make_db()
    target = db.connect(path)
    with target:
        for table in ("units", "factions", "eras", "availability"):
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            for row in rows:
                marks = ",".join("?" for _ in row.keys())
                target.execute(f"INSERT INTO {table} VALUES ({marks})", tuple(row))
    target.close()
    source.close()


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "test.db")
        seed(cls.db_path)
        cls.art_tmp = tempfile.TemporaryDirectory()
        cls.httpd = server.make_server(port=0, db_path=cls.db_path, art_dir=cls.art_tmp.name)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()
        cls.art_tmp.cleanup()

    def setUp(self):
        conn = db.connect(self.db_path)
        with conn:
            conn.execute("DELETE FROM forces")
            conn.execute("DELETE FROM collection")
        conn.close()

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = dict(headers or {})
        payload = raw
        if body is not None:
            payload = json.dumps(body)
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=hdrs)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        try:
            return res.status, json.loads(data)
        except ValueError:
            return res.status, data

    def test_collection_round_trip_and_build(self):
        status, entry = self.request("POST", "/api/collection", {"unit_uuid": "u-atlas", "qty": 1})
        self.assertEqual(status, 200)
        for uuid in ("u-locust", "u-wolfhound", "u-shadow", "u-condor"):
            self.request("POST", "/api/collection", {"unit_uuid": uuid})
        status, items = self.request("GET", "/api/collection")
        self.assertEqual(len(items), 5)

        status, result = self.request("POST", "/api/build", {"target_bv": 3500, "formation": "lance"})
        self.assertEqual(status, 200)
        self.assertLessEqual(result["builds"][0]["total_bv"], 3500)

        units = [{"collection_id": u["collection_id"], "qty": u["qty"]} for u in result["builds"][0]["units"]]
        status, force = self.request(
            "POST", "/api/forces", {"name": "API Lance", "formation": "lance", "units": units}
        )
        self.assertEqual(status, 200)
        status, forces_list = self.request("GET", "/api/forces")
        self.assertIn("API Lance", [f["name"] for f in forces_list])
        status, _ = self.request("DELETE", f"/api/forces/{force['id']}")
        self.assertEqual(status, 200)

        status, _ = self.request("PATCH", f"/api/collection/{entry['id']}", {"qty": 3})
        self.assertEqual(status, 200)

    def test_box_over_api(self):
        status, entry = self.request(
            "POST", "/api/collection", {"unit_uuid": "u-marauder", "box": "API Box"}
        )
        self.assertEqual(entry["box"], "API Box")
        status, _ = self.request("PATCH", f"/api/collection/{entry['id']}", {"box": "Moved"})
        status, items = self.request("GET", "/api/collection?box=moved")
        self.assertEqual([i["box"] for i in items], ["Moved"])
        status, boxes = self.request("GET", "/api/boxes")
        self.assertIn({"box": "Moved", "entries": 1}, boxes)

    def test_mini_faction_and_company_over_api(self):
        status, entry = self.request("POST", "/api/collection", {"unit_uuid": "u-atlas", "qty": 2})
        status, copy = self.request(
            "PATCH", f"/api/copies/{entry['minis'][0]['id']}",
            {"note": "Gloss black", "faction": "Wolf's Dragoons", "company": "Black Widow Company"},
        )
        self.assertEqual((status, copy["faction"], copy["company"]), (200, "Wolf's Dragoons", "Black Widow Company"))

        status, items = self.request("GET", "/api/collection?copy_faction=dragoons")
        self.assertEqual([i["name"] for i in items], ["Atlas AS7-D"])
        self.assertEqual([m["company"] for m in items[0]["minis"]], ["Black Widow Company", ""])
        status, items = self.request("GET", "/api/collection?copy_company=nothing")
        self.assertEqual(items, [])
        status, items = self.request("GET", "/api/collection?copy_faction=dragoons&copy_company=widow")
        self.assertEqual(len(items), 1)

        status, listed = self.request("GET", "/api/copy-factions")
        self.assertEqual(listed, [{"value": "Wolf's Dragoons", "copies": 1}])
        status, listed = self.request("GET", "/api/copy-companies")
        self.assertEqual(listed, [{"value": "Black Widow Company", "copies": 1}])

        status, copy = self.request("PATCH", f"/api/copies/{copy['id']}", {"company": "Gamma Company"})
        self.assertEqual((status, copy["faction"], copy["company"]), (200, "Wolf's Dragoons", "Gamma Company"))
        status, copy = self.request("PATCH", f"/api/copies/{copy['id']}", {"note": "", "faction": "", "company": ""})
        self.assertEqual((status, copy["note"], copy["faction"], copy["company"]), (200, "", "", ""))
        status, listed = self.request("GET", "/api/copy-factions")
        self.assertEqual(listed, [])

    def test_bulk_set_material_over_api(self):
        status, entry = self.request("POST", "/api/collection", {"unit_uuid": "u-atlas", "qty": 3})
        status, other = self.request("POST", "/api/collection", {"unit_uuid": "u-locust", "qty": 1})
        ids = [m["id"] for m in entry["minis"]] + [m["id"] for m in other["minis"]]

        status, body = self.request("PATCH", "/api/minis", {"ids": ids[:3], "material": "Resin"})
        self.assertEqual((status, body), (200, {"updated": 3}))
        status, items = self.request("GET", "/api/collection")
        by_name = {i["name"]: [m["material"] for m in i["minis"]] for i in items}
        self.assertEqual(by_name["Atlas AS7-D"], ["Resin", "Resin", "Resin"])
        self.assertEqual(by_name["Locust LCT-1V"], [""])

        status, body = self.request("PATCH", "/api/minis", {"ids": ids[:2], "material": ""})
        self.assertEqual((status, body), (200, {"updated": 2}))

        for bad in (
            {"material": "Resin"},                  # no ids
            {"ids": [], "material": "Resin"},       # empty
            {"ids": "1,2", "material": "Resin"},    # not a list
            {"ids": ["x"], "material": "Resin"},    # not numbers
            {"ids": [True], "material": "Resin"},   # a boolean is not an id
            {"ids": ids[:1]},                        # nothing to change
            {"ids": ids[:1] + [99999], "material": "Resin"},  # unknown id
            {"ids": list(range(1, 6002)), "material": "Resin"},  # far too many
        ):
            self.assertEqual(self.request("PATCH", "/api/minis", bad)[0], 400, msg=str(bad)[:60])
        status, items = self.request("GET", "/api/collection")
        by_name = {i["name"]: [m["material"] for m in i["minis"]] for i in items}
        self.assertEqual(by_name["Atlas AS7-D"], ["", "", "Resin"])  # the refused calls changed nothing

    def test_bulk_remove_over_api(self):
        status, entry = self.request("POST", "/api/collection", {"unit_uuid": "u-atlas", "qty": 3})
        status, other = self.request("POST", "/api/collection", {"unit_uuid": "u-locust", "qty": 1})
        atlas_ids = [m["id"] for m in entry["minis"]]
        locust_id = other["minis"][0]["id"]

        for bad in (
            {}, {"ids": []}, {"ids": "1"}, {"ids": ["x"]}, {"ids": [True]}, {"ids": [1.5]},
            {"ids": atlas_ids[:1] + [99999]}, {"ids": list(range(1, 6002))},
        ):
            self.assertEqual(self.request("DELETE", "/api/minis", bad)[0], 400, msg=str(bad)[:60])
        status, items = self.request("GET", "/api/collection")
        self.assertEqual(sum(i["qty"] for i in items), 4)  # the refused calls removed nothing

        status, body = self.request("DELETE", "/api/minis", {"ids": [atlas_ids[0], locust_id]})
        self.assertEqual((status, body), (200, {"removed": 2}))
        status, items = self.request("GET", "/api/collection")
        self.assertEqual([(i["name"], i["qty"]) for i in items], [("Atlas AS7-D", 2)])  # the Locust left with its last mini

    def test_bulk_edit_accepts_every_field_over_api(self):
        status, entry = self.request("POST", "/api/collection", {"unit_uuid": "u-atlas", "qty": 3})
        ids = [m["id"] for m in entry["minis"]]
        for fields in ({"painted": True}, {"box": "Shelf 2"}, {"faction": "Wolf's Dragoons"}, {"tag": "Trial"}):
            status, body = self.request("PATCH", "/api/minis", {"ids": ids[:2], **fields})
            self.assertEqual((status, body), (200, {"updated": 2}), msg=str(fields))
        status, items = self.request("GET", "/api/collection")
        minis = items[0]["minis"]
        self.assertEqual([(m["painted"], m["box"], m["faction"], m["tag"]) for m in minis],
                         [(True, "Shelf 2", "Wolf's Dragoons", "Trial")] * 2 + [(False, "", "", "")])
        self.assertEqual(items[0]["painted"], 2)

    def test_the_named_copy_endpoints_are_gone(self):
        status, entry = self.request("POST", "/api/collection", {"unit_uuid": "u-atlas", "qty": 2})
        mini = entry["minis"][0]
        self.assertNotIn("copies", entry)
        self.assertEqual(self.request("GET", f"/api/collection/{entry['id']}/copies")[0], 404)
        self.assertEqual(self.request("POST", f"/api/collection/{entry['id']}/copies", {"note": "x"})[0], 404)
        self.assertEqual(self.request("DELETE", f"/api/copies/{mini['id']}")[0], 405)  # the path still takes PATCH
        self.assertEqual(self.request("PATCH", f"/api/copies/{mini['id']}", {"note": "x"})[0], 200)

    def test_every_mini_has_material_and_can_be_edited_and_removed_over_api(self):
        status, entry = self.request("POST", "/api/collection", {"unit_uuid": "u-atlas", "qty": 3})
        self.assertEqual(len(entry["minis"]), 3)
        first, second = entry["minis"][0], entry["minis"][1]

        status, mini = self.request("PATCH", f"/api/copies/{first['id']}", {"material": "Resin", "tag": "Trial"})
        self.assertEqual((status, mini["material"], mini["tag"]), (200, "Resin", "Trial"))
        self.assertNotIn("named", mini)

        status, items = self.request("GET", "/api/collection?material=resin")
        self.assertEqual([i["name"] for i in items], ["Atlas AS7-D"])
        status, items = self.request("GET", "/api/collection?material=metal")
        self.assertEqual(items, [])
        status, listed = self.request("GET", "/api/copy-materials")
        self.assertEqual(listed, [{"value": "Resin", "copies": 1}])

        status, body = self.request("DELETE", f"/api/minis/{second['id']}")
        self.assertEqual((status, body), (200, {"removed": second["id"]}))
        status, items = self.request("GET", "/api/collection")
        self.assertEqual(items[0]["qty"], 2)
        self.assertEqual([m["id"] for m in items[0]["minis"]][0], first["id"])

        status, body = self.request("DELETE", "/api/minis/99999")
        self.assertEqual(status, 400)

    def test_copy_paint_box_and_variant_over_api(self):
        conn = db.connect(self.db_path)
        with conn:
            conn.execute("DELETE FROM units WHERE uuid = 'u-atlas-k'")
            conn.execute(
                "INSERT INTO units (uuid, mul_id, chassis, model, kind, type, subtype, tons, weight_class, bv, pv, "
                "year, tech_base, role) VALUES ('u-atlas-k', 99, 'Atlas', 'AS7-K', 'mech', 't', 's', 100, 'w', "
                "1900, 10, 3025, 'Inner Sphere', 'Juggernaut')"
            )
        conn.close()

        def drop_variant():
            cleanup = db.connect(self.db_path)
            with cleanup:
                cleanup.execute("DELETE FROM collection WHERE unit_uuid = 'u-atlas-k'")
                cleanup.execute("DELETE FROM units WHERE uuid = 'u-atlas-k'")
            cleanup.close()

        self.addCleanup(drop_variant)
        status, entry = self.request("POST", "/api/collection", {"unit_uuid": "u-atlas", "qty": 2, "painted": 1})
        unpainted = next(m for m in entry["minis"] if not m["painted"])
        status, copy = self.request(
            "PATCH", f"/api/copies/{unpainted['id']}", {"note": "gloss", "painted": True, "box": "Shelf"}
        )
        self.assertEqual((status, copy["painted"], copy["box"]), (200, True, "Shelf"))
        status, items = self.request("GET", "/api/collection")
        self.assertEqual(items[0]["painted"], 2)
        edited = next(m for m in items[0]["minis"] if m["id"] == copy["id"])
        self.assertEqual((edited["painted"], edited["box"]), (True, "Shelf"))

        status, copy = self.request("PATCH", f"/api/copies/{copy['id']}", {"painted": False, "box": ""})
        self.assertEqual((status, copy["painted"], copy["box"]), (200, False, ""))

        status, body = self.request("POST", f"/api/copies/{copy['id']}/swap", {})
        self.assertEqual(status, 400)
        status, moved = self.request("POST", f"/api/copies/{copy['id']}/swap", {"unit_uuid": "u-atlas-k"})
        self.assertEqual(status, 200)
        status, items = self.request("GET", "/api/collection")
        by_name = {i["name"]: i for i in items}
        self.assertEqual(by_name["Atlas AS7-D"]["qty"], 1)
        self.assertEqual([m["id"] for m in by_name["Atlas AS7-K"]["minis"]], [copy["id"]])

    def test_mini_note_and_tag_over_api(self):
        status, entry = self.request("POST", "/api/collection", {"unit_uuid": "u-atlas", "qty": 2})
        first, second = entry["minis"]
        status, copy = self.request(
            "PATCH", f"/api/copies/{first['id']}", {"note": "Black Widow Company", "tag": "Black Widow Co"}
        )
        self.assertEqual((status, copy["note"], copy["tag"]), (200, "Black Widow Company", "Black Widow Co"))

        status, items = self.request("GET", "/api/collection?tag=widow")
        self.assertEqual([i["name"] for i in items], ["Atlas AS7-D"])
        status, tags = self.request("GET", "/api/copy-tags")
        self.assertEqual(tags, [{"tag": "Black Widow Co", "copies": 1}])

        status, copy = self.request("PATCH", f"/api/copies/{first['id']}", {"note": "Repainted"})
        self.assertEqual((status, copy["note"]), (200, "Repainted"))

        status, copy = self.request("PATCH", f"/api/copies/{second['id']}", {"note": "second"})
        status, items = self.request("GET", "/api/collection")
        self.assertEqual([m["note"] for m in items[0]["minis"]], ["Repainted", "second"])

        status, body = self.request("PATCH", "/api/copies/99999", {"note": "ghost"})
        self.assertEqual(status, 400)

    def test_box_catalog_and_variant_endpoints(self):
        conn = db.connect(self.db_path)
        with conn:
            conn.execute("DELETE FROM box_items")
            conn.execute("DELETE FROM boxes")
            box_id = conn.execute(
                "INSERT INTO boxes (name, kind, section, source_url) VALUES ('Test Box', 'core', 'Core box', 'http://x')"
            ).lastrowid
            conn.executemany(
                "INSERT INTO box_items (box_id, position, mini_name, card_variant, qty, unit_uuid, status) VALUES (?,?,?,?,?,?,?)",
                [
                    (box_id, 0, "Atlas", "AS7-D Atlas", 1, "u-atlas", "resolved"),
                    (box_id, 1, "Locust", "", 1, None, "chassis_only"),
                ],
            )
        conn.close()

        status, found = self.request("GET", "/api/box-catalog?q=test")
        self.assertEqual((status, [b["name"] for b in found]), (200, ["Test Box"]))
        status, detail = self.request("GET", f"/api/box-catalog/{box_id}")
        self.assertEqual([i["status"] for i in detail["items"]], ["resolved", "chassis_only"])

        status, result = self.request("POST", f"/api/box-catalog/{box_id}/add", {})
        self.assertEqual(status, 200)
        self.assertEqual([a["name"] for a in result["added"]], ["Atlas AS7-D"])
        review = result["review"][0]
        self.assertEqual(review["mini_name"], "Locust")
        self.assertIn("Locust LCT-1V", [c["name"] for c in review["candidates"]])

        status, result = self.request(
            "POST", f"/api/box-catalog/{box_id}/add", {"only": {str(review["item_id"]): "u-locust"}}
        )
        self.assertEqual([a["name"] for a in result["added"]], ["Locust LCT-1V"])

        status, items = self.request("GET", "/api/collection?box=test")
        self.assertEqual(sorted(i["name"] for i in items), ["Atlas AS7-D", "Locust LCT-1V"])

        status, body = self.request("POST", f"/api/box-catalog/{box_id}/add", {"only": [1]})
        self.assertEqual(status, 400)
        status, body = self.request("GET", "/api/box-catalog/999999")
        self.assertEqual(status, 400)

        locust = next(i for i in items if i["name"] == "Locust LCT-1V")
        status, variants = self.request("GET", f"/api/collection/{locust['id']}/variants")
        self.assertEqual([v["name"] for v in variants], ["Locust LCT-1V"])
        self.assertEqual([(v["current"], v["on_card"]) for v in variants], [(True, False)])
        status, body = self.request("POST", f"/api/collection/{locust['id']}/swap", {"unit_uuid": "u-atlas"})
        self.assertEqual(status, 400)
        status, body = self.request("POST", f"/api/collection/{locust['id']}/swap", {})
        self.assertEqual(status, 400)
        status, meta = self.request("GET", "/api/meta")
        self.assertEqual(meta["box_catalog"], 1)

    def raw(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers=headers or {})
        res = conn.getresponse()
        data = res.read()
        out = (res.status, dict(res.getheaders()), data)
        conn.close()
        return out

    def test_art_endpoint_serves_cached_images_with_safe_headers(self):
        from batchall import art

        art.forget_failures()
        conn = db.connect(self.db_path)
        conn.execute("UPDATE units SET art = 'Mek/Atlas.png' WHERE uuid = 'u-atlas'")
        conn.commit()
        png = b"\x89PNG\r\n\x1a\n" + b"pixels"
        art.get_art(conn, "u-atlas", cache=self.art_tmp.name, fetch=lambda path: png)  # pre-fill the cache
        conn.close()

        status, headers, data = self.raw("/api/art/u-atlas")
        self.assertEqual((status, data), (200, png))
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("max-age", headers["Cache-Control"])

        status, headers, data = self.raw("/api/art/u-locust")  # a unit with no art
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(data))
        self.assertEqual(self.raw("/api/art/does-not-exist")[0], 404)
        self.assertEqual(self.raw("/api/art/u-atlas", {"Host": "evil.example.com"})[0], 403)

    def test_faction_icon_endpoint_serves_cached_logos_with_safe_headers(self):
        from batchall import art

        art.forget_failures()
        conn = db.connect(self.db_path)
        conn.execute("UPDATE factions SET img = '/images/factions/federated-suns-100.png' WHERE id = 1")
        conn.execute("UPDATE factions SET img = '' WHERE id = 2")
        conn.commit()
        png = b"\x89PNG\r\n\x1a\n" + b"logo"
        art.get_faction_icon(conn, 1, cache=self.art_tmp.name, fetch=lambda path: png)  # pre-fill the cache
        conn.close()

        status, headers, data = self.raw("/api/faction-icon/1")
        self.assertEqual((status, data), (200, png))
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("max-age", headers["Cache-Control"])

        self.assertEqual(self.raw("/api/faction-icon/2")[0], 404)  # no logo
        self.assertEqual(self.raw("/api/faction-icon/999")[0], 404)  # no such faction
        self.assertEqual(self.raw("/api/faction-icon/abc")[0], 404)
        self.assertEqual(self.raw("/api/faction-icon/1", {"Host": "evil.example.com"})[0], 403)

        status, meta = self.request("GET", "/api/meta")
        by_id = {f["id"]: f for f in meta["factions"]}
        self.assertTrue(by_id[1]["has_icon"])
        self.assertFalse(by_id[2]["has_icon"])
        self.assertNotIn("img", by_id[1])

    def test_art_path_never_appears_in_collection_json(self):
        conn = db.connect(self.db_path)
        conn.execute("UPDATE units SET art = 'Mek/Atlas.png' WHERE uuid = 'u-atlas'")
        conn.commit()
        conn.close()
        self.request("POST", "/api/collection", {"unit_uuid": "u-atlas"})
        status, items = self.request("GET", "/api/collection")
        self.assertIs(items[0]["has_art"], True)
        self.assertNotIn("art", items[0])
        self.assertNotIn(b"Mek/Atlas.png", json.dumps(items).encode())

    def test_rejects_foreign_host(self):
        status, _ = self.request("GET", "/api/meta", headers={"Host": "evil.example.com"})
        self.assertEqual(status, 403)

    def test_rejects_non_json_writes(self):
        status, body = self.request(
            "POST", "/api/build", raw="target_bv=3000",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 415)

    def test_validation_errors_are_400(self):
        status, body = self.request("POST", "/api/build", {"target_bv": "lots"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)
        status, body = self.request("POST", "/api/collection", {"unit_uuid": "missing"})
        self.assertEqual(status, 400)

    def test_unknown_route_and_method(self):
        self.assertEqual(self.request("GET", "/api/nope")[0], 404)
        self.assertEqual(self.request("DELETE", "/api/meta")[0], 405)

    def test_static_files_and_traversal(self):
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Batchall</title>", body)
        self.assertEqual(self.request("GET", "/%2e%2e/batchall/config.py")[0], 404)

    def test_meta_lists_formations(self):
        status, meta = self.request("GET", "/api/meta")
        self.assertEqual(status, 200)
        self.assertIn("level2", [f["key"] for f in meta["formations"]])
        self.assertEqual(len(UNITS), meta["catalog"]["units"])

    def test_unit_detail_returns_basics_specs_and_lore(self):
        conn = db.connect(self.db_path)
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO unit_details (uuid, specs, lore) VALUES (?,?,?)",
                ("u-atlas", json.dumps({"movement": {"walk": 3}}), json.dumps({"overview": ["Big."]})),
            )
        conn.close()
        status, unit = self.request("GET", "/api/units/u-atlas")
        self.assertEqual(status, 200)
        self.assertEqual(unit["name"], "Atlas AS7-D")
        self.assertEqual(unit["specs"]["movement"]["walk"], 3)
        self.assertEqual(unit["lore"]["overview"], ["Big."])
        self.assertTrue(unit["has_details"])
        self.assertNotIn("art", unit)

    def test_unit_without_stored_details_still_answers(self):
        status, unit = self.request("GET", "/api/units/u-locust")
        self.assertEqual(status, 200)
        self.assertFalse(unit["has_details"])

    def test_force_export_gives_a_mekbay_link_and_a_mul_file(self):
        self.request("POST", "/api/collection", {"unit_uuid": "u-atlas"})
        self.request("POST", "/api/collection", {"unit_uuid": "u-locust", "qty": 2})
        _, items = self.request("GET", "/api/collection")
        units = [{"collection_id": i["id"], "qty": i["qty"]} for i in items]
        _, force = self.request("POST", "/api/forces", {"name": "Export Lance", "units": units})
        status, out = self.request("GET", f"/api/forces/{force['id']}/export")
        self.assertEqual(status, 200)
        self.assertTrue(out["url"].startswith("https://mekbay.com/?units="))
        self.assertIn("u-atlas", out["url"])
        self.assertEqual(out["filename"], "Export Lance.mul")
        self.assertEqual(out["content"].count("<entity "), 3)

    def test_exporting_a_missing_force_is_an_error(self):
        status, body = self.request("GET", "/api/forces/99999/export")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_unknown_unit_detail_is_404(self):
        status, body = self.request("GET", "/api/units/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
