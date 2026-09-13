#!/usr/bin/env python3
"""Build and check a public-code-only native API ZIP from this trusted checkout.

No recursive discovery, dependency installation, session input, game assets or
network. Only the reviewed allowlist below is read. The isolated extraction
check uses this interpreter's installed requirements; it verifies the current
OS, not a different deployment platform or live NetEase compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import zipfile


SOURCE_ROOT = Path(__file__).absolute().parents[1]
BUILD = "3848703"
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024

# Explicit current import closure, not a glob of similarly named modules.
# The *_probe and game_config modules supply existing wire codecs, dataclasses
# and LOGIN/Career operations. Their dormant file-analysis entry points are not
# selected by the server; no scripts/inspect-*, desktop provider or loader input
# is shipped. Adding a dependency requires reviewing and updating this tuple.
RUNTIME_MODULES = (
    "game_career_probe.py",
    "game_config_probe.py",
    "game_login_probe.py",
    "game_rank_panel_probe.py",
    "game_social_token_probe.py",
    "game_transport_probe.py",
    "nex_rpc_probe.py",
    "portable_game_runtime.py",
    "portable_social_runtime.py",
    "socialmatrix_capture_probe.py",
    "standalone_career_query.py",
    "standalone_http_api.py",
    "standalone_match_query.py",
    "standalone_native_runtime.py",
    "standalone_native_sections.py",
    "standalone_profile_bundle.py",
    "standalone_profile_probe.py",
    "standalone_rank_query.py",
    "standalone_session_manager.py",
    "standalone_session_store.py",
    "standalone_social_session.py",
    "standalone_social_transport.py",
    "standalone_web_contract.py",
    "web_sdk_auth_probe.py",
    "web_sdk_history_login.py",
)
PUBLIC_PINS = {
    "runtime-inputs/3848703.json": "0111accea3a87a8ed6f45190f41cf5e517b400944c6342bdf689afa34bbb1d81",
    "runtime-inputs/3848703-social.json": "c3d1a44164613862529a63f4598b01af969dcb2b881107ae12c5edbb0eae09e4",
}
SOURCE_FILES = tuple(sorted((*RUNTIME_MODULES, "scripts/serve-native-api.py",
                            "requirements-native-client.txt", "NATIVE_API_SERVER.md", *PUBLIC_PINS)))
ERROR_CODES = frozenset({
    "package_source_unavailable", "package_source_not_regular", "package_source_changed",
    "package_source_bound", "package_public_pin_mismatch", "package_output_exists",
    "package_output_invalid", "package_write_failed", "package_check_failed",
})


class PackageError(ValueError):
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "package_check_failed"
        super().__init__(self.code)


def _is_reparse(info):
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _plain_stat(path, *, directory=False):
    try:
        info = path.lstat()
    except OSError:
        raise PackageError("package_source_unavailable") from None
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or _is_reparse(info):
        raise PackageError("package_source_not_regular")
    return info


def _plain_directories(path):
    # abspath does not resolve links; inspect every traversed component before
    # reading beneath it, including junction/reparse ancestors on Windows.
    for part in reversed((path, *path.parents)):
        _plain_stat(part, directory=True)


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_sources(root):
    _plain_directories(root)
    snapshots, total = {}, 0
    for name in SOURCE_FILES:
        path = root / name
        _plain_directories(path.parent)
        before = _plain_stat(path)
        if not 0 < before.st_size <= MAX_FILE_BYTES:
            raise PackageError("package_source_bound")
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or _is_reparse(opened):
                    raise PackageError("package_source_not_regular")
                if _stamp(before) != _stamp(opened):
                    raise PackageError("package_source_changed")
                raw = stream.read(MAX_FILE_BYTES + 1)
                after = os.fstat(stream.fileno())
            if _stamp(opened) != _stamp(after) or _stamp(after) != _stamp(_plain_stat(path)):
                raise PackageError("package_source_changed")
        except OSError:
            raise PackageError("package_source_unavailable") from None
        total += len(raw)
        if not 0 < len(raw) <= MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise PackageError("package_source_bound")
        if name in PUBLIC_PINS and hashlib.sha256(raw).hexdigest() != PUBLIC_PINS[name]:
            raise PackageError("package_public_pin_mismatch")
        snapshots[name] = raw
    return snapshots


def _archive(snapshots):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in SOURCE_FILES:
            # Fixed metadata excludes checkout paths/timestamps and gives an
            # identical ZIP for identical source bytes on the same zlib build.
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, snapshots[name], compresslevel=9)
    return buffer.getvalue()


_CHECK = r'''
import importlib
import importlib.abc
import json
from pathlib import Path
import runpy
import socket
import sys
import urllib.request

root = Path(sys.argv[1]).resolve()
modules = json.loads(sys.argv[2])
sys.path.insert(0, str(root))
blocked = {'flask', 'cloudscraper', 'webview', 'dpkt', 'pefile', 'capstone',
           'xdis', 'lz4', 'blake3', 'ooz', 'pyooz', 'win32api', 'win32crypt',
           'app', 'career_provider', 'career_probe', 'career_socket_probe',
           'game_career_client', 'web_sdk_history_chain', 'web_sdk_renewal', 'tests'}

class OnlyPortableImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in blocked:
            raise SystemExit(97)

sys.meta_path.insert(0, OnlyPortableImports())
if any(name.split('.')[0] in blocked for name in sys.modules):
    raise SystemExit(97)

def forbidden(*args, **kwargs):
    raise SystemExit(97)

socket.create_connection = forbidden
socket.getaddrinfo = forbidden
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.socket.bind = forbidden
# urllib3 otherwise binds ::1 only to detect local IPv6 support at import.
socket.has_ipv6 = False
urllib.request.OpenerDirector.open = forbidden
for name in modules:
    module = importlib.import_module(name)
    if Path(module.__file__).resolve().parent != root:
        raise SystemExit(97)

import requests
requests.sessions.Session.request = forbidden
import web_sdk_history_login
web_sdk_history_login.login_history_once = forbidden
from standalone_native_runtime import NativeProfileQuery
NativeProfileQuery.__call__ = forbidden
from standalone_session_manager import OwnerSessionManager
OwnerSessionManager.lookup = forbidden
OwnerSessionManager.recover_pending = forbidden

script = root / 'scripts' / 'serve-native-api.py'
sys.argv = [str(script), '--check']
runpy.run_path(str(script), run_name='__main__')
'''


def _verify_extracted(raw):
    try:
        with tempfile.TemporaryDirectory(prefix="native-api-package-check-") as directory:
            root = Path(directory)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if archive.namelist() != list(SOURCE_FILES) or archive.testzip() is not None:
                    raise PackageError("package_check_failed")
                # Only our fixed, traversal-free names are accepted above.
                archive.extractall(root)
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("NETEASE_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
            checked = subprocess.run([sys.executable, "-I", "-B", "-c", _CHECK, str(root),
                                      json.dumps([Path(name).stem for name in RUNTIME_MODULES])],
                cwd=root, env=environment, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
            expected = {"outcome": "native_public_inputs_verified", "reviewed_build": BUILD,
                        "network_attempted": False, "credentials_read": False}
            if checked.returncode != 0 or checked.stderr or json.loads(checked.stdout) != expected:
                raise PackageError("package_check_failed")
    except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile):
        raise PackageError("package_check_failed") from None


def _output_path(value):
    try:
        path = Path(os.path.abspath(os.fspath(value)))
        if path.suffix.lower() != ".zip":
            raise ValueError()
        _plain_directories(path.parent)
        try:
            path.lstat()
        except FileNotFoundError:
            return path
        raise PackageError("package_output_exists")
    except PackageError as error:
        if error.code == "package_output_exists":
            raise
        raise PackageError("package_output_invalid") from None
    except (TypeError, ValueError, OSError):
        raise PackageError("package_output_invalid") from None


def _write_new(path, raw):
    identity = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            identity = os.fstat(stream.fileno())
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise PackageError("package_output_exists") from None
    except OSError:
        # Remove only the newly created incomplete file if its identity still
        # matches. Never remove or replace someone else's existing output.
        try:
            current = path.lstat()
            if (identity is not None and stat.S_ISREG(current.st_mode) and not _is_reparse(current)
                    and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)):
                path.unlink()
        except OSError:
            pass
        raise PackageError("package_write_failed") from None


def package_native_api(output_path, *, source_root=SOURCE_ROOT):
    """Read a trusted source checkout, verify an extracted ZIP, then create it.

    Existing output is never overwritten. source_root is for a trusted checkout
    or synthetic fixture, not a mechanism for validating untrusted Python code.
    The archive contains sources/requirements, not an interpreter or packages.
    """
    output = _output_path(output_path)
    try:
        root = Path(os.path.abspath(os.fspath(source_root)))
    except (TypeError, ValueError, OSError):
        raise PackageError("package_source_unavailable") from None
    raw = _archive(_read_sources(root))
    _verify_extracted(raw)
    _write_new(output, raw)
    return {"outcome": "native_api_package_verified", "reviewed_build": BUILD,
            "file_count": len(SOURCE_FILES), "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw), "check_platform": sys.platform, "network_attempted": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Explicit new .zip path; parent must exist; never overwritten.")
    args = parser.parse_args(argv)
    try:
        report = package_native_api(args.output)
    except PackageError as error:
        print(json.dumps({"outcome": error.code}))
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
