"""Offline validation of an own-account SocialMatrix Magic-RC4 capture.

No sockets, process memory, credential export, recovered-code execution, or
plaintext file output. RC4 exists here solely to inspect the recorded legacy
transport, not as an encryption choice for the application.
"""
import argparse
import base64
from collections import Counter, defaultdict
import hmac
import json
import mmap
from pathlib import Path
import runpy
import struct
import zlib


ROOT = Path(__file__).resolve().parent
MAX_STREAM_BYTES = 2 * 1024 * 1024
CAREER_METHODS = frozenset({"query_my_player_career", "query_my_match_summary", "uid_query_my_match",
                            "query_my_hero_career", "replay_query_my_match", "sort_my_hero"})


def fold_xor(target, value):
    result = bytearray(target)
    if not result:
        raise ValueError("Empty transform target")
    for index, byte in enumerate(value):
        result[index % len(result)] ^= byte
    return bytes(result)


def magic_signature(nonce, parameter):
    value = 5381
    # Build-specific NetSecurity.py: h*33 + h + signed char, NOT standard DJB2.
    for byte in fold_xor(nonce, parameter[:4]):
        value = value * 34 + (byte if byte <= 127 else byte - 256)
    return struct.pack("<I", value & 0xFFFFFFFF)


class RC4Stream:
    """Bounded legacy transport state, with no key or plaintext representation."""
    def __init__(self, key):
        if not key or len(key) > 256:
            raise ValueError("Invalid bounded RC4 key")
        self._state = list(range(256))
        j = 0
        for i in range(256):
            j = (j + self._state[i] + key[i % len(key)]) % 256
            self._state[i], self._state[j] = self._state[j], self._state[i]
        self._i = self._j = self._processed = 0

    def process(self, data):
        if self._processed + len(data) > MAX_STREAM_BYTES:
            raise ValueError("RC4 stream exceeds bound")
        state, i, j = self._state, self._i, self._j
        result = bytearray()
        for byte in data:
            i = (i + 1) % 256
            j = (j + state[i]) % 256
            state[i], state[j] = state[j], state[i]
            result.append(byte ^ state[(state[i] + state[j]) % 256])
        self._i, self._j = i, j
        self._processed += len(data)
        return bytes(result)


def rc4_crypt(key, data):
    return RC4Stream(key).process(data)


def opening_fields(stream, server):
    offset = 6 if server else 4
    if len(stream) < offset + 6:
        raise ValueError("Truncated opening")
    size = struct.unpack_from("<I", stream)[0]
    if size > len(stream) or size < offset + 6 or size > 4096:
        raise ValueError("Invalid opening length")
    if server and stream[4:6] != b"\x01\x00":
        raise ValueError("Unsupported encryption or compression mode")
    fields = []
    for _ in range(3):
        if offset + 2 > size:
            raise ValueError("Truncated field length")
        length = struct.unpack_from("<H", stream, offset)[0]
        offset += 2
        if offset + length > size:
            raise ValueError("Truncated field")
        fields.append(stream[offset:offset + length])
        offset += length
    if offset != size:
        raise ValueError("Opening has trailing bytes")
    return fields, size


def load_build_parameter(archive):
    helper = runpy.run_path(str(ROOT / "scripts/inspect-script-container.py"))
    with open(archive, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        payload, entries = helper["parse_index"](data)  # Exact installed-build hash required.
        decoder = helper["bootstrap_decoder"](data, payload, entries)
        code = helper["parse_code"](helper["reverse_module"](helper["bounded_zlib"](
            decoder.decrypt(helper["read_entry"](data, payload, entries, 5448)))))
        if (code.co_filename != "Scripts/share/const/const_social.py" or
                code.co_names[3] != "SDK_SECURE_PARAM" or bytes([204, 2, 140, 3]) not in code.co_code):
            raise ValueError("Unverified build parameter assignment")
        parameter = code.co_consts[2].encode("ascii")
        if len(parameter) != 17:
            raise ValueError("Unexpected build parameter")
        return parameter


def decode_streams(inbound, outbound, parameter):
    server, in_start = opening_fields(inbound, True)
    client, out_start = opening_fields(outbound, False)
    if list(map(len, server)) != [4, 4, 4] or list(map(len, client)) != [4, 0, 0]:
        raise ValueError("Unreviewed Magic-RC4 opening fields")
    if not hmac.compare_digest(magic_signature(server[0], parameter), client[0]):
        raise ValueError("Capture signature does not match this installed build")
    return {"in": rc4_crypt(fold_xor(server[1], parameter), inbound[in_start:]),
            "out": rc4_crypt(fold_xor(server[2], parameter), outbound[out_start:])}


def split_records(data):
    if len(data) > MAX_STREAM_BYTES:
        raise ValueError("Stream exceeds bound")
    records = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 7 or len(records) >= 4096:
            raise ValueError("Incomplete or excessive application records")
        size, main_command, sub_command = struct.unpack_from("<IHB", data, offset)
        if size < 7 or size > len(data) - offset:
            raise ValueError("Invalid application record length")
        records.append((main_command, sub_command, data[offset + 7:offset + size]))
        offset += size
    return records


def bounded_json(data):
    if not isinstance(data, (str, bytes)) or len(data) > MAX_STREAM_BYTES:
        raise ValueError("Invalid or oversized JSON input")
    try:
        return json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Invalid JSON input") from None


def decode_envelope(body):
    """Captured uni_lz4: 0 + JSON, or 1 + LE uint32 size + LZ4 block."""
    if not body or len(body) > MAX_STREAM_BYTES:
        raise ValueError("Invalid envelope size")
    if body[0] == 0:
        raw = body[1:]
    elif body[0] == 1 and len(body) >= 6:
        import lz4.block
        size = struct.unpack_from("<I", body, 1)[0]
        if not 0 < size <= MAX_STREAM_BYTES:
            raise ValueError("Invalid decompressed envelope size")
        try:
            raw = lz4.block.decompress(body[5:], uncompressed_size=size)
        except lz4.block.LZ4BlockError:
            raise ValueError("Invalid compressed envelope") from None
        if len(raw) != size:
            raise ValueError("Decompressed envelope length mismatch")
    else:
        raise ValueError("Unsupported envelope compression")
    result = bounded_json(raw)
    if not isinstance(result, dict) or not isinstance(result.get("header"), dict) or not isinstance(result.get("body"), dict):
        raise ValueError("Invalid RPC envelope")
    return result


def decode_base64(value):
    if not isinstance(value, str) or len(value) > MAX_STREAM_BYTES:
        raise ValueError("Invalid encoded payload size")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError):
        raise ValueError("Invalid encoded payload") from None


def decode_payload(envelope):
    body = envelope["body"]
    if "str_payload" in body:
        return bounded_json(body["str_payload"])
    return bounded_json(decode_base64(body.get("payload")))


class ServiceReplyError(ValueError):
    """Only bounded numeric status metadata; never an upstream message/body."""
    def __init__(self, layer, code):
        self.layer = layer
        self.code = code if type(code) is int and -10**9 <= code <= 10**9 else None
        super().__init__("NetEase rejected the requested operation")


def decode_response(payload, *, service="xyq-career"):
    if service not in {"xyq-career", "xyq-namecard"}:
        raise ValueError("Unreviewed response service")
    if not isinstance(payload, dict) or payload.get("service") != service:
        raise ValueError("No successful Career service reply")
    if payload.get("status_code") != 200:
        raise ServiceReplyError("service", payload.get("status_code"))
    raw = decode_base64(payload.get("response_body"))
    if raw.startswith(b"\x1f\x8b"):
        try:
            decoder = zlib.decompressobj(31)
            raw = decoder.decompress(raw, MAX_STREAM_BYTES + 1)
            if len(raw) > MAX_STREAM_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError("Incomplete or oversized Career response")
        except zlib.error:
            raise ValueError("Invalid compressed Career response") from None
    result = bounded_json(raw)
    if not isinstance(result, dict):
        raise ValueError("No successful Career application reply")
    if type(result.get("code")) is not int or result["code"] != 200:
        raise ServiceReplyError("application", result.get("code"))
    if not isinstance(result.get("data"), dict):
        raise ValueError("No successful Career application reply")
    return result["data"]


def career_exchanges(records, own_uid):
    """Return only own-UID Career request/reply pairs; never print this return."""
    from career_probe import positive_id
    own_uid = positive_id(own_uid, "Own UID")
    client_hello = bounded_json(records["out"][0][2])
    server_hello = bounded_json(records["in"][0][2])
    if (records["out"][0][:2] != (0, 0) or records["in"][0][:2] != (0, 0) or
            client_hello.get("payload_type") != 1 or server_hello.get("content_encoding") != "uni_lz4" or
            server_hello.get("code") != 0):
        raise ValueError("Unreviewed serialization negotiation")
    requests = {}
    for index, (main, sub, body) in enumerate(records["out"][1:], 1):
        if (main, sub) != (1, 2):
            continue
        envelope = decode_envelope(body)
        payload = decode_payload(envelope)
        if not isinstance(payload, dict) or payload.get("service") != "xyq-career":
            continue
        if payload.get("uri") not in CAREER_METHODS or payload.get("method") != "POST":
            continue
        post = bounded_json(decode_base64(payload.get("post_body")))
        if not isinstance(post, dict) or positive_id(post.get("player_uid"), "Request UID") != own_uid:
            continue
        rpcid = envelope["header"].get("rpcid")
        if not isinstance(rpcid, str) or not rpcid or len(rpcid) > 128 or rpcid in requests:
            raise ValueError("Invalid or repeated request correlation ID")
        requests[rpcid] = {"out_index": index, "request": payload, "post_body": post}
    exchanges = []
    for index, (main, sub, body) in enumerate(records["in"][1:], 1):
        if (main, sub) != (1, 2):
            continue
        envelope = decode_envelope(body)
        rpcid = envelope["header"].get("rpcid")
        if not isinstance(rpcid, str) or rpcid not in requests:
            continue
        request = requests.pop(rpcid)
        data = decode_response(decode_payload(envelope))
        exchanges.append({**request, "in_index": index, "data": data})
    return exchanges


def load_capture_records(trace, archive):
    """Return scoped decoded records in memory; callers must not log bodies."""
    netsh = runpy.run_path(str(ROOT / "scripts/inspect-netsh-career.py"))
    capture = runpy.run_path(str(ROOT / "scripts/summarize-career-capture.py"))
    events = netsh["read_events"](trace)
    audit = netsh["analyze_events"](events)
    if not audit["all_recorded_packet_headers_in_scope"]:
        raise ValueError("Packet scope not verified")
    matching = [flow for flow in audit["flows"] if flow["remote_ip"] == "35.236.255.68" and
                flow["remote_port"] == 12101 and flow["tcp_three_way_handshake_observed"]]
    if len(matching) != 1:
        raise ValueError("Expected one complete reviewed SocialMatrix connection")
    chosen = matching[0]
    key = (chosen["remote_ip"], chosen["remote_port"], chosen["local_ip"], chosen["local_port"])
    chunks = defaultdict(list)
    for event in events["packet_events"]:
        packet = netsh["packet_header"](event)
        if packet["key"] == key and packet["payload"]:
            chunks[packet["direction"]].append((packet["seq"] + bool(packet["flags"] & 2), packet["payload"]))
    streams = {}
    for direction in ("in", "out"):
        runs, conflict = capture["assemble_runs"](chunks[direction])
        if len(runs) != 1 or conflict:
            raise ValueError("Incomplete or conflicting TCP stream")
        streams[direction] = runs[0]
    decoded = decode_streams(streams["in"], streams["out"], load_build_parameter(archive))
    return {direction: split_records(plaintext) for direction, plaintext in decoded.items()}


def inspect_capture(trace, archive):
    decoded = load_capture_records(trace, archive)
    markers = (b"xyq-career", b"query_my_player_career", b"query_my_match_summary", b"uid_query_my_match",
               b"player_uid", b"post_body", b"payload", b"header", b"token")
    result = {"scope_verified": True, "magic_rc4_signature_verified": True,
              "career_access_verified": False, "identity_verified": False,
              "plaintext_saved": False, "secrets_disclosed": False, "requests_sent": False,
              "streams": {}}
    for direction, records in decoded.items():
        result["streams"][direction] = {
                "decrypted_bytes": sum(7 + len(body) for _, _, body in records), "all_record_lengths_valid": True,
                "record_count": len(records),
                "command_counts": dict(Counter(f"{main}:{sub}" for main, sub, _ in records)),
                "known_marker_record_counts": {marker.decode(): sum(marker in body for _, _, body in records) for marker in markers}}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace")
    parser.add_argument("archive")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect_capture(args.trace, args.archive), indent=2))
    except (OSError, ValueError, KeyError, IndexError, struct.error):
        print(json.dumps({"outcome": "capture_validation_failed", "career_access_verified": False,
                          "plaintext_saved": False, "secrets_disclosed": False, "requests_sent": False}))
        raise SystemExit(1)
