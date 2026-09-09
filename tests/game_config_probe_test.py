"""Synthetic distribution/config fixtures only; never reads installed game data."""
import contextlib
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import game_config_probe as config


def encrypt(data):
    data += b"\0" * (-len(data) % 16)
    cipher = Cipher(algorithms.AES(config._words(config.PUBLIC_CONTENT_KEY)), modes.ECB()).encryptor()
    return config._words(cipher.update(config._words(data)) + cipher.finalize())


def string(value):
    raw = value.encode() + b"\0"
    return struct.pack("<i", len(raw)) + raw


def fixture(raw=b"[fixture]\nvalue=1\n"):
    encrypted = encrypt(raw)
    header = struct.pack("<QQQI", 0, len(raw), len(raw), 0)
    header += hashlib.sha1(encrypted[:len(raw)]).digest() + struct.pack("<BI", 1, 0)
    data = header + encrypted
    directory = struct.pack("<I", 1) + string("/Marvel/Config/Windows/")
    directory += struct.pack("<I", 1) + string("WindowsGame.ini") + struct.pack("<I", 0)
    directory += b"\0" * (-len(directory) % 16)
    directory_offset = len(data)
    data += encrypt(directory)
    entry = struct.pack("<III", (1 << 31) | (1 << 30) | (1 << 22), 0, len(raw))
    index = string("../../../") + struct.pack("<IQIIQQ", 1, 0, 0, 1, directory_offset, len(directory))
    index += hashlib.sha1(directory).digest() + struct.pack("<I", len(entry)) + entry + b"\0" * 4
    index += b"\0" * (-len(index) % 16)
    index_offset = len(data)
    data += encrypt(index)
    footer = b"\0" * 16 + b"\1" + struct.pack("<IIQQ", 0x5A6F12E1, 11, index_offset, len(index))
    footer += hashlib.sha1(index).digest() + b"\0" * 160
    return data + footer


def load(data):
    with patch.object(Path, "open", return_value=io.BytesIO(data)):
        return config.load_config_index(Path("fixture.pak"))


class GameConfigProbeTest(unittest.TestCase):
    def test_word_swapped_aes_matches_nist_vector(self):
        # AES-256 ECB published test vector, with the distribution word order.
        key = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
        plaintext = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        encrypted = bytes.fromhex("f3eed1bdb5d2a03c064b5a7e3db181f8")
        self.assertEqual(config.decrypt_distribution(config._words(encrypted), config._words(key)),
                         config._words(plaintext))

    def test_aes_rejects_bad_alignment(self):
        for raw, key in [(b"bad", bytes(32)), (bytes(16), bytes(16))]:
            with self.assertRaises(config.ConfigError):
                config.decrypt_distribution(raw, key)

    def test_primary_and_directory_integrity_and_exact_config(self):
        index = load(fixture())
        self.assertEqual(index.entry_count, 1)
        self.assertEqual([e.path for e in index.entries], [config.SHIPPING_CONFIG])
        self.assertEqual(index.entries[0].header_size, 53)

    def test_primary_index_checksum_failure(self):
        data = bytearray(fixture())
        data[-config.FOOTER_SIZE + 41] ^= 1
        with self.assertRaisesRegex(config.ConfigError, "index checksum"):
            load(bytes(data))

    def test_directory_checksum_failure(self):
        data = bytearray(fixture())
        data[85] ^= 1
        with self.assertRaises(config.ConfigError):
            load(bytes(data))

    def test_footer_rejects_wrong_version_or_out_of_file_index(self):
        for field_offset, value in [(21, struct.pack("<I", 10)), (25, struct.pack("<Q", 1 << 60))]:
            data = bytearray(fixture())
            start = len(data) - config.FOOTER_SIZE + field_offset
            data[start:start + len(value)] = value
            with self.assertRaises(config.ConfigError):
                load(bytes(data))

    def test_uncompressed_payload_checksum_excludes_padding(self):
        raw = b"[fixture]\nvalue=1\n"
        data = fixture(raw)
        index = load(data)
        with patch.object(Path, "open", return_value=io.BytesIO(data)), \
                patch.object(config, "content_encryption_limit", return_value=4096):
            self.assertEqual(config.read_config(index, config.SHIPPING_CONFIG), raw)

    def test_payload_and_header_mismatch_fail_before_decode(self):
        data = fixture()
        index = load(data)
        for at in (8, 53):
            broken = bytearray(data)
            broken[at] ^= 1
            with patch.object(Path, "open", return_value=io.BytesIO(broken)), self.assertRaises(config.ConfigError):
                config.read_config(index, config.SHIPPING_CONFIG)

    def test_read_refuses_other_assets_and_oversized_config(self):
        index = load(fixture())
        with patch.object(Path, "open") as opened, self.assertRaises(config.ConfigError):
            config.read_config(index, "Marvel/Content/Asset.uasset")
        opened.assert_not_called()
        oversized = replace(index, entries=(replace(index.entries[0], uncompressed=config.MAX_CONFIG + 1),))
        with self.assertRaises(config.ConfigError):
            config.read_config(oversized, config.SHIPPING_CONFIG)

    def test_shipping_url_reads_only_the_overseas_game_section(self):
        url = "https://x20na.update.easebar.com/server_pc_prod_serverB"
        value = json.dumps(json.dumps({"CN": "https://example.invalid", "EN": url}))
        raw = ("[Other]\nShippingDefaultServerURL=ignored\n"
               "[/Script/Marvel.MarvelGameInstance]\nShippingDefaultServerURL=" + value).encode()
        self.assertEqual(config.shipping_login_url(raw), url)
        for bad in (raw + b"\nShippingDefaultServerURL=duplicate", raw.replace(b"https://x20na", b"http://x20na"),
                    raw.replace(b"easebar.com", b"example.com"), raw.replace(b"[Other]", b"[Other]" )[:20]):
            with self.assertRaises((config.ConfigError, UnicodeError)):
                config.shipping_login_url(bad)

    def test_string_bounds_encoding_and_terminator(self):
        self.assertEqual(config.Reader(struct.pack("<i", -3) + "ok\0".encode("utf-16-le")).string(), "ok")
        for raw in (struct.pack("<i", 5000), string("x")[:-1], struct.pack("<i", 2) + b"xx"):
            with self.assertRaises(config.ConfigError):
                config.Reader(raw).string()

    def test_default_cli_does_not_read_config_payload_or_use_network(self):
        index = load(fixture())
        with patch.object(config, "load_config_index", return_value=index), \
                patch.object(config, "read_config") as read, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(config.main([]), 0)
        read.assert_not_called()
        self.assertFalse(json.loads(out.getvalue())["network_used"])


if __name__ == "__main__":
    unittest.main()
