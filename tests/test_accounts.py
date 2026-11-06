import tempfile
import threading
import unittest
from pathlib import Path

from batchall import accounts, db

SECRET = b"test-secret"
GOOD_PASSWORD = "correct-horse-1"


class AccountsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "shared.db"
        self.conn = db.connect_shared(self.path)
        self.addCleanup(self.conn.close)


class HandleAndPasswordTests(AccountsTestCase):
    def test_handle_rules(self):
        self.assertEqual(accounts.normalize_handle("Rose"), "rose")
        for bad in ("ab", "a" * 31, "has space", "under_score", "../x"):
            with self.subTest(bad=bad):
                with self.assertRaises(accounts.AccountError):
                    accounts.normalize_handle(bad)
        for reserved in ("api", "admin", "login", "u"):
            with self.subTest(reserved=reserved):
                with self.assertRaises(accounts.AccountError):
                    accounts.normalize_handle(reserved)

    def test_handles_are_unique_ignoring_case(self):
        accounts.create_user(self.conn, "rose", GOOD_PASSWORD)
        with self.assertRaises(accounts.AccountError):
            accounts.create_user(self.conn, "ROSE", GOOD_PASSWORD)

    def test_password_rules(self):
        with self.assertRaises(accounts.AccountError):
            accounts.hash_password("a" * 9)
        accounts.hash_password("a" * 10)
        with self.assertRaises(accounts.AccountError):
            accounts.hash_password("a" * 257)
        password = "pässwörd-ñandú"
        self.assertEqual(len(password), 14)
        stored = accounts.hash_password(password)
        self.assertTrue(accounts.verify_password(password, stored))
        self.assertFalse(accounts.verify_password(password + "x", stored))

    def test_hash_is_salted_and_not_returned(self):
        first = accounts.hash_password(GOOD_PASSWORD)
        second = accounts.hash_password(GOOD_PASSWORD)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("scrypt$16384$8$1$"))
        user = accounts.create_user(self.conn, "rose", GOOD_PASSWORD)
        self.assertNotIn("password_hash", user)
        for row in accounts.list_users(self.conn):
            self.assertNotIn("password_hash", row)

    def test_authenticate(self):
        created = accounts.create_user(self.conn, "rose", GOOD_PASSWORD)
        self.assertEqual(accounts.authenticate(self.conn, "Rose", GOOD_PASSWORD), created)
        self.assertIsNone(accounts.authenticate(self.conn, "rose", "wrong-password"))
        self.assertIsNone(accounts.authenticate(self.conn, "nobody", GOOD_PASSWORD))


class InviteTests(AccountsTestCase):
    def test_sign_up_requires_a_valid_unused_invite(self):
        for code in ("", None, "not-a-real-code"):
            with self.subTest(code=code):
                with self.assertRaisesRegex(accounts.AccountError, "invalid invite code"):
                    accounts.sign_up(self.conn, SECRET, "rose", GOOD_PASSWORD, code)
        code = accounts.make_invite(self.conn, SECRET, None)
        user = accounts.sign_up(self.conn, SECRET, "rose", GOOD_PASSWORD, code)
        row = self.conn.execute("SELECT used_by FROM invites").fetchone()
        self.assertEqual(row["used_by"], user["id"])
        with self.assertRaisesRegex(accounts.AccountError, "invalid invite code"):
            accounts.sign_up(self.conn, SECRET, "mira", GOOD_PASSWORD, code)
        self.assertIsNone(accounts.get_user_by_handle(self.conn, "mira"))

    def test_invite_code_is_stored_hashed(self):
        code = accounts.make_invite(self.conn, SECRET, None)
        stored = self.conn.execute("SELECT code_hash FROM invites").fetchone()["code_hash"]
        self.assertNotEqual(stored, code)
        self.assertEqual(len(stored), 64)

    def test_failed_sign_up_leaves_the_invite_unused(self):
        accounts.create_user(self.conn, "rose", GOOD_PASSWORD)
        code = accounts.make_invite(self.conn, SECRET, None)
        with self.assertRaises(accounts.AccountError):
            accounts.sign_up(self.conn, SECRET, "rose", GOOD_PASSWORD, code)
        self.assertIsNone(self.conn.execute("SELECT used_by FROM invites").fetchone()["used_by"])
        accounts.sign_up(self.conn, SECRET, "mira", GOOD_PASSWORD, code)

    def test_two_sign_ups_race_for_one_invite(self):
        code = accounts.make_invite(self.conn, SECRET, None)
        barrier = threading.Barrier(2)
        results = {}

        def worker(handle):
            conn = db.connect_shared(self.path)
            try:
                barrier.wait()
                try:
                    results[handle] = accounts.sign_up(conn, SECRET, handle, GOOD_PASSWORD, code)
                except accounts.AccountError as exc:
                    results[handle] = exc
            finally:
                conn.close()

        threads = [threading.Thread(target=worker, args=(h,)) for h in ("rose", "mira")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [r for r in results.values() if isinstance(r, dict)]
        losers = [r for r in results.values() if isinstance(r, accounts.AccountError)]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(losers), 1)
        count = self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(count, 1)


class FirstAdminTests(AccountsTestCase):
    def test_first_admin(self):
        self.assertIsNone(accounts.first_admin(self.conn))
        accounts.create_user(self.conn, "plain", GOOD_PASSWORD)
        self.assertIsNone(accounts.first_admin(self.conn))
        first = accounts.create_user(self.conn, "boss", GOOD_PASSWORD, role="admin")
        accounts.create_user(self.conn, "boss2", GOOD_PASSWORD, role="admin")
        self.assertEqual(accounts.first_admin(self.conn)["id"], first["id"])


if __name__ == "__main__":
    unittest.main()
