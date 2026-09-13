"""Mock-only same-connection Career composition and independent permission gates."""
import copy
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import msgpack

import standalone_native_sections as native
from nex_rpc_probe import decode_avatar_rpc, split_plaintext_records
from tests.game_login_probe_test import FakeSocket
from tests.standalone_career_query_test import payload
from tests.standalone_match_query_test import match, ranked


def setup():
    return {"social_session_accepted": True, "setup_consumer_completed": True, "mesh_request_bound": True,
            "profile_lookup": {"identity_verified": True, "uid": "123", "is_owner": False,
                "requested_platform": "pc", "visibility": {
                    "overview": {"state": "allowed"}, "history": {"state": "restricted"}}}}


class StandaloneNativeSectionsTest(unittest.TestCase):
    def run_lookup(self, accepted=None, **changes):
        args = {"connection": object(), "transport": object(), "zone": {"role_uid": 456, "zone_id": 42},
                "methods": native.career.CareerMethods(1, 2), "setup_report": setup() if accepted is None else accepted,
                "target_uid": 123}
        args.update(changes)
        return native.read_other_career_once(**args)

    def test_exact_target_and_reply_plus_projection_required(self):
        def read(conn, transport, zone, methods, uid, season, **kwargs):
            self.assertEqual((uid, season, kwargs["own_uid"]), (123, 0, 456))
            kwargs["on_verified"](payload()["data"])
            return {"career_verified": True, "target_uid": 123, "season": 0, "bytes_received": 101,
                    "private": "SECRET"}
        with patch.object(native.career, "read_one_career", side_effect=read) as reader:
            result = self.run_lookup()
        reader.assert_called_once()
        self.assertTrue(result["career_verified"])
        self.assertTrue(result["request_bound"])
        self.assertEqual(result["uid"], "123")
        self.assertEqual(result["statistics"]["matches"], 100)
        self.assertEqual(result["bytes_received"], 101)
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertFalse(result["matches_verified"])

    def test_failed_provenance_or_wrong_uid_prevents_any_request(self):
        cases = []
        for key in ("social_session_accepted", "setup_consumer_completed", "mesh_request_bound"):
            value = setup()
            value[key] = False
            cases.append(value)
        for key, val in (("identity_verified", False), ("uid", "999"), ("is_owner", True),
                         ("requested_platform", "xbox")):
            value = setup()
            value["profile_lookup"][key] = val
            cases.append(value)
        with patch.object(native.career, "read_one_career") as reader:
            for value in cases:
                result = self.run_lookup(value)
                self.assertFalse(result["request_attempted"])
            for target in (0, True, "123", 456, 1 << 63):
                self.assertFalse(self.run_lookup(target_uid=target)["request_attempted"])
        reader.assert_not_called()

    def test_overview_and_history_are_independent(self):
        with patch.object(native.career, "read_one_career") as reader:
            for state in ("restricted", "unknown", None):
                value = setup()
                value["profile_lookup"]["visibility"] = {"overview": {"state": state},
                                                        "history": {"state": "allowed"}}
                result = self.run_lookup(value)
                self.assertTrue(result["identity_verified"])
                self.assertFalse(result["request_attempted"])
                self.assertFalse(result["career_verified"])
        reader.assert_not_called()

    def test_no_projection_or_failed_native_reply_cannot_claim_success(self):
        for emit, report in ((False, {"career_verified": True, "target_uid": 123, "season": 0}),
                             (True, {"career_verified": False, "server_error_code": 5}),
                             (True, {"career_verified": True, "target_uid": 999, "season": 0}),
                             (True, {"career_verified": True, "target_uid": 123, "season": 19})):
            def read(*args, **kwargs):
                if emit:
                    kwargs["on_verified"](payload()["data"])
                return copy.deepcopy(report)
            with patch.object(native.career, "read_one_career", side_effect=read):
                result = self.run_lookup()
            self.assertFalse(result["career_verified"])
            self.assertTrue(result["identity_verified"])
            self.assertNotIn("statistics", result)

    def test_projection_rejects_wrong_uid_and_missing_or_impossible_counts(self):
        for changes in ({"player_uid": 456}, {"k": None}, {"a": True}, {"total_win_count": 101}):
            def read(*args, **kwargs):
                kwargs["on_verified"](payload(**changes)["data"])
                return {"career_verified": True, "target_uid": 123, "season": 0}
            with patch.object(native.career, "read_one_career", side_effect=read):
                result = self.run_lookup()
            self.assertFalse(result["career_verified"])
            self.assertNotIn("statistics", result)
        with patch.object(native.career, "read_one_career", side_effect=RuntimeError("SECRET")):
            self.assertNotIn("SECRET", json.dumps(self.run_lookup()))

    def test_real_native_reader_preserves_projection_failure_and_fixed_rpc_diagnostics(self):
        methods = native.career.CareerMethods(1, 2)
        requested = {"player_uid": 123, "zone_id": 42, "device_type": "pc"}
        cases = (({"k": None}, "fixture-nonce", 0, "other_career_statistics_shape", "other_player_career_verified"),
                 ({}, "wrong-nonce", 0, "other_career_reply_unverified", "career_request_context_mismatch"),
                 ({}, "fixture-nonce", 429, "other_career_reply_unverified", "career_server_error"),
                 ({}, "fixture-nonce", 0, "other_career_verified", "other_player_career_verified"))
        for changes, echoed_nonce, status, outcome, native_outcome in cases:
            context = {"is_career": True, "career_req": dict(requested), "mak_request_id": echoed_nonce}
            arguments = ["query_player_career", context, status, "PRIVATE_SERVER_TEXT", payload(**changes)["data"]]
            packed = msgpack.packb(arguments, use_bin_type=True)
            body = b"\x61" + struct.pack("<I", methods.reply) + b"\xe2" + struct.pack("<I", len(packed)) + packed
            incoming = struct.pack("<IHB", len(body) + 7, 3, 101) + body
            connection = FakeSocket(incoming)
            transport = SimpleNamespace(encrypt=lambda value: value, decrypt=lambda value: value)
            with self.subTest(outcome=outcome), \
                    patch.object(native.career.secrets, "token_hex", return_value="fixture-nonce"), \
                    patch.object(native.career, "read_one_career", wraps=native.career.read_one_career) as reader:
                result = self.run_lookup(connection=connection, transport=transport, methods=methods)
            reader.assert_called_once()
            self.assertEqual(len(connection.sent), 1)
            sent = decode_avatar_rpc(split_plaintext_records(connection.sent[0])[0], direction="out")
            self.assertEqual(sent.index, methods.request)
            self.assertEqual(sent.args[0:2], ["query_player_career", requested])
            self.assertEqual(sent.args[2]["mak_request_id"], "fixture-nonce")
            self.assertEqual(result["outcome"], outcome)
            self.assertEqual(result["native_outcome"], native_outcome)
            self.assertEqual(result["career_verified"], outcome == "other_career_verified")
            self.assertTrue(result["identity_verified"])
            self.assertEqual(result["records_received"], 1)
            self.assertTrue(result["request_attempted"])
            self.assertTrue(result["request_sent"])
            correlated = native_outcome in ("other_player_career_verified", "career_server_error")
            self.assertEqual(result["reply_context_verified"], correlated)
            self.assertEqual(result["connection_reuse_allowed"], correlated)
            self.assertNotIn("PRIVATE", json.dumps(result))
            if status:
                self.assertEqual(result["server_error_code"], status)
            if outcome != "other_career_verified":
                self.assertNotIn("statistics", result)
                self.assertIsNot(result.get("request_bound"), True)


class StandaloneNativeMatchSectionsTest(unittest.TestCase):
    def run_lookup(self, accepted=None, **changes):
        if accepted is None:
            accepted = setup()
            # History has its own gate; a restricted overview is not a veto.
            accepted["profile_lookup"]["visibility"] = {
                "overview": {"state": "restricted"}, "history": {"state": "allowed"}}
        args = {"connection": object(), "transport": object(),
                "zone": {"role_uid": 456, "zone_id": 42},
                "methods": native.career.CareerMethods(1, 2),
                "setup_report": accepted, "target_uid": 123}
        args.update(changes)
        return native.read_other_matches_once(**args)

    def test_history_public_overview_private_uses_only_one_match_reader(self):
        connection, transport = object(), object()

        def read(conn, wire, zone, methods, uid, **kwargs):
            self.assertIs(conn, connection)
            self.assertIs(wire, transport)
            self.assertEqual((zone, uid, kwargs["own_uid"]),
                             ({"role_uid": 456, "zone_id": 42}, 123, 456))
            kwargs["on_verified"]({"match_info": [ranked()]})
            return {"match_summary_verified": True, "target_uid": 123,
                    "page": 0, "page_size": 50, "match_count": 1,
                    "bytes_received": 101, "private": "PRIVATE_NATIVE"}

        with patch.object(native.career, "read_one_match_summary", side_effect=read) as reader, \
                patch.object(native.career, "read_one_career") as career_reader:
            result = self.run_lookup(connection=connection, transport=transport)
        reader.assert_called_once()
        career_reader.assert_not_called()
        self.assertTrue(result["request_attempted"])
        self.assertTrue(result["request_bound"])
        self.assertTrue(result["matches_verified"])
        self.assertFalse(result["career_verified"])
        self.assertFalse(result["ranks_verified"])
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["matches"][0]["kills"], 36)
        self.assertEqual(result["rank_change_count"], 1)
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_private_or_unknown_history_blocks_io_even_with_public_overview(self):
        values = []
        for permission in ({"state": "restricted"}, {"state": "unknown"},
                           {"state": "public"}, {}, None):
            accepted = setup()
            accepted["profile_lookup"]["visibility"]["history"] = permission
            values.append(accepted)
        missing = setup()
        del missing["profile_lookup"]["visibility"]["history"]
        values.append(missing)
        with patch.object(native.career, "read_one_match_summary") as reader, \
                patch.object(native.career, "read_one_career") as career_reader:
            for accepted in values:
                result = self.run_lookup(accepted)
                self.assertEqual(result["outcome"], "other_match_summary_history_not_available")
                self.assertTrue(result["identity_verified"])
                self.assertFalse(result["request_attempted"])
                self.assertFalse(result["matches_verified"])
        reader.assert_not_called()
        career_reader.assert_not_called()

    def test_owner_wrong_target_or_unverified_setup_never_attempts_match_io(self):
        cases = []
        for key in ("social_session_accepted", "setup_consumer_completed", "mesh_request_bound"):
            accepted = setup()
            accepted[key] = False
            cases.append(accepted)
        for key, value in (("uid", "999"), ("uid", 123), ("identity_verified", False),
                           ("is_owner", True), ("is_owner", None), ("requested_platform", "xbox")):
            accepted = setup()
            accepted["profile_lookup"][key] = value
            cases.append(accepted)
        with patch.object(native.career, "read_one_match_summary") as reader:
            for accepted in cases:
                result = self.run_lookup(accepted)
                self.assertEqual(result["outcome"], "other_match_summary_prerequisites_unverified")
                self.assertFalse(result["request_attempted"])
            for target in (456, 0, -1, True, "123", 1 << 63):
                self.assertFalse(self.run_lookup(target_uid=target)["request_attempted"])
            for methods in (None, native.career.CareerMethods(True, 2), native.career.CareerMethods(1, 0)):
                self.assertFalse(self.run_lookup(methods=methods)["request_attempted"])
        reader.assert_not_called()

    def test_verified_target_page_size_and_projected_count_must_all_agree(self):
        good = {"match_summary_verified": True, "target_uid": 123,
                "page": 0, "page_size": 50, "match_count": 1}
        changes = [{"target_uid": value} for value in (999, "123", True)]
        changes += [{"page": value} for value in (1, False, "0")]
        changes += [{"page_size": value} for value in (49, "50", 50.0)]
        changes += [{"match_count": value} for value in (0, 2, True, "1")]
        changes += [{"match_summary_verified": False}, {"match_summary_verified": 1}]
        for change in changes:
            def read(*args, **kwargs):
                kwargs["on_verified"]({"match_info": [match()]})
                return {**good, **change}

            with self.subTest(change=change), patch.object(native.career, "read_one_match_summary", side_effect=read):
                result = self.run_lookup()
            self.assertEqual(result["outcome"], "other_match_summary_reply_unverified")
            self.assertFalse(result["matches_verified"])
            self.assertNotIn("matches", result)
            self.assertIsNot(result.get("request_bound"), True)
        with patch.object(native.career, "read_one_match_summary", return_value=good):
            result = self.run_lookup()
        self.assertFalse(result["matches_verified"])
        self.assertNotIn("matches", result)

    def test_real_native_match_reader_projection_failures_and_empty_page(self):
        methods = native.career.CareerMethods(1, 2)
        requested = {"player_uid": 123, "zone_id": 42, "page": 0, "page_size": 50}
        bad_stats = match()
        bad_stats["match_player"]["k"] = None
        wrong_uid = match()
        wrong_uid["match_player"]["player_uid"] = 456
        cases = [
            ([match()], {}, "fixture-nonce", 0, "match_summary_verified", "other_player_match_summary_verified"),
            ([], {}, "fixture-nonce", 0, "match_summary_verified", "other_player_match_summary_verified"),
            ([bad_stats], {}, "fixture-nonce", 0, "match_statistics_shape", "other_player_match_summary_verified"),
            ([wrong_uid], {}, "fixture-nonce", 0, "other_match_summary_reply_unverified", "match_summary_uid_mismatch"),
            ([match()], {}, "wrong-nonce", 0, "other_match_summary_reply_unverified", "match_summary_request_context_mismatch"),
            ([match()], {"page": 1}, "fixture-nonce", 0, "other_match_summary_reply_unverified", "match_summary_request_context_mismatch"),
            ([match()], {"page_size": 49}, "fixture-nonce", 0, "other_match_summary_reply_unverified", "match_summary_request_context_mismatch"),
            ([match()], {"player_uid": 999}, "fixture-nonce", 0, "other_match_summary_reply_unverified", "match_summary_request_context_mismatch"),
            ([match()], {}, "fixture-nonce", 429, "other_match_summary_reply_unverified", "match_summary_server_error"),
            ([match()] * 51, {}, "fixture-nonce", 0, "other_match_summary_reply_unverified", "match_summary_rows_shape"),
        ]
        for rows, request_change, echoed_nonce, status, outcome, native_outcome in cases:
            context = {"is_career": True, "career_req": {**requested, **request_change},
                       "mak_request_id": echoed_nonce}
            args = ["query_match_summary", context, status, "PRIVATE_SERVER_TEXT",
                    {"match_info": rows, "private": "PRIVATE_CALLBACK"}]
            packed = msgpack.packb(args, use_bin_type=True)
            body = b"\x61" + struct.pack("<I", methods.reply) + b"\xe2" + struct.pack("<I", len(packed)) + packed
            connection = FakeSocket(struct.pack("<IHB", len(body) + 7, 3, 101) + body)
            transport = SimpleNamespace(encrypt=lambda value: value, decrypt=lambda value: value)
            with self.subTest(outcome=outcome, native=native_outcome, rows=len(rows)), \
                    patch.object(native.career.secrets, "token_hex", return_value="fixture-nonce"), \
                    patch.object(native.career, "read_one_match_summary", wraps=native.career.read_one_match_summary) as reader:
                result = self.run_lookup(connection=connection, transport=transport, methods=methods)
            reader.assert_called_once()
            self.assertEqual(len(connection.sent), 1)
            sent = decode_avatar_rpc(split_plaintext_records(connection.sent[0])[0], direction="out")
            self.assertEqual(sent.index, methods.request)
            self.assertEqual(sent.args[:2], ["query_match_summary", requested])
            self.assertEqual(sent.args[2]["mak_request_id"], "fixture-nonce")
            self.assertEqual(result["outcome"], outcome)
            self.assertEqual(result["native_outcome"], native_outcome)
            self.assertEqual(result["matches_verified"], outcome == "match_summary_verified")
            self.assertEqual(result["records_received"], 1)
            self.assertTrue(result["request_attempted"])
            self.assertTrue(result["request_sent"])
            correlated = native_outcome in ("other_player_match_summary_verified", "match_summary_server_error")
            self.assertEqual(result["reply_context_verified"], correlated)
            self.assertEqual(result["connection_reuse_allowed"], correlated)
            self.assertNotIn("PRIVATE", json.dumps(result))
            if status:
                self.assertEqual(result["server_error_code"], status)
            if outcome == "match_summary_verified":
                self.assertTrue(result["request_bound"])
                self.assertEqual(result["match_count"], len(rows))
                self.assertEqual(result["history_completeness"], "first_page_only")
                self.assertIsNone(result["has_more"])
                self.assertFalse(result["ranks_verified"])
                if not rows:
                    self.assertEqual(result["matches"], [])
                    self.assertFalse(result["rank_changes_verified"])
            else:
                self.assertNotIn("matches", result)
                self.assertIsNot(result.get("request_bound"), True)

    def test_optional_rank_failure_preserves_verified_basic_matches(self):
        def read(*args, **kwargs):
            kwargs["on_verified"]({"match_info": [ranked(new_score=None)]})
            return {"match_summary_verified": True, "target_uid": 123,
                    "page": 0, "page_size": 50, "match_count": 1}

        with patch.object(native.career, "read_one_match_summary", side_effect=read):
            result = self.run_lookup()
        self.assertTrue(result["matches_verified"])
        self.assertEqual(result["matches"][0]["kills"], 36)
        self.assertIsNone(result["matches"][0]["rank_change"])
        self.assertFalse(result["rank_changes_verified"])

    def test_errors_and_untrusted_native_diagnostics_are_fixed_and_redacted(self):
        with patch.object(native.career, "read_one_match_summary", side_effect=RuntimeError("PRIVATE_EXCEPTION")):
            result = self.run_lookup()
        self.assertEqual(result["outcome"], "other_match_summary_read_failed")
        self.assertNotIn("PRIVATE", json.dumps(result))
        reports = [None, [], {"outcome": "PRIVATE_NATIVE", "server_error_code": "PRIVATE_STATUS",
                              "bytes_received": True, "records_received": -1,
                              "application_bytes_sent": 20 * 1024 * 1024}]
        for report in reports:
            with patch.object(native.career, "read_one_match_summary", return_value=report):
                result = self.run_lookup()
            self.assertEqual(result["outcome"], "other_match_summary_reply_unverified")
            self.assertNotIn("PRIVATE", json.dumps(result))
            self.assertNotIn("server_error_code", result)
            self.assertNotIn("bytes_received", result)
            self.assertNotIn("records_received", result)
            self.assertIsNone(result["application_bytes_sent"])


class NativeSectionReuseTest(unittest.TestCase):
    def invoke(self, kind, *, selected=None, **changes):
        selected = setup() if selected is None else selected
        selected = copy.deepcopy(selected)
        if selected.get("profile_lookup") and "visibility" in selected["profile_lookup"]:
            selected["profile_lookup"]["visibility"].setdefault("history", {"state": "allowed"})
        args = {"connection": object(), "transport": object(), "zone": {"role_uid": 456, "zone_id": 42},
                "methods": native.career.CareerMethods(1, 2), "target_uid": 123, "setup_report": selected}
        args.update(changes)
        operation = native.read_other_career_once if kind == "career" else native.read_other_matches_once
        return operation(**args)

    def available(self):
        selected = setup()
        selected["profile_lookup"]["visibility"] = {"overview": {"state": "allowed"},
                                                      "history": {"state": "allowed"}}
        return selected

    def test_local_prerequisite_and_independent_privacy_skips_do_not_damage_connection(self):
        for kind in ("career", "match_summary"):
            restricted = self.available()
            section = "overview" if kind == "career" else "history"
            restricted["profile_lookup"]["visibility"][section] = {"state": "restricted"}
            with patch.object(native.career, "read_one_career") as aggregate, \
                    patch.object(native.career, "read_one_match_summary") as matches:
                for selected, target in (({}, 123), (restricted, 123), (self.available(), 456)):
                    result = self.invoke(kind, selected=selected, target_uid=target)
                    self.assertFalse(result["request_attempted"])
                    self.assertFalse(result["request_sent"])
                    self.assertFalse(result["reply_context_verified"])
                    self.assertTrue(result["connection_reuse_allowed"])
                    self.assertEqual(result["application_bytes_sent"], 0)
            aggregate.assert_not_called()
            matches.assert_not_called()

    def test_deadline_validation_skips_before_reader_and_forwarding_only_shortens_budget(self):
        for kind, method in (("career", "read_one_career"), ("match_summary", "read_one_match_summary")):
            with patch.object(native.career.time, "monotonic", return_value=100), \
                    patch.object(native.career, method) as reader:
                for deadline in (99, 100, True, "110", float("inf"), float("nan"), 10**1000):
                    result = self.invoke(kind, selected=self.available(), deadline=deadline)
                    self.assertFalse(result["request_attempted"])
                    self.assertFalse(result["reply_context_verified"])
                    self.assertTrue(result["connection_reuse_allowed"])
                reader.assert_not_called()
            returned = {"outcome": kind + "_server_error", "server_error_code": 429,
                        "request_attempted": True, "request_sent": True,
                        "reply_context_verified": True, "connection_reuse_allowed": True,
                        "application_bytes_sent": 123}
            for supplied, expected in ((110, 110), (1000, 115), (None, None)):
                with patch.object(native.career.time, "monotonic", return_value=100), \
                        patch.object(native.career, method, return_value=returned) as reader:
                    result = self.invoke(kind, selected=self.available(), deadline=supplied)
                if supplied is None:
                    self.assertNotIn("deadline", reader.call_args.kwargs)
                else:
                    self.assertEqual(reader.call_args.kwargs["deadline"], expected)
                self.assertTrue(result["reply_context_verified"])
                self.assertTrue(result["connection_reuse_allowed"])
                self.assertFalse(result["career_verified"])
                self.assertFalse(result["matches_verified"])
                self.assertEqual(result["server_error_code"], 429)

    def test_actual_partial_send_is_unknown_and_terminal_in_both_wrappers(self):
        class Partial(FakeSocket):
            def sendall(self, value):
                super().sendall(value)
                raise OSError("PRIVATE_PARTIAL")
        for kind in ("career", "match_summary"):
            sock = Partial(b"")
            wire = SimpleNamespace(encrypt=lambda value: value, decrypt=lambda value: value)
            result = self.invoke(kind, selected=self.available(), connection=sock, transport=wire)
            self.assertTrue(result["request_attempted"])
            self.assertIsNone(result["request_sent"])
            self.assertIsNone(result["application_bytes_sent"])
            self.assertFalse(result["reply_context_verified"])
            self.assertFalse(result["connection_reuse_allowed"])
            self.assertTrue(result["identity_verified"])
            self.assertEqual(len(sock.sent), 1)
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_no_io_reader_result_overrides_dispatch_attempt_but_untyped_metadata_cannot_grant_reuse(self):
        for kind, method in (("career", "read_one_career"), ("match_summary", "read_one_match_summary")):
            returned = {"outcome": kind + "_reply_timeout", "request_attempted": False, "request_sent": False,
                        "application_bytes_sent": 0, "reply_context_verified": False,
                        "connection_reuse_allowed": True}
            with patch.object(native.career, method, return_value=returned):
                result = self.invoke(kind, selected=self.available())
            self.assertFalse(result["request_attempted"])
            self.assertFalse(result["request_sent"])
            self.assertFalse(result["reply_context_verified"])
            self.assertTrue(result["connection_reuse_allowed"])
            for value in (None, [], {"request_attempted": True, "request_sent": None,
                                     "reply_context_verified": True, "connection_reuse_allowed": True},
                          {"request_attempted": True, "request_sent": True,
                           "reply_context_verified": 1, "connection_reuse_allowed": True}):
                with patch.object(native.career, method, return_value=value):
                    result = self.invoke(kind, selected=self.available())
                self.assertFalse(result["reply_context_verified"])
                self.assertFalse(result["connection_reuse_allowed"])

    def test_contradictory_native_success_uid_cannot_grant_reusable_context(self):
        for kind, method in (("career", "read_one_career"), ("match_summary", "read_one_match_summary")):
            returned = {kind + "_verified": True, "request_attempted": True, "request_sent": True,
                        "reply_context_verified": True, "connection_reuse_allowed": True,
                        "target_uid": 999, "season": 0, "page": 0, "page_size": 50, "match_count": 0}
            with patch.object(native.career, method, return_value=returned):
                result = self.invoke(kind, selected=self.available())
            self.assertFalse(result["reply_context_verified"])
            self.assertFalse(result["connection_reuse_allowed"])


if __name__ == "__main__":
    unittest.main()
