"""Encrypted, compare-and-swap storage for one authorized native API owner.

The caller supplies the database path and a 32-byte server-managed key. Nothing
reads browser storage, discovers credentials, generates a persistent key, or
exports secrets. SQLite contains ciphertext plus public owner/revision metadata.
Keep the key in the host's secret manager, separately from database backups.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


APPLICATION_ID = 0x52565353
MAX_RECORD_BYTES = 256 * 1024
MAX_REVISION = (1 << 63) - 2


class SessionStoreError(ValueError):
    def __init__(self, code="session_store_unavailable"):
        self.code = code if code in {
            "session_store_unavailable", "session_store_conflict", "session_store_in_use",
            "session_store_inputs_invalid", "session_store_record_invalid",
        } else "session_store_unavailable"
        super().__init__(self.code)


def _owner(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise SessionStoreError("session_store_inputs_invalid")
    return str(value)


def _record_bytes(record):
    if type(record) is not dict:
        raise SessionStoreError("session_store_record_invalid")
    try:
        raw = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if not 0 < len(raw) <= MAX_RECORD_BYTES:
            raise ValueError()
        return raw
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SessionStoreError("session_store_record_invalid") from None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _nonfinite(_value):
    raise ValueError()


class EncryptedSqliteSessionStore:
    """Each call opens a short transaction; CAS never silently retries a writer.

    Hold exclusive_owner() for the entire API worker lifetime. The OS releases
    its advisory lock on process exit, allowing an explicit manager recovery of
    a persisted candidate, but never replay of an uncertain token rotation.
    """
    def __init__(self, path, key):
        if type(key) is not bytes or len(key) != 32:
            raise SessionStoreError("session_store_inputs_invalid")
        self._path = Path(path).absolute()
        self._cipher = AESGCM(key)
        self._exclusive = False
        try:
            if not self._path.parent.is_dir() or self._path.is_symlink():
                raise ValueError()
            if self._path.exists():
                if not self._path.is_file():
                    raise ValueError()
                with self._connection() as connection:
                    self._check_database(connection)
            else:
                # Exclusive creation never overwrites an existing database.
                fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                with self._connection() as connection:
                    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                    connection.execute("PRAGMA user_version = 1")
                    connection.execute("CREATE TABLE sessions (owner_uid TEXT PRIMARY KEY, revision INTEGER NOT NULL, sealed BLOB NOT NULL)")
        except SessionStoreError:
            raise
        except Exception:
            raise SessionStoreError() from None

    def __repr__(self):
        return "<EncryptedSqliteSessionStore>"

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self._path, timeout=5, isolation_level=None)
        try:
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()

    def _check_database(self, connection):
        if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != 1):
            raise SessionStoreError("session_store_inputs_invalid")

    @staticmethod
    def _aad(owner, revision):
        return f"rivals-native-session:1:{owner}:{revision}".encode("ascii")

    def load(self, owner_uid):
        owner = _owner(owner_uid)
        try:
            with self._connection() as connection:
                self._check_database(connection)
                row = connection.execute("SELECT revision, sealed FROM sessions WHERE owner_uid = ?", (owner,)).fetchone()
            if row is None:
                return None
            revision, sealed = row
            if (type(revision) is not int or not 1 <= revision <= MAX_REVISION
                    or type(sealed) is not bytes or not 28 < len(sealed) <= MAX_RECORD_BYTES + 28):
                raise ValueError()
            raw = self._cipher.decrypt(sealed[:12], sealed[12:], self._aad(owner, revision))
            record = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite)
            if type(record) is not dict:
                raise ValueError()
            return revision, record
        except Exception:
            raise SessionStoreError() from None

    def compare_and_swap(self, owner_uid, expected_revision, replacement):
        owner = _owner(owner_uid)
        if expected_revision is not None and (type(expected_revision) is not int or not 1 <= expected_revision < MAX_REVISION):
            raise SessionStoreError("session_store_inputs_invalid")
        raw = _record_bytes(replacement)
        revision = 1 if expected_revision is None else expected_revision + 1
        nonce = os.urandom(12)
        sealed = nonce + self._cipher.encrypt(nonce, raw, self._aad(owner, revision))
        try:
            with self._connection() as connection:
                self._check_database(connection)
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT revision FROM sessions WHERE owner_uid = ?", (owner,)).fetchone()
                if (row[0] if row else None) != expected_revision:
                    connection.execute("ROLLBACK")
                    raise SessionStoreError("session_store_conflict")
                if row:
                    connection.execute("UPDATE sessions SET revision = ?, sealed = ? WHERE owner_uid = ?", (revision, sealed, owner))
                else:
                    connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (owner, revision, sealed))
                connection.execute("COMMIT")
            return revision
        except SessionStoreError:
            raise
        except Exception:
            raise SessionStoreError() from None

    @contextmanager
    def exclusive_owner(self):
        """Nonblocking interprocess ownership, independent of stored rotation state."""
        fd = None
        try:
            if self._exclusive:
                raise SessionStoreError("session_store_in_use")
            lock_path = self._path.with_name(self._path.name + ".lock")
            if lock_path.is_symlink():
                raise SessionStoreError("session_store_inputs_invalid")
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise SessionStoreError("session_store_inputs_invalid")
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._exclusive = True
        except SessionStoreError:
            if fd is not None:
                os.close(fd)
            raise
        except Exception:
            if fd is not None:
                os.close(fd)
            raise SessionStoreError("session_store_in_use") from None
        try:
            yield self
        finally:
            self._exclusive = False
            os.close(fd)
