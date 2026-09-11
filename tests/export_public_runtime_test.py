"""Synthetic-only export tests: no installed files, credentials or network."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import mock_open, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export-public-runtime.py"
spec = importlib.util.spec_from_file_location("export_public_runtime", SCRIPT)
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


class ExportPublicRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key()
        cls.der = cls.key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.parameter = b"fixture-parameter!"

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.sources = dict(archive="explicit-script.uep", image="explicit-image.exe",
                            config_pak="explicit-config.pak", version_json="explicit-version.json")
        self.version = json.dumps({"version": "1.1.3825464", "changelist": 3825464}).encode()
        self.shipping = b"synthetic-public-config"
        self.opened = self.stack.enter_context(patch.object(Path, "open", mock_open(read_data=self.version)))
        self.public = self.stack.enter_context(patch.object(
            export.transport, "load_public_configuration",
            return_value=export.transport.PublicConfiguration(self.key, self.parameter)))
        self.methods = self.stack.enter_context(patch.object(
            export.career, "load_career_methods", return_value=export.career.CareerMethods(123, 456)))
        self.index = self.stack.enter_context(patch.object(export.config, "load_config_index", return_value=object()))
        self.read = self.stack.enter_context(patch.object(export.config, "read_config", return_value=self.shipping))
        self.url = self.stack.enter_context(patch.object(
            export.config, "shipping_login_url", return_value=export.runtime.LOGIN_LIST_URL))
        for obj, name, value in (
                (export, "SHIPPING_CONFIG_SHA256", hashlib.sha256(self.shipping).hexdigest()),
                (export.runtime, "PUBLIC_KEY_SHA256", hashlib.sha256(self.der).hexdigest()),
                (export.runtime, "PARAMETER_SHA256", hashlib.sha256(self.parameter).hexdigest())):
            self.stack.enter_context(patch.object(obj, name, value))

    def build(self, **updates):
        return export.build_manifest(**(self.sources | updates))

    def argv(self):
        return [part for name, value in self.sources.items()
                for part in ("--" + name.replace("_", "-"), value)]

    def test_deterministic_canonical_bytes_and_parser_round_trip(self):
        first, second = self.build(), self.build()
        self.assertEqual(first, second)
        self.assertEqual(first, (json.dumps(json.loads(first), sort_keys=True, indent=2) + "\n").encode("ascii"))
        self.assertNotIn(b"explicit-", first)
        value = export.runtime.parse_runtime_manifest(
            first, expected_sha256=hashlib.sha256(first).hexdigest(),
            expected_provenance=export.runtime.SUPPORTED_PROVENANCE)
        self.assertEqual(value.client_version, "3825464")
        self.assertEqual(value.career_methods, export.career.CareerMethods(123, 456))

    def test_all_paths_explicit_and_forwarded_without_fallback(self):
        self.build()
        self.public.assert_called_once_with(archive=Path(self.sources["archive"]), image=Path(self.sources["image"]))
        self.methods.assert_called_once_with(archive=Path(self.sources["archive"]))
        self.index.assert_called_once_with(pak=Path(self.sources["config_pak"]))
        self.read.assert_called_once_with(self.index.return_value, export.config.SHIPPING_CONFIG)
        self.opened.assert_called_once_with("rb")
        self.opened().read.assert_called_once_with(export.MAX_VERSION_BYTES + 1)
        for name in self.sources:
            for value in (None, "", b"path", 3, "bad\0path"):
                with self.assertRaisesRegex(export.RuntimeExportError, "runtime_export_explicit_path_required"):
                    self.build(**{name: value})
        with self.assertRaises(TypeError):
            export.build_manifest()

    def test_version_missing_changed_wrong_type_duplicate_and_oversize_rejected_first(self):
        invalid = [b"", b"{", b"[]", b"x" * (export.MAX_VERSION_BYTES + 1),
                   b'{"version":"1.1.3825464","changelist":3825464,"changelist":3825464}',
                   b'{"version":"1.1.3825464","changelist":NaN}']
        for item in ({}, {"version": "new", "changelist": 3825464},
                     {"version": "1.1.3825464", "changelist": "3825464"},
                     {"version": "1.1.3825464", "changelist": True}):
            invalid.append(json.dumps(item).encode())
        for raw in invalid:
            with patch.object(Path, "open", mock_open(read_data=raw)):
                with self.assertRaisesRegex(export.RuntimeExportError, "runtime_export_version_unverified"):
                    self.build()
        self.public.assert_not_called()
        self.methods.assert_not_called()
        self.index.assert_not_called()

    def test_version_proof_must_agree_with_supported_client_version(self):
        provenance = replace(export.runtime.SUPPORTED_PROVENANCE, client_version="new-build")
        with patch.object(export.runtime, "SUPPORTED_PROVENANCE", provenance):
            with self.assertRaisesRegex(export.RuntimeExportError, "runtime_export_version_unverified"):
                self.build()

    def test_large_public_distribution_inventory_is_not_exported(self):
        expected = self.build()
        metadata = {
            "version": "1.1.3825464", "changelist": 3825464,
            "NonUFSFiles": ["unselected-public-distribution-entry-" + str(i) for i in range(3000)],
        }
        raw = json.dumps(metadata).encode()
        self.assertGreater(len(raw), 4096)
        self.assertLess(len(raw), export.MAX_VERSION_BYTES)
        with patch.object(Path, "open", mock_open(read_data=raw)) as source:
            actual = self.build()
        source().read.assert_called_once_with(export.MAX_VERSION_BYTES + 1)
        self.assertEqual(actual, expected)
        self.assertNotIn(b"NonUFSFiles", actual)
        self.assertNotIn(b"unselected-public-distribution", actual)

    def test_source_failures_are_fixed_redacted_and_do_not_emit_candidate(self):
        cases = ((self.public, "build"), (self.methods, "build"),
                 (self.index, "shipping"), (self.read, "shipping"), (self.url, "shipping"))
        for mocked, stage in cases:
            mocked.side_effect = OSError("private source path and fixture-token")
            out, err = io.BytesIO(), io.StringIO()
            with self.subTest(stage=stage):
                self.assertEqual(export.main(self.argv(), stdout=out, stderr=err), 1)
                self.assertEqual(out.getvalue(), b"")
                self.assertEqual(json.loads(err.getvalue()), {"outcome": "runtime_export_" + stage + "_unverified"})
                self.assertNotIn("fixture-token", err.getvalue())
            mocked.side_effect = None

    def test_version_read_exception_redacted(self):
        with patch.object(Path, "open", side_effect=OSError("fixture-token")):
            with self.assertRaisesRegex(export.RuntimeExportError, "runtime_export_version_unverified") as raised:
                self.build()
        self.assertNotIn("fixture-token", str(raised.exception))

    def test_changed_shipping_hash_and_url_fail_closed(self):
        with patch.object(export, "SHIPPING_CONFIG_SHA256", "0" * 64):
            with self.assertRaisesRegex(export.RuntimeExportError, "runtime_export_shipping_unverified"):
                self.build()
        self.url.return_value = "https://other.invalid/?token=fixture"
        with self.assertRaisesRegex(export.RuntimeExportError, "runtime_export_shipping_unverified"):
            self.build()

    def test_output_recheck_rejects_changed_material_pins_and_invalid_methods(self):
        for pin in ("PUBLIC_KEY_SHA256", "PARAMETER_SHA256"):
            with patch.object(export.runtime, pin, "0" * 64):
                with self.assertRaisesRegex(export.RuntimeExportError, "runtime_export_output_unverified"):
                    self.build()
        for methods in (export.career.CareerMethods(1, 1), export.career.CareerMethods(True, 2),
                        export.career.CareerMethods(0, 1)):
            self.methods.return_value = methods
            with self.assertRaisesRegex(export.RuntimeExportError, "runtime_export_output_unverified"):
                self.build()

    def test_cli_exact_binary_stdout_and_separate_digest_metadata(self):
        expected = self.build()
        out, err = io.BytesIO(), io.StringIO()
        self.assertEqual(export.main(self.argv(), stdout=out, stderr=err), 0)
        self.assertEqual(out.getvalue(), expected)
        report = json.loads(err.getvalue())
        self.assertEqual(report["sha256"], hashlib.sha256(expected).hexdigest())
        self.assertEqual(report["bytes"], len(expected))
        self.assertEqual(report["client_version"], "3825464")
        self.assertFalse(report["live_compatibility_verified"])
        self.assertEqual(report["outcome"], "review_required_public_runtime_candidate")
        self.assertNotIn("base64", err.getvalue())

    def test_cli_rejects_unknown_secret_output_and_abbreviated_options_without_echo(self):
        for args in ([], self.argv() + ["--sessionid", "fixture-token"],
                     self.argv() + ["--output", "fixture-token"],
                     ["--arch", "fixture-token"] + self.argv()[2:]):
            out, err = io.BytesIO(), io.StringIO()
            self.assertEqual(export.main(args, stdout=out, stderr=err), 1)
            self.assertEqual(out.getvalue(), b"")
            self.assertEqual(json.loads(err.getvalue()), {"outcome": "runtime_export_arguments_invalid"})
        self.public.assert_not_called()
        self.opened.assert_not_called()

    def test_loader_diagnostics_suppressed_without_leaking_output(self):
        original = self.public.return_value
        def noisy_loader(**kwargs):
            print("fixture-private-diagnostic")
            print("fixture-private-diagnostic", file=export.sys.stderr)
            return original
        self.public.side_effect = noisy_loader
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.build()
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_no_credentials_network_process_or_output_file_calls(self):
        targets = ("socket.create_connection", "requests.get", "requests.post", "subprocess.run",
                   "game_login_probe.load_own_login_inputs", "game_login_probe.advertised_server",
                   "pathlib.Path.write_bytes", "pathlib.Path.write_text", "builtins.open")
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(target, side_effect=AssertionError("unexpected dependency")))
                      for target in targets]
            self.build()
        for guard in guards:
            guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
