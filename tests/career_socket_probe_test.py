"""A scripted socket substitutes for all networking. Tokens are synthetic."""
import base64
import json
import struct
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import career_socket_probe as probe
from socialmatrix_capture_probe import RC4Stream, fold_xor, bounded_json
from socialmatrix_capture_probe_test import envelope, packed, response_payload


def fixture_session(**claims_update):
    claims = {"aid": "fixture:123", "exp": int(time.time()) + 3600, "iat": int(time.time()) - 60,
              "tcp": {"addr": "35.236.255.68:12101", "tls": False, "rc4": True}, **claims_update}
    token = 'eyJhbGciOiJIUzI1NiJ9.' + base64.urlsafe_b64encode(packed(claims)).decode().rstrip('=') + '.c3ludGhldGlj'
    return probe.CaptureSession(123, {"payload_type": 1, "accept_encoding": ["uni_lz4"]},
        {"header": {"aid": "fixture:123", "token": token, "version": "1.5.9"}},
        {"service": "xyq-career", "method": "POST", "uri": "query_my_player_career",
         "post_body": base64.b64encode(packed({"player_uid": 123, "zone_id": 1})).decode()},
        b"fixture-parameter", {"player_uid": 123, "k": 36})


class FakeSocket:
    def __init__(self, data):
        self.data, self.sent, self.closed = data, [], False
    def recv(self, size):
        size = min(size, 3)
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk
    def settimeout(self, seconds):
        if seconds <= 0:
            raise AssertionError("Unbounded socket timeout")
    def sendall(self, data):
        self.sent.append(data)
    def close(self):
        self.closed = True


def server_fixture(session, uid=123, bad_setup=False, rank_data=None, search_data=None, modules=None, user_data=None):
    fields = [b"abcd", b"efgh", b"ijkl"]
    hello = struct.pack("<IBB", 24, 1, 0) + b"".join(struct.pack("<H", 4) + f for f in fields)
    stream = RC4Stream(fold_xor(fields[1], session.parameter))
    setup_header = {"aid": "other" if bad_setup else "fixture:123", "rpcid": "setup-id", "streamid": "fixture-stream"}
    reply = response_payload({"career": {"player_uid": uid, "k": 36}})
    if rank_data is not None:
        reply = {**response_payload(rank_data), "service": "xyq-namecard"}
    query_record = (1, 2, envelope({"rpcid": "query-id"}, reply, response=True, compressed=True))
    if search_data is not None:
        query_record = (4, 0, b"\0" + packed({"header": {"rpcid": "query-id"},
                        "body": {"cmd": 16, "str_payload": packed(search_data).decode()}}))
    if user_data is not None:
        query_record = (4, 0, b"\0" + packed({"header": {"rpcid": "query-id"},
                        "body": {"cmd": 8, "str_payload": packed(user_data).decode()}}))
    records = [(0, 0, packed({"code": 0, "content_encoding": "uni_lz4"})),
               (1, 3, envelope(setup_header, {"modules": modules or []}, response=True)), query_record]
    return hello + stream.process(b"".join(struct.pack("<IHB", len(body)+7, a, b) + body for a, b, body in records))


class CareerSocketProbeTest(unittest.TestCase):
    def test_user_read_binds_returned_aid_and_never_relabels_own_identity(self):
        session = fixture_session()
        self.assertEqual(probe.selected_player_user_request(session, "456"), {"aid": "1_456"})
        for aid in ("1_456", "1_123", "2_456", None):
            data = {"client_info": {"aid": aid, "name": "ExampleIGN"}}
            sock = FakeSocket(server_fixture(session, user_data=data, modules=[{"name": "svc-user", "main_cmd": 4}]))
            with patch.object(probe.uuid, "uuid4", side_effect=[SimpleNamespace(hex="setup-id"), SimpleNamespace(hex="query-id")]):
                report, result = probe.probe_socket(session, kind="player_user", target_uid="456",
                                                   connection_factory=Mock(return_value=sock))
            self.assertEqual(report["target_uid_echoed"], aid == "1_456")
            self.assertEqual(result is not None, aid == "1_456")
            self.assertFalse(report["own_uid_matches"])
            self.assertFalse(report["profile_request_sent"])
            self.assertTrue(sock.closed)

    def test_search_request_is_bounded_and_target_overrides_are_rejected(self):
        session = fixture_session()
        self.assertEqual(probe.selected_player_search_request(session, "ExampleIGN"),
                         {"name": "ExampleIGN", "cursor": 0, "limit": 10, "host": 1})
        connection = Mock()
        for name in (None, "", " example", "x\n", "a" * 65):
            with self.assertRaises(ValueError):
                probe.probe_socket(session, kind="player_search", search_name=name, connection_factory=connection)
        with self.assertRaises(ValueError):
            probe.probe_socket(session, search_name="ExampleIGN", connection_factory=connection)
        with self.assertRaises(ValueError):
            probe.probe_socket(session, kind="player_search", search_name="ExampleIGN", target_uid="456", connection_factory=connection)
        connection.assert_not_called()

    def test_search_requires_current_session_named_module_and_correlated_read_reply(self):
        session = fixture_session()
        data = {"users": [{"aid": "1_456", "name": "ExampleIGN"}], "cursor": 0}
        sock = FakeSocket(server_fixture(session, search_data=data, modules=[{"name": "svc-user", "main_cmd": 4}]))
        with patch.object(probe.uuid, "uuid4", side_effect=[SimpleNamespace(hex="setup-id"), SimpleNamespace(hex="query-id")]):
            report, result = probe.probe_socket(session, kind="player_search", search_name="ExampleIGN",
                                               connection_factory=Mock(return_value=sock))
        self.assertEqual(result, data)
        self.assertTrue(report["search_result_received"])
        self.assertFalse(report["own_request_bound"])
        self.assertFalse(report["profile_request_sent"])
        self.assertTrue(sock.closed)
        for modules in ([], [{"name": "other", "main_cmd": 4}], [{"name": "svc-user", "main_cmd": True}],
                        [{"name": "svc-user", "main_cmd": 4}] * 2):
            with self.assertRaises(ValueError):
                probe.user_module_command({"modules": modules})

    def rank_session(self):
        session = fixture_session()
        session.rank_request = {"service": "xyq-namecard", "method": "POST", "uri": "GetData",
            "post_body": base64.b64encode(packed({"host": 1, "id": "1_123", "tag": "player", "fields": "rank_game_season"})).decode()}
        return session

    def test_ranks_are_own_id_and_rank_field_only(self):
        session = self.rank_session()
        self.assertEqual(probe.selected_request(session, "ranks"), session.rank_request)
        with self.assertRaises(ValueError):
            probe.selected_request(session, "ranks", 17)
        for change in [{"host": True}, {"id": "1_456"}, {"tag": "item_list"}, {"fields": "other"}, {"extra": True}]:
            modified = self.rank_session()
            post = bounded_json(base64.b64decode(modified.rank_request["post_body"]))
            modified.rank_request["post_body"] = base64.b64encode(packed({**post, **change})).decode()
            connection = Mock()
            with self.assertRaises(ValueError):
                probe.probe_socket(modified, kind="ranks", connection_factory=connection)
            connection.assert_not_called()

    def test_rank_template_requires_successful_correlated_own_player_capture(self):
        session = self.rank_session()
        request = dict(session.rank_request)
        post = bounded_json(base64.b64decode(request["post_body"]))
        post["fields"] = "completed_achievement_num,gallery_num"
        request["post_body"] = base64.b64encode(packed(post)).decode()
        response = {**response_payload({"gallery_num": 0}), "service": "xyq-namecard"}
        records = {"out": [(0, 0, b"{}"), (1, 2, envelope({"rpcid": "own"}, request))],
            "in": [(0, 0, b"{}"), (1, 2, envelope({"rpcid": "own"}, response, response=True))]}
        selected = probe.captured_rank_request(records, session)
        self.assertEqual(bounded_json(base64.b64decode(selected["post_body"]))["fields"], "rank_game_season")
        records["in"][1] = (1, 2, envelope({"rpcid": "unrelated"}, response, response=True))
        self.assertIsNone(probe.captured_rank_request(records, session))
        post["id"] = "1_456"
        request["post_body"] = base64.b64encode(packed(post)).decode()
        records["out"][1] = (1, 2, envelope({"rpcid": "own"}, request))
        self.assertIsNone(probe.captured_rank_request(records, session))

    def test_rank_reply_is_request_bound_without_claiming_an_echoed_uid(self):
        session = self.rank_session()
        sock = FakeSocket(server_fixture(session, rank_data={"rank_game_season": "{}"}))
        with patch.object(probe.uuid, "uuid4", side_effect=[SimpleNamespace(hex="setup-id"), SimpleNamespace(hex="query-id")]):
            report, result = probe.probe_socket(session, kind="ranks", connection_factory=Mock(return_value=sock))
        self.assertEqual(result, {"rank_game_season": "{}"})
        self.assertTrue(report["own_request_bound"])
        self.assertFalse(report["own_uid_matches"])
        self.assertTrue(sock.closed)

    def test_explicit_player_rank_read_changes_only_target_and_not_session(self):
        session = self.rank_session()
        original = dict(session.rank_request)
        selected = probe.selected_player_rank_request(session, "456")
        self.assertEqual(bounded_json(base64.b64decode(selected["post_body"])),
                         {"host": 1, "id": "1_456", "tag": "player", "fields": "rank_game_season"})
        self.assertEqual(session.rank_request, original)
        self.assertEqual(session.own_uid, 123)
        self.assertEqual(selected["uri"], "GetData")

    def test_player_target_never_overrides_own_career_or_accepts_invalid_input(self):
        connection = Mock()
        for kind in ("profile", "summary", "ranks"):
            with self.assertRaises(ValueError):
                probe.probe_socket(self.rank_session(), kind=kind, target_uid="456", connection_factory=connection)
        for target in (None, True, "", "IGN", "1_456", "0", "-1", "123", "1" * 21):
            with self.assertRaises(ValueError):
                probe.probe_socket(self.rank_session(), kind="player_ranks", target_uid=target, connection_factory=connection)
        with self.assertRaises(ValueError):
            probe.probe_socket(self.rank_session(), kind="player_ranks", target_uid="456", season=17,
                               connection_factory=connection)
        connection.assert_not_called()

    def test_other_player_rank_reply_never_claims_own_identity_or_public_career(self):
        session = self.rank_session()
        sock = FakeSocket(server_fixture(session, rank_data={"rank_game_season": "{}"}))
        with patch.object(probe.uuid, "uuid4", side_effect=[SimpleNamespace(hex="setup-id"), SimpleNamespace(hex="query-id")]):
            report, result = probe.probe_socket(session, kind="player_ranks", target_uid="456",
                                               connection_factory=Mock(return_value=sock))
        self.assertEqual(result, {"rank_game_season": "{}"})
        self.assertEqual(report["outcome"], "player_ranks_received")
        self.assertTrue(report["target_request_bound"])
        self.assertEqual(report["target_uid"], "456")
        for field in ("own_request_bound", "own_uid_matches", "profile_uid_matches", "target_uid_echoed",
                      "career_visibility_verified", "statistics_verified"):
            self.assertFalse(report[field])
        self.assertTrue(sock.closed)

    def test_other_player_rank_read_rejects_a_career_reply(self):
        session = self.rank_session()
        sock = FakeSocket(server_fixture(session))
        with patch.object(probe.uuid, "uuid4", side_effect=[SimpleNamespace(hex="setup-id"), SimpleNamespace(hex="query-id")]):
            report, result = probe.probe_socket(session, kind="player_ranks", target_uid="456",
                                               connection_factory=Mock(return_value=sock))
        self.assertIsNone(result)
        self.assertFalse(report["target_request_bound"])
        self.assertTrue(sock.closed)

    def test_other_player_career_operation_is_separate_and_uid_verified(self):
        session = fixture_session()
        selected = probe.selected_player_career_request(session, "456")
        self.assertEqual(selected["uri"], "query_player_career")
        self.assertEqual(bounded_json(base64.b64decode(selected["post_body"]))["player_uid"], 456)
        self.assertEqual(session.request["uri"], "query_my_player_career")
        for uid in (456, 123):
            sock = FakeSocket(server_fixture(session, uid=uid))
            with patch.object(probe.uuid, "uuid4", side_effect=[SimpleNamespace(hex="setup-id"), SimpleNamespace(hex="query-id")]):
                report, result = probe.probe_socket(session, kind="player_profile", target_uid="456",
                                                   connection_factory=Mock(return_value=sock))
            self.assertEqual(result is not None, uid == 456)
            self.assertEqual(report["target_uid_echoed"], uid == 456)
            self.assertFalse(report["own_request_bound"])
            self.assertFalse(report["profile_uid_matches"])
            self.assertTrue(sock.closed)

    def test_only_observed_seasons_can_change_a_profile_read(self):
        session = fixture_session()
        session.captured_seasons = (19, 17)
        result = probe.selected_request(session, "profile", 17)
        body = bounded_json(base64.b64decode(result["post_body"]))
        self.assertEqual(body["player_uid"], 123)
        self.assertEqual(body["match_season"], 17)
        for season in [True, "17", 18]:
            with self.assertRaises(ValueError):
                probe.selected_request(session, "profile", season)

    def test_summary_selection_is_captured_bounded_and_own_only(self):
        session = fixture_session()
        session.summary_request = {"service": "xyq-career", "method": "POST", "uri": "query_my_match_summary",
            "post_body": base64.b64encode(packed({"player_uid": 123, "page": 0, "page_size": 50})).decode()}
        self.assertIs(probe.selected_request(session, "summary"), session.summary_request)
        with self.assertRaises(ValueError):
            probe.selected_request(session, "summary", 17)
        session.summary_request["post_body"] = base64.b64encode(packed({"player_uid": 123, "page": 0, "page_size": 500})).decode()
        with self.assertRaises(ValueError):
            probe.selected_request(session, "summary")

    def test_summary_response_requires_own_participant_in_every_row(self):
        own = {"match_info": [{"match_player": {"player_uid": 123}}]}
        self.assertTrue(probe.summary_identity_matches(own, 123))
        self.assertFalse(probe.summary_identity_matches(own, 456))
        self.assertFalse(probe.summary_identity_matches({"match_info": [{}]}, 123))

    def call(self, session, data):
        sock = FakeSocket(data)
        factory = Mock(return_value=sock)
        with patch.object(probe.uuid, "uuid4", side_effect=[SimpleNamespace(hex="setup-id"), SimpleNamespace(hex="query-id")]):
            report, result = probe.probe_socket(session, connection_factory=factory)
        factory.assert_called_once_with(probe.PEER, timeout=10)
        self.assertTrue(sock.closed)
        return report, result, sock

    def test_success_is_bounded_correlated_and_nondisclosing(self):
        session = fixture_session()
        report, result, sock = self.call(session, server_fixture(session))
        self.assertEqual(report["outcome"], "own_profile_received")
        self.assertTrue(report["profile_uid_matches"])
        self.assertFalse(report["statistics_verified"])
        self.assertEqual(result["career"]["k"], 36)
        self.assertEqual(len(sock.sent), 4)  # opening ACK, hello, setup, one profile read
        self.assertNotIn(session.setup["header"]["token"], json.dumps(report))
        self.assertNotIn('fixture-stream', json.dumps(report))
        self.assertNotIn('fixture-parameter', repr(session))

    def test_bad_session_time_or_routing_never_connects(self):
        for changes in [{"exp": 1}, {"iat": int(time.time())+999}, {"nbf": True},
                        {"aid": "fixture:1234"}, {"tcp": {"addr": "203.0.113.2:12101", "tls": False, "rc4": True}}]:
            connection = Mock()
            with self.assertRaises(ValueError):
                probe.probe_socket(fixture_session(**changes), connection_factory=connection)
            connection.assert_not_called()

    def test_other_operation_or_identity_never_connects(self):
        for key, value in [("uri", "sort_my_hero"), ("method", "GET"), ("post_body", base64.b64encode(packed({"player_uid": 456})).decode())]:
            session = fixture_session()
            session.request[key] = value
            connection = Mock()
            with self.assertRaises(ValueError):
                probe.probe_socket(session, connection_factory=connection)
            connection.assert_not_called()

    def test_login_identity_failure_sends_no_profile_request(self):
        session = fixture_session()
        report, result, sock = self.call(session, server_fixture(session, bad_setup=True))
        self.assertFalse(report["profile_request_sent"])
        self.assertIsNone(result)
        self.assertEqual(len(sock.sent), 3)

    def test_wrong_response_uid_not_verified(self):
        session = fixture_session()
        report, result, _ = self.call(session, server_fixture(session, uid=456))
        self.assertFalse(report["profile_uid_matches"])
        self.assertTrue(report["profile_request_sent"])
        self.assertIsNone(result)

    def test_connection_failure_is_not_retried(self):
        connection = Mock(side_effect=OSError("fixture-secret-server-text"))
        report, data = probe.probe_socket(fixture_session(), connection_factory=connection)
        connection.assert_called_once()
        self.assertIsNone(data)
        self.assertEqual(report["stage"], "connect")
        self.assertNotIn('secret', json.dumps(report))

    def test_truncation_exits_and_closes(self):
        report, result, _ = self.call(fixture_session(), b"\x18\0")
        self.assertIsNone(result)
        self.assertFalse(report["session_accepted"])

    def test_wire_deadline_and_size_limit(self):
        wire = probe.Wire(FakeSocket(b"x"), seconds=20, monotonic=Mock(side_effect=[1, 22]))
        with self.assertRaises(TimeoutError):
            wire.exact(1)
        wire = probe.Wire(FakeSocket(b""))
        with self.assertRaises(ValueError):
            wire.exact(probe.MAX_STREAM_BYTES + 1)
