"""Synthetic SDK-log tests; never open installed game files or real logs."""

import base64
from datetime import date
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


spec = importlib.util.spec_from_file_location("sdk_log", Path(__file__).parents[1] / "scripts" / "inspect-sdk-log.py")
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)
TEST_PARAMETER = bytes(range(32))  # Synthetic fixture, not the installed value.
TEST_DAY = date(2026, 9, 5)


def encrypt_record(plaintext):
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(TEST_PARAMETER), modes.ECB()).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()) + b"\r\n"


def fake_jwt(claims):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return encode({"alg": "HS256"}) + "." + encode(claims) + ".c3ludGhldGlj"


class SdkLogTest(unittest.TestCase):
    def report(self, *lines):
        source = io.BytesIO(b"".join(encrypt_record(line) for line in lines))
        return sdk.summarize_stream(source, TEST_PARAMETER, TEST_DAY, now=1000)

    def test_roundtrip_uses_padding_and_strict_utf8(self):
        text = "[2026-09-05 12:15:30] synthetic data"
        self.assertEqual(sdk.decode_record(encrypt_record(text), TEST_PARAMETER), text)
        with self.assertRaises(sdk.InspectionError):
            sdk.decode_record(encrypt_record(b"\xff"), TEST_PARAMETER)

    def test_rejects_invalid_encoding_alignment_padding_and_size(self):
        for encoded in (b"", b"?secret", base64.b64encode(b"short"), base64.b64encode(bytes(16)), b"A" * (sdk.MAX_ENCODED_RECORD_BYTES + 1)):
            with self.subTest(length=len(encoded)), self.assertRaises(sdk.InspectionError) as raised:
                sdk.decode_record(encoded, TEST_PARAMETER)
            self.assertNotIn("secret", str(raised.exception))

    def test_bounds_stream_bytes_records_and_line_length(self):
        record = encrypt_record("2026-09-05 00:00:00 test")
        for bound, value in (("MAX_LOG_BYTES", 1), ("MAX_RECORDS", 0), ("MAX_ENCODED_RECORD_BYTES", 1)):
            with self.subTest(bound=bound), patch.object(sdk, bound, value), self.assertRaises(sdk.InspectionError):
                sdk.summarize_stream(io.BytesIO(record), TEST_PARAMETER, TEST_DAY)

    def test_date_selection_and_label_counts(self):
        report = self.report("2026-09-04 12:00:00 SocialMatrix", "2026-09-05 12:00:00 sdkuid sdkuid uni_sauth", "not timestamped SocialMatrix", "2026-99-01 00:00:00 SocialMatrix")
        self.assertEqual(report["decoded_records"], 4)
        self.assertEqual(report["timestamped_records"], 2)
        self.assertEqual(report["selected_date_records"], 1)
        self.assertEqual(report["label_record_counts"]["sdkuid"], 1)
        self.assertEqual(report["label_record_counts"]["SocialMatrix"], 0)

    def test_counts_invalid_records_without_outputting_them(self):
        report = sdk.summarize_stream(io.BytesIO(b"private-credential-value\n"), TEST_PARAMETER, TEST_DAY)
        self.assertEqual(report["invalid_records"], 1)
        self.assertNotIn("private-credential-value", json.dumps(report))

    def test_jwt_occurrences_deduplication_and_shape_are_not_verification(self):
        token = fake_jwt({"exp": 2000, "iat": 100, "sub": "sdk-user-secret", "gameid": "test"})
        report = self.report("2026-09-05 12:00:00 " + token, "2026-09-05 12:00:01 " + token)
        self.assertEqual(report["jwt_occurrences"], 2)
        self.assertEqual(report["distinct_jwts"], 1)
        self.assertEqual(report["sdk_shaped_jwts"], 1)
        self.assertEqual(report["career_shaped_jwts"], 0)
        self.assertEqual(report["future_exp_claim_jwts"], 1)
        self.assertFalse(report["jwt_signatures_verified"])
        self.assertFalse(report["identity_verified"])
        self.assertNotIn(token, json.dumps(report))
        self.assertNotIn("sdk-user-secret", json.dumps(report))

    def test_career_shaped_token_does_not_establish_access(self):
        token = fake_jwt({"aid": "owned-synthetic-id", "websocket": {"addr": "synthetic-host"}, "exp": 999})
        report = self.report("2026-09-05 12:00:00 " + token)
        self.assertEqual(report["career_shaped_jwts"], 1)
        self.assertEqual(report["future_exp_claim_jwts"], 0)
        self.assertFalse(report["career_access_verified"])
        self.assertNotIn("synthetic-host", json.dumps(report))

    def test_unknown_or_malformed_token_claims_not_echoed(self):
        for claims in ([], "not-an-object", {"exp": True}, {"exp": float("inf")}, {"exp": 10 ** 400}):
            report = self.report("2026-09-05 12:00:00 " + fake_jwt(claims))
            self.assertEqual(report["future_exp_claim_jwts"], 0)

    def test_only_expected_auth_endpoint_counts_as_success(self):
        def record(url):
            return "2026-09-05 12:00:00 " + json.dumps({"raw_info": {"url": url, "status_code": 200}})
        report = self.report(record(sdk.SDK_AUTH_URL + "?token=private-query"), record(sdk.SDK_AUTH_URL + "/other"))
        self.assertEqual(report["sdk_auth_http_200_records"], 1)
        self.assertNotIn("private-query", json.dumps(report))

    def test_sdk_login_shapes_output_no_credentials_or_personal_values(self):
        bundle = {key: "do-not-print" for key in ("sdkuid", "gas_token", "sessionid", "gameid")}
        oauth = {key: "do-not-print" for key in ("access_token", "refresh_token", "oauth2", "chl_sdk_json")}
        report = self.report("2026-09-05 12:00:00 " + json.dumps(bundle), "2026-09-05 12:00:00 " + json.dumps(oauth))
        self.assertTrue(report["sdk_login_bundle_observed"])
        self.assertTrue(report["sdk_oauth_shape_observed"])
        self.assertNotIn("do-not-print", json.dumps(report))

    def test_metadata_default_never_decodes_or_opens_log(self):
        with patch.object(sdk, "default_log_path") as path, patch.object(sdk, "load_log_parameter") as load, patch.object(sdk, "summarize_stream") as inspect, patch("sys.stdout", new_callable=io.StringIO) as out:
            path.return_value.is_file.return_value = True
            self.assertEqual(sdk.main([]), 0)
            load.assert_not_called()
            inspect.assert_not_called()
            path.return_value.open.assert_not_called()
            self.assertEqual(json.loads(out.getvalue())["outcome"], "metadata_only")

    def test_wrong_dll_build_rejected_before_pe_parsing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.dll"
            path.write_bytes(b"synthetic-not-the-installed-dll")
            with self.assertRaisesRegex(sdk.InspectionError, "unsupported_sdk_build"):
                sdk.load_log_parameter(path)

    def test_game_specific_directory_mapping(self):
        with patch.dict("os.environ", {"LOCALAPPDATA": r"C:\Synthetic"}):
            path = sdk.default_log_path()
        self.assertEqual(path.parent.name, "3109201CA1FD65B3A6AC32FAB1ABB35D")
        self.assertEqual(path.name, "NtUniSdk.log")


if __name__ == "__main__":
    unittest.main()
