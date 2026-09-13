"""Local provider integration with fake SDK/query and memory-only ownership."""

from contextlib import contextmanager
import copy
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch

from career_provider import CareerUnavailable
import local_native_provider as local
import standalone_session_manager as sessions
from standalone_session_store import SessionStoreError
from tests.standalone_session_manager_test import MemoryStore, OWNER, TARGET, bundle, candidate
from tests.standalone_web_contract_test import NOW, bundle as profile_bundle


def account(uid=OWNER, platform="pc"):
    return {"rivals_uid": str(uid), "rivals_platform": platform, "in_game_name": ""}


def profile(uid):
    result = profile_bundle()
    result["uid"] = result["identity"]["uid"] = str(uid)
    result["identity"]["is_owner"] = uid == OWNER
    result["career"]["data"]["uid"] = str(uid)
    result["matches"]["data"]["uid"] = str(uid)
    result["matches"]["data"]["matches"][0]["uid"] = str(uid)
    if uid != OWNER:
        result["current_rank"] = result["current_rank_source"] = None
    return result


class OwnedMemoryStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.ownership = threading.Lock()
        self.entered = self.exited = 0
        self.held = False

    @contextmanager
    def exclusive_owner(self):
        if not self.ownership.acquire(blocking=False):
            raise SessionStoreError("session_store_in_use")
        self.entered += 1
        self.held = True
        try:
            yield self
        finally:
            self.held = False
            self.exited += 1
            self.ownership.release()


class LocalNativeProviderTest(unittest.TestCase):
    def setUp(self):
        self.network = patch("socket.socket", side_effect=AssertionError("no networking"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.now = NOW.timestamp()
        self.store = OwnedMemoryStore()
        self.exchange = Mock(side_effect=candidate)
        self.query = Mock(side_effect=lambda sauth, uid: sessions.QueryResult(OWNER, True, profile(uid)))
        self.provider = self.make_provider(self.store)

    def make_provider(self, store, **kwargs):
        values = {"query": self.query, "exchange": self.exchange, "clock": lambda: self.now}
        values.update(kwargs)
        value = local.LocalNativeCareerProvider(store, **values)
        self.addCleanup(value.close)
        return value

    def boot(self):
        result = self.provider.bootstrap(bundle())
        self.assertEqual(result, {"outcome": "session_bootstrapped", "owner_authenticated": False})
        return result

    def error(self, call, code, status=None):
        with self.assertRaises(CareerUnavailable) as failure:
            call()
        self.assertEqual(failure.exception.code, code)
        self.assertNotIn("fixture-private", str(failure.exception))
        if status is not None:
            self.assertEqual(failure.exception.status, status)

    def test_constructor_owns_store_without_bootstrap_or_credential_lookup(self):
        self.assertTrue(self.store.held)
        self.assertEqual(self.store.entered, 1)
        self.assertEqual(self.store.load_count, 0)
        self.exchange.assert_not_called()
        self.query.assert_not_called()
        self.assertEqual(repr(self.provider), "<LocalNativeCareerProvider>")
        status = self.provider.status()
        self.assertEqual(status["provider"], "native")
        self.assertEqual(status["uid"], str(OWNER))
        self.assertFalse(status["configured"])
        self.assertFalse(status["connected"])
        self.assertEqual(status["state"], "unconfigured")

    def test_explicit_bootstrap_only_no_overwrite_or_implicit_exchange(self):
        self.error(lambda: self.provider.refresh_account(account()), "career_not_configured", 409)
        self.boot()
        original = copy.deepcopy(self.store.value)
        self.error(lambda: self.provider.bootstrap(bundle()), "career_already_configured", 409)
        self.assertEqual(self.store.value, original)
        self.error(lambda: self.provider.recover(), "career_no_pending_session", 409)
        self.assertEqual(self.store.value, original)
        self.exchange.assert_not_called()
        self.query.assert_not_called()
        status = self.provider.status()
        self.assertTrue(status["configured"])
        self.assertFalse(status["connected"])
        self.assertNotIn("fixture", json.dumps(status))

    def test_refresh_projects_exact_five_fields_and_drops_unknown_credentials(self):
        self.boot()
        source = profile(OWNER)
        source["sessionid"] = "fixture-private-access"
        source["identity"]["refresh_token"] = "fixture-private-refresh"
        source["career"]["data"]["statistics"]["password"] = "fixture-private-password"
        self.query.side_effect = lambda sauth, uid: sessions.QueryResult(OWNER, True, source)
        result = self.provider.refresh_account(account())
        self.assertEqual(set(result), {"uid", "platform", "in_game_name", "fetched_at", "native_profile"})
        self.assertEqual(result["uid"], str(OWNER))
        self.assertEqual(result["platform"], "pc")
        self.assertEqual(result["fetched_at"], int(self.now))
        self.assertEqual(result["native_profile"]["career"]["data"]["request_scope"], "unfiltered")
        self.assertEqual(result["native_profile"]["matches"]["data"]["history_completeness"], "first_page_only")
        self.assertNotIn("fixture-private", json.dumps(result))
        self.assertTrue(self.provider.status()["connected"])
        self.query.assert_called_once()
        self.exchange.assert_called_once()
        self.assertEqual(self.query.call_args.args[0]["sessionid"], "fixture-access-1")
        self.assertEqual(self.query.call_args.args[1], OWNER)

    def test_unknown_platform_and_exact_integer_uid_normalize_to_pc(self):
        self.boot()
        for platform in (None, "", "unknown", "pc"):
            value = {"rivals_uid": OWNER, "rivals_platform": platform}
            self.assertTrue(self.provider.can_refresh_account(value))
            self.assertEqual(self.provider.refresh_account(value)["platform"], "pc")
        self.exchange.assert_called_once()

    def test_invalid_uid_or_unsupported_platform_never_loads_or_queries(self):
        invalid = (True, False, 1.0, 0, 99999, 100000000000, 1 << 20000,
                   "0123456", " 1370598422", "1370598422 ", "1e6", "１２３４５６", "", None, [])
        before = self.store.load_count
        for uid in invalid:
            value = {"rivals_uid": uid, "rivals_platform": "pc"}
            self.assertFalse(self.provider.can_refresh_account(value))
            self.assertIsNone(self.provider.cached_account(value))
            code = "missing_identity" if uid is None or type(uid) is str and uid == "" else "invalid_rivals_uid"
            self.error(lambda: self.provider.refresh_account(value), code, 400)
        for platform in ("playstation", "xbox", "PC", [], True, 1):
            self.error(lambda: self.provider.refresh_account(account(platform=platform)), "career_platform_unavailable", 400)
        self.error(lambda: self.provider.refresh_account(None), "invalid_rivals_uid")
        self.assertEqual(self.store.load_count, before)
        self.exchange.assert_not_called()
        self.query.assert_not_called()

    def test_ign_only_has_actionable_uid_required_error_without_search(self):
        self.error(lambda: self.provider.refresh_account({"in_game_name": "Synthetic player"}), "career_uid_required", 400)
        self.assertEqual(self.store.load_count, 0)
        self.exchange.assert_not_called()
        self.query.assert_not_called()

    def test_cache_reuse_window_differs_from_drawer_retention_and_copies(self):
        self.boot()
        first = self.provider.refresh_account(account())
        first["native_profile"]["identity"]["in_game_name"] = "mutated"
        self.now += 29
        second = self.provider.refresh_account(account())
        self.assertEqual(second["fetched_at"], int(NOW.timestamp()))
        self.assertNotEqual(second["native_profile"]["identity"]["in_game_name"], "mutated")
        self.now += 100
        cached = self.provider.cached_account(account())
        self.assertEqual(cached["fetched_at"], int(NOW.timestamp()))
        cached["native_profile"]["matches"]["data"]["matches"].clear()
        self.assertEqual(len(self.provider.cached_account(account())["native_profile"]["matches"]["data"]["matches"]), 1)
        self.assertIsNone(self.provider.cached_account(account(TARGET)))
        self.assertIsNone(self.provider.cached_account(account(platform="xbox")))
        self.exchange.assert_called_once()
        third = self.provider.refresh_account(account())
        self.assertEqual(third["fetched_at"], int(self.now))
        self.assertEqual(self.exchange.call_count, 2)
        self.assertEqual(self.exchange.call_args.args[0].refresh_token, "fixture-refresh-1")

    def test_cache_clock_backwards_does_not_claim_reused_freshness(self):
        self.boot()
        self.provider.refresh_account(account())
        self.now -= 1
        self.provider.refresh_account(account())
        self.assertEqual(self.exchange.call_count, 2)

    def test_cache_lru_is_bounded_and_keeps_only_successful_bound_snapshots(self):
        self.boot()
        with patch.object(local, "MAX_CACHE", 3):
            for uid in (100001, 100002, 100003):
                self.provider.refresh_account(account(uid))
            self.assertIsNotNone(self.provider.cached_account(account(100001)))
            self.provider.refresh_account(account(100004))
        self.assertIsNone(self.provider.cached_account(account(100002)))
        self.assertEqual(set(self.provider._cache), {100001, 100003, 100004})
        self.assertEqual(len(self.provider._cache), 3)

    def test_partial_visibility_retains_independent_matches_and_replaces_old_cache(self):
        self.boot()
        self.provider.refresh_account(account(TARGET))
        self.now += 31
        source = profile(TARGET)
        source["identity"]["visibility"]["overview"]["state"] = "restricted"
        source["career"] = {"raw": "fixture-private-hidden-career"}
        self.query.side_effect = lambda sauth, uid: sessions.QueryResult(OWNER, True, source)
        result = self.provider.refresh_account(account(TARGET))["native_profile"]
        self.assertEqual(result["career"], {"status": "restricted", "data": None})
        self.assertTrue(result["matches_verified"])
        self.assertIsNone(result["current_rank"])
        self.assertEqual(self.provider.cached_account(account(TARGET))["native_profile"], result)
        self.assertNotIn("fixture-private", json.dumps(result))

    def test_unknown_history_keeps_career_without_exposing_old_matches(self):
        self.boot()
        source = profile(TARGET)
        source["identity"]["visibility"]["history"]["state"] = "unknown"
        self.query.side_effect = lambda sauth, uid: sessions.QueryResult(OWNER, True, source)
        result = self.provider.refresh_account(account(TARGET))["native_profile"]
        self.assertTrue(result["career_verified"])
        self.assertEqual(result["matches"], {"status": "unknown", "data": None})

    def test_identity_mismatch_and_invalid_shape_clear_only_failed_target_cache(self):
        self.boot()
        self.provider.refresh_account(account())
        self.provider.refresh_account(account(TARGET))
        self.now += 31
        self.query.side_effect = lambda sauth, uid: sessions.QueryResult(OWNER, True, profile(OWNER))
        self.error(lambda: self.provider.refresh_account(account(TARGET)), "career_identity_unavailable", 502)
        self.assertIsNone(self.provider.cached_account(account(TARGET)))
        self.assertIsNotNone(self.provider.cached_account(account()))
        malformed = profile(TARGET)
        malformed["matches"]["data"]["matches"][0]["uid"] = str(OWNER)
        self.query.side_effect = lambda sauth, uid: sessions.QueryResult(OWNER, True, malformed)
        self.error(lambda: self.provider.refresh_account(account(TARGET)), "career_identity_unavailable")
        self.assertIsNone(self.provider.cached_account(account(TARGET)))

    def test_query_failure_and_explicit_recovery_do_not_retry_old_sdk_token(self):
        self.boot()
        self.query.side_effect = RuntimeError("fixture-private-query-failure")
        self.error(lambda: self.provider.refresh_account(account()), "career_request_failed")
        self.assertEqual(self.provider.status()["state"], "candidate_recovery_required")
        self.error(lambda: self.provider.refresh_account(account()), "career_session_recovery_required")
        self.exchange.assert_called_once()
        self.query.side_effect = lambda sauth, uid: sessions.QueryResult(OWNER, True, profile(uid))
        self.assertEqual(self.provider.recover(), {"outcome": "session_lookup_complete", "owner_authenticated": True})
        self.assertEqual(self.query.call_args.args[0]["sessionid"], "fixture-access-1")
        self.assertEqual(self.query.call_args.args[1], OWNER)
        self.exchange.assert_called_once()
        self.assertTrue(self.provider.status()["connected"])
        self.assertIsNone(self.provider.cached_account(account()))

    def test_failed_or_untyped_owner_proof_does_not_publish_profile(self):
        self.boot()
        self.query.side_effect = lambda sauth, uid: sessions.QueryResult(TARGET, True, profile(uid))
        self.error(lambda: self.provider.refresh_account(account()), "career_session_unverified")
        self.assertIsNone(self.provider.cached_account(account()))
        with patch.object(self.provider._manager, "lookup", return_value={"profile": profile(OWNER)}):
            self.error(lambda: self.provider.refresh_account(account()), "career_request_failed")

    def test_fixed_status_never_includes_credential_context_or_arbitrary_fields(self):
        self.boot()
        source = {"owner_uid": OWNER, "state": "ready", "configured": True, "owner_authenticated": True,
                  "outcome": "session_ready", "candidate_saved": "fixture-private-boolean",
                  "secret": "fixture-private-token", "revision": "fixture-private-revision"}
        with patch.object(self.provider._manager, "status", return_value=source):
            status = self.provider.status()
        self.assertTrue(status["connected"])
        self.assertFalse(status["candidate_saved"])
        self.assertNotIn("fixture-private", json.dumps(status))
        self.assertNotIn("revision", status)
        with patch.object(self.provider._manager, "status", return_value=dict(source, state=[])):
            self.assertEqual(self.provider.status()["state"], "unavailable")
        with patch.object(self.provider._manager, "status", side_effect=RuntimeError("fixture-private-error")):
            self.assertFalse(self.provider.status()["connected"])

    def blocked_query(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def query(sauth, uid):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test query was not released")
            return sessions.QueryResult(OWNER, True, profile(uid))

        self.query.side_effect = query
        results, errors = [], []

        def refresh():
            try:
                results.append(self.provider.refresh_account(account()))
            except Exception as error:
                errors.append(type(error))

        worker = threading.Thread(target=refresh)
        worker.start()
        self.addCleanup(lambda: worker.join(timeout=6))
        self.assertTrue(entered.wait(2))
        return release, worker, results, errors

    def test_concurrent_refresh_bootstrap_and_recovery_are_rejected_without_duplicate_exchange(self):
        self.boot()
        release, worker, results, errors = self.blocked_query()
        try:
            self.error(lambda: self.provider.refresh_account(account()), "career_busy", 429)
            self.error(lambda: self.provider.refresh_account(account(TARGET)), "career_busy", 429)
            self.error(lambda: self.provider.bootstrap(bundle()), "career_busy", 429)
            self.error(self.provider.recover, "career_busy", 429)
            status = self.provider.status()
            self.assertTrue(status["busy"])
            self.assertFalse(status["connected"])
            self.exchange.assert_called_once()
        finally:
            release.set()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)

    def test_clear_removes_only_safe_cache_and_cannot_be_undone_by_inflight_work(self):
        self.boot()
        self.provider.refresh_account(account(TARGET))
        release, worker, _, errors = self.blocked_query()
        try:
            before = copy.deepcopy(self.store.value)
            self.provider.clear()
            self.assertIsNone(self.provider.cached_account(account(TARGET)))
            self.assertEqual(self.store.value, before)
            self.assertTrue(self.store.held)
        finally:
            release.set()
            worker.join(timeout=3)
        self.assertEqual(errors, [])
        self.assertIsNone(self.provider.cached_account(account()))
        self.assertTrue(self.provider.status()["connected"])

    def test_close_marks_closing_drains_work_then_releases_lock_and_drops_references(self):
        self.boot()
        release, worker, results, errors = self.blocked_query()
        closer = threading.Thread(target=self.provider.close)
        closer.start()
        self.addCleanup(lambda: closer.join(timeout=6))
        try:
            deadline = time.monotonic() + 2
            while not self.provider.status()["closing"] and time.monotonic() < deadline:
                threading.Event().wait(0.005)
            self.assertTrue(self.provider.status()["closing"])
            self.assertTrue(closer.is_alive())
            self.assertTrue(self.store.held)
            self.assertEqual(self.store.exited, 0)
            self.assertFalse(self.provider.can_refresh_account(account()))
            self.error(lambda: self.provider.refresh_account(account()), "career_closed")
            self.assertIsNone(self.provider.cached_account(account()))
        finally:
            release.set()
            worker.join(timeout=3)
            closer.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertFalse(self.store.held)
        self.assertEqual(self.store.exited, 1)
        self.assertIsNone(self.provider._manager)
        self.assertIsNone(self.provider._store)
        self.assertIsNone(self.provider._owner_context)
        self.assertEqual(self.provider._cache, {})
        self.assertEqual(self.store.value[1]["state"], "ready")
        self.assertEqual(self.store.value[1]["saved_account"]["refreshToken"], "fixture-refresh-1")
        self.assertEqual(self.provider.status()["state"], "closed")
        self.provider.close()
        self.assertEqual(self.store.exited, 1)

    def test_invalid_clock_or_unexpected_callback_errors_are_fixed(self):
        self.boot()
        self.provider.refresh_account(account())
        for clock in (lambda: True, lambda: float("inf"), lambda: float("nan"), lambda: 0,
                      Mock(side_effect=RuntimeError("fixture-private-clock"))):
            with patch.object(self.provider, "_clock", clock):
                self.error(lambda: self.provider.refresh_account(account()), "career_request_failed")
            self.assertIsNone(self.provider.cached_account(account()))
        with patch.object(self.provider._manager, "bootstrap", side_effect=RuntimeError("fixture-private-bootstrap")):
            self.error(lambda: self.provider.bootstrap(bundle()), "career_bootstrap_invalid")
        with patch.object(self.provider._manager, "recover_pending", side_effect=RuntimeError("fixture-private-recover")):
            self.error(self.provider.recover, "career_request_failed")

    def test_config_errors_do_not_leave_ownership_and_duplicate_provider_is_rejected(self):
        for values in ({"clock": None}, {"query": 1}, {"exchange": 1}):
            other = OwnedMemoryStore()
            self.error(lambda: self.make_provider(other, **values), "career_config_invalid")
            self.assertFalse(other.held)
        self.error(lambda: self.make_provider(self.store), "career_session_in_use", 409)
        self.assertTrue(self.store.held)
        self.provider.close()
        replacement = self.make_provider(self.store)
        self.assertTrue(self.store.held)
        replacement.close()

    def test_default_runtime_receives_explicit_resource_root_without_other_loaders(self):
        with patch.object(local, "NativeProfileQuery", return_value=self.query) as runtime:
            other = self.make_provider(OwnedMemoryStore(), query=None, runtime_root="synthetic-public-root")
            runtime.assert_called_once_with(root="synthetic-public-root")
            self.assertTrue(other.can_refresh_account(account()))
        with patch.object(local, "NativeProfileQuery", side_effect=RuntimeError("fixture-private-path")):
            self.error(lambda: self.make_provider(OwnedMemoryStore(), query=None), "career_config_invalid")


if __name__ == "__main__":
    unittest.main()
