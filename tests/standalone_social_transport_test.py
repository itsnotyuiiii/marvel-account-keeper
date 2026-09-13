"""Explicit Python variant, synthetic token and scripted socket only."""
import base64
import json
import struct
import unittest
from unittest.mock import Mock, patch

import standalone_social_session as social
import standalone_social_transport as transport
from socialmatrix_capture_probe import RC4Stream, fold_xor, magic_signature


PARAMETER = base64.b64decode("bmV0ZWFzZW5hc2RhcTIwMjA=")
FIELDS = (b"abcd", b"efgh", b"ijkl")
NOW = 1000


def packed(value):
    return json.dumps(value, separators=(",", ":")).encode()


def session(*, tcp_extra=None, host="8.8.8.8", zone_id=1):
    claims = {"aid": f"{zone_id}_123", "iat": 990, "exp": 1100,
              "tcp": {"addr": host + ":12101", "rc4": True, "tls": False, **(tcp_extra or {})}}
    def part(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")
    token = ".".join(map(part, (packed({"alg": "HS256"}), packed(claims), b"synthetic-signature")))
    return social.parse_gateway_social_token(token, own_uid=123, zone_id=zone_id, now=NOW)


def record(main, sub, body):
    return struct.pack("<IHB", len(body) + 7, main, sub) + body


def reply(*, header=None, body=None, data=None, direct=False):
    header = {"aid": "1_123", "rpcid": "2", "streamid": "fixture-stream", **(header or {})}
    data = {"modules": [{"name": "svc-user", "main_cmd": 4}]} if data is None else data
    content = {"cmd": 1, "str_payload": packed(data).decode()} if direct else \
              {"cmd": 1, "payload": base64.b64encode(packed(data)).decode()}
    return packed({"header": header, "body": {**content, **(body or {})}})


def fixture(*, hello=None, responses=None, ack_command=(0, 0), opening=None):
    opening = (struct.pack("<IBB", 24, 1, 0) + b"".join(struct.pack("<H", 4) + value for value in FIELDS)) if opening is None else opening
    records = record(*ack_command, packed({"code": 0} if hello is None else hello))
    records += b"".join(record(*value) for value in ([(1, 3, reply())] if responses is None else responses))
    return opening + RC4Stream(fold_xor(FIELDS[1], PARAMETER)).process(records)


def mesh_reply(*, header=None, body=None, data=None, direct=False):
    return reply(header={"rpcid": "3", **(header or {})}, body=body,
                 data={"data": [{"aid": "1_456", "name": "PRIVATE_NAME"}]} if data is None else data,
                 direct=direct)


def career_setup(*, command=42, header=None):
    return reply(header=header, data={"modules": [{"name": "mesh", "main_cmd": command}]})


def career_reply(*, rpcid="3", header=None, body=None, data=None, direct=False):
    return reply(header={"rpcid": rpcid, **(header or {})}, body=body,
                 data={"status": 200, "body": "PRIVATE_CAREER_BODY"} if data is None else data,
                 direct=direct)


def rank_reply(*, rpcid="3", header=None, body=None, data=None, direct=False):
    return reply(header={"rpcid": rpcid, **(header or {})}, body=body,
                 data={"service": "xyq-namecard", "status_code": 200,
                       "response_body": "PRIVATE_RANK_BODY"} if data is None else data,
                 direct=direct)


class Socket:
    def __init__(self, data, *, fail_send=None, receive_error=None):
        self.data, self.sent, self.closed = data, [], False
        self.fail_send, self.receive_error = fail_send, receive_error
        self.timeouts = []

    def recv(self, size):
        if self.receive_error is not None:
            raise self.receive_error
        chunk, self.data = self.data[:min(size, 3)], self.data[min(size, 3):]
        return chunk

    def settimeout(self, seconds):
        self.timeouts.append(seconds)
        if not 0 < seconds <= transport.MAX_SECONDS:
            raise AssertionError("Unbounded deadline")

    def sendall(self, data):
        self.sent.append(data)
        if len(self.sent) == self.fail_send:
            raise OSError("synthetic private socket message")

    def close(self):
        self.closed = True


def outgoing(sock):
    raw = RC4Stream(fold_xor(FIELDS[2], PARAMETER)).process(b"".join(sock.sent[1:]))
    rows = []
    while raw:
        size, main, sub = struct.unpack("<IHB", raw[:7])
        rows.append((main, sub, json.loads(raw[7:size])))
        raw = raw[size:]
    return rows


class StandaloneSocialTransportTest(unittest.TestCase):
    def run_setup(self, *, owner=None, wire_data=None, sock=None, parameter=PARAMETER, **kwargs):
        sock = Socket(fixture() if wire_data is None else wire_data) if sock is None else sock
        connector = Mock(return_value=sock)
        result = transport.run_social_setup_once(session() if owner is None else owner,
            parameter=parameter, connect=connector, now=kwargs.pop("now", NOW),
            monotonic=kwargs.pop("monotonic", lambda: 10), **kwargs)
        return result, sock, connector

    def test_complete_explicit_python_variant_only_sends_hello_and_setup(self):
        owner = session(host="1.1.1.1")
        result, sock, connector = self.run_setup(owner=owner)
        connector.assert_called_once_with(("1.1.1.1", 12101), timeout=10)
        self.assertEqual(result["outcome"], "social_setup_verified")
        for key in ("token_sent", "hello_accepted", "setup_request_sent", "setup_rpc_bound",
                    "setup_aid_echoed", "streamid_present", "user_module_available", "social_session_accepted"):
            self.assertIs(result[key], True)
        self.assertEqual(result["setup_reply_command"], 1)
        self.assertEqual(result["module_count"], 1)
        self.assertEqual(result["connections_attempted"], 1)
        self.assertEqual(result["handshake_bytes_sent"], 14)
        self.assertEqual(sock.sent[0], struct.pack("<IH", 14, 4) + magic_signature(FIELDS[0], PARAMETER) + struct.pack("<HH", 0, 0))
        self.assertEqual(outgoing(sock), [
            (0, 0, {"payload_type": 1, "social_python_socket_version": "1.0.0"}),
            (1, 3, {"header": {"version": "3.10.1", "rpcid": "2", "aid": "1_123", "token": owner.token},
                    "body": {"cmd": 0, "payload": "e30="}})])
        self.assertEqual(result["application_bytes_sent"], sum(map(len, sock.sent[1:])))
        self.assertIs(result["stats_requested"], False)
        self.assertIs(result["rank_query_attempted"], False)
        self.assertIs(result["rank_reply_bound"], False)
        self.assertIs(result["secrets_saved"], False)
        self.assertTrue(sock.closed)

    def test_setup_reply_can_use_direct_json_str_payload(self):
        result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(direct=True))]))
        self.assertEqual(result["outcome"], "social_setup_verified")
        self.assertTrue(sock.closed)

    def test_unsupported_parameter_and_signature_mode_fail_before_connection(self):
        for parameter in (b"wrong", b"x" * 17, bytearray(PARAMETER), PARAMETER.decode(), None):
            with self.subTest(parameter=type(parameter).__name__):
                result, _, connector = self.run_setup(parameter=parameter)
                self.assertEqual(result["outcome"], "social_parameter_unreviewed")
                connector.assert_not_called()
                self.assertIs(result["token_sent"], False)
        for value in (True, 1, "true", None):
            result, _, connector = self.run_setup(owner=session(tcp_extra={"Secure.SignatureEnabled": value}))
            self.assertEqual(result["outcome"], "social_transport_signature_unsupported")
            connector.assert_not_called()
        result, _, _ = self.run_setup(owner=session(tcp_extra={"Secure.SignatureEnabled": False}))
        self.assertEqual(result["outcome"], "social_setup_verified")

    def test_bad_context_or_expired_token_never_connects(self):
        result, _, connector = self.run_setup(owner="not a session")
        self.assertEqual(result["outcome"], "social_transport_context")
        connector.assert_not_called()
        result, _, connector = self.run_setup(now=1100)
        self.assertEqual(result["outcome"], "social_token_time")
        connector.assert_not_called()

    def test_token_expiring_during_hello_is_not_sent(self):
        owner = session()
        with patch.object(social.time, "time", side_effect=[1000, 1000, 1100]):
            result, sock, _ = self.run_setup(owner=owner, now=None)
        self.assertEqual(result["outcome"], "social_token_time")
        self.assertTrue(result["hello_accepted"])
        self.assertIs(result["token_sent"], False)
        self.assertEqual(len(sock.sent), 2)
        self.assertTrue(sock.closed)

    def test_opening_modes_and_lengths_are_bounded_before_credentials(self):
        for opening in (struct.pack("<IBB", 24, 2, 0) + b"x" * 18,
                        struct.pack("<IBB", 24, 1, 1) + b"x" * 18,
                        struct.pack("<I", 4097), struct.pack("<I", 10)):
            result, sock, _ = self.run_setup(wire_data=opening)
            self.assertEqual(result["outcome"], "social_opening_invalid")
            self.assertIs(result["token_sent"], False)
            self.assertTrue(sock.closed)

    def test_hello_rejection_and_unsupported_codec_stop_before_setup(self):
        for hello, outcome in (({"code": 11, "message": "private"}, "social_hello_rejected"),
                               ({"code": True}, "social_hello_invalid"),
                               ({}, "social_hello_invalid"),
                               ({"code": 0, "content_encoding": "uni_lz4"}, "social_hello_codec_unsupported"),
                               ({"code": 0, "payload_type": True}, "social_hello_codec_unsupported"),
                               ({"code": 0, "payload_type": 2}, "social_hello_codec_unsupported")):
            result, sock, _ = self.run_setup(wire_data=fixture(hello=hello))
            self.assertEqual(result["outcome"], outcome)
            self.assertIs(result["token_sent"], False)
            self.assertEqual(len(sock.sent), 2)
            self.assertTrue(sock.closed)

    def test_source_default_zero_ack_and_unchecked_ack_command_are_preserved(self):
        result, _, _ = self.run_setup(wire_data=fixture(hello={"payload_type": 1}, ack_command=(10, 5)))
        self.assertEqual(result["outcome"], "social_setup_verified")

    def test_both_header_and_body_status_are_checked_and_messages_are_redacted(self):
        for header, body in (({"errorcode": 401, "errormsg": "PRIVATE_REPLY"}, {}),
                             ({}, {"errorcode": 429, "errormsg": "PRIVATE_REPLY"})):
            result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(header=header, body=body))]))
            self.assertEqual(result["outcome"], "social_setup_rejected")
            self.assertIn(result["server_error_code"], (401, 429))
            self.assertNotIn("PRIVATE_REPLY", json.dumps(result))
            self.assertFalse(result["social_session_accepted"])
            self.assertTrue(sock.closed)

    def test_rpc_correlation_ignores_unrelated_records_without_retry(self):
        replies = [(9, 0, b"unexecuted notification"), (1, 3, reply(header={"rpcid": "other"})), (1, 3, reply())]
        result, sock, connector = self.run_setup(wire_data=fixture(responses=replies))
        self.assertEqual(result["outcome"], "social_setup_verified")
        self.assertEqual(result["ignored_records"], 2)
        self.assertEqual(len(sock.sent), 3)
        self.assertEqual(connector.call_count, 1)
        replies = [(1, 3, reply(header={"rpcid": "other"}))] * transport.MAX_SETUP_RECORDS
        result, sock, connector = self.run_setup(wire_data=fixture(responses=replies))
        self.assertEqual(result["outcome"], "social_setup_correlation_missing")
        self.assertEqual(connector.call_count, 1)
        self.assertTrue(sock.closed)

    def test_exact_aid_if_echoed_and_optional_absence_are_distinct(self):
        for aid in ("1_1234", "2_123", "fixture:123", None, True):
            result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(header={"aid": aid}))]))
            self.assertEqual(result["outcome"], "social_setup_identity_mismatch")
        raw = packed({"header": {"rpcid": "2"}, "body": {"cmd": 1, "payload": "e30="}})
        result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, raw)]))
        self.assertEqual(result["outcome"], "social_setup_verified")
        for key in ("setup_aid_echoed", "streamid_present", "user_module_available"):
            self.assertIs(result[key], False)
        self.assertEqual(result["module_count"], 0)

    def test_stream_and_modules_are_bounded_without_claiming_availability(self):
        for stream in (True, "x" * 513, "bad\nstream"):
            result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(header={"streamid": stream}))]))
            self.assertEqual(result["outcome"], "social_setup_invalid")
        for data in ({"modules": [None]}, {"modules": [{"name": "svc-user", "main_cmd": True}]},
                     {"modules": [{"name": "svc-user", "main_cmd": 65536}]}, {"modules": {}},
                     {"modules": []}, []):
            # Empty modules is valid and deliberately reports no service availability.
            result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(data=data))]))
            self.assertEqual(result["outcome"], "social_setup_verified" if data == {"modules": []} else "social_setup_invalid")
        data = {"modules": [{"name": str(i), "main_cmd": i + 1} for i in range(101)]}
        result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(data=data))]))
        self.assertEqual(result["outcome"], "social_setup_invalid")

    def test_optional_null_stream_and_modules_match_source_absent_semantics(self):
        for header, data in (({"streamid": None}, {}), ({"streamid": ""}, {"modules": None}),
                             ({"streamid": None}, {"modules": None})):
            result, sock, _ = self.run_setup(wire_data=fixture(responses=[
                (1, 3, reply(header=header, data=data))]))
            self.assertEqual(result["outcome"], "social_setup_verified")
            self.assertFalse(result["streamid_present"])
            self.assertFalse(result["user_module_available"])
            self.assertEqual(result["module_count"], 0)
            self.assertTrue(sock.closed)

    def test_plain_json_not_capture_compression_and_invalid_payloads_rejected(self):
        for raw in (b"\0" + reply(), b"\1\x01\x00\x00\x00x", b'{"header":{"rpcid":"2","rpcid":"2"}}'):
            result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, raw)]))
            self.assertEqual(result["outcome"], "social_json_invalid")
        for body in ({"payload": "not-base64"}, {"payload": "bnVsbA=="},
                     {"cmd": True}, {"errorcode": False}):
            result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(body=body))]))
            self.assertEqual(result["outcome"], "social_setup_invalid")
        # A truthy str_payload means the preferred payload is decoded directly,
        # so a base64 string in payload is not valid JSON for that source path.
        result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(body={"str_payload": "{}"}))]))
        self.assertEqual(result["outcome"], "social_json_invalid")
        self.assertEqual(result["validation_reason"], "payload_json_invalid")

    def test_source_precedence_for_both_payload_fields_is_raw_payload_first(self):
        selected = packed({"modules": [{"name": "svc-user", "main_cmd": 4}]}).decode()
        for plain in ("ignored-unparsed-private-text", packed({"modules": []}).decode()):
            result, sock, _ = self.run_setup(wire_data=fixture(responses=[
                (1, 3, reply(body={"payload": selected, "str_payload": plain}))]))
            self.assertEqual(result["outcome"], "social_setup_verified")
            self.assertTrue(result["user_module_available"])
            self.assertEqual(result["setup_shape"]["payload_selected"], "payload")
            self.assertEqual(result["setup_shape"]["payload_encoding"], "direct_json")
            self.assertEqual(len(sock.sent), 3)
            self.assertTrue(sock.closed)
            self.assertNotIn(plain, json.dumps(result))
        for absent in (None, ""):
            result, _, _ = self.run_setup(wire_data=fixture(responses=[
                (1, 3, reply(body={"payload": absent, "str_payload": selected}))]))
            self.assertEqual(result["outcome"], "social_setup_verified")
            self.assertEqual(result["setup_shape"]["payload_selected"], "str_payload")
        result, _, _ = self.run_setup(wire_data=fixture(responses=[
            (1, 3, reply(body={"str_payload": None}))]))
        self.assertEqual(result["outcome"], "social_setup_verified")
        self.assertEqual(result["setup_shape"]["payload_encoding"], "base64")

    def test_source_callback_decodes_nested_json_string_only_once(self):
        data = packed({"modules": [{"name": "svc-user", "main_cmd": 4}]}).decode()
        result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(data=data))]))
        self.assertEqual(result["outcome"], "social_setup_verified")
        shape = result["setup_shape"]
        self.assertEqual(shape["data_type"], "string")
        self.assertTrue(shape["nested_json_decoded"])
        self.assertEqual(shape["nested_data_type"], "object")
        self.assertEqual(result["module_count"], 1)
        result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(data=packed(data).decode()))]))
        self.assertEqual(result["outcome"], "social_setup_invalid")
        self.assertEqual(result["validation_reason"], "data_not_object")
        self.assertEqual(result["setup_shape"]["nested_data_type"], "string")

    def test_source_module_map_skips_falsey_names_allows_zero_and_overwrites(self):
        modules = [{"name": None}, {"name": False, "main_cmd": "ignored-private-value"},
                   {"name": "", "main_cmd": -1}, {"name": 0},
                   {"name": "svc-user", "main_cmd": 4}, {"name": "svc-user", "main_cmd": 0},
                   {"name": "private-module-name"}]
        result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(data={"modules": modules}))]))
        self.assertEqual(result["outcome"], "social_setup_verified")
        self.assertEqual(result["module_count"], 2)
        self.assertFalse(result["user_module_available"])
        self.assertEqual(result["setup_shape"]["modules_count"], 7)
        self.assertEqual(result["setup_shape"]["skipped_modules"], 4)
        self.assertEqual(result["setup_shape"]["duplicate_modules"], 1)
        self.assertEqual(result["setup_shape"]["module_command_type"], "absent")
        self.assertEqual(result["setup_shape"]["module_command_class"], "zero")
        self.assertNotIn("private-module-name", json.dumps(result))
        self.assertNotIn("ignored-private-value", json.dumps(result))
        modules[5]["main_cmd"] = 5
        result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(data={"modules": modules}))]))
        self.assertTrue(result["user_module_available"])

    def test_fixed_diagnostics_distinguish_payload_and_module_failures_without_values(self):
        cases = [
            ({"payload": True}, None, "setup_payload", "payload_type"),
            ({"str_payload": []}, None, "setup_payload", "str_payload_type"),
            ({"payload": "", "str_payload": None}, None, "setup_payload", "payload_missing"),
            ({"payload": "not-base64"}, None, "setup_payload", "payload_base64_invalid"),
            ({"payload": base64.b64encode(b"PRIVATE_NON_JSON").decode()}, None, "setup_payload", "payload_json_invalid"),
            ({}, [], "setup_modules", "data_not_object"),
            ({}, "PRIVATE_NON_JSON", "setup_modules", "data_nested_json_invalid"),
            ({}, {"modules": {}}, "setup_modules", "modules_type"),
            ({}, {"modules": [None]}, "setup_modules", "module_not_object"),
            ({}, {"modules": [{"name": 1}]}, "setup_modules", "module_name_invalid"),
            ({}, {"modules": [{"name": "PRIVATE_NAME", "main_cmd": "PRIVATE_COMMAND"}]}, "setup_modules", "module_command_type"),
            ({}, {"modules": [{"name": "PRIVATE_NAME", "main_cmd": 65536}]}, "setup_modules", "module_command_range"),
        ]
        for body, data, stage, reason in cases:
            with self.subTest(reason=reason):
                result, sock, connector = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(body=body, data=data))]))
                self.assertEqual(result["stage"], stage)
                self.assertEqual(result["validation_reason"], reason)
                self.assertIn(reason, transport.SETUP_VALIDATION_REASONS)
                self.assertFalse(result["social_session_accepted"])
                self.assertTrue(result["setup_rpc_bound"])
                self.assertTrue(result["setup_aid_echoed"])
                self.assertTrue(result["streamid_present"])
                self.assertNotIn("PRIVATE_", json.dumps(result))
                self.assertTrue(sock.closed)
                self.assertEqual(connector.call_count, 1)
                self.assertEqual(len(sock.sent), 3)
                for value in result["setup_shape"].values():
                    self.assertIn(type(value), (str, int, bool, type(None)))

    def test_partial_send_reports_unknown_not_false_unsent(self):
        result, sock, _ = self.run_setup(sock=Socket(fixture(), fail_send=3))
        self.assertEqual(result["outcome"], "social_transport_network_error")
        self.assertIsNone(result["token_sent"])
        self.assertIsNone(result["setup_request_sent"])
        self.assertIsNone(result["application_bytes_sent"])
        self.assertTrue(sock.closed)
        result, sock, _ = self.run_setup(sock=Socket(fixture(), fail_send=1))
        self.assertIsNone(result["handshake_bytes_sent"])
        self.assertIs(result["token_sent"], False)

    def test_response_timeout_and_stream_bounds_close_without_retry(self):
        result, sock, connector = self.run_setup(sock=Socket(b"", receive_error=TimeoutError("PRIVATE_TIMEOUT")))
        self.assertEqual(result["outcome"], "social_transport_timeout")
        self.assertTrue(sock.closed)
        self.assertEqual(connector.call_count, 1)
        result, sock, _ = self.run_setup(wire_data=fixture()[:30])
        self.assertEqual(result["outcome"], "social_connection_closed")
        self.assertTrue(sock.closed)
        result, sock, _ = self.run_setup(monotonic=Mock(side_effect=[0, 21]))
        self.assertEqual(result["outcome"], "social_transport_timeout")
        self.assertTrue(sock.closed)

    def test_no_file_access_or_extra_calls_and_no_secret_in_report(self):
        owner = session()
        with patch("builtins.open", side_effect=AssertionError("file access")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("DNS")):
            result, _, connector = self.run_setup(owner=owner)
        rendered = json.dumps(result)
        for secret in (owner.token, owner.aid, owner.route.host, "fixture-stream"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(connector.call_count, 1)

    def test_optional_mesh_callback_uses_exact_current_request_on_same_connection(self):
        owner, received = session(zone_id=7), []
        rows = {"data": [{"aid": "7_456", "name": "PRIVATE_NAME", "config_server": {}}]}
        data = fixture(responses=[(1, 3, reply(header={"aid": "7_123"})),
                                  (1, 4, mesh_reply(header={"aid": "7_123"}, data=rows))])
        result, sock, connector = self.run_setup(owner=owner, wire_data=data,
            on_setup=lambda context: received.append(context.read_mesh_user(456)))
        self.assertEqual(received, [rows])
        self.assertEqual(result["outcome"], "social_setup_verified")
        for key in ("setup_consumer_attempted", "setup_consumer_completed", "mesh_query_attempted",
                    "mesh_query_sent", "mesh_reply_received", "mesh_request_bound"):
            self.assertIs(result[key], True)
        self.assertEqual(result["mesh_query_count"], 1)
        self.assertIsNone(result["mesh_error"])
        request = outgoing(sock)[2]
        self.assertEqual(request[:2], (1, 4))
        self.assertEqual(request[2]["header"], {"version": "3.10.1", "rpcid": "3",
                                              "aid": "7_123", "streamid": "fixture-stream"})
        self.assertEqual(request[2]["body"]["cmd"], 0)
        self.assertEqual(json.loads(base64.b64decode(request[2]["body"]["payload"])), {"aids": ["7_456"]})
        self.assertEqual(result["mesh_application_bytes_sent"], len(sock.sent[3]))
        self.assertEqual(result["application_bytes_sent"], sum(map(len, sock.sent[1:])))
        self.assertEqual(connector.call_count, 1)
        self.assertEqual(len(sock.sent), 4)
        self.assertTrue(sock.closed)
        self.assertNotIn("PRIVATE_NAME", json.dumps(result))
        self.assertNotIn("7_456", json.dumps(result))
        self.assertNotIn("target_identity_verified", result)
        self.assertFalse(result["stats_requested"])

    def test_mesh_own_uid_uses_same_route_and_token_only_without_streamid(self):
        owner, received = session(), []
        result, sock, _ = self.run_setup(owner=owner, wire_data=fixture(responses=[
            (1, 3, reply(header={"streamid": None})),
            (1, 4, mesh_reply(data={"data": [{"aid": "1_123"}]}))]),
            on_setup=lambda context: received.append(context.read_mesh_user(123)))
        self.assertTrue(result["mesh_reply_received"])
        request = outgoing(sock)[2]
        self.assertEqual(request[2]["header"], {"version": "3.10.1", "rpcid": "3",
                                              "aid": "1_123", "token": owner.token})
        self.assertEqual(json.loads(base64.b64decode(request[2]["body"]["payload"])), {"aids": ["1_123"]})
        self.assertNotIn("ackRpcId", request[2]["header"])
        self.assertNotIn(owner.token, json.dumps(result))
        self.assertEqual(len(sock.sent), 4)

    def test_mesh_response_decoding_remains_source_faithful_without_target_claims(self):
        wrong_target = {"data": [{"aid": "1_999", "name": "PRIVATE_NAME"}]}
        for direct in (False, True):
            received = []
            raw = mesh_reply(data=wrong_target, direct=direct, body={"cmd": 0})
            result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply()), (1, 4, raw)]),
                on_setup=lambda context: received.append(context.read_mesh_user(456)))
            self.assertEqual(received, [wrong_target])  # Exact row binding belongs to the caller's projector.
            self.assertTrue(result["mesh_reply_received"])
            self.assertEqual(result["mesh_reply_command"], 0)
            self.assertNotIn("target_identity_verified", result)
            self.assertNotIn("1_999", json.dumps(result))

    def test_context_is_one_read_and_cannot_escape_callback_lifetime(self):
        contexts = []
        def consume(context):
            contexts.append(context)
            context.read_mesh_user(456)
            with self.assertRaisesRegex(transport.SocialTransportError, "social_mesh_already_consumed"):
                context.read_mesh_user(789)
        result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply()), (1, 4, mesh_reply())]),
                                         on_setup=consume)
        self.assertEqual(result["mesh_query_count"], 1)
        self.assertEqual(len(sock.sent), 4)
        self.assertNotIn("fixture-stream", repr(contexts[0]))
        self.assertNotIn("1_123", repr(contexts[0]))
        saved_report = json.dumps(result, sort_keys=True)
        with self.assertRaisesRegex(transport.SocialTransportError, "social_mesh_context_closed"):
            contexts[0].read_mesh_user(456)
        self.assertEqual(json.dumps(result, sort_keys=True), saved_report)
        self.assertIsNone(contexts[0]._wire)
        self.assertIsNone(contexts[0]._session)
        self.assertIsNone(contexts[0]._streamid)
        self.assertIsNone(contexts[0]._modules)
        self.assertTrue(sock.closed)

    def test_setup_module_routes_stay_private_and_do_not_change_mesh_user_route(self):
        observed = []
        modules = {"modules": [{"name": "mesh", "main_cmd": 42},
                               {"name": "svc-user", "main_cmd": 3}]}
        def consume(context):
            observed.append(dict(context._modules))
            self.assertNotIn("svc-user", repr(context))
            context.read_mesh_user(456)
        result, sock, _ = self.run_setup(wire_data=fixture(responses=[
            (1, 3, reply(data=modules)), (1, 4, mesh_reply())]), on_setup=consume)
        self.assertEqual(observed, [{"mesh": 42, "svc-user": 3}])
        self.assertEqual(result["module_count"], 2)
        self.assertEqual(outgoing(sock)[2][:2], (1, 4))
        self.assertNotIn("svc-user", json.dumps(result))
        self.assertTrue(result["mesh_request_bound"])

    def test_bad_target_is_consumed_without_sending_or_trying_another_uid(self):
        for target in (True, "456", 456.0, 0, -1, None, 2**64):
            contexts = []
            def consume(context):
                contexts.append(context)
                context.read_mesh_user(target)
            result, sock, connector = self.run_setup(on_setup=consume)
            self.assertEqual(result["outcome"], "social_setup_consumer_failed")
            self.assertEqual(result["mesh_error"], "social_mesh_target_invalid")
            self.assertEqual(result["mesh_query_count"], 1)
            self.assertIs(result["mesh_query_sent"], False)
            self.assertFalse(result["setup_consumer_completed"])
            self.assertEqual(len(sock.sent), 3)
            self.assertEqual(connector.call_count, 1)
            self.assertTrue(sock.closed)

    def test_callback_runs_only_after_successful_setup_and_return_value_is_discarded(self):
        consumer = Mock(return_value={"PRIVATE_CALLBACK_RETURN": "secret"})
        result, sock, _ = self.run_setup(on_setup=consumer)
        consumer.assert_called_once()
        self.assertTrue(result["setup_consumer_completed"])
        self.assertFalse(result["mesh_query_attempted"])
        self.assertNotIn("PRIVATE_CALLBACK_RETURN", json.dumps(result))
        self.assertEqual(len(sock.sent), 3)
        consumer = Mock()
        result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply(header={"errorcode": 403}))]),
                                         on_setup=consumer)
        consumer.assert_not_called()
        self.assertFalse(result["setup_consumer_attempted"])
        self.assertTrue(sock.closed)
        result, _, connector = self.run_setup(on_setup=True)
        self.assertEqual(result["outcome"], "social_transport_context")
        connector.assert_not_called()

    def test_consumer_projection_failure_does_not_export_data_or_claim_completion(self):
        contexts = []
        def consume(context):
            contexts.append(context)
            context.read_mesh_user(456)
            raise ValueError("PRIVATE_PROJECTOR_ERROR")
        result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply()), (1, 4, mesh_reply())]),
                                         on_setup=consume)
        self.assertTrue(result["social_session_accepted"])
        self.assertTrue(result["mesh_reply_received"])
        self.assertFalse(result["setup_consumer_completed"])
        self.assertEqual(result["outcome"], "social_setup_consumer_failed")
        self.assertNotIn("PRIVATE_", json.dumps(result))
        self.assertTrue(sock.closed)
        with self.assertRaises(transport.SocialTransportError):
            contexts[0].read_mesh_user(456)

    def test_mesh_headers_bind_owner_not_target_and_both_status_layers_reject(self):
        cases = [(mesh_reply(header={"aid": "1_456"}), "social_mesh_identity_mismatch", None),
                 (mesh_reply(header={"errorcode": 403, "errormsg": "PRIVATE_ERROR"}), "social_mesh_rejected", 403),
                 (mesh_reply(body={"errorcode": 429, "errormsg": "PRIVATE_ERROR"}), "social_mesh_rejected", 429),
                 (mesh_reply(body={"errorcode": True}), "social_mesh_reply_invalid", None)]
        for raw, error, code in cases:
            result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, reply()), (1, 4, raw)]),
                on_setup=lambda context: context.read_mesh_user(456))
            self.assertEqual(result["mesh_error"], error)
            self.assertEqual(result["mesh_server_error_code"], code)
            self.assertFalse(result["mesh_reply_received"])
            self.assertFalse(result["mesh_request_bound"])
            self.assertFalse(result["setup_consumer_completed"])
            self.assertNotIn("PRIVATE_", json.dumps(result))
            self.assertTrue(sock.closed)

    def test_mesh_correlates_rpc_and_never_retries_when_missing(self):
        responses = [(1, 3, reply()), (9, 0, b"unexecuted"),
                     (1, 4, mesh_reply(header={"rpcid": "2"})), (1, 4, mesh_reply())]
        result, sock, connector = self.run_setup(wire_data=fixture(responses=responses),
            on_setup=lambda context: context.read_mesh_user(456))
        self.assertTrue(result["mesh_reply_received"])
        self.assertEqual(result["mesh_ignored_records"], 2)
        self.assertEqual(len(sock.sent), 4)
        self.assertEqual(connector.call_count, 1)
        responses = [(1, 3, reply())] + [(1, 4, mesh_reply(header={"rpcid": "other"}))] * transport.MAX_SETUP_RECORDS
        result, sock, connector = self.run_setup(wire_data=fixture(responses=responses),
            on_setup=lambda context: context.read_mesh_user(456))
        self.assertEqual(result["mesh_error"], "social_mesh_correlation_missing")
        self.assertFalse(result["mesh_request_bound"])
        self.assertEqual(len(sock.sent), 4)
        self.assertEqual(connector.call_count, 1)
        self.assertTrue(sock.closed)

    def test_mesh_partial_send_preserves_setup_success_and_reports_unknown_query_send(self):
        result, sock, _ = self.run_setup(sock=Socket(fixture(), fail_send=4),
                                         on_setup=lambda context: context.read_mesh_user(456))
        self.assertEqual(result["mesh_error"], "social_transport_network_error")
        self.assertIsNone(result["mesh_query_sent"])
        self.assertFalse(result["mesh_request_bound"])
        self.assertIsNone(result["mesh_application_bytes_sent"])
        self.assertIsNone(result["application_bytes_sent"])
        self.assertIs(result["token_sent"], True)
        self.assertIs(result["setup_request_sent"], True)
        self.assertTrue(result["social_session_accepted"])
        self.assertFalse(result["setup_consumer_completed"])
        self.assertTrue(sock.closed)

    def test_mesh_shares_connection_deadline_and_rechecks_token_before_send(self):
        clock = [10]
        def late(context):
            clock[0] = 31
            context.read_mesh_user(456)
        result, sock, _ = self.run_setup(monotonic=lambda: clock[0], on_setup=late)
        self.assertEqual(result["mesh_error"], "social_transport_timeout")
        self.assertIs(result["mesh_query_sent"], False)
        self.assertEqual(len(sock.sent), 3)
        self.assertTrue(sock.closed)
        epoch, owner = [NOW], session()
        def expired(context):
            epoch[0] = 1100
            context.read_mesh_user(456)
        with patch.object(social.time, "time", side_effect=lambda: epoch[0]):
            result, sock, _ = self.run_setup(owner=owner, now=None, on_setup=expired)
        self.assertEqual(result["mesh_error"], "social_token_time")
        self.assertIs(result["mesh_query_sent"], False)
        self.assertEqual(len(sock.sent), 3)
        self.assertTrue(sock.closed)

    def test_mesh_payload_failures_are_fixed_diagnostics_and_do_not_replace_setup_shape(self):
        result, sock, _ = self.run_setup(wire_data=fixture(responses=[
            (1, 3, reply()), (1, 4, mesh_reply(body={"payload": "invalid-base64"}))]),
            on_setup=lambda context: context.read_mesh_user(456))
        self.assertEqual(result["mesh_error"], "social_mesh_payload_invalid")
        self.assertEqual(result["mesh_validation_reason"], "payload_base64_invalid")
        self.assertIsNone(result["validation_reason"])
        self.assertEqual(result["setup_shape"]["modules_count"], 1)
        self.assertFalse(result["mesh_reply_received"])
        self.assertFalse(result["mesh_request_bound"])
        self.assertNotIn("invalid-base64", json.dumps(result))
        self.assertTrue(sock.closed)

    def test_own_career_uses_advertised_route_exact_wrapper_and_authenticated_owner(self):
        owner, received = session(zone_id=7), []
        outer = {"status": 200, "body": "PRIVATE_CAREER_BODY", "headers": {}}
        result, sock, connector = self.run_setup(owner=owner, wire_data=fixture(responses=[
            (1, 3, career_setup(header={"aid": "7_123"})),
            (42, 2, career_reply(header={"aid": "7_123"}, data=outer))]),
            on_setup=lambda context: received.append(context.read_own_career()))
        self.assertEqual(received, [outer])
        self.assertTrue(result["setup_consumer_completed"])
        self.assertTrue(result["career_query_attempted"])
        self.assertTrue(result["career_query_sent"])
        self.assertTrue(result["career_reply_received"])
        self.assertTrue(result["career_reply_bound"])
        self.assertTrue(result["stats_requested"])
        self.assertFalse(result["mesh_query_attempted"])
        self.assertEqual(result["career_query_count"], 1)
        request = outgoing(sock)[2]
        self.assertEqual(request[:2], (42, 2))
        self.assertEqual(request[2]["header"], {"version": "3.10.1", "rpcid": "3", "aid": "7_123",
                                              "streamid": "fixture-stream"})
        self.assertEqual(request[2]["body"]["cmd"], 0)
        wrapper = json.loads(base64.b64decode(request[2]["body"]["payload"]))
        post = json.loads(base64.b64decode(wrapper.pop("post_body")))
        self.assertEqual(post, {"player_uid": 123, "zone_id": 7, "device_type": "pc"})
        self.assertEqual(wrapper, {"service": "xyq-career", "headers": {"Content-Type": "application/json",
            "Accept-Encoding": "gzip"}, "uri": "query_my_player_career", "method": "POST",
            "querystring": None, "host": None, "protocol": None, "version": None})
        self.assertEqual(result["career_application_bytes_sent"], len(sock.sent[3]))
        self.assertEqual(connector.call_count, 1)
        self.assertEqual(len(sock.sent), 4)
        self.assertTrue(sock.closed)
        for secret in (owner.token, "PRIVATE_CAREER_BODY", owner.aid, "query_my_player_career"):
            self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn("career_verified", result)

    def test_career_and_mesh_each_once_use_fresh_rpcids_in_either_order(self):
        for career_first in (False, True):
            received = []
            ordered = ([(42, 2, career_reply()), (1, 4, mesh_reply(header={"rpcid": "4"}))]
                       if career_first else [(1, 4, mesh_reply()), (42, 2, career_reply(rpcid="4"))])
            def consume(context):
                if career_first:
                    received.append(context.read_own_career())
                    received.append(context.read_mesh_user(456))
                else:
                    received.append(context.read_mesh_user(456))
                    received.append(context.read_own_career())
            result, sock, connector = self.run_setup(wire_data=fixture(responses=[
                (1, 3, career_setup()), *ordered]), on_setup=consume)
            self.assertTrue(result["setup_consumer_completed"])
            self.assertEqual(len(received), 2)
            self.assertEqual(result["mesh_query_count"], 1)
            self.assertEqual(result["career_query_count"], 1)
            self.assertTrue(result["mesh_request_bound"])
            self.assertTrue(result["career_reply_bound"])
            self.assertEqual([row[2]["header"]["rpcid"] for row in outgoing(sock)[2:]], ["3", "4"])
            self.assertEqual(connector.call_count, 1)
            self.assertEqual(len(sock.sent), 5)
            self.assertTrue(sock.closed)

    def test_career_capability_has_no_target_argument_and_expires_after_callback(self):
        contexts = []
        def consume(context):
            contexts.append(context)
            with self.assertRaises(TypeError):
                context.read_own_career(456)
            context.read_own_career()
            with self.assertRaisesRegex(transport.SocialTransportError, "social_career_already_consumed"):
                context.read_own_career()
        result, sock, _ = self.run_setup(wire_data=fixture(responses=[
            (1, 3, career_setup()), (42, 2, career_reply())]), on_setup=consume)
        self.assertTrue(result["setup_consumer_completed"])
        self.assertEqual(result["career_query_count"], 1)
        self.assertEqual(len(sock.sent), 4)
        saved = json.dumps(result, sort_keys=True)
        with self.assertRaisesRegex(transport.SocialTransportError, "social_career_context_closed"):
            contexts[0].read_own_career()
        self.assertEqual(json.dumps(result, sort_keys=True), saved)
        self.assertIsNone(contexts[0]._session)
        self.assertIsNone(contexts[0]._wire)
        self.assertIsNone(contexts[0]._modules)

    def test_career_without_advertised_positive_mesh_command_sends_nothing(self):
        for setup in (reply(), career_setup(command=0)):
            result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, setup)]),
                on_setup=lambda context: context.read_own_career())
            self.assertEqual(result["career_error"], "social_career_module_unavailable")
            self.assertEqual(result["career_query_count"], 1)
            self.assertFalse(result["career_query_sent"])
            self.assertFalse(result["career_reply_bound"])
            self.assertFalse(result["stats_requested"])
            self.assertEqual(len(sock.sent), 3)
            self.assertTrue(sock.closed)

    def test_career_adds_token_only_when_setup_has_no_streamid(self):
        owner = session()
        result, sock, _ = self.run_setup(owner=owner, wire_data=fixture(responses=[
            (1, 3, career_setup(header={"streamid": None})), (42, 2, career_reply())]),
            on_setup=lambda context: context.read_own_career())
        self.assertTrue(result["career_reply_bound"])
        header = outgoing(sock)[2][2]["header"]
        self.assertEqual(header, {"version": "3.10.1", "rpcid": "3", "aid": "1_123", "token": owner.token})
        self.assertNotIn("ackRpcId", header)
        self.assertNotIn(owner.token, json.dumps(result))

    def test_career_correlates_exact_route_rpc_and_owner_and_both_statuses(self):
        cases = [(career_reply(header={"aid": "1_456"}), "social_career_identity_mismatch", None),
                 (career_reply(header={"errorcode": 403, "errormsg": "PRIVATE_ERROR"}), "social_career_rejected", 403),
                 (career_reply(body={"errorcode": 429}), "social_career_rejected", 429),
                 (career_reply(body={"cmd": True}), "social_career_reply_invalid", None)]
        for raw, code, server in cases:
            result, sock, _ = self.run_setup(wire_data=fixture(responses=[
                (1, 3, career_setup()), (42, 2, raw)]), on_setup=lambda context: context.read_own_career())
            self.assertEqual(result["career_error"], code)
            self.assertEqual(result["career_server_error_code"], server)
            self.assertFalse(result["career_reply_bound"])
            self.assertFalse(result["setup_consumer_completed"])
            self.assertNotIn("PRIVATE_ERROR", json.dumps(result))
            self.assertTrue(sock.closed)
        result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, career_setup()),
            (1, 2, career_reply()), (42, 2, career_reply(rpcid="4")), (42, 2, career_reply())]),
            on_setup=lambda context: context.read_own_career())
        self.assertTrue(result["career_reply_bound"])
        self.assertEqual(result["career_ignored_records"], 2)

    def test_career_payload_is_outer_data_only_and_decode_failures_are_redacted(self):
        for direct in (False, True):
            received = []
            outer = {"status": 503, "body": "PRIVATE_SERVICE_ERROR"}
            result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, career_setup()),
                (42, 2, career_reply(data=outer, direct=direct, body={"cmd": 0}))]),
                on_setup=lambda context: received.append(context.read_own_career()))
            self.assertEqual(received, [outer])  # Root checks Xyq service status and owner payload.
            self.assertTrue(result["career_reply_bound"])
            self.assertEqual(result["career_reply_command"], 0)
            self.assertNotIn("PRIVATE_SERVICE_ERROR", json.dumps(result))
        result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, career_setup()),
            (42, 2, career_reply(body={"payload": "PRIVATE_BAD_BASE64"}))]),
            on_setup=lambda context: context.read_own_career())
        self.assertEqual(result["career_error"], "social_career_payload_invalid")
        self.assertEqual(result["career_validation_reason"], "payload_base64_invalid")
        self.assertFalse(result["career_reply_bound"])
        self.assertIsNone(result["validation_reason"])
        self.assertNotIn("PRIVATE_BAD_BASE64", json.dumps(result))

    def test_career_failure_makes_connection_terminal_for_other_capability(self):
        def consume(context):
            with self.assertRaises(OSError):
                context.read_own_career()
            with self.assertRaisesRegex(transport.SocialTransportError, "social_query_connection_failed"):
                context.read_mesh_user(456)
        result, sock, connector = self.run_setup(sock=Socket(fixture(responses=[
            (1, 3, career_setup())]), fail_send=4), on_setup=consume)
        self.assertEqual(result["career_error"], "social_transport_network_error")
        self.assertEqual(result["mesh_error"], "social_query_connection_failed")
        self.assertIsNone(result["career_query_sent"])
        self.assertIsNone(result["career_application_bytes_sent"])
        self.assertIsNone(result["application_bytes_sent"])
        self.assertFalse(result["career_reply_bound"])
        self.assertFalse(result["mesh_query_sent"])
        self.assertTrue(result["setup_request_sent"])
        self.assertTrue(result["token_sent"])
        self.assertEqual(connector.call_count, 1)
        self.assertEqual(len(sock.sent), 4)
        self.assertTrue(sock.closed)

    def test_career_failure_does_not_erase_prior_verified_mesh_reply(self):
        received = []
        def consume(context):
            received.append(context.read_mesh_user(456))
            with self.assertRaisesRegex(transport.SocialTransportError, "social_career_rejected"):
                context.read_own_career()
        result, sock, _ = self.run_setup(wire_data=fixture(responses=[
            (1, 3, career_setup()), (1, 4, mesh_reply()),
            (42, 2, career_reply(rpcid="4", body={"errorcode": 403}))]), on_setup=consume)
        self.assertTrue(result["setup_consumer_completed"])
        self.assertTrue(result["mesh_request_bound"])
        self.assertTrue(result["mesh_reply_received"])
        self.assertIsNone(result["mesh_error"])
        self.assertFalse(result["career_reply_bound"])
        self.assertEqual(result["career_error"], "social_career_rejected")
        self.assertEqual(len(received), 1)
        self.assertTrue(sock.closed)

    def test_career_rechecks_expiry_and_request_builder_before_sending(self):
        epoch = [NOW]
        def expired(context):
            epoch[0] = 1100
            context.read_own_career()
        with patch.object(social.time, "time", side_effect=lambda: epoch[0]):
            result, sock, _ = self.run_setup(now=None, wire_data=fixture(responses=[
                (1, 3, career_setup())]), on_setup=expired)
        self.assertEqual(result["career_error"], "social_token_time")
        self.assertFalse(result["career_query_sent"])
        self.assertFalse(result["stats_requested"])
        self.assertEqual(len(sock.sent), 3)
        with patch.object(transport, "make_own_career_request", side_effect=ValueError("PRIVATE_BUILDER")):
            result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, career_setup())]),
                on_setup=lambda context: context.read_own_career())
        self.assertEqual(result["career_error"], "social_career_request_invalid")
        self.assertFalse(result["career_query_sent"])
        self.assertFalse(result["stats_requested"])
        self.assertNotIn("PRIVATE_BUILDER", json.dumps(result))
        self.assertEqual(len(sock.sent), 3)


    def test_other_rank_uses_advertised_mesh_route_exact_target_and_normal_wrapper(self):
        owner, received = session(zone_id=7), []
        outer = {"service": "xyq-namecard", "status_code": 200, "response_body": "PRIVATE_RANK_BODY"}
        result, sock, connector = self.run_setup(owner=owner, wire_data=fixture(responses=[
            (1, 3, career_setup(header={"aid": "7_123"})),
            (42, 2, rank_reply(header={"aid": "7_123"}, data=outer))]),
            on_setup=lambda context: received.append(context.read_other_ranks(456)))
        self.assertEqual(received, [outer])
        self.assertTrue(result["setup_consumer_completed"])
        for key in ("rank_query_attempted", "rank_query_sent", "rank_reply_received", "rank_reply_bound",
                    "stats_requested"):
            self.assertTrue(result[key])
        self.assertFalse(result["career_query_attempted"])
        self.assertFalse(result["career_reply_bound"])
        self.assertFalse(result["mesh_query_attempted"])
        self.assertEqual(result["rank_query_count"], 1)
        request = outgoing(sock)[2]
        self.assertEqual(request[:2], (42, 2))
        self.assertEqual(request[2]["header"], {"version": "3.10.1", "rpcid": "3", "aid": "7_123",
                                              "streamid": "fixture-stream"})
        self.assertEqual(request[2]["body"]["cmd"], 0)
        wrapper = json.loads(base64.b64decode(request[2]["body"]["payload"]))
        post = json.loads(base64.b64decode(wrapper.pop("post_body")))
        self.assertEqual(post, {"host": 7, "tag": "player", "id": "7_456"})
        self.assertEqual(wrapper, {"service": "xyq-namecard", "headers": {"Content-Type": "application/json",
            "Accept-Encoding": "gzip"}, "uri": "GetData", "method": "POST",
            "querystring": None, "host": None, "protocol": None, "version": None})
        self.assertEqual(result["rank_application_bytes_sent"], len(sock.sent[3]))
        self.assertEqual(connector.call_count, 1)
        self.assertEqual(len(sock.sent), 4)
        self.assertTrue(sock.closed)
        self.assertNotIn("ranks_verified", result)
        for secret in (owner.token, "PRIVATE_RANK_BODY", owner.aid, "xyq-namecard"):
            self.assertNotIn(secret, json.dumps(result))

    def test_rank_mesh_and_career_have_independent_once_flags_and_shared_rpc_sequence(self):
        for order in (("mesh", "rank", "career"), ("rank", "career", "mesh")):
            received, responses = [], [(1, 3, career_setup())]
            for index, kind in enumerate(order, 3):
                rpcid = str(index)
                responses.append((1, 4, mesh_reply(header={"rpcid": rpcid})) if kind == "mesh" else
                                 (42, 2, rank_reply(rpcid=rpcid) if kind == "rank" else career_reply(rpcid=rpcid)))

            def consume(context):
                methods = {"mesh": lambda: context.read_mesh_user(456),
                           "rank": lambda: context.read_other_ranks(456), "career": context.read_own_career}
                for kind in order:
                    received.append(methods[kind]())

            result, sock, _ = self.run_setup(wire_data=fixture(responses=responses), on_setup=consume)
            self.assertTrue(result["setup_consumer_completed"])
            self.assertEqual(len(received), 3)
            for kind in order:
                self.assertEqual(result[kind + "_query_count"], 1)
            self.assertTrue(result["rank_reply_bound"])
            self.assertTrue(result["career_reply_bound"])
            self.assertTrue(result["mesh_request_bound"])
            self.assertEqual([row[2]["header"]["rpcid"] for row in outgoing(sock)[2:]], ["3", "4", "5"])
            self.assertEqual(len(sock.sent), 6)

    def test_rank_capability_is_once_and_invalidated_after_callback(self):
        contexts = []

        def consume(context):
            contexts.append(context)
            context.read_other_ranks(456)
            with self.assertRaisesRegex(transport.SocialTransportError, "social_rank_already_consumed"):
                context.read_other_ranks(789)

        result, sock, _ = self.run_setup(wire_data=fixture(responses=[
            (1, 3, career_setup()), (42, 2, rank_reply())]), on_setup=consume)
        self.assertTrue(result["setup_consumer_completed"])
        self.assertEqual(result["rank_query_count"], 1)
        self.assertEqual(len(sock.sent), 4)
        before = json.dumps(result, sort_keys=True)
        with self.assertRaisesRegex(transport.SocialTransportError, "social_rank_context_closed"):
            contexts[0].read_other_ranks(456)
        self.assertEqual(json.dumps(result, sort_keys=True), before)
        self.assertIsNone(contexts[0]._session)
        self.assertIsNone(contexts[0]._wire)
        self.assertIsNone(contexts[0]._modules)

    def test_rank_rejects_owner_or_invalid_target_before_query_io(self):
        for uid in (123, 0, -1, True, "456", 1 << 64):
            with self.subTest(uid=uid):
                result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, career_setup())]),
                    on_setup=lambda context: context.read_other_ranks(uid))
                self.assertEqual(result["rank_error"], "social_rank_target_invalid")
                self.assertFalse(result["rank_query_sent"])
                self.assertFalse(result["rank_reply_bound"])
                self.assertFalse(result["stats_requested"])
                self.assertEqual(len(sock.sent), 3)

    def test_rank_accepts_same_unsigned_uid_range_as_mesh_and_request_builder(self):
        for uid in (1 << 63, (1 << 64) - 1):
            with self.subTest(uid=uid):
                result, sock, _ = self.run_setup(wire_data=fixture(responses=[
                    (1, 3, career_setup()), (42, 2, rank_reply())]),
                    on_setup=lambda context: context.read_other_ranks(uid))
                self.assertTrue(result["rank_reply_bound"])
                query = json.loads(base64.b64decode(outgoing(sock)[2][2]["body"]["payload"]))
                post = json.loads(base64.b64decode(query["post_body"]))
                self.assertEqual(post["id"], f"1_{uid}")

    def test_rank_requires_advertised_positive_mesh_and_does_not_reuse_legacy_user_route(self):
        for setup_body in (reply(), career_setup(command=0)):
            result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, setup_body)]),
                on_setup=lambda context: context.read_other_ranks(456))
            self.assertEqual(result["rank_error"], "social_rank_module_unavailable")
            self.assertFalse(result["rank_query_sent"])
            self.assertFalse(result["stats_requested"])
            self.assertEqual(len(sock.sent), 3)

    def test_rank_adds_token_only_when_normal_setup_has_no_streamid(self):
        owner = session()
        result, sock, _ = self.run_setup(owner=owner, wire_data=fixture(responses=[
            (1, 3, career_setup(header={"streamid": None})), (42, 2, rank_reply())]),
            on_setup=lambda context: context.read_other_ranks(456))
        self.assertTrue(result["rank_reply_bound"])
        self.assertEqual(outgoing(sock)[2][2]["header"], {
            "version": "3.10.1", "rpcid": "3", "aid": "1_123", "token": owner.token})
        self.assertNotIn(owner.token, json.dumps(result))

    def test_rank_reply_has_independent_route_rpc_owner_status_and_payload_checks(self):
        cases = [
            (rank_reply(header={"aid": "1_456"}), "social_rank_identity_mismatch", None, None),
            (rank_reply(header={"errorcode": 403, "errormsg": "PRIVATE"}), "social_rank_rejected", 403, "header"),
            (rank_reply(body={"errorcode": 429}), "social_rank_rejected", 429, "body"),
            (rank_reply(body={"cmd": True}), "social_rank_reply_invalid", None, None),
            (rank_reply(body={"payload": "PRIVATE_BAD_BASE64"}), "social_rank_payload_invalid", None, None),
        ]
        for raw, error, status, layer in cases:
            result, sock, _ = self.run_setup(wire_data=fixture(responses=[
                (1, 3, career_setup()), (42, 2, raw)]), on_setup=lambda context: context.read_other_ranks(456))
            self.assertEqual(result["rank_error"], error)
            self.assertEqual(result["rank_server_error_code"], status)
            self.assertEqual(result["rank_status_layer"], layer)
            self.assertFalse(result["rank_reply_bound"])
            self.assertFalse(result["career_reply_bound"])
            self.assertIsNone(result["career_error"])
            self.assertIsNone(result["validation_reason"])
            self.assertNotIn("PRIVATE", json.dumps(result))
            self.assertTrue(sock.closed)
            if error == "social_rank_payload_invalid":
                self.assertEqual(result["rank_validation_reason"], "payload_base64_invalid")
        result, _, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, career_setup()),
            (1, 2, rank_reply()), (42, 2, rank_reply(rpcid="4")), (42, 2, rank_reply())]),
            on_setup=lambda context: context.read_other_ranks(456))
        self.assertTrue(result["rank_reply_bound"])
        self.assertEqual(result["rank_ignored_records"], 2)

    def test_rank_response_is_transient_service_data_not_a_transport_rank_claim(self):
        for direct in (False, True):
            received = []
            outer = {"service": "xyq-namecard", "status_code": 503, "response_body": "PRIVATE_ERROR"}
            result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, career_setup()),
                (42, 2, rank_reply(data=outer, direct=direct, body={"cmd": 0}))]),
                on_setup=lambda context: received.append(context.read_other_ranks(456)))
            self.assertEqual(received, [outer])
            self.assertTrue(result["rank_reply_bound"])
            self.assertEqual(result["rank_reply_command"], 0)
            self.assertNotIn("ranks_verified", result)
            self.assertNotIn("PRIVATE_ERROR", json.dumps(result))
            self.assertTrue(sock.closed)

    def test_rank_io_failure_blocks_reuse_without_erasing_prior_mesh_identity_reply(self):
        received = []

        def consume(context):
            received.append(context.read_mesh_user(456))
            with self.assertRaises(OSError):
                context.read_other_ranks(456)
            with self.assertRaisesRegex(transport.SocialTransportError, "social_query_connection_failed"):
                context.read_own_career()

        sock = Socket(fixture(responses=[(1, 3, career_setup()), (1, 4, mesh_reply())]), fail_send=5)
        result, sock, connector = self.run_setup(sock=sock, on_setup=consume)
        self.assertEqual(len(received), 1)
        self.assertTrue(result["mesh_request_bound"])
        self.assertTrue(result["mesh_reply_received"])
        self.assertIsNone(result["mesh_error"])
        self.assertEqual(result["rank_error"], "social_transport_network_error")
        self.assertEqual(result["career_error"], "social_query_connection_failed")
        self.assertIsNone(result["rank_query_sent"])
        self.assertIsNone(result["rank_application_bytes_sent"])
        self.assertIsNone(result["application_bytes_sent"])
        self.assertFalse(result["rank_reply_bound"])
        self.assertFalse(result["career_query_sent"])
        self.assertEqual(len(sock.sent), 5)
        self.assertEqual(connector.call_count, 1)
        self.assertTrue(sock.closed)

    def test_rank_read_failure_is_terminal_for_unused_mesh_and_career_capabilities(self):
        def consume(context):
            with self.assertRaisesRegex(transport.SocialTransportError, "social_connection_closed"):
                context.read_other_ranks(456)
            for operation in (lambda: context.read_mesh_user(456), context.read_own_career):
                with self.assertRaisesRegex(transport.SocialTransportError, "social_query_connection_failed"):
                    operation()

        result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, career_setup())]), on_setup=consume)
        self.assertTrue(result["rank_query_sent"])
        self.assertEqual(result["rank_error"], "social_connection_closed")
        self.assertFalse(result["mesh_query_sent"])
        self.assertFalse(result["career_query_sent"])
        self.assertEqual(len(sock.sent), 4)

    def test_rank_rechecks_expiry_deadline_and_builder_before_sending(self):
        epoch = [NOW]

        def expired(context):
            epoch[0] = 1100
            context.read_other_ranks(456)

        with patch.object(social.time, "time", side_effect=lambda: epoch[0]):
            result, sock, _ = self.run_setup(now=None, wire_data=fixture(responses=[
                (1, 3, career_setup())]), on_setup=expired)
        self.assertEqual(result["rank_error"], "social_token_time")
        self.assertFalse(result["rank_query_sent"])
        self.assertFalse(result["stats_requested"])
        self.assertEqual(len(sock.sent), 3)
        clock = [10]

        def late(context):
            clock[0] = 31
            context.read_other_ranks(456)

        result, sock, _ = self.run_setup(monotonic=lambda: clock[0], wire_data=fixture(responses=[
            (1, 3, career_setup())]), on_setup=late)
        self.assertEqual(result["rank_error"], "social_transport_timeout")
        self.assertFalse(result["rank_query_sent"])
        self.assertEqual(len(sock.sent), 3)
        with patch.object(transport, "make_other_rank_request", side_effect=ValueError("PRIVATE_BUILDER")):
            result, sock, _ = self.run_setup(wire_data=fixture(responses=[(1, 3, career_setup())]),
                on_setup=lambda context: context.read_other_ranks(456))
        self.assertEqual(result["rank_error"], "social_rank_request_invalid")
        self.assertFalse(result["rank_query_sent"])
        self.assertFalse(result["stats_requested"])
        self.assertNotIn("PRIVATE_BUILDER", json.dumps(result))
        self.assertEqual(len(sock.sent), 3)


if __name__ == "__main__":
    unittest.main()
