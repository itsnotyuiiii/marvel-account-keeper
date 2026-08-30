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
                    "password_enc": {"ct": "keep-ciphertext"},
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
            ],
        }
        tracker_app.VAULT_PATH.write_text(json.dumps(legacy), encoding="utf-8")

        tracker_app._migrate_vault_if_needed()

        migrated = json.loads(tracker_app.VAULT_PATH.read_text(encoding="utf-8"))
        account = migrated["accounts"][0]
        self.assertEqual(migrated["kdf"], legacy["kdf"])
        self.assertEqual(migrated["verifier"], legacy["verifier"])
        self.assertEqual(account["password_enc"], legacy["accounts"][0]["password_enc"])
        self.assertEqual(account["current_rank"], "Gold II")
        self.assertEqual(account["peak_rank"], "Diamond III")
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
        self.assertIsNone(account["last_refresh_source"])
        self.assertIsNone(account["last_refresh_error"])
        private_account = migrated["accounts"][1]
        self.assertIsNone(private_account["last_refresh_status"])
        self.assertIsNone(private_account["last_refresh_source"])
        self.assertIsNone(private_account["last_refresh_error"])
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
        self.assertEqual(result["last_refresh_source"], "tracker")
        self.assertEqual(result["rivals_uid"], "123456789")
        self.assertEqual(result["current_rank"], "Gold II")
        self.assertEqual(result["current_points"], 1451)
        self.assertEqual(result["peak_rank"], "Diamond III")
        self.assertEqual(result["peak_points"], 2988)
        self.assertTrue(result["tracker_history_private"])

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
        self.assertIn("private, uncrawled, or temporarily unavailable",
                      result["last_refresh_error"])

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
        self.assertGreaterEqual(guarded["_retry_after_s"], 36)
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


class SurfaceCleanupTest(unittest.TestCase):
    def test_removed_routes_and_status_field_are_absent(self) -> None:
        client = tracker_app.app.test_client()
        status = client.get("/api/status")
        self.assertEqual(status.status_code, 200)
        self.assertNotIn("has_marvel_rivals_api_key", status.get_json())
        self.assertEqual(client.get("/api/rivals/sync-status").status_code, 404)
        rules = {rule.rule for rule in tracker_app.app.url_map.iter_rules()}
        self.assertNotIn("/api/accounts/<acct_id>/matches", rules)
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
