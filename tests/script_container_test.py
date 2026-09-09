"""Synthetic tests only; no installed game, account data, or archive key needed."""
import contextlib
import importlib.util
import io
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib


@unittest.skipUnless(importlib.util.find_spec("xdis"), "optional xdis dependency unavailable")
class ScriptContainerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "script_container", Path(__file__).resolve().parents[1] / "scripts" / "inspect-script-container.py")
        cls.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.helper)

    @staticmethod
    def archive(offset=0, size=3):
        return (struct.pack("<4sQIII", b"MCPK", 0, 24, 24, 40)
                + struct.pack("<4I", 123, offset, size, 999) + b"abc")

    def test_index_and_payload_bounds(self):
        payload, entries = self.helper.parse_index(self.archive(), verify_hash=False)
        self.assertEqual(payload, 40)
        self.assertEqual(self.helper.read_entry(self.archive(), payload, entries, 0), b"abc")
        # The fourth index field is NOT the decoded marshal byte length.
        self.assertEqual(entries[0][3], 999)
        for offset, size in ((1, 3), (0, 4), (0xffffffff, 1)):
            with self.assertRaises(ValueError):
                self.helper.parse_index(self.archive(offset, size), verify_hash=False)

    def test_unknown_build_and_bad_header_rejected(self):
        with self.assertRaises(ValueError):
            self.helper.parse_index(self.archive())
        for data in (b"MCPK", b"FAIL" + self.archive()[4:],
                     struct.pack("<4sQIII", b"MCPK", 0, 24, 25, 40) + b"x" * 20):
            with self.assertRaises(ValueError):
                self.helper.parse_index(data, verify_hash=False)

    def test_zlib_requires_complete_bounded_stream(self):
        compressed = zlib.compress(b"synthetic payload")
        self.assertEqual(self.helper.bounded_zlib(compressed), b"synthetic payload")
        for data in (compressed[:-1], compressed + b"extra"):
            with self.assertRaises(ValueError):
                self.helper.bounded_zlib(data)
        with patch.object(self.helper, "MAX_DECODED", 8):
            with self.assertRaises(ValueError):
                self.helper.bounded_zlib(compressed)

    def test_reverse_transform_boundaries(self):
        original = bytes(range(256))
        reversed_data = original[::-1]
        encoded = bytes(x ^ 156 for x in reversed_data[:130]) + reversed_data[130:]
        self.assertEqual(self.helper.reverse_module(encoded), original)
        self.assertEqual(self.helper.reverse_module(b""), b"")

    def test_rotor_each_call_restarts_without_mutating_input(self):
        rotor = self.helper.RotorDecoder(b"synthetic-test-only")
        raw = bytes(range(256)) * 2
        first = rotor.decrypt(raw)
        self.assertEqual(first, rotor.decrypt(raw))
        self.assertEqual(len(first), len(raw))
        self.assertNotEqual(first, raw)
        self.assertEqual(rotor.decrypt(b""), b"")
        self.assertNotEqual(first, self.helper.RotorDecoder(b"different").decrypt(raw))

    def test_parser_diagnostics_are_not_disclosed(self):
        sentinel = object()
        def fake_loader(*args):
            print("secret-constant")
            return sentinel
        output = io.StringIO()
        with patch.object(self.helper, "load_code", fake_loader), contextlib.redirect_stdout(output):
            self.assertIs(self.helper.parse_code(b"synthetic"), sentinel)
        self.assertEqual(output.getvalue(), "")

    def test_symbol_search_uses_names_not_constant_values(self):
        child = SimpleNamespace(co_name="lookup", co_names=("xyq_client",),
                                co_consts=("secret",), co_code=b"")
        root = SimpleNamespace(co_name="module", co_names=(),
                               co_consts=(child, "hidden_constant"), co_code=b"")
        self.assertEqual(self.helper.matching_symbols(root, ["xyq_client"]),
                         [{"function": "lookup", "symbols": ["xyq_client"]}])
        self.assertEqual(self.helper.matching_symbols(root, ["hidden_constant"]), [])


if __name__ == "__main__":
    unittest.main()
