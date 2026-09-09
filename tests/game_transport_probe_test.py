"""Synthetic keypair and sockets only; never reads the installed game or account."""
import contextlib
import hashlib
import io
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import game_transport_probe as game
from nex_rpc_probe import NexRecord


class GameTransportProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        cls.configuration = game.PublicConfiguration(cls.key.public_key(), b"fixture-parameter!")
        cls.challenge = bytes(range(16))
        cls.opening = struct.pack("<IBBH", 28, 2, 0, 16) + cls.challenge + b"\0" * 4

    def unpack_ack(self, wire):
        self.assertEqual(struct.unpack_from("<I", wire)[0], len(wire))
        self.assertEqual(len(wire), 286)
        fields, offset = [], 4
        for size in (20, 128, 128):
            self.assertEqual(struct.unpack_from("<H", wire, offset)[0], size)
            offset += 2
            fields.append(wire[offset:offset + size])
            offset += size
        self.assertEqual(offset, len(wire))
        return fields

    def server_ciphers(self, ack):
        _, receive_wrapped, send_wrapped = self.unpack_ack(ack)
        receive_random = self.key.decrypt(receive_wrapped, padding.PKCS1v15())
        send_random = self.key.decrypt(send_wrapped, padding.PKCS1v15())
        return (Cipher(algorithms.AES(receive_random + self.challenge), modes.CTR(receive_random)).encryptor(),
                Cipher(algorithms.AES(send_random + self.challenge), modes.CTR(send_random)).decryptor())

    def test_acknowledgement_native_layout_and_full_parameter_fold(self):
        transport = game.prepare_transport(self.opening, self.configuration)
        proof, first, second = self.unpack_ack(transport.acknowledgement)
        expected = bytes(self.challenge[i] ^ self.configuration.parameter[i]
                         ^ (self.configuration.parameter[i + 16] if i < 2 else 0) for i in range(16))
        self.assertEqual(proof, hashlib.sha1(expected).digest())
        for encrypted in (first, second):
            self.assertEqual(len(self.key.decrypt(encrypted, padding.PKCS1v15())), 16)
        self.assertNotEqual(first, second)

    def test_fresh_randoms_for_each_direction_and_connection(self):
        with patch.object(game.secrets, "token_bytes", side_effect=[b"a" * 16, b"b" * 16, b"c" * 16, b"d" * 16]) as rng:
            first = self.unpack_ack(game.prepare_transport(self.opening, self.configuration).acknowledgement)
            second = self.unpack_ack(game.prepare_transport(self.opening, self.configuration).acknowledgement)
        values = [self.key.decrypt(value, padding.PKCS1v15()) for value in (*first[1:], *second[1:])]
        self.assertEqual(values, [letter * 16 for letter in (b"a", b"b", b"c", b"d")])
        self.assertEqual([call.args for call in rng.call_args_list], [(16,)] * 4)

    def test_cipher_directions_and_fragmented_state(self):
        transport = game.prepare_transport(self.opening, self.configuration)
        server_send, server_recv = self.server_ciphers(transport.acknowledgement)
        payload = bytes(range(255)) * 3
        encrypted = server_send.update(payload)
        self.assertEqual(b"".join(transport.decrypt(encrypted[i:i + 3]) for i in range(0, len(encrypted), 3)), payload)
        outbound = b"".join(transport.encrypt(payload[i:i + 7]) for i in range(0, len(payload), 7))
        self.assertEqual(server_recv.update(outbound), payload)

    def test_unsupported_opening_and_configuration_rejected(self):
        for opening in (b"", self.opening[:-1], self.opening[:4] + b"\1" + self.opening[5:]):
            with self.subTest(length=len(opening)), self.assertRaises(ValueError):
                game.prepare_transport(opening, self.configuration)
        for config in (None, game.PublicConfiguration(self.key.public_key(), b""),
                       game.PublicConfiguration(self.key, b"x" * 18)):
            with self.assertRaises(ValueError):
                game.prepare_transport(self.opening, config)

    def test_chunk_bounds_and_repr_do_not_expose_material(self):
        transport = game.prepare_transport(self.opening, self.configuration)
        for method in (transport.encrypt, transport.decrypt):
            for invalid in (bytearray(b"x"), None, b"x" * (game.MAX_RECORD_BYTES + 1)):
                with self.assertRaises(ValueError):
                    method(invalid)
        self.assertEqual(repr(transport), "GameTransport(own_connection_only=True)")
        self.assertNotIn("fixture", repr(self.configuration))

    def test_public_configuration_module_shape_and_key_pin(self):
        der = self.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        pem = self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        code = SimpleNamespace(co_filename="Scripts/core/bootstrap.py",
                               co_consts=(None,) * 6 + (True, pem.decode("ascii")),
                               co_code=b"\0" * 96 + bytes((204, 6, 140, 11, 204, 7, 140, 12)))
        with self.assertRaises(ValueError):
            game._public_key_from_module(code)  # Fixture isn't the pinned key.
        with patch.object(game, "PUBLIC_KEY_SHA256", hashlib.sha256(der).hexdigest()):
            self.assertEqual(game._public_key_from_module(code).public_numbers(), self.key.public_key().public_numbers())
            code.co_filename = "unexpected.py"
            with self.assertRaises(ValueError):
                game._public_key_from_module(code)

    def test_preview_never_loads_files_or_connects(self):
        output = io.StringIO()
        with patch.object(game, "load_public_configuration") as load, patch.object(game, "check_handshake") as live:
            with contextlib.redirect_stdout(output):
                self.assertEqual(game.main([]), 0)
        load.assert_not_called()
        live.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["outcome"], "preview")

    def test_native_echo_layout_and_response_binding(self):
        self.assertEqual(game._echo_request(123456).hex(), "0c000000feff016140e20100")
        body = b"\x61" + struct.pack("<i", 123456) + b"\x62" + struct.pack("<i", 654321) + b"\x43" + struct.pack("<h", 123)
        self.assertTrue(game._verify_echo(NexRecord(65534, 103, body), 123456))
        self.assertFalse(game._verify_echo(NexRecord(65534, 103, body), 123455))
        self.assertFalse(game._verify_echo(NexRecord(1, 103, body), 123456))
        for invalid in (body[:-1], body + body, body + b"\0", body[:-2] + struct.pack("<h", 1000)):
            self.assertFalse(game._verify_echo(NexRecord(65534, 103, invalid), 123456))

    def test_field_order_is_arbitrary_but_zero_echo_is_not_cookie_verification(self):
        reordered = b"\x43" + struct.pack("<h", 123) + b"\x62" + struct.pack("<i", 654321) + b"\x61" + struct.pack("<i", 123456)
        self.assertTrue(game._verify_echo(NexRecord(65534, 103, reordered), 123456))
        # Live gateway's reply had this public all-zero heartbeat shape before login.
        zero = bytes.fromhex("43000062000000006100000000")
        self.assertFalse(game._verify_echo(NexRecord(65534, 103, zero), 123456))

    def test_echo_sends_one_encrypted_heartbeat_and_verifies_matching_reply(self):
        connection = self.fake_connection("echo")
        with patch.object(game.time, "time", return_value=123456):
            report = game.check_handshake(self.configuration, send_echo=True, connect=lambda *a, **kw: connection)
        self.assertEqual(report["outcome"], "echo_verified")
        self.assertTrue(report["echo_verified"])
        self.assertEqual(report["application_bytes_sent"], 12)
        self.assertEqual(len(connection.sent), 2)
        self.assertFalse(report["login_verified"])
        self.assertTrue(connection.closed)

    def fake_connection(self, response="valid"):
        test = self
        class FakeConnection:
            def __init__(self):
                self.pending, self.sent, self.closed = test.opening, [], False
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.closed = True
            def settimeout(self, timeout):
                test.assertGreater(timeout, 0)
                test.assertLessEqual(timeout, 10)
            def recv(self, count):
                if not self.pending:
                    if response == "timeout":
                        raise TimeoutError
                    return b""
                result, self.pending = self.pending[:min(count, 3)], self.pending[min(count, 3):]
                return result
            def sendall(self, data):
                self.sent.append(data)
                if response == "echo":
                    if len(self.sent) == 1:
                        self.server_send, self.server_recv = test.server_ciphers(data)
                    else:
                        request = self.server_recv.update(data)
                        test.assertEqual(request, game._echo_request(123456))
                        body = b"\x61" + struct.pack("<i", 123456) + b"\x62" + struct.pack("<i", 654321) + b"\x43" + struct.pack("<h", 123)
                        self.pending += self.server_send.update(struct.pack("<IHB", 7 + len(body), 65534, 103) + body)
                if response in ("valid", "invalid"):
                    sender, _ = test.server_ciphers(data)
                    plaintext = struct.pack("<IHB", 8, 1, 101) + b"x" if response == "valid" else b"\xff" * 4
                    self.pending += sender.update(plaintext)
        return FakeConnection()

    def test_one_handshake_and_one_redacted_record_without_application_send(self):
        connection = self.fake_connection()
        def connect(peer, timeout):
            self.assertEqual(peer, game.OBSERVED_PEER)
            self.assertEqual(timeout, 5)
            return connection
        report = game.check_handshake(self.configuration, connect=connect)
        self.assertEqual(report["outcome"], "plaintext_record_received")
        self.assertEqual((report["main_cmd"], report["sub_cmd"], report["body_bytes"]), (1, 101, 1))
        self.assertEqual(report["application_bytes_sent"], 0)
        self.assertFalse(report["login_verified"])
        self.assertEqual(len(connection.sent), 1)
        self.assertTrue(connection.closed)

    def test_timeout_eof_and_bad_record_are_not_verified(self):
        for response, expected in (("timeout", "post_handshake_timeout"),
                                   ("closed", "post_handshake_closed"),
                                   ("invalid", "post_handshake_unvalidated")):
            connection = self.fake_connection(response)
            report = game.check_handshake(self.configuration, connect=lambda *a, **kw: connection)
            self.assertEqual(report["outcome"], expected)
            self.assertFalse(report["plaintext_record_verified"])
            self.assertFalse(report["credentials_sent"])
            self.assertTrue(connection.closed)

    def test_connection_failure_has_no_retry_or_material_in_report(self):
        from unittest.mock import Mock
        connect = Mock(side_effect=OSError("sensitive error details must not appear"))
        report = game.check_handshake(self.configuration, connect=connect)
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(report["outcome"], "connect_network_error")
        self.assertNotIn("sensitive", json.dumps(report))
        self.assertEqual(report["application_bytes_sent"], 0)
        self.assertFalse(report["login_verified"])


if __name__ == "__main__":
    unittest.main()
