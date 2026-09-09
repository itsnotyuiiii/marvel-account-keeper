"""Synthetic offline NDIS parsing; never record or open a real ETL in tests."""
import base64
import importlib.util
import ipaddress
import json
from pathlib import Path
import runpy
import struct
import unittest


HELPER = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "inspect-netsh-career.py"))


def event(*, outbound=True, flags=24, seq=100, ack=200, payload=b"secret-fixture", zero_length=False):
    src, dst = ("192.168.4.39", "35.236.255.68") if outbound else ("35.236.255.68", "192.168.4.39")
    sport, dport = (50000, 12101) if outbound else (12101, 50000)
    tcp = struct.pack("!HHIIBBHHH", sport, dport, seq, ack, 0x50, flags, 1024, 0, 0) + payload
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 0 if zero_length else 20 + len(tcp),
                     1, 0x4000, 64, 6, 0, ipaddress.ip_address(src).packed, ipaddress.ip_address(dst).packed)
    frame = b"\x08\x01" + bytes(22) + b"\xaa\xaa\x03\x00\x00\x00\x08\x00" + ip + tcp
    return {"at_utc": "2026-09-07T16:53:17Z", "keywords": str(0xC0010000),
            "declared_bytes": len(frame), "frame_base64": base64.b64encode(frame).decode()}


def wrap(*packets):
    return {"total_events": len(packets), "packet_events": list(packets), "unsupported_packet_events": 0}


class NetshCareerProtocolTest(unittest.TestCase):
    def test_header_only_has_no_payload_disclosure_or_career_claim(self):
        report = HELPER["analyze_events"](wrap(event()))
        self.assertTrue(report["all_recorded_packet_headers_in_scope"])
        self.assertFalse(report["protocol_hints_performed"])
        self.assertFalse(report["career_access_verified"])
        self.assertNotIn("secret-fixture", json.dumps(report))
        self.assertEqual(report["flows"][0]["streams"]["out"]["captured_payload_bytes"], 14)

    def test_empty_capture_is_not_verified(self):
        self.assertFalse(HELPER["analyze_events"](wrap())["all_recorded_packet_headers_in_scope"])

    def test_truncated_packet_blocks_all_protocol_hints(self):
        broken = event()
        broken["keywords"] = str(int(broken["keywords"]) | 0x02000000)
        report = HELPER["analyze_events"](wrap(event(), broken), True)
        self.assertFalse(report["all_recorded_packet_headers_in_scope"])
        self.assertFalse(report["protocol_hints_performed"])

    def test_explicit_truncated_header_inspection_never_fills_missing_bytes(self):
        packet = event(payload=b"synthetic" * 10)
        frame = base64.b64decode(packet["frame_base64"])[:-10]
        packet.update(frame_base64=base64.b64encode(frame).decode(), declared_bytes=len(frame),
                      keywords=str(int(packet["keywords"]) | 0x02000000))
        parsed = HELPER["packet_header"](packet, allow_truncated=True)
        self.assertEqual(len(parsed["payload"]), 80)
        self.assertEqual(parsed["missing_payload_bytes"], 10)
        report = HELPER["analyze_events"](wrap(packet), allow_truncated=True)
        self.assertTrue(report["all_recorded_packet_headers_in_scope"])
        self.assertFalse(report["all_recorded_packets_complete"])
        self.assertEqual(report["truncated_packet_count"], 1)
        packet["keywords"] = str(int(packet["keywords"]) & ~0x02000000)
        with self.assertRaises(ValueError):
            HELPER["packet_header"](packet, allow_truncated=True)

    def test_outside_ip_blocks_all_protocol_hints(self):
        broken = event()
        frame = bytearray(base64.b64decode(broken["frame_base64"]))
        frame[48:52] = ipaddress.ip_address("203.0.113.55").packed
        broken["frame_base64"] = base64.b64encode(frame).decode()
        self.assertFalse(HELPER["analyze_events"](wrap(event(), broken), True)["protocol_hints_performed"])

    def test_player_lookup_scope_is_separate_and_explicit(self):
        packet = event()
        frame = bytearray(base64.b64decode(packet["frame_base64"]))
        frame[48:52] = ipaddress.ip_address("34.60.237.47").packed
        packet["frame_base64"] = base64.b64encode(frame).decode()
        self.assertFalse(HELPER["analyze_events"](wrap(packet))["all_recorded_packet_headers_in_scope"])
        self.assertTrue(HELPER["analyze_events"](wrap(packet), approved_ips=HELPER["PLAYER_LOOKUP_IPS"])["all_recorded_packet_headers_in_scope"])
        frame[48:52] = ipaddress.ip_address("8.221.212.39").packed
        packet["frame_base64"] = base64.b64encode(frame).decode()
        self.assertFalse(HELPER["analyze_events"](wrap(packet), approved_ips=HELPER["PLAYER_LOOKUP_IPS"])["all_recorded_packet_headers_in_scope"])
        with self.assertRaises(ValueError):
            HELPER["analyze_events"](wrap(packet), approved_ips={"203.0.113.55"})

    def test_unknown_link_or_packet_schema_fails_closed(self):
        broken = event()
        broken["keywords"] = str(0xC0000000)
        self.assertEqual(HELPER["analyze_events"](wrap(broken))["parsed_packet_count"], 0)
        other = wrap(event())
        other["unsupported_packet_events"] = 1
        self.assertFalse(HELPER["analyze_events"](other, True)["protocol_hints_performed"])

    def test_length_mismatch_or_fragment_is_rejected(self):
        broken = event()
        broken["declared_bytes"] -= 1
        self.assertEqual(HELPER["analyze_events"](wrap(broken))["parsed_packet_count"], 0)
        broken = event()
        frame = bytearray(base64.b64decode(broken["frame_base64"]))
        frame[38:40] = b"\x20\x00"
        broken["frame_base64"] = base64.b64encode(frame).decode()
        self.assertEqual(HELPER["analyze_events"](wrap(broken))["parsed_packet_count"], 0)

    def test_signed_keywords_and_zero_length_offload_are_supported(self):
        packet = event(zero_length=True)
        packet["keywords"] = str((1 << 63 | int(packet["keywords"])) - (1 << 64))
        report = HELPER["analyze_events"](wrap(packet))
        self.assertTrue(report["all_recorded_packet_headers_in_scope"])
        self.assertEqual(report["flows"][0]["zero_ip_length_offload_packets"], 1)

    def test_tcp_handshake_requires_matching_acknowledgments(self):
        packets = [event(flags=2, seq=100, ack=0, payload=b""),
                   event(outbound=False, flags=18, seq=200, ack=101, payload=b""),
                   event(flags=16, seq=101, ack=201, payload=b"")]
        self.assertTrue(HELPER["analyze_events"](wrap(*packets))["flows"][0]["tcp_three_way_handshake_observed"])
        packets[2] = event(flags=16, seq=101, ack=999, payload=b"")
        self.assertFalse(HELPER["analyze_events"](wrap(*packets))["flows"][0]["tcp_three_way_handshake_observed"])

    @unittest.skipUnless(importlib.util.find_spec("dpkt"), "optional research dependency dpkt not installed")
    def test_verified_scope_can_enable_nondisclosing_existing_hints(self):
        report = HELPER["analyze_events"](wrap(event()), True)
        self.assertTrue(report["protocol_hints_performed"])
        self.assertNotIn("secret-fixture", json.dumps(report))
        self.assertFalse(report["career_access_verified"])
