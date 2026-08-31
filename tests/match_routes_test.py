#!/usr/bin/env python3
"""End-to-end route/lifecycle tests for owner-authorized match history.

The application module is loaded under a test-only name after pointing
``MARVEL_KEEPER_DATA`` at a temporary directory.  No test in this file can
discover or mutate the user's real vault.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TEST_ROOT_HANDLE = tempfile.TemporaryDirectory()
TEST_ROOT = Path(_TEST_ROOT_HANDLE.name)
os.environ["MARVEL_KEEPER_DATA"] = str(TEST_ROOT)
(TEST_ROOT / "vault.json").write_text(
    json.dumps({
        "initialized": False,
        "config": {"lockout_minutes": 30},
        "accounts": [],
    }),
    encoding="utf-8",
)

_APP_SPEC = importlib.util.spec_from_file_location(
    "_match_routes_app",
    ROOT / "app.py",
)
if _APP_SPEC is None or _APP_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("could not load app.py for match-route tests")
tracker_app = importlib.util.module_from_spec(_APP_SPEC)
sys.modules[_APP_SPEC.name] = tracker_app
_APP_SPEC.loader.exec_module(tracker_app)

# app.py's normal secondary backup points into Documents.  Tests keep both
# vault backup destinations inside the same disposable root.
tracker_app.EXTRA_BACKUP_DIR = TEST_ROOT / "extra-vault-backups"
tracker_app.app.config.update(TESTING=True)


SOURCE_UID = "731946285"
INFERRED_UID = "846251397"
BYSTANDER_UID = "965318742"
SOURCE_PLAYER_NAME = "Route Test Source Player"
INFERRED_PLAYER_NAME = "Route Test Inferred Player"
BYSTANDER_PLAYER_NAME = "Route Test Unrelated Player"
MASTER_KEY = b"R" * 32
ORIGIN_HEADERS = {"Origin": "http://127.0.0.1:27123"}


def _participant(
    uid: str,
    platform: str,
    name: str,
    *,
    hero: str,
    stats: dict[str, int] | None = None,
) -> dict[str, object]:
    return {
        "uid": uid,
        "platform": platform,
        "name": name,
        "hero": hero,
        "rank": "Diamond II",
        "stats": stats or {"kills": 12, "assists": 7},
    }


def _match(match_id: str = "route-match-001", **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "match_id": match_id,
        "started_at": "2026-08-29T22:30:00Z",
        "season": "season-4",
        "mode": "competitive",
        "map": "Tokyo 2099",
        "result": "win",
        "duration_seconds": 611,
        "participants_complete": True,
        "participants": [
            _participant(
                SOURCE_UID,
                "pc",
                SOURCE_PLAYER_NAME,
                hero="Storm",
                stats={"kills": 17, "damage_dealt": 12345},
            ),
            _participant(
                INFERRED_UID,
                "xbox",
                INFERRED_PLAYER_NAME,
                hero="Loki",
                stats={"assists": 22, "healing_done": 8000},
            ),
            _participant(
                BYSTANDER_UID,
                "playstation",
                BYSTANDER_PLAYER_NAME,
                hero="Groot",
                stats={"damage_taken": 9999},
            ),
        ],
    }
    value.update(changes)
    return value


def _document(
    matches: list[dict[str, object]] | None = None,
    *,
    source_uid: str = SOURCE_UID,
    source_platform: str = "pc",
    schema: str = "mrat.matches.v1",
) -> dict[str, object]:
    return {
        "schema": schema,
        "source": {"uid": source_uid, "platform": source_platform},
        "matches": [_match()] if matches is None else matches,
    }


def _json_bytes(value: dict[str, object] | None = None) -> bytes:
    return json.dumps(
        _document() if value is None else value,
        separators=(",", ":"),
    ).encode("utf-8")


class MatchRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._clear_test_data()
        tracker_app.EXTRA_BACKUP_DIR = TEST_ROOT / "extra-vault-backups"
        tracker_app._match_index_instance = None
        with tracker_app._state_lock:
            tracker_app._state["key"] = MASTER_KEY
            tracker_app._state["last_activity"] = time.time()
        with tracker_app._tracker_rate_limit_lock:
            tracker_app._tracker_rate_limited_until = 0.0
        tracker_app.VAULT_PATH.write_text(
            json.dumps({
                "initialized": True,
                "config": {"lockout_minutes": 0},
                "accounts": [],
            }),
            encoding="utf-8",
        )
        self.client = tracker_app.app.test_client()

    def tearDown(self) -> None:
        with tracker_app._state_lock:
            tracker_app._state["key"] = None
        tracker_app._match_index_instance = None

    @staticmethod
    def _clear_test_data() -> None:
        for directory in (
            tracker_app.BACKUP_DIR,
            tracker_app.MATCH_BACKUP_DIR,
            TEST_ROOT / "extra-vault-backups",
        ):
            if directory.exists():
                shutil.rmtree(directory)
        for suffix in ("", "-wal", "-shm"):
            Path(f"{tracker_app.MATCH_INDEX_PATH}{suffix}").unlink(missing_ok=True)
        tracker_app.VAULT_PATH.unlink(missing_ok=True)

    def _set_locked(self) -> None:
        with tracker_app._state_lock:
            tracker_app._state["key"] = None

    def _create_account(
        self,
        *,
        uid: str | None = SOURCE_UID,
        platform: str = "pc",
        authorized: bool = True,
        name: str = SOURCE_PLAYER_NAME,
    ) -> str:
        response = self.client.post(
            "/api/accounts",
            json={
                "in_game_name": name,
                "username": f"user-{name}",
                "email": "",
                "password": "",
                "rivals_uid": uid or "",
                "rivals_platform": platform,
                "match_history_authorized": authorized,
            },
            headers=ORIGIN_HEADERS,
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["id"]

    def _create_owned_pair(self) -> tuple[str, str]:
        source_id = self._create_account()
        inferred_id = self._create_account(
            uid=INFERRED_UID,
            platform="xbox",
            name=INFERRED_PLAYER_NAME,
        )
        return source_id, inferred_id

    def _upload(
        self,
        route: str,
        raw: bytes,
        *,
        expected_digest: str | None = None,
        expected_scope_digest: str | None = None,
        authorized: bool = True,
        filename: str = "matches.json",
    ):
        form: dict[str, object] = {
            "authorized": "true" if authorized else "false",
            "format": "json",
            "file": (io.BytesIO(raw), filename),
        }
        if expected_digest is not None:
            form["expected_digest"] = expected_digest
        if expected_scope_digest is not None:
            form["expected_scope_digest"] = expected_scope_digest
        return self.client.post(
            route,
            data=form,
            content_type="multipart/form-data",
            headers=ORIGIN_HEADERS,
        )

    def _preview(self, account_id: str, raw: bytes):
        return self._upload(
            f"/api/accounts/{account_id}/matches/import/preview",
            raw,
        )

    def _commit(
        self, account_id: str, raw: bytes, digest: str, scope_digest: str
    ):
        return self._upload(
            f"/api/accounts/{account_id}/matches/import",
            raw,
            expected_digest=digest,
            expected_scope_digest=scope_digest,
        )

    def _vault_accounts(self) -> list[dict[str, object]]:
        return tracker_app._read_vault().get("accounts", [])

    @staticmethod
    def _db_count(table: str) -> int:
        with closing(sqlite3.connect(tracker_app.MATCH_INDEX_PATH)) as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def _import_standard_match(self, source_id: str, raw: bytes | None = None):
        payload = raw or _json_bytes()
        preview = self._preview(source_id, payload)
        self.assertEqual(preview.status_code, 200, preview.get_json())
        preview_value = preview.get_json()["preview"]
        commit = self._commit(
            source_id,
            payload,
            preview_value["source_digest"],
            preview_value["scope_digest"],
        )
        self.assertEqual(commit.status_code, 200, commit.get_json())
        return commit

    def test_all_match_routes_require_unlocked_vault(self) -> None:
        self._set_locked()
        account_id = "locked-account"
        cases = (
            ("get", f"/api/accounts/{account_id}/matches", {}),
            ("get", f"/api/accounts/{account_id}/matches/status", {}),
            (
                "post",
                f"/api/accounts/{account_id}/matches/import/preview",
                {"json": {"authorized": True, "manual": {}}},
            ),
            (
                "post",
                f"/api/accounts/{account_id}/matches/import",
                {"json": {"authorized": True, "manual": {}}},
            ),
            ("delete", f"/api/accounts/{account_id}/matches", {}),
        )
        for method, path, kwargs in cases:
            with self.subTest(method=method, path=path):
                response = getattr(self.client, method)(
                    path,
                    headers=ORIGIN_HEADERS,
                    **kwargs,
                )
                self.assertEqual(response.status_code, 401, response.get_json())

    def test_import_requires_explicit_account_authorization(self) -> None:
        account_id = self._create_account(authorized=False)
        body = {"authorized": True, "manual": {}}
        for suffix in ("preview", ""):
            route = f"/api/accounts/{account_id}/matches/import"
            if suffix:
                route += f"/{suffix}"
            with self.subTest(route=route):
                response = self.client.post(route, json=body, headers=ORIGIN_HEADERS)
                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(
                    response.get_json()["error"],
                    "source_authorization_required",
                )

    def test_match_history_consent_is_fail_closed_for_non_boolean_json(self) -> None:
        response = self.client.post(
            "/api/accounts",
            json={
                "in_game_name": "Malformed Consent",
                "rivals_uid": SOURCE_UID,
                "rivals_platform": "pc",
                "match_history_authorized": "true",
            },
            headers=ORIGIN_HEADERS,
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        account = next(
            item for item in self._vault_accounts()
            if item["id"] == response.get_json()["id"]
        )
        self.assertFalse(account["match_history_authorized"])

    def test_account_uid_rejects_non_digits_instead_of_rewriting_identity(self) -> None:
        invalid = self.client.post(
            "/api/accounts",
            json={
                "in_game_name": "Invalid UID",
                "rivals_uid": "abc123-456xyz",
                "rivals_platform": "pc",
                "match_history_authorized": True,
            },
            headers=ORIGIN_HEADERS,
        )
        self.assertEqual(invalid.status_code, 422, invalid.get_json())
        self.assertEqual(invalid.get_json()["error"], "invalid_rivals_uid")
        self.assertEqual(invalid.get_json()["path"], "$.rivals_uid")
        self.assertEqual(self._vault_accounts(), [])

        valid = self.client.post(
            "/api/accounts",
            json={
                "in_game_name": "Trimmed UID",
                "rivals_uid": f"  {SOURCE_UID}  ",
                "rivals_platform": "pc",
                "match_history_authorized": True,
            },
            headers=ORIGIN_HEADERS,
        )
        self.assertEqual(valid.status_code, 200, valid.get_json())
        account_id = valid.get_json()["id"]
        account = next(
            item for item in self._vault_accounts() if item["id"] == account_id
        )
        self.assertEqual(account["rivals_uid"], SOURCE_UID)

        rejected_update = self.client.put(
            f"/api/accounts/{account_id}",
            json={"rivals_uid": "UID-112233445"},
            headers=ORIGIN_HEADERS,
        )
        self.assertEqual(rejected_update.status_code, 422, rejected_update.get_json())
        self.assertEqual(rejected_update.get_json()["error"], "invalid_rivals_uid")
        account = next(
            item for item in self._vault_accounts() if item["id"] == account_id
        )
        self.assertEqual(account["rivals_uid"], SOURCE_UID)

    def test_incomplete_identity_mismatch_and_bad_schema_are_422(self) -> None:
        missing_uid = self._create_account(uid=None, name="Missing UID")
        response = self.client.post(
            f"/api/accounts/{missing_uid}/matches/import/preview",
            json={"authorized": True, "manual": {}},
            headers=ORIGIN_HEADERS,
        )
        self.assertEqual(response.status_code, 422, response.get_json())
        self.assertEqual(response.get_json()["error"], "account_uid_required")

        missing_platform = self._create_account(
            platform="unknown",
            name="Missing Platform",
        )
        response = self.client.post(
            f"/api/accounts/{missing_platform}/matches/import/preview",
            json={"authorized": True, "manual": {}},
            headers=ORIGIN_HEADERS,
        )
        self.assertEqual(response.status_code, 422, response.get_json())
        self.assertEqual(response.get_json()["error"], "account_platform_required")

        source_id = self._create_account(name="Mismatch Source")
        mismatch = _json_bytes(_document(source_uid="112233445"))
        response = self._preview(source_id, mismatch)
        self.assertEqual(response.status_code, 422, response.get_json())
        self.assertEqual(response.get_json()["error"], "identity_mismatch")

        bad_schema = _json_bytes(_document(schema="mrat.matches.v999"))
        response = self._preview(source_id, bad_schema)
        self.assertEqual(response.status_code, 422, response.get_json())
        self.assertEqual(response.get_json()["error"], "unsupported_schema")

    def test_upload_limit_accepts_exact_boundary_and_rejects_one_byte_more(self) -> None:
        source_id = self._create_account()
        base = _json_bytes(_document(matches=[_match("boundary-match")]))
        maximum = tracker_app.MATCH_MAX_UPLOAD_BYTES
        self.assertLess(len(base), maximum)
        exact = base + (b" " * (maximum - len(base)))
        response = self._preview(source_id, exact)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            response.get_json()["preview"]["source_digest"],
            hashlib.sha256(exact).hexdigest(),
        )

        response = self._preview(source_id, exact + b" ")
        self.assertEqual(response.status_code, 413, response.get_json())
        self.assertEqual(response.get_json()["error"], "upload_too_large")

    def test_preview_is_non_mutating(self) -> None:
        source_id, _inferred_id = self._create_owned_pair()
        raw = _json_bytes()
        response = self._preview(source_id, raw)
        self.assertEqual(response.status_code, 200, response.get_json())
        preview = response.get_json()["preview"]
        self.assertEqual(preview["accepted_count"], 2)
        self.assertEqual(preview["direct_count"], 1)
        self.assertEqual(preview["inferred_count"], 1)
        self.assertFalse(tracker_app.MATCH_INDEX_PATH.exists())

    def test_commit_scope_is_bound_to_previewed_owned_accounts(self) -> None:
        source_id = self._create_account()
        raw = _json_bytes()
        preview_response = self._preview(source_id, raw)
        self.assertEqual(preview_response.status_code, 200, preview_response.get_json())
        preview = preview_response.get_json()["preview"]
        self.assertEqual(preview["direct_count"], 1)
        self.assertEqual(preview["inferred_count"], 0)

        missing_scope = self._upload(
            f"/api/accounts/{source_id}/matches/import",
            raw,
            expected_digest=preview["source_digest"],
        )
        self.assertEqual(missing_scope.status_code, 422, missing_scope.get_json())
        self.assertEqual(
            missing_scope.get_json()["error"], "invalid_expected_scope_digest"
        )

        self._create_account(
            uid=BYSTANDER_UID,
            platform="playstation",
            name=BYSTANDER_PLAYER_NAME,
        )
        changed_scope = self._commit(
            source_id,
            raw,
            preview["source_digest"],
            preview["scope_digest"],
        )
        self.assertEqual(changed_scope.status_code, 409, changed_scope.get_json())
        self.assertEqual(changed_scope.get_json()["error"], "preview_scope_mismatch")
        self.assertFalse(tracker_app.MATCH_INDEX_PATH.exists())

        refreshed = self._preview(source_id, raw)
        self.assertEqual(refreshed.status_code, 200, refreshed.get_json())
        self.assertEqual(refreshed.get_json()["preview"]["inferred_count"], 1)

    def test_digest_bound_commit_direct_inferred_and_duplicate_only(self) -> None:
        source_id, inferred_id = self._create_owned_pair()
        raw = _json_bytes()
        preview_response = self._preview(source_id, raw)
        self.assertEqual(preview_response.status_code, 200, preview_response.get_json())
        preview = preview_response.get_json()["preview"]

        mismatch = self._commit(source_id, raw, "0" * 64, preview["scope_digest"])
        self.assertEqual(mismatch.status_code, 409, mismatch.get_json())
        self.assertEqual(mismatch.get_json()["error"], "source_digest_mismatch")
        self.assertFalse(tracker_app.MATCH_INDEX_PATH.exists())

        committed = self._commit(
            source_id, raw, preview["source_digest"], preview["scope_digest"]
        )
        self.assertEqual(committed.status_code, 200, committed.get_json())
        result = committed.get_json()
        self.assertEqual(result["result"], "imported")
        self.assertEqual(result["summary"]["inserted_count"], 2)
        self.assertEqual(result["summary"]["direct_count"], 1)
        self.assertEqual(result["summary"]["inferred_count"], 1)
        self.assertEqual(self._db_count("matches"), 1)
        self.assertEqual(self._db_count("account_match_facts"), 2)

        inferred = self.client.get(f"/api/accounts/{inferred_id}/matches")
        self.assertEqual(inferred.status_code, 200, inferred.get_json())
        self.assertEqual(inferred.get_json()["total"], 1)
        self.assertEqual(
            inferred.get_json()["matches"][0]["evidence_kind"],
            "inferred_owned_account",
        )
        self.assertEqual(inferred.get_json()["matches"][0]["platform"], "xbox")

        duplicate_preview_response = self._preview(source_id, raw)
        self.assertEqual(
            duplicate_preview_response.status_code,
            200,
            duplicate_preview_response.get_json(),
        )
        duplicate_preview = duplicate_preview_response.get_json()["preview"]
        self.assertEqual(duplicate_preview["state"], "duplicate_only")
        duplicate = self._commit(
            source_id,
            raw,
            duplicate_preview["source_digest"],
            duplicate_preview["scope_digest"],
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.get_json())
        self.assertEqual(duplicate.get_json()["result"], "duplicate_only")
        self.assertEqual(duplicate.get_json()["summary"]["inserted_count"], 0)
        self.assertEqual(duplicate.get_json()["summary"]["duplicate_count"], 2)
        self.assertEqual(self._db_count("matches"), 1)
        self.assertEqual(self._db_count("account_match_facts"), 2)

    def test_second_owned_source_upgrades_evidence_without_conflict(self) -> None:
        source_id, inferred_id = self._create_owned_pair()
        self._import_standard_match(source_id)

        second_source_raw = _json_bytes(_document(
            source_uid=INFERRED_UID,
            source_platform="xbox",
        ))
        preview_response = self._preview(inferred_id, second_source_raw)
        self.assertEqual(preview_response.status_code, 200, preview_response.get_json())
        preview = preview_response.get_json()["preview"]
        self.assertEqual(preview["state"], "duplicate_only")
        self.assertEqual(preview["conflict_count"], 0)
        self.assertEqual(preview["duplicate_fact_count"], 2)

        committed = self._commit(
            inferred_id,
            second_source_raw,
            preview["source_digest"],
            preview["scope_digest"],
        )
        self.assertEqual(committed.status_code, 200, committed.get_json())
        self.assertEqual(committed.get_json()["result"], "duplicate_only")

        source_matches = self.client.get(f"/api/accounts/{source_id}/matches")
        inferred_matches = self.client.get(f"/api/accounts/{inferred_id}/matches")
        self.assertEqual(
            source_matches.get_json()["matches"][0]["evidence_kind"], "direct"
        )
        self.assertEqual(
            inferred_matches.get_json()["matches"][0]["evidence_kind"], "direct"
        )
        self.assertEqual(
            inferred_matches.get_json()["matches"][0]["provenance_count"], 2
        )

    def test_partial_import_commits_valid_matches_and_reports_rejections(self) -> None:
        source_id, _inferred_id = self._create_owned_pair()
        invalid = _match("invalid-incomplete", participants_complete=False)
        raw = _json_bytes(_document(matches=[_match("valid-partial"), invalid]))
        preview_response = self._preview(source_id, raw)
        self.assertEqual(preview_response.status_code, 200, preview_response.get_json())
        preview = preview_response.get_json()["preview"]
        self.assertEqual(preview["state"], "partial_import")
        self.assertEqual(preview["rejected_count"], 1)

        committed = self._commit(
            source_id, raw, preview["source_digest"], preview["scope_digest"]
        )
        self.assertEqual(committed.status_code, 200, committed.get_json())
        summary = committed.get_json()["summary"]
        self.assertEqual(committed.get_json()["result"], "partial_import")
        self.assertEqual(summary["inserted_count"], 2)
        self.assertEqual(summary["rejected_count"], 1)
        self.assertEqual(summary["rejections"][0]["code"], "incomplete_participants")
        status = self.client.get(f"/api/accounts/{source_id}/matches/status")
        self.assertEqual(status.status_code, 200, status.get_json())
        self.assertEqual(status.get_json()["status"]["last_error"], "partial_import")

    def test_duplicate_plus_rejected_batch_remains_partial_import(self) -> None:
        source_id, _inferred_id = self._create_owned_pair()
        self._import_standard_match(source_id)
        invalid = _match("invalid-incomplete", participants_complete=False)
        raw = _json_bytes(_document(matches=[_match(), invalid]))

        preview_response = self._preview(source_id, raw)
        self.assertEqual(preview_response.status_code, 200, preview_response.get_json())
        preview = preview_response.get_json()["preview"]
        self.assertEqual(preview["state"], "partial_import")
        self.assertEqual(preview["new_fact_count"], 0)
        self.assertEqual(preview["duplicate_fact_count"], 2)
        self.assertEqual(preview["rejected_count"], 1)

        committed = self._commit(
            source_id, raw, preview["source_digest"], preview["scope_digest"]
        )
        self.assertEqual(committed.status_code, 200, committed.get_json())
        summary = committed.get_json()["summary"]
        self.assertEqual(committed.get_json()["result"], "partial_import")
        self.assertEqual(summary["inserted_fact_count"], 0)
        self.assertEqual(summary["duplicate_fact_count"], 2)
        self.assertEqual(summary["rejected_count"], 1)
        status = self.client.get(f"/api/accounts/{source_id}/matches/status")
        self.assertEqual(status.get_json()["status"]["last_error"], "partial_import")

    def test_match_list_exposes_stable_server_pagination(self) -> None:
        source_id, _inferred_id = self._create_owned_pair()
        second = _match(
            "route-match-002",
            started_at="2026-08-29T21:30:00Z",
            season="season-3",
        )
        raw = _json_bytes(_document(matches=[_match(), second]))
        self._import_standard_match(source_id, raw)

        first = self.client.get(
            f"/api/accounts/{source_id}/matches?limit=1&offset=0"
        )
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(len(first.get_json()["matches"]), 1)
        self.assertEqual(first.get_json()["total"], 2)
        self.assertTrue(first.get_json()["has_more"])

        second_page = self.client.get(
            f"/api/accounts/{source_id}/matches?limit=1&offset=1"
        )
        self.assertEqual(second_page.status_code, 200, second_page.get_json())
        self.assertEqual(len(second_page.get_json()["matches"]), 1)
        self.assertFalse(second_page.get_json()["has_more"])

        filtered_only_page = self.client.get(
            f"/api/accounts/{source_id}/matches?limit=1&offset=0&season=season-4"
        )
        self.assertEqual(
            filtered_only_page.status_code, 200, filtered_only_page.get_json()
        )
        self.assertEqual(len(filtered_only_page.get_json()["matches"]), 1)
        self.assertFalse(filtered_only_page.get_json()["has_more"])

        one_match_season = self.client.get(
            f"/api/accounts/{source_id}/matches?limit=1&offset=0&season=season-single"
        )
        self.assertEqual(one_match_season.status_code, 200, one_match_season.get_json())
        self.assertEqual(len(one_match_season.get_json()["matches"]), 0)
        self.assertFalse(one_match_season.get_json()["has_more"])

    def test_sqlite_contains_no_uids_player_names_or_raw_payload(self) -> None:
        source_id, _inferred_id = self._create_owned_pair()
        self._import_standard_match(source_id)

        with closing(sqlite3.connect(tracker_app.MATCH_INDEX_PATH)) as connection:
            schema = "\n".join(
                row[0]
                for row in connection.execute(
                    "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
                )
            ).lower()
            persisted = []
            for table in (
                "matches",
                "account_match_facts",
                "import_batches",
                "match_provenance",
                "match_source_state",
            ):
                persisted.extend(connection.execute(f"SELECT * FROM {table}").fetchall())
        serialized = repr(persisted)
        for forbidden in (
            SOURCE_UID,
            INFERRED_UID,
            BYSTANDER_UID,
            SOURCE_PLAYER_NAME,
            INFERRED_PLAYER_NAME,
            BYSTANDER_PLAYER_NAME,
            "route-match-001",
        ):
            self.assertNotIn(forbidden, serialized)
        for forbidden_column in (
            "participant_uid",
            "participant_name",
            "player_uid",
            "player_name",
            "raw_payload",
            "raw_json",
        ):
            self.assertNotIn(forbidden_column, schema)

        # Check WAL-resident pages as well as the main file.  None may contain
        # the discarded participant strings because they never cross the
        # parser/storage projection.
        sqlite_bytes = b"".join(
            candidate.read_bytes()
            for candidate in (
                tracker_app.MATCH_INDEX_PATH,
                Path(f"{tracker_app.MATCH_INDEX_PATH}-wal"),
            )
            if candidate.exists()
        )
        for forbidden in (
            SOURCE_UID,
            INFERRED_UID,
            BYSTANDER_UID,
            SOURCE_PLAYER_NAME,
            INFERRED_PLAYER_NAME,
            BYSTANDER_PLAYER_NAME,
        ):
            self.assertNotIn(forbidden.encode("utf-8"), sqlite_bytes)

    def test_identity_changes_conflict_until_history_is_cleared(self) -> None:
        source_id, _inferred_id = self._create_owned_pair()
        self._import_standard_match(source_id)

        changes = (
            {"rivals_uid": "112233445"},
            {"rivals_platform": "playstation"},
            {"match_history_authorized": False},
        )
        for payload in changes:
            with self.subTest(payload=payload):
                response = self.client.put(
                    f"/api/accounts/{source_id}",
                    json=payload,
                    headers=ORIGIN_HEADERS,
                )
                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(response.get_json()["error"], "match_identity_conflict")

        cleared = self.client.delete(
            f"/api/accounts/{source_id}/matches",
            headers=ORIGIN_HEADERS,
        )
        self.assertEqual(cleared.status_code, 200, cleared.get_json())
        self.assertEqual(cleared.get_json()["deleted"], 1)
        self.assertEqual(cleared.get_json()["dependent_deleted"], 1)
        self.assertTrue(list(tracker_app.MATCH_BACKUP_DIR.glob("*-purge.sqlite3")))

        updated = self.client.put(
            f"/api/accounts/{source_id}",
            json={
                "rivals_uid": "112233445",
                "rivals_platform": "playstation",
                "match_history_authorized": False,
            },
            headers=ORIGIN_HEADERS,
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        account = next(item for item in self._vault_accounts() if item["id"] == source_id)
        self.assertEqual(account["rivals_uid"], "112233445")
        self.assertEqual(account["rivals_platform"], "playstation")
        self.assertFalse(account["match_history_authorized"])

    def test_account_delete_purges_history_and_creates_predelete_backup(self) -> None:
        source_id, inferred_id = self._create_owned_pair()
        self._import_standard_match(source_id)

        deleted = self.client.delete(
            f"/api/accounts/{source_id}",
            headers=ORIGIN_HEADERS,
        )
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertNotIn(source_id, {item["id"] for item in self._vault_accounts()})
        self.assertIn(inferred_id, {item["id"] for item in self._vault_accounts()})
        self.assertEqual(self._db_count("matches"), 0)
        self.assertEqual(self._db_count("account_match_facts"), 0)

        backups = list(tracker_app.MATCH_BACKUP_DIR.glob("*-purge.sqlite3"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertEqual(
                backup.execute("SELECT COUNT(*) FROM account_match_facts").fetchone()[0],
                2,
            )

    def test_predelete_backup_failure_aborts_account_delete_and_preserves_both_stores(self) -> None:
        source_id, _inferred_id = self._create_owned_pair()
        self._import_standard_match(source_id)
        index = tracker_app._match_index()
        failure = tracker_app.MatchIndexUnavailable(
            "backup_failed",
            "A required match index backup could not be created.",
        )
        with patch.object(index, "backup", side_effect=failure):
            response = self.client.delete(
                f"/api/accounts/{source_id}",
                headers=ORIGIN_HEADERS,
            )
        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertIn(source_id, {item["id"] for item in self._vault_accounts()})
        self.assertTrue(index.has_history(source_id))

    def test_vault_write_failure_after_predelete_backup_preserves_live_history(self) -> None:
        source_id, _inferred_id = self._create_owned_pair()
        self._import_standard_match(source_id)
        index = tracker_app._match_index()

        with patch.object(
            tracker_app,
            "_write_vault",
            side_effect=OSError("injected vault write failure"),
        ):
            response = self.client.delete(
                f"/api/accounts/{source_id}",
                headers=ORIGIN_HEADERS,
            )

        self.assertEqual(response.status_code, 500, response.get_json())
        self.assertEqual(response.get_json()["error"], "io_error")
        self.assertIn(source_id, {item["id"] for item in self._vault_accounts()})
        self.assertTrue(index.has_history(source_id))
        self.assertTrue(
            list(tracker_app.MATCH_BACKUP_DIR.glob("*-account_delete.sqlite3"))
        )

    def test_postdelete_match_cleanup_failure_is_pending_and_reconciles(self) -> None:
        source_id, _inferred_id = self._create_owned_pair()
        self._import_standard_match(source_id)
        index = tracker_app._match_index()
        failure = tracker_app.MatchIndexUnavailable(
            "database_busy",
            "The match index is busy. Try again shortly.",
        )
        with patch.object(index, "purge_account", side_effect=failure), patch.object(
            index, "reconcile_accounts", side_effect=failure
        ):
            response = self.client.delete(
                f"/api/accounts/{source_id}",
                headers=ORIGIN_HEADERS,
            )

        self.assertEqual(response.status_code, 202, response.get_json())
        self.assertTrue(response.get_json()["match_cleanup_pending"])
        self.assertNotIn(source_id, {item["id"] for item in self._vault_accounts()})
        self.assertTrue(index.has_history(source_id))

        tracker_app._reconcile_match_index_if_present()
        self.assertFalse(index.has_history(source_id))
        self.assertEqual(self._db_count("account_match_facts"), 0)

    def test_corrupt_match_database_does_not_break_rank_refresh(self) -> None:
        account_id = self._create_account(authorized=False)
        corrupt = b"not-a-sqlite-database\x00match-only-corruption"
        tracker_app.MATCH_INDEX_PATH.write_bytes(corrupt)
        tracker_app._match_index_instance = None

        match_status = self.client.get(f"/api/accounts/{account_id}/matches/status")
        self.assertEqual(match_status.status_code, 503, match_status.get_json())
        self.assertEqual(match_status.get_json()["error"], "corrupt_database")
        self.assertEqual(tracker_app.MATCH_INDEX_PATH.read_bytes(), corrupt)

        refresh = {
            "current_rank": "Gold I",
            "peak_rank": "Platinum III",
            "current_points": 1550,
            "peak_points": 1700,
            "last_refresh_ts": int(time.time()),
            "last_refresh_status": "ok",
            "last_refresh_code": None,
            "last_refresh_error": None,
            "last_refresh_source": "tracker",
        }
        with patch.object(tracker_app, "_refresh_account_stats", return_value=refresh):
            response = self.client.post(
                f"/api/accounts/{account_id}/refresh-stats",
                headers=ORIGIN_HEADERS,
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["account"]["current_rank"], "Gold I")
        self.assertEqual(response.get_json()["account"]["last_refresh_status"], "ok")
        self.assertEqual(tracker_app.MATCH_INDEX_PATH.read_bytes(), corrupt)

    def test_storage_full_match_commit_preserves_rank_and_rate_limit_state(self) -> None:
        source_id, _inferred_id = self._create_owned_pair()
        with tracker_app._vault_write_lock:
            vault = tracker_app._read_vault()
            source = next(item for item in vault["accounts"] if item["id"] == source_id)
            source.update({
                "current_rank": "Diamond II",
                "peak_rank": "Grandmaster III",
                "current_points": 2711,
                "peak_points": 3099,
                "last_refresh_ts": 1788100000,
                "last_refresh_status": "ok",
                "last_refresh_code": None,
                "last_refresh_error": None,
                "last_refresh_source": "tracker",
            })
            tracker_app._write_vault(vault)
        with tracker_app._tracker_rate_limit_lock:
            tracker_app._tracker_rate_limited_until = 1788200000.0

        raw = _json_bytes()
        preview_response = self._preview(source_id, raw)
        self.assertEqual(preview_response.status_code, 200, preview_response.get_json())
        preview = preview_response.get_json()["preview"]
        index = tracker_app._match_index()
        self.assertTrue(index.initialize().available)
        failure = tracker_app.MatchIndexStorageFull(
            "storage_full",
            "The match index storage location is full.",
        )
        with patch.object(index, "import_records", side_effect=failure):
            committed = self._commit(
                source_id,
                raw,
                preview["source_digest"],
                preview["scope_digest"],
            )

        self.assertEqual(committed.status_code, 507, committed.get_json())
        self.assertEqual(committed.get_json()["error"], "storage_full")
        persisted = next(
            item for item in self._vault_accounts() if item["id"] == source_id
        )
        self.assertEqual(persisted["current_rank"], "Diamond II")
        self.assertEqual(persisted["peak_rank"], "Grandmaster III")
        self.assertEqual(persisted["last_refresh_status"], "ok")
        self.assertEqual(persisted["last_refresh_ts"], 1788100000)
        with tracker_app._tracker_rate_limit_lock:
            self.assertEqual(tracker_app._tracker_rate_limited_until, 1788200000.0)
        self.assertEqual(self._db_count("account_match_facts"), 0)

    def test_refresh_all_works_while_match_database_is_corrupt(self) -> None:
        first = self._create_account(authorized=False, name="Refresh All One")
        second = self._create_account(
            uid=INFERRED_UID,
            platform="xbox",
            authorized=False,
            name="Refresh All Two",
        )
        corrupt = b"not-a-sqlite-database\x00refresh-all-isolation"
        tracker_app.MATCH_INDEX_PATH.write_bytes(corrupt)
        tracker_app._match_index_instance = None
        outcomes = [
            {
                "current_rank": "Gold I",
                "last_refresh_ts": 1788110001,
                "last_refresh_status": "ok",
                "last_refresh_code": None,
                "last_refresh_error": None,
                "last_refresh_source": "tracker",
            },
            {
                "current_rank": "Platinum II",
                "last_refresh_ts": 1788110002,
                "last_refresh_status": "ok",
                "last_refresh_code": None,
                "last_refresh_error": None,
                "last_refresh_source": "tracker",
            },
        ]
        with patch.object(
            tracker_app, "_refresh_account_stats", side_effect=outcomes
        ), patch.object(tracker_app.time, "sleep"):
            response = self.client.post(
                "/api/accounts/refresh-all",
                headers=ORIGIN_HEADERS,
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["summary"]["ok"], 2)
        ranks = {
            item["id"]: item["current_rank"]
            for item in self._vault_accounts()
            if item["id"] in {first, second}
        }
        self.assertEqual(ranks, {first: "Gold I", second: "Platinum II"})
        self.assertEqual(tracker_app.MATCH_INDEX_PATH.read_bytes(), corrupt)

    def test_startup_reconciliation_removes_orphans_and_is_failure_isolated(self) -> None:
        source_id, inferred_id = self._create_owned_pair()
        self._import_standard_match(source_id)

        # Simulate the JSON side of a cross-store delete having committed while
        # SQLite cleanup did not.  Startup reconciliation must remove both the
        # missing source's fact and history inferred solely through it.
        vault = tracker_app._read_vault()
        vault["accounts"] = [
            account for account in vault["accounts"] if account["id"] != source_id
        ]
        tracker_app.VAULT_PATH.write_text(json.dumps(vault), encoding="utf-8")
        tracker_app._reconcile_match_index_if_present()
        self.assertEqual(self._db_count("matches"), 0)
        self.assertEqual(self._db_count("account_match_facts"), 0)
        self.assertIn(inferred_id, {item["id"] for item in self._vault_accounts()})
        self.assertTrue(list(tracker_app.MATCH_BACKUP_DIR.glob("*-reconcile.sqlite3")))

        # A reconciliation failure is logged and swallowed; it cannot prevent
        # the credential vault from being read at startup.
        index = tracker_app._match_index()
        with patch.object(
            index,
            "reconcile_accounts",
            side_effect=tracker_app.MatchIndexUnavailable(
                "database_busy",
                "The match index is busy. Try again shortly.",
            ),
        ):
            tracker_app._reconcile_match_index_if_present()
        self.assertIn(inferred_id, {item["id"] for item in self._vault_accounts()})


if __name__ == "__main__":
    unittest.main(verbosity=2)
