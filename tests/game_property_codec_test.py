"""Byte-authored and msgpack-authored synthetic properties; no game input."""
import math
import random
import struct
import unittest
from unittest.mock import patch

import msgpack

import game_property_codec as codec


def packed(value):
    return msgpack.packb(value, use_bin_type=True)


def frame(payload, *, tag=0xC7, code=0):
    if tag in (0xC7, 0xC8, 0xC9):
        size = {0xC7: 1, 0xC8: 2, 0xC9: 4}[tag]
        return bytes([tag]) + len(payload).to_bytes(size, "big") + bytes([code & 255]) + payload
    assert len(payload) == 1 << (tag - 0xD4)
    return bytes([tag, code & 255]) + payload


def payload(pairs=(), *, class_name="A"):
    return packed(class_name) + b"".join(packed(key) + packed(value) for key, value in pairs)


def source(pairs=(), *, class_name="A"):
    raw = payload(pairs, class_name=class_name)
    return frame(raw, tag=0xC7 if len(raw) <= 255 else 0xC9)


def value_source(raw):
    return frame(packed("A") + packed("x") + raw, tag=0xC9)


class GamePropertyCodecTest(unittest.TestCase):
    def decode(self, data, *, wanted=("x",), class_name="A"):
        return codec.decode_named_fields(data, expected_class=class_name, wanted_names=wanted)

    def assert_code(self, expected, data, *, wanted=("x",), class_name="A"):
        with self.assertRaises(codec.PropertyCodecError) as caught:
            self.decode(data, wanted=wanted, class_name=class_name)
        self.assertEqual(caught.exception.code, expected)
        self.assertEqual(str(caught.exception), expected)
        self.assertIn(expected, codec.ERROR_CODES)

    def test_byte_authored_ext0_is_concatenated_class_and_pairs_not_container(self):
        # C7 payload length=10 excludes type0. Payload: 'A','uid',42,'x',-1.
        raw = b"\xc7\x0a\x00\xa1A\xa3uid\x2a\xa1x\xff"
        result = self.decode(raw, wanted=("uid", "x", "absent"))
        self.assertEqual(result.fields, {"uid": 42, "x": -1})
        self.assertNotIn("absent", result.fields)
        self.assertEqual(self.decode(source(), wanted=()).fields, {})

    def test_variable_and_fixed_extension_headers_match_standard_wire_lengths(self):
        raw = payload((("x", 7),))
        for tag in (0xC7, 0xC8, 0xC9):
            with self.subTest(tag=tag):
                framed = frame(raw, tag=tag)
                native = msgpack.unpackb(framed)
                self.assertEqual(native, msgpack.ExtType(0, raw))
                self.assertEqual(self.decode(framed).fields, {"x": 7})
        for tag, name in ((0xD5, "A"), (0xD6, "AAA"), (0xD7, "A" * 7), (0xD8, "A" * 15)):
            self.assertEqual(self.decode(frame(packed(name), tag=tag), wanted=(), class_name=name).fields, {})

    def test_exact_exttype_and_opaque_inputs_support_explicit_nested_read(self):
        child = msgpack.ExtType(0, payload((("ranks", {18: {"level": 9}}),), class_name="RankGameComponent"))
        top = msgpack.ExtType(0, payload((("RankGame", child), ("private", "PRIVATE_DATA")), class_name="GameServiceAvatar"))
        result = self.decode(top, wanted=("RankGame",), class_name="GameServiceAvatar")
        extension = result.fields["RankGame"]
        self.assertIs(type(extension), codec.OpaqueExtension)
        self.assertEqual(extension.code, 0)
        self.assertEqual(extension.data, child.data)
        nested = self.decode(extension, wanted=("ranks",), class_name="RankGameComponent")
        self.assertEqual(nested.fields, {"ranks": {18: {"level": 9}}})
        self.assertNotIn("private", result.fields)

    def test_nested_extension_class_is_not_inferred_and_its_payload_is_opaque(self):
        malformed = msgpack.ExtType(0, b"PRIVATE_NOT_A_PROPERTY")
        result = self.decode(source((("x", [malformed, {"next": malformed}]),)))
        self.assertEqual(result.fields["x"][0].data, malformed.data)
        self.assertIs(type(result.fields["x"][1]["next"]), codec.OpaqueExtension)
        with self.assertRaises(codec.PropertyCodecError):
            self.decode(result.fields["x"][0])
        numeric = msgpack.ExtType(0, payload(((1, "PRIVATE"),)))
        nested = self.decode(source((("x", numeric),))).fields["x"]
        self.assert_code("property_numeric_key_unreviewed", nested)

    def test_extension_codes_including_timestamp_are_preserved_not_interpreted(self):
        for tag, length in ((0xD4, 1), (0xD5, 2), (0xD6, 4), (0xD7, 8), (0xD8, 16)):
            raw = frame(b"\x00" * length, tag=tag, code=-1)
            extension = self.decode(value_source(raw)).fields["x"]
            self.assertIs(type(extension), codec.OpaqueExtension)
            self.assertEqual((extension.code, extension.data), (-1, b"\x00" * length))
        self.assert_code("property_extension_type", msgpack.ExtType(7, payload()))
        self.assert_code("property_extension_type", frame(payload(), code=-1))

    def test_all_integer_tags_keep_values_without_sentinel_conversion(self):
        cases = ((b"\x00", 0), (b"\x7f", 127), (b"\xe0", -32), (b"\xff", -1),
                 (b"\xcc\xff", 255), (b"\xcd\x01\x00", 256),
                 (b"\xce\x80\x00\x00\x00", 2**31), (b"\xcf" + b"\xff" * 8, 2**64 - 1),
                 (b"\xd0\x80", -128), (b"\xd1\x80\x00", -(2**15)),
                 (b"\xd2\x80" + b"\x00" * 3, -(2**31)),
                 (b"\xd3\x80" + b"\x00" * 7, -(2**63)))
        for raw, expected in cases:
            with self.subTest(raw=raw):
                value = self.decode(value_source(raw)).fields["x"]
                self.assertIs(type(value), int)
                self.assertEqual(value, expected)

    def test_nil_bool_float_and_nonfinite_primitives_are_not_normalized(self):
        for raw, expected in ((b"\xc0", None), (b"\xc2", False), (b"\xc3", True),
                              (b"\xca" + struct.pack(">f", 1.5), 1.5),
                              (b"\xcb" + struct.pack(">d", -2.25), -2.25)):
            actual = self.decode(value_source(raw)).fields["x"]
            self.assertIs(type(actual), type(expected))
            self.assertEqual(actual, expected)
        for value in (float("inf"), float("-inf"), float("nan")):
            actual = self.decode(value_source(packed(value))).fields["x"]
            self.assertTrue(math.isnan(actual) if math.isnan(value) else actual == value)

    def test_string_binary_array_and_map_tags_preserve_selected_values(self):
        cases = (
            (b"\xa2hi", "hi"), (b"\xd9\x02hi", "hi"), (b"\xda\x00\x02hi", "hi"),
            (b"\xdb\x00\x00\x00\x02hi", "hi"),
            (b"\xc4\x02\x00\xff", b"\x00\xff"), (b"\xc5\x00\x02\x00\xff", b"\x00\xff"),
            (b"\xc6\x00\x00\x00\x02\x00\xff", b"\x00\xff"),
            (b"\x92\x01\x02", [1, 2]), (b"\xdc\x00\x02\x01\x02", [1, 2]),
            (b"\xdd\x00\x00\x00\x02\x01\x02", [1, 2]),
            (b"\x81\x01\xa1v", {1: "v"}), (b"\xde\x00\x01\x01\xa1v", {1: "v"}),
            (b"\xdf\x00\x00\x00\x01\x01\xa1v", {1: "v"}),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(self.decode(value_source(raw)).fields, {"x": expected})
        self.assertEqual(self.decode(source((("x", "ゆいᶻᶻᶻ"),))).fields["x"], "ゆいᶻᶻᶻ")

    def test_unselected_containers_and_binary_and_extensions_are_not_retained(self):
        raw = source((("private", [{"value": b"PRIVATE" * 1000}, msgpack.ExtType(99, b"PRIVATE_EXT")]), ("x", 3)))
        with patch.object(codec, "OpaqueExtension", side_effect=AssertionError("unselected extension constructed")):
            self.assertEqual(self.decode(raw).fields, {"x": 3})

    def test_every_named_field_duplicate_is_rejected_even_when_unselected(self):
        for wanted in (("x",), ()):
            self.assert_code("property_duplicate_field", source((("private", 1), ("private", 2))), wanted=wanted)

    def test_every_numeric_property_key_is_unreviewed_including_zero_and_negative(self):
        for key in (0, 1, 65535, -1, 1.5, float("nan")):
            for wanted in (("x",), ()):
                self.assert_code("property_numeric_key_unreviewed", source(((key, "PRIVATE"),)), wanted=wanted)
        for key in (True, None, b"x", ["x"], {"x": 1}):
            self.assert_code("property_key_invalid", source(((key, 1),)))

    def test_selected_map_duplicate_keys_rejected_and_skipped_maps_structural_only(self):
        raw = value_source(b"\x82\x01\x07\xcc\x01\x08")
        self.assert_code("property_duplicate_map_key", raw)
        self.assertEqual(self.decode(raw, wanted=()).fields, {})
        # Python would otherwise silently merge bool True and integer 1.
        self.assert_code("property_duplicate_map_key", value_source(b"\x82\xc3\x07\x01\x08"))
        complex_key = value_source(b"\x81\x91\x01\x07")
        self.assert_code("property_map_key_unsupported", complex_key)
        self.assertEqual(self.decode(complex_key, wanted=()).fields, {})

    def test_requested_class_exact_and_first_item_must_be_bounded_string(self):
        self.assert_code("property_class_mismatch", source(class_name="PRIVATE_OTHER_CLASS"))
        for invalid in (None, True, 1, b"A", [], {}, ""):
            self.assert_code("property_class_invalid", frame(packed(invalid)))
        with patch.object(codec, "MAX_CLASS_BYTES", 1):
            self.assert_code("property_class_invalid", source(class_name="AA"))

    def test_outer_must_be_single_complete_ext0_without_trailing_data(self):
        for raw in (packed({}), packed([]), packed("A"), packed(None)):
            self.assert_code("property_outer_extension", raw)
        for suffix in (b"\x00", source(), b"PRIVATE_TRAILING"):
            self.assert_code("property_trailing_data", source() + suffix)
        self.assert_code("property_truncated", msgpack.ExtType(0, b""))

    def test_every_prefix_of_complete_frame_is_rejected_not_resynchronized(self):
        raw = source((("x", [1, {"key": b"value"}]),))
        for length in range(len(raw)):
            with self.subTest(length=length), self.assertRaises(codec.PropertyCodecError):
                self.decode(raw[:length])
        self.assertEqual(self.decode(raw).fields, {"x": [1, {"key": b"value"}]})

    def test_missing_value_and_truncated_value_are_distinct_fixed_errors(self):
        self.assert_code("property_incomplete_pair", frame(packed("A") + packed("x")))
        self.assert_code("property_truncated", value_source(b"\xcd\x00"))
        self.assert_code("property_truncated", value_source(b"\x92\x01"))
        self.assert_code("property_truncated", value_source(b"\x81\x01"))
        self.assert_code("property_truncated", value_source(b"\xc7\x04\x00\x01"))

    def test_reserved_tag_and_invalid_utf8_are_rejected_even_when_skipped(self):
        for wanted in (("x",), ()):
            self.assert_code("property_wire_invalid", value_source(b"\xc1"), wanted=wanted)
            self.assert_code("property_string_invalid", value_source(b"\xa1\xff"), wanted=wanted)

    def test_request_requires_exact_bounded_class_and_unique_literal_tuple_names(self):
        for name in (None, b"A", "", "\ud800", "A" * (codec.MAX_CLASS_BYTES + 1)):
            self.assert_code("property_request_invalid", source(), class_name=name)
        for names in (["x"], "x", None, ("x", "x"), (True,), (b"x",), ("",),
                      ("\ud800",), ("x" * (codec.MAX_KEY_BYTES + 1),),
                      tuple(str(n) for n in range(codec.MAX_WANTED_FIELDS + 1))):
            self.assert_code("property_request_invalid", source(), wanted=names)

    def test_only_exact_supported_input_types_are_accepted(self):
        class BytesSubclass(bytes):
            pass
        class OpaqueSubclass(codec.OpaqueExtension):
            pass
        for value in (None, [], {}, bytearray(source()), memoryview(source()), BytesSubclass(source()),
                      OpaqueSubclass(0, payload()), codec.OpaqueExtension(True, payload()),
                      codec.OpaqueExtension(0, bytearray(payload())), codec.OpaqueExtension(128, payload())):
            self.assert_code("property_input_invalid", value)

    def test_input_extension_string_and_binary_bounds_apply_before_allocation(self):
        with patch.object(codec, "MAX_INPUT_BYTES", 1):
            self.assert_code("property_input_bound", source())
        with patch.object(codec, "MAX_EXTENSION_BYTES", 1):
            self.assert_code("property_size_bound", source())
            self.assert_code("property_input_bound", msgpack.ExtType(0, payload()))
        for name, raw in (("MAX_STRING_BYTES", b"\xdb\x00\x00\x00\x10"),
                          ("MAX_BINARY_BYTES", b"\xc6\x00\x00\x00\x10")):
            with patch.object(codec, name, 4):
                for wanted in (("x",), ()):
                    self.assert_code("property_size_bound", value_source(raw), wanted=wanted)
        self.assert_code("property_size_bound", value_source(b"\xc9\xff\xff\xff\xff\x00"))

    def test_property_key_and_field_count_bounds_include_unselected_fields(self):
        with patch.object(codec, "MAX_KEY_BYTES", 1):
            self.assert_code("property_key_invalid", source((("xx", 1),)))
        with patch.object(codec, "MAX_FIELDS", 1):
            self.assert_code("property_field_bound", source((("x", 1), ("y", 2))))
        self.assert_code("property_key_invalid", source((("", 1),)))

    def test_container_depth_and_node_bounds_apply_to_skipped_subtrees(self):
        for wanted in (("x",), ()):
            with patch.object(codec, "MAX_CONTAINER_ITEMS", 2):
                self.assert_code("property_size_bound", source((("x", [1, 2, 3]),)), wanted=wanted)
                self.assert_code("property_size_bound", source((("x", {1: 1, 2: 2, 3: 3}),)), wanted=wanted)
            with patch.object(codec, "MAX_DEPTH", 2):
                self.assert_code("property_depth_bound", source((("x", [[[1]]]),)), wanted=wanted)
                self.assertEqual(self.decode(source((("x", [[1]]),)), wanted=wanted).fields,
                                 {"x": [[1]]} if wanted else {})
            with patch.object(codec, "MAX_NODES", 5):
                self.assert_code("property_node_bound", source((("x", [1, 2, 3]),)), wanted=wanted)
            self.assert_code("property_size_bound", value_source(b"\xdd\xff\xff\xff\xff"), wanted=wanted)
            self.assert_code("property_size_bound", value_source(b"\xdf\xff\xff\xff\xff"), wanted=wanted)

    def test_results_and_errors_have_no_remote_data_in_repr(self):
        marker = "PRIVATE_DATA_NO_REPR"
        result = self.decode(source((("x", marker),)))
        self.assertEqual(result.fields, {"x": marker})
        self.assertEqual(repr(result), "<NamedFields>")
        self.assertEqual(repr(codec.OpaqueExtension(0, marker.encode())), "<OpaqueExtension>")
        for value in (result, codec.OpaqueExtension(0, marker.encode()), codec.PropertyCodecError(marker)):
            self.assertNotIn(marker, repr(value))
        self.assert_code("property_class_mismatch", source(class_name=marker))

    def test_seeded_arbitrary_wire_inputs_only_return_results_or_fixed_codec_errors(self):
        generator = random.Random(3848703)
        for _ in range(1500):
            arbitrary = generator.randbytes(generator.randrange(65))
            for raw in (arbitrary, value_source(arbitrary)):
                try:
                    result = self.decode(raw)
                except codec.PropertyCodecError as error:
                    self.assertIn(error.code, codec.ERROR_CODES)
                    self.assertEqual(str(error), error.code)
                else:
                    self.assertIs(type(result), codec.NamedFields)
                    self.assertTrue(set(result.fields) <= {"x"})

    def test_seeded_valid_values_agree_with_messagepack_primitives_and_containers(self):
        generator = random.Random(3848703)
        primitives = [None, True, False, 0, -1, -(2**63), 2**64 - 1, 1.25,
                      "", "ゆい", b"", b"\x00\xff", msgpack.ExtType(7, b"opaque")]
        def value(depth=0):
            choice = generator.randrange(3 if depth < 4 else 1)
            if choice == 0:
                return generator.choice(primitives)
            if choice == 1:
                return [value(depth + 1) for _ in range(generator.randrange(4))]
            return {str(n): value(depth + 1) for n in range(generator.randrange(4))}
        def expected(item):
            if type(item) is msgpack.ExtType:
                return codec.OpaqueExtension(item.code, item.data)
            if type(item) is list:
                return [expected(child) for child in item]
            if type(item) is dict:
                return {key: expected(child) for key, child in item.items()}
            return item
        for _ in range(200):
            original = value()
            self.assertEqual(self.decode(value_source(packed(original))).fields, {"x": expected(original)})


if __name__ == "__main__":
    unittest.main()
