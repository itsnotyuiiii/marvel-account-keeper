"""Cloud app integration using projected fixtures, never real accounts."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from stats_provider_test import tracker_app as app
from career_provider import CareerUnavailable
from tests.standalone_web_contract_test import bundle, UID
from standalone_web_contract import project_profile_envelope


class CloudDesktopAppTest(unittest.TestCase):
    def setUp(self):
        self.key = b"C" * 32
        source = bundle()
        source["ranks_verified"] = True
        source["ranks"] = {"status": "available", "data": {"uid": str(UID), "platform": "pc",
            "points_basis": "server_rank_score", "season_count": 1, "seasons": [{
                "season": 20, "level": 14, "points": 4350, "peak_level": 14, "peak_points": 4350,
                "placements_completed": 3, "record": None, "updated_at": None}]}}
        now = datetime.now(timezone.utc)
        profile = project_profile_envelope(source, UID, now)["nativeProfile"]
        self.snapshot = {"uid": str(UID), "platform": "pc", "in_game_name": "Synthetic ゆい",
            "fetched_at": int(now.timestamp()), "native_profile": profile}
        self.provider = Mock()
        self.provider.refresh_account.return_value = self.snapshot
        self.provider.cached_account.return_value = self.snapshot
        self.provider.can_refresh_account.return_value = True
        for name, value in (("_cloud_mode", True), ("_local_native_mode", False), ("_netease_only", True),
                            ("_career_provider", self.provider)):
            patcher = patch.object(app, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        for name in ("_try_tracker", "_match_index", "_fetch_latest_release"):
            patcher = patch.object(app, name, side_effect=AssertionError("not a cloud operation"))
            patcher.start(); self.addCleanup(patcher.stop)

    def card(self):
        return {"id": "cloud-fixture", "rivals_uid": str(UID), "rivals_platform": "pc",
            "in_game_name": "OldName", "password_enc": "preserved-ciphertext", "notes": "keep",
            "current_rank": "Bronze III", "current_points": 10, "peak_rank": "Diamond II"}

    def test_refresh_preserves_manual_data_and_presents_exact_placements(self):
        card = self.card()
        update = app._refresh_account_stats(card)
        app._apply_refresh_updates({"accounts": [card]}, card["id"], update)
        for key in ("password_enc", "notes", "current_rank", "current_points", "peak_rank"):
            self.assertEqual(card[key], self.card()[key])
        self.assertEqual(card["in_game_name"], "Synthetic ゆい")
        self.assertNotIn("synthetic_match", json.dumps(card))
        public = app._public_account(card, self.key)
        rank = public["netease_native"]["rank_display"]
        self.assertEqual((rank["status"], rank["placements_completed"]), ("estimated", 3))
        self.assertTrue(public["netease_native"]["profile"]["matches_verified"])
        self.assertNotIn("rank_display", card["netease_native"])

    def test_failed_lookup_retains_saved_data_and_empty_uid_is_not_invalid(self):
        card = self.card()
        card["netease_native"] = {"fetched_at": 1, "profile": {"old": True}}
        before = copy.deepcopy(card["netease_native"])
        self.provider.refresh_account.side_effect = CareerUnavailable("cloud_profile_not_returned",
            "NetEase returned no profile. This does not establish an invalid UID.", 404)
        update = app._refresh_account_stats(card)
        app._apply_refresh_updates({"accounts": [card]}, card["id"], update)
        self.assertEqual(card["netease_native"], before)
        self.assertEqual(card["last_refresh_status"], "error")
        self.assertEqual(card["rivals_uid"], str(UID))

    def test_cloud_lock_and_update_isolation(self):
        app._suspend_native_connection()
        self.provider.suspend.assert_called_once()
        with app.app.test_request_context("/api/check-update"):
            self.assertEqual(app.check_update().get_json()["channel"], "cloud-preview")
        with app.app.test_request_context("/api/apply-update", method="POST"), patch.object(app, "_require_key", return_value=(self.key, None)):
            response, code = app.apply_update()
            self.assertEqual(code, 409)
            self.assertEqual(response.get_json()["error"], "cloud_preview_channel")

    def test_cloud_does_not_accept_local_netease_login(self):
        with app.app.test_request_context("/api/netease/connect", method="POST", json={"persist": True}), patch.object(app, "_require_key", return_value=(self.key, None)):
            _, status = app.connect_native_netease()
            self.assertEqual(status, 409)
            self.provider.connect.assert_not_called()

    def test_malformed_saved_native_wrapper_does_not_break_listing(self):
        self.provider.cached_account.return_value = None
        for malformed in ("old", [1], {"profile": None}):
            card = self.card()
            card["netease_native"] = malformed
            result = app._public_account(card, self.key)
            self.assertEqual(result["rivals_uid"], str(UID))

    def test_cloud_import_does_not_migrate_or_unlock_before_instance_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = b'{"initialized":true,"accounts":[{"id":"old-fixture"}]}'
            (root / "vault.json").write_bytes(original)
            (root / "remembered_key.bin").write_bytes(b"synthetic-invalid-dpapi")
            environment = dict(os.environ, MARVEL_KEEPER_DATA=str(root))
            result = subprocess.run([sys.executable, "-B", "-c",
                "import sys; sys.argv.append('--cloud-stats'); import app; assert app._state['key'] is None"],
                cwd=Path(__file__).resolve().parents[1], env=environment,
                capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0)
            self.assertEqual((root / "vault.json").read_bytes(), original)
            self.assertEqual((root / "remembered_key.bin").read_bytes(), b"synthetic-invalid-dpapi")
            self.assertFalse((root / "backups").exists())


if __name__ == "__main__":
    unittest.main()
