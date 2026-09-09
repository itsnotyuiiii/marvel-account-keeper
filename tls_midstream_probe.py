"""Bounded, offline TLS 1.3 record verification for one selected captured flow.

Use only with authorized local captures and session keys. Nothing is captured,
sent, modified, exported or printed except fixed labels/counts. Decrypted bytes
stay in memory. Record authentication does not verify a TLS handshake, server
identity, process ownership, HTTP schema, account identity or Career access.
Supports generation-zero AES-GCM/ChaCha20 application keys, not TLS 1.2,
CCM, early data, missing TCP ranges, key updates or HTTP/2 decompression.
"""

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

import tls_capture_probe as capture


EvidenceError = capture.EvidenceError
MAX_SESSIONS = 1024
MAX_START_SEQUENCE = 4095
MAX_ATTEMPTS = 2_000_000
MAX_SECONDS = 20
MAX_RECORD_BYTES = 2 ** 14 + 256
MAX_PREFIX_BYTES = MAX_RECORD_BYTES + 5
MAX_ANCHORS = 8
MAX_RECORDS = 512
MAX_APPLICATION_BYTES = 2 * 1024 * 1024
PREFIX = b"\x17\x03\x03"
APP_LABELS = ("CLIENT_TRAFFIC_SECRET_0", "SERVER_TRAFFIC_SECRET_0")
CIPHERS = ("TLS_AES_128_GCM_SHA256", "TLS_AES_256_GCM_SHA384", "TLS_CHACHA20_POLY1305_SHA256")
CONTENT_TYPES = {21: "alert", 22: "handshake", 23: "application"}
CAREER_MARKERS = ("query_my_player_career", "query_my_match_summary", "uid_query_my_match")


@dataclass(repr=False)
class ApplicationPair:
    client: bytes
    server: bytes


@dataclass(repr=False)
class DecodedDirection:
    offset: int
    sequence: int
    counts: Counter
    application: bytes
    truncated_tail: bool
    chain_error: str | None
    search_ceiling: int | None = None

    def summary(self):
        return {"prefix_bytes_unexamined": self.offset, "starting_sequence": self.sequence,
                "starting_sequence_search_ceiling": self.search_ceiling,
                "authenticated_records": sum(self.counts.values()),
                "record_types": dict(self.counts), "application_bytes_authenticated": len(self.application),
                "truncated_tail": self.truncated_tail, "chain_error": self.chain_error,
                "known_career_operation_markers": [name for name in CAREER_MARKERS
                                                  if name.encode("ascii") in self.application]}


class Budget:
    def __init__(self):
        self.started = time.monotonic()
        self.attempts = 0

    def tick(self):
        if self.attempts >= MAX_ATTEMPTS:
            raise EvidenceError("decryption_attempt_limit_exceeded")
        if self.attempts % 256 == 0 and time.monotonic() - self.started > MAX_SECONDS:
            raise EvidenceError("decryption_time_limit_exceeded")
        self.attempts += 1


def application_pairs(source):
    """Reuse strict validation; retain application secrets only in private objects."""
    raw = source.read(capture.MAX_KEYLOG_BYTES + 1)
    if len(raw) > capture.MAX_KEYLOG_BYTES:
        raise EvidenceError("keylog_limit_exceeded")
    validated, counts = capture.read_keylog(io.BytesIO(raw))
    groups = {}
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[0] not in tuple(s.encode("ascii") for s in APP_LABELS):
            continue
        try:
            label = fields[0].decode("ascii")
            random = bytes.fromhex(fields[1].decode("ascii"))
            secret = bytes.fromhex(fields[2].decode("ascii"))
        except (ValueError, UnicodeError):
            continue
        value = validated.get((random, label))
        if value is None or value != (len(secret), hashlib.sha256(secret).digest()):
            continue
        groups.setdefault(random, {})[label] = secret
        if len(groups) > MAX_SESSIONS:
            raise EvidenceError("keylog_session_limit_exceeded")
    pairs = [ApplicationPair(group[APP_LABELS[0]], group[APP_LABELS[1]])
             for group in groups.values() if all(label in group for label in APP_LABELS)
             and len(group[APP_LABELS[0]]) == len(group[APP_LABELS[1]])]
    return pairs, counts


def expand_label(secret, label, length):
    if len(secret) not in (32, 48) or label not in (b"key", b"iv") or length not in (12, 16, 32):
        raise EvidenceError("unsupported_key_derivation")
    full_label = b"tls13 " + label
    info = length.to_bytes(2, "big") + bytes([len(full_label)]) + full_label + b"\0"
    algorithm = hashes.SHA384() if len(secret) == 48 else hashes.SHA256()
    return HKDFExpand(algorithm=algorithm, length=length, info=info).derive(secret)


def record_keys(secret, cipher):
    if cipher not in CIPHERS or len(secret) != (48 if cipher == CIPHERS[1] else 32):
        raise EvidenceError("unsupported_cipher_or_secret_length")
    key = expand_label(secret, b"key", 16 if cipher == CIPHERS[0] else 32)
    aead = ChaCha20Poly1305(key) if cipher == CIPHERS[2] else AESGCM(key)
    return aead, expand_label(secret, b"iv", 12)


def nonce(iv, sequence):
    if len(iv) != 12 or not 0 <= sequence < 2 ** 64:
        raise EvidenceError("invalid_record_nonce_input")
    return (int.from_bytes(iv, "big") ^ sequence).to_bytes(12, "big")


def record_at(stream, offset):
    if len(stream) - offset < 5:
        return None
    if stream[offset:offset + 3] != PREFIX:
        raise EvidenceError("non_tls13_record_after_anchor")
    size = int.from_bytes(stream[offset + 3:offset + 5], "big")
    if not 17 <= size <= MAX_RECORD_BYTES:
        raise EvidenceError("unsupported_tls13_record_length")
    end = offset + 5 + size
    if end > len(stream):
        return None
    return stream[offset:offset + 5], stream[offset + 5:end], end


def candidate_anchors(stream):
    if len(stream) > capture.MAX_FLOW_BYTES:
        raise EvidenceError("flow_limit_exceeded")
    anchors, offset = [], 0
    while len(anchors) < MAX_ANCHORS:
        offset = stream.find(PREFIX, offset, min(len(stream), MAX_PREFIX_BYTES + 3))
        if offset < 0 or offset > MAX_PREFIX_BYTES:
            break
        try:
            if record_at(stream, offset) is not None:
                anchors.append(offset)
        except EvidenceError:
            pass
        offset += 1
    return anchors


def inner_plaintext(plain):
    if len(plain) > 2 ** 14 + 1:
        raise EvidenceError("authenticated_inner_plaintext_too_large")
    unpadded = plain.rstrip(b"\0")
    if not unpadded or unpadded[-1] not in CONTENT_TYPES:
        raise EvidenceError("authenticated_inner_content_type_invalid")
    kind, content = unpadded[-1], unpadded[:-1]
    if kind != 23 and not content:
        raise EvidenceError("authenticated_empty_control_record")
    return kind, content


def decode_chain(stream, offset, sequence, keys, first_plain, budget):
    aead, iv = keys
    start, first_sequence = offset, sequence
    counts, parts, total = Counter(), [], 0
    tail, error = False, None
    plain = first_plain
    while offset < len(stream):
        try:
            if sum(counts.values()) >= MAX_RECORDS:
                raise EvidenceError("record_count_limit_exceeded")
            record = record_at(stream, offset)
            if record is None:
                tail = True
                break
            header, encrypted, end = record
            if plain is None:
                budget.tick()
                plain = aead.decrypt(nonce(iv, sequence), encrypted, header)
            kind, content = inner_plaintext(plain)
            counts[CONTENT_TYPES[kind]] += 1
            if kind == 23:
                total += len(content)
                if total > MAX_APPLICATION_BYTES:
                    raise EvidenceError("application_byte_limit_exceeded")
                parts.append(content)
            offset, sequence, plain = end, sequence + 1, None
        except InvalidTag:
            error = "subsequent_record_authentication_failed"
            break
        except EvidenceError as exc:
            # Limits abort the entire operation; malformed authenticated tails
            # are reported without discarding evidence or skipping ahead.
            if "limit_exceeded" in str(exc):
                raise
            error = str(exc)
            break
    return DecodedDirection(start, first_sequence, counts, b"".join(parts), tail, error)


def match_direction(stream, prepared, max_sequence, budget):
    """Try every key at each counter; authenticate the first matching prefix.

    Counters are searched in ascending order. At the first successful counter,
    check ALL candidate keys before returning, preserving alias ambiguity. Do
    not exhaust 4096 counters for each unrelated, older key before trying the
    next one. The returned summaries explicitly bound the counters examined;
    identities that would match only at a higher counter are not ruled out.
    """
    for offset in candidate_anchors(stream):
        header, encrypted, _ = record_at(stream, offset)
        matches = {}
        for sequence in range(max_sequence + 1):
            for identity, keys in prepared.items():
                aead, iv = keys
                budget.tick()
                try:
                    plain = aead.decrypt(nonce(iv, sequence), encrypted, header)
                except InvalidTag:
                    continue
                matches[identity] = decode_chain(stream, offset, sequence, keys, plain, budget)
                matches[identity].search_ceiling = sequence
            if matches:
                # Never skip an authenticated but damaged chain to manufacture
                # a successful later match. Equal-key aliases remain visible.
                return matches
    return {}


def analyze(client_stream, server_stream, keylog_source, max_start_sequence=MAX_START_SEQUENCE):
    report = {"outcome": "not_checked", "record_decryption_verified": False,
              "bidirectional_session_match": False, "keylog_read": False,
              "process_ownership_verified": False, "tls_peer_identity_verified": False,
              "full_connection_decrypted": False, "application_protocol_decoded": False,
              "career_access_verified": False, "network_requests_sent": False,
              "secrets_output": False, "plaintext_output": False}
    budget = Budget()
    try:
        if not 0 <= max_start_sequence <= MAX_START_SEQUENCE:
            raise EvidenceError("sequence_search_limit_invalid")
        if not candidate_anchors(client_stream) and not candidate_anchors(server_stream):
            raise EvidenceError("no_complete_tls13_candidate_records")
        report["keylog_read"] = True
        pairs, report["keylog_counts"] = application_pairs(keylog_source)
        report["complete_application_key_pairs"] = len(pairs)
        if not pairs:
            raise EvidenceError("no_complete_tls13_application_key_pairs")
        prepared = {"client": {}, "server": {}}
        for index, pair in enumerate(pairs):
            ciphers = (CIPHERS[1],) if len(pair.client) == 48 else (CIPHERS[0], CIPHERS[2])
            for cipher in ciphers:
                for direction in prepared:
                    prepared[direction][(index, cipher)] = record_keys(getattr(pair, direction), cipher)
        client = match_direction(client_stream, prepared["client"], max_start_sequence, budget)
        server = match_direction(server_stream, prepared["server"], max_start_sequence, budget)
        report["direction_match_counts"] = {"client": len(client), "server": len(server)}
        shared = set(client) & set(server)
        if len(shared) > 1:
            raise EvidenceError("ambiguous_keylog_session_match")
        if not shared:
            report["outcome"] = "unpaired_direction_match" if client or server else "no_authenticated_match_within_bounds"
        else:
            identity = next(iter(shared))
            report["bidirectional_session_match"] = True
            report["record_cipher"] = identity[1]
            report["keylog_pair_index_for_this_read"] = identity[0]
            report["client"] = client[identity].summary()
            report["server"] = server[identity].summary()
            report["record_decryption_verified"] = not (client[identity].chain_error or server[identity].chain_error)
            report["outcome"] = "authenticated_tls13_record_prefixes" if report["record_decryption_verified"] else "authenticated_anchor_with_unverified_tail"
    except EvidenceError as exc:
        report["outcome"] = str(exc)
    report["decryption_attempts"] = budget.attempts
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--keylog", type=Path)
    parser.add_argument("--client-ip")
    parser.add_argument("--client-port", type=int)
    parser.add_argument("--server-ip")
    parser.add_argument("--server-port", type=int)
    parser.add_argument("--max-start-sequence", type=int, default=MAX_START_SEQUENCE)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args(argv)
    if not args.inspect:
        print(json.dumps({"outcome": "metadata_only", "capture_exists": args.capture.is_file(),
                          "keylog_exists": args.keylog.is_file() if args.keylog else False,
                          "secrets_read": False, "network_requests_sent": False}))
        return 0
    if not args.keylog or not args.client_ip or not args.server_ip or any(p is None or not 1 <= p <= 65535 for p in (args.client_port, args.server_port)):
        parser.error("--inspect requires a key-log path and explicit client/server IPs and ports")
    if not 0 <= args.max_start_sequence <= MAX_START_SEQUENCE:
        parser.error("sequence search exceeds the fixed bound")
    try:
        streams, count = capture.load_selected_flow(args.capture, args.client_ip, args.client_port, args.server_ip, args.server_port)
        with args.keylog.open("rb") as source:
            result = analyze(streams["client"], streams["server"], source, args.max_start_sequence)
        result["selected_flow_packets"] = count
        print(json.dumps(result, indent=2))
        return 0 if result["record_decryption_verified"] else 1
    except EvidenceError as exc:
        outcome = str(exc)
    except (OSError, ValueError, ImportError):
        outcome = "local_input_dependency_or_capture_unavailable"
    print(json.dumps({"outcome": outcome, "network_requests_sent": False, "record_decryption_verified": False}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
