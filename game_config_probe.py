"""Read-only reader for the installed game's packaged configuration.

Used by the experimental direct-NetEase app. No network, process access, extraction to disk,
game modification, or recovered-code execution. Only config entry metadata is
printed. Index hashes are integrity checks, not publisher authentication.

Format references: natimerry/repak-rivals (repak/src/{pak,entry,data,utils}.rs)
and FabianFG/CUE4Parse (GameTypes/NetEase/MAR/Encryption/Aes/NetEaseAes.cs).
The content key below is public distribution metadata, NOT an account secret.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path
import struct
from typing import BinaryIO
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


DEFAULT_PAK = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\MarvelRivals"
    r"\MarvelGame\Marvel\Content\Paks\pakchunk0-Windows.pak"
)
# Published as the default in repak-rivals/repak_cli/src/main.rs.
PUBLIC_CONTENT_KEY = bytes.fromhex(
    "0C263D8C22DCB085894899C3A3796383E9BF9DE0CBFB08C9BF2DEF2E84F29D74"
)
FOOTER_SIZE = 221
MAX_INDEX = 8 * 1024 * 1024
MAX_CONFIG = 2 * 1024 * 1024
MAX_ENTRIES = 100_000
CONFIG_PATHS = frozenset({
    "Marvel/Config/DefaultEngine.ini", "Marvel/Config/DefaultGame.ini",
    "Marvel/Config/Windows/WindowsEngine.ini", "Marvel/Config/Windows/WindowsGame.ini",
})
SHIPPING_CONFIG = "Marvel/Config/Windows/WindowsGame.ini"


class ConfigError(ValueError):
    """Unsupported or inconsistent public distribution data."""


def _words(data: bytes) -> bytes:
    if len(data) % 4:
        raise ConfigError("word alignment")
    return b"".join(data[i:i + 4][::-1] for i in range(0, len(data), 4))


def decrypt_distribution(data: bytes, key: bytes = PUBLIC_CONTENT_KEY) -> bytes:
    if len(key) != 32 or len(data) % 16:
        raise ConfigError("AES size")
    cipher = Cipher(algorithms.AES(_words(key)), modes.ECB()).decryptor()
    return _words(cipher.update(_words(data)) + cipher.finalize())


class Reader:
    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)
        self.size = len(data)

    def read(self, size: int) -> bytes:
        if not 0 <= size <= self.size - self.stream.tell():
            raise ConfigError("truncated data")
        return self.stream.read(size)

    def number(self, fmt: str) -> int:
        return struct.unpack("<" + fmt, self.read(struct.calcsize("<" + fmt)))[0]

    def string(self) -> str:
        count = self.number("i")
        if not 0 < abs(count) <= 4096:
            raise ConfigError("string size")
        wide = count < 0
        raw = self.read(abs(count) * (2 if wide else 1))
        end = b"\0\0" if wide else b"\0"
        if not raw.endswith(end):
            raise ConfigError("string terminator")
        try:
            result = raw[:-len(end)].decode("utf-16-le" if wide else "utf-8")
        except UnicodeError as exc:
            raise ConfigError("string encoding") from exc
        if "\0" in result:
            raise ConfigError("embedded terminator")
        return result

    def count(self, maximum: int = MAX_ENTRIES) -> int:
        value = self.number("I")
        if value > maximum:
            raise ConfigError("count bound")
        return value


@dataclass(frozen=True)
class Entry:
    path: str
    offset: int
    compressed: int
    uncompressed: int
    compression: int
    encrypted: bool
    block_size: int
    blocks: tuple[tuple[int, int], ...]
    header_size: int


def decode_entry(path: str, encoded: bytes, at: int) -> Entry:
    if not 0 <= at < len(encoded):
        raise ConfigError("entry offset")
    r = Reader(encoded[at:])
    bits = r.number("I")
    compression = (bits >> 23) & 63
    encrypted = bool(bits & (1 << 22))
    block_count = (bits >> 6) & 65535
    if block_count > 1024:
        raise ConfigError("block count")
    block_size = bits & 63
    block_size = r.number("I") if block_size == 63 else block_size << 11
    offset = r.number("I" if bits & (1 << 31) else "Q")
    uncompressed = r.number("I" if bits & (1 << 30) else "Q")
    compressed = r.number("I" if bits & (1 << 29) else "Q") if compression else uncompressed
    header_size = 53 + (4 + 16 * block_count if compression else 0)
    blocks = []
    cursor = header_size
    if compression and block_count == 0:
        raise ConfigError("missing compression blocks")
    if not compression and block_count:
        raise ConfigError("unexpected compression blocks")
    if block_count == 1 and not encrypted:
        blocks.append((cursor, cursor + compressed))
    else:
        for _ in range(block_count):
            size = r.number("I")
            if not size:
                raise ConfigError("empty block")
            blocks.append((cursor, cursor + size))
            cursor += (size + 15) & ~15 if encrypted else size
    return Entry(path, offset, compressed, uncompressed, compression, encrypted,
                 block_size, tuple(blocks), header_size)


def _read_range(stream: BinaryIO, offset: int, size: int, end: int, limit: int) -> bytes:
    if not 0 <= offset <= end or not 0 <= size <= min(limit, end - offset):
        raise ConfigError("file range")
    stream.seek(offset)
    raw = stream.read(size)
    if len(raw) != size:
        raise ConfigError("file truncated")
    return raw


def _index(stream: BinaryIO, offset: int, size: int, digest: bytes, end: int) -> bytes:
    data = decrypt_distribution(_read_range(stream, offset, size, end, MAX_INDEX))
    if hashlib.sha1(data).digest() != digest:
        raise ConfigError("index checksum mismatch")
    return data


@dataclass(frozen=True)
class ConfigIndex:
    pak: Path
    mount: str
    entry_count: int
    index_sha1: str
    compression_names: tuple[str, ...]
    entries: tuple[Entry, ...]


def load_config_index(pak: Path = DEFAULT_PAK) -> ConfigIndex:
    """Verify indexes; retain only the four login-related configs, never assets."""
    with pak.open("rb") as stream:
        size = stream.seek(0, 2)
        footer = _read_range(stream, size - FOOTER_SIZE, FOOTER_SIZE, size, FOOTER_SIZE)
        magic, version, offset, length = struct.unpack_from("<IIQQ", footer, 17)
        if magic != 0x5A6F12E1 or version != 11 or footer[16] != 1:
            raise ConfigError("unsupported PAK footer")
        end = size - FOOTER_SIZE
        r = Reader(_index(stream, offset, length, footer[41:61], end))
        mount = r.string()
        if mount != "../../../":
            raise ConfigError("unexpected mount")
        count = r.count()
        r.number("Q")  # path hash seed is unused for exact directory lookup
        directory = None
        for kind in ("path", "directory"):
            flag = r.number("I")
            if flag not in (0, 1):
                raise ConfigError("index flag")
            if flag:
                ref = _index(stream, r.number("Q"), r.number("Q"), r.read(20), end)
                if kind == "directory":
                    directory = ref
        if directory is None:
            raise ConfigError("full directory index absent")
        encoded = r.read(r.count(MAX_INDEX))
        if r.number("I") != 0:
            raise ConfigError("unencoded entries unsupported")
        d = Reader(directory)
        selected = []
        seen = set()
        for _ in range(d.count()):
            dirname = d.string().lstrip("/")
            for _ in range(d.count()):
                path = dirname + d.string()
                at = d.number("I")
                if path in seen or len(seen) >= count:
                    raise ConfigError("duplicate or excess entries")
                seen.add(path)
                if at == 0x80000000:
                    continue
                if path in CONFIG_PATHS:
                    selected.append(decode_entry(path, encoded, at))
        if len(seen) != count:
            raise ConfigError("directory count mismatch")
        names = tuple(footer[61 + i * 32:93 + i * 32].split(b"\0", 1)[0].decode("ascii")
                      for i in range(5))
        return ConfigIndex(pak, mount, count, footer[41:61].hex(), names, tuple(selected))


def content_encryption_limit(path: str) -> int:
    # Primary format source: repak-rivals/repak/src/data.rs get_limit.
    from blake3 import blake3  # Optional research dependency.
    if path not in CONFIG_PATHS:
        raise ConfigError("config path not allowlisted")
    digest = blake3(b"\x11\x22\x33\x44" + path.lower().encode("ascii")).digest()
    return ((int.from_bytes(digest[:8], "little") % 61) * 63 + 319) & ~63


def read_config(index: ConfigIndex, path: str) -> bytes:
    """Return one allowlisted small configuration in RAM; never export assets."""
    if path not in CONFIG_PATHS:
        raise ConfigError("config path not allowlisted")
    matches = [entry for entry in index.entries if entry.path == path]
    if len(matches) != 1:
        raise ConfigError("config missing or ambiguous")
    e = matches[0]
    if not 0 <= e.uncompressed <= MAX_CONFIG or not 0 <= e.compressed <= MAX_CONFIG:
        raise ConfigError("config size")
    with index.pak.open("rb") as stream:
        end = stream.seek(0, 2) - FOOTER_SIZE
        r = Reader(_read_range(stream, e.offset, e.header_size, end, 32768))
        if (r.number("Q"), r.number("Q"), r.number("Q"), r.number("I")) != (
                0, e.compressed, e.uncompressed, e.compression):
            raise ConfigError("data header mismatch")
        digest = r.read(20)
        blocks = tuple((r.number("Q"), r.number("Q")) for _ in range(r.count(1024))) if e.compression else ()
        flags, block_size = r.number("B"), r.number("I")
        if blocks != e.blocks or flags != int(e.encrypted):
            raise ConfigError("data blocks mismatch")
        expected_block_size = e.uncompressed if len(blocks) == 1 else e.block_size
        if block_size != expected_block_size:
            raise ConfigError("data block size mismatch")
        length = (e.compressed + 15) & ~15 if e.encrypted else e.compressed
        data = _read_range(stream, e.offset + e.header_size, length, end, MAX_CONFIG + 15)
    # Installed files hash ciphertext, excluding final AES padding.
    if hashlib.sha1(data[:e.compressed]).digest() != digest:
        raise ConfigError("config checksum mismatch")
    if e.encrypted:
        limit = min(len(data), content_encryption_limit(path))
        data = decrypt_distribution(data[:limit]) + data[limit:]
    data = data[:e.compressed]
    if not e.compression:
        if len(data) != e.uncompressed:
            raise ConfigError("uncompressed size mismatch")
        return data
    if not 1 <= e.compression <= len(index.compression_names):
        raise ConfigError("compression slot")
    if index.compression_names[e.compression - 1] != "Oodle":
        raise ConfigError("unsupported compression")
    import ooz  # Optional open-source decompressor; never calls game code.
    remaining, chunks, previous = e.uncompressed, [], 0
    for start, stop in blocks:
        start, stop = start - e.header_size, stop - e.header_size
        if not previous <= start < stop <= len(data) or remaining <= 0:
            raise ConfigError("compression range")
        size = min(remaining, block_size)
        if size <= 0:
            raise ConfigError("empty compression output")
        try:
            chunk = ooz.decompress(data[start:stop], size)
        except RuntimeError as exc:
            raise ConfigError("decompression failed") from exc
        if len(chunk) != size:
            raise ConfigError("decompression size")
        chunks.append(chunk)
        remaining -= size
        previous = stop
    if remaining:
        raise ConfigError("incomplete decompression")
    return b"".join(chunks)


def shipping_login_url(config: bytes) -> str:
    """Read the overseas URL only, from the actual Windows game-instance section."""
    if len(config) > MAX_CONFIG:
        raise ConfigError("config size")
    section, values = "", []
    for line in config.decode("utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line
        elif section == "[/Script/Marvel.MarvelGameInstance]" and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() == "ShippingDefaultServerURL":
                values.append(value.strip())
    if len(values) != 1 or not values[0]:
        raise ConfigError("shipping URL missing or ambiguous")
    try:
        urls = json.loads(json.loads(values[0]))
        result = urls["EN"]
        parts = urlsplit(result)
    except (ValueError, KeyError, TypeError) as exc:
        raise ConfigError("shipping URL format") from exc
    if (parts.scheme != "https" or parts.netloc != "x20na.update.easebar.com"
            or parts.path != "/server_pc_prod_serverB" or parts.query or parts.fragment):
        raise ConfigError("unrecognized production URL")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pak", type=Path, default=DEFAULT_PAK)
    parser.add_argument("--read-login-url", action="store_true", help="Read the overseas login-list URL; no network")
    args = parser.parse_args(argv)
    try:
        index = load_config_index(args.pak)
        report = {"index_verified": True, "entry_count": index.entry_count,
                  "index_sha1": index.index_sha1,
                  "compression_names": index.compression_names,
                  "config_entries": [asdict(entry) for entry in index.entries],
                  "files_written": False, "network_used": False}
        if args.read_login_url:
            raw = read_config(index, SHIPPING_CONFIG)
            report.update(config_sha256=hashlib.sha256(raw).hexdigest(),
                          production_login_list_url=shipping_login_url(raw))
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ConfigError, UnicodeError, ImportError):
        print(json.dumps({"index_verified": False, "outcome": "configuration_not_supported"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
