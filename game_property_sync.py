"""Bounded, data-only path decoding for current 3848703 property sync.

Reviewed native 0x55aba90 reads one MessagePack array and dispatches ext2 to
0x55abe20. The extension payload is operation, then streamed header/path/value
entries to EOF. Operation is a positive integer <=127; this initial reader
supports only 1/2/3. Ordinary header is the FULL path length when bit0x10 is
clear; packed-index paths remain unsupported. Each entry consumes one value,
including operation2 (0x55ac662-0x55ac6cf). No-op ext2 containing only a valid
operation succeeds at 0x55abf1c -> 0x55acbcd -> 0x55acbec.

No mutation semantics are inferred: operation2 does not universally mean
delete for native object attributes. Numeric roots are retained as numbers,
not resolved to descriptors; the caller must classify or reject them.
The native outer helper is looser about trailing bytes and an ext handler's
failure. This reader deliberately requires whole-message validity and never
returns a partial change list or accepts a failed parse as a nil value.
value_kind describes the MessagePack tag only, not native sentinel conversion
or a verified source-field type. No values, remote objects or raw payloads are
retained in results. The caller separately owns entity/owner/lifecycle checks.
"""
from dataclasses import dataclass, field

import game_property_codec as codec


MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_EXTENSIONS = 256
MAX_ENTRIES = 1024
MAX_TOTAL_SEGMENTS = 4096
MAX_PATH_SEGMENTS = 32
MAX_SEGMENT_BYTES = 128
SUPPORTED_OPERATIONS = frozenset({1, 2, 3})
VALUE_KINDS = frozenset({"null", "bool", "int", "float", "str", "bytes", "array", "map", "extension"})
ERROR_CODES = codec.ERROR_CODES | frozenset({
    "property_sync_input_invalid", "property_sync_input_bound", "property_sync_outer_array",
    "property_sync_extension_bound", "property_sync_element_invalid", "property_sync_extension_type",
    "property_sync_operation_invalid", "property_sync_operation_unsupported", "property_sync_header_invalid",
    "property_sync_packed_index", "property_sync_path_bound", "property_sync_segment_invalid",
    "property_sync_entry_bound", "property_sync_segment_bound", "property_sync_trailing_data",
})


class PropertySyncError(ValueError):
    """Allowlisted codes only, including bounded structural codec failures."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in ERROR_CODES else "property_sync_input_invalid"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class PropertyChange:
    operation: int = field(repr=False)
    path: tuple[str | int, ...] = field(repr=False)
    value_kind: str = field(repr=False)

    def __repr__(self):
        return "<PropertyChange>"


@dataclass(frozen=True, repr=False)
class SyncPaths:
    entries: tuple[PropertyChange, ...] = field(repr=False)

    def __repr__(self):
        return "<SyncPaths>"


def _count_node(reader, depth):
    # Array/ext envelopes are parsed without building a Python object. Count
    # their nodes/depth exactly as the codec counts materialized wire values.
    if depth > codec.MAX_DEPTH:
        raise PropertySyncError("property_depth_bound")
    reader.budget.nodes += 1
    if reader.budget.nodes > codec.MAX_NODES:
        raise PropertySyncError("property_node_bound")


def _array_length(reader):
    _count_node(reader, 0)
    tag = reader.byte()
    if 0x90 <= tag <= 0x9F:
        length = tag & 0x0F
    elif tag in (0xDC, 0xDD):
        length = reader.length(2 if tag == 0xDC else 4)
    else:
        raise PropertySyncError("property_sync_outer_array")
    if length > MAX_EXTENSIONS:
        raise PropertySyncError("property_sync_extension_bound")
    reader.container_bound(length, length)
    return length


def _extension(reader):
    _count_node(reader, 1)
    tag = reader.byte()
    if tag not in (0xC7, 0xC8, 0xC9) and not 0xD4 <= tag <= 0xD8:
        raise PropertySyncError("property_sync_element_invalid")
    code, payload = reader.extension(tag)
    if code != 2:
        raise PropertySyncError("property_sync_extension_type")
    return codec._Reader(payload, reader.budget)


def _segment(value):
    if type(value) is int:
        return -(1 << 63) <= value < 1 << 63
    if type(value) is str and value:
        # The shared reader already checked UTF-8. Enforce the path-specific
        # byte limit rather than counting Unicode codepoints.
        return len(value.encode("utf-8")) <= MAX_SEGMENT_BYTES
    return False


def _value_kind(reader):
    if reader.position == len(reader.data):
        raise PropertySyncError("property_truncated")
    tag = reader.data[reader.position]
    if tag <= 0x7F or tag >= 0xE0 or 0xCC <= tag <= 0xD3:
        return "int"
    if 0xA0 <= tag <= 0xBF or tag in (0xD9, 0xDA, 0xDB):
        return "str"
    if 0x90 <= tag <= 0x9F or tag in (0xDC, 0xDD):
        return "array"
    if 0x80 <= tag <= 0x8F or tag in (0xDE, 0xDF):
        return "map"
    if tag in (0xC7, 0xC8, 0xC9) or 0xD4 <= tag <= 0xD8:
        return "extension"
    if tag in (0xC4, 0xC5, 0xC6):
        return "bytes"
    if tag in (0xCA, 0xCB):
        return "float"
    if tag in (0xC2, 0xC3):
        return "bool"
    if tag == 0xC0:
        return "null"
    raise PropertySyncError("property_wire_invalid")


def _decode(data):
    outer = codec._Reader(data)
    extension_count = _array_length(outer)
    entries, total_segments = [], 0
    for _ in range(extension_count):
        reader = _extension(outer)
        operation = reader.value(depth=2)
        if type(operation) is not int or not 1 <= operation <= 127:
            raise PropertySyncError("property_sync_operation_invalid")
        if operation not in SUPPORTED_OPERATIONS:
            raise PropertySyncError("property_sync_operation_unsupported")
        # Empty outer arrays and operation-only extensions are actual no-ops.
        while reader.position < len(reader.data):
            if len(entries) >= MAX_ENTRIES:
                raise PropertySyncError("property_sync_entry_bound")
            header = reader.value(depth=2)
            if type(header) is not int or not 1 <= header <= (1 << 31) - 1:
                raise PropertySyncError("property_sync_header_invalid")
            if header & 0x10:
                raise PropertySyncError("property_sync_packed_index")
            if header > MAX_PATH_SEGMENTS:
                raise PropertySyncError("property_sync_path_bound")
            if total_segments + header > MAX_TOTAL_SEGMENTS:
                raise PropertySyncError("property_sync_segment_bound")
            path = []
            for _ in range(header):
                segment = reader.value(depth=2)
                if not _segment(segment):
                    raise PropertySyncError("property_sync_segment_invalid")
                path.append(segment)
            value_kind = _value_kind(reader)
            reader.value(depth=2, keep=False)
            entries.append(PropertyChange(operation, tuple(path), value_kind))
            total_segments += header
    if outer.position != len(outer.data):
        raise PropertySyncError("property_sync_trailing_data")
    return SyncPaths(tuple(entries))


def decode_sync_paths(data):
    """Decode exact bytes, preserving update order/duplicates and no values.

    Only one complete outer array of ext2 objects is accepted. The native
    receiver skips some other extension types; this narrower reviewed reader
    rejects them instead. All malformed/unsupported entries fail the entire
    result; no partial path list escapes. Outer envelopes, all ext payloads,
    path metadata and skipped values share the codec's node/depth budget.
    """
    if type(data) is not bytes:
        raise PropertySyncError("property_sync_input_invalid")
    if not 0 < len(data) <= MAX_INPUT_BYTES:
        raise PropertySyncError("property_sync_input_bound")
    try:
        return _decode(data)
    except codec.PropertyCodecError as error:
        raise PropertySyncError(getattr(error, "code", None)) from None
