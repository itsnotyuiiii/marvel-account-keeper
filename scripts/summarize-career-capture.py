"""Local metadata-only PCAPNG inspection. Requires optional research package dpkt.

Never print packet payloads, HTTP headers, TLS secrets, or session tokens. This
does not decrypt or replay requests. Capture files must remain outside Git.
"""
import argparse
from collections import Counter, defaultdict
import ipaddress
import json
import socket
import zlib

import dpkt


def payload_hint(data):
    if not data:
        return "no_payload"
    if len(data) >= 5 and data[0] in (20, 21, 22, 23) and data[1] == 3 and data[2] <= 4:
        return "tls_record_header"
    if data.startswith((b"GET ", b"POST ", b"PUT ", b"HEAD ", b"HTTP/1.")):
        return "plaintext_http"
    if data.startswith(b"\x1f\x8b"):
        return "gzip_header"
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        return "zstd_frame_header"
    try:
        parsed = json.loads(data)
        if isinstance(parsed, (dict, list)):
            return "json_document"
    except (ValueError, UnicodeError, RecursionError):
        pass
    return "unidentified_payload"


def extract_ip(raw):
    packet = dpkt.ethernet.Ethernet(raw).data
    if isinstance(packet, (dpkt.ip.IP, dpkt.ip6.IP6)):
        return packet
    # Observed PktMon conversion: interface declares Ethernet but packet data
    # retains a 24-byte wireless header and 8-byte LLC/SNAP header. Recognize
    # the exact SNAP signature instead of searching arbitrary payload offsets.
    if len(raw) >= 32 and raw[24:30] == b"\xaa\xaa\x03\x00\x00\x00":
        if raw[30:32] == b"\x08\x00":
            return dpkt.ip.IP(raw[32:])
        if raw[30:32] == b"\x86\xdd":
            return dpkt.ip6.IP6(raw[32:])
    return None


def assemble_runs(chunks):
    """Reassemble captured ranges; not a full TCP implementation (no wrap)."""
    runs = []
    start = None
    current = bytearray()
    inconsistent_overlap = False
    for seq, data in sorted(chunks):
        if start is None or seq > start + len(current):
            if current:
                runs.append(bytes(current))
            start, current = seq, bytearray(data)
            continue
        offset = seq - start
        overlap = min(len(data), len(current) - offset)
        if current[offset:offset + overlap] != data[:overlap]:
            inconsistent_overlap = True
        current.extend(data[overlap:])
    if current:
        runs.append(bytes(current))
    return runs, inconsistent_overlap


def framing_hypotheses(data):
    """Offline length-prefix hypotheses, never assertions about the protocol.

    Only report layouts covering an entire run with two or more records. No
    packet data is sent anywhere, and no field values are disclosed.
    """
    candidates = []
    for width in (2, 3, 4):
        for endian in ("little", "big"):
            for offset in range(9):
                for header in range(offset + width, 17):
                    for includes_header in (True, False):
                        pos = 0
                        lengths = []
                        while pos + header <= len(data):
                            size = int.from_bytes(data[pos + offset:pos + offset + width], endian)
                            total = size if includes_header else size + header
                            if total < header or total == 0 or pos + total > len(data):
                                break
                            lengths.append(total)
                            pos += total
                        if pos == len(data) and len(lengths) >= 2:
                            candidates.append({"width": width, "endian": endian,
                                               "length_offset": offset, "header_bytes": header,
                                               "length_includes_header": includes_header,
                                               "record_count": len(lengths), "record_bytes": lengths})
    return candidates


def msgpack_hypotheses(data):
    """Check a bounded header range without exposing decoded keys or values.

    MessagePack accepts many arbitrary single bytes as scalars; those aren't
    evidence. Require exact consumption of the suffix and at least one map or
    array, and still label matches as hypotheses, not a protocol identification.
    """
    try:
        import msgpack
    except ImportError:
        return {"available": False, "candidates": []}
    candidates = []
    for offset in range(min(33, len(data))):
        unpacker = msgpack.Unpacker(raw=True, strict_map_key=False,
                                    max_buffer_size=2 * 1024 * 1024,
                                    max_array_len=65536, max_map_len=65536,
                                    max_str_len=2 * 1024 * 1024,
                                    max_bin_len=2 * 1024 * 1024)
        unpacker.feed(data[offset:])
        containers = objects = consumed = 0
        try:
            for value in unpacker:
                objects += 1
                containers += isinstance(value, (dict, list))
                consumed = unpacker.tell()
        except (ValueError, TypeError, msgpack.UnpackException):
            continue
        if consumed == len(data) - offset and containers:
            candidates.append({"header_bytes": offset, "objects": objects,
                               "containers": containers, "consumed_bytes": consumed})
    return {"available": True, "candidates": candidates}


def mos_packet_hypotheses(data):
    """Test the plaintext layout seen in this build's MOS receive parser.

    RVA 0x5479fe0 masks a little-endian word to a 28-bit total length and
    interprets its upper nibble as flags. The ordinary header is seven bytes;
    flag 8 adds a two-byte trailing-extension length. This is a structural
    hypothesis only: compression/encryption precede this parser, and a capture
    may begin mid-stream. Do not emit message identifiers or body/extension data.
    Require two whole records consuming the suffix, with at most 32 skipped bytes.
    """
    candidates = []
    for offset in range(min(33, len(data))):
        pos = offset
        lengths = []
        extended_records = 0
        while pos + 7 <= len(data):
            word = int.from_bytes(data[pos:pos + 4], "little")
            size, flags = word & 0x0fffffff, word >> 28
            if not 7 <= size <= 0x04000007 or pos + size > len(data):
                break
            if flags & 8:
                if size < 9:
                    break
                extension_size = int.from_bytes(data[pos + 7:pos + 9], "little")
                if extension_size > size - 9:
                    break
                extended_records += 1
            lengths.append(size)
            pos += size
        if pos == len(data) and len(lengths) >= 2:
            candidates.append({"skipped_prefix_bytes": offset,
                               "record_count": len(lengths), "record_bytes": lengths,
                               "extended_records": extended_records})
    return candidates


def socialmatrix_opening_hypotheses(data):
    """Recognize only a complete leading hello/ack layout, never its secrets.

    Build-specific archive entry 43 defines a <I total size, then (for the
    server hello only) suite/compression bytes, then three <H-length byte
    strings. This is a structural hypothesis, not protocol, cipher, peer or
    authentication verification. A captured run need not begin at TCP startup.
    Do not scan offsets, decrypt, join gaps, or expose the variable fields.
    """
    if len(data) < 4:
        return []
    size = int.from_bytes(data[:4], "little")
    candidates = []
    for kind, fixed_bytes in (("server_hello", 6), ("client_ack", 4)):
        # Three uint16 lengths bound both allocation-free work and total size.
        if not fixed_bytes + 6 <= size <= fixed_bytes + 6 + 3 * 65535 or size > len(data):
            continue
        if kind == "server_hello" and data[4] not in (0, 1, 2):
            continue  # Only suites defined by the inspected Python transport.
        offset = fixed_bytes
        lengths = []
        for _ in range(3):
            if offset + 2 > size:
                break
            length = int.from_bytes(data[offset:offset + 2], "little")
            offset += 2
            if offset + length > size:
                break
            lengths.append(length)
            offset += length
        if len(lengths) != 3 or offset != size:
            continue
        candidate = {"kind": kind, "message_bytes": size,
                     "variable_field_bytes": lengths,
                     "trailing_stream_bytes": len(data) - size,
                     "protocol_verified": False}
        if kind == "server_hello":
            candidate.update(secure_suite=data[4], compression_flag=data[5])
        candidates.append(candidate)
    return candidates


def stream_hints(chunks):
    """Deduplicate TCP/offload overlap; report structure, never decoded values."""
    runs, inconsistent_overlap = assemble_runs(chunks)
    markers = (b"career", b"match_uid", b"rank_game", b"player_uid", b"query_player")
    compressed = []
    for data in runs:
        # Test only valid zlib headers near the start of each captured run.
        # Bounded decompression; never print or persist the resulting bytes.
        for offset in range(min(64, len(data) - 1)):
            cmf, flg = data[offset:offset + 2]
            if cmf & 15 != 8 or cmf >> 4 > 7 or (cmf * 256 + flg) % 31:
                continue
            try:
                decoder = zlib.decompressobj()
                decoded = decoder.decompress(data[offset:], 2 * 1024 * 1024)
                if decoder.eof:
                    compressed.append({"header_offset": offset, "decoded_bytes": len(decoded),
                                       "hint": payload_hint(decoded),
                                       "known_field_markers": [m.decode() for m in markers if m in decoded]})
            except zlib.error:
                pass
    return {"unique_payload_bytes": sum(map(len, runs)), "contiguous_runs": len(runs),
            "overlap_conflict": inconsistent_overlap,
            "run_start_hints": [payload_hint(data) for data in runs],
            "length_prefix_hypotheses": [framing_hypotheses(data) for data in runs],
            "mos_plaintext_hypotheses": [mos_packet_hypotheses(data) for data in runs],
            "socialmatrix_opening_hypotheses": [
                socialmatrix_opening_hypotheses(data) if not inconsistent_overlap else []
                for data in runs],
            "msgpack_hypotheses": [msgpack_hypotheses(data) for data in runs],
            "zstd_frame_header_offsets": [[offset for offset in range(min(64, len(data)))
                                           if data[offset:offset + 4] == b"\x28\xb5\x2f\xfd"]
                                          for data in runs],
            "known_field_markers": [m.decode() for m in markers if any(m in data for data in runs)],
            "zlib_streams": compressed}


def summarize(path):
    flows = defaultdict(lambda: {"packets": 0, "payload_bytes": 0, "hints": Counter(),
                                 "payload_events": []})
    skips = Counter()
    streams = defaultdict(list)
    first = last = None
    with open(path, "rb") as capture:
        reader = dpkt.pcapng.Reader(capture)
        if reader.datalink() != dpkt.pcap.DLT_EN10MB:
            raise ValueError("This helper expects Ethernet PCAPNG packets.")
        for stamp, raw in reader:
            first = stamp if first is None else first
            last = stamp
            try:
                packet = extract_ip(raw)
                if not isinstance(packet, (dpkt.ip.IP, dpkt.ip6.IP6)):
                    skips["not_ip"] += 1
                    continue
                tcp = packet.data
                if not isinstance(tcp, dpkt.tcp.TCP):
                    skips["not_tcp"] += 1
                    continue
                family = socket.AF_INET if isinstance(packet, dpkt.ip.IP) else socket.AF_INET6
                src, dst = (socket.inet_ntop(family, addr) for addr in (packet.src, packet.dst))
                src_local, dst_local = ipaddress.ip_address(src).is_private, ipaddress.ip_address(dst).is_private
                if src_local == dst_local:
                    skips["ambiguous_direction"] += 1
                    continue
                if src_local:
                    key = (dst, tcp.dport, tcp.sport)
                    direction = "out"
                else:
                    key = (src, tcp.sport, tcp.dport)
                    direction = "in"
                flow = flows[key]
                flow["packets"] += 1
                flow["payload_bytes"] += len(tcp.data)
                hint = payload_hint(tcp.data)
                flow["hints"][hint] += 1
                if tcp.data:
                    streams[(key, direction)].append((tcp.seq, bytes(tcp.data)))
                    flow["payload_events"].append({"seconds": round(stamp - first, 3),
                                                   "direction": direction,
                                                   "bytes": len(tcp.data), "hint": hint})
            except (dpkt.UnpackError, ValueError, OSError):
                skips["unparsed_packet"] += 1
    return {"duration_seconds": round(last - first, 3) if first is not None else 0,
            "skipped": dict(skips), "connections": [
                {"remote_ip": key[0], "remote_port": key[1], "local_port": key[2], **value,
                 "streams": {direction: stream_hints(streams[(key, direction)]) for direction in ("in", "out")}}
                for key, value in sorted(flows.items())]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", help="Local PCAPNG path; contents are never uploaded")
    args = parser.parse_args()
    print(json.dumps(summarize(args.capture), indent=2))
