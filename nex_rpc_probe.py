"""Offline avatar-RPC decoding from the installed build's static contract.

Not used by the app and NOT yet validated against a decoded live game RPC.
Accepts already-plaintext complete records in memory only: no socket, capture,
session acquisition, game execution, file writing, or replay facility.
Callers must not print/save returned RPC arguments (which can be private).

Evidence: script.uep entries5051/5238/5312/8739; native image SHA256
54154e879731f3f6fad32371bff78d62629c3d87ad5443dc9466871312a76714.
pack_nex_for_send 0x545e360 writes the seven-byte header; 0x545e56b
stores its masked total size. Descriptor parser 0x5485340 maps uint32 to
wire3 and blob to wire7; small field tags are (wire_type << 5) | field_id.
0x5484880 writes little-endian integers; 0x5481a95 writes a uint32 blob
length followed by its bytes. These are public format constants, not keys.
"""
from dataclasses import dataclass, field
import struct

import msgpack


MAX_PLAINTEXT_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 1024
AVATAR_MAIN_CMD = 3
AVATAR_REQUEST_SUBCMD = 1
AVATAR_REPLY_SUBCMD = 101


@dataclass(frozen=True)
class GameHello:
    suite: int
    compression_flags: int
    challenge: bytes = field(repr=False)


def decode_game_hello(data):
    """Parse only the observed suite2/no-compression server opening.

    One unauthenticated connection to the previously observed game gateway
    returned this 28-byte shape: size:uint32, suite:uint8, compression:uint8,
    then three uint16-length-prefixed fields with sizes16/0/0. The native
    handshake branch is 0x547acde. Parsing its challenge is NOT authentication,
    a completed key exchange, or a way to decrypt a different connection.
    """
    if not isinstance(data, bytes) or len(data) != 28:
        raise ValueError("Expected the complete observed game opening")
    size, suite, compression = struct.unpack_from("<IBB", data)
    if size != len(data) or suite != 2 or compression != 0:
        raise ValueError("Unvalidated game opening mode")
    offset, fields = 6, []
    for expected in (16, 0, 0):
        if offset + 2 > len(data):
            raise ValueError("Incomplete game opening field")
        size = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        if size != expected or size > len(data) - offset:
            raise ValueError("Unvalidated game opening field length")
        fields.append(data[offset:offset + size])
        offset += size
    if offset != len(data):
        raise ValueError("Unexpected trailing game opening bytes")
    return GameHello(suite, compression, fields[0])


@dataclass(frozen=True)
class NexRecord:
    main_cmd: int
    sub_cmd: int
    body: bytes


@dataclass(frozen=True)
class AvatarRpc:
    direction: str
    index: int
    args: list


def split_plaintext_records(data):
    """Strict complete-stream framing; never fill gaps or scan for a prefix.

    Extension flag8's trailing extension is excluded from the main body.
    Other flags are not validated here, so they remain unsupported.
    """
    if not isinstance(data, bytes) or not 7 <= len(data) <= MAX_PLAINTEXT_BYTES:
        raise ValueError("Expected a bounded complete plaintext stream")
    offset, records = 0, []
    while offset < len(data):
        if len(records) >= MAX_RECORDS or len(data) - offset < 7:
            raise ValueError("Incomplete or excessive NEX records")
        word, main, sub = struct.unpack_from("<IHB", data, offset)
        size, flags = word & 0x0FFFFFFF, word >> 28
        if flags not in (0, 8):
            raise ValueError("Unvalidated NEX record flags")
        header = 9 if flags == 8 else 7
        if not header <= size <= MAX_PLAINTEXT_BYTES or offset + size > len(data):
            raise ValueError("Incomplete or oversized NEX record")
        extra = struct.unpack_from("<H", data, offset + 7)[0] if flags == 8 else 0
        if extra > size - header:
            raise ValueError("Invalid NEX extension length")
        records.append(NexRecord(main, sub, data[offset + header:offset + size - extra]))
        offset += size
    return records


def _unique_map(pairs):
    result = {}
    for key, value in pairs:
        if type(key) not in (str, int) or key in result:
            raise ValueError("Unexpected or repeated MessagePack map key")
        result[key] = value
    return result


def _no_extension(code, data):
    raise ValueError("Unvalidated MessagePack extension")


def decode_avatar_rpc(record, *, direction):
    """Decode only schema5051's rpc_index:uint32 and args:blob fields.

    The RPC index itself is not an identity check or proof of a Career reply.
    Application-specific method, target, request/reply and privacy validation
    must occur before using any resulting data. Field order may vary.
    """
    if direction not in ("out", "in"):
        raise ValueError("Specify the observed stream direction")
    expected_sub = AVATAR_REQUEST_SUBCMD if direction == "out" else AVATAR_REPLY_SUBCMD
    if (not isinstance(record, NexRecord) or record.main_cmd != AVATAR_MAIN_CMD or
            record.sub_cmd != expected_sub or not isinstance(record.body, bytes) or
            not 1 <= len(record.body) <= MAX_PLAINTEXT_BYTES):
        raise ValueError("Not a bounded avatar RPC for this direction")
    index, packed, offset = None, None, 0
    body = record.body
    while offset < len(body):
        tag = body[offset]
        offset += 1
        if len(body) - offset < 4:
            raise ValueError("Incomplete avatar RPC field")
        value = struct.unpack_from("<I", body, offset)[0]
        offset += 4
        if tag == 0x61 and index is None:  # wire3, field1
            index = value
        elif tag == 0xE2 and packed is None:  # wire7, field2
            if not 1 <= value <= MAX_PLAINTEXT_BYTES or value > len(body) - offset:
                raise ValueError("Incomplete or oversized avatar RPC arguments")
            packed = body[offset:offset + value]
            offset += value
        else:
            raise ValueError("Unexpected or repeated avatar RPC field")
    if index is None or packed is None:
        raise ValueError("Missing avatar RPC fields")
    try:
        args = msgpack.unpackb(packed, raw=False, strict_map_key=False,
            object_pairs_hook=_unique_map, ext_hook=_no_extension,
            max_str_len=MAX_PLAINTEXT_BYTES, max_bin_len=MAX_PLAINTEXT_BYTES,
            max_array_len=4096, max_map_len=4096, max_ext_len=0)
    except (ValueError, TypeError, UnicodeError, msgpack.ExtraData, msgpack.StackError):
        raise ValueError("Invalid or unsupported avatar RPC MessagePack") from None
    if not isinstance(args, list) or len(args) > 32:
        raise ValueError("Expected bounded positional avatar RPC arguments")
    return AvatarRpc(direction, index, args)
