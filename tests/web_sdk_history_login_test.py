"""Synthetic account/context and fake urllib responses only; no real credentials."""
import copy
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
from email.message import Message
import io
import json
import unittest
from unittest.mock import Mock, patch

import web_sdk_history_login as history
from web_sdk_auth_probe_test import fixture


def previous():
    return dict(fixture(), sdkuid="123456789012345", deviceid="fixture-prior-device")


def account():
    return {"userId": 123456789012345, "refreshToken": "fixture-saved-token", "method": "steam",
            "clientPlatform": "p", "deviceId": "fixture-saved-device"}


def context():
    return dict(history.PUBLIC_CONTEXT, ci="fixture-mpay-udid", lang="en_US", device_id="fixture-http-device")


def inputs():
    return history.build_history_login_inputs(previous(), account(), context())


def response(**extra):
    return json.dumps(dict(code=0, user={"token": "fixture-replacement-token",
                                        "login_token": "fixture-access-token"}, **extra)).encode()


class FakeReply(io.BytesIO):
    def __init__(self, raw, status=200):
        super().__init__(raw)
        self.status = status
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class HistoryInputTest(unittest.TestCase):
    def test_exact_form_and_independent_explicit_context(self):
        before = previous(), account(), context()
        snapshots = copy.deepcopy(before)
        item = history.build_history_login_inputs(*before)
        expected = ("user_id=123456789012345&token=fixture-saved-token&cp=w&platform=p&scene=login"
                    "&game_id=502&cv=3.1.5&gv=1.0.0&ci=fixture-mpay-udid"
                    "&app_channel=netease.allysdk_global&lang=en_US&device_id=fixture-http-device&debug_mode=0")
        self.assertEqual(history.build_history_form(item), expected.encode())
        self.assertEqual(before, snapshots)
        self.assertEqual(item.saved_device_id, "fixture-saved-device")
        self.assertNotEqual(item.saved_device_id, previous()["deviceid"])
        self.assertNotIn(str(history.EXPECTED_OWNER_UID).encode(), history.build_history_form(item))
        self.assertNotIn("fixture-", repr(item))
        before[1]["refreshToken"] = "fixture-mutated"
        before[2]["ci"] = "fixture-mutated"
        self.assertEqual(history.build_history_form(item), expected.encode())
        with self.assertRaises(FrozenInstanceError):
            item.saved_user_id = 999

    def test_no_required_context_field_is_defaulted(self):
        with patch.object(history.urlrequest, "build_opener") as create:
            for key in history.CONTEXT_FIELDS:
                data = context()
                del data[key]
                with self.subTest(key=key), self.assertRaises(history.HistoryLoginError):
                    history.build_history_login_inputs(previous(), account(), data)
        create.assert_not_called()

    def test_package_name_omitted_unless_explicit_including_explicit_empty(self):
        self.assertNotIn(b"package_name", history.build_history_form(inputs()))
        for value in ("", "fixture.package"):
            item = history.build_history_login_inputs(previous(), account(), dict(context(), package_name=value))
            self.assertTrue(history.build_history_form(item).endswith(("&package_name=" + value).encode()))

    def test_only_exact_projected_steam_pc_saved_account(self):
        cases = [None, {}, dict(account(), nickname="fixture-name"), dict(account(), method="epic"),
                 dict(account(), clientPlatform="pc"), dict(account(), method="qrcode")]
        for value in cases:
            with self.subTest(value_type=type(value).__name__), self.assertRaisesRegex(
                    history.HistoryLoginError, "history_account_unreviewed"):
                history.build_history_login_inputs(previous(), value, context())

    def test_account_must_match_prior_sdk_id_not_game_uid(self):
        for value in (999, history.EXPECTED_OWNER_UID):
            with self.assertRaisesRegex(history.HistoryLoginError, "history_owner_binding_invalid"):
                history.build_history_login_inputs(previous(), dict(account(), userId=value), context())
        for value in ("123456789012345", True, 1.0, 0, -1, 1 << 53):
            with self.assertRaisesRegex(history.HistoryLoginError, "history_numeric_id_unsupported"):
                history.build_history_login_inputs(previous(), dict(account(), userId=value), context())
        for value in ("01", "+1", "1e3", "1.0", " 1", "\u0661", "9007199254740992"):
            with self.assertRaisesRegex(history.HistoryLoginError, "history_numeric_id_unsupported"):
                history.build_history_login_inputs(dict(previous(), sdkuid=value), account(), context())

    def test_lossless_numeric_edges_and_device_conversion(self):
        for value in (1, history.MAX_SAFE_INTEGER):
            item = history.build_history_login_inputs(dict(previous(), sdkuid=str(value)),
                dict(account(), userId=value, deviceId=value), dict(context(), device_id=value))
            self.assertEqual(item.saved_device_id, str(value))
            self.assertIn(("&device_id=" + str(value)).encode(), history.build_history_form(item))

    def test_normal_numeric_saved_fields_are_lossless_decimal_strings(self):
        saved_device = 12345678901234
        item = history.build_history_login_inputs(dict(previous(), deviceid=str(saved_device)),
            dict(account(), deviceId=saved_device), dict(context(), device_id="fixture-separate-http-device"))
        self.assertEqual(item.saved_user_id, account()["userId"])
        self.assertEqual(item.sauth_sdkuid, str(account()["userId"]))
        self.assertEqual(item.saved_device_id, "12345678901234")
        raw = history.build_history_form(item)
        self.assertIn(b"user_id=123456789012345&", raw)
        self.assertIn(b"device_id=fixture-separate-http-device&", raw)
        self.assertNotIn(b".0", raw.split(b"&game_id=")[0])

    def test_non_lossless_numeric_device_types_are_rejected_before_io(self):
        with patch.object(history.urlrequest, "build_opener") as create:
            for value in (True, False, 1.0, 12345678901234.0, 0, -1, 1 << 53):
                for location in ("saved", "http"):
                    saved, data = account(), context()
                    if location == "saved":
                        saved["deviceId"] = value
                    else:
                        data["device_id"] = value
                    with self.subTest(value_type=type(value).__name__, location=location), \
                            self.assertRaises(history.HistoryLoginError) as raised:
                        item = history.build_history_login_inputs(previous(), saved, data)
                        history.login_history_once(item)
                    self.assertFalse(raised.exception.request_attempted)
        create.assert_not_called()

    def test_prior_context_must_be_observed_game_sdk_contract(self):
        for value in ({}, None, dict(previous(), gameid="different-game"), dict(previous(), platform="web")):
            with self.assertRaisesRegex(history.HistoryLoginError, "history_context_invalid"):
                history.build_history_login_inputs(value, account(), context())

    def test_unreviewed_context_and_wire_unsafe_values_are_rejected(self):
        cases = [dict(context(), game_id="x20naxx2gb"), dict(context(), game_id=502),
                 dict(context(), cp="p"), dict(context(), cv="1.4.2"), dict(context(), gv="3848703"),
                 dict(context(), app_channel="netease_global"), dict(context(), debug_mode=True),
                 dict(context(), lang=""), dict(context(), device_id=True), dict(context(), ci=None),
                 dict(context(), signature="fixture-secret"), dict(context(), package_name=None)]
        for value in cases:
            with self.subTest(keys=list(value)), self.assertRaises(history.HistoryLoginError):
                history.build_history_login_inputs(previous(), account(), value)
        for value in ("a+b", "a&b", "a%20b", "a b", "a\nb", "\ud800", "\u00e9", "x" * 32769, ""):
            with self.assertRaises(history.HistoryLoginError):
                history.build_history_login_inputs(previous(), dict(account(), refreshToken=value), context())

    def test_direct_input_objects_are_revalidated_before_io(self):
        base = inputs()
        cases = [None, replace(base, expected_owner_uid=1), replace(base, expected_owner_uid=True),
                 replace(base, saved_user_id=1), replace(base, refresh_token="a&b"),
                 replace(base, mpay_context=base.mpay_context + (("ci", "duplicate"),)),
                 replace(base, mpay_context=list(base.mpay_context)), replace(base, saved_device_id="")]
        with patch.object(history.urlrequest, "build_opener") as create:
            for value in cases:
                with self.assertRaises(history.HistoryLoginError) as raised:
                    history.login_history_once(value)
                self.assertFalse(raised.exception.request_attempted)
                self.assertIsNone(raised.exception.http_status)
        create.assert_not_called()


class HistoryResponseTest(unittest.TestCase):
    def test_success_returns_only_transient_unverified_tokens_not_sauth(self):
        item = inputs()
        candidate = history.validate_history_response(item, response(), http_status=200)
        self.assertEqual(candidate.access_token, "fixture-access-token")
        self.assertEqual(candidate.refresh_token, "fixture-replacement-token")
        self.assertEqual(candidate.sdkuid, previous()["sdkuid"])
        self.assertEqual(candidate.saved_device_id, account()["deviceId"])
        self.assertEqual(candidate.expected_owner_uid, history.EXPECTED_OWNER_UID)
        self.assertEqual(candidate.platform, "pc")
        self.assertFalse(candidate.owner_authentication_verified)
        self.assertFalse(candidate.automatic_renewal_verified)
        self.assertFalse(candidate.sauth_constructed)
        self.assertFalse(hasattr(candidate, "sauth"))
        self.assertNotIn("fixture-", repr(candidate))
        self.assertEqual(item.refresh_token, "fixture-saved-token")

    def test_reply_metadata_never_claims_server_identity_or_expiry_verification(self):
        raw = json.dumps({"code": 0, "user": {"id": 999, "token": "fixture-new", "login_token": "fixture-access"},
                          "owner_verified": True, "expires_in": 999999, "msg": "fixture-private"}).encode()
        candidate = history.validate_history_response(inputs(), raw, http_status=200)
        self.assertEqual(candidate.sdkuid, previous()["sdkuid"])
        self.assertFalse(candidate.owner_authentication_verified)
        self.assertFalse(hasattr(candidate, "expires_in"))

    def test_response_codes_and_shapes_are_strict_and_errors_redacted(self):
        cases = [(b'[]', "history_response_shape"), (b'{"code":true}', "history_response_shape"),
                 (b'{"code":"0"}', "history_response_shape"), (b'{"code":0.0}', "history_response_shape"),
                 (b'{"code":0}', "history_response_shape"), (b'{"code":0,"user":[]}', "history_response_shape"),
                 (b'{"code":1000,"msg":"fixture-private"}', "history_service_rejected")]
        for raw, expected in cases:
            with self.assertRaisesRegex(history.HistoryLoginError, expected) as raised:
                history.validate_history_response(inputs(), raw, http_status=200)
            self.assertNotIn("fixture-", str(raised.exception))

    def test_duplicate_nonfinite_encoding_and_response_bounds(self):
        for raw in (b'\xff', b'\xef\xbb\xbf{}', b'{', b'{"code":0,"code":1}',
                    b'{"code":0,"user":{"x":1,"x":2}}', b'{"code":0,"extra":NaN}'):
            with self.assertRaisesRegex(history.HistoryLoginError, "history_response_json"):
                history.validate_history_response(inputs(), raw, http_status=200)
        for raw in (b'', b'x' * (history.MAX_RESPONSE_BYTES + 1), "{}", bytearray(b'{}')):
            with self.assertRaisesRegex(history.HistoryLoginError, "history_response_bound"):
                history.validate_history_response(inputs(), raw, http_status=200)

    def test_both_tokens_required_bounded_and_rotation_is_preserved(self):
        for field in ("token", "login_token"):
            for value in (None, True, 9, "", " ", "\ud800", "x" * 32769):
                user = {"token": "fixture-renewal", "login_token": "fixture-access", field: value}
                with self.assertRaisesRegex(history.HistoryLoginError, "history_response_token_invalid"):
                    history.validate_history_response(inputs(), json.dumps({"code": 0, "user": user}).encode(), http_status=200)
        for token in ("fixture-saved-token", "fixture-new-token"):
            raw = json.dumps({"code": 0, "user": {"token": token, "login_token": "fixture-access"}}).encode()
            self.assertEqual(history.validate_history_response(inputs(), raw, http_status=200).refresh_token, token)

    def test_http_status_and_error_metadata_are_typed_and_bounded(self):
        for status in (199, 301, 403, 500):
            with self.assertRaises(history.HistoryLoginError) as raised:
                history.validate_history_response(inputs(), b'fixture-private', http_status=status)
            self.assertEqual(raised.exception.http_status, status)
            self.assertFalse(raised.exception.request_attempted)
        for status in (True, "200", 99, 600, None, []):
            error = history.HistoryLoginError("fixture-secret", request_attempted="fixture-secret", http_status=status)
            self.assertEqual(error.code, "history_request_failed")
            self.assertIsNone(error.http_status)
            self.assertIsNone(error.request_attempted)
            self.assertNotIn("fixture-", repr(error))

    def test_service_rejection_retains_code_without_a_token_invalid_or_expired_claim(self):
        for code in (-2147483648, -1, 1, 429, 1000, 2147483647):
            raw = json.dumps({"code": code, "msg": "fixture-private token expired, retry now"}).encode()
            with self.subTest(code=code), self.assertRaises(history.HistoryLoginError) as raised:
                history.validate_history_response(inputs(), raw, http_status=200)
            error = raised.exception
            self.assertEqual(error.code, "history_service_rejected")
            self.assertEqual(error.application_code, code)
            self.assertEqual(error.http_status, 200)
            self.assertFalse(error.request_attempted)
            self.assertEqual(str(error), "history_service_rejected")
            self.assertNotIn("expired", repr(error))
            self.assertNotIn("reauthentication", repr(error))

    def test_application_code_metadata_is_only_a_bounded_exact_integer(self):
        for code in (-2147483648, -1, 0, 2147483647):
            self.assertEqual(history.HistoryLoginError("history_service_rejected", application_code=code).application_code, code)
        for code in (None, True, False, "1000", 1000.0, -2147483649, 2147483648, [], {}):
            with self.subTest(code_type=type(code).__name__):
                error = history.HistoryLoginError("history_service_rejected", application_code=code)
                self.assertIsNone(error.application_code)
        for code in (-2147483649, 2147483648):
            with self.assertRaises(history.HistoryLoginError) as raised:
                history.validate_history_response(inputs(), json.dumps({"code": code}).encode(), http_status=200)
            self.assertEqual(raised.exception.code, "history_service_rejected")
            self.assertIsNone(raised.exception.application_code)

    def test_pure_helpers_never_open_files_network_or_load_desktop_credentials(self):
        targets = ("builtins.open", "pathlib.Path.open", "socket.create_connection",
                   "urllib.request.build_opener", "game_login_probe.load_own_login_inputs",
                   "game_login_probe.check_login", "game_transport_probe.load_public_configuration")
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(target, side_effect=AssertionError("unexpected dependency")))
                      for target in targets]
            item = inputs()
            history.build_history_form(item)
            history.validate_history_response(item, response(), http_status=200)
        for guard in guards:
            guard.assert_not_called()


class HistoryHTTPTest(unittest.TestCase):
    def test_one_exact_post_no_proxy_auth_cookie_redirect_or_fallback(self):
        reply = FakeReply(response())
        opener = Mock()
        opener.open.return_value = reply
        with patch.object(history.urlrequest, "build_opener", return_value=opener) as create, \
                patch.dict("os.environ", {"HTTPS_PROXY": "https://fixture-proxy.invalid", "NETRC": "fixture-netrc"}):
            candidate = history.login_history_once(inputs())
        self.assertFalse(candidate.owner_authentication_verified)
        self.assertEqual(opener.open.call_count, 1)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, history.HISTORY_ENDPOINT)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.data, history.build_history_form(inputs()))
        self.assertEqual(dict(request.header_items()), {
            "Accept": "application/json, text/plain, */*",
            "Content-type": "application/x-www-form-urlencoded; charset=utf-8",
        })
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 10})
        handlers = create.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], history._NoRedirect)
        self.assertIsInstance(handlers[2], history.urlrequest.HTTPSHandler)
        self.assertTrue(reply.closed)
        self.assertEqual(reply.read_sizes, [history.MAX_RESPONSE_BYTES + 1])

    def test_actual_opener_has_no_environment_proxy_cookie_or_auth_handlers(self):
        with patch("urllib.request.getproxies", side_effect=AssertionError("environment proxy lookup")):
            opener = history.urlrequest.build_opener(history.urlrequest.ProxyHandler({}),
                                                    history._NoRedirect(), history.urlrequest.HTTPSHandler())
        self.assertFalse(any(isinstance(handler, (history.urlrequest.HTTPCookieProcessor,
            history.urlrequest.HTTPBasicAuthHandler, history.urlrequest.ProxyBasicAuthHandler)) for handler in opener.handlers))
        redirect = next(handler for handler in opener.handlers if isinstance(handler, history._NoRedirect))
        self.assertIsNone(redirect.redirect_request(None, None, 302, "fixture-message", {}, "https://fixture.invalid"))

    def test_http_errors_never_read_body_or_retry(self):
        for status in (301, 302, 307, 400, 403, 500):
            body = FakeReply(b"fixture-private" * 10000, status)
            error = history.urlerror.HTTPError(history.HISTORY_ENDPOINT, status, "fixture-private", Message(), body)
            opener = Mock()
            opener.open.side_effect = error
            with patch.object(history.urlrequest, "build_opener", return_value=opener), \
                    self.assertRaises(history.HistoryLoginError) as raised:
                history.login_history_once(inputs())
            self.assertEqual(raised.exception.code, "history_http_error")
            self.assertEqual(raised.exception.http_status, status)
            self.assertTrue(raised.exception.request_attempted)
            self.assertEqual(body.read_sizes, [])
            self.assertTrue(body.closed)
            self.assertEqual(opener.open.call_count, 1)
            self.assertNotIn("fixture-", str(raised.exception))

    def test_non2xx_returned_response_also_closed_without_read(self):
        reply = FakeReply(b"fixture-private", 400)
        opener = Mock()
        opener.open.return_value = reply
        with patch.object(history.urlrequest, "build_opener", return_value=opener), \
                self.assertRaises(history.HistoryLoginError) as raised:
            history.login_history_once(inputs())
        self.assertEqual(raised.exception.http_status, 400)
        self.assertEqual(reply.read_sizes, [])
        self.assertTrue(reply.closed)

    def test_network_failure_is_redacted_and_attempt_count_is_honest(self):
        opener = Mock()
        opener.open.side_effect = history.urlerror.URLError("fixture-token")
        with patch.object(history.urlrequest, "build_opener", return_value=opener), \
                self.assertRaises(history.HistoryLoginError) as raised:
            history.login_history_once(inputs())
        self.assertEqual(opener.open.call_count, 1)
        self.assertTrue(raised.exception.request_attempted)
        self.assertIsNone(raised.exception.http_status)
        self.assertNotIn("fixture-", str(raised.exception))
        with patch.object(history.urlrequest, "build_opener", side_effect=RuntimeError("fixture-token")), \
                self.assertRaises(history.HistoryLoginError) as raised:
            history.login_history_once(inputs())
        self.assertFalse(raised.exception.request_attempted)

    def test_bound_parser_and_stream_failures_do_not_retry_or_leak(self):
        for raw, code in ((b"x" * (history.MAX_RESPONSE_BYTES + 100), "history_response_bound"),
                          (b'{', "history_response_json"),
                          (b'{"code":1000,"msg":"fixture-private"}', "history_service_rejected")):
            reply = FakeReply(raw)
            opener = Mock()
            opener.open.return_value = reply
            with patch.object(history.urlrequest, "build_opener", return_value=opener), \
                    self.assertRaises(history.HistoryLoginError) as raised:
                history.login_history_once(inputs())
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(raised.exception.http_status, 200)
            self.assertEqual(raised.exception.application_code, 1000 if code == "history_service_rejected" else None)
            self.assertTrue(raised.exception.request_attempted)
            self.assertTrue(reply.closed)
            self.assertEqual(opener.open.call_count, 1)
            self.assertEqual(reply.read_sizes, [history.MAX_RESPONSE_BYTES + 1])
        reply = FakeReply(b"")
        opener.open.return_value = reply
        with patch.object(history.urlrequest, "build_opener", return_value=opener), \
                patch.object(reply, "read", side_effect=RuntimeError("fixture-stream-secret")), \
                self.assertRaises(history.HistoryLoginError) as raised:
            history.login_history_once(inputs())
        self.assertEqual(raised.exception.code, "history_request_failed")
        self.assertEqual(raised.exception.http_status, 200)
        self.assertTrue(reply.closed)


if __name__ == "__main__":
    unittest.main()
