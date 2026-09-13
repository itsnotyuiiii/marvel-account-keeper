"""Real process-boundary recovery; all credentials and remote callbacks are fake.

The parent force-stops a child while its real OS owner lock is held. On Windows
Popen.terminate() uses TerminateProcess, not the CLI's graceful signal drain.
POSIX uses kill() here for the same abrupt-exit scenario. This does not establish
graceful Linux signal handling, power-loss durability, or live SDK token expiry.
"""

from contextlib import closing
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from standalone_session_store import EncryptedSqliteSessionStore, SessionStoreError
from tests.standalone_session_manager_test import OWNER


ROOT = Path(__file__).resolve().parents[1]
KEY = b"P" * 32  # Fixed test-only encryption key, not an operator secret.
PRIVATE_MARKERS = (KEY, b"fixture-prior-refresh", b"fixture-prior-access",
                   b"fixture-refresh-1", b"fixture-access-1", b"fixture-refresh-2",
                   b"fixture-access-2", b"fixture-wrapper", b"fixture-mpay")

# -I excludes user site/PYTHONPATH; the only inserted source path is the test's
# verified checkout. The audit hook blocks even socket creation/DNS, and its
# SystemExit cannot be swallowed by manager Exception-to-fixed-outcome handling.
CHILD = r'''
import sys

def no_network(event, args):
    if event.startswith('socket.'):
        raise SystemExit(97)

sys.addaudithook(no_network)
sys.path.insert(0, sys.argv[1])
from contextlib import contextmanager
import json
import standalone_session_manager as sessions
from standalone_session_store import EncryptedSqliteSessionStore
from tests.standalone_session_manager_test import OWNER, TARGET, bundle, candidate

def emit(value):
    print(json.dumps(value, sort_keys=True, separators=(',', ':')), flush=True)

def main():
    mode, phase, path = sys.argv[2:]
    if mode == 'network_guard':
        import socket
        socket.socket()  # Must stop before any socket or connection exists.
        raise SystemExit(96)
    store = EncryptedSqliteSessionStore(path, b'P' * 32)
    calls = {'exchange': 0, 'query': 0}

    def hold():
        revision, record = store.load(OWNER)
        emit({'phase': phase, 'state': record['state'], 'revision': revision,
              'exchange_calls': calls['exchange'], 'query_calls': calls['query']})
        # The parent keeps stdin open, then force-stops us. No sleeps/poll loop
        # or context-manager unwinding is involved in the acceptance boundary.
        sys.stdin.buffer.read(1)
        raise SystemExit(95)

    def exchange(inputs):
        calls['exchange'] += 1
        if mode == 'recover':
            raise SystemExit(99)  # Recovery must never exchange ANY SDK token.
        expected = 'fixture-refresh-1' if mode == 'next' else 'fixture-prior-refresh'
        assert inputs.refresh_token == expected
        if mode == 'hold' and phase == 'refresh_in_flight':
            hold()
        return candidate(inputs, 2 if mode == 'next' else 1)

    def query(sauth, uid):
        calls['query'] += 1
        assert uid == (TARGET if mode == 'hold' else OWNER)
        assert sauth['sessionid'] == ('fixture-access-2' if mode == 'next' else 'fixture-access-1')
        assert 'refresh_token' not in sauth and 'refreshToken' not in sauth
        _, during = store.load(OWNER)
        assert during['state'] == 'candidate_verifying'
        assert during['saved_account']['refreshToken'] == (
            'fixture-refresh-2' if mode == 'next' else 'fixture-refresh-1')
        if mode == 'hold':
            assert phase == 'candidate_verifying'
            hold()
        return sessions.QueryResult(OWNER, True, {'uid': str(uid)})

    manager = sessions.OwnerSessionManager(store, exchange=exchange, query=query,
        clock_ms=lambda: 1700000001234, random_samples=lambda: [0.25] * 8)
    with store.exclusive_owner():
        if mode == 'hold':
            assert manager.bootstrap(bundle()).outcome == 'session_bootstrapped'
            real_cas = store.compare_and_swap
            real_connection = store._connection
            armed = False

            def after_commit(owner, revision, replacement):
                nonlocal armed
                result = real_cas(owner, revision, replacement)
                if replacement['state'] == 'candidate_pending':
                    if phase == 'candidate_pending':
                        hold()
                    if phase == 'sqlite_before_commit':
                        armed = True
                return result

            class ConnectionProxy:
                def __init__(self, connection):
                    self.connection = connection

                def execute(self, sql, *args):
                    # Stop the ACTUAL next candidate_verifying CAS after its
                    # UPDATE, before COMMIT; candidate_pending is already durable.
                    if armed and sql == 'COMMIT':
                        hold()
                    return self.connection.execute(sql, *args)

            @contextmanager
            def transaction_boundary():
                with real_connection() as connection:
                    yield ConnectionProxy(connection)

            store.compare_and_swap = after_commit
            if phase == 'sqlite_before_commit':
                store._connection = transaction_boundary
            manager.lookup(TARGET)
            raise SystemExit(96)  # A requested stop boundary was never reached.

        before_revision, before = store.load(OWNER)
        if mode == 'next':
            assert before['state'] == 'ready' and before['owner_verified'] is True
            assert before['saved_account']['refreshToken'] == 'fixture-refresh-1'
            result = manager.lookup(OWNER)
        else:
            assert mode == 'recover'
            assert manager.recover_pending().outcome == 'session_exclusive_ownership_required'
            assert calls == {'exchange': 0, 'query': 0}
            if phase == 'refresh_in_flight':
                assert before['state'] == 'refresh_in_flight'
                assert before['saved_account']['refreshToken'] == 'fixture-prior-refresh'
                assert before['candidate'] is None
                assert manager.lookup(TARGET).outcome == 'session_rotation_unknown'
                result = manager.recover_pending(exclusive_ownership=True)
                assert manager.lookup(OWNER).outcome == 'session_rotation_unknown'
                assert store.load(OWNER) == (before_revision, before)
            else:
                assert before['saved_account']['refreshToken'] == 'fixture-refresh-1'
                assert before['candidate'] == {'access_token': 'fixture-access-1'}
                if phase == 'candidate_verifying':
                    assert manager.lookup(TARGET).outcome == 'session_verification_in_flight'
                    assert calls == {'exchange': 0, 'query': 0}
                result = manager.recover_pending(exclusive_ownership=True)
        after_revision, after = store.load(OWNER)
        if result.owner_authenticated:
            assert result.outcome == 'session_lookup_complete'
            assert result.profile == {'uid': str(OWNER)}
            assert after['state'] == 'ready' and after['candidate'] is None
            assert after['owner_verified'] is True
            assert after['saved_account']['refreshToken'] == (
                'fixture-refresh-2' if mode == 'next' else 'fixture-refresh-1')
        emit({'outcome': result.outcome, 'owner_authenticated': result.owner_authenticated,
              'state_before': before['state'], 'state_after': after['state'],
              'revision_before': before_revision, 'revision_after': after_revision,
              'exchange_calls': calls['exchange'], 'query_calls': calls['query']})

try:
    main()
except Exception:
    # Synthetic assertion/fixture failures should not print stored inputs either.
    raise SystemExit(96) from None
'''


class StandaloneSessionProcessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rivals-session-process-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.database = self.directory / "synthetic-session.sqlite3"
        # Parent operations also need no sockets. Children install their own
        # non-inherited guard before importing the manager or synthetic callbacks.
        guard = patch("socket.socket", side_effect=AssertionError("no test networking"))
        guard.start()
        self.addCleanup(guard.stop)

    def command(self, mode, phase):
        return [sys.executable, "-I", "-B", "-c", CHILD, str(ROOT), mode, phase, str(self.database)]

    def environment(self):
        return {key: value for key, value in os.environ.items()
                if not key.upper().startswith("NETEASE_") and key.upper() != "PYTHONPATH"}

    def assert_redacted(self, *outputs):
        for output in outputs:
            for marker in PRIVATE_MARKERS:
                self.assertNotIn(marker, output)
            self.assertNotIn(b"Traceback", output)

    @staticmethod
    def force_stop(process):
        if process.poll() is None:
            if os.name == "nt":
                process.terminate()  # Windows TerminateProcess, not SIGTERM drain.
            else:
                process.kill()  # Explicitly abrupt POSIX exit, not graceful signals.
        process.wait(timeout=10)

    def start_at_boundary(self, phase):
        process = subprocess.Popen(self.command("hold", phase), cwd=self.directory,
            env=self.environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)

        def cleanup():
            self.force_stop(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

        self.addCleanup(cleanup)
        lines = queue.Queue()

        def read_line():
            lines.put(process.stdout.readline())

        reader = threading.Thread(target=read_line, daemon=True)
        reader.start()
        try:
            line = lines.get(timeout=10)
        except queue.Empty:
            self.fail("Child did not reach the bounded synthetic stop point")
        reader.join(timeout=1)
        self.assertFalse(reader.is_alive())
        self.assert_redacted(line)
        self.assertTrue(line, "Child exited before the synthetic stop point")
        report = json.loads(line)
        self.assertEqual(report["phase"], phase)
        self.assertEqual(report["exchange_calls"], 1)
        self.assertEqual(report["query_calls"], int(phase == "candidate_verifying"))
        self.assertIsNone(process.poll(), "Child must still own the lock")
        return process, report

    def finish_forced_exit(self, process):
        self.force_stop(process)
        stdout, stderr = process.communicate(timeout=5)
        self.assert_redacted(stdout, stderr)
        self.assertEqual(stdout, b"")
        self.assertEqual(stderr, b"")
        self.assertNotEqual(process.returncode, 0)
        self.assertNotIn(process.returncode, (95, 96, 97, 99))

    def fresh_process(self, mode, phase, *, expected_returncode=0):
        result = subprocess.run(self.command(mode, phase), cwd=self.directory,
            env=self.environment(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=15)
        self.assert_redacted(result.stdout, result.stderr)
        self.assertEqual(result.returncode, expected_returncode, "Synthetic child failed its process-boundary assertions")
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout) if result.stdout else None

    def assert_ciphertext_only(self, *, owner_active=False):
        for path in self.directory.iterdir():
            # Windows' held byte-range lock also forbids reading that byte.
            # Check the lock file after exit; database/journal remain checked.
            if owner_active and path.name == self.database.name + ".lock":
                continue
            if path.is_file():
                contents = path.read_bytes()
                for marker in PRIVATE_MARKERS:
                    self.assertNotIn(marker, contents)

    def killed_candidate(self, phase):
        process, ready = self.start_at_boundary(phase)
        expected_state = "candidate_verifying" if phase == "candidate_verifying" else "candidate_pending"
        self.assertEqual(ready["state"], expected_state)
        self.assertEqual(ready["revision"], 4 if phase == "candidate_verifying" else 3)
        store = EncryptedSqliteSessionStore(self.database, KEY)
        with self.assertRaisesRegex(SessionStoreError, "^session_store_in_use$"):
            with store.exclusive_owner():
                self.fail("Competing process acquired the live owner's lock")
        if phase == "sqlite_before_commit":
            with closing(sqlite3.connect(self.database, timeout=0.05, isolation_level=None)) as connection:
                with self.assertRaises(sqlite3.OperationalError) as failure:
                    connection.execute("BEGIN IMMEDIATE")
                self.assertEqual(failure.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
        self.assert_ciphertext_only(owner_active=True)
        self.finish_forced_exit(process)
        # A NEW process takes the OS lock, loads the committed candidate, and
        # writes a fresh CAS transaction: both lock layers must now be usable.
        recovered = self.fresh_process("recover", phase)
        self.assertEqual(recovered["outcome"], "session_lookup_complete")
        self.assertTrue(recovered["owner_authenticated"])
        self.assertEqual(recovered["state_before"], expected_state)
        self.assertEqual(recovered["state_after"], "ready")
        self.assertEqual(recovered["revision_before"], ready["revision"])
        self.assertEqual(recovered["revision_after"], ready["revision"] + 2)
        self.assertEqual(recovered["exchange_calls"], 0)
        self.assertEqual(recovered["query_calls"], 1)
        self.assert_ciphertext_only()

    def test_forced_exit_after_durable_candidate_commit_recovers_without_sdk_exchange(self):
        self.killed_candidate("candidate_pending")

    def test_forced_exit_during_verification_recovers_then_next_process_uses_latest_refresh(self):
        self.killed_candidate("candidate_verifying")
        result = self.fresh_process("next", "candidate_verifying")
        self.assertEqual(result["outcome"], "session_lookup_complete")
        self.assertEqual(result["state_before"], "ready")
        self.assertEqual(result["state_after"], "ready")
        self.assertTrue(result["owner_authenticated"])
        self.assertEqual(result["exchange_calls"], 1)
        self.assertEqual(result["query_calls"], 1)
        self.assertGreater(result["revision_after"], result["revision_before"])
        self.assert_ciphertext_only()

    def test_forced_exit_rolls_back_open_sqlite_cas_and_releases_both_locks(self):
        self.killed_candidate("sqlite_before_commit")

    def test_forced_exit_in_uncertain_exchange_never_replays_original_token(self):
        process, ready = self.start_at_boundary("refresh_in_flight")
        self.assertEqual(ready["state"], "refresh_in_flight")
        self.assertEqual(ready["revision"], 2)
        store = EncryptedSqliteSessionStore(self.database, KEY)
        with self.assertRaisesRegex(SessionStoreError, "^session_store_in_use$"):
            with store.exclusive_owner():
                self.fail("Competing process acquired the live owner's lock")
        self.finish_forced_exit(process)
        result = self.fresh_process("recover", "refresh_in_flight")
        self.assertEqual(result["outcome"], "session_rotation_unknown")
        self.assertFalse(result["owner_authenticated"])
        self.assertEqual(result["state_before"], "refresh_in_flight")
        self.assertEqual(result["state_after"], "refresh_in_flight")
        self.assertEqual(result["revision_before"], ready["revision"])
        self.assertEqual(result["revision_after"], ready["revision"])
        self.assertEqual(result["exchange_calls"], 0)
        self.assertEqual(result["query_calls"], 0)
        self.assert_ciphertext_only()

    def test_child_audit_guard_rejects_socket_creation_before_networking(self):
        self.assertIsNone(self.fresh_process("network_guard", "none", expected_returncode=97))
        self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
