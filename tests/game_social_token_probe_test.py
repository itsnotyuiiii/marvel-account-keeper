"""Synthetic passive startup streams; no accounts, listeners or network."""
import base64
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import msgpack

import game_career_probe as career
import game_login_probe as login
import game_social_token_probe as probe
from web_sdk_auth_probe_test import FakeSocket, auth_reply, fixture


INDEX = 12345  # Synthetic method supplied by the trusted caller, not a live pin.
ZONE = {"zone_id": 11001, "role_uid": 1370598422}


def record(main, sub, body):
    return struct.pack("<IHB", len(body) + 7, main, sub) + body


def rpc(index, args):
    packed = msgpack.packb(args, use_bin_type=True)
    return record(3, 101, b"\x61" + struct.pack("<I", index)
                  + b"\xe2" + struct.pack("<I", len(packed)) + packed)


class GameSocialTokenProbeTest(unittest.TestCase):
    def read(self, incoming, *, zone=ZONE, index=INDEX, callback=None, parser_error=None,
             activate_owner=True):
        if activate_owner:
            create = login.encode_body(career.ENTITY_CREATE, {
                "eid": 1370598422, "classname": "PlayerAvatar", "initdata": b""})
            incoming = record(65534, 113, create) + incoming
        sock = FakeSocket(incoming)
        transient = object()
        with patch.object(probe.social, "parse_gateway_social_token", return_value=transient,
                          side_effect=parser_error) as parse:
            report = probe.read_social_token(
                sock, SimpleNamespace(decrypt=lambda value: value), zone,
                token_rpc_index=index, on_token=callback)
        self.assertFalse(sock.sent)
        self.assertFalse(report["social_session_verified"])
        self.assertFalse(report["career_verified"])
        self.assertFalse(report["secrets_saved"])
        self.assertFalse(report["remote_code_executed"])
        self.assertEqual(report["application_bytes_sent"], 0)
        self.assertNotIn("fixture-secret", json.dumps(report))
        return report, parse, transient

    def test_one_normal_token_is_passive_bound_and_callback_only(self):
        callback = Mock()
        report, parse, transient = self.read(rpc(INDEX, ["fixture-secret"]), callback=callback)
        self.assertEqual(report["outcome"], "social_token_claims_validated")
        self.assertTrue(report["social_token_received"])
        self.assertTrue(report["owner_claim_matches"])
        self.assertTrue(report["time_claims_valid"])
        self.assertTrue(report["route_shape_valid"])
        self.assertTrue(report["consumer_completed"])
        parse.assert_called_once_with("fixture-secret", own_uid=1370598422, zone_id=11001)
        callback.assert_called_once_with(transient)

    def test_token_need_not_leave_reader_if_no_consumer(self):
        report, parse, _ = self.read(rpc(INDEX, ["fixture-secret"]))
        self.assertTrue(report["social_token_received"])
        self.assertFalse(report["consumer_completed"])
        parse.assert_called_once()

    def test_invalid_owner_zone_method_or_consumer_stops_before_receive(self):
        cases = [({"zone_id": 11001, "role_uid": 1822797559}, INDEX, None),
                 ({"zone_id": True, "role_uid": 1370598422}, INDEX, None),
                 ({"zone_id": 11001}, INDEX, None), (ZONE, True, None),
                 (ZONE, 0, None), (ZONE, 2**31, None), (ZONE, INDEX, "fixture-secret")]
        for zone, index, callback in cases:
            with self.subTest(index_type=type(index).__name__), patch.object(login, "receive_record") as receive:
                report, parse, _ = self.read(b"", zone=zone, index=index, callback=callback)
                self.assertEqual(report["outcome"], "social_token_reader_inputs_invalid")
                receive.assert_not_called()
                parse.assert_not_called()

    def test_unrelated_rpc_and_entity_events_never_become_a_token(self):
        body = login.encode_body(career.ENTITY_CREATE, {
            "eid": 1370598422, "classname": "PlayerAvatar", "initdata": b"fixture-secret"})
        incoming = record(65534, 113, body) + rpc(999, ["fixture-secret"]) + rpc(INDEX, ["fixture-secret"])
        report, parse, _ = self.read(incoming, activate_owner=False)
        self.assertTrue(report["social_token_received"])
        self.assertEqual(report["entity_create_records"], 1)
        self.assertEqual(report["startup_record_count"], 1)
        self.assertEqual(report["other_rpc_count"], 1)
        self.assertEqual(report["records_received"], 3)
        parse.assert_called_once()

    def test_engine_entity_telemetry_is_not_an_account_uid_gate(self):
        destroyed = record(65534, 114, login.encode_body(career.ENTITY_DESTROY, {"eid": 1370598422}))
        for incoming, activate in ((destroyed + rpc(INDEX, ["fixture-secret"]), True),
                                   (rpc(INDEX, ["fixture-secret"]), False)):
            report, parse, _ = self.read(incoming, activate_owner=activate)
            self.assertEqual(report["outcome"], "social_token_claims_validated")
            self.assertTrue(report["social_token_received"])
            parse.assert_called_once_with("fixture-secret", own_uid=1370598422, zone_id=11001)

    def test_initial_destroy_then_normal_owner_create_is_supported(self):
        destroyed = record(65534, 114, login.encode_body(career.ENTITY_DESTROY, {"eid": 1370598422}))
        created = record(65534, 113, login.encode_body(career.ENTITY_CREATE, {
            "eid": 1370598422, "classname": "PlayerAvatar", "initdata": b""}))
        report, parse, _ = self.read(destroyed + created + rpc(INDEX, ["fixture-secret"]),
                                     activate_owner=False)
        self.assertTrue(report["social_token_received"])
        self.assertEqual(report["entity_destroy_records"], 1)
        self.assertEqual(report["entity_create_records"], 1)
        parse.assert_called_once()

    def test_exact_one_string_argument_is_required(self):
        for args in ([], [b"fixture-secret"], ["fixture-secret", "extra"], [None], [1]):
            with self.subTest(args_type=type(args[0]).__name__ if args else "empty"):
                report, parse, _ = self.read(rpc(INDEX, args))
                self.assertEqual(report["outcome"], "social_token_argument_shape")
                self.assertFalse(report["social_token_received"])
                parse.assert_not_called()

    def test_empty_token_is_not_a_session_or_refresh_request(self):
        report, parse, _ = self.read(rpc(INDEX, [""]))
        self.assertEqual(report["outcome"], "social_token_empty")
        self.assertTrue(report["social_token_received"])
        self.assertFalse(report["owner_claim_matches"])
        parse.assert_not_called()

    def test_claim_rejection_never_reaches_consumer(self):
        callback = Mock()
        report, _, _ = self.read(rpc(INDEX, ["fixture-secret"]), callback=callback,
                                 parser_error=probe.social.SocialSessionError("fixture-secret"))
        self.assertEqual(report["outcome"], "social_token_claims_rejected")
        self.assertTrue(report["social_token_received"])
        self.assertFalse(report["owner_claim_matches"])
        callback.assert_not_called()

    def test_known_hotfix_envelopes_are_bounded_and_never_executed(self):
        body = login.encode_body(login.HOTFIX_PUSH, {
            "split": 0, "path": "fixture", "content": b"fixture-secret", "signature": b"signature"})
        hotfix = record(4, 102, body)
        report, _, _ = self.read(hotfix * 2 + rpc(INDEX, ["fixture-secret"]))
        self.assertTrue(report["social_token_received"])
        self.assertEqual(report["startup_hotfix_records"], 2)
        report, parse, _ = self.read(hotfix * 17 + rpc(INDEX, ["fixture-secret"]))
        self.assertEqual(report["outcome"], "social_token_hotfix_bound")
        self.assertEqual(report["startup_hotfix_records"], 16)
        parse.assert_not_called()

    def test_sequence_reset_unknown_command_and_record_limit_stop(self):
        cases = [(record(65534, 112, login.encode_body(career.MSG_RESET, {"reset": 1})),
                  "social_token_startup_rejected"),
                 (record(65534, 119, b""), "social_token_unrecognized_startup_command"),
                 (rpc(999, []) * 128, "social_token_record_bound")]
        for wire, outcome in cases:
            with self.subTest(outcome=outcome):
                report, parse, _ = self.read(wire)
                self.assertEqual(report["outcome"], outcome)
                self.assertFalse(report["social_token_received"])
                parse.assert_not_called()

    def test_receive_limit_and_network_errors_are_redacted(self):
        def oversized(connection, transport, deadline, report, **kwargs):
            self.assertEqual(kwargs["max_size"], probe.MAX_STARTUP_BYTES)
            report["bytes_received"] = probe.MAX_STARTUP_BYTES + 1
            return SimpleNamespace()
        with patch.object(login, "receive_record", side_effect=oversized):
            report, parse, _ = self.read(b"")
            self.assertEqual(report["outcome"], "social_token_receive_bound")
            parse.assert_not_called()
        for error, outcome in ((TimeoutError("fixture-secret"), "social_token_timeout"),
                               (EOFError("fixture-secret"), "social_token_connection_closed"),
                               (OSError("fixture-secret"), "social_token_network_error")):
            with self.subTest(outcome=outcome), patch.object(login, "receive_record", side_effect=error):
                report, _, _ = self.read(b"")
                self.assertEqual(report["outcome"], outcome)

    def test_consumer_error_does_not_leak_or_imply_service_acceptance(self):
        callback = Mock(side_effect=RuntimeError("fixture-secret"))
        report, _, _ = self.read(rpc(INDEX, ["fixture-secret"]), callback=callback)
        self.assertEqual(report["outcome"], "social_token_consumer_failed")
        self.assertTrue(report["social_token_received"])
        self.assertTrue(report["owner_claim_matches"])
        self.assertFalse(report["consumer_completed"])


class SocialTokenLoginChainTest(unittest.TestCase):
    def run_chain(self, *, auth_uid=1370598422, gateway_uid=1370598422, claim_uid=1370598422):
        now = 1700000000
        def segment(value):
            return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
        token = ".".join((segment({"alg": "RS256"}), segment({
            "aid": f"11001_{claim_uid}", "iat": now - 10, "exp": now + 600,
            "tcp": {"addr": "8.8.4.4:12101", "rc4": True}}), "c2ln"))
        prepare = login.encode_body(login.PREPARE, {
            "version_list": [], "args": b"", "is_test_branch": 0, "timestamp": now, "timezone": 0})
        auth = auth_reply(auth_uid)
        auth["zone_list"][0]["zone_id"] = 11001
        first = FakeSocket(bytes(28) + record(1, 101, prepare)
                           + record(1, 102, login.encode_body(login.AUTH_REPLY, auth)))
        result = {"errcode": 0, "relogin": 0, "info": {
            "uid": gateway_uid, "client_ver": "3848703", "login_key": "fixture-secret"}}
        created = record(65534, 113, login.encode_body(career.ENTITY_CREATE, {
            "eid": 90001, "classname": "PlayerAvatar", "initdata": b""}))
        second = FakeSocket(bytes(28) + record(1, 103, login.encode_body(login.LOGIN_REPLY, result))
                            + created + rpc(1538836248, [token]))
        connect = Mock(side_effect=[first, second])
        transport = SimpleNamespace(acknowledgement=b"ack", encrypt=lambda value: value, decrypt=lambda value: value)
        original = login.check_login
        callback = Mock()
        def check(configuration, server, **kwargs):
            self.assertTrue(kwargs["enter"])
            self.assertEqual(kwargs["expected_uid"], 1370598422)
            self.assertEqual(kwargs["inputs"].auth["client_version"], "3848703")
            return original(configuration, server, connect=connect, **kwargs)
        with patch.object(login, "check_login", side_effect=check), \
                patch.object(login, "prepare_transport", return_value=transport), \
                patch.object(login.time, "time", return_value=now), \
                patch.object(login, "load_own_login_inputs") as desktop, \
                patch("pathlib.Path.open") as files:
            report = probe.run_social_token_once(fixture(), None, login.LoginServer("8.8.8.8", 13001, (10001,)),
                                                 client_version="3848703", on_token=callback)
        files.assert_not_called()
        desktop.assert_not_called()
        self.assertTrue(first.closed)
        self.assertNotIn(token, json.dumps(report))
        self.assertNotIn("fixture-secret", json.dumps(report))
        self.assertFalse(report["career_verified"])
        self.assertFalse(report["social_token"]["social_session_verified"])
        return report, callback, connect, second

    def test_verified_login_and_real_claim_parser_deliver_only_in_memory(self):
        report, callback, connect, second = self.run_chain()
        self.assertTrue(report["game_login_verified"])
        self.assertTrue(report["social_token"]["social_token_received"])
        self.assertTrue(report["social_token"]["owner_claim_matches"])
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(len(second.sent), 2)  # Handshake + ENTER only, no token request.
        self.assertTrue(second.closed)
        callback.assert_called_once()
        self.assertEqual(callback.call_args.args[0].own_uid, 1370598422)

    def test_auth_or_gateway_uid_mismatch_never_collects_token(self):
        for auth, gateway, expected_connections in ((1822797559, 1370598422, 1),
                                                     (1370598422, 1822797559, 2)):
            report, callback, connect, _ = self.run_chain(auth_uid=auth, gateway_uid=gateway)
            self.assertEqual(connect.call_count, expected_connections)
            self.assertFalse(report["game_login_verified"])
            self.assertFalse(report["social_token"]["social_token_received"])
            callback.assert_not_called()

    def test_token_wrong_owner_is_actionable_without_exposing_claims(self):
        report, callback, _, second = self.run_chain(claim_uid=1822797559)
        self.assertTrue(report["game_login_verified"])
        self.assertEqual(report["social_token"]["outcome"], "social_token_claims_rejected")
        self.assertEqual(report["social_token"]["claim_error"], "social_token_identity")
        self.assertFalse(report["social_token"]["owner_claim_matches"])
        self.assertTrue(second.closed)
        callback.assert_not_called()

    def test_unreviewed_build_cannot_reach_authentication(self):
        for version in ("3825464", "3848704", None, True):
            with patch.object(login, "check_login") as check:
                report = probe.run_social_token_once(fixture(), None, None, client_version=version)
                self.assertEqual(report["outcome"], "social_token_reader_inputs_invalid")
                self.assertFalse(report["credentials_sent"])
                check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
