#!/usr/bin/env python3
"""Independent regression coverage for the isolated SQLite match index."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from match_index import (  # noqa: E402
    ImportBatch,
    MatchFact,
    MatchIndex,
    MatchIndexConflict,
    MatchIndexStorageFull,
    MatchIndexUnavailable,
    SourceStateUpdate,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _batch(value: str, *, imported_at: str | None = None) -> ImportBatch:
    return ImportBatch(
        source_kind="json_v1",
        source_digest=_digest(f"source:{value}"),
        schema_version="mrat.matches.v1",
        policy_version="local_owner_v1",
        authorization_basis="owner_attestation",
        imported_at=imported_at,
    )


def _fact(
    account_id: str,
    match_key: str,
    *,
    occurred_at: str = "2026-08-20T12:00:00Z",
    platform: str = "pc",
    evidence_kind: str = "direct",
    source_account_id: str | None = None,
    season: str | None = "season_3",
    mode: str | None = "competitive",
    hero: str | None = "Luna Snow",
    kills: int | None = 12,
) -> MatchFact:
    return MatchFact(
        account_id=account_id,
        match_key=match_key,
        occurred_at=occurred_at,
        platform=platform,
        evidence_kind=evidence_kind,
        source_record_digest=_digest(f"record:{account_id}:{match_key}"),
        season=season,
        mode=mode,
        map_name="Yggsgard",
        result="win",
        duration_seconds=612,
        hero=hero,
        kills=kills,
        deaths=4,
        assists=9,
        damage_dealt=15432,
        damage_taken=8765,
        healing_done=2222,
        rank_at_match="Diamond III",
        source_account_id=source_account_id,
    )


class MatchIndexTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "data" / "match-index.sqlite3"
        self.backup_dir = self.root / "backups"
        self.index = MatchIndex(self.db_path, self.backup_dir, busy_timeout_ms=10_000)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _table_count(self, table: str) -> int:
        with closing(sqlite3.connect(self.db_path)) as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class CreationAndMigrationTest(MatchIndexTestCase):
    def test_constructor_is_lazy_and_creation_is_idempotent(self) -> None:
        self.assertFalse(self.db_path.exists())
        self.assertEqual(self.index.health().schema_version, 0)
        self.assertFalse(self.db_path.exists())

        health = self.index.initialize()
        self.assertTrue(health.available)
        self.assertTrue(health.initialized)
        self.assertEqual(health.schema_version, 1)
        self.assertTrue(self.db_path.exists())
        self.assertFalse(self.backup_dir.exists())

        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue({
            "matches",
            "account_match_facts",
            "import_batches",
            "match_provenance",
            "match_source_state",
        }.issubset(tables))

        second = MatchIndex(self.db_path, self.backup_dir).initialize()
        self.assertTrue(second.available)
        self.assertEqual(list(self.backup_dir.glob("*.sqlite3")), [])

    def test_existing_version_zero_database_is_backed_up_before_migration(self) -> None:
        self.db_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO legacy_marker VALUES ('pre-migration')")
            connection.execute("PRAGMA user_version = 0")
            connection.commit()

        health = self.index.initialize()
        self.assertTrue(health.available, health)
        backups = list(self.backup_dir.glob("*-migration.sqlite3"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(
                backup.execute("SELECT value FROM legacy_marker").fetchone()[0],
                "pre-migration",
            )
        with closing(sqlite3.connect(self.db_path)) as current:
            self.assertEqual(current.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(
                current.execute("SELECT value FROM legacy_marker").fetchone()[0],
                "pre-migration",
            )

        # Initialization at the current schema does not create redundant backups.
        self.assertTrue(MatchIndex(self.db_path, self.backup_dir).initialize().available)
        self.assertEqual(len(list(self.backup_dir.glob("*-migration.sqlite3"))), 1)

    def test_required_backup_failure_aborts_migration_without_schema_or_data_changes(
        self,
    ) -> None:
        self.db_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO legacy_marker VALUES ('must-survive')")
            connection.execute("PRAGMA user_version = 0")
            connection.commit()
        # A regular file at the configured backup-directory path makes the
        # required online backup fail before _migrate can begin its transaction.
        self.backup_dir.write_text("not a directory", encoding="utf-8")

        health = self.index.initialize()

        self.assertFalse(health.available)
        self.assertFalse(health.initialized)
        self.assertEqual(health.error_code, "backup_failed")
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertEqual(tables, {"legacy_marker"})
            self.assertEqual(
                connection.execute("SELECT value FROM legacy_marker").fetchone()[0],
                "must-survive",
            )

    def test_schema_has_no_participant_identity_or_raw_payload_storage(self) -> None:
        self.assertTrue(self.index.initialize().available)
        with closing(sqlite3.connect(self.db_path)) as connection:
            schema = "\n".join(
                row[0]
                for row in connection.execute(
                    "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
                )
            ).lower()
            columns = {
                row[1].lower()
                for table in (
                    "matches",
                    "account_match_facts",
                    "import_batches",
                    "match_provenance",
                    "match_source_state",
                )
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
        for forbidden in ("participant", "player_name", "player_uid", "payload", "raw_json"):
            self.assertNotIn(forbidden, schema)
            self.assertNotIn(forbidden, columns)
        with self.assertRaises(TypeError):
            MatchFact(  # type: ignore[call-arg]
                account_id="owned-a",
                match_key="match-a",
                occurred_at="2026-08-20T12:00:00Z",
                platform="pc",
                evidence_kind="direct",
                source_record_digest=_digest("record-a"),
                participant_uid="123456789",
            )


class ImportAndQueryTest(MatchIndexTestCase):
    def test_import_deduplicates_and_keeps_provenance_and_state(self) -> None:
        direct = _fact("owned-a", "match-shared")
        inferred = _fact(
            "owned-b",
            "match-shared",
            platform="xbox",
            evidence_kind="inferred_owned_account",
            source_account_id="owned-a",
            hero="Rocket Raccoon",
            kills=7,
        )
        states = (
            SourceStateUpdate(
                "owned-a",
                "json_v1",
                "2026-08-21T00:00:00Z",
                last_success_at="2026-08-21T00:00:00Z",
            ),
            SourceStateUpdate(
                "owned-b",
                "json_v1",
                "2026-08-21T00:01:00Z",
                error_code="source_partial",
                retry_at="2026-08-21T01:01:00Z",
            ),
        )
        classification = self.index.classify_records((direct, inferred))
        self.assertEqual(classification.new_count, 2)
        self.assertEqual(classification.conflict_count, 0)

        first = self.index.import_records(
            _batch("first", imported_at="2026-08-21T00:02:00Z"),
            (direct, inferred),
            source_states=states,
            rejected_count=2,
        )
        self.assertEqual(first.accepted_count, 2)
        self.assertEqual(first.duplicate_count, 0)
        self.assertEqual(first.rejected_count, 2)
        self.assertFalse(first.duplicate_only)
        self.assertEqual(self._table_count("matches"), 1)
        self.assertEqual(self._table_count("account_match_facts"), 2)
        self.assertEqual(self.index.list_matches("owned-a")[0].platform, "pc")
        self.assertEqual(self.index.list_matches("owned-b")[0].platform, "xbox")

        after = self.index.classify_records((direct, inferred))
        self.assertEqual(after.duplicate_count, 2)
        self.assertEqual(after.duplicate_keys, (
            ("owned-a", "match-shared"),
            ("owned-b", "match-shared"),
        ))
        duplicate = self.index.import_records(
            _batch("duplicate", imported_at="2026-08-21T00:03:00Z"),
            (direct, inferred),
        )
        self.assertEqual(duplicate.accepted_count, 0)
        self.assertEqual(duplicate.duplicate_count, 2)
        self.assertTrue(duplicate.duplicate_only)
        self.assertEqual(self._table_count("matches"), 1)
        self.assertEqual(self._table_count("account_match_facts"), 2)
        self.assertEqual(self.index.list_matches("owned-a")[0].provenance_count, 2)

        status_a = self.index.status("owned-a")
        self.assertEqual(status_a.total_matches, 1)
        self.assertEqual(status_a.direct_matches, 1)
        self.assertEqual(status_a.inferred_matches, 0)
        self.assertEqual(status_a.last_success_at, "2026-08-21T00:00:00.000000Z")
        status_b = self.index.status("owned-b")
        self.assertEqual(status_b.inferred_matches, 1)
        self.assertEqual(status_b.last_error_code, "source_partial")
        self.assertEqual(status_b.retry_at, "2026-08-21T01:01:00.000000Z")

    def test_evidence_transitions_are_monotonic_across_batches(self) -> None:
        inferred = replace(
            _fact("owned-a", "match-inferred-first"),
            evidence_kind="inferred_owned_account",
            source_account_id="owned-source-a",
            source_record_digest=_digest("inferred-first"),
        )
        direct = replace(
            inferred,
            evidence_kind="direct",
            source_account_id=None,
            source_record_digest=_digest("direct-second"),
        )
        self.index.import_records(_batch("inferred-first"), (inferred,))

        classification = self.index.classify_records((direct,))
        self.assertEqual(classification.new_count, 0)
        self.assertEqual(classification.duplicate_count, 1)
        self.assertEqual(classification.conflict_count, 0)
        promoted = self.index.import_records(_batch("direct-second"), (direct,))
        self.assertEqual(promoted.accepted_count, 0)
        self.assertEqual(promoted.duplicate_count, 1)
        self.assertTrue(promoted.duplicate_only)
        stored = self.index.list_matches("owned-a")[0]
        self.assertEqual(stored.evidence_kind, "direct")
        self.assertEqual(stored.provenance_count, 2)

        direct_first = replace(
            _fact("owned-a", "match-direct-first"),
            source_record_digest=_digest("direct-first"),
        )
        inferred_second = replace(
            direct_first,
            evidence_kind="inferred_owned_account",
            source_account_id="owned-source-b",
            source_record_digest=_digest("inferred-second"),
        )
        self.index.import_records(_batch("direct-first"), (direct_first,))
        classification = self.index.classify_records((inferred_second,))
        self.assertEqual(classification.duplicate_count, 1)
        self.assertEqual(classification.conflict_count, 0)
        retained = self.index.import_records(
            _batch("inferred-second"), (inferred_second,)
        )
        self.assertEqual(retained.accepted_count, 0)
        self.assertEqual(retained.duplicate_count, 1)
        rows = {row.match_key: row for row in self.index.list_matches("owned-a")}
        self.assertEqual(rows["match-direct-first"].evidence_kind, "direct")
        self.assertEqual(rows["match-direct-first"].provenance_count, 2)

        with closing(sqlite3.connect(self.db_path)) as connection:
            sources = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT source_account_id FROM match_provenance
                    WHERE account_id = ? AND match_key = ?
                    """,
                    ("owned-a", "match-inferred-first"),
                )
            }
        self.assertEqual(sources, {None, "owned-source-a"})

        factual_conflict = replace(
            inferred_second,
            hero="Storm",
            source_record_digest=_digest("different-facts"),
        )
        conflict = self.index.classify_records((factual_conflict,))
        self.assertEqual(conflict.duplicate_count, 0)
        self.assertEqual(conflict.conflict_count, 1)

    def test_evidence_transitions_classify_in_both_orders_within_one_preview(self) -> None:
        inferred_first = replace(
            _fact("owned-a", "match-within-inferred-first"),
            evidence_kind="inferred_owned_account",
            source_account_id="owned-source-a",
            source_record_digest=_digest("within-inferred-first"),
        )
        direct_second = replace(
            inferred_first,
            evidence_kind="direct",
            source_account_id=None,
            source_record_digest=_digest("within-direct-second"),
        )
        direct_first = replace(
            _fact("owned-a", "match-within-direct-first"),
            source_record_digest=_digest("within-direct-first"),
        )
        inferred_second = replace(
            direct_first,
            evidence_kind="inferred_owned_account",
            source_account_id="owned-source-b",
            source_record_digest=_digest("within-inferred-second"),
        )
        records = (inferred_first, direct_second, direct_first, inferred_second)

        classification = self.index.classify_records(records)
        self.assertEqual(classification.new_count, 2)
        self.assertEqual(classification.duplicate_count, 2)
        self.assertEqual(classification.conflict_count, 0)
        # This sequence exercises the classifier's virtual within-preview
        # state. The allowlisted v1 adapters bind a batch to one source
        # identity, so they cannot emit both evidence kinds for the same
        # account/match in a real commit. Real transitions are covered above as
        # separate batches, where both provenance rows are retained.
        self.assertEqual(self._table_count("account_match_facts"), 0)
        self.assertEqual(self._table_count("import_batches"), 0)

    def test_classification_handles_duplicates_and_conflicts_within_preview(self) -> None:
        first = _fact("owned-a", "match-one")
        duplicate = replace(first, source_record_digest=_digest("same-facts-new-source"))
        conflicting_account = replace(first, kills=999)
        conflicting_match = _fact(
            "owned-b",
            "match-one",
            mode="quickplay",
        )
        result = self.index.classify_records(
            (first, duplicate, conflicting_account, conflicting_match)
        )
        self.assertEqual(result.new_count, 1)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.conflict_count, 2)
        self.assertEqual(result.conflict_keys, (
            ("owned-a", "match-one"),
            ("owned-b", "match-one"),
        ))

    def test_conflict_rolls_back_entire_batch_and_source_state(self) -> None:
        existing = _fact("owned-a", "match-existing")
        self.index.import_records(_batch("base"), (existing,))
        batches_before = self._table_count("import_batches")
        first_new = _fact("owned-b", "match-new")
        conflict = _fact(
            "owned-c",
            "match-existing",
            mode="different-mode",
        )
        with self.assertRaises(MatchIndexConflict) as raised:
            self.index.import_records(
                _batch("conflict"),
                (first_new, conflict),
                source_states=(SourceStateUpdate(
                    "owned-b",
                    "json_v1",
                    "2026-08-22T00:00:00Z",
                    error_code="should_rollback",
                ),),
            )
        self.assertEqual(raised.exception.code, "fact_conflict")
        self.assertFalse(self.index.has_history("owned-b"))
        self.assertEqual(self._table_count("import_batches"), batches_before)
        self.assertEqual(self.index.status("owned-b").source_states, ())

    def test_filters_and_pagination_are_exact_and_stable(self) -> None:
        facts = (
            _fact(
                "owned-a",
                "match-new-pc",
                occurred_at="2026-08-23T12:00:00Z",
                platform="pc",
                season="season_3",
            ),
            _fact(
                "owned-a",
                "match-old-xbox",
                occurred_at="2026-08-22T12:00:00Z",
                platform="xbox",
                season="season_2",
            ),
            _fact(
                "owned-a",
                "match-middle-pc",
                occurred_at="2026-08-23T11:00:00Z",
                platform="pc",
                season="season_3",
            ),
        )
        self.index.import_records(_batch("filters"), facts)
        self.assertEqual(
            [item.match_key for item in self.index.list_matches("owned-a", limit=2)],
            ["match-new-pc", "match-middle-pc"],
        )
        self.assertEqual(
            [item.match_key for item in self.index.list_matches(
                "owned-a", limit=2, offset=1
            )],
            ["match-middle-pc", "match-old-xbox"],
        )
        self.assertEqual(
            [item.match_key for item in self.index.list_matches(
                "owned-a", season="season_3", platform="PC"
            )],
            ["match-new-pc", "match-middle-pc"],
        )
        self.assertEqual(
            [item.match_key for item in self.index.list_matches(
                "owned-a", season="season_2", platform="xbox"
            )],
            ["match-old-xbox"],
        )
        with self.assertRaises(ValueError):
            self.index.list_matches("owned-a", platform="switch")


class BackupPurgeAndReconcileTest(MatchIndexTestCase):
    def test_required_backup_failure_aborts_purge_without_data_loss(self) -> None:
        self.index.import_records(_batch("backup-gate"), (_fact("owned-a", "match-a"),))
        # A regular file at the configured directory path forces the online
        # backup to fail before the purge transaction begins.
        self.backup_dir.write_text("not a directory", encoding="utf-8")

        with self.assertRaises(MatchIndexUnavailable) as raised:
            self.index.purge_account("owned-a")
        self.assertEqual(raised.exception.code, "backup_failed")
        self.assertTrue(self.index.has_history("owned-a"))
        self.assertEqual(self._table_count("matches"), 1)
        self.assertEqual(self._table_count("account_match_facts"), 1)

    def test_purge_backup_precedes_orphan_and_inference_cleanup(self) -> None:
        direct = _fact("owned-a", "match-shared")
        inferred = _fact(
            "owned-b",
            "match-shared",
            evidence_kind="inferred_owned_account",
            source_account_id="owned-a",
            hero="Magneto",
        )
        retained = _fact(
            "owned-c",
            "match-retained",
            occurred_at="2026-08-24T12:00:00Z",
        )
        self.index.import_records(
            _batch("purge"),
            (direct, inferred, retained),
            source_states=(SourceStateUpdate(
                "owned-a", "json_v1", "2026-08-24T13:00:00Z"
            ),),
        )

        result = self.index.purge_account("owned-a")
        self.assertTrue(result.backup_path.exists())
        self.assertEqual(result.facts_deleted, 1)
        self.assertEqual(result.dependent_facts_deleted, 1)
        self.assertEqual(result.matches_deleted, 1)
        self.assertFalse(self.index.has_history("owned-a"))
        self.assertFalse(self.index.has_history("owned-b"))
        self.assertTrue(self.index.has_history("owned-c"))

        # The online SQLite backup is independently readable and captures the
        # complete pre-purge state, including WAL-resident rows.
        restored = MatchIndex(result.backup_path, self.root / "restore-backups")
        self.assertTrue(restored.initialize().available)
        self.assertTrue(restored.has_history("owned-a"))
        self.assertTrue(restored.has_history("owned-b"))
        self.assertTrue(restored.has_history("owned-c"))

    def test_closed_database_can_be_replaced_with_backup_to_restore_facts(self) -> None:
        original = _fact("owned-a", "match-before-backup")
        later = _fact(
            "owned-b",
            "match-after-backup",
            occurred_at="2026-08-25T12:00:00Z",
        )
        self.index.import_records(_batch("restore-base"), (original,))
        restore_point = self.index.backup(purpose="operational_restore")

        self.index.purge_account("owned-a")
        self.index.import_records(_batch("restore-later"), (later,))
        self.assertFalse(self.index.has_history("owned-a"))
        self.assertTrue(self.index.has_history("owned-b"))

        # Every MatchIndex operation has closed its own connection at this
        # point. Model the documented offline restore: discard live WAL/SHM,
        # replace the main file with the consistent Connection.backup() image,
        # then start a fresh MatchIndex facade.
        for suffix in ("-wal", "-shm"):
            Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)
        self.db_path.unlink()
        shutil.copy2(restore_point, self.db_path)
        restored = MatchIndex(self.db_path, self.root / "post-restore-backups")

        self.assertTrue(restored.initialize().available)
        self.assertTrue(restored.has_history("owned-a"))
        self.assertFalse(restored.has_history("owned-b"))
        self.assertEqual(
            [row.match_key for row in restored.list_matches("owned-a")],
            ["match-before-backup"],
        )

    def test_reconcile_removes_invalid_targets_and_invalid_inference_sources(self) -> None:
        direct = _fact("owned-a", "match-shared")
        inferred = _fact(
            "owned-b",
            "match-shared",
            evidence_kind="inferred_owned_account",
            source_account_id="owned-a",
        )
        self.index.import_records(_batch("reconcile"), (direct, inferred))

        result = self.index.reconcile_accounts(("owned-b",))
        self.assertIsNotNone(result.backup_path)
        self.assertTrue(result.backup_path.exists())
        self.assertEqual(result.facts_deleted, 1)
        self.assertEqual(result.dependent_facts_deleted, 1)
        self.assertFalse(self.index.has_history("owned-a"))
        self.assertFalse(self.index.has_history("owned-b"))
        self.assertEqual(self._table_count("matches"), 0)
        self.assertEqual(self._table_count("import_batches"), 0)

        backups_before = tuple(self.backup_dir.glob("*-reconcile.sqlite3"))
        no_op = self.index.reconcile_accounts(("owned-b",))
        self.assertIsNone(no_op.backup_path)
        self.assertEqual(tuple(self.backup_dir.glob("*-reconcile.sqlite3")), backups_before)


class IsolationAndConcurrencyTest(MatchIndexTestCase):
    def test_corrupt_database_is_reported_without_replacement(self) -> None:
        self.db_path.parent.mkdir(parents=True)
        corrupt = b"this is intentionally not a sqlite database\x00private-marker"
        self.db_path.write_bytes(corrupt)

        health = self.index.health()
        self.assertFalse(health.available)
        self.assertEqual(health.error_code, "corrupt_database")
        self.assertEqual(self.db_path.read_bytes(), corrupt)
        initialized = self.index.initialize()
        self.assertFalse(initialized.available)
        self.assertEqual(initialized.error_code, "corrupt_database")
        self.assertEqual(self.db_path.read_bytes(), corrupt)
        with self.assertRaises(MatchIndexUnavailable) as raised:
            self.index.status("owned-a")
        self.assertEqual(raised.exception.code, "corrupt_database")
        self.assertEqual(self.db_path.read_bytes(), corrupt)

    def test_storage_full_error_has_stable_exception_type(self) -> None:
        failure = MatchIndex._as_match_error(  # noqa: SLF001 - classification contract
            sqlite3.OperationalError("database or disk is full")
        )
        self.assertIsInstance(failure, MatchIndexStorageFull)
        self.assertEqual(failure.code, "storage_full")

    def test_storage_full_after_import_writes_rolls_back_every_table(self) -> None:
        baseline = _fact("owned-a", "match-baseline")
        self.index.import_records(
            _batch("storage-baseline"),
            (baseline,),
            source_states=(SourceStateUpdate(
                "owned-a",
                "json_v1",
                "2026-08-25T00:00:00Z",
                last_success_at="2026-08-25T00:00:00Z",
            ),),
        )
        tables = (
            "matches",
            "account_match_facts",
            "import_batches",
            "match_provenance",
            "match_source_state",
        )
        counts_before = {table: self._table_count(table) for table in tables}
        original_insert = self.index._insert_import  # noqa: SLF001 - transaction injection

        def insert_then_fill_disk(*args, **kwargs):
            original_insert(*args, **kwargs)
            raise sqlite3.OperationalError("database or disk is full")

        attempted = _fact("owned-b", "match-must-rollback")
        with patch.object(
            self.index,
            "_insert_import",
            side_effect=insert_then_fill_disk,
        ):
            with self.assertRaises(MatchIndexStorageFull) as raised:
                self.index.import_records(
                    _batch("storage-full"),
                    (attempted,),
                    source_states=(SourceStateUpdate(
                        "owned-b",
                        "json_v1",
                        "2026-08-25T01:00:00Z",
                        last_success_at="2026-08-25T01:00:00Z",
                    ),),
                    rejected_count=3,
                )

        # MatchIndexStorageFull is the stable exception class mapped to HTTP
        # 507 by the application boundary; raw SQLite text never escapes.
        self.assertEqual(raised.exception.code, "storage_full")
        self.assertEqual(
            {table: self._table_count(table) for table in tables},
            counts_before,
        )
        self.assertTrue(self.index.has_history("owned-a"))
        self.assertFalse(self.index.has_history("owned-b"))
        self.assertEqual(self.index.status("owned-b").source_states, ())

    def test_concurrent_readers_and_writers_share_no_connections(self) -> None:
        self.assertTrue(self.index.initialize().available)
        facts = [
            _fact(
                "owned-a",
                f"match-{number:03d}",
                occurred_at=f"2026-08-{(number % 20) + 1:02d}T12:00:00Z",
                kills=number,
            )
            for number in range(40)
        ]

        def write_one(number: int) -> None:
            result = self.index.import_records(_batch(f"thread-{number}"), (facts[number],))
            self.assertEqual(result.accepted_count, 1)

        def read_repeatedly() -> None:
            for _ in range(30):
                status = self.index.status("owned-a")
                self.assertGreaterEqual(status.total_matches, 0)
                self.index.list_matches("owned-a", limit=10)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(write_one, number) for number in range(len(facts))]
            futures.extend(executor.submit(read_repeatedly) for _ in range(5))
            for future in as_completed(futures):
                future.result()

        self.assertEqual(self.index.status("owned-a").total_matches, len(facts))
        self.assertEqual(self._table_count("matches"), len(facts))
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
