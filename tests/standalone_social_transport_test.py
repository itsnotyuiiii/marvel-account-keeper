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


def session(*, tcp_extra=None, host="8.8.8.8"):
    claims = {"aid": "1_123", "iat": 990, "exp": 1100,
              "tcp": {"addr": host + ":12101", "rc4": True, "tls": False, **(tcp_extra or {})}}
    def part(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")
    token = ".".join(map(part, (packed({"alg": "HS256"}), packed(claims), b"synthetic-signature")))
    return social.parse_gateway_social_token(token, own_uid=123, zone_id=1, now=NOW)


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


if __name__ == "__main__":
    unittest.main()
