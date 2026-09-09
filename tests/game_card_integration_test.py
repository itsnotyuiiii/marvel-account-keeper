"""Game Career card integration; synthetic sessions and mocked vault/network only."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

import career_provider as provider
from career_rank_test import rank_reply, rank_row
from player_card_lookup_test import account, configured, user
from stats_provider_test import tracker_app


PROFILE = {"matches": 40, "wins": 24, "kills": 120, "deaths": 30, "assists": 70}


def game_provider(profile=None):
    """Inject one app-level reader without any target-account credentials."""
    reader = Mock()
    reader.read_profile.return_value = copy.deepcopy(PROFILE if profile is None else profile)
    reader.status.return_value = {"configured": True, "connected": False}
    original = configured()
    value = provider.CapturedCareerProvider("fixture.etl", "fixture.uep", "123", game_reader=reader)
    value._session = original._session
    return value, reader


def replies(uid="456789", name="ExampleIGN"):
    return [({"target_uid_echoed": True}, {"client_info": user(uid, name)}),
            ({"target_request_bound": True}, rank_reply())]


class GameCareerCardIntegrationTest(unittest.TestCase):
    def test_one_add_by_uid_or_exact_ign_populates_profile_and_ranks(self):
        for identity in ({"rivals_uid": "456789"}, {"in_game_name": "exampleign"}):
            with self.subTest(identity=identity):
                p, reader = game_provider()
                responses = replies()
                if "in_game_name" in identity:
                    responses[0] = ({"search_result_received": True}, {
                        "users": [user("111111", "NotExample"), user()], "cursor": -1})
                payload = {**identity, "refresh_stats": True, "password": ""}
                with tracker_app.app.test_request_context("/api/accounts", method="POST", json=payload), \
                        patch.object(tracker_app, "_require_key", return_value=(b"fixture", None)), \
                        patch.object(tracker_app, "_career_provider", p), \
                        patch.object(tracker_app, "_read_vault", return_value={"accounts": []}), \
                        patch.object(tracker_app, "_write_vault") as write, \
                        patch.object(tracker_app, "_try_tracker") as tracker, \
                        patch.object(provider, "probe_socket", side_effect=responses) as read:
                    response = tracker_app.create_account().get_json()
                self.assertTrue(response["ok"])
                card = response["account"]
                self.assertEqual((card["rivals_uid"], card["in_game_name"], card["rivals_platform"]),
                                 ("456789", "ExampleIGN", "pc"))
                self.assertEqual((card["current_rank"], card["peak_rank"]), ("Diamond II", "Celestial I"))
                self.assertEqual({key: card["netease_profile"][key] for key in PROFILE}, PROFILE)
                self.assertEqual(card["netease_profile"]["season"], 19)
                self.assertIsNone(card["netease_profile_note"])
                self.assertEqual(card["last_refresh_source"], "netease")
                self.assertEqual(card["last_refresh_status"], "ok")
                reader.read_profile.assert_called_once_with(456789, 19, platform="pc")
                self.assertEqual(read.call_count, 2)
                tracker.assert_not_called()
                saved = write.call_args.args[0]
                self.assertEqual(len(saved["accounts"]), 1)
                self.assertNotIn("_netease_identity", json.dumps(saved))
                self.assertNotIn("never-export", json.dumps(saved))

    def test_private_and_unverifiable_visibility_block_game_and_rank_reads(self):
        for visibility in (1, 2, 3, 4, True, 99, "0"):
            with self.subTest(visibility=visibility):
                p, reader = game_provider()
                identity = user(settings={"CareerOverviewIsVisibleToOther_1": visibility})
                with patch.object(provider, "probe_socket", return_value=(
                        {"target_uid_echoed": True}, {"client_info": identity})) as read:
                    result = p.refresh_account(account())
                self.assertEqual(result["lookup_code"], "career_profile_restricted")
                self.assertIsNone(result["profile"])
                self.assertIsNone(result["ranks"])
                reader.read_profile.assert_not_called()
                self.assertEqual(read.call_count, 1)

    def test_console_identity_skips_game_career_but_keeps_platform_rank(self):
        for platform, os_id in (("playstation", 2), ("xbox", 4)):
            with self.subTest(platform=platform):
                p, reader = game_provider()
                rank_data = rank_reply({str(os_id * 1_000_000 + 1019): rank_row(
                    max_level=21, max_rank_score=5016)})
                with patch.object(provider, "probe_socket", side_effect=[
                        ({"target_uid_echoed": True}, {"client_info": user(os_id=str(os_id))}),
                        ({"target_request_bound": True}, rank_data)]):
                    result = p.refresh_account(account())
                self.assertEqual(result["platform"], platform)
                self.assertEqual(result["ranks"]["current"]["rank"], "Diamond II")
                self.assertIsNone(result["profile"])
                self.assertIn("PC only", result["profile_error"])
                reader.read_profile.assert_not_called()

    def test_typed_game_failure_preserves_saved_profile_and_refreshes_ranks(self):
        from game_career_client import GameCareerUnavailable

        p, reader = game_provider()
        reader.read_profile.side_effect = GameCareerUnavailable("game_career_session_unavailable")
        saved_profile = {"season": 17, "fetched_at": 123, **PROFILE}
        card = account(netease_profile=copy.deepcopy(saved_profile), current_rank="Bronze III")
        with patch.object(tracker_app, "_career_provider", p), \
                patch.object(tracker_app, "_try_tracker") as tracker, \
                patch.object(provider, "probe_socket", side_effect=replies()):
            updates = tracker_app._refresh_account_stats(card)
        self.assertNotIn("netease_profile", updates)
        tracker_app._apply_refresh_updates({"accounts": [card]}, card["id"], updates)
        self.assertEqual(card["netease_profile"], saved_profile)
        self.assertEqual(card["current_rank"], "Diamond II")
        self.assertEqual(card["peak_rank"], "Celestial I")
        self.assertEqual(card["netease_profile_note"], reader.read_profile.side_effect.message)
        self.assertEqual(card["last_refresh_status"], "ok")
        tracker.assert_not_called()

    def test_malformed_projected_counts_do_not_replace_saved_profile(self):
        for malformed in ({**PROFILE, "kills": True}, {**PROFILE, "wins": -1},
                          {**PROFILE, "deaths": 10**13}, {"matches": 40}):
            with self.subTest(malformed=malformed):
                p, _ = game_provider(malformed)
                card = account(netease_profile=copy.deepcopy(PROFILE))
                with patch.object(tracker_app, "_career_provider", p), \
                        patch.object(provider, "probe_socket", side_effect=replies()):
                    updates = tracker_app._refresh_account_stats(card)
                self.assertNotIn("netease_profile", updates)
                tracker_app._apply_refresh_updates({"accounts": [card]}, card["id"], updates)
                self.assertEqual(card["netease_profile"], PROFILE)
                self.assertEqual(card["current_rank"], "Diamond II")
                self.assertIn("unfamiliar Career format", card["netease_profile_note"])

    def test_one_reader_serves_two_targets_and_cached_result_is_detached(self):
        p, reader = game_provider()
        other_profile = {**PROFILE, "matches": 50, "wins": 30}
        reader.read_profile.side_effect = [copy.deepcopy(PROFILE), other_profile]
        with patch.object(provider, "probe_socket", side_effect=replies() + replies("222222", "OtherIGN")) as read:
            first = p.refresh_account(account())
            first["profile"]["kills"] = -1
            cached = p.refresh_account(account())
            second = p.refresh_account(account(rivals_uid="222222"))
        self.assertEqual(cached["profile"], PROFILE)
        self.assertEqual(second["uid"], "222222")
        self.assertEqual(second["profile"], other_profile)
        self.assertEqual([call.args for call in reader.read_profile.call_args_list], [(456789, 19), (222222, 19)])
        self.assertEqual([call.kwargs for call in reader.read_profile.call_args_list], [{"platform": "pc"}] * 2)
        self.assertEqual(read.call_count, 4)
        self.assertNotIn("password", repr(reader.read_profile.call_args_list))
        self.assertNotIn("local-credential", repr(reader.read_profile.call_args_list))

    def test_clear_closes_shared_reader_and_drops_cached_player_data(self):
        p, reader = game_provider()
        with patch.object(provider, "probe_socket", side_effect=replies()):
            p.refresh_account(account())
        self.assertTrue(p._card_cache)
        p.clear()
        reader.close.assert_called_once_with()
        self.assertIsNone(p._session)
        self.assertIsNone(p._snapshot)
        self.assertFalse(p._card_cache)
        self.assertFalse(p._card_attempts)

    def test_status_includes_only_projected_shared_reader_status(self):
        p, reader = game_provider()
        status = p.status()
        reader.status.assert_called_once_with()
        reader.read_profile.assert_not_called()
        self.assertEqual(status["game_career"], {"configured": True, "connected": False})
        self.assertNotIn("never-export", json.dumps(status))

    def test_identity_edit_during_lookup_discards_new_profile_and_rank_updates(self):
        for change in ({"rivals_uid": "222222"}, {"rivals_platform": "xbox"},
                       {"in_game_name": "DifferentIGN"}):
            with self.subTest(change=change):
                p, _ = game_provider()
                original = account(netease_profile={"season": 17, "matches": 3})
                with patch.object(tracker_app, "_career_provider", p), \
                        patch.object(provider, "probe_socket", side_effect=replies()):
                    updates = tracker_app._refresh_account_stats(original)
                self.assertIn("netease_profile", updates)
                changed = {**copy.deepcopy(original), **change}
                expected = copy.deepcopy(changed)
                tracker_app._apply_refresh_updates({"accounts": [changed]}, changed["id"], updates)
                self.assertEqual(changed, expected)

    def test_wrong_identity_stops_before_game_reader_is_called(self):
        p, reader = game_provider()
        with patch.object(provider, "probe_socket", return_value=(
                {"target_uid_echoed": True}, {"client_info": user("222222")})) as read, \
                self.assertRaises(provider.CareerUnavailable) as error:
            p.refresh_account(account())
        self.assertEqual(error.exception.code, "career_shape_changed")
        reader.read_profile.assert_not_called()
        read.assert_called_once()


if __name__ == "__main__":
    unittest.main()
