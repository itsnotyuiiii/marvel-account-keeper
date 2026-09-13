"""Bounded, data-only named properties from reviewed build 3848703.

Source: OWNER_RANK_RESEARCH.md, native init decoder 0x559d6b0/0x559d840.
The outer MessagePack extension has type zero. Its payload is a concatenated
class-name string and field-key/value pairs to EOF, NOT an array or map.
Current wire parser 0x55b4dc0/0x55b4e0c and table 0x55b57e0 use standard
MessagePack tags. C7/C8/C9 lengths exclude the extension type byte; native
0x55bb320 likewise stores a payload length excluding that byte.

Numeric field indices need reviewed descriptors. 0x55ab448-0x55ab45b skips
index registration when index=0; no automatic numbering may be inferred.
This decoder consequently rejects every numeric property key it encounters.
It neither proves owner identity nor projects ranks or native sentinels.

No file/network access, dynamic imports, remote class instantiation, or remote
code execution. Only selected fields are retained. Nested extensions remain
opaque until a caller explicitly supplies their reviewed class/field names.
Returned objects are transient private data; their repr and all errors omit
remote class names and values. Do not log or export their .fields/.data.
"""
from dataclasses import dataclass, field
import struct

import msgpack


MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_EXTENSION_BYTES = 4 * 1024 * 1024
MAX_STRING_BYTES = 64 * 1024
MAX_BINARY_BYTES = 1024 * 1024
MAX_CLASS_BYTES = 128
MAX_KEY_BYTES = 128
MAX_FIELDS = 2048
MAX_WANTED_FIELDS = 32
MAX_CONTAINER_ITEMS = 4096
MAX_DEPTH = 32
MAX_NODES = 100000

ERROR_CODES = frozenset({
    "property_input_invalid", "property_request_invalid", "property_input_bound",
    "property_truncated", "property_trailing_data", "property_outer_extension",
    "property_extension_type", "property_class_invalid", "property_class_mismatch",
    "property_key_invalid", "property_numeric_key_unreviewed", "property_duplicate_field",
    "property_incomplete_pair", "property_wire_invalid", "property_string_invalid",
    "property_size_bound", "property_field_bound", "property_depth_bound",
    "property_node_bound", "property_duplicate_map_key", "property_map_key_unsupported",
})


class PropertyCodecError(ValueError):
    """Only fixed diagnostic codes, never remote data or exception messages."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in ERROR_CODES else "property_input_invalid"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class OpaqueExtension:
    code: int = field(repr=False)
    data: bytes = field(repr=False)

    def __repr__(self):
        return "<OpaqueExtension>"


@dataclass(frozen=True, repr=False)
class NamedFields:
    fields: dict = field(repr=False)

    def __repr__(self):
        return "<NamedFields>"


@dataclass
class _Budget:
    nodes: int = 0


class _Reader:
    """Structural scanner: keep=False never builds arrays/maps/binary values."""
    def __init__(self, data, budget=None):
        self.data = memoryview(data)
        self.position = 0
        self.budget = _Budget() if budget is None else budget

    def take(self, length):
        if length > len(self.data) - self.position:
            raise PropertyCodecError("property_truncated")
        start = self.position
        self.position += length
        return self.data[start:self.position]

    def byte(self):
        return self.take(1)[0]

    def length(self, size):
        return int.from_bytes(self.take(size), "big")

    def extension(self, tag):
        if 0xD4 <= tag <= 0xD8:
            length = 1 << (tag - 0xD4)
        elif tag in (0xC7, 0xC8, 0xC9):
            length = self.length({0xC7: 1, 0xC8: 2, 0xC9: 4}[tag])
        else:
            raise PropertyCodecError("property_outer_extension")
        if length > MAX_EXTENSION_BYTES:
            raise PropertyCodecError("property_size_bound")
        code = self.byte()
        if code >= 128:
            code -= 256
        return code, self.take(length)

    def string(self, length, keep):
        if length > MAX_STRING_BYTES:
            raise PropertyCodecError("property_size_bound")
        raw = self.take(length)
        try:
            value = str(raw, "utf-8", errors="strict")
        except UnicodeError:
            raise PropertyCodecError("property_string_invalid") from None
        return value if keep else None

    def array(self, length, depth, keep):
        self.container_bound(length, length)
        if keep:
            return [self.value(depth=depth + 1, keep=True) for _ in range(length)]
        for _ in range(length):
            self.value(depth=depth + 1, keep=False)
        return None

    def mapping(self, length, depth, keep):
        self.container_bound(length, 2 * length)
        if not keep:
            for _ in range(2 * length):
                self.value(depth=depth + 1, keep=False)
            return None
        result = {}
        for _ in range(length):
            key = self.value(depth=depth + 1, keep=True)
            # Selected maps have ordinary hashable scalar keys. Never convert
            # an array/ext key into a guessed Python/native identity.
            if type(key) not in (str, bytes, int, float, bool, type(None)):
                raise PropertyCodecError("property_map_key_unsupported")
            if key in result:
                raise PropertyCodecError("property_duplicate_map_key")
            result[key] = self.value(depth=depth + 1, keep=True)
        return result

    def container_bound(self, length, minimum_nodes):
        if length > MAX_CONTAINER_ITEMS:
            raise PropertyCodecError("property_size_bound")
        if self.budget.nodes + minimum_nodes > MAX_NODES:
            raise PropertyCodecError("property_node_bound")
        if minimum_nodes > len(self.data) - self.position:
            raise PropertyCodecError("property_truncated")

    def value(self, *, depth=0, keep=True):
        if depth > MAX_DEPTH:
            raise PropertyCodecError("property_depth_bound")
        self.budget.nodes += 1
        if self.budget.nodes > MAX_NODES:
            raise PropertyCodecError("property_node_bound")
        tag = self.byte()
        if tag <= 0x7F:
            return tag if keep else None
        if tag >= 0xE0:
            return tag - 256 if keep else None
        if 0xA0 <= tag <= 0xBF:
            return self.string(tag & 0x1F, keep)
        if 0x90 <= tag <= 0x9F:
            return self.array(tag & 0x0F, depth, keep)
        if 0x80 <= tag <= 0x8F:
            return self.mapping(tag & 0x0F, depth, keep)
        if tag == 0xC0:
            return None
        if tag in (0xC2, 0xC3):
            return (tag == 0xC3) if keep else None
        if tag in (0xC4, 0xC5, 0xC6):
            length = self.length({0xC4: 1, 0xC5: 2, 0xC6: 4}[tag])
            if length > MAX_BINARY_BYTES:
                raise PropertyCodecError("property_size_bound")
            raw = self.take(length)
            return bytes(raw) if keep else None
        if tag in (0xC7, 0xC8, 0xC9) or 0xD4 <= tag <= 0xD8:
            code, raw = self.extension(tag)
            return OpaqueExtension(code, bytes(raw)) if keep else None
        if tag in (0xCA, 0xCB):
            raw = self.take(4 if tag == 0xCA else 8)
            return struct.unpack(">f" if tag == 0xCA else ">d", raw)[0] if keep else None
        if 0xCC <= tag <= 0xD3:
            size = (1, 2, 4, 8)[(tag - 0xCC) % 4]
            raw = self.take(size)
            return int.from_bytes(raw, "big", signed=tag >= 0xD0) if keep else None
        if tag in (0xD9, 0xDA, 0xDB):
            return self.string(self.length({0xD9: 1, 0xDA: 2, 0xDB: 4}[tag]), keep)
        if tag in (0xDC, 0xDD):
            return self.array(self.length(2 if tag == 0xDC else 4), depth, keep)
        if tag in (0xDE, 0xDF):
            return self.mapping(self.length(2 if tag == 0xDE else 4), depth, keep)
        # C1 is reserved, not a resynchronization point or a native sentinel.
        raise PropertyCodecError("property_wire_invalid")


def _name(value, bound):
    if type(value) is not str or not value:
        return False
    try:
        return len(value.encode("utf-8")) <= bound
    except UnicodeError:
        return False


def _payload(data):
    if type(data) is bytes:
        if not 0 < len(data) <= MAX_INPUT_BYTES:
            raise PropertyCodecError("property_input_bound")
        reader = _Reader(data)
        code, payload = reader.extension(reader.byte())
        if reader.position != len(reader.data):
            raise PropertyCodecError("property_trailing_data")
    elif type(data) in (msgpack.ExtType, OpaqueExtension):
        code, payload = data.code, data.data
        if type(code) is not int or not -128 <= code <= 127 or type(payload) is not bytes:
            raise PropertyCodecError("property_input_invalid")
        if len(payload) > MAX_EXTENSION_BYTES or len(payload) > MAX_INPUT_BYTES:
            raise PropertyCodecError("property_input_bound")
    else:
        raise PropertyCodecError("property_input_invalid")
    if code != 0:
        raise PropertyCodecError("property_extension_type")
    return payload


def decode_named_fields(data, *, expected_class, wanted_names):
    """Read one ext0, retaining only literal caller-selected named fields.

    data is exact bytes containing a single complete MessagePack extension, an
    exact msgpack.ExtType, or an exact OpaqueExtension returned by this decoder.
    wanted_names is a bounded tuple of unique strings; an empty tuple validates
    and skips all fields. All property keys (including skipped ones) must be
    named, unique, bounded strings. Missing requested fields remain absent.

    Skipped containers are structurally scanned with shared depth/node/length
    bounds, without constructing their arrays, maps or binary values. Duplicate
    map keys are rejected when a map is selected for materialization; skipped
    maps are structural only. Nested extension payloads are not recursively
    interpreted, even if their code is zero. No native sentinel conversion,
    class inference, numeric-index lookup or owner binding happens here.
    """
    if (not _name(expected_class, MAX_CLASS_BYTES) or type(wanted_names) is not tuple
            or len(wanted_names) > MAX_WANTED_FIELDS
            or any(not _name(name, MAX_KEY_BYTES) for name in wanted_names)
            or len(set(wanted_names)) != len(wanted_names)):
        raise PropertyCodecError("property_request_invalid")
    reader = _Reader(_payload(data))
    class_name = reader.value()
    if not _name(class_name, MAX_CLASS_BYTES):
        raise PropertyCodecError("property_class_invalid")
    if class_name != expected_class:
        raise PropertyCodecError("property_class_mismatch")
    wanted, seen, selected = frozenset(wanted_names), set(), {}
    while reader.position < len(reader.data):
        if len(seen) >= MAX_FIELDS:
            raise PropertyCodecError("property_field_bound")
        key = reader.value()
        if type(key) in (int, float):
            raise PropertyCodecError("property_numeric_key_unreviewed")
        if not _name(key, MAX_KEY_BYTES):
            raise PropertyCodecError("property_key_invalid")
        if key in seen:
            raise PropertyCodecError("property_duplicate_field")
        seen.add(key)
        if reader.position == len(reader.data):
            raise PropertyCodecError("property_incomplete_pair")
        value = reader.value(keep=key in wanted)
        if key in wanted:
            selected[key] = value
    return NamedFields(selected)
