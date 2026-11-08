"""A base test case for the share link routes: alice with a small collection and a link, and bob."""

import base64
import sys
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from support import mark_multiuser, seed_catalog  # noqa: E402

from batchall import collection, db, forces, photos, server  # noqa: E402
from hosted import accounts, sharing  # noqa: E402

PASSWORD = "correct horse battery"
JPEG = b"\xff\xd8\xff\xe0" + b"fake photo"


class ShareFixture(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.shared = Path(self._tmp.name) / "batchall.db"
        override = override_settings(BATCHALL_SHARED_DB=str(self.shared))
        override.enable()
        self.addCleanup(override.disable)
        saved_cache = server.art_cache_dir
        server.art_cache_dir = Path(self._tmp.name) / "art"
        self.addCleanup(setattr, server, "art_cache_dir", saved_cache)

        conn = db.connect_shared(self.shared)
        seed_catalog(conn)
        conn.close()
        mark_multiuser(self.shared)

        self.alice = accounts.create_user("alice", PASSWORD)
        self.bob = accounts.create_user("bob", PASSWORD)
        self.seed_alice()
        self.seed_bob()
        self.link = sharing.create_link(self.alice)
        self.base = f"/s/{self.link.token}"

    def seed_alice(self):
        conn = db.connect_user(self.alice.id, self.shared)
        try:
            self.entry = collection.add(conn, "u-atlas", qty=1)
            self.mini = collection.get(conn, self.entry["id"])["minis"][0]["id"]
            photos.save(conn, self.mini, base64.b64encode(JPEG).decode(), 800, 600)
            self.force = forces.save(
                conn, "Alice lance", "lance", [{"collection_id": self.entry["id"], "qty": 1}]
            )
        finally:
            conn.close()

    def seed_bob(self):
        conn = db.connect_user(self.bob.id, self.shared)
        try:
            entry = collection.add(conn, "u-locust", qty=3)
            self.bob_mini = max(m["id"] for m in collection.get(conn, entry["id"])["minis"])
            photos.save(conn, self.bob_mini, base64.b64encode(JPEG).decode(), 800, 600)
        finally:
            conn.close()
        alice_minis = {m["id"] for m in self.alice_rows("SELECT id FROM copies")}
        assert self.bob_mini not in alice_minis, "bob's mini id must not exist in alice's database"

    def alice_rows(self, sql):
        conn = db.connect_user_readonly(self.alice.id, self.shared)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def snapshot_alice(self):
        """Row count and rows for every table in alice's own database file, so an update shows too."""
        conn = db.connect_user_readonly(self.alice.id, self.shared)
        try:
            names = [
                r["name"]
                for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type = 'table' ORDER BY name")
            ]
            snapshot = {}
            for name in names:
                rows = sorted((tuple(r) for r in conn.execute(f'SELECT * FROM main."{name}"')), key=repr)
                snapshot[name] = (len(rows), rows)
            return snapshot
        finally:
            conn.close()
