"""Synthetic hello/key-log tests; no game, real secrets, capture, or network."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tls_capture_probe as tls


RANDOM = bytes(range(32))
SECRET = bytes(range(48))


def vec(data, width=2):
    return len(data).to_bytes(width, "big") + data


def record(data):
    return b"\x16\x03\x03" + vec(data)


def hello(client=True, tls13=False, random=None, cipher=None, fragmented=False):
    cipher = cipher or (0x1301 if tls13 else 0xC02F)
    random = random if random is not None else (RANDOM if client else b"s" * 32)
    body = b"\x03\x03" + random + b"\0"
    body += vec(cipher.to_bytes(2, "big")) + b"\x01\0" if client else cipher.to_bytes(2, "big") + b"\0"
    if tls13:
        version = b"\x02\x03\x04" if client else b"\x03\x04"
        body += vec(b"\0\x2b" + vec(version))
    message = bytes([1 if client else 2]) + vec(body, 3)
    return record(message[:11]) + record(message[11:]) if fragmented else record(message)


def keyline(label="CLIENT_RANDOM", random=RANDOM, secret=SECRET):
    return f"{label} {random.hex()} {secret.hex()}\n".encode()


class TlsCaptureProbeTest(unittest.TestCase):
    def test_tls12_matching_material_is_not_decryption_or_ownership(self):
        report = tls.correlate(hello(), hello(False), io.BytesIO(keyline()))
        self.assertTrue(report["matching_material_present"])
        for name in ("process_ownership_verified", "tls_decryption_verified", "career_access_verified", "network_requests_sent", "secrets_output"):
            self.assertFalse(report[name])
        self.assertNotIn(SECRET.hex(), json.dumps(report))
        self.assertNotIn(RANDOM.hex(), json.dumps(report))

    def test_tls13_needs_matching_both_direction_handshake_and_app_secrets(self):
        keys = b"".join(keyline(label, secret=SECRET[:32]) for label in tls.TLS13_LABELS)
        report = tls.correlate(hello(tls13=True), hello(False, tls13=True), io.BytesIO(keys))
        self.assertTrue(report["matching_material_present"])
        self.assertEqual(report["tls_version"], "1.3")
        missing = tls.correlate(hello(tls13=True), hello(False, tls13=True), io.BytesIO(keys.splitlines(keepends=True)[0]))
        self.assertFalse(missing["matching_material_present"])

    def test_tls13_hash_length_must_match_cipher(self):
        keys = b"".join(keyline(label, secret=SECRET[:32]) for label in tls.TLS13_LABELS)
        report = tls.correlate(hello(tls13=True, cipher=0x1302), hello(False, tls13=True, cipher=0x1302), io.BytesIO(keys))
        self.assertFalse(report["matching_material_present"])

    def test_launcher_or_old_session_random_does_not_match(self):
        report = tls.correlate(hello(), hello(False), io.BytesIO(keyline(random=b"x" * 32)))
        self.assertFalse(report["matching_material_present"])
        self.assertEqual(report["matching_key_labels"], [])

    def test_conflicting_secret_is_not_resolved_by_another_duplicate(self):
        keys = keyline() + keyline(secret=b"x" * 48) + keyline()
        report = tls.correlate(hello(), hello(False), io.BytesIO(keys))
        self.assertFalse(report["matching_material_present"])
        self.assertGreater(report["keylog"]["conflicting_entries"], 0)

    def test_duplicate_and_unsupported_labels_are_counted_without_disclosure(self):
        entries, report = tls.read_keylog(io.BytesIO(b"# comment\n" + keyline() * 2 + keyline("EXPORTER_SECRET")))
        self.assertEqual(report["duplicate_lines"], 1)
        self.assertEqual(report["ignored_lines"], 1)
        self.assertEqual(len(entries), 1)
        self.assertNotIn(SECRET, [v for v in entries.values()])

    def test_bad_keylog_lines_do_not_match_or_leak(self):
        report = tls.correlate(hello(), hello(False), io.BytesIO(b"private-invalid-credential\n" + keyline(secret=b"x" * 32)))
        self.assertFalse(report["matching_material_present"])
        self.assertEqual(report["keylog"]["invalid_lines"], 2)
        self.assertNotIn("private-invalid-credential", json.dumps(report))

    def test_fragments_across_tls_records_are_reassembled(self):
        report = tls.correlate(hello(fragmented=True), hello(False, fragmented=True), io.BytesIO(keyline()))
        self.assertTrue(report["matching_material_present"])

    def test_encrypted_only_and_arbitrary_prefix_are_not_searched(self):
        for client in (b"\x17\x03\x03\0\x03abc", b"prefix" + hello(), b"", hello()[:-1]):
            report = tls.correlate(client, hello(False), io.BytesIO(keyline()))
            self.assertFalse(report["matching_material_present"])
            self.assertFalse(report["client_hello_present"])

    def test_missing_server_hello_never_reads_key_file(self):
        source = io.BytesIO(keyline())
        report = tls.correlate(hello(), b"", source)
        self.assertTrue(report["client_hello_present"])
        self.assertFalse(report["server_hello_present"])
        self.assertEqual(source.tell(), 0)

    def test_retry_and_unoffered_cipher_are_explicit(self):
        report = tls.correlate(hello(), hello(False, random=tls.HELLO_RETRY_RANDOM))
        self.assertEqual(report["outcome"], "hello_retry_requires_full_handshake_analysis")
        report = tls.correlate(hello(), hello(False, cipher=0xC030))
        self.assertEqual(report["outcome"], "unoffered_cipher_suite")

    def test_inconsistent_versions_and_ciphers_are_rejected(self):
        cases = (
            (hello(tls13=True, cipher=0xC02F), hello(False), "inconsistent_tls12_hellos"),
            (hello(cipher=0x1301), hello(False, cipher=0x1301), "inconsistent_tls12_hellos"),
            (hello(cipher=0x1301), hello(False, tls13=True), "inconsistent_tls13_hellos"),
        )
        for client, server, outcome in cases:
            with self.subTest(outcome=outcome):
                source = io.BytesIO(keyline())
                report = tls.correlate(client, server, source)
                self.assertEqual(report["outcome"], outcome)
                self.assertFalse(report["matching_material_present"])
                self.assertEqual(source.tell(), 0)

    def test_hello_inner_lengths_are_checked(self):
        for body in (b"\x03\x03" + RANDOM + b"\xff", b"\x03\x03" + RANDOM + b"\0\0\x03abc\x01\0"):
            with self.assertRaises(tls.EvidenceError):
                tls.parse_hello(body, True)

    def test_resource_bounds(self):
        with patch.object(tls, "MAX_FLOW_BYTES", 3):
            self.assertEqual(tls.correlate(hello(), hello(False))["outcome"], "flow_limit_exceeded")
        with patch.object(tls, "MAX_KEYLOG_BYTES", 3), self.assertRaises(tls.EvidenceError):
            tls.read_keylog(io.BytesIO(keyline()))
        with self.assertRaises(tls.EvidenceError):
            tls.read_keylog(io.BytesIO(b"x" * (tls.MAX_KEYLOG_LINE + 1)))

    def test_default_is_metadata_only(self):
        with patch.object(tls, "load_selected_flow") as read, patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(tls.main(["synthetic.pcapng"]), 0)
            read.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["outcome"], "metadata_only")

    def test_malformed_capture_header_is_reported_without_content(self):
        try:
            import dpkt
        except ImportError:
            self.skipTest("optional dpkt missing")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.pcapng"
            path.write_bytes(b"private-malformed-packet")
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                result = tls.main([str(path), "--inspect", "--client-ip", "192.0.2.1",
                                   "--client-port", "12345", "--server-ip", "198.51.100.1",
                                   "--server-port", "443"])
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(output.getvalue())["outcome"], "unparsed_capture_header")
            self.assertNotIn("private-malformed-packet", output.getvalue())

    def test_pcap_flow_selection_and_conflict(self):
        try:
            import dpkt
        except ImportError:
            self.skipTest("optional dpkt missing")
        import socket

        def packet(client, data, seq, port=12345, flags=dpkt.tcp.TH_ACK):
            src, dst = ("192.0.2.1", "198.51.100.1") if client else ("198.51.100.1", "192.0.2.1")
            tcp = dpkt.tcp.TCP(sport=port if client else 443, dport=443 if client else port, seq=seq, flags=flags, data=data)
            ip = dpkt.ip.IP(src=socket.inet_aton(src), dst=socket.inet_aton(dst), p=6, data=tcp)
            return bytes(dpkt.ethernet.Ethernet(src=b"a" * 6, dst=b"b" * 6, type=0x0800, data=ip))

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.pcapng"
            with path.open("wb") as source:
                writer = dpkt.pcapng.Writer(source)
                writer.writepkt(packet(True, hello(), 100), ts=1)
                writer.writepkt(packet(False, hello(False), 200), ts=2)
                writer.writepkt(packet(True, b"unrelated-launcher-data", 300, port=54321), ts=3)
            streams, count = tls.load_selected_flow(path, "192.0.2.1", 12345, "198.51.100.1", 443)
            self.assertEqual(count, 2)
            self.assertEqual(streams["client"], hello())
            with path.open("wb") as source:
                writer = dpkt.pcapng.Writer(source)
                writer.writepkt(packet(True, hello(), 100), ts=1)
                writer.writepkt(packet(True, b"conflict", 104), ts=2)
            with self.assertRaisesRegex(tls.EvidenceError, "tcp_gap_wrap_or_overlap_conflict"):
                tls.load_selected_flow(path, "192.0.2.1", 12345, "198.51.100.1", 443)
            with path.open("wb") as source:
                writer = dpkt.pcapng.Writer(source)
                writer.writepkt(packet(True, hello()[:10], 100), ts=1)
                writer.writepkt(packet(True, hello()[11:], 111), ts=2)
            with self.assertRaisesRegex(tls.EvidenceError, "tcp_gap_wrap_or_overlap_conflict"):
                tls.load_selected_flow(path, "192.0.2.1", 12345, "198.51.100.1", 443)
            with path.open("wb") as source:
                writer = dpkt.pcapng.Writer(source)
                writer.writepkt(packet(True, b"", 100, flags=dpkt.tcp.TH_SYN), ts=1)
                writer.writepkt(packet(True, b"", 200, flags=dpkt.tcp.TH_SYN), ts=2)
            with self.assertRaisesRegex(tls.EvidenceError, "multiple_tcp_connections_selected"):
                tls.load_selected_flow(path, "192.0.2.1", 12345, "198.51.100.1", 443)


if __name__ == "__main__":
    unittest.main()
