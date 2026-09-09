"""Read-only, build-specific MCPK script metadata inspection.

Never execute/import recovered code, print constants or keys, or write extracted
game files. Only inspect a locally installed archive. No network or process API.
The rotor parameters are derived in memory from the archive's own bootstrap;
they are distribution-file parameters, not account/session credentials.
Requires optional xdis. The game's opcode numbers are customized: a stock
Python disassembly is NOT trustworthy even when marshal parsing succeeds.
"""
import argparse
import contextlib
import hashlib
import io
import json
import math
import mmap
import struct
import zlib

from xdis.unmarshal import load_code

ARCHIVE_SHA256 = "4dbe9d3246a05d4a402373cc11c03a938692db9bc16aaaf939ddff4e8682d22e"
MAX_DECODED = 4 * 1024 * 1024
BOOTSTRAP_ENTRY = 4703

# Rotor algorithm adapted from CPython v2.3.7 Modules/rotormodule.c:
# https://github.com/python/cpython/blob/v2.3.7/Modules/rotormodule.c
# Copyright 1994 by Lance Ellinghouse,
# Cathedral City, California Republic, United States of America.
# All Rights Reserved
# Permission to use, copy, modify, and distribute this software and its
# documentation for any purpose and without fee is hereby granted,
# provided that the above copyright notice appear in all copies and that
# both that copyright notice and this permission notice appear in
# supporting documentation, and that the name of Lance Ellinghouse
# not be used in advertising or publicity pertaining to distribution
# of the software without specific, written prior permission.
# LANCE ELLINGHOUSE DISCLAIMS ALL WARRANTIES WITH REGARD TO
# THIS SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
# FITNESS, IN NO EVENT SHALL LANCE ELLINGHOUSE BE LIABLE FOR ANY SPECIAL,
# INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING
# FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
# NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION
# WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.


class RotorDecoder:
    """Legacy rotor compatibility for local distribution files, not security."""

    def __init__(self, key):
        keys = [995, 576, 767]
        for ch in key:
            rotated = [(k << 3) | (k >> 13) for k in keys]
            keys = [(rotated[0] + ch) & 65535, (rotated[1] ^ ch) & 65535,
                    (rotated[2] - ch) & 65535]
        keys[1] |= 1
        seed = [k if k < 32768 else k - 65536 for k in keys]

        def rand(n):
            for i, (divisor, multiplier, subtractor, modulus) in enumerate(
                    ((177, 171, 2, 30269), (176, 172, 35, 30307), (178, 170, 63, 30323))):
                # Reproduce C truncating division/remainder for negative seeds.
                quotient = math.trunc(seed[i] / divisor)
                value = multiplier * (seed[i] - quotient * divisor) - subtractor * quotient
                seed[i] = value + modulus if value < 0 else value
            value = seed[0] / 30269.0 + seed[1] / 30307.0 + seed[2] / 30323.0
            return int((value - math.floor(value)) * n) % n

        self.positions, self.advances, self.inverse = [], [], []
        for _ in range(6):
            self.positions.append(rand(256))
            self.advances.append(1 + 2 * rand(128))
            forward, inverse = list(range(256)), list(range(256))
            for size in range(256, 1, -1):
                chosen = rand(size)
                value = forward[chosen]
                forward[chosen], forward[size - 1] = forward[size - 1], value
                inverse[value] = size - 1
            inverse[forward[0]] = 0
            self.inverse.append(inverse)

    def decrypt(self, data):
        # The bootstrap uses decrypt(), which restarts the rotor for each file.
        positions = self.positions.copy()
        result = bytearray()
        for value in data:
            for i in range(5, -1, -1):
                value = positions[i] ^ self.inverse[i][value]
            result.append(value)
            for i in range(6):
                value = positions[i] + self.advances[i]
                positions[i] = value % 256
                if value >= 256 and i < 5:
                    positions[i + 1] = (positions[i + 1] + 1) % 256
        return bytes(result)


def bounded_zlib(data):
    decoder = zlib.decompressobj()
    result = decoder.decompress(data, MAX_DECODED + 1)
    if len(result) > MAX_DECODED or not decoder.eof or decoder.unused_data:
        raise ValueError("Incomplete, oversized, or trailing-data compressed entry")
    return result


def reverse_module(data):
    return (bytes(ch ^ 156 for ch in data[:130]) + data[130:])[::-1]


def parse_code(data):
    # Third-party parser diagnostics are not part of the metadata output.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return load_code(io.BytesIO(data), 3495, {})


def parse_index(data, verify_hash=True):
    if len(data) < 24:
        raise ValueError("Truncated archive")
    if verify_hash and hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("Unrecognized archive build; revalidate the format before decoding")
    magic, timestamp, start, second, payload = struct.unpack_from("<4sQIII", data)
    if magic != b"MCPK" or not 24 <= start <= second <= payload <= len(data):
        raise ValueError("Invalid archive section boundaries")
    if (second - start) % 12 or (payload - second) % 16:
        raise ValueError("Invalid index record alignment")
    entries = list(struct.iter_unpack("<4I", data[second:payload]))
    if len(entries) > 100000:
        raise ValueError("Too many archive entries")
    for _, offset, size, _ in entries:
        if offset + size > len(data) - payload:
            raise ValueError("Entry is outside the payload section")
    return payload, entries


def read_entry(data, payload, entries, index):
    _, offset, size, _ = entries[index]
    if size > MAX_DECODED:
        raise ValueError("Compressed entry exceeds research bound")
    return data[payload + offset:payload + offset + size]


def bootstrap_decoder(data, payload, entries):
    raw = read_entry(data, payload, entries, BOOTSTRAP_ENTRY)
    corrected = bytes(a ^ b for a, b in zip(raw[:4], b"MCPK")) + raw[4:]
    bootstrap = parse_code(bounded_zlib(corrected))
    if bootstrap.co_filename != "redirect.py":
        raise ValueError("Unexpected bootstrap")
    init = next(c for c in bootstrap.co_consts if getattr(c, "co_name", None) == "init_rotor")
    c = init.co_consts
    # Fixed expression verified against this build's bootstrap. Never eval or
    # execute the recovered function, and never persist/print its constants.
    key = (c[1] * c[4] + (c[2] + c[1] + c[3]) * c[5] + c[6] + c[7]
           + c[2] * c[8] + c[3] * c[9] + c[10] + c[11] + c[12]).encode()
    return RotorDecoder(key)


def walk_code(code):
    yield code
    for child in code.co_consts:
        if hasattr(child, "co_code"):
            yield from walk_code(child)


def matching_symbols(code, symbols):
    return [{"function": item.co_name, "symbols": sorted(set(item.co_names).intersection(symbols))}
            for item in walk_code(code) if set(item.co_names).intersection(symbols)]


def inspect(path, start, count, contains, details=False, symbols=()):
    with open(path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        payload, entries = parse_index(data)
        decoder = bootstrap_decoder(data, payload, entries)
        counts = {"parsed": 0, "matched": 0, "errors": 0}
        for index in range(start, min(len(entries), start + count)):
            if index == BOOTSTRAP_ENTRY:
                continue
            try:
                raw = read_entry(data, payload, entries, index)
                decoded = reverse_module(bounded_zlib(decoder.decrypt(raw)))
                code = parse_code(decoded)
                counts["parsed"] += 1
                matches = matching_symbols(code, symbols) if symbols else []
                if (not contains and not symbols or matches
                        or any(term.lower() in code.co_filename.lower() for term in contains)):
                    counts["matched"] += 1
                    result = {"entry": index, "filename": code.co_filename}
                    if matches:
                        result["symbol_matches"] = matches
                    if details:
                        result["module_names"] = list(code.co_names)
                        result["functions"] = [{"name": c.co_name, "names": list(c.co_names)}
                                               for c in walk_code(code) if c is not code]
                    print(json.dumps(result), flush=True)
            except (ValueError, TypeError, AssertionError, EOFError, zlib.error,
                    struct.error, KeyError, IndexError, OverflowError, RecursionError):
                counts["errors"] += 1
                print(json.dumps({"entry": index, "error": "not_decoded"}), flush=True)
        print(json.dumps({"summary": counts, "archive_entries": len(entries),
                          "inspected_start": start, "inspected_stop": min(len(entries), start + count)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--contains", action="append", default=[])
    parser.add_argument("--symbol", action="append", default=[], help="Find exact referenced code names")
    parser.add_argument("--details", action="store_true", help="Report code names only, never constants")
    args = parser.parse_args()
    if args.start < 0 or args.count < 1:
        parser.error("start must be nonnegative and count positive")
    inspect(args.archive, args.start, args.count, args.contains, args.details, args.symbol)
