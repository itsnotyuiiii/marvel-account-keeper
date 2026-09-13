"""Desktop lifecycle tests: temporary encrypted stores and synthetic SDK only."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from career_provider import CareerUnavailable
from local_native_provider import LocalNativeCareerProvider
from local_native_session import LocalNativeConnection, MemorySessionStore
from standalone_session_store import EncryptedSqliteSessionStore
from standalone_session_manager import QueryResult
from tests.local_native_provider_test import account, profile
from tests.standalone_session_manager_test import OWNER, bundle, candidate


class FakeLogin:
    def __init__(self, factory, *, on_bundle, is_active):
        self.callback, self.active = on_bundle, is_active
        self.running = False
    def start(self):
        self.running = self.active()
    def status(self):
        return {"running": self.running, "outcome": "local_login_waiting" if self.running else "local_login_idle"}
    def deliver(self, value):
        try:
            return self.callback(value)
        finally:
            self.running = False
            self.callback = self.active = None
    def cancel(self):
        self.running = False
        self.callback = self.active = None


class LocalNativeSessionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = b"S" * 32
        self.exchange = Mock(side_effect=candidate)
        self.query = Mock(side_effect=lambda sauth, uid: QueryResult(OWNER, True, profile(uid)))
        self.providers = []
        self.logins = []
        self.factory = Mock(side_effect=self.make_provider)
        self.connection = self.new_connection()
        self.network = patch("socket.socket", side_effect=AssertionError("no network"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def make_provider(self, store, **kwargs):
        value = LocalNativeCareerProvider(store, query=self.query, exchange=self.exchange)
        self.providers.append(value)
        return value

    def make_login(self, *args, **kwargs):
        value = FakeLogin(*args, **kwargs)
        self.logins.append(value)
        return value

    def new_connection(self):
        value = LocalNativeConnection(self.root, self.root, lambda: self.key,
            provider_factory=self.factory, login_factory=self.make_login)
        value.enable_browser(Mock())
        self.addCleanup(value.close)
        return value

    def connect(self, persist=True):
        self.connection.connect(persist=persist)
        self.assertTrue(self.logins[-1].deliver(bundle()))

    def error(self, action, code):
        with self.assertRaises(CareerUnavailable) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_status_is_lazy_and_locked_never_opens_store(self):
        self.assertTrue(self.connection.status()["can_connect"])
        self.factory.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])
        self.key = None
        self.error(self.connection.status, "locked")
        self.error(lambda: self.connection.connect(persist=True), "locked")
        self.factory.assert_not_called()

    def test_encrypted_persistence_survives_close_without_plaintext_tokens(self):
        self.connect()
        self.assertEqual(self.connection.status()["persistence"], "encrypted_local")
        self.assertFalse(self.connection.status()["connected"])
        self.connection.refresh_account(account())
        self.assertTrue(self.connection.status()["connected"])
        self.connection.close()
        for path in self.root.iterdir():
            self.assertNotIn(b"fixture-", path.read_bytes())
        reopened = self.new_connection()
        self.assertTrue(reopened.status()["connected"])
        self.assertEqual(reopened.refresh_account(account())["uid"], str(OWNER))
        self.assertEqual(self.exchange.call_count, 2)

    def test_session_only_never_creates_files_and_lock_discards_it(self):
        self.connect(persist=False)
        self.connection.refresh_account(account())
        self.assertEqual(self.connection.status()["persistence"], "memory")
        self.assertIsNotNone(self.connection.cached_account(account()))
        self.connection.suspend()
        self.assertIsNone(self.connection.cached_account(account()))
        self.assertEqual(self.connection.status()["state"], "unconfigured")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_pending_login_status_and_double_connect(self):
        self.connection.connect(persist=True)
        self.assertTrue(self.connection.status()["connection_in_progress"])
        self.assertFalse(self.connection.status()["can_connect"])
        self.error(lambda: self.connection.connect(), "career_busy")

    def test_lock_then_unlock_same_key_rejects_late_login(self):
        self.connection.connect(persist=True)
        callback = self.logins[-1].callback
        self.connection.suspend()
        self.assertFalse(callback(bundle()))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_key_change_without_lock_rejects_handoff(self):
        self.connection.connect(persist=True)
        self.key = b"T" * 32
        self.assertFalse(self.logins[-1].deliver(bundle()))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_bundle_does_not_create_database_and_can_retry(self):
        self.connection.connect(persist=True)
        with self.assertRaises(ValueError):
            self.logins[-1].deliver({})
        self.assertEqual(list(self.root.iterdir()), [])
        self.connect()

    def test_empty_existing_store_can_be_bootstrapped(self):
        EncryptedSqliteSessionStore(self.root / "netease-session.sqlite3", self.key)
        self.assertTrue(self.connection.status()["can_connect"])
        self.error(lambda: self.connection.connect(persist=False), "career_persistence_required")
        self.connect(persist=True)
        self.assertEqual(self.connection.status()["persistence"], "encrypted_local")
        self.assertEqual(len(self.providers), 1)

    def test_existing_session_is_not_replaced_and_wrong_key_cannot_connect(self):
        self.connect()
        self.error(lambda: self.connection.connect(persist=True), "career_already_connected")
        self.connection.suspend()
        self.key = b"T" * 32
        self.assertEqual(self.connection.status()["state"], "unavailable")
        self.error(lambda: self.connection.connect(persist=True), "career_already_connected")

    def test_two_suspend_calls_wait_for_admitted_rotation(self):
        self.connect()
        entered, release = threading.Event(), threading.Event()
        def slow_query(sauth, uid):
            entered.set()
            if not release.wait(3):
                raise AssertionError("timeout")
            return QueryResult(OWNER, True, profile(uid))
        self.query.side_effect = slow_query
        refresh = threading.Thread(target=lambda: self.connection.refresh_account(account()))
        refresh.start()
        self.assertTrue(entered.wait(1))
        drains = [threading.Thread(target=self.connection.suspend) for _ in range(2)]
        for worker in drains:
            worker.start()
        self.assertTrue(self.connection._suspending)
        self.error(self.connection.status, "career_busy")
        release.set()
        for worker in [refresh, *drains]:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertIsNone(self.connection._provider)
        self.assertIsNone(self.connection._key)
        self.assertIsNone(self.connection.cached_account(account()))

    def test_connection_status_does_not_hold_guard_when_calling_login(self):
        self.connection.connect()
        def status_with_callback_lock():
            acquired = threading.Event()
            def use_controller_guard():
                with self.connection._guard:
                    acquired.set()
            worker = threading.Thread(target=use_controller_guard)
            worker.start()
            self.assertTrue(acquired.wait(1), "controller/login lock inversion")
            worker.join(1)
            return {"running": True}
        self.logins[-1].status = status_with_callback_lock
        self.assertTrue(self.connection.status()["connection_in_progress"])

    def test_status_contains_no_session_material(self):
        self.connect()
        text = json.dumps(self.connection.status())
        self.assertNotIn("fixture", text)
        self.assertNotIn("refreshToken", text)
        self.assertNotIn("sessionid", text)

    def test_no_browser_or_nonboolean_persistence_rejected(self):
        self.connection.enable_browser(None)
        self.error(lambda: self.connection.connect(persist=True), "career_browser_unavailable")
        for value in (None, 0, 1, "true", {}):
            self.error(lambda: self.connection.connect(persist=value), "career_invalid_request")

    def test_memory_store_defensive_copy_and_conflicts(self):
        store = MemorySessionStore()
        with store.exclusive_owner():
            source = {"synthetic": [1]}
            self.assertEqual(store.compare_and_swap(OWNER, None, source), 1)
            source["synthetic"].append(2)
            self.assertEqual(store.load(OWNER)[1], {"synthetic": [1]})
            with self.assertRaises(ValueError):
                store.compare_and_swap(OWNER, None, source)
        self.assertIsNone(store.load(OWNER))


if __name__ == "__main__":
    unittest.main()
