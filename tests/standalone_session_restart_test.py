"""Real encrypted SQLite + fresh manager instances, synthetic SDK/query only.

These are durable restart simulations, not live token-expiry or service tests.
The fake crash unwinds the previous worker/OS-lock context; the next worker opens
the same encrypted database with a NEW store and manager, without prior objects'
in-memory state. Temporary files contain only synthetic fixtures.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import standalone_session_manager as sessions
from standalone_session_store import EncryptedSqliteSessionStore, SessionStoreError
import web_sdk_history_login as history
from tests.standalone_session_manager_test import OWNER, TARGET, SimulatedCrash, bundle, candidate


class SessionRestartIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rivals-session-restart-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "session.sqlite3"
        self.key = b"R" * 32  # Test-only key; never a real credential.
        self.no_network = patch.object(history.urlrequest, "build_opener",
                                       side_effect=AssertionError("no network in restart integration"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def store(self):
        return EncryptedSqliteSessionStore(self.path, self.key)

    def manager(self, store, *, exchange, query):
        return sessions.OwnerSessionManager(store, exchange=exchange, query=query,
                                             random_samples=lambda: [0.25] * 8,
                                             clock_ms=lambda: 1700000001234)

    def good_query(self):
        return Mock(side_effect=lambda sauth, uid: sessions.QueryResult(
            OWNER, True, {"uid": str(uid), "identity": {"uid": str(uid)}}))

    def bootstrap(self, manager):
        self.assertEqual(manager.bootstrap(bundle()).outcome, "session_bootstrapped")

    def assert_ciphertext_only(self):
        contents = self.path.read_bytes()
        for plaintext in (b"fixture-prior-refresh", b"fixture-refresh-1", b"fixture-access-1",
                          b"fixture-refresh-2", b"fixture-access-2", b"fixture-wrapper", b"fixture-mpay"):
            self.assertNotIn(plaintext, contents)

    def test_completed_rotation_survives_new_store_and_manager_then_uses_latest_refresh(self):
        first_store = self.store()
        first_exchange = Mock(side_effect=lambda item: candidate(item, 1))
        first_query = self.good_query()
        first = self.manager(first_store, exchange=first_exchange, query=first_query)
        with first_store.exclusive_owner():
            self.bootstrap(first)
            self.assertTrue(first.lookup(TARGET).owner_authenticated)
            first_revision, first_record = first_store.load(OWNER)
        self.assertEqual(first_record["saved_account"]["refreshToken"], "fixture-refresh-1")
        self.assertTrue(first_record["owner_verified"])
        self.assertIsNone(first_record["candidate"])
        self.assertEqual(first_exchange.call_args.args[0].refresh_token, "fixture-prior-refresh")

        restarted_store = self.store()
        restarted_exchange = Mock(side_effect=lambda item: candidate(item, 2))
        restarted_query = self.good_query()
        restarted = self.manager(restarted_store, exchange=restarted_exchange, query=restarted_query)
        self.assertIsNot(restarted_store, first_store)
        self.assertIsNot(restarted, first)
        with restarted_store.exclusive_owner():
            self.assertTrue(restarted.status()["owner_authenticated"])
            result = restarted.lookup(OWNER)
            revision, record = restarted_store.load(OWNER)
        self.assertEqual(result.outcome, "session_lookup_complete")
        self.assertEqual(restarted_exchange.call_args.args[0].refresh_token, "fixture-refresh-1")
        self.assertEqual(restarted_query.call_args.args[0]["sessionid"], "fixture-access-2")
        self.assertGreater(revision, first_revision)
        self.assertEqual(record["saved_account"]["refreshToken"], "fixture-refresh-2")
        self.assertFalse(restarted.status()["automatic_renewal_verified"])
        first_exchange.assert_called_once()
        restarted_exchange.assert_called_once()
        self.assert_ciphertext_only()

    def test_crash_during_candidate_verification_reopens_and_recovers_under_exclusive_owner(self):
        old_store = self.store()
        old_exchange = Mock(side_effect=candidate)
        old_query = Mock(side_effect=SimulatedCrash())
        old = self.manager(old_store, exchange=old_exchange, query=old_query)
        with self.assertRaises(SimulatedCrash):
            with old_store.exclusive_owner():
                self.bootstrap(old)
                old.lookup(TARGET)

        restarted_store = self.store()
        forbidden_exchange = Mock(side_effect=AssertionError("pending candidate must not rotate SDK token"))
        query = self.good_query()
        restarted = self.manager(restarted_store, exchange=forbidden_exchange, query=query)
        revision_before, saved = restarted_store.load(OWNER)
        self.assertEqual(saved["state"], "candidate_verifying")
        self.assertEqual(saved["candidate"], {"access_token": "fixture-access-1"})
        self.assertEqual(saved["saved_account"]["refreshToken"], "fixture-refresh-1")
        self.assertEqual(restarted.lookup(TARGET).outcome, "session_verification_in_flight")
        self.assertEqual(restarted.recover_pending().outcome, "session_exclusive_ownership_required")
        query.assert_not_called()
        with restarted_store.exclusive_owner():
            result = restarted.recover_pending(exclusive_ownership=True)
            revision_after, saved = restarted_store.load(OWNER)
        self.assertTrue(result.owner_authenticated)
        self.assertEqual(query.call_args.args[1], OWNER)
        self.assertEqual(query.call_args.args[0]["sessionid"], "fixture-access-1")
        self.assertGreater(revision_after, revision_before)
        self.assertEqual(saved["state"], "ready")
        self.assertEqual(saved["saved_account"]["refreshToken"], "fixture-refresh-1")
        self.assertIsNone(saved["candidate"])
        forbidden_exchange.assert_not_called()
        old_exchange.assert_called_once()
        self.assert_ciphertext_only()

    def test_crash_after_candidate_commit_before_verification_reopens_saved_candidate(self):
        old_store = self.store()
        old_exchange, old_query = Mock(side_effect=candidate), self.good_query()
        old = self.manager(old_store, exchange=old_exchange, query=old_query)
        real_cas = old_store.compare_and_swap

        def crash_after_durable_candidate(owner_uid, expected_revision, replacement):
            revision = real_cas(owner_uid, expected_revision, replacement)
            if replacement["state"] == "candidate_pending":
                raise SimulatedCrash()
            return revision

        with self.assertRaises(SimulatedCrash):
            with old_store.exclusive_owner():
                self.bootstrap(old)
                with patch.object(old_store, "compare_and_swap", side_effect=crash_after_durable_candidate):
                    old.lookup(TARGET)
        old_query.assert_not_called()
        restarted_store = self.store()
        forbidden_exchange = Mock(side_effect=AssertionError("no SDK exchange for saved candidate"))
        query = self.good_query()
        restarted = self.manager(restarted_store, exchange=forbidden_exchange, query=query)
        self.assertEqual(restarted.status()["state"], "candidate_pending")
        with restarted_store.exclusive_owner():
            self.assertTrue(restarted.recover_pending(exclusive_ownership=True).owner_authenticated)
        self.assertEqual(query.call_args.args[0]["sessionid"], "fixture-access-1")
        forbidden_exchange.assert_not_called()
        self.assert_ciphertext_only()

    def test_refresh_in_flight_restart_never_retries_original_token_even_with_exclusive_recovery(self):
        old_store = self.store()
        old_exchange = Mock(side_effect=SimulatedCrash())
        old = self.manager(old_store, exchange=old_exchange, query=self.good_query())
        with self.assertRaises(SimulatedCrash):
            with old_store.exclusive_owner():
                self.bootstrap(old)
                old.lookup(TARGET)

        restarted_store = self.store()
        forbidden_exchange, query = Mock(side_effect=AssertionError("unknown rotation")), self.good_query()
        restarted = self.manager(restarted_store, exchange=forbidden_exchange, query=query)
        before = restarted_store.load(OWNER)
        self.assertEqual(before[1]["state"], "refresh_in_flight")
        self.assertEqual(before[1]["saved_account"]["refreshToken"], "fixture-prior-refresh")
        self.assertIsNone(before[1]["candidate"])
        with restarted_store.exclusive_owner():
            self.assertEqual(restarted.lookup(TARGET).outcome, "session_rotation_unknown")
            self.assertEqual(restarted.recover_pending(exclusive_ownership=True).outcome, "session_rotation_unknown")
            self.assertEqual(restarted.lookup(OWNER).outcome, "session_rotation_unknown")
        self.assertEqual(restarted_store.load(OWNER), before)
        self.assertTrue(restarted.status()["rotation_uncertain"])
        forbidden_exchange.assert_not_called()
        query.assert_not_called()
        old_exchange.assert_called_once()

    def test_attempted_exchange_error_persists_rotation_unknown_and_does_not_retry_after_restart(self):
        old_store = self.store()
        old_exchange = Mock(side_effect=history.HistoryLoginError(
            "history_request_failed", request_attempted=True))
        old = self.manager(old_store, exchange=old_exchange, query=self.good_query())
        with old_store.exclusive_owner():
            self.bootstrap(old)
            self.assertEqual(old.lookup(TARGET).outcome, "session_rotation_unknown")
        restarted_store = self.store()
        forbidden_exchange, query = Mock(side_effect=AssertionError("unknown rotation")), self.good_query()
        restarted = self.manager(restarted_store, exchange=forbidden_exchange, query=query)
        self.assertEqual(restarted.status()["state"], "rotation_unknown")
        before = restarted_store.load(OWNER)
        with restarted_store.exclusive_owner():
            self.assertEqual(restarted.lookup(TARGET).outcome, "session_rotation_unknown")
            self.assertEqual(restarted.recover_pending(exclusive_ownership=True).outcome, "session_rotation_unknown")
        self.assertEqual(restarted_store.load(OWNER), before)
        forbidden_exchange.assert_not_called()
        query.assert_not_called()
        old_exchange.assert_called_once()

    def test_verified_253_persists_latest_refresh_for_next_lookup_after_restart(self):
        old_store = self.store()
        old_exchange = Mock(side_effect=candidate)
        rejected = Mock(return_value=sessions.QueryResult(OWNER, False, auth_rejection_code=253))
        old = self.manager(old_store, exchange=old_exchange, query=rejected)
        with old_store.exclusive_owner():
            self.bootstrap(old)
            result = old.lookup(TARGET)
        self.assertEqual(result.outcome, "session_access_rejected")
        old_exchange.assert_called_once()
        rejected.assert_called_once()

        restarted_store = self.store()
        next_exchange = Mock(side_effect=lambda item: candidate(item, 2))
        query = self.good_query()
        restarted = self.manager(restarted_store, exchange=next_exchange, query=query)
        saved = restarted_store.load(OWNER)[1]
        self.assertEqual(saved["state"], "ready")
        self.assertFalse(saved["owner_verified"])
        self.assertIsNone(saved["candidate"])
        self.assertEqual(saved["saved_account"]["refreshToken"], "fixture-refresh-1")
        with restarted_store.exclusive_owner():
            self.assertTrue(restarted.lookup(TARGET).owner_authenticated)
        next_exchange.assert_called_once()
        self.assertEqual(next_exchange.call_args.args[0].refresh_token, "fixture-refresh-1")
        self.assertEqual(query.call_args.args[0]["sessionid"], "fixture-access-2")
        self.assertEqual(restarted_store.load(OWNER)[1]["saved_account"]["refreshToken"], "fixture-refresh-2")
        self.assert_ciphertext_only()

    def test_unclassified_auth_rejection_remains_recovery_required_across_restart(self):
        old_store = self.store()
        old_exchange = Mock(side_effect=candidate)
        rejected = Mock(return_value=sessions.QueryResult(OWNER, False, auth_rejection_code=1911))
        old = self.manager(old_store, exchange=old_exchange, query=rejected)
        with old_store.exclusive_owner():
            self.bootstrap(old)
            self.assertEqual(old.lookup(TARGET).outcome, "session_owner_unverified")
        restarted_store = self.store()
        forbidden_exchange, query = Mock(side_effect=AssertionError("no SDK retry for unknown AUTH")), self.good_query()
        restarted = self.manager(restarted_store, exchange=forbidden_exchange, query=query)
        self.assertEqual(restarted.status()["state"], "candidate_recovery_required")
        with restarted_store.exclusive_owner():
            self.assertEqual(restarted.lookup(TARGET).outcome, "session_recovery_required")
            query.assert_not_called()
            self.assertTrue(restarted.recover_pending(exclusive_ownership=True).owner_authenticated)
        self.assertEqual(query.call_args.args[0]["sessionid"], "fixture-access-1")
        self.assertEqual(query.call_args.args[1], OWNER)
        forbidden_exchange.assert_not_called()

    def test_new_worker_must_acquire_real_exclusive_owner_lock_before_recovery(self):
        old_store = self.store()
        old = self.manager(old_store, exchange=Mock(side_effect=candidate), query=Mock(side_effect=SimulatedCrash()))
        with self.assertRaises(SimulatedCrash):
            with old_store.exclusive_owner():
                self.bootstrap(old)
                old.lookup(TARGET)
        restarted_store, competing_store = self.store(), self.store()
        forbidden_exchange, query = Mock(side_effect=AssertionError("no SDK retry")), self.good_query()
        restarted = self.manager(restarted_store, exchange=forbidden_exchange, query=query)
        with competing_store.exclusive_owner():
            with self.assertRaisesRegex(SessionStoreError, "^session_store_in_use$"):
                with restarted_store.exclusive_owner():
                    restarted.recover_pending(exclusive_ownership=True)
        query.assert_not_called()
        with restarted_store.exclusive_owner():
            self.assertTrue(restarted.recover_pending(exclusive_ownership=True).owner_authenticated)
        forbidden_exchange.assert_not_called()


if __name__ == "__main__":
    unittest.main()
