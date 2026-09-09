"""Synthetic protocol fixtures only; no captures, game archive, or sockets."""
import base64
import gzip
import json
import struct
import unittest
from unittest.mock import patch

import lz4.block
import socialmatrix_capture_probe as probe


def packed(value):
    return json.dumps(value, separators=(",", ":")).encode()


def envelope(header, payload, *, response=False, compressed=False):
    body = {"str_payload": packed(payload).decode()} if response else {"payload": base64.b64encode(packed(payload)).decode()}
    raw = packed({"header": header, "body": body})
    return b"\1" + lz4.block.compress(raw) if compressed else b"\0" + raw


def response_payload(data):
    return {"service": "xyq-career", "status_code": 200,
            "response_body": base64.b64encode(gzip.compress(packed({"code": 200, "data": data}))).decode()}


class SocialMatrixCaptureTest(unittest.TestCase):
    def test_rejections_report_only_bounded_numeric_status(self):
        for payload, layer, code in [
            ({"service": "xyq-career", "status_code": 403, "message": "fixture-secret"}, "service", 403),
            ({"service": "xyq-career", "status_code": 200,
              "response_body": base64.b64encode(packed({"code": 404, "message": "fixture-secret"})).decode()}, "application", 404)]:
            with self.assertRaises(probe.ServiceReplyError) as context:
                probe.decode_response(payload)
            self.assertEqual((context.exception.layer, context.exception.code), (layer, code))
            self.assertNotIn("fixture-secret", str(context.exception))
        for code in (True, "fixture-secret", 10**15):
            self.assertIsNone(probe.ServiceReplyError("service", code).code)

    def test_rc4_rfc6229_known_vector_and_continuous_state(self):
        key = bytes.fromhex("0102030405")
        expected = bytes.fromhex("b2396305f03dc027ccc3524a0a1118a8")
        self.assertEqual(probe.rc4_crypt(key, bytes(16)), expected)
        stream = probe.RC4Stream(key)
        self.assertEqual(stream.process(bytes(7)) + stream.process(bytes(9)), expected)
        self.assertEqual(probe.rc4_crypt(key, bytes(272))[256:], bytes.fromhex("1cfcf62b03eddb641d77dfcf7f8d8c93"))

    def test_rc4_input_bounds(self):
        for key in [b"", bytes(257)]:
            with self.assertRaises(ValueError):
                probe.RC4Stream(key)
        stream = probe.RC4Stream(b"key")
        stream._processed = probe.MAX_STREAM_BYTES
        with self.assertRaises(ValueError):
            stream.process(b"x")

    def test_xor_fold_and_signed_hash(self):
        self.assertEqual(probe.fold_xor(bytes(2), b"abc"), bytes([ord("a") ^ ord("c"), ord("b")]))
        self.assertEqual(probe.magic_signature(bytes([128]), bytes(4)), struct.pack("<I", 5381 * 34 - 128))
        with self.assertRaises(ValueError):
            probe.fold_xor(b"", b"x")

    def test_opening_and_capture_signature(self):
        parameter = b"synthetic-param17"
        fields = [b"abcd", b"efgh", b"ijkl"]
        server = struct.pack("<IBB", 24, 1, 0) + b"".join(struct.pack("<H", 4) + p for p in fields)
        client = struct.pack("<IH", 14, 4) + probe.magic_signature(fields[0], parameter) + bytes(4)
        record = struct.pack("<IHB", 9, 1, 2) + b"{}"
        inbound = server + probe.rc4_crypt(probe.fold_xor(fields[1], parameter), record)
        outbound = client + probe.rc4_crypt(probe.fold_xor(fields[2], parameter), record)
        self.assertEqual(probe.decode_streams(inbound, outbound, parameter), {"in": record, "out": record})
        with self.assertRaises(ValueError):
            probe.decode_streams(inbound, outbound, b"wrong")
        with self.assertRaises(ValueError):
            probe.opening_fields(server[:4] + b"\2\0" + server[6:], True)

    def test_truncated_opening(self):
        for body in [b"", bytes(12), struct.pack("<I", 5000) + bytes(12), struct.pack("<I", 14) + bytes(12)]:
            with self.assertRaises(ValueError):
                probe.opening_fields(body, False)

    def test_record_lengths(self):
        record = struct.pack("<IHB", 10, 1, 2) + b"abc"
        self.assertEqual(probe.split_records(record), [(1, 2, b"abc")])
        for data in [record[:-1], record + b"x", struct.pack("<IHB", 6, 0, 0)]:
            with self.assertRaises(ValueError):
                probe.split_records(data)

    def test_plain_and_lz4_envelopes(self):
        for compressed in [False, True]:
            obj = probe.decode_envelope(envelope({"rpcid": "fixture"}, {"count": 4}, compressed=compressed))
            self.assertEqual(probe.decode_payload(obj), {"count": 4})
        obj = probe.decode_envelope(envelope({}, {"count": 7}, response=True))
        self.assertEqual(probe.decode_payload(obj), {"count": 7})

    def test_bad_envelope_rejected(self):
        for data in [b"", b"\2{}", b"\0[]", b"\0{}", b"\1" + struct.pack("<I", probe.MAX_STREAM_BYTES + 1) + b"x",
                     b"\1" + struct.pack("<I", 25) + b"bad"]:
            with self.assertRaises(ValueError):
                probe.decode_envelope(data)
        data = envelope({}, {}, compressed=True)
        with self.assertRaises(ValueError):
            probe.decode_envelope(data[:1] + struct.pack("<I", struct.unpack_from("<I", data, 1)[0] + 1) + data[5:])

    def test_base64_and_json_rejected_without_reflecting_input(self):
        for fn, data in [(probe.decode_base64, "secret%%%"), (probe.bounded_json, "secret-value"),
                         (probe.decode_base64, None), (probe.bounded_json, [])]:
            with self.assertRaises(ValueError) as context:
                fn(data)
            self.assertNotIn("secret", str(context.exception))

    def test_gzip_reply_envelope_and_size_checked(self):
        data = {"career": {"player_uid": 123}}
        payload = response_payload(data)
        self.assertEqual(probe.decode_response(payload), data)
        for change in [{"status_code": 403}, {"service": "other"}, {"response_body": base64.b64encode(b'{"code":403}').decode()}]:
            with self.assertRaises(ValueError):
                probe.decode_response({**payload, **change})
        raw = gzip.compress(packed({"code": 200, "data": data})) + b"tail"
        with self.assertRaises(ValueError):
            probe.decode_response({**payload, "response_body": base64.b64encode(raw).decode()})
        with patch.object(probe, "MAX_STREAM_BYTES", 512):
            raw = gzip.compress(packed({"code": 200, "data": {"large": "x" * 2000}}))
            with self.assertRaises(ValueError):
                probe.decode_response({**payload, "response_body": base64.b64encode(raw).decode()})

    def test_career_correlation_and_own_uid_filter(self):
        request = {"service": "xyq-career", "method": "POST", "uri": "query_my_player_career",
                   "post_body": base64.b64encode(packed({"player_uid": 123})).decode()}
        records = {"out": [(0, 0, packed({"payload_type": 1})), (1, 2, envelope({"rpcid": "one"}, request))],
                   "in": [(0, 0, packed({"code": 0, "content_encoding": "uni_lz4"})),
                          (1, 2, envelope({"rpcid": "one"}, response_payload({"career": {"player_uid": 123}}), response=True))]}
        pairs = probe.career_exchanges(records, 123)
        self.assertEqual(pairs[0]["data"]["career"]["player_uid"], 123)
        self.assertEqual(probe.career_exchanges(records, 456), [])
        records["in"][1] = (1, 2, envelope({"rpcid": "wrong"}, {}, response=True))
        self.assertEqual(probe.career_exchanges(records, 123), [])
        records["out"].append(records["out"][1])
        with self.assertRaises(ValueError):
            probe.career_exchanges(records, 123)
