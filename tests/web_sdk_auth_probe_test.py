"""Synthetic SDK inputs and fake sockets only; no real sessions or network."""
import copy
import json
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import game_login_probe as login
import web_sdk_auth_probe as probe


def fixture():
    return dict(probe.SDK_CONSTANTS, sdkuid="fixture-sdk-user",
                sessionid="fixture-session-secret", deviceid="fixture-device",
                udid="fixture-udid", client_login_sn="fixture-login-number")


def auth_reply(uid=probe.EXPECTED_OWNER_UID):
    return {"err_code": 0, "auth_hostid": 13001, "auth_sn": "fixture-auth-secret",
            "zone_list": [{"role_uid": uid, "role_inited": 1,
                           "gate_ip": "8.8.8.8", "gate_port": 10002}]}


class FakeSocket:
    def __init__(self, incoming):
        self.incoming, self.sent, self.closed = incoming, [], False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def settimeout(self, timeout):
        pass

    def recv(self, count):
        value, self.incoming = self.incoming[:count], self.incoming[count:]
        return value

    def sendall(self, value):
        self.sent.append(value)


class WebSdkAuthProbeTest(unittest.TestCase):
    def test_actual_sdk_values_are_preserved_without_native_metadata(self):
        sauth = fixture()
        inputs = probe.build_web_login_inputs(sauth)
        self.assertEqual(json.loads(inputs.auth["sauth_json"]), sauth)
        self.assertEqual(set(inputs.auth), set(probe.DIRECT_FIELDS) | {"sauth_json"})
        self.assertEqual(len(inputs.auth), 8)
        for target, source in probe.DIRECT_FIELDS.items():
            self.assertEqual(inputs.auth[target], sauth[source])
        roundtrip = login.decode_body(login.AUTH_REQUEST, login.encode_body(login.AUTH_REQUEST, inputs.auth))
        self.assertEqual(roundtrip, inputs.auth)
        for name in ("os_name", "client_version", "engine_ver", "ip", "ip_info",
                     "device_height", "device_width", "info", "is_root", "is_emulator"):
            self.assertNotIn(name, roundtrip)
        self.assertNotIn("fixture-session-secret", repr(inputs))

    def test_refresh_token_is_discarded_without_mutating_caller(self):
        sauth = dict(fixture(), refresh_token="fixture-refresh-secret")
        before = copy.deepcopy(sauth)
        inputs = probe.build_web_login_inputs(sauth)
        self.assertEqual(json.loads(inputs.auth["sauth_json"]), fixture())
        self.assertNotIn("fixture-refresh-secret", json.dumps(inputs.auth))
        self.assertEqual(sauth, before)

    def test_existing_native_builder_still_rejects_browser_bundle(self):
        with self.assertRaises(login.LoginError):
            login.build_auth(fixture(), {"SDC_LOG_OS_NAME": "pc", "DEVICE_DESKTOP_RESOLUTION": "1080*1920"})

    def test_explicit_reviewed_version_adds_only_two_outer_fields(self):
        sauth = dict(fixture(), refresh_token="fixture-refresh-secret")
        before = copy.deepcopy(sauth)
        minimal = probe.build_web_login_inputs(sauth, client_version=None)
        versioned = probe.build_web_login_inputs(sauth, client_version=login.CLIENT_VERSION)
        self.assertEqual(versioned.auth, dict(minimal.auth, client_version="3825464", engine_ver="3825464"))
        self.assertEqual(json.loads(versioned.auth["sauth_json"]), fixture())
        self.assertNotIn("client_full_version", versioned.auth)
        self.assertNotIn("fixture-refresh-secret", json.dumps(versioned.auth))
        self.assertEqual(sauth, before)

    def test_unreviewed_versions_stop_before_network(self):
        for value in ("", "3825465", "1.1.3825464.3825464", 3825464, True, [], {}, "fixture-secret"):
            with self.subTest(value_type=type(value).__name__), patch.object(login, "check_login") as check:
                report = probe.run_auth_once(fixture(), None, None, client_version=value)
                self.assertEqual(report["outcome"], "web_sdk_version_unreviewed")
                self.assertFalse(report["credentials_sent"])
                self.assertEqual(report["connections_attempted"], 0)
                self.assertNotIn("fixture-secret", json.dumps(report))
                check.assert_not_called()

    def test_reviewed_pc_selector_is_separate_from_unchanged_sauth(self):
        sauth = fixture()
        before = copy.deepcopy(sauth)
        inputs = probe.build_web_login_inputs(sauth, client_version=login.CLIENT_VERSION, os_name="pc")
        self.assertEqual(inputs.auth["os_name"], "pc")
        self.assertEqual(set(inputs.auth), set(probe.DIRECT_FIELDS) | {
            "sauth_json", "client_version", "engine_ver", "os_name"})
        self.assertEqual(json.loads(inputs.auth["sauth_json"]), sauth)
        self.assertEqual(sauth, before)

    def test_unreviewed_os_names_stop_before_network(self):
        for value in ("", "windows", "web", True, [], "fixture-secret"):
            with self.subTest(value_type=type(value).__name__), patch.object(login, "check_login") as check:
                report = probe.run_auth_once(fixture(), None, None, os_name=value)
                self.assertEqual(report["outcome"], "web_sdk_platform_unreviewed")
                self.assertFalse(report["credentials_sent"])
                self.assertNotIn("fixture-secret", json.dumps(report))
                check.assert_not_called()

    def test_missing_or_extra_sdk_fields_stop_before_network(self):
        cases = [None, [], dict(fixture(), gas_token=""), dict(fixture(), ip="")]
        for key in probe.SDK_FIELDS:
            value = fixture()
            del value[key]
            cases.append(value)
        for value in cases:
            with self.subTest(value_type=type(value).__name__), patch.object(login, "check_login") as check:
                report = probe.run_auth_once(value, None, None)
                self.assertEqual(report["outcome"], "web_sdk_shape")
                self.assertFalse(report["credentials_sent"])
                check.assert_not_called()

    def test_contract_mismatch_is_not_relabelled_to_pc(self):
        for key in probe.SDK_CONSTANTS:
            with self.subTest(key=key), patch.object(login, "check_login") as check:
                value = fixture()
                value[key] = "different"
                report = probe.run_auth_once(value, None, None)
                self.assertEqual(report["outcome"], "web_sdk_contract_mismatch")
                check.assert_not_called()

    def test_empty_identity_bad_types_and_bounds_are_rejected(self):
        cases = []
        for key in ("sdkuid", "sessionid", "deviceid"):
            for invalid in ("", " "):
                cases.append((dict(fixture(), **{key: invalid}), "web_sdk_identity_missing"))
        for key in probe.SDK_FIELDS | {"refresh_token"}:
            cases.append((dict(fixture(), **{key: None}), "web_sdk_field_type"))
            cases.append((dict(fixture(), **{key: "x" * (probe.FIELD_LIMITS[key] + 1)}), "web_sdk_field_bound"))
        cases.append((dict(fixture(), sessionid="\ud800"), "web_sdk_field_type"))
        for value, outcome in cases:
            with self.subTest(outcome=outcome), patch.object(login, "check_login") as check:
                report = probe.run_auth_once(value, None, None)
                self.assertEqual(report["outcome"], outcome)
                check.assert_not_called()

    def test_escape_expansion_cannot_make_oversized_wire_message(self):
        with patch.object(login, "check_login") as check:
            report = probe.run_auth_once(dict(fixture(), sessionid="\0" * 12000), None, None)
        self.assertEqual(report["outcome"], "web_sdk_body_bound")
        check.assert_not_called()

    def _run_wire(self, reply=None, *, args=b"", versions=None, client_version=None):
        def record(sub, body):
            return struct.pack("<IHB", len(body) + 7, 1, sub) + body

        prepare = login.encode_body(login.PREPARE, {
            "version_list": versions or [], "args": args, "is_test_branch": 0,
            "timestamp": 1700000000, "timezone": 0,
        })
        wire = bytes(28) + record(101, prepare)
        if reply is not None:
            wire += record(102, login.encode_body(login.AUTH_REPLY, reply))
        sock = FakeSocket(wire)
        connect = Mock(return_value=sock)
        transport = SimpleNamespace(acknowledgement=b"ack", encrypt=lambda value: value, decrypt=lambda value: value)
        real_check = login.check_login

        def check(configuration, server, **kwargs):
            self.assertEqual(kwargs["expected_uid"], 1370598422)
            self.assertIs(kwargs["enter"], False)
            self.assertIsNone(kwargs["after_login"])
            return real_check(configuration, server, connect=connect, **kwargs)

        with patch.object(login, "check_login", side_effect=check) as dispatch, \
                patch.object(login, "prepare_transport", return_value=transport), \
                patch.object(login.time, "time", return_value=1700000000), \
                patch.object(login, "check_gateway") as gateway, \
                patch.object(login, "load_own_login_inputs") as desktop:
            report = probe.run_auth_once(fixture(), None, login.LoginServer("8.8.8.8", 13001, (10001,)),
                                         client_version=client_version)
        dispatch.assert_called_once()
        connect.assert_called_once()
        gateway.assert_not_called()
        desktop.assert_not_called()
        self.assertTrue(sock.closed)
        self.assertFalse(report["game_login_verified"])
        self.assertFalse(report["career_verified"])
        self.assertFalse(report["secrets_saved"])
        self.assertNotIn("fixture-session-secret", json.dumps(report))
        self.assertNotIn("fixture-auth-secret", json.dumps(report))
        return sock, report

    def test_one_auth_connection_exact_owner_bound_and_closed_without_enter(self):
        sock, report = self._run_wire(auth_reply())
        self.assertEqual(report["outcome"], "own_existing_role_verified")
        self.assertTrue(report["auth_service_accepted"])
        self.assertTrue(report["own_role_verified"])
        self.assertEqual(report["connections_attempted"], 1)
        self.assertEqual(len(sock.sent), 2)
        self.assertEqual(struct.unpack_from("<HB", sock.sent[1], 4), (1, 1))

    def test_wrong_uid_is_not_success(self):
        sock, report = self._run_wire(auth_reply(1822797559))
        self.assertEqual(report["outcome"], "expected_role_missing_or_ambiguous")
        self.assertTrue(report["auth_service_accepted"])
        self.assertFalse(report["own_role_verified"])
        self.assertEqual(len(sock.sent), 2)

    def test_versioned_wire_and_1911_rejection_close_without_retry(self):
        sock, report = self._run_wire({"err_code": 1911}, client_version=login.CLIENT_VERSION)
        wire_auth = login.decode_body(login.AUTH_REQUEST, sock.sent[1][7:])
        self.assertEqual(wire_auth["client_version"], "3825464")
        self.assertEqual(wire_auth["engine_ver"], "3825464")
        self.assertEqual(set(wire_auth), set(probe.DIRECT_FIELDS) | {"sauth_json", "client_version", "engine_ver"})
        self.assertEqual(json.loads(wire_auth["sauth_json"]), fixture())
        self.assertEqual(report["outcome"], "server_rejected_authentication")
        self.assertEqual(report["server_error_code"], 1911)
        self.assertFalse(report["auth_service_accepted"])
        self.assertFalse(report["own_role_verified"])

    def test_current_reviewed_version_preserves_wire_and_owner_binding(self):
        sock, report = self._run_wire(auth_reply(), client_version="3848703")
        wire_auth = login.decode_body(login.AUTH_REQUEST, sock.sent[1][7:])
        self.assertEqual(wire_auth["client_version"], "3848703")
        self.assertEqual(wire_auth["engine_ver"], "3848703")
        self.assertEqual(json.loads(wire_auth["sauth_json"]), fixture())
        self.assertEqual(report["outcome"], "own_existing_role_verified")
        self.assertTrue(report["own_role_verified"])
        self.assertEqual(report["connections_attempted"], 1)
        self.assertEqual(len(sock.sent), 2)

    def test_prepare_requirements_stop_before_credentials(self):
        for args, versions in ((b"do-not-execute", []), (b"", [{"platform": "pc", "version": "required"}])):
            with self.subTest(args_present=bool(args)):
                sock, report = self._run_wire(args=args, versions=versions)
                self.assertEqual(report["outcome"], "additional_prepare_requirements")
                self.assertFalse(report["credentials_sent"])
                self.assertEqual(sock.sent, [b"ack"])

    def test_rejection_is_redacted_and_does_not_retry(self):
        sock, report = self._run_wire({"err_code": 253, "sdk_login_str": json.dumps({
            "code": 405, "subcode": 17, "access_token": "fixture-sdk-secret",
            "channel_msg": {"msg": "fixture-sdk-secret session expired"},
        })})
        self.assertEqual(report["outcome"], "server_rejected_authentication")
        self.assertEqual(report["server_error_code"], 253)
        self.assertEqual(report["sdk_code"], 405)
        self.assertEqual(report["sdk_subcode"], 17)
        self.assertTrue(report["sdk_message_mentions_expiry"])
        self.assertNotIn("fixture-sdk-secret", json.dumps(report))
        self.assertEqual(len(sock.sent), 2)

    def test_unexpected_exception_is_redacted_and_transmission_unknown(self):
        for error in (login.LoginError("fixture-secret"), RuntimeError("fixture-secret")):
            with self.subTest(error_type=type(error).__name__), patch.object(login, "check_login", side_effect=error) as check:
                report = probe.run_auth_once(fixture(), None, None)
                self.assertEqual(report["outcome"], "web_auth_probe_failed")
                self.assertIsNone(report["credentials_sent"])
                self.assertIsNone(report["connections_attempted"])
                self.assertNotIn("fixture-secret", json.dumps(report))
                check.assert_called_once()

    def test_report_projection_rejects_free_text_and_unknown_fields(self):
        for raw in ({"outcome": "fixture-secret"}, {"outcome": ["fixture-secret"]}, "fixture-secret"):
            with self.subTest(raw_type=type(raw).__name__), patch.object(login, "check_login", return_value=raw):
                report = probe.run_auth_once(fixture(), None, None)
                self.assertEqual(report["outcome"], "web_auth_report_unrecognized")
                self.assertNotIn("fixture-secret", json.dumps(report))
        raw = {"outcome": "server_rejected_authentication", "message": "fixture-secret",
               "sdk_code": "fixture-secret", "credentials_sent": "fixture-secret",
               "server_error_code": True, "own_role_verified": 1,
               "game_login_verified": True, "secrets_saved": True}
        with patch.object(login, "check_login", return_value=raw):
            report = probe.run_auth_once(fixture(), None, None)
        self.assertNotIn("fixture-secret", json.dumps(report))
        self.assertNotIn("message", report)
        self.assertNotIn("sdk_code", report)
        self.assertNotIn("server_error_code", report)
        self.assertFalse(report["own_role_verified"])
        self.assertFalse(report["game_login_verified"])
        self.assertFalse(report["secrets_saved"])


if __name__ == "__main__":
    unittest.main()
