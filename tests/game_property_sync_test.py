"""Synthetic ordinary-path sync frames; no game files, credentials or I/O."""
from dataclasses import FrozenInstanceError, fields
import random
import struct
import unittest
from unittest.mock import patch

import msgpack

import game_property_codec as codec
import game_property_sync as sync


def packed(value):
    return msgpack.packb(value, use_bin_type=True)


def extension(operation=1, entries=()):
    raw = packed(operation)
    for path, value in entries:
        raw += packed(len(path)) + b"".join(packed(segment) for segment in path) + packed(value)
    return msgpack.ExtType(2, raw)


def wire(operation=1, entries=()):
    return packed([extension(operation, entries)])


def raw_extension(payload, *, code=2):
    return packed([msgpack.ExtType(code, payload)])


def raw_value(value):
    return raw_extension(b"\x01\x01\xa1x" + value)


class GamePropertySyncTest(unittest.TestCase):
    def assert_code(self, code, data):
        with self.assertRaises(sync.PropertySyncError) as caught:
            sync.decode_sync_paths(data)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertIn(code, sync.ERROR_CODES)

    def test_byte_authored_standard_array_ext2_with_full_path_and_value(self):
        # Array1, ext8 payload18/type2; operation1, length2, strings, map0.
        raw = b"\x91\xc7\x12\x02\x01\x02\xa8RankGame\xa5ranks\x80"
        expected = sync.PropertyChange(1, ("RankGame", "ranks"), "map")
        self.assertEqual(sync.decode_sync_paths(raw).entries, (expected,))
        self.assertEqual(msgpack.unpackb(raw)[0].code, 2)

    def test_variable_outer_array_and_extension_headers(self):
        payload = b"\x01\x01\xa1x\x07"
        for outer in (b"\x91", b"\xdc\x00\x01", b"\xdd\x00\x00\x00\x01"):
            for tag, size in ((0xC7, 1), (0xC8, 2), (0xC9, 4)):
                raw = outer + bytes([tag]) + len(payload).to_bytes(size, "big") + b"\x02" + payload
                self.assertEqual(sync.decode_sync_paths(raw).entries, (sync.PropertyChange(1, ("x",), "int"),))
        # A fixext1 operation-only payload is a verified no-op.
        self.assertEqual(sync.decode_sync_paths(b"\x91\xd4\x02\x01").entries, ())

    def test_multiple_entries_and_extensions_preserve_order_and_duplicate_paths(self):
        data = packed([extension(1, [(('a',), 1), (('a',), 2)]),
                       extension(2, [(('a', 0), None)]), extension(3, [(('b', -1), True)])])
        result = sync.decode_sync_paths(data)
        self.assertEqual(result.entries, (
            sync.PropertyChange(1, ("a",), "int"), sync.PropertyChange(1, ("a",), "int"),
            sync.PropertyChange(2, ("a", 0), "null"), sync.PropertyChange(3, ("b", -1), "bool")))

    def test_op2_also_consumes_exactly_one_value_before_following_entry(self):
        self.assertEqual(sync.decode_sync_paths(wire(2, [(('a',), None), (('b',), 5)])).entries,
                         (sync.PropertyChange(2, ("a",), "null"), sync.PropertyChange(2, ("b",), "int")))
        self.assert_code("property_truncated", raw_extension(b"\x02\x01\xa1a"))

    def test_empty_array_and_valid_operation_only_extensions_are_noops(self):
        for data in (b"\x90", b"\xdc\x00\x00", b"\xdd\x00\x00\x00\x00",
                     packed([extension(1), extension(2), extension(3)])):
            self.assertEqual(sync.decode_sync_paths(data).entries, ())
        self.assert_code("property_sync_operation_unsupported", wire(7))
        self.assert_code("property_truncated", raw_extension(b""))

    def test_all_fixed_value_kinds_are_wire_classifications_only(self):
        cases = (
            (b"\xc0", "null"), (b"\xc2", "bool"), (b"\xc3", "bool"),
            (b"\x00", "int"), (b"\xff", "int"), (b"\xcc\x01", "int"),
            (b"\xcd\x00\x01", "int"), (b"\xce" + b"\x00" * 4, "int"),
            (b"\xcf" + b"\xff" * 8, "int"), (b"\xd0\xff", "int"),
            (b"\xd1\xff\xff", "int"), (b"\xd2" + b"\xff" * 4, "int"),
            (b"\xd3" + b"\xff" * 8, "int"),
            (b"\xca" + struct.pack(">f", float("nan")), "float"),
            (b"\xcb" + struct.pack(">d", float("inf")), "float"),
            (b"\xa1a", "str"), (b"\xd9\x01a", "str"), (b"\xda\x00\x01a", "str"),
            (b"\xdb\x00\x00\x00\x01a", "str"),
            (b"\xc4\x01x", "bytes"), (b"\xc5\x00\x01x", "bytes"), (b"\xc6\x00\x00\x00\x01x", "bytes"),
            (b"\x90", "array"), (b"\xdc\x00\x00", "array"), (b"\xdd\x00\x00\x00\x00", "array"),
            (b"\x80", "map"), (b"\xde\x00\x00", "map"), (b"\xdf\x00\x00\x00\x00", "map"),
            (b"\xc7\x00\x63", "extension"), (b"\xc8\x00\x00\x63", "extension"),
            (b"\xc9\x00\x00\x00\x00\x63", "extension"),
        )
        for raw, kind in cases:
            self.assertEqual(sync.decode_sync_paths(raw_value(raw)).entries[0].value_kind, kind)
        for tag in range(0xD4, 0xD9):
            value = bytes([tag, 255]) + b"\x00" * (1 << (tag - 0xD4))
            self.assertEqual(sync.decode_sync_paths(raw_value(value)).entries[0].value_kind, "extension")
        self.assertEqual({kind for _, kind in cases}, sync.VALUE_KINDS)

    def test_numeric_path_segments_are_preserved_without_descriptor_or_sentinel_conversion(self):
        path = (0, -(1 << 63), (1 << 63) - 1, "ranks")
        self.assertEqual(sync.decode_sync_paths(wire(1, [(path, {})])).entries,
                         (sync.PropertyChange(1, path, "map"),))

    def test_full_ordinary_header_is_not_low_nibble_masked(self):
        path = tuple(range(32))
        self.assertEqual(sync.decode_sync_paths(wire(1, [(path, None)])).entries[0].path, path)
        for header in (16, 17, 31, 48):
            self.assert_code("property_sync_packed_index", raw_extension(packed(1) + packed(header)))
        self.assert_code("property_sync_path_bound", raw_extension(packed(1) + packed(33)))

    def test_operations_are_exact_positive_bounded_integers_and_supported_subset(self):
        for operation in (0, -1, 128, True, 1.0, "1", None, [], {}):
            self.assert_code("property_sync_operation_invalid", wire(operation))
        for operation in (4, 7, 127):
            self.assert_code("property_sync_operation_unsupported", wire(operation))

    def test_headers_are_nonbool_positive_int32(self):
        for header in (0, -1, 1 << 31, True, 1.0, "1", None, [], {}):
            self.assert_code("property_sync_header_invalid", raw_extension(packed(1) + packed(header)))

    def test_segments_are_nonempty_bounded_utf8_strings_or_signed_int64(self):
        for segment in (True, False, None, 1.0, b"x", [], {}, 1 << 63, (1 << 64) - 1, ""):
            self.assert_code("property_sync_segment_invalid", wire(1, [((segment,), 7)]))
        self.assertEqual(sync.decode_sync_paths(wire(1, [(("ゆ" * 42,), 7)])).entries[0].path, ("ゆ" * 42,))
        self.assert_code("property_sync_segment_invalid", wire(1, [(("ゆ" * 43,), 7)]))
        self.assert_code("property_sync_segment_invalid", wire(1, [(("x" * 129,), 7)]))

    def test_only_exact_bytes_one_outer_array_and_ext2_elements(self):
        class BytesSubclass(bytes):
            pass
        for data in (None, [], {}, bytearray(b"\x90"), memoryview(b"\x90"), BytesSubclass(b"\x90")):
            self.assert_code("property_sync_input_invalid", data)
        for data in (packed({}), packed(None), packed(1), packed(msgpack.ExtType(2, b"\x01"))):
            self.assert_code("property_sync_outer_array", data)
        for item in (None, 1, {}, [], "x"):
            self.assert_code("property_sync_element_invalid", packed([item]))
        for code in (0, 1, 3, 127):
            self.assert_code("property_sync_extension_type", raw_extension(b"\x01", code=code))

    def test_trailing_outer_bytes_and_partial_later_entries_never_return_partial_results(self):
        good = wire(1, [(('x',), 7)])
        for suffix in (b"\x00", b"\x90", good, b"PRIVATE_TRAILING"):
            self.assert_code("property_sync_trailing_data", good + suffix)
        first = extension(1, [(('x',), 7)])
        self.assert_code("property_truncated", packed([first, msgpack.ExtType(2, b"\x01\x01")]))
        self.assert_code("property_truncated", raw_extension(first.data + b"\x01\xa1x"))

    def test_all_prefixes_of_nonempty_valid_wire_frame_reject_incomplete_parse(self):
        good = wire(1, [(('RankGame', 'ranks', 1001019), {"level": 3})])
        for length in range(len(good)):
            with self.subTest(length=length), self.assertRaises(sync.PropertySyncError):
                sync.decode_sync_paths(good[:length])
        self.assertEqual(len(sync.decode_sync_paths(good).entries), 1)

    def test_values_are_structurally_skipped_without_opaque_or_map_materialization(self):
        marker = "PRIVATE_VALUE_NOT_RETAINED"
        data = wire(1, [(('x',), [{"secret": marker}, msgpack.ExtType(0, marker.encode())])])
        original = codec._Reader.value
        calls = []
        def read(reader, **kwargs):
            calls.append(kwargs.get("keep", True))
            return original(reader, **kwargs)
        with patch.object(codec._Reader, "value", read), \
                patch.object(codec, "OpaqueExtension", side_effect=AssertionError("value object materialized")):
            result = sync.decode_sync_paths(data)
        self.assertIn(False, calls)
        self.assertEqual(result.entries, (sync.PropertyChange(1, ("x",), "array"),))
        self.assertNotIn(marker, repr(result))
        self.assertNotIn(marker, repr(result.entries))
        self.assertFalse(hasattr(result.entries[0], "value"))

    def test_skipped_map_duplicate_keys_are_allowed_as_sequential_uninterpreted_value(self):
        # The classifier retains only kind 'map', not this replacement value.
        self.assertEqual(sync.decode_sync_paths(raw_value(b"\x82\xa1a\x01\xa1a\x02")).entries[0].value_kind, "map")

    def test_input_extension_entry_path_and_total_segment_bounds(self):
        with patch.object(sync, "MAX_INPUT_BYTES", 1):
            self.assert_code("property_sync_input_bound", wire())
        with patch.object(sync, "MAX_EXTENSIONS", 1):
            self.assert_code("property_sync_extension_bound", packed([extension(), extension()]))
        with patch.object(sync, "MAX_ENTRIES", 1):
            self.assert_code("property_sync_entry_bound", wire(1, [(('a',), 1), (('b',), 2)]))
        with patch.object(sync, "MAX_TOTAL_SEGMENTS", 2):
            self.assert_code("property_sync_segment_bound", packed([
                extension(1, [(('a',), 1)]), extension(2, [(('b', 0), None)])]))
        with patch.object(sync, "MAX_PATH_SEGMENTS", 1):
            self.assert_code("property_sync_path_bound", wire(1, [(('a', 'b'), 1)]))
        with patch.object(codec, "MAX_EXTENSION_BYTES", 1):
            self.assert_code("property_size_bound", wire(1, [(('x',), 1)]))
        self.assert_code("property_sync_extension_bound", b"\xdd\xff\xff\xff\xff")

    def test_shared_node_budget_counts_outer_extensions_and_all_payloads(self):
        # Array1 + ext1 + op1 + header1 + segment1 + value1 = six nodes.
        data = wire(1, [(('x',), 1)])
        with patch.object(codec, "MAX_NODES", 6):
            self.assertEqual(len(sync.decode_sync_paths(data).entries), 1)
        with patch.object(codec, "MAX_NODES", 5):
            self.assert_code("property_node_bound", data)
        # A new extension cannot reset the shared budget.
        with patch.object(codec, "MAX_NODES", 3):
            self.assert_code("property_node_bound", packed([extension(), extension()]))
        with patch.object(codec, "MAX_NODES", 0):
            self.assert_code("property_node_bound", b"\x90")

    def test_depth_string_binary_and_container_bounds_apply_to_skipped_values(self):
        # Outer array depth0, extension1, payload nodes2, nested array items3.
        with patch.object(codec, "MAX_DEPTH", 2):
            self.assertEqual(sync.decode_sync_paths(wire(1, [(('x',), [])])).entries[0].value_kind, "array")
            self.assert_code("property_depth_bound", wire(1, [(('x',), [None])]))
        for limit, data in (("MAX_STRING_BYTES", wire(1, [(('x',), "abc")])),
                            ("MAX_BINARY_BYTES", wire(1, [(('x',), b"abc")])),
                            ("MAX_CONTAINER_ITEMS", wire(1, [(('x',), [1, 2, 3])]))):
            with patch.object(codec, limit, 2):
                self.assert_code("property_size_bound", data)
        self.assert_code("property_string_invalid", raw_value(b"\xa1\xff"))
        self.assert_code("property_wire_invalid", raw_value(b"\xc1"))
        self.assert_code("property_truncated", raw_value(b"\xcd\x00"))

    def test_results_are_frozen_repr_hidden_and_errors_never_expose_unknown_codes(self):
        result = sync.decode_sync_paths(wire(1, [(('PRIVATE_PATH',), "PRIVATE_VALUE")]))
        self.assertEqual(repr(result), "<SyncPaths>")
        self.assertEqual(repr(result.entries[0]), "<PropertyChange>")
        self.assertEqual([item.name for item in fields(result.entries[0])], ["operation", "path", "value_kind"])
        for value in (result, result.entries[0], sync.PropertySyncError("PRIVATE_ERROR"), sync.PropertySyncError([])):
            self.assertNotIn("PRIVATE", repr(value))
        with self.assertRaises(FrozenInstanceError):
            result.entries = ()
        with self.assertRaises(FrozenInstanceError):
            result.entries[0].path = ()
        failure = codec.PropertyCodecError("property_truncated")
        failure.code = "PRIVATE_ALTERED_CODE"
        with patch.object(codec._Reader, "byte", side_effect=failure):
            self.assert_code("property_sync_input_invalid", b"\x90")

    def test_seeded_arbitrary_inputs_only_return_bounded_paths_or_fixed_errors(self):
        generator = random.Random(3848703)
        for _ in range(1500):
            arbitrary = generator.randbytes(generator.randrange(65))
            for data in (arbitrary, raw_extension(arbitrary), raw_value(arbitrary)):
                try:
                    result = sync.decode_sync_paths(data)
                except sync.PropertySyncError as error:
                    self.assertIn(error.code, sync.ERROR_CODES)
                else:
                    self.assertIs(type(result), sync.SyncPaths)
                    self.assertLessEqual(len(result.entries), sync.MAX_ENTRIES)
                    self.assertTrue(all(entry.value_kind in sync.VALUE_KINDS for entry in result.entries))


if __name__ == "__main__":
    unittest.main()
