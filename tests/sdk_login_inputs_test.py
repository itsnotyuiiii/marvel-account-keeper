"""Synthetic SDK transactions only; no real logs, accounts, or network."""
import base64
import copy
import datetime as dt
import json
import unittest

import game_login_probe as login


STAMP = "2026-09-08 04:19:30"
NOW = dt.datetime(2026, 9, 8, 5)
PROPS = {"SDC_LOG_OS_NAME": "pc", "DEVICE_DESKTOP_RESOLUTION": "2880*1800"}


def fixture():
    bundle = {key: "fixture" for key in ("sdk_version", "deviceid", "ip", "app_channel", "aim_info")}
    bundle.update(gameid="x20naxx2gb", platform="pc", login_channel="fixture-channel",
                  sdkuid="pre-auth-id", udid="fixture-device", gas_token="",
                  sessionid="pre-auth-session", get_access_token="1")
    inner = {"sdkuid": "post-auth-id", "aid": "123456",
             "chl_sdk_json": {"sdkuid": "post-auth-id", "aid": "123456",
                              "gameid": bundle["gameid"], "login_channel": bundle["login_channel"],
                              "udid": bundle["udid"]},
             "extra": {"channel_raw_info": json.dumps({"token": "new-channel-token",
                                                        "login_token": "new-login-token"})},
             "oauth2": {"access_token": "oauth-not-gas", "refresh_token": "never-send-refresh"}}
    response = {"code": 200, "status": "ok", "sdkuid": inner["sdkuid"], "aid": 123456,
                "data": {"app_channel": bundle["app_channel"]}}
    return bundle, response, inner


def pack(response, inner):
    return dict(response, unisdk_login_json=base64.b64encode(json.dumps(inner).encode()).decode())


class SdkLoginInputsTest(unittest.TestCase):
    def test_normal_response_updates_id_and_prefers_new_login_token(self):
        bundle, response, inner = fixture()
        before = copy.deepcopy(bundle)
        final = login.finalize_sdk_bundle(bundle, pack(response, inner))
        self.assertEqual(final["sdkuid"], "post-auth-id")
        self.assertEqual(final["sessionid"], "new-login-token")
        self.assertEqual(final["gas_token"], "")
        self.assertEqual(final["get_access_token"], "1")
        self.assertEqual(bundle, before)
        self.assertNotIn("oauth-not-gas", json.dumps(final))
        self.assertNotIn("never-send-refresh", json.dumps(final))

    def test_channel_token_fallback_when_login_token_empty(self):
        bundle, response, inner = fixture()
        inner["extra"]["channel_raw_info"] = json.dumps({"token": "new-channel-token", "login_token": ""})
        final = login.finalize_sdk_bundle(bundle, pack(response, inner))
        self.assertEqual(final["sessionid"], "new-channel-token")

    def test_missing_or_wrongly_typed_session_stops(self):
        for raw in ({}, {"token": []}, {"token": "x", "login_token": {}}):
            bundle, response, inner = fixture()
            inner["extra"]["channel_raw_info"] = json.dumps(raw)
            with self.assertRaises(login.LoginError):
                login.finalize_sdk_bundle(bundle, pack(response, inner))

    def test_sdk_token_only_fills_empty_gas_token(self):
        for old, expected in (("", "sdk-only-token"), ("existing-token", "existing-token")):
            bundle, response, inner = fixture()
            bundle["gas_token"] = old
            inner["sdk_token"] = "sdk-only-token"
            final = login.finalize_sdk_bundle(bundle, pack(response, inner))
            self.assertEqual(final["gas_token"], expected)

    def test_mismatched_game_channel_device_or_identity_rejected(self):
        for key in ("gameid", "login_channel", "udid", "sdkuid", "aid"):
            bundle, response, inner = fixture()
            inner["chl_sdk_json"][key] = "other-account"
            with self.assertRaisesRegex(login.LoginError, "sdk_response_binding_mismatch"):
                login.finalize_sdk_bundle(bundle, pack(response, inner))

    def test_outer_response_id_and_application_must_agree(self):
        for key in ("sdkuid", "aid", "app_channel"):
            bundle, response, inner = fixture()
            (response["data"] if key == "app_channel" else response)[key] = "other-account"
            with self.assertRaises(login.LoginError):
                login.finalize_sdk_bundle(bundle, pack(response, inner))

    def test_unsuccessful_response_is_not_treated_as_login(self):
        for code, status in ((502, "ok"), (200, "error"), (True, "ok"), ("200", "ok")):
            bundle, response, inner = fixture()
            response.update(code=code, status=status)
            with self.assertRaisesRegex(login.LoginError, "sdk_response_not_successful"):
                login.finalize_sdk_bundle(bundle, pack(response, inner))

    def test_base64_and_duplicate_json_errors_do_not_include_source(self):
        bundle, response, inner = fixture()
        for raw in ("secret-invalid-base64!", base64.b64encode(b'{"sdkuid":"secret","sdkuid":"other"}').decode()):
            response["unisdk_login_json"] = raw
            with self.assertRaises(login.LoginError) as raised:
                login.finalize_sdk_bundle(bundle, response)
            self.assertEqual(str(raised.exception), "sdk_response_shape")

    def test_response_can_precede_telemetry_copy_of_request(self):
        bundle, response, inner = fixture()
        records = [(STAMP, PROPS), (STAMP, pack(response, inner)), (STAMP, bundle)]
        inputs = login.select_sdk_login(records, now=NOW)
        self.assertEqual(inputs.auth["sdkuid"], "post-auth-id")
        self.assertEqual(json.loads(inputs.auth["sauth_json"])["sessionid"], "new-login-token")
        self.assertNotIn("new-login-token", repr(inputs))
        self.assertEqual(inputs.source_timestamp, STAMP)

    def test_identical_duplicate_log_records_are_not_ambiguous(self):
        bundle, response, inner = fixture()
        response = pack(response, inner)
        records = [(STAMP, PROPS), (STAMP, response), (STAMP, bundle), (STAMP, dict(bundle)), (STAMP, dict(response))]
        self.assertEqual(login.select_sdk_login(records, now=NOW).auth["sdkuid"], "post-auth-id")

    def test_distinct_same_time_accounts_are_ambiguous(self):
        bundle, response, inner = fixture()
        records = [(STAMP, PROPS), (STAMP, pack(response, inner)), (STAMP, bundle),
                   (STAMP, dict(bundle, sdkuid="another-account"))]
        with self.assertRaisesRegex(login.LoginError, "sdk_transaction_missing_or_ambiguous"):
            login.select_sdk_login(records, now=NOW)

    def test_newer_incomplete_login_never_falls_back_to_previous_account(self):
        bundle, response, inner = fixture()
        records = [(STAMP, PROPS), (STAMP, pack(response, inner)), (STAMP, bundle),
                   ("2026-09-08 04:20:00", dict(bundle, sdkuid="another-account"))]
        with self.assertRaisesRegex(login.LoginError, "sdk_transaction_missing_or_ambiguous"):
            login.select_sdk_login(records, now=NOW)

    def test_response_from_different_timestamp_is_not_combined(self):
        bundle, response, inner = fixture()
        records = [(STAMP, PROPS), (STAMP, bundle), ("2026-09-08 04:20:00", pack(response, inner))]
        with self.assertRaises(login.LoginError):
            login.select_sdk_login(records, now=NOW)

    def test_old_or_far_future_sources_rejected(self):
        bundle, response, inner = fixture()
        records = [(STAMP, PROPS), (STAMP, bundle), (STAMP, pack(response, inner))]
        for now in (NOW + dt.timedelta(days=3), NOW - dt.timedelta(days=3)):
            with self.assertRaisesRegex(login.LoginError, "own_sdk_login_too_old"):
                login.select_sdk_login(records, now=now)


if __name__ == "__main__":
    unittest.main()
