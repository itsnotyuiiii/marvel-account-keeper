"""Normal suite2 game transport for the experimental direct-NetEase app.

Loads public distribution configuration from hash-pinned installed files,
creates fresh keys for its OWN connection, and can send one key-exchange reply.
No game-process access, capture decoding, credentials, login, Career RPC or file output.
The command previews by default. --handshake contacts only the previously
observed game peer, once, then reports structural metadata and closes. --echo
also sends one native-protocol heartbeat to verify both transport directions.
"""
import argparse
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import mmap
from pathlib import Path
import secrets
import socket
import struct
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from nex_rpc_probe import decode_game_hello, split_plaintext_records


GAME_ROOT = Path(r"C:\Program Files (x86)\Steam\steamapps\common\MarvelRivals\MarvelGame\Marvel")
ARCHIVE_PATH = GAME_ROOT / "Content" / "Marvel" / "NonAssetsToCopy" / "script.uep"
IMAGE_PATH = GAME_ROOT / "Binaries" / "Win64" / "Marvel-Win64-Shipping.exe"
IMAGE_SHA256 = "54154e879731f3f6fad32371bff78d62629c3d87ad5443dc9466871312a76714"
PUBLIC_KEY_SHA256 = "d37c1ab339cfd920f261b8457f9e5dadac6c076684ba93c51987d2299464401c"
PARAMETER_SHA256 = "e0f8b1c2e1e906a1d4cf345c554852a844cc8bc1411c0b7ba2385885cd4a9c49"
PARAMETER_RVA = 0xB41FB78
OBSERVED_PEER = ("34.60.237.47", 10203)
MAX_RECORD_BYTES = 64 * 1024


@dataclass(frozen=True)
class PublicConfiguration:
    public_key: rsa.RSAPublicKey = field(repr=False)
    parameter: bytes = field(repr=False)


def _archive_helper():
    path = Path(__file__).resolve().parent / "scripts" / "inspect-script-container.py"
    spec = importlib.util.spec_from_file_location("game_archive_inspector", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)  # Our inspector, NEVER the recovered game code.
    return helper


def _public_key_from_module(code):
    if (code.co_filename != "Scripts/core/bootstrap.py" or len(code.co_consts) <= 7
            or code.co_consts[6] is not True or not isinstance(code.co_consts[7], str)):
        raise ValueError("Unexpected public-key configuration")
    # The pinned module assigns True to USE_PUB_KEY and const7 to MOS_PUB_KEY.
    if code.co_code[96:104] != bytes((204, 6, 140, 11, 204, 7, 140, 12)):
        raise ValueError("Unvalidated public-key assignment")
    try:
        key = serialization.load_pem_public_key(code.co_consts[7].encode("ascii"))
        der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    except (ValueError, TypeError):
        raise ValueError("Invalid distribution public key") from None
    if (hashlib.sha256(der).hexdigest() != PUBLIC_KEY_SHA256
            or not isinstance(key, rsa.RSAPublicKey) or key.key_size != 1024):
        raise ValueError("Unrecognized distribution public key")
    return key


def load_public_configuration(archive=ARCHIVE_PATH, image=IMAGE_PATH):
    """Static extraction only; refuses another build rather than guessing RVAs."""
    helper = _archive_helper()
    with Path(archive).open("rb") as source:
        if not 24 <= source.seek(0, 2) <= 64 * 1024 * 1024:
            raise ValueError("Unrecognized archive size")
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
            payload, entries = helper.parse_index(data)  # Full archive hash gate.
            decoder = helper.bootstrap_decoder(data, payload, entries)
            code = helper.parse_code(helper.reverse_module(helper.bounded_zlib(
                decoder.decrypt(helper.read_entry(data, payload, entries, 3145)))))
            public_key = _public_key_from_module(code)
    import pefile  # Parses disk data only; never executes or loads the image.
    with Path(image).open("rb") as source:
        if not 1 <= source.seek(0, 2) <= 320 * 1024 * 1024:
            raise ValueError("Unrecognized native image size")
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
            if hashlib.sha256(data).hexdigest() != IMAGE_SHA256:
                raise ValueError("Unrecognized native image build")
            pe = pefile.PE(data=data, fast_load=True)
            try:
                parameter = bytes(pe.get_data(PARAMETER_RVA, 18))
            finally:
                pe.close()
    if len(parameter) != 18 or hashlib.sha256(parameter).hexdigest() != PARAMETER_SHA256:
        raise ValueError("Unrecognized handshake parameter")
    return PublicConfiguration(public_key, parameter)


@dataclass(repr=False)
class GameTransport:
    acknowledgement: bytes
    _receiver: object
    _sender: object

    def __repr__(self):
        return "GameTransport(own_connection_only=True)"

    def decrypt(self, chunk):
        if not isinstance(chunk, bytes) or len(chunk) > MAX_RECORD_BYTES:
            raise ValueError("Expected a bounded ciphertext chunk")
        return self._receiver.update(chunk)

    def encrypt(self, chunk):
        if not isinstance(chunk, bytes) or len(chunk) > MAX_RECORD_BYTES:
            raise ValueError("Expected a bounded plaintext chunk")
        return self._sender.update(chunk)


def prepare_transport(opening, configuration):
    """Native 0x547acde/0x5487860: SHA1 proof + two RSA-wrapped fresh randoms.

    This implements the installed protocol, not a new cryptographic design.
    Suite2 uses RSA PKCS1-v1.5 and AES-256-CTR, as selected by the native code.
    The signature is SHA1 of the challenge XOR-folded with the full parameter;
    it is NOT an account login or proof of server identity.
    """
    hello = decode_game_hello(opening)
    if (not isinstance(configuration, PublicConfiguration)
            or not isinstance(configuration.public_key, rsa.RSAPublicKey)
            or configuration.public_key.key_size != 1024
            or not isinstance(configuration.parameter, bytes)
            or len(configuration.parameter) != 18):
        raise ValueError("Unvalidated game transport configuration")
    receive_random, send_random = secrets.token_bytes(16), secrets.token_bytes(16)
    folded = bytearray(hello.challenge)
    for index, value in enumerate(configuration.parameter):
        folded[index % 16] ^= value
    proof = hashlib.sha1(folded).digest()
    wrapped = [configuration.public_key.encrypt(value, padding.PKCS1v15())
               for value in (receive_random, send_random)]
    fields = b"".join(struct.pack("<H", len(value)) + value for value in (proof, *wrapped))
    # Native 0x5487860: uint32 size, then three uint16-prefixed strings. No mode bytes.
    acknowledgement = struct.pack("<I", 4 + len(fields)) + fields
    receiver = Cipher(algorithms.AES(receive_random + hello.challenge),
                      modes.CTR(receive_random)).decryptor()
    sender = Cipher(algorithms.AES(send_random + hello.challenge),
                    modes.CTR(send_random)).encryptor()
    return GameTransport(acknowledgement, receiver, sender)


def _receive_exact(connection, count, deadline, counters):
    result = bytearray()
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        connection.settimeout(remaining)
        chunk = connection.recv(count - len(result))
        if not chunk:
            raise EOFError
        counters["bytes_received"] += len(chunk)
        result.extend(chunk)
    return bytes(result)


def _echo_request(timestamp):
    # Entry5053 C_SYS_GAME_ECHO: main65534/sub1, field1 timestamp:int32.
    return struct.pack("<IHBBi", 12, 65534, 1, 0x61, timestamp)


def _verify_echo(record, timestamp):
    # Entry5053 S_SYS_GAME_ECHO: timestamp:int32, svr_ts:int32, svr_ms:int16.
    if (record.main_cmd, record.sub_cmd) != (65534, 103):
        return False
    values, offset = {}, 0
    body = record.body
    while offset < len(body):
        tag = body[offset]
        offset += 1
        fmt = {0x61: "<i", 0x62: "<i", 0x43: "<h"}.get(tag)
        if fmt is None or tag in values or len(body) - offset < struct.calcsize(fmt):
            return False
        values[tag] = struct.unpack_from(fmt, body, offset)[0]
        offset += struct.calcsize(fmt)
    return (set(values) == {0x61, 0x62, 0x43} and values[0x61] == timestamp
            and 0 <= values[0x43] < 1000)


def check_handshake(configuration, *, send_echo=False, connect=socket.create_connection):
    """One bounded check; even a correctly bound echo does not mean login."""
    if type(send_echo) is not bool:
        raise ValueError("Specify whether to send the single heartbeat")
    report = {"outcome": "not_connected", "connections_attempted": 1,
              "bytes_received": 0, "handshake_bytes_sent": 0,
              "application_bytes_sent": 0, "credentials_sent": False,
              "login_verified": False, "career_verified": False,
              "plaintext_record_verified": False, "echo_verified": False,
              "secrets_saved": False}
    stage = "connect"
    try:
        deadline = time.monotonic() + 10
        with connect(OBSERVED_PEER, timeout=5) as connection:
            stage = "opening"
            opening = _receive_exact(connection, 28, deadline, report)
            transport = prepare_transport(opening, configuration)
            connection.sendall(transport.acknowledgement)
            report["handshake_bytes_sent"] = len(transport.acknowledgement)
            stage = "post_handshake"
            timestamp = int(time.time())
            if send_echo:
                request = _echo_request(timestamp)
                connection.sendall(transport.encrypt(request))
                report["application_bytes_sent"] = len(request)
            prefix = transport.decrypt(_receive_exact(connection, 4, deadline, report))
            size_word = struct.unpack("<I", prefix)[0]
            size, flags = size_word & 0x0FFFFFFF, size_word >> 28
            if flags not in (0, 8) or not (9 if flags == 8 else 7) <= size <= MAX_RECORD_BYTES:
                raise ValueError("Unvalidated post-handshake framing")
            frame = prefix + transport.decrypt(_receive_exact(connection, size - 4, deadline, report))
            record, = split_plaintext_records(frame)
            report.update(outcome="plaintext_record_received", plaintext_record_verified=True,
                          main_cmd=record.main_cmd, sub_cmd=record.sub_cmd,
                          body_bytes=len(record.body))
            if send_echo and _verify_echo(record, timestamp):
                report.update(outcome="echo_verified", echo_verified=True)
    except TimeoutError:
        report["outcome"] = stage + "_timeout"
    except EOFError:
        report["outcome"] = stage + "_closed"
    except OSError:
        report["outcome"] = stage + "_network_error"
    except ValueError:
        report["outcome"] = stage + "_unvalidated"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--handshake", action="store_true", help="Send one normal handshake; no login or RPC")
    group.add_argument("--echo", action="store_true", help="Handshake plus one heartbeat; no credentials or login")
    args = parser.parse_args(argv)
    if not args.handshake and not args.echo:
        print(json.dumps({"outcome": "preview", "network_attempted": False,
                          "credentials_read": False, "configuration_read": False}))
        return 0
    try:
        configuration = load_public_configuration()
    except (OSError, ValueError, ImportError):
        print(json.dumps({"outcome": "configuration_unavailable", "network_attempted": False}))
        return 1
    print(json.dumps(check_handshake(configuration, send_echo=args.echo)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
