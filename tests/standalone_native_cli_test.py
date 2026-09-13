"""Real CLI subprocesses, synthetic stdin/key and no networking of any kind."""
import base64
from contextlib import ExitStack, contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from standalone_session_store import EncryptedSqliteSessionStore, SessionStoreError
from tests.standalone_session_manager_test import bundle, OWNER


ROOT = Path(__file__).resolve().parents[1]
KEY = b"c" * 32
KEY_TEXT = base64.urlsafe_b64encode(KEY).decode("ascii")
PRIVATE_MARKERS = (b"fixture-prior-access", b"fixture-prior-refresh", b"fixture-saved-device",
                   b"fixture-mpay", b"PRIVATE_CLI_INPUT", KEY, KEY_TEXT.encode("ascii"))

# Execute the actual CLI __main__ with normal argv/environment/stdin, while a
# process-local guard makes accidental socket/SDK traffic a distinct hard fail.
# SystemExit is intentional: CLI's fixed Exception handling must not hide a
# forbidden lookup/recovery/network call behind an expected invalid-input code.
GUARDED_CLI = r'''
import runpy
import socket
import sys
from pathlib import Path
import urllib.request

root = Path(sys.argv[1])
sys.path.insert(0, str(root))

def forbidden(*args, **kwargs):
    raise SystemExit(97)

socket.create_connection = forbidden
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.socket.bind = forbidden
# urllib3 otherwise binds ::1 at import only to detect local IPv6 support.
# Disable that capability probe in this no-socket synthetic subprocess.
socket.has_ipv6 = False
urllib.request.OpenerDirector.open = forbidden
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
sys.argv = [str(script), *sys.argv[2:]]
runpy.run_path(str(script), run_name='__main__')
'''


class StandaloneNativeCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "synthetic-session.sqlite3"
        self.addCleanup(self.temp.cleanup)

    def cli(self, *args, stdin=b"", key=KEY_TEXT, token=None):
        environment = os.environ.copy()
        # Never inherit an operator's real native-service credentials.
        environment.pop("NETEASE_SESSION_KEY", None)
        environment.pop("NETEASE_API_TOKEN", None)
        if key is not None:
            environment["NETEASE_SESSION_KEY"] = key
        if token is not None:
            environment["NETEASE_API_TOKEN"] = token
        completed = subprocess.run([sys.executable, "-B", "-c", GUARDED_CLI, str(ROOT), *map(str, args)],
            input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.root, env=environment, timeout=20)
        self.assertNotEqual(completed.returncode, 97, "Forbidden network/lookup/recovery operation attempted")
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, completed.stdout)
            self.assertNotIn(marker, completed.stderr)
        self.assertNotIn(b"Traceback", completed.stderr)
        return completed

    def initialize(self, data=None, *, raw=None, key=KEY_TEXT, token=None):
        stdin = json.dumps(bundle() if data is None else data).encode("utf-8") if raw is None else raw
        return self.cli("--database", self.database, "--initialize-from-stdin", stdin=stdin, key=key, token=token)

    def report(self, completed, expected, returncode=1):
        self.assertEqual(completed.returncode, returncode)
        self.assertEqual(completed.stderr, b"")
        self.assertEqual(json.loads(completed.stdout), expected)

    def record(self, key=KEY):
        return EncryptedSqliteSessionStore(self.database, key).load(OWNER)

    def assert_no_plaintext_on_disk(self):
        for path in self.root.iterdir():
            if path.is_file():
                raw = path.read_bytes()
                for marker in PRIVATE_MARKERS:
                    self.assertNotIn(marker, raw)

    def test_check_only_loads_public_inputs_without_key_database_or_network(self):
        for key in (None, "PRIVATE_CLI_INPUT"):
            completed = self.cli("--check", key=key, token="PRIVATE_CLI_INPUT", stdin=b"PRIVATE_CLI_INPUT")
            self.report(completed, {"outcome": "native_public_inputs_verified", "reviewed_build": "3848703",
                                    "network_attempted": False, "credentials_read": False}, returncode=0)
            self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_or_invalid_key_fails_before_database_or_lock_creation(self):
        for key in (None, "", "PRIVATE_CLI_INPUT", base64.b64encode(b"too-short").decode("ascii")):
            completed = self.initialize(key=key)
            self.report(completed, {"outcome": "native_service_configuration_invalid"})
            self.assertEqual(list(self.root.iterdir()), [])

    def test_stdin_initialization_persists_only_encrypted_required_context(self):
        self.report(self.initialize(), {"outcome": "session_bootstrapped"}, returncode=0)
        self.assertTrue(self.database.is_file())
        revision, record = self.record()
        self.assertEqual(revision, 1)
        self.assertEqual(record["owner_uid"], OWNER)
        self.assertEqual(record["state"], "ready")
        self.assertIs(record["owner_verified"], False)
        self.assertIsNone(record["candidate"])
        self.assertEqual(record["saved_account"]["refreshToken"], bundle()["saved_account"]["refreshToken"])
        self.assertEqual(record["mpay_context"], bundle()["mpay_context"])
        for discarded in ("fixture-prior-access", "fixture-prior-device", "fixture-prior-serial", "sessionid"):
            self.assertNotIn(discarded, json.dumps(record))
        self.assert_no_plaintext_on_disk()
        # Test-key authentication is exercised across a fresh store instance.
        with self.assertRaises(SessionStoreError):
            self.record(key=b"x" * 32)

    def test_duplicate_initialization_does_not_replace_encrypted_record(self):
        self.report(self.initialize(), {"outcome": "session_bootstrapped"}, returncode=0)
        before, disk_before = self.record(), self.database.read_bytes()
        replacement = bundle()
        replacement["saved_account"]["refreshToken"] = "PRIVATE_CLI_INPUT"
        self.report(self.initialize(replacement), {"outcome": "session_already_configured"})
        self.assertEqual(self.record(), before)
        self.assertEqual(self.database.read_bytes(), disk_before)
        self.assert_no_plaintext_on_disk()

    def test_malformed_duplicate_nonutf8_and_oversized_stdin_are_fixed_no_record(self):
        duplicate = (b'{"selected_uid":1370598422,"selected_uid":1370598422,'
                     b'"private":"PRIVATE_CLI_INPUT"}')
        nested = json.dumps(bundle()).replace('"refreshToken":', '"refreshToken":"PRIVATE_CLI_INPUT","refreshToken":').encode()
        cases = (b"", b'{"private":"PRIVATE_CLI_INPUT"', duplicate, nested,
                 b'\xffPRIVATE_CLI_INPUT', b"PRIVATE_CLI_INPUT" + b"x" * (256 * 1024))
        for raw in cases:
            completed = self.initialize(raw=raw)
            self.report(completed, {"outcome": "native_service_unavailable"})
            self.assertIsNone(self.record())
            self.assert_no_plaintext_on_disk()

    def test_wrong_owner_or_shape_never_bootstraps_or_queries(self):
        for data in ({}, [], dict(bundle(), selected_uid=123), dict(bundle(), private="PRIVATE_CLI_INPUT")):
            self.report(self.initialize(data), {"outcome": "session_context_invalid"})
            self.assertIsNone(self.record())

    def test_conflicting_flags_and_bad_ports_fail_before_database_or_stdin(self):
        cases = (("--check", "--database", self.database), ("--check", "--initialize-from-stdin"),
                 ("--check", "--recover-pending"), ("--initialize-from-stdin",),
                 ("--database", self.database, "--initialize-from-stdin", "--recover-pending"),
                 ("--database", self.database, "--initialize-from-stdin", "--port", "0"),
                 ("--database", self.database, "--initialize-from-stdin", "--port", "65536"))
        for args in cases:
            completed = self.cli(*args, stdin=b"PRIVATE_CLI_INPUT")
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, b"")
            self.assertIn(b"error:", completed.stderr)
            self.assertEqual(list(self.root.iterdir()), [])

    def test_bind_host_is_an_exact_serving_only_option(self):
        cases = (("--check", "--bind-host", "0.0.0.0"),
                 ("--check", "--bind-host", "127.0.0.1"),
                 ("--database", self.database, "--initialize-from-stdin", "--bind-host", "0.0.0.0"),
                 ("--database", self.database, "--recover-pending", "--bind-host", "127.0.0.1"))
        cases += tuple(("--database", self.database, "--bind-host", host)
                       for host in ("localhost", "::", "192.0.2.1", "https://example.test", ""))
        for args in cases:
            with self.subTest(args=args):
                completed = self.cli(*args, stdin=b"PRIVATE_CLI_INPUT", token="t" * 32)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, b"")
                self.assertIn(b"error:", completed.stderr)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_serve_configuration_requires_valid_private_bearer_before_database_or_listener(self):
        for token in (None, "short", "PRIVATE_CLI_INPUT\n" + "x" * 32):
            completed = self.cli("--database", self.database, token=token)
            self.report(completed, {"outcome": "native_service_configuration_invalid"})
            self.assertEqual(list(self.root.iterdir()), [])

    def test_nonloopback_opt_in_still_requires_key_and_private_bearer(self):
        for key, token in ((None, "t" * 32), (KEY_TEXT, None), (KEY_TEXT, "short")):
            completed = self.cli("--database", self.database, "--bind-host", "0.0.0.0", key=key, token=token)
            self.report(completed, {"outcome": "native_service_configuration_invalid"})
            self.assertEqual(list(self.root.iterdir()), [])

    def test_initialization_releases_exclusive_lock_on_exit(self):
        self.report(self.initialize(), {"outcome": "session_bootstrapped"}, returncode=0)
        store = EncryptedSqliteSessionStore(self.database, KEY)
        with store.exclusive_owner():
            completed = self.initialize()
            self.report(completed, {"outcome": "native_service_unavailable"})
        self.report(self.initialize(), {"outcome": "session_already_configured"})


class NativeCliTerminationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("synthetic_native_cli", ROOT / "scripts" / "serve-native-api.py")
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def signals(self):
        kinds = (self.cli.signal.SIGINT, self.cli.signal.SIGTERM)
        previous = {kind: object() for kind in kinds}
        installed, events = previous.copy(), []
        def register(kind, handler):
            old = installed[kind]
            installed[kind] = handler
            events.append("restore" if handler is previous[kind] else "install")
            return old
        return kinds, previous, installed, events, register

    def test_repeated_termination_is_nonthrowing_and_handlers_survive_drain(self):
        kinds, previous, installed, events, register = self.signals()
        with patch.object(self.cli.signal, "signal", side_effect=register):
            with self.cli._termination_requested() as stopped, ExitStack() as stack:
                self.assertFalse(stopped.is_set())
                def drain():
                    events.append("drain")
                    for kind in kinds:
                        self.assertIsNot(installed[kind], previous[kind])
                        installed[kind](kind, None)
                        installed[kind](kind, None)
                    self.assertTrue(stopped.is_set())
                stack.callback(drain)
                for kind in kinds:
                    installed[kind](kind, None)
                    installed[kind](kind, None)
                self.assertTrue(stopped.is_set())
        self.assertEqual(installed, previous)
        self.assertEqual(events, ["install", "install", "drain", "restore", "restore"])

    def exercise_serve_binding(self, host_args, expected_host, *, port=12345):
        kinds, previous, installed, events, register = self.signals()
        port_args = [] if port is None else ["--port", str(port)]
        expected_port = 8080 if port is None else port
        class Store:
            @contextmanager
            def exclusive_owner(inner):
                events.append("lock")
                try:
                    yield inner
                finally:
                    events.append("unlock")
        class Service:
            def close(inner):
                events.append("drain")
                for kind in kinds:
                    self.assertIsNot(installed[kind], previous[kind])
                    installed[kind](kind, None)
                    installed[kind](kind, None)
        class Server:
            server_port = expected_port
            def handle_request(inner):
                events.append("request")
                installed[kinds[1]](kinds[1], None)
            def server_close(inner):
                events.append("server_close")
        output = io.StringIO()
        with patch.object(self.cli.signal, "signal", side_effect=register), \
             patch.object(self.cli, "NativeProfileQuery", return_value=object()), \
             patch.object(self.cli, "EncryptedSqliteSessionStore", return_value=Store()), \
             patch("standalone_session_manager.OwnerSessionManager", return_value=object()), \
             patch("standalone_http_api.ProfileLookupService", return_value=Service()), \
             patch("standalone_http_api.make_http_server", return_value=Server()) as make_server, \
             patch.dict(os.environ, {"NETEASE_SESSION_KEY": KEY_TEXT, "NETEASE_API_TOKEN": "t" * 32,
                                     "HOST": "0.0.0.0", "PORT": "9999"}), \
             patch("sys.stdout", output):
            exit_code = self.cli.main(["--database", "synthetic-not-created.sqlite3", *port_args, *host_args])
        self.assertEqual(exit_code, 0)
        self.assertEqual(events, ["install", "install", "lock", "request", "server_close", "drain",
                                  "unlock", "restore", "restore"])
        self.assertEqual(installed, previous)
        self.assertEqual(make_server.call_count, 1)
        self.assertEqual(make_server.call_args.kwargs,
                         {"bearer_token": "t" * 32, "host": expected_host, "port": expected_port})
        self.assertEqual(json.loads(output.getvalue()), {"outcome": "native_http_listening", "host": expected_host,
                                                       "port": expected_port, "tls_proxy_required": True})

    def test_main_closes_listener_then_drains_worker_before_unlock_and_signal_restore(self):
        self.exercise_serve_binding([], "127.0.0.1")

    def test_explicit_managed_proxy_binding_retains_shutdown_order(self):
        self.exercise_serve_binding(["--bind-host", "0.0.0.0"], "0.0.0.0")

    def test_explicit_loopback_binding_retains_shutdown_order(self):
        self.exercise_serve_binding(["--bind-host", "127.0.0.1"], "127.0.0.1")

    def test_host_and_port_environment_do_not_override_defaults(self):
        self.exercise_serve_binding([], "127.0.0.1", port=None)


if __name__ == "__main__":
    unittest.main()
