import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import make_db

from batchall import collection, db

ROOT = Path(__file__).resolve().parent.parent


class CliMiniTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "cli.db")
        source = make_db()
        target = db.connect(self.db_path)
        with target:
            for row in source.execute("SELECT * FROM units").fetchall():
                target.execute(f"INSERT INTO units VALUES ({','.join('?' for _ in row.keys())})", tuple(row))
        entry = collection.add(target, "u-marauder", qty=1)
        self.mini_id = entry["minis"][0]["id"]
        self.entry_id = entry["id"]
        target.close()

    def run_cli(self, *args):
        env = dict(os.environ, BATCHALL_DB=self.db_path, BATCHALL_DATA_DIR=self.tmp.name)
        return subprocess.run(
            [sys.executable, "-m", "batchall", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_sets_faction_company_material_and_paint(self):
        result = self.run_cli(
            "collection", "mini", "--id", str(self.mini_id),
            "--faction", "Wolf's Dragoons", "--company", "Black Widow Company",
            "--material", "cgl plastic", "--painted", "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["faction"], "Wolf's Dragoons")
        self.assertEqual(out["company"], "Black Widow Company")
        self.assertEqual(out["material"], "CGL Plastic")
        self.assertTrue(out["painted"])

    def test_painted_flows_into_the_entry_count(self):
        self.run_cli("collection", "mini", "--id", str(self.mini_id), "--painted", "1")
        listing = json.loads(self.run_cli("collection", "list").stdout)
        self.assertEqual(listing[0]["painted"], 1)

    def test_fields_left_out_keep_their_values(self):
        self.run_cli("collection", "mini", "--id", str(self.mini_id), "--tag", "Gothic", "--material", "Resin")
        out = json.loads(self.run_cli("collection", "mini", "--id", str(self.mini_id), "--company", "Alpha").stdout)
        self.assertEqual(out["tag"], "Gothic")
        self.assertEqual(out["material"], "Resin")
        self.assertEqual(out["company"], "Alpha")

    def test_none_clears_the_material(self):
        self.run_cli("collection", "mini", "--id", str(self.mini_id), "--material", "Metal")
        out = json.loads(self.run_cli("collection", "mini", "--id", str(self.mini_id), "--material", "none").stdout)
        self.assertEqual(out["material"], "")

    def test_unknown_material_is_refused_and_nothing_changes(self):
        result = self.run_cli(
            "collection", "mini", "--id", str(self.mini_id), "--material", "Wood", "--company", "Alpha"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("material must be one of", json.loads(result.stdout)["error"])
        out = json.loads(self.run_cli("collection", "mini", "--id", str(self.mini_id)).stdout)
        self.assertEqual(out["company"], "")

    def test_unknown_mini_id_is_an_error(self):
        result = self.run_cli("collection", "mini", "--id", "9999", "--tag", "x")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no copy with id 9999", json.loads(result.stdout)["error"])

    def test_missing_id_is_an_error(self):
        result = self.run_cli("collection", "mini", "--tag", "x")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--id", json.loads(result.stdout)["error"])

    def test_no_fields_shows_the_mini(self):
        out = json.loads(self.run_cli("collection", "mini", "--id", str(self.mini_id)).stdout)
        self.assertEqual(out["id"], self.mini_id)
        self.assertEqual(out["collection_id"], self.entry_id)


if __name__ == "__main__":
    unittest.main()
