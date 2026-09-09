"""Read-only PE/Python binding metadata inspection; optional dependency pefile.

Never load or execute the supplied executable. Only print identifier-shaped
module/method names and relative code offsets, not arbitrary strings or keys.
The Python structure layout is a hypothesis, checked against the mosclient
method set before reporting. This does not establish runtime call ownership.
"""
import argparse
import hashlib
import json
import re
import struct

import pefile


def inspect(path):
    pe = pefile.PE(path, fast_load=True)
    try:
        if pe.PE_TYPE != pefile.OPTIONAL_HEADER_MAGIC_PE_PLUS:
            raise ValueError("Expected a 64-bit PE image.")
        data = pe.__data__
        base = pe.OPTIONAL_HEADER.ImageBase

        def identifier(ptr):
            if not base <= ptr < base + pe.OPTIONAL_HEADER.SizeOfImage:
                return None
            try:
                pos = pe.get_offset_from_rva(ptr - base)
                end = data.find(b"\0", pos, pos + 81)
                raw = data[pos:end] if end > pos else b""
                if re.fullmatch(rb"[A-Za-z_][A-Za-z0-9_.]{0,79}", raw):
                    return raw.decode("ascii")
            except pefile.PEFormatError:
                pass
            return None

        def method(pos):
            if pos < 0 or pos + 32 > len(data):
                return None
            ptr, fn, flags = struct.unpack_from("<QQI", data, pos)
            name = identifier(ptr)
            if not name or flags not in (1, 2, 3, 4, 8, 16, 32, 64, 128, 130):
                return None
            section = pe.get_section_by_rva(fn - base)
            if section is None or not section.Characteristics & 0x20000000:
                return None
            return {"name": name, "function_rva": hex(fn - base), "flags": flags}

        tables = {}
        for anchor in re.finditer(b"load_msgpack\0", data):
            address = base + pe.get_rva_from_offset(anchor.start())
            for reference in re.finditer(re.escape(struct.pack("<Q", address)), data):
                start = reference.start()
                if not method(start):
                    continue
                for _ in range(64):
                    if not method(start - 32):
                        break
                    start -= 32
                rows = []
                for pos in range(start, min(start + 64 * 32, len(data)), 32):
                    row = method(pos)
                    if row is None:
                        break
                    rows.append(row)
                names = {row["name"] for row in rows}
                if not {"new_tcp_connect", "send", "dump_msgpack", "load_msgpack"} <= names:
                    continue
                table_address = base + pe.get_rva_from_offset(start)
                modules = set()
                for ref in re.finditer(re.escape(struct.pack("<Q", table_address)), data):
                    # 64-bit PyModuleDef: m_name is 24 bytes before m_methods.
                    if ref.start() >= 24:
                        name = identifier(struct.unpack_from("<Q", data, ref.start() - 24)[0])
                        if name:
                            modules.add(name)
                tables[start] = {"module_candidates": sorted(modules),
                                 "method_table_rva": hex(table_address - base), "methods": rows}
        return {"image_sha256": hashlib.sha256(data).hexdigest(),
                "image_size_bytes": len(data), "network_binding_tables": list(tables.values()),
                "runtime_connection_attribution_verified": False}
    finally:
        pe.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", help="Local installed executable to inspect without executing")
    args = parser.parse_args()
    print(json.dumps(inspect(args.executable), indent=2))
