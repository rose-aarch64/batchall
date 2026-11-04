import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import make_db

from bt import db, forces

ROOT = Path(__file__).resolve().parent.parent


class ExportCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "cli.db")
        source = make_db()
        target = db.connect(self.db_path)
        with target:
            for row in source.execute("SELECT * FROM units").fetchall():
                target.execute(f"INSERT INTO units VALUES ({','.join('?' for _ in row.keys())})", tuple(row))
        from bt import collection

        entry = collection.add(target, "u-atlas", qty=1)
        self.force = forces.save(target, "CLI Lance", "lance", [{"collection_id": entry["id"], "qty": 1}])
        target.close()

    def run_cli(self, *args):
        env = dict(os.environ, BT_DB=self.db_path, BT_DATA_DIR=self.tmp.name)
        return subprocess.run(
            [sys.executable, "-m", "bt", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30
        )

    def test_default_prints_the_mekbay_link(self):
        result = self.run_cli("forces", "export", "--id", str(self.force["id"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["url"].startswith("https://mekbay.com/?units=u-atlas"))

    def test_mul_format_writes_a_file(self):
        out = os.path.join(self.tmp.name, "force.mul")
        result = self.run_cli("forces", "export", "--id", str(self.force["id"]), "--format", "mul", "--out", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('chassis="Atlas"', Path(out).read_text(encoding="utf-8"))

    def test_mul_format_without_out_prints_the_xml(self):
        result = self.run_cli("forces", "export", "--id", str(self.force["id"]), "--format", "mul")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<unit ", result.stdout)

    def test_missing_id_is_reported(self):
        result = self.run_cli("forces", "export")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
