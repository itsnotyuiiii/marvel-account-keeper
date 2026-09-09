"""Offline TLS capture/key-log correlation for one explicitly selected TCP flow.

Never capture, connect, launch, decrypt, export keys, or print packet content.
Matching material is not proof of process ownership, authenticated TLS, Career
access, or correct stats. The caller must independently establish the flow's
game ownership. Default CLI mode checks metadata only; --inspect reads inputs.
"""

import argparse
import hashlib
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import struct


MAX_CAPTURE_BYTES = 64 * 1024 * 1024
MAX_FLOW_BYTES = 8 * 1024 * 1024
MAX_PACKETS = 100000
MAX_KEYLOG_BYTES = 1024 * 1024
MAX_KEYLOG_LINE = 512
MAX_HANDSHAKE_BYTES = 256 * 1024
TLS13_LABELS = (
    "CLIENT_HANDSHAKE_TRAFFIC_SECRET", "SERVER_HANDSHAKE_TRAFFIC_SECRET",
    "CLIENT_TRAFFIC_SECRET_0", "SERVER_TRAFFIC_SECRET_0",
)
KEY_LABELS = ("CLIENT_RANDOM",) + TLS13_LABELS
HELLO_RETRY_RANDOM = hashlib.sha256(b"HelloRetryRequest").digest()


class EvidenceError(ValueError):
    """Only fixed error codes; never put input data in the message."""


class Reader:
    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, size):
        if size < 0 or self.offset + size > len(self.data):
            raise EvidenceError("truncated_hello")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def number(self, size):
        return int.from_bytes(self.take(size), "big")

    def vector(self, size):
        return self.take(self.number(size))


def parse_hello(body, client):
    reader = Reader(body)
    version, random = reader.number(2), reader.take(32)
    session_id = reader.vector(1)
    if version not in (0x0301, 0x0302, 0x0303) or len(session_id) > 32:
        raise EvidenceError("unsupported_hello")
    if client:
        suites = reader.vector(2)
        if not suites or len(suites) % 2:
            raise EvidenceError("invalid_cipher_suites")
        ciphers = {int.from_bytes(suites[i:i + 2], "big") for i in range(0, len(suites), 2)}
        if not reader.vector(1):
            raise EvidenceError("invalid_compression_methods")
    else:
        ciphers = {reader.number(2)}
        if reader.number(1) != 0:
            raise EvidenceError("unsupported_tls_compression")
    extensions = {}
    if reader.offset < len(body):
        extra = Reader(reader.vector(2))
        while extra.offset < len(extra.data):
            kind, value = extra.number(2), extra.vector(2)
            if kind in extensions:
                raise EvidenceError("duplicate_tls_extension")
            extensions[kind] = value
    if reader.offset != len(body):
        raise EvidenceError("trailing_hello_data")
    if client and 43 in extensions:
        offered = Reader(extensions[43])
        versions = offered.vector(1)
        if not versions or len(versions) % 2 or offered.offset != len(offered.data):
            raise EvidenceError("invalid_supported_versions")
        offered_versions = {int.from_bytes(versions[i:i + 2], "big") for i in range(0, len(versions), 2)}
    else:
        offered_versions = {version}
    if not client and 43 in extensions:
        if len(extensions[43]) != 2:
            raise EvidenceError("invalid_selected_version")
        version = int.from_bytes(extensions[43], "big")
    return {"version": version, "random": random, "session_id": session_id,
            "ciphers": ciphers, "offered_versions": offered_versions}


def initial_hello(stream, client):
    """TLS record + handshake reassembly; never search arbitrary payload offsets."""
    if len(stream) > MAX_FLOW_BYTES:
        raise EvidenceError("flow_limit_exceeded")
    records = Reader(stream)
    handshake = bytearray()
    expected = 1 if client else 2
    while records.offset < len(stream):
        if len(stream) - records.offset < 5:
            raise EvidenceError("truncated_tls_record")
        kind, version, size = records.number(1), records.number(2), records.number(2)
        if kind not in (20, 21, 22, 23) or version not in (0x0301, 0x0302, 0x0303) or size > 18432:
            raise EvidenceError("not_initial_tls_records")
        if size > len(stream) - records.offset:
            raise EvidenceError("truncated_tls_record")
        data = records.take(size)
        if kind != 22:
            # No scanning encrypted records or searching after a missing hello.
            raise EvidenceError("initial_hello_missing")
        handshake.extend(data)
        if len(handshake) > MAX_HANDSHAKE_BYTES:
            raise EvidenceError("handshake_limit_exceeded")
        if len(handshake) < 4:
            continue
        if handshake[0] != expected:
            raise EvidenceError("unexpected_initial_handshake")
        size = int.from_bytes(handshake[1:4], "big")
        if size > MAX_HANDSHAKE_BYTES:
            raise EvidenceError("handshake_limit_exceeded")
        if len(handshake) >= 4 + size:
            return parse_hello(bytes(handshake[4:4 + size]), client)
    raise EvidenceError("initial_hello_missing")


def read_keylog(source):
    """Retain random/label/length/digest only; never return the secret itself."""
    entries = {}
    report = {"recognized_lines": 0, "invalid_lines": 0, "ignored_lines": 0,
              "duplicate_lines": 0, "conflicting_entries": 0}
    total = 0
    while True:
        line = source.readline(MAX_KEYLOG_LINE + 1)
        if not line:
            break
        total += len(line)
        if total > MAX_KEYLOG_BYTES or len(line) > MAX_KEYLOG_LINE:
            raise EvidenceError("keylog_limit_exceeded")
        if not line.strip() or line.lstrip().startswith(b"#"):
            continue
        fields = line.split()
        if len(fields) != 3:
            report["invalid_lines"] += 1
            continue
        label, random, secret = fields
        if label not in tuple(s.encode("ascii") for s in KEY_LABELS):
            report["ignored_lines"] += 1
            continue
        if not re.fullmatch(rb"[0-9a-fA-F]{64}", random) or not re.fullmatch(rb"(?:[0-9a-fA-F]{64}|[0-9a-fA-F]{96})", secret):
            report["invalid_lines"] += 1
            continue
        label = label.decode("ascii")
        secret_bytes = bytes.fromhex(secret.decode("ascii"))
        if label == "CLIENT_RANDOM" and len(secret_bytes) != 48:
            report["invalid_lines"] += 1
            continue
        key = (bytes.fromhex(random.decode("ascii")), label)
        value = (len(secret_bytes), hashlib.sha256(secret_bytes).digest())
        report["recognized_lines"] += 1
        if key in entries:
            if entries[key] == value:
                report["duplicate_lines"] += 1
            else:
                entries[key] = None
                report["conflicting_entries"] += 1
        else:
            entries[key] = value
    return entries, report


def correlate(client_bytes, server_bytes, keylog_source=None):
    report = {"outcome": "not_checked", "client_hello_present": False,
              "server_hello_present": False, "matching_material_present": False,
              "matching_key_labels": [], "process_ownership_verified": False,
              "tls_decryption_verified": False, "career_access_verified": False,
              "network_requests_sent": False, "secrets_output": False}
    try:
        client = initial_hello(client_bytes, True)
        report["client_hello_present"] = True
        server = initial_hello(server_bytes, False)
        report["server_hello_present"] = True
        if server["random"] == HELLO_RETRY_RANDOM:
            raise EvidenceError("hello_retry_requires_full_handshake_analysis")
        if not server["ciphers"] <= client["ciphers"]:
            raise EvidenceError("unoffered_cipher_suite")
        version = server["version"]
        if version not in (0x0303, 0x0304):
            raise EvidenceError("unsupported_selected_tls_version")
        report["tls_version"] = "1.3" if version == 0x0304 else "1.2"
        cipher = next(iter(server["ciphers"]))
        report["cipher_suite"] = f"0x{cipher:04x}"
        if version == 0x0304:
            if version not in client["offered_versions"] or server["session_id"] != client["session_id"] or cipher not in range(0x1301, 0x1306):
                raise EvidenceError("inconsistent_tls13_hellos")
            needed, secret_size = TLS13_LABELS, 48 if cipher == 0x1302 else 32
        else:
            if version not in client["offered_versions"] or cipher in range(0x1301, 0x1306):
                raise EvidenceError("inconsistent_tls12_hellos")
            needed, secret_size = ("CLIENT_RANDOM",), 48
        if keylog_source is None:
            raise EvidenceError("matching_keylog_required")
        entries, report["keylog"] = read_keylog(keylog_source)
        for label in needed:
            value = entries.get((client["random"], label))
            if value is not None and value[0] == secret_size:
                report["matching_key_labels"].append(label)
        report["matching_material_present"] = len(report["matching_key_labels"]) == len(needed)
        report["outcome"] = "matching_material_not_yet_decrypted" if report["matching_material_present"] else "required_matching_secrets_missing_or_conflicting"
    except EvidenceError as exc:
        report["outcome"] = str(exc)
    return report


def load_selected_flow(path, client_ip, client_port, server_ip, server_port):
    """Only assemble the explicitly selected 4-tuple, not unrelated traffic."""
    import dpkt  # Optional research dependency; never install from this helper.
    spec = importlib.util.spec_from_file_location("capture_metadata", Path(__file__).parent / "scripts" / "summarize-career-capture.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    selected = (str(ipaddress.ip_address(client_ip)), client_port,
                str(ipaddress.ip_address(server_ip)), server_port)
    reverse = (selected[2], selected[3], selected[0], selected[1])
    chunks, syns = {"client": [], "server": []}, {"client": set(), "server": set()}
    total = matched = 0
    with path.open("rb") as source:
        if path.stat().st_size > MAX_CAPTURE_BYTES:
            raise EvidenceError("capture_limit_exceeded")
        try:
            reader = dpkt.pcapng.Reader(source)
        except (dpkt.UnpackError, ValueError, struct.error):
            raise EvidenceError("unparsed_capture_header") from None
        if reader.datalink() != dpkt.pcap.DLT_EN10MB:
            raise EvidenceError("unsupported_capture_link_type")
        iterator = iter(reader)
        for index in range(MAX_PACKETS + 1):
            try:
                _, raw = next(iterator)
            except StopIteration:
                break
            except (dpkt.UnpackError, ValueError, struct.error):
                raise EvidenceError("unparsed_capture_record") from None
            if index >= MAX_PACKETS or source.tell() > MAX_CAPTURE_BYTES:
                raise EvidenceError("capture_limit_exceeded")
            try:
                packet = helper.extract_ip(raw)
            except (dpkt.UnpackError, ValueError, struct.error):
                raise EvidenceError("unparsed_capture_packet") from None
            if not isinstance(packet, (dpkt.ip.IP, dpkt.ip6.IP6)) or not isinstance(packet.data, dpkt.tcp.TCP):
                continue
            tcp = packet.data
            flow = (str(ipaddress.ip_address(packet.src)), tcp.sport,
                    str(ipaddress.ip_address(packet.dst)), tcp.dport)
            if flow not in (selected, reverse):
                continue
            direction = "client" if flow == selected else "server"
            matched += 1
            if tcp.flags & dpkt.tcp.TH_SYN:
                syns[direction].add(tcp.seq)
            if tcp.data:
                total += len(tcp.data)
                if total > MAX_FLOW_BYTES:
                    raise EvidenceError("flow_limit_exceeded")
                chunks[direction].append(((tcp.seq + bool(tcp.flags & dpkt.tcp.TH_SYN)) % 2 ** 32, bytes(tcp.data)))
    output = {}
    for direction in chunks:
        if len(syns[direction]) > 1:
            raise EvidenceError("multiple_tcp_connections_selected")
        runs, conflict = helper.assemble_runs(chunks[direction])
        if conflict or len(runs) > 1:
            raise EvidenceError("tcp_gap_wrap_or_overlap_conflict")
        output[direction] = runs[0] if runs else b""
    return output, matched


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--keylog", type=Path)
    parser.add_argument("--client-ip")
    parser.add_argument("--client-port", type=int)
    parser.add_argument("--server-ip")
    parser.add_argument("--server-port", type=int)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args(argv)
    if not args.inspect:
        print(json.dumps({"outcome": "metadata_only", "capture_exists": args.capture.is_file(),
                          "keylog_exists": args.keylog.is_file() if args.keylog else False,
                          "secrets_read": False, "network_requests_sent": False}))
        return 0
    if not args.client_ip or not args.server_ip or any(p is None or not 1 <= p <= 65535 for p in (args.client_port, args.server_port)):
        parser.error("--inspect requires explicit client/server IPs and valid ports")
    try:
        streams, count = load_selected_flow(args.capture, args.client_ip, args.client_port, args.server_ip, args.server_port)
        if args.keylog:
            with args.keylog.open("rb") as source:
                result = correlate(streams["client"], streams["server"], source)
        else:
            result = correlate(streams["client"], streams["server"])
        result["selected_flow_packets"] = count
        print(json.dumps(result, indent=2))
        return 0 if result["matching_material_present"] else 1
    except EvidenceError as exc:
        outcome = str(exc)
    except (OSError, ValueError, ImportError):
        outcome = "local_input_dependency_or_capture_unavailable"
    print(json.dumps({"outcome": outcome, "network_requests_sent": False, "tls_decryption_verified": False}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
