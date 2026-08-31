#!/usr/bin/env python3
"""Regression coverage for the v2.15 stats-provider cleanup."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_TEMP_DIR = tempfile.TemporaryDirectory()
_DATA_DIR = Path(_TEMP_DIR.name)
os.environ["MARVEL_KEEPER_DATA"] = str(_DATA_DIR)
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Keep app.py's import-time migration from finding the repository's real/local
# vault. This current-schema shell needs no write, so import has no side effects.
(_DATA_DIR / "vault.json").write_text(
    json.dumps({
        "initialized": False,
        "config": {"lockout_minutes": 30},
        "accounts": [],
    }),
    encoding="utf-8",
)

import app as tracker_app  # noqa: E402  (environment must be set first)


class ProviderMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        tracker_app.EXTRA_BACKUP_DIR = _DATA_DIR / "extra-backups"
        for folder in (tracker_app.BACKUP_DIR, tracker_app.EXTRA_BACKUP_DIR):
            if folder.exists():
                for item in folder.glob("vault-*.json"):
                    item.unlink()

    def test_legacy_provider_data_is_pruned_with_backup(self) -> None:
        migration_key = b"M" * 32
        encrypted_password = tracker_app._encrypt(
            migration_key,
            "synthetic-v2.15-password",
        )
        legacy = {
            "initialized": True,
            "kdf": {"salt": "preserve-me"},
            "verifier": {"ct": "preserve-me-too"},
            "config": {
                "lockout_minutes": 45,
                "marvel_rivals_api_key": "must-be-removed",
                "rivals_api_usage": {"count": 99},
                "ui_options": {"view": "table"},
            },
            "accounts": [
                {
                    "id": "legacy-account",
                    "in_game_name": "LegacyPlayer",
                    "current_rank": "Gold II",
                    "peak_rank": "Diamond III",
                    "current_points": 4180,
                    "peak_points": 4312,
                    "password_enc": encrypted_password,
                    "last_refresh_status": "bad_key",
                    "last_refresh_source": "marvelrivalsapi",
                    "last_refresh_error": "old provider error",
                    "tracker_private": True,
                    "rivals_update_requested_at": 123,
                    "recent_matches": [{"match_uid": "old-cache"}],
                    "matches_synced_at": 456,
                    "matches_error": "old match error",
                },
                {
                    "id": "legacy-private-account",
                    "in_game_name": "FormerlyPrivate",
                    "last_refresh_status": "private",
                    "last_refresh_source": "tracker",
                    "last_refresh_error": "old private-provider state",
                },
                {
                    "id": "legacy-sourceless-account",
                    "in_game_name": "SourceLess",
                    "current_rank": "Platinum I",
                    "last_refresh_ts": 123,
                    "last_refresh_status": "not_found",
                    "last_refresh_source": None,
                    "last_refresh_error": "Unexpected HTTP 502",
                },
            ],
        }
        tracker_app.VAULT_PATH.write_text(json.dumps(legacy), encoding="utf-8")

        tracker_app._migrate_vault_if_needed()

        migrated = json.loads(tracker_app.VAULT_PATH.read_text(encoding="utf-8"))
        account = migrated["accounts"][0]
        self.assertEqual(migrated["kdf"], legacy["kdf"])
        self.assertEqual(migrated["verifier"], legacy["verifier"])
        self.assertEqual(account["password_enc"], legacy["accounts"][0]["password_enc"])
        self.assertEqual(
            tracker_app._decrypt(migration_key, account["password_enc"]),
            "synthetic-v2.15-password",
        )
        self.assertEqual(account["current_rank"], "Gold II")
        self.assertEqual(account["peak_rank"], "Diamond III")
        self.assertEqual(account["current_points"], 4180)
        self.assertEqual(account["peak_points"], 4312)
        self.assertEqual(account["rivals_platform"], "unknown")
        self.assertFalse(account["match_history_authorized"])
        self.assertNotIn("marvel_rivals_api_key", migrated["config"])
        self.assertNotIn("rivals_api_usage", migrated["config"])
        for removed in (
            "tracker_private",
            "rivals_update_requested_at",
            "recent_matches",
            "matches_synced_at",
            "matches_error",
        ):
            self.assertNotIn(removed, account)
        self.assertIsNone(account["last_refresh_status"])
        self.assertIsNone(account["last_refresh_code"])
        self.assertIsNone(account["last_refresh_source"])
        self.assertIsNone(account["last_refresh_error"])
        private_account = migrated["accounts"][1]
        self.assertIsNone(private_account["last_refresh_status"])
        self.assertIsNone(private_account["last_refresh_code"])
        self.assertIsNone(private_account["last_refresh_source"])
        self.assertIsNone(private_account["last_refresh_error"])
        sourceless_account = migrated["accounts"][2]
        self.assertEqual(sourceless_account["current_rank"], "Platinum I")
        self.assertIsNone(sourceless_account["last_refresh_ts"])
        self.assertIsNone(sourceless_account["last_refresh_status"])
        self.assertIsNone(sourceless_account["last_refresh_code"])
        self.assertIsNone(sourceless_account["last_refresh_source"])
        self.assertIsNone(sourceless_account["last_refresh_error"])
        self.assertTrue(list(tracker_app.BACKUP_DIR.glob("vault-*.json")))
        self.assertTrue(list(tracker_app.EXTRA_BACKUP_DIR.glob("vault-*.json")))


class TrackerRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        with tracker_app._tracker_rate_limit_lock:
            tracker_app._tracker_rate_limited_until = 0.0

    def test_public_profile_refresh_backfills_uid_and_rank(self) -> None:
        payload = {
            "data": {
                "platformInfo": {
                    "platformUserHandle": "SamplePlayer",
                    "platformUserId": "123456789",
                },
                "metadata": {
                    "lastUpdated": {"value": "2026-08-28T12:34:56Z"},
                    "isPrivateBattleHistory": True,
                },
                "segments": [
                    {
                        "type": "overview",
                        "stats": {
                            "ranked": {
                                "value": 1450.6,
                                "metadata": {"tierName": "Gold II"},
                            },
                            "peakRanked": {
                                "value": 1500,
                                "metadata": {"tierName": "Gold I"},
                            },
                        },
                    },
                    {
                        "type": "ranked-peaks",
                        "stats": {
                            "peakTiers": {
                                "value": [{
                                    "value": 2988.2,
                                    "metadata": {"tierName": "Diamond III"},
                                }],
                            },
                        },
                    },
                ],
            },
        }
        with patch.object(tracker_app, "_fetch_tracker_player",
                          return_value=(200, payload, None)):
            result = tracker_app._refresh_account_stats({
                "in_game_name": "SamplePlayer",
                "rivals_uid": "",
            })

        self.assertEqual(result["last_refresh_status"], "ok")
        self.assertIsNone(result["last_refresh_code"])
        self.assertEqual(result["last_refresh_source"], "tracker")
        self.assertEqual(result["rivals_uid"], "123456789")
        self.assertEqual(result["current_rank"], "Gold II")
        self.assertEqual(result["current_points"], 1451)
        self.assertEqual(result["peak_rank"], "Diamond III")
        self.assertEqual(result["peak_points"], 2988)
        self.assertTrue(result["tracker_history_private"])

    def test_profile_refresh_does_not_rewrite_malformed_uid(self) -> None:
        payload = {
            "data": {
                "platformInfo": {
                    "platformUserHandle": "MalformedUidPlayer",
                    "platformUserId": "abc123-456xyz",
                },
                "metadata": {},
                "segments": [{
                    "type": "overview",
                    "stats": {
                        "ranked": {
                            "value": 1450,
                            "metadata": {"tierName": "Gold II"},
                        },
                    },
                }],
            },
        }
        with patch.object(tracker_app, "_fetch_tracker_player",
                          return_value=(200, payload, None)):
            result = tracker_app._refresh_account_stats({
                "in_game_name": "MalformedUidPlayer",
                "rivals_uid": "",
            })

        self.assertEqual(result["last_refresh_status"], "ok")
        self.assertEqual(result["current_rank"], "Gold II")
        self.assertNotIn("rivals_uid", result)

    def test_unavailable_profile_is_not_claimed_private(self) -> None:
        unavailable = {
            "errors": [{
                "code": "CollectorResultStatus::Private",
                "message": "Collector could not expose profile",
            }],
        }
        with patch.object(tracker_app, "_fetch_tracker_player",
                          return_value=(400, unavailable, None)):
            result = tracker_app._try_tracker({
                "in_game_name": "HiddenPlayer",
                "rivals_uid": "987654321",
            })

        self.assertEqual(result["last_refresh_status"], "error")
        self.assertEqual(result["last_refresh_code"], "profile_unavailable")
        self.assertIn("collector cannot expose", result["last_refresh_error"])
        self.assertIn("Tracker/game API sync issue", result["last_refresh_error"])

    def test_refresh_failures_have_stable_specific_reason_codes(self) -> None:
        cases = [
            (
                (404, {"errors": [{"code": "CollectorResultStatus::NotFound"}]}, None),
                "not_found",
                "player_not_found",
            ),
            (
                (200, {"data": {"segments": []}}, None),
                "not_found",
                "no_ranked_data",
            ),
            ((0, None, None), "error", "network_error"),
            ((200, None, None), "error", "invalid_response"),
            ((200, {"data": {"platformInfo": []}}, None),
             "error", "invalid_response"),
            ((403, None, None), "error", "provider_blocked"),
            ((503, None, None), "error", "provider_unavailable"),
        ]
        for fetch_result, expected_status, expected_code in cases:
            with self.subTest(expected_code=expected_code), \
                    patch.object(tracker_app, "_fetch_tracker_player",
                                 return_value=fetch_result):
                result = tracker_app._try_tracker({
                    "in_game_name": "ReasonCodePlayer",
                    "rivals_uid": "",
                })
            self.assertEqual(result["last_refresh_status"], expected_status)
            self.assertEqual(result["last_refresh_code"], expected_code)

    def test_unrelated_segment_schema_drift_does_not_hide_valid_rank(self) -> None:
        payload = {
            "data": {
                "platformInfo": {"platformUserHandle": "StablePlayer"},
                "metadata": {},
                "segments": [
                    {
                        "type": "overview",
                        "stats": {
                            "ranked": {
                                "value": 1500,
                                "metadata": {"tierName": "Gold I"},
                            },
                        },
                    },
                    {
                        "type": "ranked-peaks",
                        "stats": {
                            "peakTiers": {
                                "metadata": "future-container-field",
                                "value": [{
                                    "value": 2200,
                                    "metadata": {"tierName": "Diamond III"},
                                }],
                            },
                        },
                    },
                    {"type": "unrelated-future-segment", "stats": "new schema"},
                ],
            },
        }
        with patch.object(tracker_app, "_fetch_tracker_player",
                          return_value=(200, payload, None)):
            result = tracker_app._try_tracker({
                "in_game_name": "StablePlayer",
                "rivals_uid": "",
            })

        self.assertEqual(result["last_refresh_status"], "ok")
        self.assertEqual(result["current_rank"], "Gold I")
        self.assertEqual(result["peak_rank"], "Diamond III")

    def test_non_finite_score_does_not_abort_rank_refresh(self) -> None:
        payload = {
            "data": {
                "segments": [{
                    "type": "overview",
                    "stats": {
                        "ranked": {
                            "value": "Infinity",
                            "metadata": {"tierName": "Gold II"},
                        },
                    },
                }],
            },
        }
        with patch.object(tracker_app, "_fetch_tracker_player",
                          return_value=(200, payload, None)):
            result = tracker_app._try_tracker({
                "in_game_name": "NonFiniteScore",
                "rivals_uid": "",
            })

        self.assertEqual(result["last_refresh_status"], "ok")
        self.assertEqual(result["current_rank"], "Gold II")
        self.assertNotIn("current_points", result)

    def test_rate_limit_stops_identifier_fallback_and_bulk_sweep(self) -> None:
        with patch.object(tracker_app, "_fetch_tracker_player",
                          return_value=(429, None, 37)) as fetch:
            result = tracker_app._try_tracker({
                "in_game_name": "RateLimitedPlayer",
                "rivals_uid": "987654321",
            })
            guarded = tracker_app._try_tracker({
                "in_game_name": "AnotherPlayer",
                "rivals_uid": "123456789",
            })

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["_retry_after_s"], 37)
        self.assertEqual(result["last_refresh_code"], "rate_limited")
        self.assertGreaterEqual(guarded["_retry_after_s"], 36)
        self.assertEqual(guarded["last_refresh_code"], "rate_limited")
        self.assertIn("rate limiting", result["last_refresh_error"])

        vault = {
            "accounts": [
                {"id": "first", "in_game_name": "One"},
                {"id": "second", "in_game_name": "Two"},
            ],
        }
        limited = {
            "last_refresh_status": "error",
            "last_refresh_error": "limited",
            "_retry_after_s": 37,
        }
        with patch.object(tracker_app, "_refresh_account_stats",
                          return_value=limited.copy()), \
                patch.object(tracker_app, "_commit_updates") as commit:
            events = list(tracker_app._refresh_all_steps(vault, b"unused"))

        commit.assert_not_called()
        summary = events[-1]["summary"]
        self.assertEqual(summary["rate_limited"], 1)
        self.assertEqual(summary["by_code"], {"rate_limited": 1})
        self.assertEqual(summary["retry_after_s"], 37)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(events[-2]["done"], 2)

    def test_single_refresh_returns_retry_after_without_vault_write(self) -> None:
        limited = {
            "last_refresh_status": "error",
            "last_refresh_error": "Tracker.gg is rate limiting requests.",
            "_retry_after_s": 23,
        }
        vault = {
            "accounts": [{
                "id": "limited-account",
                "in_game_name": "RateLimitedPlayer",
                "last_refresh_ts": None,
            }],
        }
        with tracker_app.app.test_request_context(
                "/api/accounts/limited-account/refresh-stats", method="POST"), \
                patch.object(tracker_app, "_require_key",
                             return_value=(b"unused", None)), \
                patch.object(tracker_app, "_read_vault", return_value=vault), \
                patch.object(tracker_app, "_refresh_account_stats",
                             return_value=limited.copy()), \
                patch.object(tracker_app, "_commit_updates") as commit:
            response = tracker_app.refresh_account_stats("limited-account")

        commit.assert_not_called()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "23")
        self.assertEqual(response.get_json()["error"], "rate_limited")

    def test_refresh_all_summary_breaks_failures_down_by_reason(self) -> None:
        vault = {
            "accounts": [
                {"id": "unavailable", "in_game_name": "Unavailable"},
                {"id": "missing", "in_game_name": "Missing"},
            ],
        }
        outcomes = [
            {
                "last_refresh_status": "error",
                "last_refresh_code": "profile_unavailable",
                "last_refresh_error": "unavailable",
            },
            {
                "last_refresh_status": "not_found",
                "last_refresh_code": "player_not_found",
                "last_refresh_error": "not found",
            },
        ]

        def merged(acct_id: str, updates: dict) -> dict:
            return {"id": acct_id, **updates}

        with patch.object(tracker_app, "_refresh_account_stats",
                          side_effect=outcomes), \
                patch.object(tracker_app, "_commit_updates",
                             side_effect=merged), \
                patch.object(tracker_app.time, "sleep"):
            events = list(tracker_app._refresh_all_steps(vault, b"unused"))

        summary = events[-1]["summary"]
        self.assertEqual(summary["error"], 1)
        self.assertEqual(summary["not_found"], 1)
        self.assertEqual(summary["by_code"], {
            "profile_unavailable": 1,
            "player_not_found": 1,
        })
        self.assertEqual(events[0]["code"], "profile_unavailable")
        self.assertEqual(events[1]["code"], "player_not_found")


class SurfaceCleanupTest(unittest.TestCase):
    def test_retired_provider_surface_is_absent_and_local_matches_exist(self) -> None:
        client = tracker_app.app.test_client()
        status = client.get("/api/status")
        self.assertEqual(status.status_code, 200)
        self.assertNotIn("has_marvel_rivals_api_key", status.get_json())
        self.assertEqual(client.get("/api/rivals/sync-status").status_code, 404)
        rules = {rule.rule for rule in tracker_app.app.url_map.iter_rules()}
        self.assertIn("/api/accounts/<acct_id>/matches", rules)
        self.assertIn("/api/accounts/<acct_id>/matches/import/preview", rules)
        self.assertIn("/api/accounts/<acct_id>/matches/import", rules)
        self.assertNotIn("/api/rivals/lookup-uid/<uid>", rules)

    def test_public_ui_has_only_user_opened_rivalsdata_link(self) -> None:
        root = Path(__file__).resolve().parent.parent
        public_text = "\n".join(
            (root / rel).read_text(encoding="utf-8")
            for rel in (
                "README.md",
                "static/app.jsx",
                "static/card-variants.jsx",
                "static/demo-tour.js",
                "static/styles.css",
            )
        )
        self.assertNotIn("marvelrivalsapi", public_text.lower())
        self.assertIn("https://rivalsdata.com/player/", public_text)
        self.assertNotIn("fetch(\"https://rivalsdata.com", public_text)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        _TEMP_DIR.cleanup()
