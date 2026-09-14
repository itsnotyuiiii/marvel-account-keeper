"""Offline cloud desktop tests: private config, exact contracts and lifecycle."""

import json
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from career_provider import CareerUnavailable
import cloud_desktop_provider as cloud
from standalone_web_contract import project_profile_envelope
from tests.standalone_web_contract_test import NOW, UID, bundle


TOKEN = "fixture-private-service-token-1234567890"
KEY = b"k" * 32
JOB = "a" * 32


def account(uid=UID, platform="pc"):
    return {"rivals_uid": str(uid), "rivals_platform": platform}


def envelope(uid=UID):
    profile = bundle()
    if uid != UID:
        profile["uid"] = profile["identity"]["uid"] = str(uid)
        profile["identity"]["is_owner"] = False
        profile["career"]["data"]["uid"] = str(uid)
        profile["matches"]["data"]["uid"] = str(uid)
        profile["matches"]["data"]["matches"][0]["uid"] = str(uid)
        profile["current_rank"] = profile["current_rank_source"] = None
    return project_profile_envelope(profile, uid, NOW)


def response(status=200, body=None, headers=None):
    if body is None:
        body = envelope()
    return status, headers or {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8")


class CloudDesktopProviderTest(unittest.TestCase):
    def setUp(self):
        self.network = patch("socket.socket", side_effect=AssertionError("no networking"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / cloud.CLOUD_CONFIG_FILE
        self.now = NOW.timestamp()
        self.ticks = 0.0
        self.key = KEY
        self.transport = Mock(return_value=response())
        self.unprotect = Mock(return_value=json.dumps({"version": 1, "origin": cloud.DEFAULT_ORIGIN,
                                                      "token": TOKEN}).encode("utf-8"))
        self.waits = []
        self.provider = self.make_provider()

    def make_provider(self, **kwargs):
        values = {"transport": self.transport, "unprotect": self.unprotect,
                  "clock": lambda: self.now, "monotonic": lambda: self.ticks, "wait": self.wait}
        values.update(kwargs)
        provider = cloud.CloudDesktopConnection(self.directory, lambda: self.key, **values)
        self.addCleanup(provider.close)
        return provider

    def wait(self, seconds):
        self.waits.append(seconds)
        self.ticks += seconds

    def provision(self):
        return cloud.provision_cloud_config(cloud.DEFAULT_ORIGIN, TOKEN, self.path,
                                            protect=lambda raw: b"encrypted-fixture")

    def error(self, call, code, status=None):
        with self.assertRaises(CareerUnavailable) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        if status is not None:
            self.assertEqual(caught.exception.status, status)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn("fixture-private", str(caught.exception))
        return caught.exception

    def test_missing_private_config_has_no_login_or_network_side_effect(self):
        status = self.provider.status()
        self.assertEqual(status["provider"], "cloud")
        self.assertFalse(status["configured"])
        self.assertFalse(status["connected"])
        self.assertFalse(status["can_connect"])
        self.assertEqual(status["state"], "unconfigured")
        self.assertFalse(self.provider.can_refresh_account(account()))
        self.error(lambda: self.provider.refresh_account(account()), "cloud_not_configured", 409)
        self.assertFalse(self.path.exists())
        self.transport.assert_not_called()
        self.unprotect.assert_not_called()
        self.assertEqual(repr(self.provider), "<CloudDesktopConnection>")

    def test_provision_encrypts_separate_file_without_plaintext_or_overwrite(self):
        protect = Mock(return_value=b"encrypted-fixture")
        output = cloud.provision_cloud_config(cloud.DEFAULT_ORIGIN + "/", TOKEN, self.path, protect=protect)
        self.assertEqual(output, self.path)
        self.assertEqual(self.path.read_bytes(), b"encrypted-fixture")
        config = json.loads(protect.call_args.args[0])
        self.assertEqual(config, {"version": 1, "origin": cloud.DEFAULT_ORIGIN, "token": TOKEN})
        self.assertNotIn(TOKEN.encode(), self.path.read_bytes())
        self.error(lambda: self.provision(), "cloud_config_invalid")
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_provision_rejects_unapproved_origin_tokens_and_protection_failure(self):
        for origin in ("http://localhost", "https://example.com", cloud.DEFAULT_ORIGIN + "/path",
                       cloud.DEFAULT_ORIGIN + "?token=x", cloud.DEFAULT_ORIGIN + "//", True, None):
            self.error(lambda: cloud.provision_cloud_config(origin, TOKEN, self.path, protect=lambda raw: raw),
                       "cloud_config_invalid")
        for token in (None, True, "x", "x" * 5000, TOKEN + "\n", TOKEN + " ", "Bearer " + TOKEN):
            self.error(lambda: cloud.provision_cloud_config(cloud.DEFAULT_ORIGIN, token, self.path,
                        protect=lambda raw: raw), "cloud_config_invalid")
        for encrypted in (None, "bad", b"", b"x" * 16385):
            self.error(lambda: cloud.provision_cloud_config(cloud.DEFAULT_ORIGIN, TOKEN, self.path,
                        protect=lambda raw: encrypted), "cloud_config_invalid")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_locked_vault_never_decrypts_or_sends(self):
        self.provision()
        for key in (None, b"", b"x" * 31, b"x" * 33, "x" * 32, bytearray(KEY)):
            self.key = key
            self.assertEqual(self.provider.status()["state"], "locked")
            self.assertFalse(self.provider.can_refresh_account(account()))
            self.error(lambda: self.provider.refresh_account(account()), "locked", 401)
        self.unprotect.assert_not_called()
        self.transport.assert_not_called()

    def test_status_is_local_readiness_not_an_upstream_authentication_claim(self):
        self.provision()
        status = self.provider.status()
        self.assertTrue(status["configured"])
        self.assertTrue(status["connected"])
        self.assertEqual(status["state"], "ready")
        self.assertFalse(status["upstream_verified"])
        self.assertIn("to verify", status["message"])
        self.assertNotIn(TOKEN, json.dumps(status))
        self.assertNotIn(cloud.DEFAULT_ORIGIN, json.dumps(status))
        self.transport.assert_not_called()

    def test_corrupt_or_foreign_user_config_does_not_fall_back_or_replace(self):
        self.provision()
        self.unprotect.side_effect = ValueError("fixture-private-secret")
        self.error(lambda: self.provider.refresh_account(account()), "cloud_config_invalid", 409)
        self.assertEqual(self.provider.status()["state"], "unavailable")
        self.assertEqual(self.path.read_bytes(), b"encrypted-fixture")
        self.transport.assert_not_called()

    def test_decrypted_config_schema_strict_and_bounded(self):
        self.provision()
        configs = [b"{}", b"[1]", b"x" * 8193,
            b'{"version":1,"version":1}',
            json.dumps({"version": True, "origin": cloud.DEFAULT_ORIGIN, "token": TOKEN}).encode(),
            json.dumps({"version": 1, "origin": "https://example.com", "token": TOKEN}).encode(),
            json.dumps({"version": 1, "origin": cloud.DEFAULT_ORIGIN, "token": TOKEN, "aws_secret": TOKEN}).encode()]
        for config in configs:
            self.unprotect.return_value = config
            self.error(lambda: self.provider.refresh_account(account()), "cloud_config_invalid")
        self.transport.assert_not_called()

    def test_uid_platform_validation_happens_before_decryption(self):
        self.provision()
        for uid in (True, False, 1.0, 99999, 100000000000, 1 << 20000, "0123456", "１２３４５６", "1e6", " 1370598422", []):
            value = {"rivals_uid": uid, "rivals_platform": "pc"}
            self.assertFalse(self.provider.can_refresh_account(value))
            self.assertIsNone(self.provider.cached_account(value))
            self.error(lambda: self.provider.refresh_account(value), "invalid_rivals_uid", 400)
        for platform in ("xbox", "playstation", "PC", True, []):
            self.error(lambda: self.provider.refresh_account(account(platform=platform)), "career_platform_unavailable")
        self.error(lambda: self.provider.refresh_account({"in_game_name": "Someone"}), "career_uid_required")
        self.error(lambda: self.provider.refresh_account({}), "missing_identity")
        self.unprotect.assert_not_called()
        self.transport.assert_not_called()

    def test_success_uses_only_exact_uid_and_service_bearer(self):
        self.provision()
        result = self.provider.refresh_account({**account(), "password": "not-sent", "username": "not-sent",
                                                 "notes": "not-sent", "in_game_name": "not-sent"})
        self.assertEqual(set(result), {"uid", "platform", "in_game_name", "fetched_at", "native_profile"})
        self.assertEqual(result["fetched_at"], int(self.now))
        self.assertEqual(result["uid"], str(UID))
        self.assertEqual(result["in_game_name"], "Synthetic ゆい")
        args = self.transport.call_args.args
        self.assertEqual(args[0], cloud.DEFAULT_ORIGIN + "/v1/players/" + str(UID))
        self.assertEqual(args[1], {"Authorization": "Bearer " + TOKEN, "Accept": "application/json"})
        self.assertEqual(args[3], 65536)
        self.assertLessEqual(args[2], 15)
        self.assertTrue(self.provider.status()["upstream_verified"])
        self.assertNotIn("not-sent", repr(self.transport.call_args))

    def test_accepted_integer_uid_and_unknown_platform(self):
        self.provision()
        for platform in (None, "", "unknown", "pc"):
            value = {"rivals_uid": UID, "rivals_platform": platform}
            self.assertTrue(self.provider.can_refresh_account(value))
            self.assertEqual(self.provider.refresh_account(value)["platform"], "pc")
        self.transport.assert_called_once()

    def test_unknown_fields_dropped_at_all_levels(self):
        self.provision()
        source = envelope()
        source["private"] = TOKEN
        source["nativeProfile"]["sessionid"] = TOKEN
        source["nativeProfile"]["identity"]["password"] = TOKEN
        source["nativeProfile"]["career"]["data"]["statistics"]["private"] = TOKEN
        self.transport.return_value = response(body=source)
        result = self.provider.refresh_account(account())
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_projection_rejects_wrong_uid_source_shape_time_and_unverified_data(self):
        self.provision()
        variants = []
        for change in ({"uid": "1822797559"}, {"source": "tracker"}, {"schemaVersion": True},
                       {"schemaVersion": 1}, {"fetchedAt": "2026-09-12T20:00:00"},
                       {"fetchedAt": "2026-09-12T20:06:00+00:00"}, {"fetchedAt": "2020-01-01T00:00:00Z"}):
            variants.append({**envelope(), **change})
        for field in ("identity_verified", "owner_authentication_verified", "game_login_verified", "social_session_accepted"):
            value = envelope(); value["nativeProfile"][field] = False; variants.append(value)
        value = envelope(); value["nativeProfile"]["identity"]["uid"] = "1822797559"; variants.append(value)
        value = envelope(); value["nativeProfile"]["matches"]["data"]["matches"][0]["uid"] = "1822797559"; variants.append(value)
        value = envelope(); value["nativeProfile"]["career"]["data"]["statistics"]["wins"] = 99999; variants.append(value)
        for value in variants:
            self.transport.return_value = response(body=value)
            self.error(lambda: self.provider.refresh_account(account()), "cloud_response_invalid", 502)
            self.assertIsNone(self.provider.cached_account(account()))

    def test_pending_resumes_same_uid_and_job_without_new_lookup(self):
        self.provision()
        self.transport.side_effect = [
            response(503, {"error": {"code": "lookup_pending"}, "lookupId": JOB}, {"Retry-After": "3"}),
            response(503, {"error": {"code": "lookup_pending"}, "lookupId": JOB}), response()]
        self.provider.refresh_account(account())
        urls = [call.args[0] for call in self.transport.call_args_list]
        base = cloud.DEFAULT_ORIGIN + "/v1/players/" + str(UID)
        self.assertEqual(urls, [base, base + "?lookup=" + JOB, base + "?lookup=" + JOB])
        self.assertEqual(self.waits, [3, 2])
        self.assertEqual(self.provider._pending, {})

    def test_pending_timeout_keeps_job_for_next_manual_refresh(self):
        self.provision()
        self.transport.return_value = response(503, {"error": {"code": "lookup_pending"}, "lookupId": JOB},
                                               {"Retry-After": "31"})
        self.error(lambda: self.provider.refresh_account(account()), "cloud_lookup_pending", 503)
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(self.waits, [])
        self.transport.return_value = response()
        self.provider.refresh_account(account())
        self.assertTrue(self.transport.call_args.args[0].endswith("?lookup=" + JOB))

    def test_changed_or_malformed_pending_id_is_never_sent(self):
        self.provision()
        for job in ("../../secret", "a" * 31, "A" * 32, None, True):
            self.transport.return_value = response(503, {"error": {"code": "lookup_pending"}, "lookupId": job})
            self.error(lambda: self.provider.refresh_account(account()), "cloud_response_invalid")
        self.transport.side_effect = [response(503, {"error": {"code": "lookup_pending"}, "lookupId": JOB}),
                                      response(503, {"error": {"code": "lookup_pending"}, "lookupId": "b" * 32})]
        self.error(lambda: self.provider.refresh_account(account()), "cloud_response_invalid")
        self.assertNotIn("b" * 32, self.transport.call_args.args[0])

    def test_busy_and_rate_limit_retry_after_are_bounded(self):
        self.provision()
        self.transport.side_effect = [response(503, {"error": {"code": "lookup_busy"}}, {"Retry-After": "2"}),
                                      response(429, {"message": "Too Many Requests"}, {"retry-after": "4"}), response()]
        self.provider.refresh_account(account())
        self.assertEqual(self.waits, [2, 4])
        self.provider.clear()
        self.transport.side_effect = None
        self.transport.return_value = response(429, {"message": "Too Many Requests"}, {"Retry-After": "120"})
        self.error(lambda: self.provider.refresh_account(account()), "cloud_rate_limited", 429)
        self.assertEqual(self.waits, [2, 4])

    def test_max_attempts_limits_zero_retry_after_without_real_sleep(self):
        self.provision()
        self.transport.return_value = response(503, {"error": {"code": "lookup_pending"}, "lookupId": JOB}, {"Retry-After": "0"})
        self.error(lambda: self.provider.refresh_account(account()), "cloud_lookup_pending")
        self.assertEqual(self.transport.call_count, cloud.MAX_ATTEMPTS)
        self.assertTrue(all(wait >= 1 for wait in self.waits))

    def test_typed_no_profile_is_not_invalid_uid_and_exact_uid_required(self):
        self.provision()
        self.transport.return_value = response(404, {"error": {"code": "profile_not_returned"}, "uid": str(UID)})
        error = self.error(lambda: self.provider.refresh_account(account()), "cloud_profile_not_returned", 404)
        self.assertIn("does not establish", error.message)
        self.transport.return_value = response(404, {"error": {"code": "profile_not_returned"}, "uid": "1822797559"})
        self.error(lambda: self.provider.refresh_account(account()), "cloud_response_invalid")

    def test_fixed_terminal_errors_never_echo_remote_messages_or_retry(self):
        self.provision()
        cases = [(401, "unknown", "cloud_access_rejected"), (403, "unknown", "cloud_access_rejected"),
                 (503, "lookup_unavailable", "cloud_lookup_unavailable"), (502, "bad", "cloud_lookup_unavailable"),
                 (410, "lookup_expired", "cloud_lookup_expired"), (404, "bad", "cloud_response_invalid")]
        for status, code, expected in cases:
            self.transport.reset_mock()
            self.transport.return_value = response(status, {"error": {"code": code, "message": TOKEN}})
            self.error(lambda: self.provider.refresh_account(account()), expected)
            self.transport.assert_called_once()

    def test_non_json_redirect_duplicate_fields_and_oversize_are_rejected(self):
        self.provision()
        for status, raw in ((200, b"<html>secret</html>"), (200, b'{"uid":"1","uid":"2"}'),
                            (200, b'{"value":NaN}'), (200, b"x" * 65537), (302, b"{}")):
            self.transport.return_value = (status, {"Location": "https://elsewhere.invalid"}, raw)
            self.error(lambda: self.provider.refresh_account(account()), "cloud_response_invalid")

    def test_network_failure_redacted_without_automatic_new_job(self):
        self.provision()
        self.transport.side_effect = RuntimeError(TOKEN)
        self.error(lambda: self.provider.refresh_account(account()), "cloud_request_failed", 503)
        self.transport.assert_called_once()

    def test_cache_is_copied_age_bounded_and_other_uid_survives_target_failure(self):
        self.provision()
        first = self.provider.refresh_account(account())
        first["native_profile"]["identity"]["in_game_name"] = "mutated"
        self.now += 29
        second = self.provider.refresh_account(account())
        self.assertEqual(second["in_game_name"], "Synthetic ゆい")
        self.transport.assert_called_once()
        target = 1822797559
        self.transport.return_value = response(body=envelope(target))
        self.provider.refresh_account(account(target))
        self.now += 2
        self.transport.return_value = response(503, {"error": {"code": "lookup_unavailable"}})
        self.error(lambda: self.provider.refresh_account(account()), "cloud_lookup_unavailable")
        self.assertIsNone(self.provider.cached_account(account()))
        self.assertIsNotNone(self.provider.cached_account(account(target)))
        value = self.provider.cached_account(account(target))
        value["native_profile"]["matches"]["data"]["matches"].clear()
        self.assertEqual(len(self.provider.cached_account(account(target))["native_profile"]["matches"]["data"]["matches"]), 1)

    def test_clock_backwards_does_not_claim_cached_freshness(self):
        self.provision()
        self.provider.refresh_account(account())
        self.now -= 1
        self.provider.refresh_account(account())
        self.assertEqual(self.transport.call_count, 2)

    def test_expired_pending_job_is_terminal_then_next_refresh_starts_new_job(self):
        self.provision()
        self.transport.side_effect = [response(503, {"error": {"code": "lookup_pending"}, "lookupId": JOB}),
                                      response(410, {"error": {"code": "lookup_expired"}}), response()]
        self.error(lambda: self.provider.refresh_account(account()), "cloud_lookup_expired")
        self.assertEqual(self.provider._pending, {})
        self.provider.refresh_account(account())
        self.assertNotIn("?lookup=", self.transport.call_args.args[0])

    def test_clear_during_inflight_request_cannot_refill_cache(self):
        self.provision()

        def clear_during_request(*args):
            self.provider.clear()
            return response()

        self.transport.side_effect = clear_during_request
        self.error(lambda: self.provider.refresh_account(account()), "locked")
        self.assertEqual(self.provider._cache, {})

    def test_cache_eviction_does_not_retain_unbounded_profile_data(self):
        self.provision()
        with patch.object(cloud, "MAX_CACHE", 2):
            for uid in (UID, 1822797559, 10826896958):
                self.transport.return_value = response(body=envelope(uid))
                self.provider.refresh_account(account(uid))
        self.assertEqual(len(self.provider._cache), 2)
        self.assertIsNone(self.provider.cached_account(account()))

    def test_suspend_releases_credentials_and_cache_then_unlock_reloads(self):
        self.provision()
        self.provider.refresh_account(account())
        self.assertEqual(self.unprotect.call_count, 1)
        self.key = None
        self.provider.suspend()
        self.assertIsNone(self.provider._credentials)
        self.assertEqual(self.provider._cache, {})
        self.assertEqual(self.provider._pending, {})
        self.assertIsNone(self.provider.cached_account(account()))
        self.assertFalse(self.provider.status()["upstream_verified"])
        self.key = KEY
        self.provider.refresh_account(account())
        self.assertEqual(self.unprotect.call_count, 2)
        self.assertEqual(self.transport.call_count, 2)
        self.assertTrue(self.path.exists())

    def test_inflight_lock_discards_response_and_serializes_operations(self):
        self.provision()
        entered, release = threading.Event(), threading.Event()
        errors = []

        def blocked(*args):
            entered.set()
            release.wait(3)
            return response()

        def refresh():
            try:
                self.provider.refresh_account(account())
            except CareerUnavailable as error:
                errors.append(error.code)

        self.transport.side_effect = blocked
        thread = threading.Thread(target=refresh)
        thread.start()
        self.assertTrue(entered.wait(3))
        self.error(lambda: self.provider.refresh_account(account()), "career_busy", 429)
        self.key = None
        suspender = threading.Thread(target=self.provider.suspend)
        suspender.start()
        release.set()
        thread.join(3); suspender.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(suspender.is_alive())
        self.assertEqual(errors, ["locked"])
        self.assertIsNone(self.provider._credentials)
        self.assertEqual(self.provider._cache, {})

    def test_suspend_cancels_pending_pause_without_new_request(self):
        self.provision()
        entered = threading.Event()
        errors = []
        provider = self.make_provider(wait=None)

        def pending(*args):
            entered.set()
            return response(503, {"error": {"code": "lookup_pending"}, "lookupId": JOB}, {"Retry-After": "10"})

        self.transport.side_effect = pending

        def refresh():
            try:
                provider.refresh_account(account())
            except CareerUnavailable as error:
                errors.append(error.code)

        thread = threading.Thread(target=refresh)
        thread.start()
        self.assertTrue(entered.wait(3))
        self.key = None
        provider.suspend()
        thread.join(3)
        self.assertEqual(errors, ["locked"])
        self.transport.assert_called_once()

    def test_close_permanent_and_local_net_ease_login_is_not_exposed(self):
        self.provision()
        self.error(self.provider.connect, "cloud_managed_session")
        self.error(self.provider.recover, "cloud_managed_session")
        self.error(self.provider.refresh, "career_uid_required")
        self.assertIsNone(self.provider.enable_browser(Mock()))
        self.provider.close()
        self.error(lambda: self.provider.refresh_account(account()), "career_closed")
        self.assertEqual(self.provider.status()["state"], "closed")
        self.assertFalse(self.provider.can_refresh_account(account()))


class HttpTransportTest(unittest.TestCase):
    def test_bearer_does_not_follow_redirects_or_use_environment_auth(self):
        import requests
        response_mock = Mock()
        response_mock.__enter__ = Mock(return_value=response_mock)
        response_mock.__exit__ = Mock(return_value=False)
        response_mock.status_code = 302
        response_mock.headers = {"Location": "https://unapproved.invalid"}
        response_mock.iter_content.return_value = [b"{}"]
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.return_value = response_mock
        with patch.object(requests, "Session", return_value=session):
            result = cloud._http_get(cloud.DEFAULT_ORIGIN + "/v1/players/" + str(UID),
                                     {"Authorization": "Bearer " + TOKEN}, 15, 65536)
        self.assertEqual(result[0], 302)
        self.assertIs(session.trust_env, False)
        self.assertIs(session.get.call_args.kwargs["allow_redirects"], False)
        self.assertIs(session.get.call_args.kwargs["stream"], True)
        self.assertEqual(session.get.call_args.kwargs["timeout"], (5.0, 15))
        session.get.assert_called_once()
        response_mock.__exit__.assert_called_once()
        session.__exit__.assert_called_once()

    def test_oversized_stream_is_stopped_and_closed(self):
        import requests
        response_mock = Mock()
        response_mock.__enter__ = Mock(return_value=response_mock)
        response_mock.__exit__ = Mock(return_value=False)
        response_mock.iter_content.return_value = [b"x" * 8192] * 10
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.return_value = response_mock
        with patch.object(requests, "Session", return_value=session), self.assertRaises(ValueError):
            cloud._http_get(cloud.DEFAULT_ORIGIN, {"Authorization": "Bearer " + TOKEN}, 15, 65536)
        response_mock.__exit__.assert_called_once()
        session.__exit__.assert_called_once()


@unittest.skipUnless(sys.platform == "win32", "Windows-user DPAPI only")
class WindowsDpapiTest(unittest.TestCase):
    def test_real_dpapi_synthetic_connection_roundtrip_has_no_plaintext(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / cloud.CLOUD_CONFIG_FILE
            cloud.provision_cloud_config(cloud.DEFAULT_ORIGIN, TOKEN, path)
            self.assertNotIn(TOKEN.encode(), path.read_bytes())
            provider = cloud.CloudDesktopConnection(folder, lambda: KEY,
                transport=Mock(return_value=response()), clock=lambda: NOW.timestamp())
            try:
                self.assertEqual(provider.refresh_account(account())["uid"], str(UID))
                self.assertTrue(provider.status()["upstream_verified"])
            finally:
                provider.close()


if __name__ == "__main__":
    unittest.main()
