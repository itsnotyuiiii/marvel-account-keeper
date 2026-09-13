"""Synthetic native owner rank-panel data and sockets; no account or network."""
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import msgpack

import game_career_probe as career
import game_login_probe as login
import game_rank_panel_probe as probe
from nex_rpc_probe import decode_avatar_rpc, split_plaintext_records


ZONE = {"zone_id": 11001, "role_uid": 1370598422}


def record(main, sub, body):
    return struct.pack("<IHB", len(body) + 7, main, sub) + body


def rpc(index, args):
    packed = msgpack.packb(args, use_bin_type=True)
    return record(3, 101, b"\x61" + struct.pack("<I", index)
                  + b"\xe2" + struct.pack("<I", len(packed)) + packed)


def panel(**changes):
    value = {"cur_game_id": 19, "left_time": 86400, "open": True, "start_time": 1700000000,
             "cur_data": {"level": 14, "rank_score": 2545.5}}
    value.update(changes)
    return value


class FakeSocket:
    def __init__(self, incoming, fail_send=False):
        self.incoming, self.sent, self.timeouts, self.fail_send = incoming, [], [], fail_send

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, value):
        self.sent.append(value)
        if self.fail_send:
            raise OSError("PRIVATE_PARTIAL_SEND")

    def recv(self, count):
        size = min(count, 3)
        value, self.incoming = self.incoming[:size], self.incoming[size:]
        return value


class RankPanelProjectionTest(unittest.TestCase):
    def project(self, **changes):
        return probe.project_rank_panel_args([panel(**changes)])

    def test_current_season_and_source_numeric_rank_are_separate_from_history_and_labels(self):
        result = self.project()
        self.assertTrue(result["current_season_verified"])
        self.assertEqual(result["current_season"], 19)
        self.assertTrue(result["own_rank_verified"])
        self.assertEqual(result["current"], {"season": 19, "level": 14, "points": 2545.5,
                         "points_basis": "server_rank_score", "points_verified": True})
        self.assertTrue(result["season_open"])
        self.assertEqual(result["seconds_left"], 86400)
        self.assertEqual(result["start_time"], 1700000000)
        for key in ("rank_history_verified", "rank_labels_verified", "overall_peak_verified"):
            self.assertFalse(result[key])
        self.assertNotIn("uid", result)
        self.assertNotIn("rank_panel_verified", result)

    def test_integral_numeric_fields_are_normalized_without_string_or_boolean_coercion(self):
        result = self.project(cur_game_id=19.0, cur_data={"level": 14.0, "rank_score": 0})
        self.assertIs(type(result["current_season"]), int)
        self.assertIs(type(result["current"]["level"]), int)
        self.assertEqual(result["current"]["points"], 0)
        for value in (True, "19", 19.5, 0, -1, 1000, float("nan"), float("inf"), 10**1000):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(probe.RankPanelError, "rank_panel_season_invalid"):
                    self.project(cur_game_id=value)

    def test_missing_or_empty_current_data_does_not_fabricate_rank(self):
        for value in (None, {}):
            result = self.project(cur_data=value)
            self.assertTrue(result["current_season_verified"])
            self.assertFalse(result["own_rank_verified"])
            self.assertFalse(result["own_rank_present"])
            self.assertIsNone(result["current"])
            self.assertIsNone(result["own_rank_error"])
        value = panel()
        del value["cur_data"]
        result = probe.project_rank_panel_args([value])
        self.assertEqual(result["shape"]["cur_data_type"], "missing")
        self.assertIsNone(result["current"])

    def test_invalid_optional_rank_does_not_erase_verified_current_season(self):
        cases = [("PRIVATE_DATA", "rank_panel_rank_shape"), ([], "rank_panel_rank_shape"),
                 ({"level": True, "rank_score": 1}, "rank_panel_level_invalid"),
                 ({"level": "14", "rank_score": 1}, "rank_panel_level_invalid"),
                 ({"level": 1.5, "rank_score": 1}, "rank_panel_level_invalid"),
                 ({"level": 65536, "rank_score": 1}, "rank_panel_level_invalid"),
                 ({"level": 10**1000, "rank_score": 1}, "rank_panel_level_invalid")]
        for value, error in cases:
            result = self.project(cur_data=value)
            self.assertTrue(result["current_season_verified"])
            self.assertEqual(result["current_season"], 19)
            self.assertFalse(result["own_rank_verified"])
            self.assertEqual(result["own_rank_error"], error)
            self.assertIsNone(result["current"])
            self.assertNotIn("PRIVATE_DATA", json.dumps(result))

    def test_missing_and_bad_score_remain_unknown_without_source_default_zero(self):
        for value in (None, True, "0", -1, 1_000_001, float("nan"), float("inf"), 10**1000):
            result = self.project(cur_data={"level": 0, "rank_score": value})
            self.assertTrue(result["current_season_verified"])
            self.assertFalse(result["own_rank_verified"])
            self.assertEqual(result["current"]["level"], 0)
            self.assertIsNone(result["current"]["points"])
            self.assertFalse(result["current"]["points_verified"])
            self.assertEqual(result["own_rank_error"], "rank_panel_score_invalid")
            json.dumps(result, allow_nan=False)
        result = self.project(cur_data={"level": 14})
        self.assertIsNone(result["current"]["points"])
        self.assertEqual(result["shape"]["rank_score_type"], "missing")

    def test_nonrank_optional_metadata_is_typed_and_absence_is_unknown(self):
        for open_value, expected in ((False, False), (0, False), (1, True), (1.0, True),
                                     ("open", None), (2, None), (None, None)):
            result = self.project(open=open_value, left_time=-10, start_time=float("nan"))
            self.assertIs(result["season_open"], expected)
            self.assertEqual(result["seconds_left"], -10)
            self.assertIsNone(result["start_time"])
            self.assertTrue(result["current_season_verified"])
            json.dumps(result, allow_nan=False)
        value = {"cur_game_id": 19}
        result = probe.project_rank_panel_args([value])
        self.assertIsNone(result["season_open"])
        self.assertIsNone(result["seconds_left"])

    def test_unknown_identity_and_type_like_fields_are_not_invented_response_echoes(self):
        baseline = self.project()
        for value in (1, 1.0, True, "PRIVATE", 0, 2, None, 1.1):
            self.assertEqual(self.project(rank_game_type=value, uid="PRIVATE", id="PRIVATE"), baseline)

    def test_fixed_shapes_and_argument_limits_export_no_unknown_fields(self):
        result = self.project(uid=123, aid="PRIVATE_AID", token="PRIVATE_TOKEN",
                              cur_data={"level": 14, "rank_score": 2, "PRIVATE_KEY": "PRIVATE_VALUE"})
        output = json.dumps(result)
        for value in ("PRIVATE", "uid", "aid", "token"):
            self.assertNotIn(value, output)
        for args in (None, {}, [], [panel(), panel()], [None], [{str(n): n for n in range(513)}]):
            with self.assertRaisesRegex(probe.RankPanelError, "rank_panel_argument_shape"):
                probe.project_rank_panel_args(args)
        error = probe.RankPanelError("PRIVATE_FAILURE")
        self.assertEqual(str(error), "rank_panel_reply_unvalidated")


class RankPanelReaderTest(unittest.TestCase):
    def read(self, incoming=None, *, zone=ZONE, version="3848703", sock=None, transport=None, deadline=None):
        if incoming is None:
            incoming = rpc(probe.REPLY_INDEX, [panel()])
        sock = FakeSocket(incoming) if sock is None else sock
        transport = (SimpleNamespace(encrypt=lambda data: data, decrypt=lambda data: data)
                     if transport is None else transport)
        result = probe.read_rank_panel_once(sock, transport, zone, client_version=version, deadline=deadline)
        self.assertNotIn("PRIVATE", json.dumps(result, allow_nan=False))
        self.assertFalse(result["secrets_saved"])
        self.assertFalse(result["remote_code_executed"])
        self.assertFalse(result["response_identity_echo_verified"])
        self.assertFalse(result["nonce_correlated"])
        return result, sock

    def test_exact_one_normal_request_and_reply_on_supplied_owner_connection(self):
        result, sock = self.read()
        self.assertEqual(result["outcome"], "owner_rank_panel_verified")
        self.assertTrue(result["owner_context_valid"])
        self.assertEqual(result["uid"], str(ZONE["role_uid"]))
        self.assertTrue(result["rank_panel_verified"])
        self.assertTrue(result["current_season_verified"])
        self.assertTrue(result["own_rank_verified"])
        self.assertTrue(result["connection_reuse_allowed"])
        self.assertTrue(result["request_sent"])
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(len(sock.sent), 1)
        request, = split_plaintext_records(sock.sent[0])
        decoded = decode_avatar_rpc(request, direction="out")
        self.assertEqual(decoded.index, 1543545906)
        self.assertEqual(decoded.args, [1])
        self.assertEqual(result["application_bytes_sent"], len(sock.sent[0]))
        self.assertEqual(result["records_received"], 1)
        self.assertFalse(sock.incoming)
        self.assertTrue(all(0 < value <= 15 for value in sock.timeouts))

    def test_unsupported_build_wrong_owner_and_malformed_zone_never_send_or_receive(self):
        inputs = [(ZONE, "3825464"), (ZONE, 3848703), (ZONE, True), ({}, "3848703"),
                  ({**ZONE, "role_uid": 123}, "3848703"), ({**ZONE, "role_uid": True}, "3848703"),
                  ({**ZONE, "zone_id": True}, "3848703"), ({**ZONE, "zone_id": 0}, "3848703"),
                  ({**ZONE, "zone_id": 2**31}, "3848703")]
        for zone, version in inputs:
            with patch.object(login, "receive_record") as receive:
                result, sock = self.read(zone=zone, version=version)
            self.assertEqual(result["outcome"], "rank_panel_inputs_invalid")
            self.assertFalse(result["request_attempted"])
            self.assertFalse(result["rank_panel_verified"])
            self.assertFalse(sock.sent)
            receive.assert_not_called()

    def test_engine_entity_ids_do_not_gate_owner_and_unrelated_rpc_is_not_executed(self):
        created = record(65534, 113, login.encode_body(career.ENTITY_CREATE, {
            "eid": 90001, "classname": "GameServiceAvatar", "initdata": b"PRIVATE_INITDATA"}))
        destroyed = record(65534, 114, login.encode_body(career.ENTITY_DESTROY, {"eid": 90002}))
        update = record(65534, 120, login.encode_body(career.ENTITY_PROP, {
            "entity_id": 90001, "sync_msg": b"PRIVATE_PROPERTIES"}))
        ack = record(65534, 111, login.encode_body(career.MSG_ACK, {"ack": 4}))
        result, sock = self.read(created + destroyed + update + ack + rpc(123, ["PRIVATE_RPC"])
                                 + rpc(probe.REPLY_INDEX, [panel()]))
        self.assertTrue(result["rank_panel_verified"])
        self.assertEqual(result["entity_create_records"], 1)
        self.assertEqual(result["entity_destroy_records"], 1)
        self.assertEqual(result["startup_record_count"], 4)
        self.assertEqual(result["other_rpc_count"], 1)
        self.assertEqual(len(sock.sent), 1)

    def test_hotfix_is_only_bounded_envelope_telemetry_never_executed(self):
        hotfix = record(4, 102, login.encode_body(login.HOTFIX_PUSH, {
            "split": 0, "path": "PRIVATE_PATH", "content": b"PRIVATE_CODE", "signature": b"PRIVATE_SIG"}))
        result, _ = self.read(hotfix * 2 + rpc(probe.REPLY_INDEX, [panel()]))
        self.assertTrue(result["rank_panel_verified"])
        self.assertEqual(result["startup_hotfix_records"], 2)
        self.assertEqual(result["startup_hotfix_bytes"], 24)
        result, sock = self.read(hotfix * 17 + rpc(probe.REPLY_INDEX, [panel()]))
        self.assertEqual(result["outcome"], "rank_panel_hotfix_bound")
        self.assertFalse(result["connection_reuse_allowed"])
        self.assertEqual(result["startup_hotfix_records"], 16)
        self.assertEqual(len(sock.sent), 1)

    def test_unrelated_rpc_argument_blobs_are_not_decoded(self):
        # Unknown extension / non-MessagePack payload need not be interpreted
        # for an unrelated method. Its current public envelope still validates.
        packed = b"PRIVATE_UNPARSED_ARGUMENTS"
        unrelated = record(3, 101, b"\xe2" + struct.pack("<I", len(packed)) + packed
                           + b"\x61" + struct.pack("<I", 999))
        with patch.object(probe, "decode_avatar_rpc", wraps=decode_avatar_rpc) as decode:
            result, _ = self.read(unrelated + rpc(probe.REPLY_INDEX, [panel()]))
        self.assertTrue(result["rank_panel_verified"])
        self.assertEqual(result["other_rpc_count"], 1)
        self.assertEqual(decode.call_count, 1)

    def test_reset_unknown_command_and_record_bound_fail_without_retry(self):
        cases = [(record(65534, 112, login.encode_body(career.MSG_RESET, {"reset": 1})),
                  "rank_panel_startup_rejected"),
                 (record(9, 9, b"PRIVATE"), "rank_panel_unrecognized_startup_command"),
                 (rpc(999, []) * probe.MAX_RECORDS, "rank_panel_record_bound")]
        for incoming, expected in cases:
            result, sock = self.read(incoming)
            self.assertEqual(result["outcome"], expected)
            self.assertFalse(result["rank_panel_verified"])
            self.assertFalse(result["connection_reuse_allowed"])
            self.assertEqual(len(sock.sent), 1)

    def test_reply_method_is_exact_and_bad_matching_args_do_not_trigger_another_read(self):
        incoming = rpc(probe.REQUEST_INDEX, [panel()]) + rpc(probe.REPLY_INDEX, [panel(cur_game_id="PRIVATE")])
        result, sock = self.read(incoming + rpc(probe.REPLY_INDEX, [panel()]))
        self.assertEqual(result["other_rpc_count"], 1)
        self.assertTrue(result["reply_method_received"])
        self.assertEqual(result["outcome"], "rank_panel_season_invalid")
        self.assertFalse(result["current_season_verified"])
        self.assertFalse(result["rank_panel_verified"])
        self.assertTrue(sock.incoming)
        self.assertEqual(len(sock.sent), 1)
        result, _ = self.read(rpc(probe.REPLY_INDEX, [panel(rank_game_type=2)]))
        self.assertTrue(result["rank_panel_verified"])

    def test_total_receive_bound_and_errors_are_fixed_and_terminal(self):
        def excessive(connection, transport, deadline, report, **kwargs):
            self.assertEqual(kwargs["max_size"], probe.MAX_BYTES)
            report["bytes_received"] = probe.MAX_BYTES + 1
            return object()
        with patch.object(login, "receive_record", side_effect=excessive):
            result, sock = self.read()
        self.assertEqual(result["outcome"], "rank_panel_receive_bound")
        self.assertEqual(len(sock.sent), 1)
        for error, code in ((TimeoutError("PRIVATE"), "rank_panel_timeout"),
                            (EOFError("PRIVATE"), "rank_panel_connection_closed"),
                            (OSError("PRIVATE"), "rank_panel_network_error"),
                            (ValueError("PRIVATE"), "rank_panel_reply_unvalidated"),
                            (RuntimeError("PRIVATE"), "rank_panel_unexpected")):
            with patch.object(login, "receive_record", side_effect=error):
                result, sock = self.read()
            self.assertEqual(result["outcome"], code)
            self.assertFalse(result["connection_reuse_allowed"])
            self.assertEqual(len(sock.sent), 1)

    def test_partial_send_is_unknown_and_never_retried(self):
        sock = FakeSocket(b"", fail_send=True)
        with patch.object(login, "receive_record") as receive:
            result, _ = self.read(sock=sock)
        self.assertEqual(result["outcome"], "rank_panel_network_error")
        self.assertTrue(result["request_attempted"])
        self.assertIsNone(result["request_sent"])
        self.assertIsNone(result["application_bytes_sent"])
        self.assertFalse(result["connection_reuse_allowed"])
        self.assertEqual(len(sock.sent), 1)
        receive.assert_not_called()
        crypt = SimpleNamespace(encrypt=Mock(side_effect=ValueError("PRIVATE")))
        result, sock = self.read(transport=crypt)
        self.assertFalse(result["request_attempted"])
        self.assertFalse(sock.sent)

    def test_total_receive_budget_limits_next_record_before_reading_its_body(self):
        first, second = rpc(999, []), rpc(probe.REPLY_INDEX, [panel()])
        with patch.object(probe, "MAX_BYTES", len(first) + 20):
            result, sock = self.read(first + second)
        self.assertEqual(result["outcome"], "rank_panel_receive_bound")
        self.assertEqual(result["bytes_received"], len(first) + 4)
        self.assertEqual(sock.incoming, second[4:])
        self.assertFalse(result["connection_reuse_allowed"])
        self.assertEqual(len(sock.sent), 1)

    def test_reply_projection_preserves_season_when_optional_rank_missing_or_invalid(self):
        for data in (None, {}, {"level": 14}, {"level": "PRIVATE", "rank_score": 1}):
            result, sock = self.read(rpc(probe.REPLY_INDEX, [panel(cur_data=data)]))
            self.assertTrue(result["rank_panel_verified"])
            self.assertTrue(result["current_season_verified"])
            self.assertFalse(result["own_rank_verified"])
            self.assertTrue(result["connection_reuse_allowed"])
            self.assertEqual(len(sock.sent), 1)

    def test_reader_does_not_authenticate_connect_or_load_installed_files(self):
        with patch("builtins.open") as opened, patch("pathlib.Path.open") as path_open, \
                patch("socket.create_connection") as connect, patch.object(login, "check_login") as auth:
            result, _ = self.read()
        self.assertTrue(result["rank_panel_verified"])
        for method in (opened, path_open, connect, auth):
            method.assert_not_called()

    def test_expired_or_invalid_shared_deadline_before_cipher_is_a_reusable_no_io_skip(self):
        for deadline in (99, 100, True, "110", float("inf"), float("nan"), 10**1000):
            cipher = Mock(side_effect=AssertionError("must not encrypt"))
            with patch.object(probe.time, "monotonic", return_value=100), \
                    patch.object(login, "receive_record") as receive:
                result, sock = self.read(deadline=deadline, transport=SimpleNamespace(encrypt=cipher))
            self.assertFalse(result["request_attempted"])
            self.assertFalse(result["request_sent"])
            self.assertFalse(result["reply_method_received"])
            self.assertFalse(result["rank_panel_verified"])
            self.assertTrue(result["connection_reuse_allowed"])
            self.assertFalse(sock.sent)
            self.assertEqual(result["application_bytes_sent"], 0)
            cipher.assert_not_called()
            receive.assert_not_called()
            expected = "rank_panel_timeout" if type(deadline) is int and deadline in (99, 100) else "rank_panel_deadline_invalid"
            self.assertEqual(result["outcome"], expected)

    def test_shared_deadline_caps_send_and_receive_and_cannot_extend_default_budget(self):
        original = login.receive_record
        for supplied, expected in ((None, 115), (110, 110), (1000, 115)):
            seen = []
            def receive(connection, transport, deadline, report, **kwargs):
                seen.append(deadline)
                return original(connection, transport, deadline, report, **kwargs)
            with patch.object(probe.time, "monotonic", return_value=100), \
                    patch.object(login, "receive_record", side_effect=receive):
                result, sock = self.read(deadline=supplied)
            self.assertTrue(result["rank_panel_verified"])
            self.assertEqual(seen, [expected])
            self.assertEqual(sock.timeouts[0], expected - 100)
            self.assertTrue(all(0 < value <= expected - 100 for value in sock.timeouts))

    def test_expiry_during_encryption_sends_nothing_but_cannot_reuse_advanced_cipher(self):
        clock = [100]
        def encrypt(value):
            clock[0] = 110
            return value
        with patch.object(probe.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(login, "receive_record") as receive:
            result, sock = self.read(deadline=110, transport=SimpleNamespace(encrypt=encrypt))
        self.assertEqual(result["outcome"], "rank_panel_timeout")
        self.assertFalse(result["request_attempted"])
        self.assertFalse(result["request_sent"])
        self.assertFalse(result["connection_reuse_allowed"])
        self.assertEqual(result["application_bytes_sent"], 0)
        self.assertFalse(sock.sent)
        self.assertFalse(sock.timeouts)
        receive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
