"""Portable ZIP checks using public source fixtures only; no live credentials."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "package-native-api.py"
SPEC = importlib.util.spec_from_file_location("package_native_api", SCRIPT)
pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pack)


class PackNativeApiTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="native-package-test-")
        self.base = Path(self.temporary.name)
        self.source = self.base / "trusted-source"
        self.source.mkdir()
        self.output = self.base / "native-api.zip"
        self.addCleanup(self.temporary.cleanup)
        for name in pack.SOURCE_FILES:
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def build(self):
        return pack.package_native_api(self.output, source_root=self.source)

    def assert_failure(self, code):
        with self.assertRaises(pack.PackageError) as caught:
            self.build()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertFalse(self.output.exists())

    def test_exact_allowlist_and_public_pins_only_with_isolated_extraction_check(self):
        marker = "SYNTHETIC_DO_NOT_PACKAGE_7b8b1edb"
        for name in (".env", "session.sqlite", ".git/config", ".venv/secret.py",
                     "tests/not-runtime.py", "scripts/inspect-script-container.py",
                     "runtime-inputs/3825464.json", "game/script.uep", "capture.pcap"):
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(marker, encoding="utf-8")
        with patch.dict(os.environ, {"NETEASE_API_TOKEN": marker, "NETEASE_SESSION_KEY": marker}):
            report = self.build()
        self.assertEqual(report["outcome"], "native_api_package_verified")
        self.assertEqual(report["reviewed_build"], "3848703")
        self.assertEqual(report["check_platform"], sys.platform)
        self.assertFalse(report["network_attempted"])
        self.assertEqual(report["file_count"], 32)
        self.assertEqual(len(pack.RUNTIME_MODULES), 27)
        raw = self.output.read_bytes()
        self.assertEqual(report["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report["bytes"], len(raw))
        self.assertNotIn(str(self.source), json.dumps(report))
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.namelist(), list(pack.SOURCE_FILES))
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            self.assertIsNone(archive.testzip())
            for item in archive.infolist():
                data = archive.read(item)
                self.assertEqual(data, (self.source / item.filename).read_bytes())
                self.assertNotIn(marker.encode(), data)
                self.assertEqual(item.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertTrue(stat.S_ISREG(item.external_attr >> 16))
                self.assertNotIn("\\", item.filename)
                self.assertNotIn("..", Path(item.filename).parts)
            for name, pin in pack.PUBLIC_PINS.items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), pin)

    def test_same_public_source_bytes_produce_identical_archives(self):
        self.build()
        other = self.base / "second.zip"
        second = pack.package_native_api(other, source_root=self.source)
        self.assertEqual(self.output.read_bytes(), other.read_bytes())
        self.assertEqual(second["sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_existing_output_refused_before_source_reads_and_never_changed(self):
        self.output.write_bytes(b"existing archive bytes")
        with patch.object(pack, "_read_sources") as read:
            with self.assertRaisesRegex(pack.PackageError, "package_output_exists"):
                self.build()
        read.assert_not_called()
        self.assertEqual(self.output.read_bytes(), b"existing archive bytes")

    def test_output_created_during_check_is_not_overwritten(self):
        def concurrent_creation(_raw):
            self.output.write_bytes(b"other caller archive")
        with patch.object(pack, "_verify_extracted", side_effect=concurrent_creation):
            with self.assertRaisesRegex(pack.PackageError, "package_output_exists"):
                self.build()
        self.assertEqual(self.output.read_bytes(), b"other caller archive")

    def test_both_independently_pinned_manifests_reject_modified_bytes(self):
        for name in pack.PUBLIC_PINS:
            path = self.source / name
            original = path.read_bytes()
            try:
                path.write_bytes(original + b"\n")
                with patch.object(pack, "_verify_extracted") as checked:
                    self.assert_failure("package_public_pin_mismatch")
                checked.assert_not_called()
            finally:
                path.write_bytes(original)

    def test_missing_source_or_directory_instead_of_regular_file_is_rejected(self):
        path = self.source / "standalone_http_api.py"
        path.unlink()
        self.assert_failure("package_source_unavailable")
        path.mkdir()
        self.assert_failure("package_source_not_regular")

    def test_symlink_source_file_is_never_followed(self):
        path = self.source / "standalone_http_api.py"
        target = self.base / "linked.py"
        path.replace(target)
        try:
            path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("This OS account cannot create symbolic links")
        self.assert_failure("package_source_not_regular")

    def test_symlink_source_directory_is_never_followed(self):
        path = self.source / "runtime-inputs"
        target = self.base / "linked-inputs"
        path.replace(target)
        try:
            path.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("This OS account cannot create directory symbolic links")
        self.assert_failure("package_source_not_regular")

    def test_special_and_windows_reparse_entries_are_rejected_without_open(self):
        for info in (SimpleNamespace(st_mode=stat.S_IFIFO, st_file_attributes=0),
                     SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400),
                     SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)):
            with patch.object(Path, "lstat", return_value=info):
                with self.assertRaisesRegex(pack.PackageError, "package_source_not_regular"):
                    pack._plain_stat(self.source / "standalone_http_api.py")

    def test_file_and_total_read_bounds_prevent_output(self):
        with patch.object(pack, "MAX_FILE_BYTES", 1):
            self.assert_failure("package_source_bound")
        with patch.object(pack, "MAX_TOTAL_BYTES", 1):
            self.assert_failure("package_source_bound")

    def test_source_change_during_read_is_rejected_before_packaging(self):
        original_stamp = pack._stamp
        calls = [0]
        def changed_stamp(info):
            value = original_stamp(info)
            calls[0] += 1
            # The first two stamps compare lstat versus the newly opened fd;
            # make that comparison differ without changing real user files.
            return value if calls[0] != 2 else (*value[:-1], value[-1] + 1)
        with patch.object(pack, "_stamp", side_effect=changed_stamp), patch.object(pack, "_verify_extracted") as checked:
            self.assert_failure("package_source_changed")
        checked.assert_not_called()

    def test_import_of_desktop_or_research_dependency_fails_extracted_check(self):
        path = self.source / "standalone_web_contract.py"
        original = path.read_bytes()
        for dependency in ("pefile", "flask", "tests"):
            with self.subTest(dependency=dependency):
                path.write_bytes(original + f"\nimport {dependency}\n".encode())
                self.assert_failure("package_check_failed")

    def test_network_attempt_from_extracted_check_is_blocked_and_no_output_written(self):
        path = self.source / "standalone_web_contract.py"
        path.write_bytes(path.read_bytes() + b"\nimport socket\nsocket.getaddrinfo('never-resolve.example',443)\n")
        self.assert_failure("package_check_failed")

    def test_failed_archive_write_cleans_only_its_new_incomplete_output(self):
        with patch.object(pack, "_verify_extracted"), patch.object(pack.os, "fsync", side_effect=OSError("PRIVATE_DISK_ERROR")):
            self.assert_failure("package_write_failed")

    def test_output_must_be_explicit_new_zip_in_existing_directory(self):
        for value in (None, self.base / "no-extension", self.base / "missing" / "output.zip"):
            with self.assertRaisesRegex(pack.PackageError, "package_output_invalid"):
                pack.package_native_api(value, source_root=self.source)

    def test_actual_cli_requires_output_and_reports_fixed_no_overwrite(self):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("NETEASE_")}
        environment["NETEASE_API_TOKEN"] = "SYNTHETIC_NOT_IN_ARCHIVE_dce389d2"
        completed = subprocess.run([sys.executable, "-B", str(SCRIPT), "--output", str(self.output)],
            cwd=self.base, env=environment, input=b"SYNTHETIC_STDIN_IGNORED", capture_output=True, timeout=40)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, b"")
        report = json.loads(completed.stdout)
        self.assertEqual(report["outcome"], "native_api_package_verified")
        prior = self.output.read_bytes()
        repeated = subprocess.run([sys.executable, "-B", str(SCRIPT), "--output", str(self.output)],
            cwd=self.base, env=environment, capture_output=True, timeout=5)
        self.assertEqual(repeated.returncode, 1)
        self.assertEqual(json.loads(repeated.stdout), {"outcome": "package_output_exists"})
        self.assertEqual(repeated.stderr, b"")
        self.assertEqual(self.output.read_bytes(), prior)
        missing = subprocess.run([sys.executable, "-B", str(SCRIPT)],
            cwd=self.base, env=environment, capture_output=True, timeout=5)
        self.assertEqual(missing.returncode, 2)
        self.assertIn(b"--output", missing.stderr)
        self.assertNotIn(b"Traceback", missing.stderr)


if __name__ == "__main__":
    unittest.main()
