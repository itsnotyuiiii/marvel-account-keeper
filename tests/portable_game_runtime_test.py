"""Synthetic public configuration only; no game files, credentials or network."""
import base64
from contextlib import ExitStack
from dataclasses import asdict, replace
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import mock_open, patch

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

import game_career_probe
import game_login_probe
import game_transport_probe
import portable_game_runtime as runtime


class PortableGameRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        cls.der = cls.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.parameter = b"fixture-parameter!"  # 18 public test bytes, never production material.

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(runtime, "PUBLIC_KEY_SHA256", hashlib.sha256(self.der).hexdigest()))
        self.stack.enter_context(patch.object(runtime, "PARAMETER_SHA256", hashlib.sha256(self.parameter).hexdigest()))
        self.manifest = {
            "schema_version": 1,
            "game_id": "x20naxx2gb",
            "provenance": asdict(runtime.SUPPORTED_PROVENANCE),
            "transport": {
                "public_key_der_base64": base64.b64encode(self.der).decode("ascii"),
                "handshake_parameter_base64": base64.b64encode(self.parameter).decode("ascii"),
            },
            "career_methods": {"request": 123, "reply": 456},
            "login_list_url": runtime.LOGIN_LIST_URL,
        }

    def raw(self):
        return json.dumps(self.manifest, separators=(",", ":")).encode("utf-8")

    def parse(self, raw=None, **kwargs):
        if raw is None:
            raw = self.raw()
        kwargs.setdefault("expected_sha256", hashlib.sha256(raw).hexdigest())
        kwargs.setdefault("expected_provenance", runtime.SUPPORTED_PROVENANCE)
        return runtime.parse_runtime_manifest(raw, **kwargs)

    def test_valid_pinned_manifest_projects_existing_types(self):
        value = self.parse()
        self.assertIsInstance(value.configuration, game_transport_probe.PublicConfiguration)
        self.assertEqual(value.configuration.public_key.public_numbers(), self.key.public_key().public_numbers())
        self.assertEqual(value.configuration.parameter, self.parameter)
        self.assertEqual(value.career_methods, game_career_probe.CareerMethods(123, 456))
        self.assertEqual(value.login_list_url, runtime.LOGIN_LIST_URL)
        self.assertEqual(value.client_version, game_login_probe.CLIENT_VERSION)
        self.assertEqual(value.provenance, runtime.SUPPORTED_PROVENANCE)
        self.assertEqual(value.manifest_sha256, hashlib.sha256(self.raw()).hexdigest())
        self.assertNotIn(self.manifest["transport"]["public_key_der_base64"], repr(value))
        self.assertNotIn("fixture-parameter", repr(value))

    def test_current_provenance_is_explicit_and_legacy_defaults_are_unchanged(self):
        self.assertEqual(runtime.SUPPORTED_PROVENANCE.client_version, "3825464")
        self.assertEqual(game_login_probe.CLIENT_VERSION, "3825464")
        self.assertEqual(runtime.SUPPORTED_PROVENANCE.image_sha256, game_transport_probe.IMAGE_SHA256)
        self.manifest["provenance"] = asdict(runtime.CURRENT_PROVENANCE)
        value = self.parse(expected_provenance=runtime.CURRENT_PROVENANCE)
        self.assertEqual(value.client_version, "3848703")
        self.assertEqual(value.provenance, runtime.CURRENT_PROVENANCE)
        self.assertEqual(value.configuration.parameter, self.parameter)
        with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_provenance_mismatch"):
            self.parse(expected_provenance=runtime.SUPPORTED_PROVENANCE)

    def test_reviewed_version_registry_is_explicit_pure_and_has_no_unknown_fallback(self):
        targets = ("builtins.open", "pathlib.Path.open", "socket.create_connection", "requests.get",
                   "requests.post", "game_transport_probe.load_public_configuration",
                   "game_career_probe.load_career_methods", "game_login_probe.advertised_server",
                   "game_login_probe.load_own_login_inputs", "game_login_probe.load_config_index")
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(target, side_effect=AssertionError("unexpected dependency")))
                      for target in targets]
            self.assertEqual(runtime.reviewed_runtime_pin("3825464"),
                             (runtime.SUPPORTED_PROVENANCE, runtime.REVIEWED_MANIFEST_SHA256))
            self.assertEqual(runtime.reviewed_runtime_pin("3848703"),
                             (runtime.CURRENT_PROVENANCE, runtime.CURRENT_MANIFEST_SHA256))
            for version in (None, "", "latest", "3848704", "03848703", "3848703\n",
                            3848703, True, [], {}, b"3848703"):
                with self.subTest(version_type=type(version).__name__), \
                        self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_provenance_unsupported"):
                    runtime.reviewed_runtime_pin(version)
        for guard in guards:
            guard.assert_not_called()

    def test_mixed_build_provenance_is_rejected_before_file_io(self):
        mixed = (
            replace(runtime.CURRENT_PROVENANCE, image_sha256=runtime.SUPPORTED_PROVENANCE.image_sha256),
            replace(runtime.CURRENT_PROVENANCE, archive_sha256=runtime.SUPPORTED_PROVENANCE.archive_sha256),
            replace(runtime.CURRENT_PROVENANCE, client_version=runtime.SUPPORTED_PROVENANCE.client_version),
            replace(runtime.SUPPORTED_PROVENANCE, client_version=runtime.CURRENT_PROVENANCE.client_version),
        )
        with patch.object(Path, "open") as opened:
            for provenance in mixed:
                with self.subTest(version=provenance.client_version), \
                        self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_provenance_unsupported"):
                    runtime.load_runtime_manifest("explicit.json", expected_sha256="0" * 64,
                                                  expected_provenance=provenance)
            opened.assert_not_called()

    def test_pin_and_provenance_are_explicit_required_arguments(self):
        with self.assertRaises(TypeError):
            runtime.parse_runtime_manifest(self.raw())
        with self.assertRaises(TypeError):
            runtime.load_runtime_manifest(Path("fixture.json"))

    def test_invalid_pins_and_missing_provenance_rejected(self):
        for pin in (None, "", "f" * 63, "G" * 64, "F" * 64, b"f" * 64, "f" * 64 + "\n"):
            with self.subTest(pin=pin), self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_manifest_pin_required"):
                self.parse(expected_sha256=pin)
        for provenance in (None, {}, asdict(runtime.SUPPORTED_PROVENANCE),
                           replace(runtime.SUPPORTED_PROVENANCE, client_version="new-patch")):
            with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_provenance_unsupported"):
                self.parse(expected_provenance=provenance)

    def test_exact_bytes_are_pinned_before_json_is_interpreted(self):
        pin = hashlib.sha256(self.raw()).hexdigest()
        for raw in (self.raw() + b"\n", b"credential-like garbage", self.raw().replace(b'"request":123', b'"request":789')):
            with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_manifest_hash_mismatch"):
                self.parse(raw, expected_sha256=pin)

    def test_manifest_size_and_type_are_bounded(self):
        for raw in (b"", b"x" * (runtime.MAX_MANIFEST_BYTES + 1), "{}", bytearray(b"{}"), None):
            with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_manifest_bound"):
                runtime.parse_runtime_manifest(raw, expected_sha256="0" * 64,
                                               expected_provenance=runtime.SUPPORTED_PROVENANCE)

    def test_malformed_json_nonfinite_values_and_duplicate_keys_rejected(self):
        for raw in (b"\xff", b"{", b"\xef\xbb\xbf{}", b"[]", b"null", b'{"x":NaN}',
                    b'{"x":Infinity}', b'{"x":1,"x":2}',
                    self.raw().replace(b'"request":123', b'"request":123,"request":123')):
            with self.subTest(raw=raw[:40]), self.assertRaises(runtime.RuntimeManifestError):
                self.parse(raw)

    def test_schema_version_game_and_missing_fields_rejected(self):
        for value in (True, 0, 2, "1", 1.0, None):
            self.manifest["schema_version"] = value
            with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_manifest_version"):
                self.parse()
        self.manifest["schema_version"] = 1
        self.manifest["game_id"] = "other-game"
        with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_manifest_game"):
            self.parse()
        del self.manifest["game_id"]
        with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_manifest_shape"):
            self.parse()

    def test_unknown_fields_including_credentials_are_rejected_everywhere(self):
        for obj in (self.manifest, self.manifest["provenance"], self.manifest["transport"],
                    self.manifest["career_methods"]):
            for field in ("sessionid", "gas_token", "refresh_token", "path", "extra"):
                obj[field] = "fixture-sensitive-value"
                with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_manifest_shape") as raised:
                    self.parse()
                self.assertNotIn("fixture-sensitive-value", str(raised.exception))
                del obj[field]

    def test_source_build_fingerprints_and_version_must_match_reviewed_provenance(self):
        for name in ("archive_sha256", "image_sha256", "client_version"):
            original = self.manifest["provenance"][name]
            for invalid in ("unreviewed", None, 1):
                self.manifest["provenance"][name] = invalid
                with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_provenance_mismatch"):
                    self.parse()
            self.manifest["provenance"][name] = original

    def test_only_exact_observed_https_login_list_url_allowed(self):
        for url in ("http://x20na.update.easebar.com/server_pc_prod_serverB",
                    runtime.LOGIN_LIST_URL + "?token=fixture", runtime.LOGIN_LIST_URL + "#fragment",
                    runtime.LOGIN_LIST_URL + "/", "https://example.invalid/list",
                    "https://x20na.update.easebar.com.evil.invalid/server_pc_prod_serverB",
                    "https://user:password@x20na.update.easebar.com/server_pc_prod_serverB",
                    "https://x20na.update.easebar.com:443/server_pc_prod_serverB", None):
            self.manifest["login_list_url"] = url
            with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_login_url_unsupported"):
                self.parse()

    def test_public_material_base64_length_and_fingerprints_rejected(self):
        material = self.manifest["transport"]
        for name in material:
            original = material[name]
            for invalid in (None, "", "not-base64!", original + "\n", "A" * 500,
                            base64.b64encode(b"different").decode("ascii")):
                material[name] = invalid
                with self.assertRaises(runtime.RuntimeManifestError):
                    self.parse()
            material[name] = original
        material["handshake_parameter_base64"] = base64.b64encode(b"x" * 17).decode("ascii")
        with patch.object(runtime, "PARAMETER_SHA256", hashlib.sha256(b"x" * 17).hexdigest()):
            with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_public_material_mismatch"):
                self.parse()

    def test_parameter_and_public_key_are_pinned_independently_of_manifest(self):
        for name in ("PUBLIC_KEY_SHA256", "PARAMETER_SHA256"):
            with patch.object(runtime, name, "0" * 64):
                with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_public_material_mismatch"):
                    self.parse()

    def test_only_canonical_rsa_1024_public_der_is_accepted(self):
        ec_der = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        pkcs1_der = self.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.PKCS1)
        wrong_size_der = rsa.generate_private_key(public_exponent=65537, key_size=1536).public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        for der in (b"not a DER key", ec_der, pkcs1_der, wrong_size_der):
            self.manifest["transport"]["public_key_der_base64"] = base64.b64encode(der).decode("ascii")
            with patch.object(runtime, "PUBLIC_KEY_SHA256", hashlib.sha256(der).hexdigest()):
                with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_public_key_shape"):
                    self.parse()

    def test_unsupported_key_algorithm_has_a_fixed_diagnostic(self):
        with patch.object(runtime.serialization, "load_der_public_key",
                          side_effect=UnsupportedAlgorithm("untrusted-details")):
            with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_public_key_shape") as raised:
                self.parse()
        self.assertNotIn("untrusted-details", str(raised.exception))

    def test_career_method_indices_are_distinct_positive_int31(self):
        for name in ("request", "reply"):
            original = self.manifest["career_methods"][name]
            for invalid in (True, 0, -1, 2**31, 1.0, "123", None):
                self.manifest["career_methods"][name] = invalid
                with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_career_methods_shape"):
                    self.parse()
            self.manifest["career_methods"][name] = original
        self.manifest["career_methods"]["reply"] = self.manifest["career_methods"]["request"]
        with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_career_methods_shape"):
            self.parse()

    def test_parse_and_server_projection_never_consult_files_network_or_installed_loaders(self):
        targets = ("builtins.open", "pathlib.Path.open", "socket.create_connection", "requests.get",
                   "game_transport_probe.load_public_configuration", "game_career_probe.load_career_methods",
                   "game_login_probe.advertised_server", "game_login_probe.load_own_login_inputs",
                   "game_login_probe.load_config_index", "game_login_probe.read_config")
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch(target, side_effect=AssertionError("unexpected dependency"))) for target in targets]
            self.parse()
            runtime.project_login_server(b"prod 34.8.137.117 10001 13001 1 1788426300 10001-10010")
        for mocked in mocks:
            mocked.assert_not_called()

    def test_explicit_file_loader_reads_once_with_bound_and_no_fallback(self):
        source = mock_open(read_data=self.raw())
        pin = hashlib.sha256(self.raw()).hexdigest()
        with patch.object(Path, "open", source):
            value = runtime.load_runtime_manifest(Path("explicit-public-config.json"), expected_sha256=pin,
                                                  expected_provenance=runtime.SUPPORTED_PROVENANCE)
        source.assert_called_once_with("rb")
        source().read.assert_called_once_with(runtime.MAX_MANIFEST_BYTES + 1)
        self.assertEqual(value.manifest_sha256, pin)
        self.assertTrue(source().__exit__.called)

    def test_file_failure_paths_are_fixed_and_invalid_pin_is_checked_before_open(self):
        kwargs = {"expected_sha256": "0" * 64, "expected_provenance": runtime.SUPPORTED_PROVENANCE}
        for path in (None, "", b"not-a-path", 123):
            with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_manifest_path_required"):
                runtime.load_runtime_manifest(path, **kwargs)
        with patch.object(Path, "open", side_effect=OSError("private path and fixture-token")) as opened:
            with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_manifest_file_unavailable") as raised:
                runtime.load_runtime_manifest(Path("explicit.json"), **kwargs)
            self.assertNotIn("fixture-token", str(raised.exception))
            opened.assert_called_once()
        with patch.object(Path, "open") as opened:
            with self.assertRaises(runtime.RuntimeManifestError):
                runtime.load_runtime_manifest("explicit.json", expected_sha256=None,
                                              expected_provenance=runtime.SUPPORTED_PROVENANCE)
            opened.assert_not_called()

    def test_file_loader_rejects_oversized_content(self):
        with patch.object(Path, "open", return_value=io.BytesIO(b"x" * (runtime.MAX_MANIFEST_BYTES + 1))):
            with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_manifest_bound"):
                runtime.load_runtime_manifest("explicit.json", expected_sha256="0" * 64,
                                              expected_provenance=runtime.SUPPORTED_PROVENANCE)

    def test_supplied_server_list_reuses_existing_validation_and_preserves_exclusions(self):
        raw = b"prod 34.8.137.117 10001 13001 1 1788426300 10001-10010:10002"
        self.assertEqual(runtime.project_login_server(raw), game_login_probe.parse_server_list(raw))
        self.assertNotIn(10002, runtime.project_login_server(raw).ports)
        for raw in (b"", b"x" * 4097, b"prod 127.0.0.1 10001 13001 1 1788426300 10001-10010",
                    b"prod 34.8.137.117 10001 13001 0 1788426300 10001-10010",
                    b"fixture-sensitive-value", None):
            with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_server_list_invalid") as raised:
                runtime.project_login_server(raw)
            self.assertNotIn("fixture-sensitive-value", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
