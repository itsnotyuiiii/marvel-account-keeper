"""Static-contract fixtures only; no live game plaintext or session material."""
import struct
import unittest
from unittest.mock import patch

import msgpack
import nex_rpc_probe as nex


def body(args=None, *, rpc_index=12345, reverse=False, packed=None):
    if packed is None:
        packed = msgpack.packb(args or [], use_bin_type=True)
    index = b"\x61" + struct.pack("<I", rpc_index)
    blob = b"\xe2" + struct.pack("<I", len(packed)) + packed
    return blob + index if reverse else index + blob


def frame(payload, *, sub=1, extra=None):
    if extra is None:
        return struct.pack("<IHB", 7 + len(payload), 3, sub) + payload
    return struct.pack("<IHBH", 0x80000000 | (9 + len(payload) + len(extra)), 3, sub, len(extra)) + payload + extra


class NexRpcProbeTest(unittest.TestCase):
    def test_observed_game_hello_shape_with_synthetic_challenge(self):
        challenge = b"synthetic-nonce!"
        wire = struct.pack("<IBBH", 28, 2, 0, 16) + challenge + b"\0\0\0\0"
        hello = nex.decode_game_hello(wire)
        self.assertEqual((hello.suite, hello.compression_flags), (2, 0))
        self.assertEqual(hello.challenge, challenge)
        self.assertNotIn(challenge.decode(), repr(hello))

    def test_game_hello_requires_complete_exact_record(self):
        wire = struct.pack("<IBBH", 28, 2, 0, 16) + b"x" * 16 + b"\0\0\0\0"
        for data in (b"", wire[:-1], wire + b"\0", bytearray(wire)):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                nex.decode_game_hello(data)

    def test_game_hello_rejects_other_modes_including_socialmatrix(self):
        for size, suite, compression in ((27, 2, 0), (28, 1, 0), (28, 0, 0),
                                         (28, 2, 1), (28, 255, 0)):
            data = struct.pack("<IBBH", size, suite, compression, 16) + b"x" * 16 + b"\0\0\0\0"
            with self.subTest(size=size, suite=suite, compression=compression), self.assertRaises(ValueError):
                nex.decode_game_hello(data)

    def test_game_hello_rejects_unexpected_field_lengths(self):
        wire = struct.pack("<IBBH", 28, 2, 0, 16) + b"x" * 16 + b"\0\0\0\0"
        for offset, size in ((6, 15), (6, 65535), (24, 1), (26, 1)):
            data = wire[:offset] + struct.pack("<H", size) + wire[offset + 2:]
            with self.subTest(offset=offset, size=size), self.assertRaises(ValueError):
                nex.decode_game_hello(data)

    def test_hand_authored_wire_fixture_uses_native_tags_and_byte_order(self):
        # size18, main3/sub1, field1 uint32(0x12345678), field2 one-byte empty array.
        data = bytes.fromhex("120000000300016178563412e20100000090")
        rpc = nex.decode_avatar_rpc(nex.split_plaintext_records(data)[0], direction="out")
        self.assertEqual(rpc.index, 0x12345678)
        self.assertEqual(rpc.args, [])

    def test_career_positional_arguments_remain_distinct_from_transport_identity(self):
        args = ["query_player_career", {"player_uid": 456789, "zone_id": 11001,
                "match_season": 19, "device_type": "pc"}, {"is_career": True}]
        rpc = nex.decode_avatar_rpc(nex.split_plaintext_records(frame(body(args)))[0], direction="out")
        self.assertEqual(rpc.args, args)
        self.assertEqual(rpc.index, 12345)  # synthetic index, not a live method ID
        self.assertNotEqual(rpc.index, args[1]["player_uid"])

    def test_field_order_and_incoming_command(self):
        args = ["query_player_career", {"is_career": True}, 0, "", {"career": {"player_uid": 456789}}]
        record = nex.split_plaintext_records(frame(body(args, reverse=True), sub=101))[0]
        self.assertEqual(nex.decode_avatar_rpc(record, direction="in").args, args)
        with self.assertRaises(ValueError):
            nex.decode_avatar_rpc(record, direction="out")

    def test_extension_excluded_and_multiple_complete_records(self):
        records = nex.split_plaintext_records(frame(body(), extra=b"not-rpc") + frame(body(), sub=101))
        self.assertEqual(len(records), 2)
        self.assertEqual(nex.decode_avatar_rpc(records[0], direction="out").args, [])
        self.assertEqual(nex.decode_avatar_rpc(records[1], direction="in").args, [])

    def test_incomplete_prefix_gaps_sizes_and_unknown_flags_rejected(self):
        valid = frame(body())
        bad = [b"", valid[:6], valid[:-1], valid + b"\0", b"prefix" + valid,
               struct.pack("<IHB", 6, 3, 1), struct.pack("<IHB", 1 << 28 | 7, 3, 1),
               struct.pack("<IHBH", 0x80000009, 3, 1, 1)]
        for data in bad:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                nex.split_plaintext_records(data)

    def test_record_count_and_stream_size_bounds(self):
        with patch.object(nex, "MAX_RECORDS", 1), self.assertRaises(ValueError):
            nex.split_plaintext_records(frame(body()) * 2)
        with patch.object(nex, "MAX_PLAINTEXT_BYTES", 8), self.assertRaises(ValueError):
            nex.split_plaintext_records(frame(body()))

    def test_unknown_duplicate_or_missing_fields_rejected(self):
        valid = body()
        cases = [valid + valid, b"\x62" + valid[1:], valid[:5], valid[5:],
                 valid[:6] + struct.pack("<I", 9999) + valid[10:], valid + b"\0"]
        for data in cases:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                nex.decode_avatar_rpc(nex.NexRecord(3, 1, data), direction="out")

    def test_non_avatar_or_unknown_direction_rejected(self):
        for main, sub, direction in [(1, 1, "out"), (3, 2, "out"), (3, 1, "unknown")]:
            with self.subTest(main=main, sub=sub, direction=direction), self.assertRaises(ValueError):
                nex.decode_avatar_rpc(nex.NexRecord(main, sub, body()), direction=direction)

    def test_invalid_nonarray_duplicate_key_and_extension_messagepack_rejected(self):
        cases = [b"\xc1", b"\x90\x90", b"\x80", b"\x91\x82\xa1x\x01\xa1x\x02",
                 msgpack.packb([msgpack.ExtType(1, b"x")]), msgpack.packb(list(range(33))),
                 b"\x91\xa1\xff", b"\xdd\xff\xff\xff\xff"]
        for data in cases:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                nex.decode_avatar_rpc(nex.NexRecord(3, 1, body(packed=data)), direction="out")


if __name__ == "__main__":
    unittest.main()
