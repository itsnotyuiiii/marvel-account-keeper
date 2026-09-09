"""Synthetic wire data, SDK inputs and sockets; no accounts or live requests."""
import contextlib
import io
import json
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import game_login_probe as login
from nex_rpc_probe import NexRecord


def record(sub, body):
    return struct.pack("<IHB", len(body) + 7, 1, sub) + body


def auth_reply(uid=login.EXPECTED_OWN_UID):
    return {"err_code": 0, "auth_hostid": 13001, "auth_sn": "fixture-only",
            "zone_list": [{"role_uid": uid, "role_inited": 1, "gate_ip": "8.8.8.8", "gate_port": 10002}]}


class FakeSocket:
    def __init__(self, incoming):
        self.incoming, self.sent, self.closed = incoming, [], False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def settimeout(self, timeout):
        pass

    def recv(self, count):
        result, self.incoming = self.incoming[:min(count, 3)], self.incoming[min(count, 3):]
        return result

    def sendall(self, data):
        self.sent.append(data)


class GameLoginProbeTest(unittest.TestCase):
    def test_extended_tag_is_big_endian_but_string_length_is_little_endian(self):
        schema = login.fields((16, "s", 8), (30, "n", 1))
        expected = b"\xf0\x10\x03\x00\x00\x00abc\x30\x1e\x01"
        self.assertEqual(login.encode_body(schema, {"s": "abc", "n": 1}), expected)
        self.assertEqual(login.decode_body(schema, expected), {"s": "abc", "n": 1})

    def test_array_is_byte_length_with_individually_framed_dicts_not_count(self):
        schema = login.fields((1, "rows", 9, login.fields((1, "value", 5))))
        expected = bytes.fromhex("e112000000050000006101000000050000006102000000")
        value = {"rows": [{"value": 1}, {"value": 2}]}
        self.assertEqual(login.encode_body(schema, value), expected)
        self.assertEqual(login.decode_body(schema, expected), value)

    def test_nested_struct_and_signed_integer_roundtrip(self):
        schema = login.fields((1, "data", 11, login.fields((2, "small", 1), (3, "wide", 12))))
        value = {"data": {"small": -128, "wide": 2**60}}
        wire = login.encode_body(schema, value)
        self.assertEqual(login.decode_body(schema, wire), value)

    def test_all_auth_fields_roundtrip_including_ids_16_to_30(self):
        bundle = {key: "fixture" for key in ("sdkuid", "gas_token", "sessionid", "deviceid", "ip",
                                               "sdk_version", "app_channel", "login_channel", "udid", "aim_info")}
        bundle.update(gameid="x20naxx2gb", platform="pc", gas_token="", get_access_token="fixture-flag")
        value = login.build_auth(bundle, {"SDC_LOG_OS_NAME": "pc", "DEVICE_DESKTOP_RESOLUTION": "2880*1800"})
        self.assertEqual(value["client_version"], "3825464")
        self.assertEqual(value["info"]["platform_type"], 0)
        self.assertEqual(value["device_height"], 2880)
        self.assertEqual(value["device_width"], 1800)
        self.assertEqual(json.loads(value["sauth_json"]), bundle)
        self.assertEqual(login.decode_body(login.AUTH_REQUEST, login.encode_body(login.AUTH_REQUEST, value)), value)

    def test_invalid_sdk_inputs_do_not_become_mock_auth(self):
        with self.assertRaises(login.LoginError):
            login.build_auth({}, {})

    def test_decoder_rejects_duplicate_unknown_mistyped_and_noncanonical_tags(self):
        for wire in (b"\x61\0\0\0\0" * 2, b"\x6f\0\0\0\0", b"\x21\0",
                     b"\x70\x01\0\0\0\0", b"\x10\xf0\0\0\0\0"):
            with self.assertRaises(login.LoginError):
                login.decode_body(login.AUTH_REPLY, wire)

    def test_decoder_rejects_truncated_lengths_and_arrays(self):
        for wire in (b"\xe2\x10\0\0\0x", b"\xe5\x05\0\0\0\x02\0\0\0x", b"\xe2\0"):
            with self.assertRaises(login.LoginError):
                login.decode_body(login.AUTH_REPLY, wire)

    def test_integer_ranges_and_array_bound(self):
        for value in (128, True, "1"):
            with self.assertRaises(login.LoginError):
                login.encode_body(login.fields((1, "n", 1)), {"n": value})
        with self.assertRaises(login.LoginError):
            login.encode_body(login.PREPARE, {"version_list": [{}] * 33})

    def test_shipping_list_host_number_is_not_gateway_port(self):
        server = login.parse_server_list(b"prod 34.8.137.117 10001 13001 1 1788426300 10001-10010")
        self.assertEqual(server.host_number, 13001)
        self.assertEqual(server.ports, tuple(range(10001, 10011)))
        server = login.parse_server_list(b"prod 34.8.137.117 10001 13001 1 1788426300 10001-10010:10002")
        self.assertNotIn(10002, server.ports)

    def test_http_list_reader_decodes_content_encoding_with_a_size_bound(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.raw.read.return_value = b"prod 34.8.137.117 10001 13001 1 1788426300 10001-10010"
        with patch.object(login, "load_config_index"), patch.object(login, "read_config"), \
                patch.object(login, "shipping_login_url", return_value="https://example.invalid/list"), \
                patch("requests.get", return_value=response) as get:
            self.assertEqual(login.advertised_server().host_number, 13001)
        response.raw.read.assert_called_once_with(4097, decode_content=True)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_list_rejects_private_host_closed_status_and_unbounded_range(self):
        for raw in (b"prod 127.0.0.1 10001 13001 1 1788426300 10001-10010",
                    b"prod 34.8.137.117 10001 13001 0 1788426300 10001-10010",
                    b"prod 34.8.137.117 10001 13001 1 1788426300 1-65000",
                    b"prod 34.8.137.117 10001 13001 1 1788426300 10001-10010:12345"):
            with self.assertRaises(login.LoginError):
                login.parse_server_list(raw)

    def test_expected_role_is_required_unique_initialized_and_public_gateway(self):
        self.assertEqual(login.verify_auth_role(auth_reply(), login.EXPECTED_OWN_UID)["role_uid"], login.EXPECTED_OWN_UID)
        missing = auth_reply(12345)
        duplicate = auth_reply()
        duplicate["zone_list"] *= 2
        uninitialized = auth_reply()
        uninitialized["zone_list"][0]["role_inited"] = 0
        private = auth_reply()
        private["zone_list"][0]["gate_ip"] = "127.0.0.1"
        for reply in (missing, duplicate, uninitialized, private, {"err_code": 1}):
            with self.assertRaises(login.LoginError):
                login.verify_auth_role(reply, login.EXPECTED_OWN_UID)

    def run_check(self, reply=None, *, authenticate=True, extra_args=b""):
        prep = login.encode_body(login.PREPARE, {"version_list": [], "args": extra_args,
                                                "is_test_branch": 0, "timestamp": 1700000000, "timezone": 0})
        wire = bytes(28) + record(101, prep)
        if reply is not None:
            wire += record(102, login.encode_body(login.AUTH_REPLY, reply))
        sock = FakeSocket(wire)
        transport = SimpleNamespace(acknowledgement=b"ack", encrypt=lambda x: x, decrypt=lambda x: x)
        inputs = login.LoginInputs({"sauth_json": "fixture-secret"}, "fixture") if authenticate else None
        server = login.LoginServer("8.8.8.8", 13001, (10001,))
        with patch.object(login, "prepare_transport", return_value=transport), \
                patch.object(login.time, "time", return_value=1700000000):
            report = login.check_login(None, server, inputs=inputs, connect=lambda *a, **k: sock)
        return sock, report

    def test_prepare_sends_no_application_bytes_or_credentials(self):
        sock, report = self.run_check(authenticate=False)
        self.assertTrue(report["prepare_verified"])
        self.assertFalse(report["credentials_sent"])
        self.assertEqual(sock.sent, [b"ack"])
        self.assertTrue(sock.closed)

    def test_auth_success_is_uid_bound_but_not_gateway_login(self):
        sock, report = self.run_check(auth_reply())
        self.assertTrue(report["auth_service_accepted"])
        self.assertTrue(report["own_role_verified"])
        self.assertFalse(report["game_login_verified"])
        self.assertFalse(report["career_verified"])
        self.assertEqual(len(sock.sent), 2)
        self.assertNotIn("fixture-secret", json.dumps(report))
        self.assertTrue(sock.closed)

    def test_auth_rejection_does_not_retry_or_attempt_other_roles(self):
        sock, report = self.run_check({"err_code": 501})
        self.assertEqual(report["server_error_code"], 501)
        self.assertFalse(report["own_role_verified"])
        self.assertEqual(len(sock.sent), 2)
        self.assertTrue(sock.closed)

    def test_sdk_error_exposes_codes_but_never_free_form_message_or_credentials(self):
        raw = json.dumps({"code": 405, "subcode": "123", "access_token": "fixture-secret",
                          "channel_msg": {"msg": "session expired for fixture-secret"}})
        sock, report = self.run_check({"err_code": 253, "sdk_login_str": raw})
        self.assertEqual(report["sdk_code"], 405)
        self.assertEqual(report["sdk_subcode"], 123)
        self.assertTrue(report["sdk_message_mentions_expiry"])
        self.assertNotIn("fixture-secret", json.dumps(report))
        self.assertTrue(sock.closed)

    def test_additional_prepare_requirements_stop_before_credentials(self):
        sock, report = self.run_check(extra_args=b"do-not-execute")
        self.assertEqual(report["outcome"], "additional_prepare_requirements")
        self.assertFalse(report["credentials_sent"])
        self.assertEqual(len(sock.sent), 1)

    def test_default_preview_avoids_credentials_and_network(self):
        with patch.object(login, "advertised_server") as network, \
                patch.object(login, "load_own_login_inputs") as inputs, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(login.main([]), 0)
        network.assert_not_called()
        inputs.assert_not_called()
        self.assertFalse(json.loads(out.getvalue())["network_attempted"])


if __name__ == "__main__":
    unittest.main()
