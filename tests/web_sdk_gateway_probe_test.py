"""Synthetic LOGIN/gateway streams; no real accounts, listeners or network."""
import copy
from contextlib import ExitStack
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import game_login_probe as login
import web_sdk_auth_probe as probe
from web_sdk_auth_probe_test import FakeSocket, auth_reply, fixture


CURRENT_VERSION = "3848703"


def record(main, sub, body):
    return struct.pack("<IHB", len(body) + 7, main, sub) + body


def gateway_reply(uid=probe.EXPECTED_OWNER_UID):
    return {"errcode": 0, "relogin": 0,
            "info": {"uid": uid, "client_ver": CURRENT_VERSION, "login_key": "fixture-login-secret"}}


class WebSdkGatewayProbeTest(unittest.TestCase):
    def run_wire(self, *, auth=None, gateway=None, prefix=b"", expected_connections=2,
                 prepare_args=b"", gateway_sub=103):
        auth = auth_reply() if auth is None else auth
        gateway = gateway_reply() if gateway is None else gateway
        prepare = login.encode_body(login.PREPARE, {
            "version_list": [], "args": prepare_args, "is_test_branch": 0,
            "timestamp": 1700000000, "timezone": 0})
        first = FakeSocket(bytes(28) + record(1, 101, prepare)
                           + record(1, 102, login.encode_body(login.AUTH_REPLY, auth)))
        second = FakeSocket(bytes(28) + prefix + record(1, gateway_sub, login.encode_body(login.LOGIN_REPLY, gateway)))
        connect = Mock(side_effect=[first, second])
        transport = SimpleNamespace(acknowledgement=b"ack", encrypt=lambda value: value, decrypt=lambda value: value)
        real_check = login.check_login
        sauth = fixture()
        before = copy.deepcopy(sauth)

        def check(configuration, server, **kwargs):
            self.assertEqual(kwargs["expected_uid"], 1370598422)
            self.assertIs(kwargs["enter"], True)
            self.assertIsNone(kwargs["after_login"])
            self.assertEqual(kwargs["inputs"].auth["client_version"], CURRENT_VERSION)
            self.assertEqual(kwargs["inputs"].auth["engine_ver"], CURRENT_VERSION)
            self.assertEqual(kwargs["inputs"].auth["os_name"], "pc")
            return real_check(configuration, server, connect=connect, **kwargs)

        forbidden = (
            "game_login_probe.load_own_login_inputs", "game_login_probe.advertised_server",
            "game_login_probe.load_config_index", "game_transport_probe.load_public_configuration",
            "game_career_probe.load_career_methods", "game_career_probe.read_one_career",
            "game_career_probe.read_one_match_summary", "pathlib.Path.open", "requests.get", "requests.post",
        )
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(target, side_effect=AssertionError("unexpected dependency")))
                      for target in forbidden]
            dispatch = stack.enter_context(patch.object(login, "check_login", side_effect=check))
            stack.enter_context(patch.object(login, "prepare_transport", return_value=transport))
            stack.enter_context(patch.object(login.time, "time", return_value=1700000000))
            report = probe.run_gateway_once(sauth, None, login.LoginServer("8.8.8.8", 13001, (10001,)),
                                           client_version=CURRENT_VERSION)
        dispatch.assert_called_once()
        self.assertEqual(connect.call_count, expected_connections)
        self.assertEqual(sauth, before)
        self.assertTrue(first.closed)
        if expected_connections == 2:
            self.assertTrue(second.closed)
        else:
            self.assertFalse(second.sent)
        for guard in guards:
            guard.assert_not_called()
        self.assertFalse(report["career_verified"])
        self.assertFalse(report["secrets_saved"])
        self.assertFalse(report["remote_code_executed"])
        for forbidden_value in ("fixture-session-secret", "fixture-auth-secret", "fixture-login-secret",
                                "fixture-private-reason", "never execute", "8.8.8.8"):
            self.assertNotIn(forbidden_value, json.dumps(report))
        return first, second, report

    def test_success_is_two_closed_connections_exact_auth_then_owner_enter(self):
        first, second, report = self.run_wire()
        self.assertEqual(report["outcome"], "own_gateway_login_verified")
        self.assertTrue(report["game_login_verified"])
        self.assertTrue(report["auth_service_accepted"])
        self.assertTrue(report["own_role_verified"])
        self.assertEqual(report["connections_attempted"], 1)
        self.assertEqual(report["gateway"]["connections_attempted"], 1)
        self.assertTrue(report["gateway"]["game_login_verified"])
        self.assertTrue(report["gateway"]["login_result_uid_matches"])
        self.assertEqual(len(first.sent), 2)
        self.assertEqual(len(second.sent), 2)
        self.assertEqual(struct.unpack_from("<HB", first.sent[1], 4), (1, 1))
        self.assertEqual(struct.unpack_from("<HB", second.sent[1], 4), (1, 3))
        entered = login.decode_body(login.ENTER_REQUEST, second.sent[1][7:])
        self.assertEqual(entered, {"auth_hostid": 13001, "auth_sn": "fixture-auth-secret",
                                  "device_id": "", "basic_info": "", "uid": 1370598422})

    def test_auth_rejection_never_opens_gateway(self):
        _, second, report = self.run_wire(auth={"err_code": 1911}, expected_connections=1)
        self.assertEqual(report["outcome"], "server_rejected_authentication")
        self.assertEqual(report["server_error_code"], 1911)
        self.assertFalse(report["game_login_verified"])
        self.assertNotIn("gateway", report)
        self.assertFalse(second.sent)

    def test_auth_owner_mismatch_never_opens_gateway(self):
        _, _, report = self.run_wire(auth=auth_reply(1822797559), expected_connections=1)
        self.assertEqual(report["outcome"], "expected_role_missing_or_ambiguous")
        self.assertTrue(report["auth_service_accepted"])
        self.assertFalse(report["own_role_verified"])
        self.assertFalse(report["game_login_verified"])

    def test_prepare_requirements_prevent_auth_and_gateway(self):
        first, second, report = self.run_wire(prepare_args=b"do-not-execute", expected_connections=1)
        self.assertEqual(report["outcome"], "additional_prepare_requirements")
        self.assertFalse(report["credentials_sent"])
        self.assertEqual(first.sent, [b"ack"])
        self.assertFalse(second.sent)

    def test_gateway_owner_mismatch_is_not_success(self):
        _, _, report = self.run_wire(gateway=gateway_reply(1822797559))
        self.assertEqual(report["outcome"], "gateway_role_mismatch")
        self.assertFalse(report["game_login_verified"])
        self.assertFalse(report["gateway"]["game_login_verified"])
        self.assertTrue(report["gateway"]["login_result_uid_present"])
        self.assertFalse(report["gateway"]["login_result_uid_matches"])

    def test_gateway_rejection_has_no_retry_or_free_text(self):
        _, _, report = self.run_wire(gateway={"errcode": 123, "reason": "fixture-private-reason"})
        self.assertEqual(report["outcome"], "gateway_rejected_login")
        self.assertEqual(report["gateway"]["server_error_code"], 123)
        self.assertFalse(report["game_login_verified"])

    def test_unknown_gateway_command_and_record_mode_keep_only_numeric_diagnostics(self):
        _, _, report = self.run_wire(gateway_sub=104)
        self.assertEqual(report["outcome"], "unexpected_gateway_login_command")
        self.assertEqual(report["gateway"]["reply_main"], 1)
        self.assertEqual(report["gateway"]["reply_sub"], 104)
        self.assertFalse(report["game_login_verified"])
        _, _, report = self.run_wire(prefix=struct.pack("<I", (1 << 28) | 64))
        self.assertEqual(report["outcome"], "record_framing")
        self.assertEqual(report["gateway"]["unrecognized_record_flags"], 1)
        self.assertEqual(report["gateway"]["unrecognized_record_size"], 64)
        self.assertFalse(report["game_login_verified"])

    def test_normal_hotfix_envelopes_are_counted_not_executed(self):
        body = login.encode_body(login.HOTFIX_PUSH, {
            "split": 0, "path": "fixture", "content": b"never execute", "signature": b"fixture"})
        _, _, report = self.run_wire(prefix=record(4, 102, body) * 2)
        self.assertTrue(report["game_login_verified"])
        self.assertEqual(report["gateway"]["startup_hotfix_records"], 2)
        self.assertEqual(report["gateway"]["startup_hotfix_bytes"], len(b"never execute") * 2)
        self.assertFalse(report["gateway"]["remote_code_executed"])

    def test_hotfix_limit_closes_after_one_gateway_attempt(self):
        body = login.encode_body(login.HOTFIX_PUSH, {
            "split": 0, "path": "fixture", "content": b"never execute", "signature": b"fixture"})
        _, _, report = self.run_wire(prefix=record(4, 102, body) * 17)
        self.assertEqual(report["outcome"], "startup_hotfix_bound")
        self.assertEqual(report["gateway"]["startup_hotfix_records"], 16)
        self.assertFalse(report["game_login_verified"])

    def test_only_current_explicit_version_and_pc_are_eligible_before_dispatch(self):
        for version in (None, "3825464", "3848704", 3848703, True, [], "fixture-secret"):
            with self.subTest(version_type=type(version).__name__), patch.object(login, "check_login") as dispatch:
                report = probe.run_gateway_once(fixture(), None, None, client_version=version)
                self.assertEqual(report["outcome"], "web_sdk_version_unreviewed")
                self.assertFalse(report["credentials_sent"])
                dispatch.assert_not_called()
        for os_name in (None, "web", "", True, [], "fixture-secret"):
            with self.subTest(os_type=type(os_name).__name__), patch.object(login, "check_login") as dispatch:
                report = probe.run_gateway_once(fixture(), None, None, client_version=CURRENT_VERSION, os_name=os_name)
                self.assertEqual(report["outcome"], "web_sdk_platform_unreviewed")
                self.assertFalse(report["credentials_sent"])
                dispatch.assert_not_called()
        with self.assertRaises(TypeError):
            probe.run_gateway_once(fixture(), None, None)

    def test_invalid_sauth_is_rejected_before_dispatch(self):
        with patch.object(login, "check_login") as dispatch:
            report = probe.run_gateway_once({}, None, None, client_version=CURRENT_VERSION)
        self.assertEqual(report["outcome"], "web_sdk_shape")
        dispatch.assert_not_called()

    def test_unexpected_exception_is_redacted_and_transmission_unknown(self):
        with patch.object(login, "check_login", side_effect=RuntimeError("fixture-secret")) as dispatch:
            report = probe.run_gateway_once(fixture(), None, None, client_version=CURRENT_VERSION)
        self.assertEqual(report["outcome"], "web_gateway_probe_failed")
        self.assertIsNone(report["credentials_sent"])
        self.assertIsNone(report["connections_attempted"])
        self.assertFalse(report["game_login_verified"])
        self.assertNotIn("fixture-secret", json.dumps(report))
        dispatch.assert_called_once()

    def test_isolated_success_flags_do_not_prove_verified_chain(self):
        incomplete = {
            "outcome": "own_gateway_login_verified", "game_login_verified": True,
            "gateway": {"outcome": "own_gateway_login_verified", "game_login_verified": True,
                        "login_key": "fixture-secret", "login_key_present": True},
        }
        with patch.object(login, "check_login", return_value=incomplete):
            report = probe.run_gateway_once(fixture(), None, None, client_version=CURRENT_VERSION)
        self.assertEqual(report["outcome"], "web_gateway_report_unrecognized")
        self.assertFalse(report["game_login_verified"])
        self.assertFalse(report["gateway"]["game_login_verified"])
        self.assertNotIn("fixture-secret", json.dumps(report))

    def test_report_accepts_only_typed_diagnostics_and_fixed_outcomes(self):
        for raw in (None, {"outcome": "fixture-secret"}, {"outcome": ["fixture-secret"]},
                    {"outcome": "gateway_rejected_login", "gateway": {"outcome": "fixture-secret"}}):
            with self.subTest(raw_type=type(raw).__name__), patch.object(login, "check_login", return_value=raw):
                report = probe.run_gateway_once(fixture(), None, None, client_version=CURRENT_VERSION)
                self.assertEqual(report["outcome"], "web_gateway_report_unrecognized")
                self.assertNotIn("fixture-secret", json.dumps(report))
        raw = {"outcome": "gateway_rejected_login", "message": "fixture-secret",
               "gateway": {"outcome": "gateway_rejected_login", "server_error_code": True,
                           "login_key_present": 1, "startup_hotfix_records": "fixture-secret",
                           "reply_relogin": "fixture-secret", "auth_sn": "fixture-secret",
                           "career_verified": True, "secrets_saved": True}}
        with patch.object(login, "check_login", return_value=raw):
            report = probe.run_gateway_once(fixture(), None, None, client_version=CURRENT_VERSION)
        self.assertNotIn("fixture-secret", json.dumps(report))
        self.assertNotIn("server_error_code", report["gateway"])
        self.assertNotIn("login_key_present", report["gateway"])
        self.assertNotIn("startup_hotfix_records", report["gateway"])
        self.assertFalse(report["gateway"]["career_verified"])
        self.assertFalse(report["gateway"]["secrets_saved"])

    def test_command_and_record_diagnostic_bounds_reject_bools_strings_and_outliers(self):
        limits = {"reply_main": 65535, "reply_sub": 255,
                  "unrecognized_record_flags": 15, "unrecognized_record_size": 2**28 - 1}
        for name, maximum in limits.items():
            for value in (True, "fixture-secret", -1, maximum + 1):
                raw = {"outcome": "record_framing", "gateway": {"outcome": "record_framing", name: value}}
                with self.subTest(name=name, value_type=type(value).__name__), \
                        patch.object(login, "check_login", return_value=raw):
                    report = probe.run_gateway_once(fixture(), None, None, client_version=CURRENT_VERSION)
                self.assertNotIn(name, report["gateway"])
                self.assertNotIn("fixture-secret", json.dumps(report))
            for value in (0, maximum):
                raw = {"outcome": "record_framing", "gateway": {"outcome": "record_framing", name: value}}
                with patch.object(login, "check_login", return_value=raw):
                    report = probe.run_gateway_once(fixture(), None, None, client_version=CURRENT_VERSION)
                self.assertEqual(report["gateway"][name], value)


if __name__ == "__main__":
    unittest.main()
