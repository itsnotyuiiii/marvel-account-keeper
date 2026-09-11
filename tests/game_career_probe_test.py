"""Synthetic other-player Career RPC and reply binding; no network/accounts."""
import contextlib
import copy
import hashlib
import io
import json
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import msgpack

import game_career_probe as career
from game_login_probe_test import FakeSocket
from nex_rpc_probe import decode_avatar_rpc, split_plaintext_records
from nex_rpc_probe import NexRecord


METHODS = career.CareerMethods(123, 456)
TARGET = 1822797559


def rpc_record(index, args):
    packed = msgpack.packb(args, use_bin_type=True)
    body = b"\x61" + struct.pack("<I", index) + b"\xe2" + struct.pack("<I", len(packed)) + packed
    return struct.pack("<IHB", len(body) + 7, 3, 101) + body


def reply():
    _, requested = career.make_request(METHODS, TARGET, 11001, 19, "fixture-nonce")
    context = {"is_career": True, "career_req": requested, "mak_request_id": "fixture-nonce"}
    return ["query_player_career", context, 0, "", {"career": {"player_uid": TARGET, "fixture_stat": 7}}], requested


def match_reply(rows=None):
    _, requested = career.make_match_summary_request(METHODS, TARGET, 11001, "fixture-nonce")
    context = {"is_career": True, "career_req": dict(requested), "mak_request_id": "fixture-nonce"}
    if rows is None:
        rows = [{"match_player": {"player_uid": TARGET, "fixture_stat": 7},
                 "other_participant": {"player_uid": career.login.EXPECTED_OWN_UID}}]
    return ["query_match_summary", context, 0, "", {"match_info": rows}], requested


class GameMatchSummaryProbeTest(unittest.TestCase):
    def test_request_is_only_first_other_player_page_on_existing_career_rpc(self):
        request, data = career.make_match_summary_request(METHODS, TARGET, 11001, "fixture-nonce")
        rpc = decode_avatar_rpc(split_plaintext_records(request)[0], direction="out")
        self.assertEqual(rpc.index, METHODS.request)
        self.assertEqual(data, {"player_uid": TARGET, "zone_id": 11001, "page": 0, "page_size": 50})
        self.assertEqual(rpc.args, ["query_match_summary", data,
            {"is_career": True, "career_req": data, "mak_request_id": "fixture-nonce"}])
        for option in ("page", "page_size", "match_season", "device_type", "game_mode_id", "uri"):
            with self.subTest(option=option), self.assertRaises(TypeError):
                career.make_match_summary_request(METHODS, TARGET, 11001, "fixture", **{option: 1})

    def test_request_rejects_invalid_target_zone_nonce_or_login_owner(self):
        for uid, zone, nonce, own in ((career.login.EXPECTED_OWN_UID, 1, "nonce", career.login.EXPECTED_OWN_UID),
                                      (True, 1, "nonce", 1), (2**63, 1, "nonce", 1),
                                      (TARGET, True, "nonce", 1), (TARGET, 0, "nonce", 1),
                                      (TARGET, 1, "", 1), (TARGET, 1, "x" * 65, 1),
                                      (TARGET, 1, "nonce", True), (TARGET, 1, "nonce", 2**63)):
            with self.subTest(uid=uid, zone=zone, own=own), self.assertRaises(career.CareerError):
                career.make_match_summary_request(METHODS, uid, zone, nonce, own_uid=own)

    def test_verified_rows_bind_target_not_shared_login_owner(self):
        args, requested = match_reply()
        result = career.verify_match_summary_args(args, requested, "fixture-nonce")
        self.assertTrue(result["match_summary_verified"])
        self.assertEqual(result["target_uid"], TARGET)
        self.assertEqual(result["match_count"], 1)
        self.assertIsNone(result["has_more"])
        self.assertNotIn("fixture_stat", json.dumps(result))
        self.assertNotIn("career_verified", result)

    def test_empty_list_is_success_only_with_exact_request_and_nonce(self):
        args, requested = match_reply([])
        result = career.verify_match_summary_args(args, requested, "fixture-nonce")
        self.assertTrue(result["match_summary_verified"])
        self.assertEqual(result["match_count"], 0)
        self.assertIsNone(result["has_more"])
        for changed in ({"mak_request_id": "wrong"}, {"is_career": False}, {"career_req": {}}):
            bad = copy.deepcopy(args)
            bad[1].update(changed)
            with self.subTest(changed=changed), self.assertRaises(career.CareerError):
                career.verify_match_summary_args(bad, requested, "fixture-nonce")

    def test_requested_and_echoed_fields_cannot_expand_scope(self):
        for changes in ({"page": 1}, {"page_size": 100}, {"page": False}, {"page_size": 50.0},
                        {"zone_id": "11001"}, {"player_uid": str(TARGET)}, {"match_season": 19}):
            args, requested = match_reply([])
            requested.update(changes)
            args[1]["career_req"] = dict(requested)
            with self.subTest(changes=changes), self.assertRaises(career.CareerError):
                career.verify_match_summary_args(args, requested, "fixture-nonce")
        for changes in ({"player_uid": TARGET + 1}, {"zone_id": 11002}, {"page": 1},
                        {"page": False}, {"page_size": 50.0}, {"device_type": "pc"}):
            args, requested = match_reply([])
            args[1]["career_req"].update(changes)
            with self.subTest(echo=changes), self.assertRaises(career.CareerError):
                career.verify_match_summary_args(args, requested, "fixture-nonce")

    def test_method_code_and_collection_shape_are_strict(self):
        for method in ("query_my_match_summary", "query_player_career", None):
            args, requested = match_reply()
            args[0] = method
            with self.assertRaises(career.CareerError):
                career.verify_match_summary_args(args, requested, "fixture-nonce")
        for code in (True, "0", None):
            args, requested = match_reply()
            args[2] = code
            with self.assertRaises(career.CareerError):
                career.verify_match_summary_args(args, requested, "fixture-nonce")
        for data in (None, {}, {"match_info": None}, {"match_info": {}}):
            args, requested = match_reply()
            args[4] = data
            with self.assertRaises(career.CareerError):
                career.verify_match_summary_args(args, requested, "fixture-nonce")

    def test_every_row_must_be_a_target_match_player_and_page_is_bounded(self):
        good = {"match_player": {"player_uid": TARGET}}
        args, requested = match_reply([good] * 50)
        self.assertEqual(career.verify_match_summary_args(args, requested, "fixture-nonce")["match_count"], 50)
        args[4]["match_info"].append(good)
        with self.assertRaises(career.CareerError):
            career.verify_match_summary_args(args, requested, "fixture-nonce")
        for bad in (None, [], {}, {"match_player": None}, {"match_player": {}},
                    *({"match_player": {"player_uid": uid}} for uid in
                      (career.login.EXPECTED_OWN_UID, TARGET + 1, str(TARGET), True, None))):
            args, requested = match_reply([good, bad])
            with self.subTest(bad=bad), self.assertRaises(career.CareerError):
                career.verify_match_summary_args(args, requested, "fixture-nonce")

    def test_server_error_preserves_only_code_and_never_becomes_empty_history(self):
        args, requested = match_reply([])
        args[2:5] = [123, "fixture-private-message", {"token": "fixture-never-export"}]
        result = career.verify_match_summary_args(args, requested, "fixture-nonce")
        self.assertFalse(result["match_summary_verified"])
        self.assertEqual(result["server_error_code"], 123)
        self.assertNotIn("match_count", result)
        self.assertNotIn("fixture-", json.dumps(result))

    def read_summary(self, incoming, **kwargs):
        sock = FakeSocket(incoming)
        with patch.object(career.secrets, "token_hex", return_value="fixture-nonce"):
            result = career.read_one_match_summary(sock, SimpleNamespace(encrypt=lambda v: v, decrypt=lambda v: v),
                {"zone_id": 11001}, METHODS, TARGET, **kwargs)
        self.assertEqual(len(sock.sent), 1)
        return result

    def test_reader_reuses_bounded_lifecycle_handling_and_returns_only_verified_rows(self):
        args, _ = match_reply()
        lifecycle = career.login.encode_body(career.ENTITY_DESTROY, {"eid": 456})
        incoming = struct.pack("<IHB", len(lifecycle) + 7, 65534, 114) + lifecycle
        incoming += rpc_record(999, ["never-execute"]) + rpc_record(METHODS.reply, args)
        received = []
        result = self.read_summary(incoming, own_uid=456, on_verified=received.append)
        self.assertTrue(result["match_summary_verified"])
        self.assertEqual(result["startup_record_count"], 1)
        self.assertEqual(result["other_rpc_count"], 1)
        self.assertEqual(received, [args[4]])
        self.assertFalse(result["remote_code_executed"])
        self.assertFalse(result["secrets_saved"])
        self.assertNotIn("fixture_stat", json.dumps(result))

    def test_reader_empty_and_mismatched_responses_only_publish_on_verified_success(self):
        for valid in (True, False):
            args, _ = match_reply([] if valid else [{"match_player": {"player_uid": 999}}])
            received = []
            result = self.read_summary(rpc_record(METHODS.reply, args), on_verified=received.append)
            self.assertEqual(result["match_summary_verified"], valid)
            self.assertEqual(len(received), int(valid))

    def test_reader_unknown_command_record_limit_and_receive_limit_stop(self):
        result = self.read_summary(struct.pack("<IHB", 7, 65534, 119))
        self.assertEqual(result["outcome"], "unrecognized_match_summary_startup_command")
        self.assertFalse(result["match_summary_verified"])
        result = self.read_summary(rpc_record(999, []) * 128)
        self.assertEqual(result["outcome"], "match_summary_record_bound")
        self.assertFalse(result["match_summary_verified"])
        def oversized(connection, transport, deadline, report, **kwargs):
            self.assertEqual(kwargs["max_size"], 4 * 1024 * 1024)
            report["bytes_received"] = 4 * 1024 * 1024 + 1
            return NexRecord(3, 101, b"")
        with patch.object(career.login, "receive_record", side_effect=oversized):
            result = self.read_summary(b"")
        self.assertEqual(result["outcome"], "match_summary_receive_bound")
        self.assertFalse(result["match_summary_verified"])

    def test_reader_projection_failure_is_not_verified_and_text_is_redacted(self):
        args, _ = match_reply()
        def reject(_data):
            raise ValueError("fixture-never-export")
        result = self.read_summary(rpc_record(METHODS.reply, args), on_verified=reject)
        self.assertFalse(result["match_summary_verified"])
        self.assertEqual(result["outcome"], "match_summary_reply_unvalidated")
        self.assertNotIn("fixture-", json.dumps(result))


class GameCareerProbeTest(unittest.TestCase):
    def test_rpc_index_uses_ascii_hex_words_not_binary_digest(self):
        raw = hashlib.md5(b"Career.C_QUERY_CAREERfixture-salt", usedforsecurity=False).hexdigest()[:16].encode()
        a, b, c, d = struct.unpack(">4I", raw)
        product = a * b * c * d
        expected = ((product >> 32) & 0x7FFFFFFF) ^ (product & 0x7FFFFFFF)
        self.assertEqual(career.rpc_index("Career.C_QUERY_CAREER", "fixture-salt"), expected)

    def test_request_matches_native_rpc_envelope_and_pc_career_shape(self):
        request, data = career.make_request(METHODS, TARGET, 11001, 19, "fixture-nonce")
        rpc = decode_avatar_rpc(split_plaintext_records(request)[0], direction="out")
        self.assertEqual(rpc.index, METHODS.request)
        self.assertEqual(rpc.args[0], "query_player_career")
        self.assertEqual(data, {"player_uid": TARGET, "zone_id": 11001, "device_type": "pc", "match_season": 19})
        self.assertEqual(rpc.args[1], rpc.args[2]["career_req"])
        self.assertNotIn("game_mode_id", data)

    def test_all_seasons_omits_season_filter(self):
        _, data = career.make_request(METHODS, TARGET, 11001, 0, "fixture-nonce")
        self.assertNotIn("match_season", data)

    def test_own_invalid_target_zone_or_season_rejected(self):
        for uid, zone, season in ((career.login.EXPECTED_OWN_UID, 1, 19), (True, 1, 19),
                                 (TARGET, 0, 19), (TARGET, 1, -1)):
            with self.assertRaises(career.CareerError):
                career.make_request(METHODS, uid, zone, season, "fixture-nonce")

    def test_valid_reply_is_bound_to_requested_other_player(self):
        args, requested = reply()
        result = career.verify_career_args(args, requested, "fixture-nonce")
        self.assertTrue(result["career_verified"])
        self.assertEqual(result["target_uid"], TARGET)

    def test_wrong_nonce_season_or_method_rejected(self):
        for change in ("nonce", "season", "method"):
            args, requested = reply()
            requested = dict(requested)
            if change == "nonce":
                args[1]["mak_request_id"] = "another-request"
            elif change == "season":
                args[1]["career_req"]["match_season"] = 18
            else:
                args[0] = "query_my_player_career"
            with self.assertRaises(career.CareerError):
                career.verify_career_args(args, requested, "fixture-nonce")

    def test_own_career_or_other_uid_cannot_fill_target_card(self):
        for uid in (career.login.EXPECTED_OWN_UID, "1822797559", None):
            args, requested = reply()
            args[4]["career"]["player_uid"] = uid
            with self.assertRaisesRegex(career.CareerError, "career_uid_mismatch"):
                career.verify_career_args(args, requested, "fixture-nonce")

    def test_server_privacy_or_unavailable_error_never_becomes_statistics(self):
        args, requested = reply()
        args[2:5] = [123, "fixture-private-message", {}]
        result = career.verify_career_args(args, requested, "fixture-nonce")
        self.assertFalse(result["career_verified"])
        self.assertEqual(result["server_error_code"], 123)
        self.assertNotIn("fixture-private-message", json.dumps(result))

    def test_other_avatar_initialization_rpcs_are_not_executed(self):
        args, _ = reply()
        incoming = rpc_record(999, ["do-not-execute"]) + rpc_record(METHODS.reply, args)
        sock = FakeSocket(incoming)
        with patch.object(career.secrets, "token_hex", return_value="fixture-nonce"):
            result = career.read_one_career(sock, SimpleNamespace(encrypt=lambda v: v, decrypt=lambda v: v),
                                           {"zone_id": 11001}, METHODS, TARGET, 19)
        self.assertTrue(result["career_verified"])
        self.assertEqual(result["other_rpc_count"], 1)
        self.assertEqual(len(sock.sent), 1)
        self.assertFalse(result["remote_code_executed"])

    def test_unrecognized_startup_command_stops_without_more_sends(self):
        sock = FakeSocket(struct.pack("<IHB", 7, 65534, 119))
        result = career.read_one_career(sock, SimpleNamespace(encrypt=lambda v: v, decrypt=lambda v: v),
                                       {"zone_id": 11001}, METHODS, TARGET, 19)
        self.assertEqual(result["outcome"], "unrecognized_career_startup_command")
        self.assertFalse(result["career_verified"])
        self.assertEqual(len(sock.sent), 1)

    def test_known_entity_lifecycle_and_ack_do_not_become_target_stats(self):
        report = {"own_entity_created": False, "startup_record_count": 0}
        rows = [(114, career.ENTITY_DESTROY, {"eid": career.login.EXPECTED_OWN_UID}),
                (113, career.ENTITY_CREATE, {"eid": career.login.EXPECTED_OWN_UID,
                                            "classname": "Avatar", "initdata": b"own-data-not-target"}),
                (111, career.MSG_ACK, {"ack": 7}),
                (120, career.ENTITY_PROP, {"entity_id": career.login.EXPECTED_OWN_UID, "sync_msg": b"own-stats"})]
        for sub, schema, value in rows:
            record = NexRecord(65534, sub, career.login.encode_body(schema, value))
            self.assertTrue(career.consume_startup_record(record, report))
        self.assertTrue(report["own_entity_created"])
        self.assertEqual(report["startup_record_count"], 4)
        self.assertNotIn("own-data-not-target", json.dumps(report))
        self.assertNotIn("career_verified", report)

    def test_sequence_reset_is_not_silently_discarded(self):
        record = NexRecord(65534, 112, career.login.encode_body(career.MSG_RESET, {"reset": 8}))
        with self.assertRaisesRegex(career.CareerError, "server_sequence_reset_requires_handling"):
            career.consume_startup_record(record, {})

    def test_missing_or_bad_entity_metadata_is_rejected(self):
        for value in ({"eid": 0}, {}):
            record = NexRecord(65534, 114, career.login.encode_body(career.ENTITY_DESTROY, value))
            with self.assertRaises(career.CareerError):
                career.consume_startup_record(record, {"own_entity_created": False, "startup_record_count": 0})

    def test_large_entity_blob_requires_explicit_larger_decode_bound(self):
        body = b"\x81" + struct.pack("<q", 123) + b"\xe2" + struct.pack("<I", 5) + b"Class"
        content = b"x" * (career.login.MAX_BODY + 1)
        body += b"\xe3" + struct.pack("<I", len(content)) + content
        with self.assertRaises(career.login.LoginError):
            career.login.decode_body(career.ENTITY_CREATE, body)
        report = {"own_entity_created": False, "startup_record_count": 0}
        self.assertTrue(career.consume_startup_record(NexRecord(65534, 113, body), report))

    def test_preview_reads_no_inputs_and_sends_nothing(self):
        with patch.object(career, "load_career_methods") as methods, \
                patch.object(career.login, "load_own_login_inputs") as inputs, \
                patch.object(career.login, "advertised_server") as network, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(career.main(["--uid", str(TARGET)]), 0)
        methods.assert_not_called()
        inputs.assert_not_called()
        network.assert_not_called()
        self.assertFalse(json.loads(out.getvalue())["network_attempted"])

    def test_payload_sink_runs_only_for_verified_target(self):
        for success in (True, False):
            args, _ = reply()
            if not success:
                args[4]["career"]["player_uid"] = 999
            sock = FakeSocket(rpc_record(METHODS.reply, args))
            received = []
            with patch.object(career.secrets, "token_hex", return_value="fixture-nonce"):
                result = career.read_one_career(sock, SimpleNamespace(encrypt=lambda v: v, decrypt=lambda v: v),
                    {"zone_id": 11001}, METHODS, TARGET, 19, on_verified=received.append)
            self.assertEqual(result["career_verified"], success)
            self.assertEqual(len(received), int(success))
            self.assertNotIn("fixture_stat", json.dumps(result))

    def test_configured_own_uid_is_not_hardcoded_for_app_requests(self):
        with self.assertRaises(career.CareerError):
            career.make_request(METHODS, 456, 11001, 19, "fixture", own_uid=456)
        _, requested = career.make_request(METHODS, TARGET, 11001, 19, "fixture", own_uid=456)
        self.assertEqual(requested["player_uid"], TARGET)

    def test_shared_reader_career_projection_failure_is_not_verified(self):
        args, _ = reply()
        sock = FakeSocket(rpc_record(METHODS.reply, args))
        def reject(_data):
            raise ValueError("fixture-never-export")
        with patch.object(career.secrets, "token_hex", return_value="fixture-nonce"):
            result = career.read_one_career(sock, SimpleNamespace(encrypt=lambda v: v, decrypt=lambda v: v),
                {"zone_id": 11001}, METHODS, TARGET, 19, on_verified=reject)
        self.assertFalse(result["career_verified"])
        self.assertEqual(result["outcome"], "career_reply_unvalidated")
        self.assertNotIn("fixture-", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
