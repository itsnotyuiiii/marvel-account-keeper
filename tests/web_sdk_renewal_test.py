"""Synthetic OAuth response and HTTP-adapter tests; no real credentials/network."""
import copy
from contextlib import ExitStack
import json
import io
import unittest
from unittest.mock import patch
from urllib3.connection import HTTPSConnection

import web_sdk_renewal as renewal
from web_sdk_auth_probe_test import fixture


def context():
    return dict(fixture(), refresh_token="fixture-renewal-secret")


def response(**fields):
    return json.dumps(dict(code=0, access_token="fixture-new-session", **fields)).encode("utf-8")


class WebSdkRenewalTest(unittest.TestCase):
    def test_success_changes_only_session_and_never_asserts_owner_or_renewal_verified(self):
        previous = context()
        before = copy.deepcopy(previous)
        candidate = renewal.validate_oauth_refresh_response(previous, response(), http_status=200)
        self.assertEqual(candidate.sauth, dict(fixture(), sessionid="fixture-new-session"))
        self.assertEqual(candidate.refresh_token, "fixture-renewal-secret")
        self.assertEqual(previous, before)
        self.assertFalse(candidate.owner_authentication_verified)
        self.assertFalse(candidate.automatic_renewal_verified)
        self.assertNotIn("refresh_token", candidate.sauth)
        self.assertNotIn("gas_token", candidate.sauth)
        self.assertNotIn("fixture-", repr(candidate))
        candidate.sauth["sdkuid"] = "changed-in-candidate"
        self.assertEqual(previous, before)

    def test_success_follows_sdk_2xx_policy_not_http_status_alone(self):
        for status in (200, 201, 299):
            candidate = renewal.validate_oauth_refresh_response(context(), response(), http_status=status)
            self.assertFalse(candidate.owner_authentication_verified)
        for status in (True, "200", 0, 199, 300, 401, 500, None):
            with self.subTest(status=status), self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_http_error"):
                renewal.validate_oauth_refresh_response(context(), response(), http_status=status)
        with self.assertRaises(TypeError):
            renewal.validate_oauth_refresh_response(context(), response())

    def test_previous_request_context_and_nonempty_renewal_token_are_required(self):
        cases = [None, [], {}, fixture(), dict(context(), refresh_token=""),
                 dict(context(), refresh_token=" "), dict(context(), refresh_token=123),
                 dict(context(), refresh_token="x" * 32769), dict(context(), gameid="different-game"),
                 dict(context(), platform="web"), dict(context(), extra="fixture-secret")]
        for previous in cases:
            with self.subTest(context_type=type(previous).__name__), \
                    self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_context_invalid"):
                renewal.validate_oauth_refresh_response(previous, response(), http_status=200)

    def test_body_is_bytes_and_bounded(self):
        for raw in (None, "{}", bytearray(b"{}"), b"", b"x" * (renewal.MAX_RESPONSE_BYTES + 1)):
            with self.subTest(raw_type=type(raw).__name__), \
                    self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_response_bound"):
                renewal.validate_oauth_refresh_response(context(), raw, http_status=200)

    def test_json_rejects_duplicates_nonfinite_and_bad_encoding(self):
        for raw in (b"{", b"\xff", b'\xef\xbb\xbf{}', b'{"code":0,"code":1}',
                    b'{"code":0,"extra":NaN}', b'{"code":0,"extra":Infinity}',
                    b'{"code":0,"extra":{"x":1,"x":2}}'):
            with self.subTest(raw_prefix=raw[:16]), \
                    self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_response_json"):
                renewal.validate_oauth_refresh_response(context(), raw, http_status=200)

    def test_result_code_must_be_exact_integer(self):
        for value in ([], None, "0", {}, {"code": True}, {"code": "0"}, {"code": 0.0}, {}):
            with self.subTest(value_type=type(value).__name__), \
                    self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_response_shape"):
                renewal.validate_oauth_refresh_response(context(), json.dumps(value).encode(), http_status=200)

    def test_rejection_requires_normal_reauthentication_without_free_text(self):
        before = context()
        for code in (1, 401, -1):
            raw = json.dumps({"code": code, "msg": "fixture-private-error", "access_token": "fixture-secret"}).encode()
            with self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_reauthentication_required") as raised:
                renewal.validate_oauth_refresh_response(before, raw, http_status=200)
            self.assertNotIn("fixture-", str(raised.exception))
        self.assertEqual(before, context())

    def test_access_token_is_nonempty_bounded_and_wire_valid(self):
        for value in (None, "", " ", 123, True, {}, [], "x" * 32769, "\ud800", "\0" * 10900):
            raw = json.dumps({"code": 0, "access_token": value}).encode()
            with self.subTest(token_type=type(value).__name__), \
                    self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_access_token_shape"):
                renewal.validate_oauth_refresh_response(context(), raw, http_status=200)
        with self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_access_token_shape"):
            renewal.validate_oauth_refresh_response(context(), b'{"code":0}', http_status=200)

    def test_unverified_rotation_is_not_silently_accepted_or_ignored(self):
        for value in (None, "", "fixture-renewal-secret", "fixture-rotated-secret"):
            with self.subTest(value_type=type(value).__name__), \
                    self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_rotation_unverified") as raised:
                renewal.validate_oauth_refresh_response(context(), response(refresh_token=value), http_status=200)
            self.assertNotIn("fixture-", str(raised.exception))

    def test_response_extras_never_replace_request_identity_or_leak(self):
        candidate = renewal.validate_oauth_refresh_response(context(), response(
            sdkuid="different-response-user", deviceid="different-device", msg="fixture-private-message",
            gas_token="not-a-game-token", owner_authentication_verified=True), http_status=200)
        self.assertEqual(candidate.sauth["sdkuid"], fixture()["sdkuid"])
        self.assertEqual(candidate.sauth["deviceid"], fixture()["deviceid"])
        self.assertFalse(candidate.owner_authentication_verified)
        self.assertNotIn("fixture-private-message", json.dumps(candidate.sauth))
        self.assertNotIn("not-a-game-token", json.dumps(candidate.sauth))

    def test_validation_does_not_read_files_network_or_login(self):
        targets = ("builtins.open", "pathlib.Path.open", "socket.create_connection", "requests.get", "requests.post",
                   "game_login_probe.check_login", "game_login_probe.load_own_login_inputs",
                   "game_login_probe.advertised_server", "game_transport_probe.load_public_configuration")
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(target, side_effect=AssertionError("unexpected dependency")))
                      for target in targets]
            candidate = renewal.validate_oauth_refresh_response(context(), response(), http_status=200)
        for guard in guards:
            guard.assert_not_called()
        self.assertFalse(candidate.automatic_renewal_verified)


class FakeHTTPBody(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.read_sizes = []
        self.read_error = None

    def read(self, size=-1):
        self.read_sizes.append(size)
        if self.read_error is not None:
            raise self.read_error
        return super().read(size)

    def release_conn(self):
        self.close()


class WebSdkRefreshHTTPTest(unittest.TestCase):
    def setUp(self):
        self.previous = dict(context(), sdkuid="123456789012345")
        self.configuration = renewal.OAuthRefreshConfiguration(
            endpoint=renewal.OAUTH_REFRESH_ENDPOINT, appid="502")
        self.calls, self.responses, self.bodies = [], [], []
        self.status, self.raw, self.headers = 200, response(), {}
        self.on_send = None
        self.read_error = None

    def fake_send(self, adapter, request, **kwargs):
        self.assertEqual(adapter.max_retries.total, 0)
        self.calls.append((request, kwargs))
        if self.on_send is not None:
            self.on_send()
        result = renewal.requests.Response()
        result.status_code, result.request, result.url = self.status, request, request.url
        result.headers.update(self.headers)
        result.raw = FakeHTTPBody(self.raw)
        result.raw.read_error = self.read_error
        self.responses.append(result)
        self.bodies.append(result.raw)
        return result

    def run_fake(self):
        owner = self

        def send(adapter, request, **kwargs):
            return owner.fake_send(adapter, request, **kwargs)

        with patch("requests.adapters.HTTPAdapter.send", new=send):
            return renewal.refresh_once(self.previous, configuration=self.configuration)

    def assert_closed_once(self):
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(body.closed for body in self.bodies))

    def test_one_exact_post_without_browser_environment_or_fallback(self):
        before = copy.deepcopy(self.previous)
        guards = ("requests.sessions.get_netrc_auth", "builtins.open", "pathlib.Path.open",
                  "socket.create_connection", "game_login_probe.check_login",
                  "game_login_probe.load_own_login_inputs", "game_login_probe.advertised_server",
                  "game_transport_probe.load_public_configuration")
        with ExitStack() as stack:
            blocked = [stack.enter_context(patch(target, side_effect=AssertionError("unexpected dependency")))
                       for target in guards]
            stack.enter_context(patch.dict("os.environ", {
                "HTTPS_PROXY": "https://fixture-private-proxy.invalid/", "NETRC": "fixture-netrc",
                "REQUESTS_CA_BUNDLE": "fixture-cert-file", "CURL_CA_BUNDLE": "fixture-other-cert",
            }))
            candidate = self.run_fake()
        for guard in blocked:
            guard.assert_not_called()
        self.assert_closed_once()
        request, options = self.calls[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, "https://sdk-os.mpsdk.easebar.com//api/users/login/v2/oauth/refresh_token")
        self.assertEqual(request.body, "game_id=502&user_id=123456789012345&refresh_token=fixture-renewal-secret")
        self.assertEqual(dict(request.headers), {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": str(len(request.body)),
        })
        self.assertEqual(options["proxies"], {})
        self.assertIs(options["verify"], True)
        self.assertIs(options["stream"], True)
        self.assertIsNone(options["cert"])
        self.assertEqual(options["timeout"], 10)
        expected = dict(before, sessionid="fixture-new-session")
        del expected["refresh_token"]
        self.assertEqual(candidate.sauth, expected)
        self.assertEqual(self.previous, before)

    def test_success_preserves_context_and_unverified_flags(self):
        before = copy.deepcopy(self.previous)
        candidate = self.run_fake()
        expected = dict(before, sessionid="fixture-new-session")
        del expected["refresh_token"]
        self.assertEqual(candidate.sauth, expected)
        self.assertEqual(candidate.refresh_token, before["refresh_token"])
        self.assertFalse(candidate.owner_authentication_verified)
        self.assertFalse(candidate.automatic_renewal_verified)
        self.assertEqual(before, self.previous)
        self.assertNotIn("fixture-", repr(candidate))
        self.assert_closed_once()

    def test_snapshot_binds_response_even_if_caller_changes_context_during_io(self):
        original = self.previous.copy()
        self.on_send = lambda: self.previous.update(sdkuid="999", refresh_token="fixture-unrelated")
        candidate = self.run_fake()
        self.assertEqual(candidate.sauth["sdkuid"], original["sdkuid"])
        self.assertEqual(candidate.refresh_token, original["refresh_token"])
        self.assert_closed_once()

    def test_only_explicit_exact_reviewed_configuration_before_io(self):
        cases = [None, {}, {"endpoint": renewal.OAUTH_REFRESH_ENDPOINT, "appid": "502"},
                 renewal.OAuthRefreshConfiguration(renewal.OAUTH_REFRESH_ENDPOINT, 502),
                 renewal.OAuthRefreshConfiguration(renewal.OAUTH_REFRESH_ENDPOINT, "0502"),
                 renewal.OAuthRefreshConfiguration(renewal.OAUTH_REFRESH_ENDPOINT, "x20naxx2gb"),
                 renewal.OAuthRefreshConfiguration(renewal.OAUTH_REFRESH_ENDPOINT.replace(".com//", ".com/"), "502"),
                 renewal.OAuthRefreshConfiguration("https://fixture.invalid", "502")]
        with patch.object(renewal, "_NoRedirectSession") as create:
            for config in cases:
                with self.subTest(config_type=type(config).__name__), self.assertRaisesRegex(
                        renewal.WebSdkRenewalError, "web_refresh_configuration_unreviewed") as raised:
                    renewal.refresh_once(self.previous, configuration=config)
                self.assertFalse(raised.exception.request_attempted)
            with self.assertRaises(TypeError):
                renewal.refresh_once(self.previous)
        create.assert_not_called()

    def test_unsupported_numeric_sdk_ids_never_reach_io(self):
        cases = ["fixture-sdk-user", "0", "00", "01", "-1", "+1", " 1", "1 ", "1.0",
                 "1e3", "0x10", "1\n", "\u0661", "9007199254740992", "9999999999999999"]
        with patch.object(renewal, "_NoRedirectSession") as create:
            for value in cases:
                with self.subTest(value=value), self.assertRaisesRegex(
                        renewal.WebSdkRenewalError, "web_refresh_numeric_sdkuid_unsupported") as raised:
                    renewal.refresh_once(dict(self.previous, sdkuid=value), configuration=self.configuration)
                self.assertFalse(raised.exception.request_attempted)
        create.assert_not_called()

    def test_js_safe_integer_edges_are_lossless(self):
        for value in ("1", "9007199254740991"):
            with self.subTest(value=value):
                self.previous["sdkuid"] = value
                candidate = self.run_fake()
                self.assertEqual(candidate.sauth["sdkuid"], value)
                self.assertIn("&user_id=" + value + "&", self.calls[-1][0].body)

    def test_raw_form_tokens_cannot_change_decoded_value(self):
        with patch.object(renewal, "_NoRedirectSession") as create:
            for token in ("fixture+secret", "fixture&secret", "fixture%20secret", "fixture%secret",
                          "fixture\nsecret", "fixture secret", "fixture\0secret", "fixture\u00e9secret"):
                with self.subTest(token_length=len(token)), self.assertRaisesRegex(
                        renewal.WebSdkRenewalError, "web_refresh_form_token_unsupported"):
                    renewal.refresh_once(dict(self.previous, refresh_token=token), configuration=self.configuration)
        create.assert_not_called()
        self.previous["refresh_token"] = "fixture.base64/url_token-~=="
        candidate = self.run_fake()
        self.assertEqual(candidate.refresh_token, self.previous["refresh_token"])
        self.assertTrue(self.calls[-1][0].body.endswith(self.previous["refresh_token"]))

    def test_invalid_context_is_rejected_before_io(self):
        with patch.object(renewal, "_NoRedirectSession") as create:
            for previous in ({}, dict(self.previous, platform="web"), dict(self.previous, extra="fixture-secret")):
                with self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_context_invalid"):
                    renewal.refresh_once(previous, configuration=self.configuration)
        create.assert_not_called()

    def test_http_rejections_never_read_body_retry_or_follow_redirect(self):
        self.raw = b"fixture-private-error" * renewal.MAX_RESPONSE_BYTES
        self.headers = {"Location": "https://fixture-other.invalid/", "Set-Cookie": "fixture-private-cookie"}
        for status in (199, 301, 302, 307, 401, 403, 500):
            self.calls.clear()
            self.status = status
            with self.subTest(status=status), self.assertRaisesRegex(
                    renewal.WebSdkRenewalError, "web_refresh_http_error") as raised:
                self.run_fake()
            self.assert_closed_once()
            self.assertEqual(self.bodies[-1].read_sizes, [])
            self.assertNotIn("fixture-", str(raised.exception))
            self.assertTrue(raised.exception.request_attempted)
            self.assertEqual(raised.exception.http_status, status)

    def test_bounded_stream_closed_without_retry(self):
        self.raw = b"x" * (renewal.MAX_RESPONSE_BYTES + 8192)
        with self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_response_bound"):
            self.run_fake()
        self.assert_closed_once()
        self.assertEqual(self.bodies[0].read_sizes, [4096] * 17)

    def test_request_failure_is_redacted_and_never_retried(self):
        for error in (renewal.requests.Timeout("fixture-token"), RuntimeError("fixture-secret")):
            with patch("requests.adapters.HTTPAdapter.send", side_effect=error) as send, \
                    self.assertRaisesRegex(renewal.WebSdkRenewalError, "web_refresh_request_failed") as raised:
                renewal.refresh_once(self.previous, configuration=self.configuration)
            self.assertEqual(send.call_count, 1)
            self.assertNotIn("fixture-", str(raised.exception))
            self.assertTrue(raised.exception.request_attempted)
            self.assertIsNone(raised.exception.http_status)

    def test_http_success_still_uses_strict_pure_validator(self):
        for raw, expected in ((b'{"code":1,"message":"fixture-secret"}', "web_refresh_reauthentication_required"),
                              (b'{"code":0,"access_token":"fixture-session","refresh_token":"fixture-rotated"}',
                               "web_refresh_rotation_unverified"), (b'{', "web_refresh_response_json"),
                              (b'', "web_refresh_response_bound")):
            self.calls.clear()
            self.raw = raw
            with self.assertRaisesRegex(renewal.WebSdkRenewalError, expected) as raised:
                self.run_fake()
            self.assert_closed_once()
            self.assertNotIn("fixture-", str(raised.exception))
            self.assertTrue(raised.exception.request_attempted)
            self.assertEqual(raised.exception.http_status, 200)

    def test_stream_failure_closes_response_and_redacts_without_retry(self):
        self.read_error = renewal.requests.ConnectionError("fixture-private-stream-error")
        with self.assertRaises(renewal.WebSdkRenewalError) as raised:
            self.run_fake()
        self.assertEqual(raised.exception.code, "web_refresh_request_failed")
        self.assertTrue(raised.exception.request_attempted)
        self.assertEqual(raised.exception.http_status, 200)
        self.assertNotIn("fixture-", str(raised.exception))
        self.assert_closed_once()

    def test_session_creation_failure_reports_no_request_attempt(self):
        with patch.object(renewal, "_NoRedirectSession", side_effect=RuntimeError("fixture-secret")), \
                self.assertRaises(renewal.WebSdkRenewalError) as raised:
            renewal.refresh_once(self.previous, configuration=self.configuration)
        self.assertFalse(raised.exception.request_attempted)
        self.assertIsNone(raised.exception.http_status)
        self.assertEqual(raised.exception.code, "web_refresh_request_failed")

    def test_diagnostics_cannot_expose_unrecognized_text_or_fabricate_attempt_boolean(self):
        for value in ("fixture-private-error", None, [], 17):
            error = renewal.WebSdkRenewalError(value, request_attempted="fixture-secret")
            self.assertEqual(error.code, "web_refresh_request_failed")
            self.assertIsNone(error.request_attempted)
            self.assertEqual(str(error), error.code)
            self.assertNotIn("fixture-", repr(error))

    def test_http_status_metadata_accepts_only_bounded_exact_integers(self):
        for status in (100, 200, 301, 400, 599):
            with self.subTest(status=status):
                error = renewal.WebSdkRenewalError("web_refresh_http_error", http_status=status)
                self.assertEqual(error.http_status, status)
                self.assertEqual(str(error), "web_refresh_http_error")
        for status in (None, True, False, "200", 200.0, 0, 99, 600, 999, [], {}):
            with self.subTest(status_type=type(status).__name__):
                error = renewal.WebSdkRenewalError("web_refresh_http_error", http_status=status)
                self.assertIsNone(error.http_status)

    def test_transport_request_line_retains_double_slash_without_io(self):
        request = renewal.requests.Request("POST", renewal.OAUTH_REFRESH_ENDPOINT).prepare()
        adapter = renewal.requests.adapters.HTTPAdapter()
        connection = HTTPSConnection("sdk-os.mpsdk.easebar.com")
        try:
            with patch("urllib3.util.connection.create_connection", side_effect=AssertionError("unexpected IO")) as connect:
                path = adapter.request_url(request, {})
                connection.putrequest("POST", path)
            connect.assert_not_called()
            self.assertIsNone(connection.sock)
            self.assertEqual(connection._buffer[0], b"POST //api/users/login/v2/oauth/refresh_token HTTP/1.1")
        finally:
            connection.close()
            adapter.close()

    def test_pure_non2xx_validation_retains_only_bounded_http_status(self):
        with self.assertRaises(renewal.WebSdkRenewalError) as raised:
            renewal.validate_oauth_refresh_response(self.previous, b"fixture-private-body", http_status=403)
        self.assertEqual(raised.exception.http_status, 403)
        self.assertFalse(raised.exception.request_attempted)
        self.assertNotIn("fixture-", repr(raised.exception))


if __name__ == "__main__":
    unittest.main()
