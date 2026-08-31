"""Isolated, owner-authorized Marvel Rivals match storage.

The match index deliberately has no dependency on the credential vault or the
rank-refresh pipeline.  Callers pass internal vault account IDs and already
normalized facts; player names, Marvel Rivals UIDs, participant collections,
and raw source payloads have no representation in this schema.
"""

from __future__ import annotations

import errno
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence


SCHEMA_VERSION = 1
_PLATFORMS = frozenset({"unknown", "pc", "playstation", "xbox"})
_EVIDENCE_KINDS = frozenset({"direct", "inferred_owned_account"})
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_DIGEST_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class MatchIndexError(RuntimeError):
    """Base class for stable match-index failures.

    ``code`` is suitable for application routing.  ``detail`` is intentionally
    generic and never contains SQLite text, paths, or imported values.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class MatchIndexUnavailable(MatchIndexError):
    """The match store cannot currently serve requests (HTTP 503 class)."""


class MatchIndexStorageFull(MatchIndexError):
    """The match store or its backup destination has no space (HTTP 507)."""


class MatchIndexConflict(MatchIndexError):
    """A stable key was presented with different normalized facts (HTTP 409)."""


@dataclass(frozen=True)
class MatchFact:
    """One opted-in account's normalized facts for one match.

    ``account_id`` and ``source_account_id`` are internal vault identifiers,
    never Marvel Rivals UIDs.  ``source_account_id`` is required for inferred
    evidence and must be omitted for direct evidence.
    """

    account_id: str
    match_key: str
    occurred_at: str
    platform: str
    evidence_kind: str
    source_record_digest: str
    season: str | None = None
    mode: str | None = None
    map_name: str | None = None
    result: str | None = None
    duration_seconds: int | None = None
    hero: str | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    damage_dealt: int | None = None
    damage_taken: int | None = None
    healing_done: int | None = None
    rank_at_match: str | None = None
    source_account_id: str | None = None


@dataclass(frozen=True)
class ImportBatch:
    source_kind: str
    source_digest: str
    schema_version: str
    policy_version: str
    authorization_basis: str
    imported_at: str | None = None


@dataclass(frozen=True)
class SourceStateUpdate:
    account_id: str
    source_kind: str
    last_attempt_at: str
    last_success_at: str | None = None
    error_code: str | None = None
    retry_at: str | None = None


@dataclass(frozen=True)
class MatchIndexHealth:
    available: bool
    initialized: bool
    schema_version: int | None
    error_code: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class RecordClassification:
    new_count: int
    duplicate_count: int
    conflict_count: int
    new_keys: tuple[tuple[str, str], ...]
    duplicate_keys: tuple[tuple[str, str], ...]
    conflict_keys: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ImportResult:
    batch_id: str
    accepted_count: int
    rejected_count: int
    duplicate_count: int
    duplicate_only: bool


@dataclass(frozen=True)
class StoredSourceState:
    account_id: str
    source_kind: str
    last_attempt_at: str
    last_success_at: str | None
    error_code: str | None
    retry_at: str | None


@dataclass(frozen=True)
class StoredMatch:
    account_id: str
    match_key: str
    occurred_at: str
    platform: str
    season: str | None
    mode: str | None
    map_name: str | None
    result: str | None
    duration_seconds: int | None
    hero: str | None
    kills: int | None
    deaths: int | None
    assists: int | None
    damage_dealt: int | None
    damage_taken: int | None
    healing_done: int | None
    rank_at_match: str | None
    evidence_kind: str
    provenance_count: int
    first_imported_at: str
    last_imported_at: str


@dataclass(frozen=True)
class AccountMatchStatus:
    account_id: str
    total_matches: int
    direct_matches: int
    inferred_matches: int
    first_occurred_at: str | None
    last_occurred_at: str | None
    last_success_at: str | None
    last_error_code: str | None
    retry_at: str | None
    source_states: tuple[StoredSourceState, ...]


@dataclass(frozen=True)
class PurgeResult:
    account_id: str
    facts_deleted: int
    dependent_facts_deleted: int
    provenance_deleted: int
    source_states_deleted: int
    matches_deleted: int
    batches_deleted: int
    backup_path: Path


@dataclass(frozen=True)
class ReconcileResult:
    facts_deleted: int
    dependent_facts_deleted: int
    provenance_deleted: int
    source_states_deleted: int
    matches_deleted: int
    batches_deleted: int
    backup_path: Path | None


@dataclass(frozen=True)
class _CanonicalFact:
    account_id: str
    match_key: str
    occurred_at: str
    platform: str
    evidence_kind: str
    source_record_digest: str
    season: str | None
    mode: str | None
    map_name: str | None
    result: str | None
    duration_seconds: int | None
    hero: str | None
    kills: int | None
    deaths: int | None
    assists: int | None
    damage_dealt: int | None
    damage_taken: int | None
    healing_done: int | None
    rank_at_match: str | None
    source_account_id: str | None

    @property
    def match_values(self) -> tuple[object, ...]:
        return (
            self.occurred_at,
            self.season,
            self.mode,
            self.map_name,
            self.result,
            self.duration_seconds,
        )

    @property
    def account_values(self) -> tuple[object, ...]:
        return (
            self.platform,
            self.hero,
            self.kills,
            self.deaths,
            self.assists,
            self.damage_dealt,
            self.damage_taken,
            self.healing_done,
            self.rank_at_match,
            self.evidence_kind,
        )

    @property
    def account_fact_values(self) -> tuple[object, ...]:
        """The factual account fields, excluding monotonic evidence strength."""

        return self.account_values[:-1]


@dataclass(frozen=True)
class _CanonicalBatch:
    source_kind: str
    source_digest: str
    schema_version: str
    policy_version: str
    authorization_basis: str
    imported_at: str


@dataclass(frozen=True)
class _CanonicalSourceState:
    account_id: str
    source_kind: str
    last_attempt_at: str
    last_success_at: str | None
    error_code: str | None
    retry_at: str | None


_MIGRATION_1 = (
    """
    CREATE TABLE matches (
        match_key TEXT PRIMARY KEY,
        occurred_at TEXT NOT NULL,
        season TEXT,
        mode TEXT,
        map_name TEXT,
        result TEXT,
        duration_seconds INTEGER CHECK (
            duration_seconds IS NULL OR duration_seconds >= 0
        ),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE account_match_facts (
        account_id TEXT NOT NULL,
        match_key TEXT NOT NULL,
        platform TEXT NOT NULL CHECK (
            platform IN ('unknown', 'pc', 'playstation', 'xbox')
        ),
        hero TEXT,
        kills INTEGER CHECK (kills IS NULL OR kills >= 0),
        deaths INTEGER CHECK (deaths IS NULL OR deaths >= 0),
        assists INTEGER CHECK (assists IS NULL OR assists >= 0),
        damage_dealt INTEGER CHECK (damage_dealt IS NULL OR damage_dealt >= 0),
        damage_taken INTEGER CHECK (damage_taken IS NULL OR damage_taken >= 0),
        healing_done INTEGER CHECK (healing_done IS NULL OR healing_done >= 0),
        rank_at_match TEXT,
        evidence_kind TEXT NOT NULL CHECK (
            evidence_kind IN ('direct', 'inferred_owned_account')
        ),
        created_at TEXT NOT NULL,
        PRIMARY KEY (account_id, match_key),
        FOREIGN KEY (match_key) REFERENCES matches(match_key) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE import_batches (
        batch_id TEXT PRIMARY KEY,
        source_kind TEXT NOT NULL,
        source_digest TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        authorization_basis TEXT NOT NULL,
        imported_at TEXT NOT NULL,
        accepted_count INTEGER NOT NULL CHECK (accepted_count >= 0),
        rejected_count INTEGER NOT NULL CHECK (rejected_count >= 0),
        duplicate_count INTEGER NOT NULL CHECK (duplicate_count >= 0)
    )
    """,
    """
    CREATE TABLE match_provenance (
        provenance_id INTEGER PRIMARY KEY,
        account_id TEXT NOT NULL,
        match_key TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        source_record_digest TEXT NOT NULL,
        source_account_id TEXT,
        FOREIGN KEY (account_id, match_key)
            REFERENCES account_match_facts(account_id, match_key)
            ON DELETE CASCADE,
        FOREIGN KEY (batch_id) REFERENCES import_batches(batch_id)
            ON DELETE CASCADE,
        UNIQUE (batch_id, account_id, match_key, source_record_digest)
    )
    """,
    """
    CREATE TABLE match_source_state (
        account_id TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        last_attempt_at TEXT NOT NULL,
        last_success_at TEXT,
        error_code TEXT,
        retry_at TEXT,
        PRIMARY KEY (account_id, source_kind)
    ) WITHOUT ROWID
    """,
    "CREATE INDEX idx_matches_occurred_at ON matches(occurred_at DESC, match_key)",
    "CREATE INDEX idx_provenance_source_account ON match_provenance(source_account_id)",
    "CREATE INDEX idx_provenance_batch ON match_provenance(batch_id)",
)

_REQUIRED_TABLES = frozenset({
    "matches",
    "account_match_facts",
    "import_batches",
    "match_provenance",
    "match_source_state",
})


class MatchIndex:
    """Thread-safe facade over a separate SQLite match database.

    The constructor performs no filesystem access.  Call :meth:`initialize`
    explicitly, or let the first data operation initialize lazily.  Each public
    operation owns its SQLite connection.  Writes are serialized within this
    process by ``_match_write_lock``; an application that also holds a vault
    lock must acquire the vault lock first.
    """

    def __init__(
        self,
        db_path: Path | str,
        backup_dir: Path | str,
        *,
        busy_timeout_ms: int = 5000,
    ) -> None:
        if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int):
            raise ValueError("busy_timeout_ms must be an integer")
        if busy_timeout_ms < 1 or busy_timeout_ms > 120_000:
            raise ValueError("busy_timeout_ms must be between 1 and 120000")
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)
        if self.db_path == self.backup_dir:
            raise ValueError("db_path and backup_dir must differ")
        self.busy_timeout_ms = busy_timeout_ms
        self._match_write_lock = threading.RLock()
        self._initialized = False

    def initialize(self) -> MatchIndexHealth:
        """Create or migrate the match index, returning a non-throwing status."""

        with self._match_write_lock:
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                existed_with_content = self.db_path.exists() and self.db_path.stat().st_size > 0
                with self._operation_connection() as connection:
                    current = self._user_version(connection)
                    if current > SCHEMA_VERSION:
                        raise MatchIndexUnavailable(
                            "schema_too_new",
                            "The match index was created by a newer application version.",
                        )
                    if current < SCHEMA_VERSION:
                        if existed_with_content:
                            self._backup_connection(connection, "migration")
                        self._migrate(connection, current)
                    self._verify_database(connection)
                self._initialized = True
                return MatchIndexHealth(True, True, SCHEMA_VERSION)
            except (OSError, sqlite3.Error, MatchIndexError) as exc:
                self._initialized = False
                failure = self._as_match_error(exc)
                return MatchIndexHealth(
                    False,
                    False,
                    self._safe_existing_version(),
                    failure.code,
                    failure.detail,
                )

    def health(self) -> MatchIndexHealth:
        """Inspect availability without raising or repairing/replacing a bad DB."""

        if not self.db_path.exists():
            return MatchIndexHealth(True, False, 0)
        try:
            with self._inspection_connection() as connection:
                version = self._user_version(connection)
                if version > SCHEMA_VERSION:
                    return MatchIndexHealth(
                        False,
                        False,
                        version,
                        "schema_too_new",
                        "The match index was created by a newer application version.",
                    )
                if version < SCHEMA_VERSION:
                    return MatchIndexHealth(True, False, version)
                self._verify_database(connection)
                return MatchIndexHealth(True, True, version)
        except (OSError, sqlite3.Error, MatchIndexError) as exc:
            failure = self._as_match_error(exc)
            return MatchIndexHealth(
                False,
                False,
                self._safe_existing_version(),
                failure.code,
                failure.detail,
            )

    def backup(self, *, purpose: str = "manual") -> Path:
        """Create a transactionally consistent SQLite backup."""

        safe_purpose = _token(purpose, "purpose", maximum=40)
        self._require_initialized()
        with self._match_write_lock:
            with self._operation_connection() as connection:
                return self._backup_connection(connection, safe_purpose)

    def classify_records(self, records: Iterable[MatchFact]) -> RecordClassification:
        """Classify a normalized import without persisting any match data."""

        canonical = self._canonical_records(records)
        self._require_initialized()
        with self._operation_connection() as connection:
            return self._classify(connection, canonical)

    def import_records(
        self,
        batch: ImportBatch,
        records: Iterable[MatchFact],
        *,
        source_states: Iterable[SourceStateUpdate] = (),
        rejected_count: int = 0,
    ) -> ImportResult:
        """Commit one normalized import and its source state atomically."""

        canonical_batch = _canonical_batch(batch)
        canonical_records = self._canonical_records(records)
        canonical_states = _canonical_source_states(source_states)
        rejected = _nonnegative_int(rejected_count, "rejected_count", maximum=10_000_000)
        self._require_initialized()

        with self._match_write_lock:
            with self._operation_connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    result = self._insert_import(
                        connection,
                        canonical_batch,
                        canonical_records,
                        canonical_states,
                        rejected,
                    )
                    connection.execute("COMMIT")
                    return result
                except MatchIndexError:
                    self._rollback_quietly(connection)
                    raise
                except sqlite3.Error as exc:
                    self._rollback_quietly(connection)
                    raise self._as_match_error(exc) from None

    def list_matches(
        self,
        account_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        season: str | None = None,
        platform: str | None = None,
    ) -> list[StoredMatch]:
        """List one opted-in account's normalized match history, newest first."""

        account = _account_id(account_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        season_filter = _optional_text(season, "season", maximum=80)
        platform_filter = None
        if platform is not None:
            platform_filter = _text(platform, "platform", maximum=20).lower()
            if platform_filter not in _PLATFORMS:
                raise ValueError("platform must be unknown, pc, playstation, or xbox")
        self._require_initialized()
        predicates = ["f.account_id = ?"]
        parameters: list[object] = [account]
        if season_filter is not None:
            predicates.append("m.season = ?")
            parameters.append(season_filter)
        if platform_filter is not None:
            predicates.append("f.platform = ?")
            parameters.append(platform_filter)
        parameters.extend((limit, offset))
        sql = f"""
            SELECT
                f.account_id, m.match_key, m.occurred_at, f.platform,
                m.season, m.mode, m.map_name, m.result, m.duration_seconds,
                f.hero, f.kills, f.deaths, f.assists, f.damage_dealt,
                f.damage_taken, f.healing_done, f.rank_at_match,
                f.evidence_kind, COUNT(p.provenance_id) AS provenance_count,
                MIN(b.imported_at) AS first_imported_at,
                MAX(b.imported_at) AS last_imported_at
            FROM account_match_facts AS f
            JOIN matches AS m ON m.match_key = f.match_key
            JOIN match_provenance AS p
              ON p.account_id = f.account_id AND p.match_key = f.match_key
            JOIN import_batches AS b ON b.batch_id = p.batch_id
            WHERE {' AND '.join(predicates)}
            GROUP BY f.account_id, f.match_key
            ORDER BY m.occurred_at DESC, m.match_key ASC
            LIMIT ? OFFSET ?
        """
        with self._operation_connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [StoredMatch(**dict(row)) for row in rows]

    def status(self, account_id: str) -> AccountMatchStatus:
        """Return match counts and isolated source-state details for an account."""

        account = _account_id(account_id)
        self._require_initialized()
        with self._operation_connection() as connection:
            counts = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_matches,
                    COALESCE(SUM(evidence_kind = 'direct'), 0) AS direct_matches,
                    COALESCE(SUM(evidence_kind = 'inferred_owned_account'), 0)
                        AS inferred_matches,
                    MIN(m.occurred_at) AS first_occurred_at,
                    MAX(m.occurred_at) AS last_occurred_at
                FROM account_match_facts AS f
                JOIN matches AS m ON m.match_key = f.match_key
                WHERE f.account_id = ?
                """,
                (account,),
            ).fetchone()
            state_rows = connection.execute(
                """
                SELECT account_id, source_kind, last_attempt_at, last_success_at,
                       error_code, retry_at
                FROM match_source_state
                WHERE account_id = ?
                ORDER BY source_kind ASC
                """,
                (account,),
            ).fetchall()
        states = tuple(StoredSourceState(**dict(row)) for row in state_rows)
        latest_state = max(states, key=lambda state: state.last_attempt_at, default=None)
        successes = [state.last_success_at for state in states if state.last_success_at]
        return AccountMatchStatus(
            account_id=account,
            total_matches=int(counts["total_matches"]),
            direct_matches=int(counts["direct_matches"]),
            inferred_matches=int(counts["inferred_matches"]),
            first_occurred_at=counts["first_occurred_at"],
            last_occurred_at=counts["last_occurred_at"],
            last_success_at=max(successes, default=None),
            last_error_code=(latest_state.error_code if latest_state else None),
            retry_at=(latest_state.retry_at if latest_state else None),
            source_states=states,
        )

    def has_history(self, account_id: str) -> bool:
        account = _account_id(account_id)
        self._require_initialized()
        with self._operation_connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM account_match_facts WHERE account_id = ? LIMIT 1",
                (account,),
            ).fetchone()
        return row is not None

    def purge_account(self, account_id: str) -> PurgeResult:
        """Back up, then remove an account and facts inferred only through it."""

        account = _account_id(account_id)
        self._require_initialized()
        with self._match_write_lock:
            with self._operation_connection() as connection:
                backup_path = self._backup_connection(connection, "purge")
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    before_provenance = self._count(connection, "match_provenance")
                    connection.execute(
                        "DELETE FROM match_provenance WHERE source_account_id = ?",
                        (account,),
                    )
                    facts_deleted = connection.execute(
                        "DELETE FROM account_match_facts WHERE account_id = ?",
                        (account,),
                    ).rowcount
                    source_states_deleted = connection.execute(
                        "DELETE FROM match_source_state WHERE account_id = ?",
                        (account,),
                    ).rowcount
                    dependent_facts_deleted = connection.execute(
                        """
                        DELETE FROM account_match_facts
                        WHERE NOT EXISTS (
                            SELECT 1 FROM match_provenance AS p
                            WHERE p.account_id = account_match_facts.account_id
                              AND p.match_key = account_match_facts.match_key
                        )
                        """
                    ).rowcount
                    matches_deleted = self._delete_orphan_matches(connection)
                    batches_deleted = self._delete_orphan_batches(connection)
                    after_provenance = self._count(connection, "match_provenance")
                    connection.execute("COMMIT")
                except sqlite3.Error as exc:
                    self._rollback_quietly(connection)
                    raise self._as_match_error(exc) from None
        return PurgeResult(
            account_id=account,
            facts_deleted=facts_deleted,
            dependent_facts_deleted=dependent_facts_deleted,
            provenance_deleted=before_provenance - after_provenance,
            source_states_deleted=source_states_deleted,
            matches_deleted=matches_deleted,
            batches_deleted=batches_deleted,
            backup_path=backup_path,
        )

    def reconcile_accounts(self, valid_account_ids: Iterable[str]) -> ReconcileResult:
        """Remove rows no longer authorized by the current vault account set."""

        valid = tuple(dict.fromkeys(_account_id(item) for item in valid_account_ids))
        self._require_initialized()
        with self._match_write_lock:
            with self._operation_connection() as connection:
                self._create_valid_accounts_temp_table(connection, valid)
                if not self._has_reconciliation_work(connection):
                    return ReconcileResult(0, 0, 0, 0, 0, 0, None)
                backup_path = self._backup_connection(connection, "reconcile")
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    before_provenance = self._count(connection, "match_provenance")
                    connection.execute(
                        """
                        DELETE FROM match_provenance
                        WHERE source_account_id IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM temp.valid_accounts AS v
                              WHERE v.account_id = match_provenance.source_account_id
                          )
                        """
                    )
                    facts_deleted = connection.execute(
                        """
                        DELETE FROM account_match_facts
                        WHERE NOT EXISTS (
                            SELECT 1 FROM temp.valid_accounts AS v
                            WHERE v.account_id = account_match_facts.account_id
                        )
                        """
                    ).rowcount
                    source_states_deleted = connection.execute(
                        """
                        DELETE FROM match_source_state
                        WHERE NOT EXISTS (
                            SELECT 1 FROM temp.valid_accounts AS v
                            WHERE v.account_id = match_source_state.account_id
                        )
                        """
                    ).rowcount
                    dependent_facts_deleted = connection.execute(
                        """
                        DELETE FROM account_match_facts
                        WHERE NOT EXISTS (
                            SELECT 1 FROM match_provenance AS p
                            WHERE p.account_id = account_match_facts.account_id
                              AND p.match_key = account_match_facts.match_key
                        )
                        """
                    ).rowcount
                    matches_deleted = self._delete_orphan_matches(connection)
                    batches_deleted = self._delete_orphan_batches(connection)
                    after_provenance = self._count(connection, "match_provenance")
                    connection.execute("COMMIT")
                except sqlite3.Error as exc:
                    self._rollback_quietly(connection)
                    raise self._as_match_error(exc) from None
        return ReconcileResult(
            facts_deleted=facts_deleted,
            dependent_facts_deleted=dependent_facts_deleted,
            provenance_deleted=before_provenance - after_provenance,
            source_states_deleted=source_states_deleted,
            matches_deleted=matches_deleted,
            batches_deleted=batches_deleted,
            backup_path=backup_path,
        )

    def _require_initialized(self) -> None:
        if self._initialized and self.db_path.exists():
            return
        health = self.initialize()
        if not health.available or not health.initialized:
            if health.error_code == "storage_full":
                raise MatchIndexStorageFull(
                    "storage_full",
                    health.detail or "The match index storage location is full.",
                )
            raise MatchIndexUnavailable(
                health.error_code or "database_unavailable",
                health.detail or "The match index is unavailable.",
            )

    @contextmanager
    def _operation_connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                str(self.db_path),
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys = ON")
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(journal_mode).lower() != "wal":
                raise MatchIndexUnavailable(
                    "database_unavailable",
                    "The match index could not enable safe journaling.",
                )
            connection.execute("PRAGMA synchronous = NORMAL")
            yield connection
        except MatchIndexError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise self._as_match_error(exc) from None
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def _inspection_connection(self) -> Iterator[sqlite3.Connection]:
        """Open the existing database read-only for a side-effect-free health check."""

        connection: sqlite3.Connection | None = None
        try:
            uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            connection.execute("PRAGMA query_only = ON")
            yield connection
        except MatchIndexError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise self._as_match_error(exc) from None
        finally:
            if connection is not None:
                connection.close()

    def _migrate(self, connection: sqlite3.Connection, current: int) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = current
            while version < SCHEMA_VERSION:
                if version == 0:
                    for statement in _MIGRATION_1:
                        connection.execute(statement)
                    version = 1
                    connection.execute("PRAGMA user_version = 1")
                else:  # pragma: no cover - defensive future-migration guard
                    raise MatchIndexUnavailable(
                        "database_unavailable",
                        "The match index has no migration path for this schema.",
                    )
            connection.execute("COMMIT")
        except MatchIndexError:
            self._rollback_quietly(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback_quietly(connection)
            raise self._as_match_error(exc) from None

    def _verify_database(self, connection: sqlite3.Connection) -> None:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if str(quick_check).lower() != "ok":
            raise MatchIndexUnavailable(
                "corrupt_database",
                "The match index failed its integrity check.",
            )
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not _REQUIRED_TABLES.issubset(tables):
            raise MatchIndexUnavailable(
                "corrupt_database",
                "The match index schema is incomplete.",
            )

    def _backup_connection(self, source: sqlite3.Connection, purpose: str) -> Path:
        destination_path: Path | None = None
        destination: sqlite3.Connection | None = None
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            base = self.backup_dir / f"match-index-{stamp}-{purpose}.sqlite3"
            destination_path = base
            suffix = 1
            while destination_path.exists():
                destination_path = base.with_name(f"{base.stem}-{suffix}{base.suffix}")
                suffix += 1
            destination = sqlite3.connect(str(destination_path), isolation_level=None)
            source.backup(destination)
            check = destination.execute("PRAGMA quick_check").fetchone()[0]
            if str(check).lower() != "ok":
                raise MatchIndexUnavailable(
                    "backup_failed",
                    "The match index backup failed its integrity check.",
                )
            destination.close()
            destination = None
            return destination_path
        except MatchIndexStorageFull:
            raise
        except MatchIndexError:
            if destination is not None:
                destination.close()
            if destination_path is not None:
                self._unlink_partial_backup(destination_path)
            raise
        except (OSError, sqlite3.Error) as exc:
            if destination is not None:
                destination.close()
            if destination_path is not None:
                self._unlink_partial_backup(destination_path)
            classified = self._as_match_error(exc)
            if isinstance(classified, MatchIndexStorageFull):
                raise classified from None
            raise MatchIndexUnavailable(
                "backup_failed",
                "A required match index backup could not be created.",
            ) from None

    @staticmethod
    def _unlink_partial_backup(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _insert_import(
        self,
        connection: sqlite3.Connection,
        batch: _CanonicalBatch,
        records: Sequence[_CanonicalFact],
        source_states: Sequence[_CanonicalSourceState],
        rejected_count: int,
    ) -> ImportResult:
        batch_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO import_batches (
                batch_id, source_kind, source_digest, schema_version,
                policy_version, authorization_basis, imported_at,
                accepted_count, rejected_count, duplicate_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 0)
            """,
            (
                batch_id,
                batch.source_kind,
                batch.source_digest,
                batch.schema_version,
                batch.policy_version,
                batch.authorization_basis,
                batch.imported_at,
                rejected_count,
            ),
        )
        accepted = 0
        duplicates = 0
        for record in records:
            disposition = self._record_disposition(connection, record)
            if disposition == "conflict":
                raise MatchIndexConflict(
                    "fact_conflict",
                    "A match key already has different normalized facts.",
                )
            if disposition == "new":
                self._insert_record(connection, record, batch.imported_at)
                accepted += 1
            else:
                duplicates += 1
                if disposition == "upgrade":
                    connection.execute(
                        """
                        UPDATE account_match_facts
                        SET evidence_kind = 'direct'
                        WHERE account_id = ? AND match_key = ?
                          AND evidence_kind = 'inferred_owned_account'
                        """,
                        (record.account_id, record.match_key),
                    )
            # Allowlisted adapters bind each batch to exactly one source
            # identity. Real evidence transitions therefore arrive in
            # separate batches (and batch_id preserves each provenance row).
            # Repeated identical records inside one batch remain coalesced by
            # the schema's existing provenance uniqueness rule.
            connection.execute(
                """
                INSERT OR IGNORE INTO match_provenance (
                    account_id, match_key, batch_id, source_record_digest,
                    source_account_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.account_id,
                    record.match_key,
                    batch_id,
                    record.source_record_digest,
                    record.source_account_id,
                ),
            )
        for state in source_states:
            connection.execute(
                """
                INSERT INTO match_source_state (
                    account_id, source_kind, last_attempt_at, last_success_at,
                    error_code, retry_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, source_kind) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = excluded.last_success_at,
                    error_code = excluded.error_code,
                    retry_at = excluded.retry_at
                """,
                (
                    state.account_id,
                    state.source_kind,
                    state.last_attempt_at,
                    state.last_success_at,
                    state.error_code,
                    state.retry_at,
                ),
            )
        connection.execute(
            """
            UPDATE import_batches
            SET accepted_count = ?, duplicate_count = ?
            WHERE batch_id = ?
            """,
            (accepted, duplicates, batch_id),
        )
        return ImportResult(
            batch_id=batch_id,
            accepted_count=accepted,
            rejected_count=rejected_count,
            duplicate_count=duplicates,
            duplicate_only=bool(records) and accepted == 0 and rejected_count == 0,
        )

    def _insert_record(
        self,
        connection: sqlite3.Connection,
        record: _CanonicalFact,
        created_at: str,
    ) -> None:
        match = connection.execute(
            "SELECT 1 FROM matches WHERE match_key = ?",
            (record.match_key,),
        ).fetchone()
        if match is None:
            connection.execute(
                """
                INSERT INTO matches (
                    match_key, occurred_at, season, mode, map_name, result,
                    duration_seconds, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.match_key,
                    *record.match_values,
                    created_at,
                ),
            )
        connection.execute(
            """
            INSERT INTO account_match_facts (
                account_id, match_key, platform, hero, kills, deaths, assists,
                damage_dealt, damage_taken, healing_done, rank_at_match,
                evidence_kind, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.account_id,
                record.match_key,
                *record.account_values,
                created_at,
            ),
        )

    def _classify(
        self,
        connection: sqlite3.Connection,
        records: Sequence[_CanonicalFact],
    ) -> RecordClassification:
        new_keys: list[tuple[str, str]] = []
        duplicate_keys: list[tuple[str, str]] = []
        conflict_keys: list[tuple[str, str]] = []
        virtual_matches: dict[str, tuple[object, ...]] = {}
        virtual_facts: dict[tuple[str, str], tuple[object, ...]] = {}
        for record in records:
            key = (record.account_id, record.match_key)
            disposition = self._record_disposition(
                connection,
                record,
                virtual_matches=virtual_matches,
                virtual_facts=virtual_facts,
            )
            if disposition == "new":
                new_keys.append(key)
                virtual_matches.setdefault(record.match_key, record.match_values)
                virtual_facts[key] = record.account_values
            elif disposition in {"duplicate", "upgrade"}:
                duplicate_keys.append(key)
                if disposition == "upgrade":
                    # Classification is non-mutating, but later records in the
                    # same preview must observe the stronger virtual evidence.
                    virtual_facts[key] = record.account_values
            else:
                conflict_keys.append(key)
        return RecordClassification(
            len(new_keys),
            len(duplicate_keys),
            len(conflict_keys),
            tuple(new_keys),
            tuple(duplicate_keys),
            tuple(conflict_keys),
        )

    def _record_disposition(
        self,
        connection: sqlite3.Connection,
        record: _CanonicalFact,
        *,
        virtual_matches: dict[str, tuple[object, ...]] | None = None,
        virtual_facts: dict[tuple[str, str], tuple[object, ...]] | None = None,
    ) -> str:
        if virtual_matches is not None and record.match_key in virtual_matches:
            existing_match = virtual_matches[record.match_key]
        else:
            row = connection.execute(
                """
                SELECT occurred_at, season, mode, map_name, result,
                       duration_seconds
                FROM matches WHERE match_key = ?
                """,
                (record.match_key,),
            ).fetchone()
            existing_match = tuple(row) if row is not None else None
        if existing_match is not None and existing_match != record.match_values:
            return "conflict"

        key = (record.account_id, record.match_key)
        if virtual_facts is not None and key in virtual_facts:
            existing_fact = virtual_facts[key]
        else:
            row = connection.execute(
                """
                SELECT platform, hero, kills, deaths, assists, damage_dealt,
                       damage_taken, healing_done, rank_at_match, evidence_kind
                FROM account_match_facts
                WHERE account_id = ? AND match_key = ?
                """,
                key,
            ).fetchone()
            existing_fact = tuple(row) if row is not None else None
        if existing_fact is None:
            return "new"
        if existing_fact[:-1] != record.account_fact_values:
            return "conflict"
        existing_evidence = existing_fact[-1]
        if existing_evidence == record.evidence_kind:
            return "duplicate"
        if (
            existing_evidence == "inferred_owned_account"
            and record.evidence_kind == "direct"
        ):
            return "upgrade"
        # Evidence is monotonic: an inferred observation can add provenance to
        # an already-direct fact, but it can never weaken the stored evidence.
        return "duplicate"

    @staticmethod
    def _count(connection: sqlite3.Connection, table: str) -> int:
        # table is always an internal constant at call sites.
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    @staticmethod
    def _delete_orphan_matches(connection: sqlite3.Connection) -> int:
        return connection.execute(
            """
            DELETE FROM matches
            WHERE NOT EXISTS (
                SELECT 1 FROM account_match_facts AS f
                WHERE f.match_key = matches.match_key
            )
            """
        ).rowcount

    @staticmethod
    def _delete_orphan_batches(connection: sqlite3.Connection) -> int:
        return connection.execute(
            """
            DELETE FROM import_batches
            WHERE NOT EXISTS (
                SELECT 1 FROM match_provenance AS p
                WHERE p.batch_id = import_batches.batch_id
            )
            """
        ).rowcount

    @staticmethod
    def _create_valid_accounts_temp_table(
        connection: sqlite3.Connection,
        valid: Sequence[str],
    ) -> None:
        connection.execute("DROP TABLE IF EXISTS temp.valid_accounts")
        connection.execute(
            "CREATE TEMP TABLE valid_accounts (account_id TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        connection.executemany(
            "INSERT INTO temp.valid_accounts(account_id) VALUES (?)",
            ((account_id,) for account_id in valid),
        )

    @staticmethod
    def _has_reconciliation_work(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            """
            SELECT
                EXISTS (
                    SELECT 1 FROM account_match_facts AS f
                    WHERE NOT EXISTS (
                        SELECT 1 FROM temp.valid_accounts AS v
                        WHERE v.account_id = f.account_id
                    )
                )
                OR EXISTS (
                    SELECT 1 FROM match_provenance AS p
                    WHERE p.source_account_id IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM temp.valid_accounts AS v
                          WHERE v.account_id = p.source_account_id
                      )
                )
                OR EXISTS (
                    SELECT 1 FROM match_source_state AS s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM temp.valid_accounts AS v
                        WHERE v.account_id = s.account_id
                    )
                )
            """
        ).fetchone()
        return bool(row[0])

    @staticmethod
    def _rollback_quietly(connection: sqlite3.Connection) -> None:
        try:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    @staticmethod
    def _user_version(connection: sqlite3.Connection) -> int:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def _safe_existing_version(self) -> int | None:
        if not self.db_path.exists():
            return 0
        try:
            with self._inspection_connection() as connection:
                return int(connection.execute("PRAGMA user_version").fetchone()[0])
        except (OSError, sqlite3.Error, MatchIndexError):
            return None

    @staticmethod
    def _as_match_error(exc: BaseException) -> MatchIndexError:
        if isinstance(exc, MatchIndexError):
            return exc
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            return MatchIndexStorageFull(
                "storage_full",
                "The match index storage location is full.",
            )
        if isinstance(exc, sqlite3.Error):
            code = getattr(exc, "sqlite_errorcode", None)
            base_code = code & 0xFF if isinstance(code, int) else None
            message = str(exc).lower()
            if base_code == sqlite3.SQLITE_FULL or "database or disk is full" in message:
                return MatchIndexStorageFull(
                    "storage_full",
                    "The match index storage location is full.",
                )
            if base_code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB} or any(
                marker in message
                for marker in ("malformed", "file is not a database", "not a database")
            ):
                return MatchIndexUnavailable(
                    "corrupt_database",
                    "The match index is corrupt or unreadable.",
                )
            if base_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or "locked" in message:
                return MatchIndexUnavailable(
                    "database_busy",
                    "The match index is busy. Try again shortly.",
                )
        return MatchIndexUnavailable(
            "database_unavailable",
            "The match index is unavailable.",
        )

    @staticmethod
    def _canonical_records(records: Iterable[MatchFact]) -> tuple[_CanonicalFact, ...]:
        try:
            items = tuple(records)
        except TypeError as exc:
            raise ValueError("records must be an iterable of MatchFact values") from exc
        return tuple(_canonical_fact(record) for record in items)


def _canonical_fact(value: MatchFact) -> _CanonicalFact:
    if not isinstance(value, MatchFact):
        raise ValueError("records must contain MatchFact values")
    evidence = _text(value.evidence_kind, "evidence_kind", maximum=40)
    if evidence not in _EVIDENCE_KINDS:
        raise ValueError("evidence_kind must be direct or inferred_owned_account")
    source_account = (
        _account_id(value.source_account_id)
        if value.source_account_id is not None
        else None
    )
    if evidence == "inferred_owned_account" and source_account is None:
        raise ValueError("inferred_owned_account requires source_account_id")
    if evidence == "direct" and source_account is not None:
        raise ValueError("direct evidence must not set source_account_id")
    account = _account_id(value.account_id)
    if source_account == account:
        raise ValueError("source_account_id must differ from account_id")
    platform = _text(value.platform, "platform", maximum=20).lower()
    if platform not in _PLATFORMS:
        raise ValueError("platform must be unknown, pc, playstation, or xbox")
    return _CanonicalFact(
        account_id=account,
        match_key=_token(value.match_key, "match_key", maximum=200),
        occurred_at=_timestamp(value.occurred_at, "occurred_at"),
        platform=platform,
        evidence_kind=evidence,
        source_record_digest=_digest(value.source_record_digest, "source_record_digest"),
        season=_optional_text(value.season, "season", maximum=80),
        mode=_optional_text(value.mode, "mode", maximum=100),
        map_name=_optional_text(value.map_name, "map_name", maximum=120),
        result=_optional_text(value.result, "result", maximum=80),
        duration_seconds=_optional_nonnegative_int(
            value.duration_seconds,
            "duration_seconds",
            maximum=7 * 24 * 60 * 60,
        ),
        hero=_optional_text(value.hero, "hero", maximum=120),
        kills=_optional_nonnegative_int(value.kills, "kills", maximum=1_000_000),
        deaths=_optional_nonnegative_int(value.deaths, "deaths", maximum=1_000_000),
        assists=_optional_nonnegative_int(value.assists, "assists", maximum=1_000_000),
        damage_dealt=_optional_nonnegative_int(
            value.damage_dealt, "damage_dealt", maximum=2_000_000_000
        ),
        damage_taken=_optional_nonnegative_int(
            value.damage_taken, "damage_taken", maximum=2_000_000_000
        ),
        healing_done=_optional_nonnegative_int(
            value.healing_done, "healing_done", maximum=2_000_000_000
        ),
        rank_at_match=_optional_text(
            value.rank_at_match, "rank_at_match", maximum=100
        ),
        source_account_id=source_account,
    )


def _canonical_batch(value: ImportBatch) -> _CanonicalBatch:
    if not isinstance(value, ImportBatch):
        raise ValueError("batch must be an ImportBatch")
    return _CanonicalBatch(
        source_kind=_token(value.source_kind, "source_kind", maximum=80),
        source_digest=_digest(value.source_digest, "source_digest"),
        schema_version=_token(value.schema_version, "schema_version", maximum=80),
        policy_version=_token(value.policy_version, "policy_version", maximum=80),
        authorization_basis=_token(
            value.authorization_basis,
            "authorization_basis",
            maximum=100,
        ),
        imported_at=(
            _timestamp(value.imported_at, "imported_at")
            if value.imported_at is not None
            else _utc_now()
        ),
    )


def _canonical_source_states(
    values: Iterable[SourceStateUpdate],
) -> tuple[_CanonicalSourceState, ...]:
    try:
        items = tuple(values)
    except TypeError as exc:
        raise ValueError("source_states must be an iterable") from exc
    result: list[_CanonicalSourceState] = []
    seen: set[tuple[str, str]] = set()
    for value in items:
        if not isinstance(value, SourceStateUpdate):
            raise ValueError("source_states must contain SourceStateUpdate values")
        account = _account_id(value.account_id)
        source = _token(value.source_kind, "source_kind", maximum=80)
        key = (account, source)
        if key in seen:
            raise ValueError("source_states contains a duplicate account/source pair")
        seen.add(key)
        result.append(_CanonicalSourceState(
            account_id=account,
            source_kind=source,
            last_attempt_at=_timestamp(value.last_attempt_at, "last_attempt_at"),
            last_success_at=(
                _timestamp(value.last_success_at, "last_success_at")
                if value.last_success_at is not None
                else None
            ),
            error_code=(
                _token(value.error_code, "error_code", maximum=80)
                if value.error_code is not None
                else None
            ),
            retry_at=(
                _timestamp(value.retry_at, "retry_at")
                if value.retry_at is not None
                else None
            ),
        ))
    return tuple(result)


def _account_id(value: object) -> str:
    return _token(value, "account_id", maximum=128)


def _token(value: object, field: str, *, maximum: int) -> str:
    text = _text(value, field, maximum=maximum)
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{field} contains unsupported characters")
    return text


def _digest(value: object, field: str) -> str:
    text = _text(value, field, maximum=64)
    if not _DIGEST_RE.fullmatch(text):
        raise ValueError(f"{field} must be a 64-character hexadecimal SHA-256")
    return text.lower()


def _text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) > maximum:
        raise ValueError(f"{field} is too long")
    if _CONTROL_RE.search(text):
        raise ValueError(f"{field} contains control characters")
    return text


def _optional_text(value: object, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum=maximum)


def _timestamp(value: object, field: str) -> str:
    text = _text(value, field, maximum=40)
    parse_value = f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(parse_value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    normalized = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    return normalized.replace("+00:00", "Z")


def _nonnegative_int(value: object, field: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < 0 or value > maximum:
        raise ValueError(f"{field} is outside the supported range")
    return value


def _optional_nonnegative_int(
    value: object,
    field: str,
    *,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, field, maximum=maximum)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "SCHEMA_VERSION",
    "AccountMatchStatus",
    "ImportBatch",
    "ImportResult",
    "MatchFact",
    "MatchIndex",
    "MatchIndexConflict",
    "MatchIndexError",
    "MatchIndexHealth",
    "MatchIndexStorageFull",
    "MatchIndexUnavailable",
    "PurgeResult",
    "ReconcileResult",
    "RecordClassification",
    "SourceStateUpdate",
    "StoredMatch",
    "StoredSourceState",
]
