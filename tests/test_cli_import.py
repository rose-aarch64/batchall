import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import make_db

from bt import db

ROOT = Path(__file__).resolve().parent.parent


class CliImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "cli.db")
        source = make_db()
        target = db.connect(self.db_path)
        with target:
            for row in source.execute("SELECT * FROM units").fetchall():
                target.execute(f"INSERT INTO units VALUES ({','.join('?' for _ in row.keys())})", tuple(row))
        target.close()

    def run_cli(self, *args, stdin=""):
        env = dict(os.environ, BT_DB=self.db_path, BT_DATA_DIR=self.tmp.name)
        return subprocess.run(
            [sys.executable, "-m", "bt", *args],
            cwd=ROOT,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_mul_flag_imports_by_mul_id(self):
        result = self.run_cli("collection", "import", "--mul", stdin="2x MUL 1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(len(out["added"]), 1)
        self.assertEqual(out["unmatched"], [])

    def test_without_flag_a_bare_id_is_treated_as_a_name(self):
        result = self.run_cli("collection", "import", stdin="1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["added"], [])


class ServeDbTests(unittest.TestCase):
    def test_serve_passes_db_path_through(self):
        from unittest import mock

        from bt import cli

        conn = mock.Mock()
        with mock.patch("bt.server.serve") as serve:
            args = cli.make_parser().parse_args(["--db", "/tmp/x.db", "serve", "--port", "0"])
            cli.cmd_serve(args, conn)
        self.assertEqual(serve.call_args.kwargs["db_path"], "/tmp/x.db")


if __name__ == "__main__":
    unittest.main()
