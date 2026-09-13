from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from standalone_session_store import EncryptedSqliteSessionStore, SessionStoreError


class SessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "session.sqlite3"
        self.key = b"s" * 32
        self.store = EncryptedSqliteSessionStore(self.path, self.key)
        self.record = {"state": "candidate_pending", "token": "synthetic-private-token", "account": {"device": "test"}}

    def tearDown(self):
        self.temp.cleanup()

    def test_restart_roundtrip_and_ciphertext_only(self):
        self.assertIsNone(self.store.load(1370598422))
        self.assertEqual(self.store.compare_and_swap(1370598422, None, self.record), 1)
        other = EncryptedSqliteSessionStore(self.path, self.key)
        self.assertEqual(other.load(1370598422), (1, self.record))
        self.assertNotIn(b"synthetic-private-token", self.path.read_bytes())
        self.assertNotIn(b"candidate_pending", self.path.read_bytes())
        self.assertNotIn("synthetic", repr(self.store))

    def test_cas_does_not_overwrite_a_newer_rotation(self):
        self.store.compare_and_swap(1370598422, None, self.record)
        with self.assertRaisesRegex(SessionStoreError, "session_store_conflict"):
            self.store.compare_and_swap(1370598422, None, {"bad": True})
        self.assertEqual(self.store.compare_and_swap(1370598422, 1, {"state": "ready"}), 2)
        with self.assertRaises(SessionStoreError):
            self.store.compare_and_swap(1370598422, 1, self.record)
        self.assertEqual(self.store.load(1370598422), (2, {"state": "ready"}))

    def test_key_owner_and_revision_authentication(self):
        self.store.compare_and_swap(1370598422, None, self.record)
        wrong = EncryptedSqliteSessionStore(self.path, b"x" * 32)
        with self.assertRaises(SessionStoreError):
            wrong.load(1370598422)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE sessions SET owner_uid = '1234'")
        with self.assertRaises(SessionStoreError):
            self.store.load(1234)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE sessions SET owner_uid = '1370598422', revision = 2")
        with self.assertRaises(SessionStoreError):
            self.store.load(1370598422)

    def test_bounds_no_coercion_and_independent_copies(self):
        for owner in [True, "1370598422", 0, -1, 2**63]:
            with self.assertRaises(SessionStoreError):
                self.store.load(owner)
        for record in [[], {"bad": float("inf")}, {"bad": "x" * (256 * 1024)}]:
            with self.assertRaises(SessionStoreError):
                self.store.compare_and_swap(1370598422, None, record)
        self.store.compare_and_swap(1370598422, None, self.record)
        loaded = self.store.load(1370598422)[1]
        loaded["account"]["device"] = "changed"
        self.assertEqual(self.store.load(1370598422)[1]["account"]["device"], "test")

    def test_lock_rejects_second_worker_and_releases(self):
        other = EncryptedSqliteSessionStore(self.path, self.key)
        with self.store.exclusive_owner():
            with self.assertRaisesRegex(SessionStoreError, "session_store_in_use"):
                with other.exclusive_owner():
                    self.fail("Second worker admitted")
        with other.exclusive_owner():
            self.assertTrue(other._exclusive)

    def test_unrelated_database_is_not_modified(self):
        path = Path(self.temp.name) / "unrelated.sqlite3"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE keep_me (value TEXT)")
        before = path.read_bytes()
        with self.assertRaises(SessionStoreError):
            EncryptedSqliteSessionStore(path, self.key)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
