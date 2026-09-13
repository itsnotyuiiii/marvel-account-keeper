"""Synthetic renewal candidates and in-memory CAS only; no real credentials."""

import copy
from dataclasses import replace
import json
import threading
import unittest
from unittest.mock import Mock, patch

import standalone_session_manager as sessions
import web_sdk_history_login as history


OWNER = sessions.OWNER_UID
TARGET = 10826896958


def bundle():
    return {
        "selected_uid": OWNER,
        "sauth": {"gameid": "x20naxx2gb", "login_channel": "netease_global",
                  "app_channel": "netease.allysdk_global", "platform": "pc",
                  "sdk_version": "1.0.0", "sdkuid": "123456789012345",
                  "sessionid": "fixture-prior-access", "deviceid": "fixture-prior-device",
                  "udid": "fixture-wrapper", "client_login_sn": "fixture-prior-serial"},
        "saved_account": {"userId": 123456789012345, "refreshToken": "fixture-prior-refresh",
                          "method": "steam", "clientPlatform": "p", "deviceId": "fixture-saved-device"},
        "mpay_context": dict(history.PUBLIC_CONTEXT, ci="fixture-mpay", lang="en_US",
                             device_id="fixture-http-device"),
        "wrapper_udid": "fixture-wrapper",
    }


def candidate(inputs, number=1):
    return history.HistoryLoginCandidate(inputs.expected_owner_uid, inputs.sauth_sdkuid,
                                         inputs.saved_device_id, f"fixture-access-{number}",
                                         f"fixture-refresh-{number}")


class SimulatedCrash(BaseException):
    pass


class MemoryStore:
    def __init__(self):
        self.value = None
        self.calls = []
        self.load_count = 0
        self.fail_state = None
        self.commit_then_fail_state = None
        self.lock = threading.Lock()

    def load(self, owner_uid):
        if owner_uid != OWNER:
            raise AssertionError("wrong owner")
        with self.lock:
            self.load_count += 1
            return copy.deepcopy(self.value)

    def compare_and_swap(self, owner_uid, expected_revision, replacement):
        if owner_uid != OWNER:
            raise AssertionError("wrong owner")
        with self.lock:
            self.calls.append((expected_revision, copy.deepcopy(replacement)))
            current_revision = None if self.value is None else self.value[0]
            if current_revision != expected_revision:
                raise RuntimeError("fixture-CAS-conflict-secret")
            state = replacement["state"]
            if state == self.fail_state:
                self.fail_state = None
                raise RuntimeError("fixture-store-write-secret")
            revision = 1 if current_revision is None else current_revision + 1
            self.value = revision, copy.deepcopy(replacement)
            if state == self.commit_then_fail_state:
                self.commit_then_fail_state = None
                raise RuntimeError("fixture-ambiguous-commit-secret")
            return revision


class OwnerSessionManagerTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.exchange = Mock(side_effect=candidate)
        self.query = Mock(side_effect=lambda sauth, uid: sessions.QueryResult(
            OWNER, True, {"uid": str(uid), "identity": {"uid": str(uid)}}))
        self.manager = self.make_manager()
        self.network = patch.object(history.urlrequest, "build_opener", side_effect=AssertionError("no network"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def make_manager(self, **kwargs):
        values = dict(query=self.query, exchange=self.exchange, clock_ms=lambda: 1700000000000,
                      random_samples=lambda: [0.1] * 8)
        values.update(kwargs)
        return sessions.OwnerSessionManager(self.store, **values)

    def boot(self):
        result = self.manager.bootstrap(bundle())
        self.assertEqual(result.outcome, "session_bootstrapped")
        self.assertFalse(result.owner_authenticated)
        return result

    def record(self):
        return self.store.value[1]

    def test_bootstrap_only_required_context_no_prior_access_or_serial(self):
        original = bundle()
        before = copy.deepcopy(original)
        result = self.manager.bootstrap(original)
        self.assertEqual(result.outcome, "session_bootstrapped")
        self.assertEqual(original, before)
        self.assertEqual(set(self.record()), sessions.RECORD_FIELDS)
        self.assertEqual(set(self.record()["saved_account"]), history.ACCOUNT_FIELDS)
        serialized = json.dumps(self.record())
        for discarded in ("fixture-prior-access", "fixture-prior-serial", "fixture-prior-device", "sessionid"):
            self.assertNotIn(discarded, serialized)
        self.assertEqual(self.record()["wrapper_udid"], "fixture-wrapper")
        self.assertEqual(self.record()["mpay_context"]["ci"], "fixture-mpay")
        self.assertEqual(self.record()["mpay_context"]["device_id"], "fixture-http-device")
        self.assertEqual(self.record()["saved_account"]["deviceId"], "fixture-saved-device")
        self.assertIsNone(self.record()["candidate"])
        self.assertFalse(self.record()["owner_verified"])
        self.exchange.assert_not_called()
        self.query.assert_not_called()
        original["saved_account"]["refreshToken"] = "fixture-changed"
        original["mpay_context"]["ci"] = "fixture-changed"
        self.assertEqual(self.record()["saved_account"]["refreshToken"], "fixture-prior-refresh")

    def test_numeric_source_device_ids_are_normalized_losslessly(self):
        data = bundle()
        data["saved_account"]["deviceId"] = 123456789012345
        data["mpay_context"]["device_id"] = 223456789012345
        self.assertEqual(self.manager.bootstrap(data).outcome, "session_bootstrapped")
        self.assertEqual(self.record()["saved_account"]["deviceId"], "123456789012345")
        self.assertEqual(self.record()["mpay_context"]["device_id"], "223456789012345")

    def test_invalid_bootstrap_binding_context_and_extras_before_store_or_io(self):
        cases = [None, {}, dict(bundle(), selected_uid=TARGET), dict(bundle(), selected_uid=True),
                 dict(bundle(), extra="fixture-secret"), dict(bundle(), wrapper_udid="fixture-different")]
        for section, key, value in (("sauth", "refresh_token", "fixture-secret"),
                                    ("saved_account", "userId", TARGET),
                                    ("saved_account", "method", "epic"),
                                    ("saved_account", "userId", 1 << 53),
                                    ("saved_account", "displayName", "fixture-name"),
                                    ("mpay_context", "lang", "en-US"),
                                    ("mpay_context", "ci", "fixture&new=value")):
            data = bundle()
            data[section][key] = value
            cases.append(data)
        for data in cases:
            with self.subTest(case=len(str(type(data)))):
                self.assertEqual(self.manager.bootstrap(data).outcome, "session_context_invalid")
        self.assertEqual(self.store.load_count, 0)
        self.assertEqual(self.store.calls, [])
        self.exchange.assert_not_called()
        self.query.assert_not_called()

    def test_bootstrap_never_overwrites_existing_ready_or_uncertain_record(self):
        self.boot()
        before = copy.deepcopy(self.store.value)
        self.assertEqual(self.manager.bootstrap(bundle()).outcome, "session_already_configured")
        self.assertEqual(self.store.value, before)
        self.record()["state"] = "rotation_unknown"
        self.assertEqual(self.manager.bootstrap(bundle()).outcome, "session_already_configured")
        self.assertEqual(self.record()["state"], "rotation_unknown")

    def test_success_order_candidate_saved_before_sauth_and_query(self):
        self.boot()
        events = []

        def exchange(item):
            events.append("sdk")
            self.assertEqual(self.record()["state"], "refresh_in_flight")
            return candidate(item)

        def samples():
            events.append("samples")
            self.assertEqual(self.record()["state"], "candidate_verifying")
            self.assertEqual(self.record()["saved_account"]["refreshToken"], "fixture-refresh-1")
            self.assertEqual(self.record()["candidate"], {"access_token": "fixture-access-1"})
            return [0.1] * 8

        def clock():
            events.append("clock")
            return 1700000000000

        def query(sauth, uid):
            events.append("query")
            self.assertEqual(uid, TARGET)
            self.assertEqual(sauth["sessionid"], "fixture-access-1")
            self.assertNotIn("refresh_token", sauth)
            self.assertEqual(sauth["deviceid"], "fixture-saved-device")
            self.assertEqual(sauth["udid"], "fixture-wrapper")
            return sessions.QueryResult(OWNER, True, {"uid": str(uid)})

        manager = self.make_manager(exchange=exchange, random_samples=samples, clock_ms=clock, query=query)
        result = manager.lookup(TARGET)
        self.assertEqual(events, ["sdk", "samples", "clock", "query"])
        self.assertEqual([record["state"] for _, record in self.store.calls],
                         ["ready", "refresh_in_flight", "candidate_pending", "candidate_verifying", "ready"])
        self.assertEqual(result.outcome, "session_lookup_complete")
        self.assertTrue(result.owner_authenticated)
        self.assertEqual(result.profile, {"uid": str(TARGET)})
        self.assertTrue(self.record()["owner_verified"])
        self.assertIsNone(self.record()["candidate"])
        self.assertEqual(self.record()["saved_account"]["refreshToken"], "fixture-refresh-1")

    def test_every_ready_lookup_refreshes_using_only_latest_returned_token_no_ttl(self):
        self.boot()
        seen = []

        def exchange(item):
            seen.append(item.refresh_token)
            return candidate(item, len(seen))

        manager = self.make_manager(exchange=exchange)
        self.assertTrue(manager.lookup(TARGET).owner_authenticated)
        self.assertTrue(manager.lookup(OWNER).owner_authenticated)
        self.assertEqual(seen, ["fixture-prior-refresh", "fixture-refresh-1"])
        self.assertEqual([call.args[0]["sessionid"] for call in self.query.call_args_list],
                         ["fixture-access-1", "fixture-access-2"])
        status = manager.status()
        self.assertTrue(status["owner_authenticated"])
        self.assertFalse(status["sdk_expiry_known"])
        self.assertFalse(status["automatic_renewal_verified"])

    def test_profile_unavailable_does_not_prevent_owner_promotion(self):
        self.boot()
        self.query.side_effect = lambda sauth, uid: sessions.QueryResult(OWNER, True, None)
        result = self.manager.lookup(TARGET)
        self.assertEqual(result.outcome, "session_lookup_complete")
        self.assertTrue(result.owner_authenticated)
        self.assertIsNone(result.profile)
        self.assertEqual(self.record()["state"], "ready")
        self.assertTrue(self.record()["owner_verified"])

    def test_invalid_uid_before_store_reads_or_network(self):
        for uid in (None, True, False, 0, -1, 1.0, str(TARGET), 1 << 63):
            self.assertEqual(self.manager.lookup(uid).outcome, "session_uid_invalid")
        self.assertEqual(self.store.load_count, 0)
        self.exchange.assert_not_called()

    def test_unconfigured_lookup_and_status(self):
        self.assertEqual(self.manager.lookup(TARGET).outcome, "session_unconfigured")
        status = self.manager.status()
        self.assertEqual(status["state"], "unconfigured")
        self.assertFalse(status["configured"])
        self.exchange.assert_not_called()

    def test_exact_typed_owner_proof_required_and_new_candidate_is_retained(self):
        cases = [None, {}, {"owner_uid": OWNER, "owner_authenticated": True},
                 sessions.QueryResult(TARGET, True, {"uid": str(TARGET)}),
                 sessions.QueryResult(OWNER, 1, {}), sessions.QueryResult(True, True, {}),
                 sessions.QueryResult(OWNER, False, {"fixture-secret": "fixture-secret"})]
        for reply in cases:
            with self.subTest(reply_type=type(reply).__name__):
                self.store.value = None
                self.boot()
                self.query.side_effect = None
                self.query.return_value = reply
                result = self.manager.lookup(TARGET)
                self.assertEqual(result.outcome, "session_owner_unverified")
                self.assertFalse(result.owner_authenticated)
                self.assertIsNone(result.profile)
                self.assertEqual(self.record()["state"], "candidate_recovery_required")
                self.assertEqual(self.record()["saved_account"]["refreshToken"], "fixture-refresh-1")
                self.assertEqual(self.record()["candidate"]["access_token"], "fixture-access-1")

    def test_query_exception_retains_candidate_and_does_not_fallback_or_repeat_sdk(self):
        self.boot()
        self.query.side_effect = RuntimeError("fixture-query-secret")
        result = self.manager.lookup(TARGET)
        self.assertEqual(result.outcome, "session_query_failed")
        self.assertEqual(self.record()["state"], "candidate_recovery_required")
        self.assertEqual(self.exchange.call_count, 1)
        self.query.side_effect = lambda sauth, uid: sessions.QueryResult(OWNER, True, {"uid": str(uid)})
        self.assertEqual(self.manager.lookup(TARGET).outcome, "session_recovery_required")
        self.assertTrue(self.manager.recover_pending(exclusive_ownership=True).owner_authenticated)
        self.assertEqual(self.exchange.call_count, 1)
        self.assertEqual(self.query.call_args.args[0]["sessionid"], "fixture-access-1")

    def test_sauth_provider_failure_retains_new_tokens_before_query(self):
        self.boot()
        manager = self.make_manager(random_samples=Mock(side_effect=RuntimeError("fixture-rng-secret")))
        result = manager.lookup(TARGET)
        self.assertEqual(result.outcome, "session_sauth_failed")
        self.assertEqual(self.record()["state"], "candidate_pending")
        self.assertEqual(self.record()["saved_account"]["refreshToken"], "fixture-refresh-1")
        self.query.assert_not_called()
        self.assertTrue(self.make_manager().lookup(TARGET).owner_authenticated)
        self.assertEqual(self.exchange.call_count, 1)

    def test_refresh_claim_write_failure_prevents_remote_exchange(self):
        self.boot()
        self.store.fail_state = "refresh_in_flight"
        self.assertEqual(self.manager.lookup(TARGET).outcome, "session_store_write_failed")
        self.assertEqual(self.record()["state"], "ready")
        self.exchange.assert_not_called()
        self.query.assert_not_called()

    def test_candidate_write_failure_leaves_unknown_old_token_never_retried(self):
        self.boot()
        self.store.fail_state = "candidate_pending"
        self.assertEqual(self.manager.lookup(TARGET).outcome, "session_store_write_failed")
        self.assertEqual(self.record()["state"], "refresh_in_flight")
        self.assertEqual(self.exchange.call_count, 1)
        self.query.assert_not_called()
        self.assertEqual(self.make_manager().lookup(TARGET).outcome, "session_rotation_unknown")
        self.assertEqual(self.manager.recover_pending(exclusive_ownership=True).outcome, "session_rotation_unknown")
        self.assertEqual(self.exchange.call_count, 1)

    def test_candidate_ambiguous_commit_can_resume_saved_candidate_without_exchange(self):
        self.boot()
        self.store.commit_then_fail_state = "candidate_pending"
        self.assertEqual(self.manager.lookup(TARGET).outcome, "session_store_write_failed")
        self.assertEqual(self.record()["state"], "candidate_pending")
        self.query.assert_not_called()
        self.assertTrue(self.make_manager().lookup(TARGET).owner_authenticated)
        self.assertEqual(self.exchange.call_count, 1)
        self.assertEqual(self.query.call_args.args[0]["sessionid"], "fixture-access-1")

    def test_verification_claim_failure_preserves_restartable_pending_tokens(self):
        self.boot()
        self.store.fail_state = "candidate_verifying"
        self.assertEqual(self.manager.lookup(TARGET).outcome, "session_store_write_failed")
        self.assertEqual(self.record()["state"], "candidate_pending")
        self.query.assert_not_called()
        self.assertTrue(self.make_manager().recover_pending(exclusive_ownership=True).owner_authenticated)
        self.assertEqual(self.exchange.call_count, 1)
        self.assertEqual(self.query.call_args.args[1], OWNER)

    def test_promotion_failure_preserves_candidate_and_suppresses_profile(self):
        self.boot()
        self.store.fail_state = "ready"
        result = self.manager.lookup(TARGET)
        self.assertEqual(result.outcome, "session_store_write_failed")
        self.assertTrue(result.owner_authenticated)
        self.assertIsNone(result.profile)
        self.assertEqual(self.record()["state"], "candidate_verifying")
        self.assertEqual(self.record()["saved_account"]["refreshToken"], "fixture-refresh-1")
        self.assertEqual(self.make_manager().lookup(TARGET).outcome, "session_verification_in_flight")
        recovered = self.make_manager().recover_pending(exclusive_ownership=True)
        self.assertTrue(recovered.owner_authenticated)
        self.assertEqual(self.exchange.call_count, 1)

    def test_known_pre_request_sdk_failure_stops_without_retry_and_retains_old_token(self):
        self.boot()
        self.exchange.side_effect = history.HistoryLoginError("history_context_invalid", request_attempted=False)
        self.assertEqual(self.manager.lookup(TARGET).outcome, "session_sdk_not_attempted")
        self.assertEqual(self.record()["state"], "ready")
        self.assertEqual(self.record()["saved_account"]["refreshToken"], "fixture-prior-refresh")
        self.assertEqual(self.exchange.call_count, 1)
        self.query.assert_not_called()

    def test_attempted_or_unknown_sdk_failure_blocks_rotation_without_invalid_uid_claim(self):
        for attempted in (True, None):
            self.store.value = None
            self.boot()
            self.exchange.reset_mock()
            self.exchange.side_effect = history.HistoryLoginError(
                "history_service_rejected", request_attempted=attempted, http_status=429, application_code=123)
            result = self.manager.lookup(TARGET)
            self.assertEqual(result.outcome, "session_rotation_unknown")
            self.assertEqual(self.record()["state"], "rotation_unknown")
            self.assertTrue(self.manager.status()["rotation_uncertain"])
            self.assertEqual(self.manager.lookup(TARGET).outcome, "session_rotation_unknown")
            self.assertEqual(self.exchange.call_count, 1)
        self.query.assert_not_called()

    def test_generic_exchange_exception_is_redacted_and_not_retried(self):
        self.boot()
        self.exchange.side_effect = RuntimeError("fixture-http-secret")
        result = self.manager.lookup(TARGET)
        self.assertEqual(result.outcome, "session_rotation_unknown")
        self.assertNotIn("fixture", repr(result))
        self.assertNotIn("fixture", json.dumps(self.manager.status()))
        self.assertEqual(self.exchange.call_count, 1)

    def test_crash_during_exchange_requires_rebootstrap_not_old_token_retry(self):
        self.boot()
        self.exchange.side_effect = SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.manager.lookup(TARGET)
        self.assertEqual(self.record()["state"], "refresh_in_flight")
        restarted = self.make_manager()
        self.assertEqual(restarted.lookup(TARGET).outcome, "session_rotation_unknown")
        self.assertEqual(restarted.recover_pending(exclusive_ownership=True).outcome, "session_rotation_unknown")
        self.assertEqual(self.exchange.call_count, 1)

    def test_crash_during_verification_recovers_only_with_exclusive_attestation(self):
        self.boot()
        self.query.side_effect = SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.manager.lookup(TARGET)
        self.assertEqual(self.record()["state"], "candidate_verifying")
        restarted = self.make_manager()
        self.assertEqual(restarted.lookup(TARGET).outcome, "session_verification_in_flight")
        reads = self.store.load_count
        for value in (False, None, 1, "yes"):
            self.assertEqual(restarted.recover_pending(exclusive_ownership=value).outcome,
                             "session_exclusive_ownership_required")
        self.assertEqual(self.store.load_count, reads)
        self.query.side_effect = lambda sauth, uid: sessions.QueryResult(OWNER, True, {"uid": str(uid)})
        result = restarted.recover_pending(exclusive_ownership=True)
        self.assertTrue(result.owner_authenticated)
        self.assertEqual(self.exchange.call_count, 1)
        self.assertEqual(self.query.call_args.args[1], OWNER)
        self.assertEqual(self.query.call_args.args[0]["sessionid"], "fixture-access-1")
        self.assertFalse(restarted.status()["automatic_renewal_verified"])

    def test_pending_candidate_after_restart_is_verified_before_another_exchange(self):
        self.boot()
        self.store.fail_state = "candidate_verifying"
        self.manager.lookup(TARGET)
        durable_copy = copy.deepcopy(self.store.value)
        replacement_store = MemoryStore()
        replacement_store.value = durable_copy
        query = Mock(return_value=sessions.QueryResult(OWNER, True, None))
        forbidden_exchange = Mock(side_effect=AssertionError("must not rotate pending"))
        restarted = sessions.OwnerSessionManager(replacement_store, query=query, exchange=forbidden_exchange,
                                                 clock_ms=lambda: 1700000001000,
                                                 random_samples=lambda: [0.2] * 8)
        self.assertTrue(restarted.lookup(TARGET).owner_authenticated)
        forbidden_exchange.assert_not_called()
        self.assertEqual(query.call_args.args[0]["sessionid"], "fixture-access-1")

    def test_mutating_callback_cannot_replace_persisted_token_or_context(self):
        self.boot()

        def query(sauth, uid):
            sauth.update(sessionid="fixture-injected-access", sdkuid="999", udid="fixture-injected-wrapper")
            return sessions.QueryResult(OWNER, True, {"uid": str(uid), "refreshToken": "fixture-injected-refresh"})

        result = self.make_manager(query=query).lookup(TARGET)
        self.assertTrue(result.owner_authenticated)
        self.assertEqual(self.record()["saved_account"]["refreshToken"], "fixture-refresh-1")
        self.assertEqual(self.record()["wrapper_udid"], "fixture-wrapper")
        # Profiles stay explicitly private/untrusted; status/repr never includes
        # them. The separate HTTP projector is responsible for dropping extras.
        self.assertEqual(result.profile["refreshToken"], "fixture-injected-refresh")
        self.assertNotIn("fixture", repr(result))
        self.assertNotIn("fixture", json.dumps(self.manager.status()))

    def test_malformed_or_wrong_bound_returned_candidate_does_not_authenticate(self):
        source = history.build_history_login_inputs(bundle()["sauth"], bundle()["saved_account"],
                                                    bundle()["mpay_context"])
        good = candidate(source)
        for value in (None, {}, replace(good, sdkuid="999"), replace(good, expected_owner_uid=TARGET),
                      replace(good, saved_device_id="fixture-other-device"), replace(good, access_token=""),
                      replace(good, refresh_token="x" * (history.MAX_TOKEN_BYTES + 1))):
            self.store.value = None
            self.boot()
            self.exchange.side_effect = None
            self.exchange.return_value = value
            self.assertEqual(self.manager.lookup(TARGET).outcome, "session_rotation_unknown")
            self.assertIsNone(self.record()["candidate"])
        self.query.assert_not_called()

    def test_returned_unrepresentable_refresh_is_retained_not_replaced_with_old_token(self):
        self.boot()
        self.exchange.side_effect = lambda item: replace(candidate(item), refresh_token="fixture&unsupported=value")
        self.assertTrue(self.manager.lookup(TARGET).owner_authenticated)
        self.assertEqual(self.record()["saved_account"]["refreshToken"], "fixture&unsupported=value")
        self.assertEqual(self.manager.lookup(TARGET).outcome, "session_context_invalid")
        self.assertEqual(self.exchange.call_count, 1)

    def test_two_managers_cannot_rotate_same_revision(self):
        self.boot()
        first_entered, release = threading.Event(), threading.Event()
        results = []

        def exchange(item):
            first_entered.set()
            if not release.wait(5):
                raise AssertionError("test worker did not release")
            return candidate(item)

        first = self.make_manager(exchange=exchange)
        thread = threading.Thread(target=lambda: results.append(first.lookup(TARGET)))
        thread.start()
        try:
            self.assertTrue(first_entered.wait(5))
            other = self.make_manager().lookup(OWNER)
            self.assertEqual(other.outcome, "session_rotation_unknown")
            self.exchange.assert_not_called()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results[0].owner_authenticated)

    def test_stale_ready_cas_loser_stops_without_network(self):
        self.boot()
        stale = self.store.load(OWNER)
        self.record()["state"] = "refresh_in_flight"
        self.store.value = self.store.value[0] + 1, self.record()
        with patch.object(self.store, "load", return_value=stale):
            result = self.manager.lookup(TARGET)
        self.assertEqual(result.outcome, "session_store_write_failed")
        self.exchange.assert_not_called()
        self.query.assert_not_called()

    def test_two_workers_cannot_verify_same_pending_revision(self):
        self.boot()
        self.store.fail_state = "candidate_verifying"
        self.manager.lookup(TARGET)
        entered, release = threading.Event(), threading.Event()
        results = []

        def query(sauth, uid):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test worker did not release")
            return sessions.QueryResult(OWNER, True, None)

        thread = threading.Thread(target=lambda: results.append(self.make_manager(query=query).lookup(TARGET)))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            result = self.make_manager().lookup(OWNER)
            self.assertEqual(result.outcome, "session_verification_in_flight")
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results[0].owner_authenticated)
        self.assertEqual(self.exchange.call_count, 1)

    def test_store_failures_and_malformed_revisions_are_redacted(self):
        with patch.object(self.store, "load", side_effect=RuntimeError("fixture-load-secret")):
            result = self.manager.lookup(TARGET)
            status = self.manager.status()
        self.assertEqual(result.outcome, "session_store_unavailable")
        self.assertEqual(status["outcome"], "session_store_unavailable")
        self.assertNotIn("fixture", repr(result) + json.dumps(status))
        for value in ([1, {}], (True, {}), (-1, {}), ((1 << 63), {}), (1, {"fixture-secret": "fixture-secret"})):
            with patch.object(self.store, "load", return_value=value):
                self.assertEqual(self.manager.lookup(TARGET).outcome, "session_store_record_invalid")
        self.exchange.assert_not_called()

    def test_invalid_stored_schema_owner_candidate_context_before_remote_io(self):
        self.boot()
        original = copy.deepcopy(self.store.value)
        changes = [("schema_version", True), ("schema_version", 2), ("owner_uid", TARGET),
                   ("state", "fixture-secret"), ("owner_verified", 1), ("sdkuid", "999"),
                   ("candidate", {"access_token": "fixture-access"}),
                   ("wrapper_udid", ""), ("extra", "fixture-secret")]
        for key, value in changes:
            self.store.value = copy.deepcopy(original)
            self.record()[key] = value
            self.assertEqual(self.manager.lookup(TARGET).outcome, "session_store_record_invalid")
        self.exchange.assert_not_called()
        self.query.assert_not_called()

    def test_invalid_cas_return_value_stops_without_remote_io(self):
        self.boot()
        for revision in (None, True, -1, 1, 1 << 63):
            with patch.object(self.store, "compare_and_swap", return_value=revision) as update:
                self.assertEqual(self.manager.lookup(TARGET).outcome, "session_store_write_failed")
                update.assert_called_once()
        self.exchange.assert_not_called()

    def test_repr_hidden_results_manager_and_internal_record(self):
        self.boot()
        result = self.manager.lookup(TARGET)
        loaded = self.manager._load()
        query_result = sessions.QueryResult(OWNER, True, {"fixture-secret": "fixture-secret"})
        for value in (result, loaded, query_result, self.manager):
            self.assertNotIn("fixture", repr(value))
            self.assertNotIn(str(TARGET), repr(value))
        self.assertEqual(set(self.manager.status()), {
            "outcome", "owner_uid", "state", "configured", "revision", "owner_authenticated",
            "candidate_saved", "rotation_uncertain", "requires_exclusive_recovery",
            "sdk_expiry_known", "automatic_renewal_verified"})

    def test_no_pending_recovery_never_rotates_ready_session(self):
        self.assertEqual(self.manager.recover_pending(exclusive_ownership=True).outcome, "session_unconfigured")
        self.boot()
        self.assertEqual(self.manager.recover_pending(exclusive_ownership=True).outcome,
                         "session_no_pending_candidate")
        self.exchange.assert_not_called()
        self.query.assert_not_called()

    def test_defaults_use_only_normal_history_sdk_not_desktop_or_browser_loaders(self):
        self.boot()
        with patch.object(history, "login_history_once", side_effect=candidate) as normal_exchange:
            manager = sessions.OwnerSessionManager(self.store, query=self.query)
            self.assertTrue(manager.lookup(TARGET).owner_authenticated)
        normal_exchange.assert_called_once()
        self.exchange.assert_not_called()

    def test_invalid_provider_constructor_is_fixed_error(self):
        for kwargs in ({"query": None}, {"exchange": 1}, {"clock_ms": "fixture-secret"}, {"random_samples": 1}):
            with self.assertRaisesRegex(sessions.SessionManagerError, "^session_context_invalid$"):
                self.make_manager(**kwargs)

    def test_reviewed_253_allows_next_lookup_to_exchange_latest_refresh_without_same_call_retry(self):
        self.boot()
        seen = []

        def exchange(item):
            seen.append(item.refresh_token)
            return candidate(item, len(seen))

        self.query.side_effect = [sessions.QueryResult(OWNER, False, auth_rejection_code=253),
                                  sessions.QueryResult(OWNER, True, {"uid": str(TARGET)})]
        manager = self.make_manager(exchange=exchange)
        first = manager.lookup(TARGET)
        self.assertEqual(first.outcome, "session_access_rejected")
        self.assertFalse(first.owner_authenticated)
        self.assertIsNone(first.profile)
        self.assertEqual(seen, ["fixture-prior-refresh"])
        self.assertEqual(self.query.call_count, 1)
        self.assertEqual(self.record()["state"], "ready")
        self.assertFalse(self.record()["owner_verified"])
        self.assertIsNone(self.record()["candidate"])
        self.assertEqual(self.record()["saved_account"]["refreshToken"], "fixture-refresh-1")
        self.assertTrue(manager.lookup(TARGET).owner_authenticated)
        self.assertEqual(seen, ["fixture-prior-refresh", "fixture-refresh-1"])
        self.assertEqual(self.query.call_args.args[0]["sessionid"], "fixture-access-2")
        self.assertFalse(manager.status()["automatic_renewal_verified"])

    def test_non_253_or_unbound_rejection_never_permits_another_sdk_exchange(self):
        cases = [sessions.QueryResult(OWNER, False, auth_rejection_code=code)
                 for code in (None, True, False, 253.0, "253", 1911, 0, -1, 1 << 64)]
        cases += [sessions.QueryResult(TARGET, False, auth_rejection_code=253),
                  sessions.QueryResult(True, False, auth_rejection_code=253),
                  sessions.QueryResult(OWNER, 0, auth_rejection_code=253),
                  sessions.QueryResult(OWNER, True, auth_rejection_code=253)]
        for reply in cases:
            self.store.value = None
            self.boot()
            self.exchange.reset_mock()
            self.query.reset_mock()
            self.query.side_effect = None
            self.query.return_value = reply
            self.assertEqual(self.manager.lookup(TARGET).outcome, "session_owner_unverified")
            self.assertEqual(self.record()["state"], "candidate_recovery_required")
            self.assertEqual(self.manager.lookup(TARGET).outcome, "session_recovery_required")
            self.assertEqual(self.exchange.call_count, 1)
            self.assertEqual(self.query.call_count, 1)
            status = self.manager.status()
            self.assertTrue(status["candidate_saved"])
            self.assertTrue(status["requires_exclusive_recovery"])
            self.assertFalse(status["owner_authenticated"])
            self.assertEqual(status["outcome"], "session_recovery_required")

    def test_sdk_service_code_253_does_not_equal_correlated_game_auth_253(self):
        self.boot()
        self.exchange.side_effect = history.HistoryLoginError(
            "history_service_rejected", request_attempted=True, http_status=200, application_code=253)
        self.assertEqual(self.manager.lookup(TARGET).outcome, "session_rotation_unknown")
        self.assertEqual(self.manager.lookup(TARGET).outcome, "session_rotation_unknown")
        self.assertEqual(self.exchange.call_count, 1)
        self.query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
