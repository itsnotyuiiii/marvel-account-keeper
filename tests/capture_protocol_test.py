"""Synthetic-only coverage for optional, local capture analysis tooling."""
import importlib.util
from pathlib import Path
import runpy
import unittest
import zlib


@unittest.skipUnless(importlib.util.find_spec("dpkt"), "optional research dependency dpkt is not installed")
class CaptureProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "summarize-career-capture.py"))

    def test_payload_hints_are_not_payload_disclosures(self):
        hint = self.helper["payload_hint"]
        self.assertEqual(hint(b"POST /secret HTTP/1.1\r\nAuthorization: secret"), "plaintext_http")
        self.assertEqual(hint(b'\x17\x03\x03\x00\x03abc'), "tls_record_header")
        self.assertEqual(hint(b'{"token":"secret"}'), "json_document")
        self.assertEqual(hint(b'\x00\xff\x80'), "unidentified_payload")

    def test_overlapping_offload_packets_are_counted_once(self):
        result = self.helper["stream_hints"]([(104, b"efgh"), (100, b"abcdefgh"), (100, b"abcd")])
        self.assertEqual(result["unique_payload_bytes"], 8)
        self.assertEqual(result["contiguous_runs"], 1)
        self.assertFalse(result["overlap_conflict"])

    def test_sequence_gaps_remain_distinct(self):
        result = self.helper["stream_hints"]([(100, b"ab"), (104, b"cd")])
        self.assertEqual(result["contiguous_runs"], 2)
        self.assertEqual(result["unique_payload_bytes"], 4)

    def test_conflicting_overlap_is_reported(self):
        result = self.helper["stream_hints"]([(100, b"abcd"), (102, b"XY")])
        self.assertTrue(result["overlap_conflict"])

    def test_length_prefix_hypotheses_find_synthetic_records(self):
        result = self.helper["framing_hypotheses"](b'\x00\x03abc\x00\x04defg')
        self.assertTrue(any(c["width"] == 2 and c["endian"] == "big"
                            and c["length_offset"] == 0 and c["header_bytes"] == 2
                            and not c["length_includes_header"] and c["record_count"] == 2
                            for c in result))

    def test_one_record_is_insufficient_framing_evidence(self):
        self.assertEqual(self.helper["framing_hypotheses"](b'\x00\x03abc'), [])

    @staticmethod
    def mos_record(body=b"secret", flags=0, extension=None):
        extra = b"" if extension is None else len(extension).to_bytes(2, "little")
        trailer = b"" if extension is None else extension
        size = 7 + len(extra) + len(body) + len(trailer)
        return ((size | flags << 28).to_bytes(4, "little") + b"\x12\x34\x56"
                + extra + body + trailer)

    def test_mos_masked_length_and_non_disclosure(self):
        data = self.mos_record(flags=1) + self.mos_record(flags=2)
        result = self.helper["mos_packet_hypotheses"](data)
        self.assertIn({"skipped_prefix_bytes": 0, "record_count": 2,
                       "record_bytes": [13, 13], "extended_records": 0}, result)
        self.assertNotIn("secret", str(result))

    def test_mos_extension_layout(self):
        data = self.mos_record(flags=8, extension=b"private") + self.mos_record()
        result = self.helper["mos_packet_hypotheses"](data)
        self.assertTrue(any(c["skipped_prefix_bytes"] == 0 and c["extended_records"] == 1
                            for c in result))
        self.assertNotIn("private", str(result))

    def test_mos_requires_multiple_complete_records(self):
        probe = self.helper["mos_packet_hypotheses"]
        record = self.mos_record()
        for data in (b"", record, record + record[:-1], record + record + b"\xff"):
            self.assertEqual(probe(data), [])

    def test_mos_rejects_invalid_lengths(self):
        probe = self.helper["mos_packet_hypotheses"]
        for word in (0, 6, 0x04000008, 0x80000008):
            self.assertEqual(probe(word.to_bytes(4, "little") + b"\xff" * 12), [])
        invalid_extension = (10 | 8 << 28).to_bytes(4, "little") + b"\xff" * 6
        self.assertEqual(probe(invalid_extension + self.mos_record()), [])

    def test_mos_prefix_search_is_bounded(self):
        data = self.mos_record() * 2
        probe = self.helper["mos_packet_hypotheses"]
        self.assertTrue(any(c["skipped_prefix_bytes"] == 32 for c in probe(b"\xff" * 32 + data)))
        self.assertEqual(probe(b"\xff" * 33 + data), [])

    @staticmethod
    def socialmatrix_opening(fields, server=False, suite=1):
        body = bytes((suite, 0)) if server else b""
        body += b"".join(len(value).to_bytes(2, "little") + value for value in fields)
        return (len(body) + 4).to_bytes(4, "little") + body

    def test_socialmatrix_server_layout_exposes_lengths_not_values(self):
        opening = self.socialmatrix_opening((b"nonce_secret", b"server_secret", b"client_secret"), server=True)
        result = self.helper["socialmatrix_opening_hypotheses"](opening + b"encrypted_secret")
        server = next(item for item in result if item["kind"] == "server_hello")
        self.assertEqual(server["variable_field_bytes"], [12, 13, 13])
        self.assertEqual(server["message_bytes"], len(opening))
        self.assertEqual(server["trailing_stream_bytes"], 16)
        self.assertEqual(server["secure_suite"], 1)
        self.assertFalse(server["protocol_verified"])
        self.assertNotIn("secret", str(result))

    def test_socialmatrix_ack_and_empty_fields_are_structural_only(self):
        probe = self.helper["socialmatrix_opening_hypotheses"]
        for fields in ((b"sign_secret", b"", b""), (b"", b"", b"")):
            result = probe(self.socialmatrix_opening(fields))
            ack = next(item for item in result if item["kind"] == "client_ack")
            self.assertEqual(ack["variable_field_bytes"], list(map(len, fields)))
            self.assertFalse(ack["protocol_verified"])
            self.assertNotIn("secret", str(result))

    def test_socialmatrix_opening_rejects_truncation_lengths_padding_and_offsets(self):
        probe = self.helper["socialmatrix_opening_hypotheses"]
        opening = self.socialmatrix_opening((b"test", b"key-one", b"key-two"), server=True)
        for length in range(len(opening)):
            self.assertEqual(probe(opening[:length]), [])
        malformed = (
            b"\xff" + opening,
            b"\x00\x00\x00\x00" + opening[4:],
            b"\xff\xff\xff\xff" + opening[4:],
            opening[:6] + b"\xff\xff" + opening[8:],
            (len(opening) + 1).to_bytes(4, "little") + opening[4:] + b"\x00",
            self.socialmatrix_opening((b"test", b"key-one", b"key-two"), server=True, suite=255),
        )
        for value in malformed:
            self.assertEqual(probe(value), [])

    def test_socialmatrix_opening_reassembles_overlap_but_not_gaps_or_conflicts(self):
        opening = self.socialmatrix_opening((b"test", b"key-one", b"key-two"), server=True)
        hints = self.helper["stream_hints"]
        result = hints([(100, opening[:10]), (106, opening[6:])])
        self.assertTrue(result["socialmatrix_opening_hypotheses"][0])
        result = hints([(100, opening[:10]), (111, opening[11:])])
        self.assertFalse(any(result["socialmatrix_opening_hypotheses"]))
        result = hints([(100, opening), (108, b"different")])
        self.assertTrue(result["overlap_conflict"])
        self.assertFalse(any(result["socialmatrix_opening_hypotheses"]))

    def test_zstd_signature_is_metadata_only(self):
        self.assertEqual(self.helper["payload_hint"](b"\x28\xb5\x2f\xfdsecret"), "zstd_frame_header")
        result = self.helper["stream_hints"]([(100, b"xx\x28\xb5\x2f\xfdsecret")])
        self.assertEqual(result["zstd_frame_header_offsets"], [[2]])
        self.assertNotIn("secret", str(result))

    @unittest.skipUnless(importlib.util.find_spec("msgpack"), "optional msgpack dependency unavailable")
    def test_msgpack_hypotheses_do_not_disclose_values_or_accept_scalars(self):
        import msgpack
        probe = self.helper["msgpack_hypotheses"]
        result = probe(msgpack.packb({"token": "secret", "career": [1, 2, 3]}))
        self.assertTrue(any(c["header_bytes"] == 0 for c in result["candidates"]))
        self.assertNotIn("secret", str(result))
        self.assertEqual(probe(b'\x01\x02\x03')["candidates"], [])

    def test_decompression_does_not_disclose_values(self):
        raw = b'{"career":"secret","token":"secret"}'
        result = self.helper["stream_hints"]([(100, b"prefix" + zlib.compress(raw))])
        self.assertNotIn("secret", str(result))
        self.assertEqual(result["zlib_streams"][0]["header_offset"], 6)
        self.assertEqual(result["zlib_streams"][0]["known_field_markers"], ["career"])

    def test_ethernet_and_observed_snap_framing(self):
        import dpkt
        packet = dpkt.ip.IP(src=b"\x0a\x00\x00\x01", dst=b"\x08\x08\x08\x08", p=6,
                            data=dpkt.tcp.TCP(sport=12345, dport=12101))
        ethernet = bytes(dpkt.ethernet.Ethernet(type=0x800, data=packet))
        snap = b"\x00" * 24 + b"\xaa\xaa\x03\x00\x00\x00\x08\x00" + bytes(packet)
        for frame in (ethernet, snap):
            parsed = self.helper["extract_ip"](frame)
            self.assertIsInstance(parsed, dpkt.ip.IP)
            self.assertEqual(parsed.data.dport, 12101)


if __name__ == "__main__":
    unittest.main()
