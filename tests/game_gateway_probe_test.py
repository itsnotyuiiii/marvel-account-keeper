"""Synthetic normal gateway LOGIN; no accounts or live sockets."""
import json
import struct
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import game_login_probe as login
from game_login_probe_test import FakeSocket, auth_reply, record


def gateway_reply(uid=login.EXPECTED_OWN_UID):
    return {"errcode": 0, "relogin": 0,
            "info": {"uid": uid, "client_ver": login.CLIENT_VERSION, "login_key": "fixture-login-secret"},
            "timestamp": 1700000000, "timestamp2": 1700000000, "reason": ""}


class GameGatewayProbeTest(unittest.TestCase):
    def run_check(self, reply, *, sub=103, prefix=b""):
        incoming = bytes(28) + prefix + record(sub, login.encode_body(login.LOGIN_REPLY, reply))
        sock = FakeSocket(incoming)
        transport = SimpleNamespace(acknowledgement=b"ack", encrypt=lambda x: x, decrypt=lambda x: x)
        with patch.object(login, "prepare_transport", return_value=transport):
            report = login.check_gateway(None, auth_reply(), connect=lambda *a, **k: sock)
        return sock, report

    def test_schema_roundtrip(self):
        reply = gateway_reply()
        self.assertEqual(login.decode_body(login.LOGIN_REPLY, login.encode_body(login.LOGIN_REPLY, reply)), reply)

    def test_normal_own_gateway_success_sends_exact_enter_not_create(self):
        sock, report = self.run_check(gateway_reply())
        self.assertTrue(report["game_login_verified"])
        self.assertFalse(report["career_verified"])
        self.assertTrue(sock.closed)
        self.assertEqual(len(sock.sent), 2)
        self.assertEqual(sock.sent[1][4:7], b"\x01\x00\x03")
        enter = login.decode_body(login.ENTER_REQUEST, sock.sent[1][7:])
        self.assertEqual(enter, {"auth_hostid": 13001, "auth_sn": "fixture-only", "device_id": "",
                                 "basic_info": "", "uid": login.EXPECTED_OWN_UID})
        self.assertNotIn("fixture-login-secret", json.dumps(report))
        self.assertNotIn("fixture-only", json.dumps(report))

    def test_wrong_uid_or_invalid_relogin_flag_never_counts_as_success(self):
        wrong_uid = gateway_reply(12345)
        relogin = gateway_reply()
        relogin["relogin"] = 2
        for reply in (wrong_uid, relogin):
            sock, report = self.run_check(reply)
            self.assertFalse(report["game_login_verified"])
            self.assertTrue(sock.closed)

    def test_optional_relogin_indicator_is_not_an_alternate_auth_path(self):
        for value in (None, 0, 1):
            reply = gateway_reply()
            if value is None:
                del reply["relogin"]
            else:
                reply["relogin"] = value
            sock, report = self.run_check(reply)
            self.assertTrue(report["game_login_verified"])
            self.assertEqual(sock.sent[1][4:7], b"\x01\x00\x03")
            self.assertTrue(sock.closed)

    def test_gateway_rejection_has_no_retry(self):
        sock, report = self.run_check({"errcode": 123, "reason": "fixture-secret-reason"})
        self.assertEqual(report["server_error_code"], 123)
        self.assertEqual(report["connections_attempted"], 1)
        self.assertEqual(len(sock.sent), 2)
        self.assertTrue(sock.closed)
        self.assertNotIn("fixture-secret-reason", json.dumps(report))

    def test_unexpected_command_is_not_guessed_or_executed(self):
        sock, report = self.run_check(gateway_reply(), sub=104)
        self.assertEqual(report["outcome"], "unexpected_gateway_login_command")
        self.assertFalse(report["game_login_verified"])
        self.assertTrue(sock.closed)

    def test_known_startup_hotfix_is_bounded_and_never_executed(self):
        body = login.encode_body(login.HOTFIX_PUSH, {"split": 0, "path": "fixture-update",
                                 "content": b"never execute this", "signature": b"fixture"})
        update = struct.pack("<IHB", len(body) + 7, 4, 102) + body
        sock, report = self.run_check(gateway_reply(), prefix=update * 2)
        self.assertTrue(report["game_login_verified"])
        self.assertEqual(report["startup_hotfix_records"], 2)
        self.assertFalse(report["remote_code_executed"])
        self.assertNotIn("never execute this", json.dumps(report))
        self.assertTrue(sock.closed)

    def test_excessive_startup_hotfixes_stop_before_login_verified(self):
        body = login.encode_body(login.HOTFIX_PUSH, {"split": 0, "path": "fixture-update",
                                 "content": b"x", "signature": b"fixture"})
        update = struct.pack("<IHB", len(body) + 7, 4, 102) + body
        sock, report = self.run_check(gateway_reply(), prefix=update * 17)
        self.assertEqual(report["outcome"], "startup_hotfix_bound")
        self.assertFalse(report["game_login_verified"])
        self.assertTrue(sock.closed)

    def test_invalid_auth_role_stops_before_network(self):
        with patch.object(login.socket, "create_connection") as network:
            with self.assertRaises(login.LoginError):
                login.check_gateway(None, auth_reply(12345), connect=network)
        network.assert_not_called()

    def test_missing_binding_wrong_types_and_missing_normal_result_rejected(self):
        for key, value in (("uid", True), ("login_key", "")):
            reply = gateway_reply()
            reply["info"][key] = value
            with self.assertRaises(login.LoginError):
                login.verify_gateway_login(reply, login.EXPECTED_OWN_UID)
        for key in ("errcode",):
            reply = gateway_reply()
            del reply[key]
            with self.assertRaises(login.LoginError):
                login.verify_gateway_login(reply, login.EXPECTED_OWN_UID)

    def test_entry_requires_auth_inputs(self):
        with self.assertRaisesRegex(login.LoginError, "gateway_requires_authentication"):
            login.check_login(None, login.LoginServer("8.8.8.8", 13001, (10001,)), enter=True)

    def test_larger_avatar_record_uses_bounded_cipher_chunks(self):
        class BulkSocket(FakeSocket):
            def recv(self, count):
                result, self.incoming = self.incoming[:count], self.incoming[count:]
                return result
        body = b"x" * (login.MAX_BODY + 17)
        sock = BulkSocket(struct.pack("<IHB", len(body) + 7, 3, 101) + body)
        chunks = []
        def decrypt(value):
            chunks.append(len(value))
            return value
        result = login.receive_record(sock, SimpleNamespace(decrypt=decrypt), time.monotonic() + 5,
                                      {"bytes_received": 0}, max_size=4 * 1024 * 1024)
        self.assertEqual(result.body, body)
        self.assertTrue(all(size <= login.MAX_BODY for size in chunks))

    def test_unknown_record_mode_is_reported_not_guessed(self):
        report = {"bytes_received": 0}
        sock = FakeSocket(struct.pack("<I", (1 << 28) | 100))
        with self.assertRaises(login.LoginError):
            login.receive_record(sock, SimpleNamespace(decrypt=lambda v: v), time.monotonic() + 5, report)
        self.assertEqual(report["unrecognized_record_flags"], 1)
        self.assertEqual(report["unrecognized_record_size"], 100)


if __name__ == "__main__":
    unittest.main()
