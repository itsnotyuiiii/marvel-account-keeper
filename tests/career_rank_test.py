"""Direct rank/card regression checks. All sessions, sockets and vaults synthetic."""
import base64
import copy
import json
import unittest
from unittest.mock import Mock, patch

import career_provider as provider
import career_provider_test as fixtures
from career_provider_test import profile
from stats_provider_test import tracker_app


def rank_row(season=19, **changes):
    return {"rank_game_id": season, "level": 14, "rank_score": 4350.0,
            "max_level": 0, "max_rank_score": 0, "season_max_level": 23,
            "update_time": 1700000000, "fixed_finished": 0, "fixed_battle_count": 0,
            "extra_private_field": "never-export", **changes}


def rank_reply(rows=None):
    if rows is None:
        rows = {"1001019": rank_row(), "1001017": rank_row(17, level=17, rank_score=4659.65,
            max_level=21, max_rank_score=5016.41, fixed_finished=1, fixed_battle_count=10)}
    return {"rank_game_season": json.dumps({k: json.dumps(v) for k, v in rows.items()})}


class RankProjectionTest(unittest.TestCase):
    def test_nested_seasons_and_peak_are_correct_and_nondisclosing(self):
        value = provider.project_ranks(rank_reply(), "pc", 19)
        self.assertEqual(value["current"]["rank"], "Diamond II")
        self.assertEqual(value["current"]["qualification"], "estimated")
        self.assertEqual(value["current"]["placements_completed"], 0)
        self.assertEqual(value["peak"]["peak_rank"], "Celestial I")
        self.assertEqual(value["peak"]["peak_points"], 5016)
        self.assertEqual(value["peak"]["season"], 17)
        self.assertNotIn("never-export", json.dumps(value))
        self.assertNotIn("season_max_level", json.dumps(value))

    def test_levels_not_score_thresholds_drive_rank_names(self):
        value = provider.project_ranks(rank_reply({"1001019": rank_row(level=14, rank_score=4299)}), "pc", 19)
        self.assertEqual(value["current"]["rank"], "Diamond II")
        self.assertEqual(provider.rank_name(1), "Bronze III")
        self.assertEqual(provider.rank_name(22), "Eternity")
        self.assertEqual(provider.rank_name(23), "One Above All")
        self.assertIsNone(provider.rank_name(0))
        for level in [-1, True, 24, 14.0, "14"]:
            with self.assertRaises(ValueError):
                provider.rank_name(level)

    def test_other_platform_or_mode_does_not_supply_peak(self):
        rows = {"1001019": rank_row(), "2001019": rank_row(max_level=23, max_rank_score=6000),
                "1002019": rank_row(max_level=23, max_rank_score=6000)}
        value = provider.project_ranks(rank_reply(rows), "pc", 19)
        self.assertEqual(len(value["seasons"]), 1)
        self.assertIsNone(value["peak"])

    def test_missing_current_season_is_not_latest_historical_rank(self):
        value = provider.project_ranks(rank_reply({"1001017": rank_row(17)}), "pc", 19)
        self.assertIsNone(value["current"])

    def test_ranked_record_is_scoped_to_selected_platform_mode_and_season(self):
        rows = {"1001019": rank_row(battle_count=27, win_count=21),
                "1001017": rank_row(17, battle_count=47, win_count=20),
                "2001019": rank_row(battle_count=99, win_count=99),
                "1002019": rank_row(battle_count=88, win_count=88)}
        value = provider.project_ranks(rank_reply(rows), "pc", 19)
        self.assertEqual(value["current"]["record"], {"matches": 27, "wins": 21})
        self.assertEqual(value["seasons"][1]["record"], {"matches": 47, "wins": 20})
        self.assertEqual(len(value["seasons"]), 2)
        self.assertNotIn("battle_count", json.dumps(value))

    def test_missing_or_invalid_record_is_not_zero_and_keeps_valid_rank(self):
        changes = [{}, {"battle_count": 27}, {"win_count": 21},
                   {"battle_count": 20, "win_count": 21}]
        for invalid in (-1, True, "27", 1.5, float("inf"), 10**13):
            changes += [{"battle_count": invalid, "win_count": 0},
                        {"battle_count": 27, "win_count": invalid}]
        for change in changes:
            with self.subTest(change=change):
                value = provider.project_ranks(rank_reply({"1001019": rank_row(**change)}), "pc", 19)
                self.assertIsNone(value["current"]["record"])
                self.assertEqual(value["current"]["rank"], "Diamond II")
        value = provider.project_ranks(rank_reply({"1001019": rank_row(battle_count=0, win_count=0)}), "pc", 19)
        self.assertEqual(value["current"]["record"], {"matches": 0, "wins": 0})

    def test_unknown_or_malformed_fields_do_not_guess(self):
        changes = [{"rank_game_id": 18}, {"rank_game_id": True}, {"rank_score": float("nan")},
            {"max_rank_score": -1}, {"max_level": 24}, {"fixed_finished": True}, {"fixed_finished": 2},
            {"fixed_battle_count": -1}, {"fixed_battle_count": 1001}, {"update_time": "today"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                provider.project_ranks(rank_reply({"1001019": rank_row(**change)}), "pc", 19)
        for data in [{}, {"rank_game_season": "[]"}, {"rank_game_season": "secret"},
                     rank_reply({"not-a-key": rank_row()}), rank_reply({"1001019": []})]:
            with self.assertRaises(ValueError):
                provider.project_ranks(data, "pc", 19)

    def test_legacy_missing_placement_state_is_unknown_not_unplaced(self):
        row = rank_row(3)
        del row["fixed_finished"]
        del row["fixed_battle_count"]
        value = provider.project_ranks(rank_reply({"1001003": row}), "pc", 3)
        self.assertEqual(value["current"]["qualification"], "unknown")


class DirectCardRefreshTest(unittest.TestCase):
    def configured(self):
        value = fixtures.CareerProviderTest().configured()
        value._session.rank_request = {"synthetic": True}  # probe_socket is mocked below
        value._session.captured_seasons = (19, 17)
        return value

    def account(self, **changes):
        return {"id": "synthetic-card", "rivals_uid": "123", "rivals_platform": "pc", **changes}

    def responses(self):
        user = {"aid": "1_123", "name": "ExampleIGN", "config_server": {"login_os": "1", "career_settings": {}}}
        return [({"target_uid_echoed": True}, {"client_info": user}),
                ({"own_uid_matches": True}, profile()), ({"own_request_bound": True}, rank_reply())]

    def test_card_calls_current_season_profile_and_ranks_no_tracker_or_history(self):
        p = self.configured()
        with patch.object(tracker_app, "_career_provider", p), patch.object(tracker_app, "_try_tracker") as tracker, \
                patch.object(provider, "probe_socket", side_effect=self.responses()) as read:
            updates = tracker_app._refresh_account_stats(self.account())
        tracker.assert_not_called()
        self.assertEqual(read.call_count, 3)
        self.assertEqual(read.call_args_list[0].kwargs, {"kind": "player_user", "target_uid": "123"})
        self.assertEqual(read.call_args_list[1].kwargs, {"season": 19})
        self.assertEqual(read.call_args_list[2].kwargs, {"kind": "ranks"})
        self.assertEqual(updates["last_refresh_source"], "netease")
        self.assertEqual(updates["current_rank"], "Diamond II")
        self.assertEqual(updates["peak_rank"], "Celestial I")
        self.assertEqual(updates["netease_profile"]["season"], 19)
        self.assertEqual(updates["netease_rank"]["qualification"], "estimated")
        self.assertNotIn("recent_matches", updates)

    def test_invalid_or_missing_identity_never_loads_capture_or_uses_fallback(self):
        p = self.configured()
        for change, code in [({"rivals_uid": ""}, "missing_identity"),
                             ({"rivals_uid": "12x3"}, "invalid_rivals_uid"),
                             ({"rivals_platform": "invalid"}, "career_platform_required")]:
            with patch.object(tracker_app, "_career_provider", p), patch.object(tracker_app, "_try_tracker") as tracker, \
                    patch.object(p, "_load") as load, patch.object(provider, "probe_socket") as read:
                updates = tracker_app._refresh_account_stats(self.account(**change))
            self.assertEqual(updates["last_refresh_code"], code)
            load.assert_not_called()
            read.assert_not_called()
            tracker.assert_not_called()

    def test_netease_package_without_shortcut_never_falls_back_to_tracker(self):
        with patch.object(tracker_app, "_netease_only", True), patch.object(tracker_app, "_career_provider", None), \
                patch.object(tracker_app, "_try_tracker") as tracker:
            updates = tracker_app._refresh_account_stats(self.account())
            self.assertEqual(updates["last_refresh_code"], "career_not_configured")
            tracker.assert_not_called()

    def test_rank_failure_still_saves_aggregate_without_changing_ranks(self):
        p = self.configured()
        with patch.object(tracker_app, "_career_provider", p), \
                patch.object(provider, "probe_socket", side_effect=self.responses()[:2] + [({}, None)]):
            updates = tracker_app._refresh_account_stats(self.account())
        self.assertEqual(updates["netease_profile"]["matches"], 10)
        self.assertEqual(updates["last_refresh_code"], "career_rank_unavailable")
        self.assertNotIn("current_rank", updates)
        self.assertNotIn("peak_rank", updates)

    def test_rank_record_persists_separately_and_missing_new_counts_clear_old_record(self):
        acct = self.account(netease_rank={"season": 17, "record": {"matches": 47, "wins": 20}})
        for changes, expected in [({"battle_count": 27, "win_count": 21}, {"matches": 27, "wins": 21}), ({}, None)]:
            replies = self.responses()[:2] + [({"own_request_bound": True}, rank_reply({"1001019": rank_row(**changes)}))]
            with patch.object(tracker_app, "_career_provider", self.configured()), \
                    patch.object(provider, "probe_socket", side_effect=replies):
                updates = tracker_app._refresh_account_stats(acct)
            merged = tracker_app._apply_refresh_updates({"accounts": [copy.deepcopy(acct)]}, acct["id"], updates)
            self.assertEqual(merged["netease_rank"]["record"], expected)
            self.assertEqual(merged["netease_rank"]["season"], 19)
            self.assertEqual(merged["netease_profile"]["matches"], 10)
            self.assertNotIn("battle_count", json.dumps(merged))

    def test_history_failure_does_not_discard_profile_or_rank(self):
        p = self.configured()
        with patch.object(provider, "probe_socket", side_effect=self.responses()[1:] + [({}, None)]):
            snapshot = p.refresh(season=17)
        self.assertEqual(snapshot["ranks"]["current_season"], 19)
        self.assertEqual(snapshot["season"], 17)
        self.assertIsNotNone(snapshot["ranks"]["current"])
        self.assertIsNotNone(snapshot["match_error"])
        self.assertFalse(snapshot["matches_loaded"])

    def test_duplicate_cards_reuse_only_successful_recent_same_season_read(self):
        p = self.configured()
        with patch.object(provider, "probe_socket", side_effect=self.responses()) as read:
            first = p.refresh_account(self.account())
            second = p.refresh_account(self.account())
        self.assertEqual(first, second)
        self.assertEqual(read.call_count, 3)
        # A historical panel request must never be reused as current-card data.
        p._snapshot = {"season": 17}
        self.assertEqual(p.refresh_account(self.account())["season"], 19)
        key = ("uid", "123", "pc", 19)
        p._card_attempts[key] += 0.01  # later failure cannot make an old cache fresh
        with self.assertRaises(provider.CareerUnavailable):
            p.refresh_account(self.account())

    def test_identity_race_discards_all_netease_updates(self):
        acct = self.account(rivals_uid="456", current_rank="Bronze III")
        original = copy.deepcopy(acct)
        updates = {"_netease_identity": ("123", "pc", ""), "current_rank": "Diamond II", "netease_profile": {"matches": 10}}
        merged = tracker_app._apply_refresh_updates({"accounts": [acct]}, acct["id"], updates)
        self.assertEqual(merged, original)

    def test_peak_monotonicity_and_internal_binding_not_persisted(self):
        acct = self.account(peak_rank="One Above All", peak_points=5800)
        updates = {"_netease_identity": ("123", "pc", ""), "current_rank": "Diamond II",
            "peak_rank": "Celestial I", "peak_points": 5016, "netease_rank": {"peak_season": 17}}
        merged = tracker_app._apply_refresh_updates({"accounts": [acct]}, acct["id"], updates)
        self.assertEqual(merged["peak_rank"], "One Above All")
        self.assertEqual(merged["peak_points"], 5800)
        self.assertTrue(merged["netease_rank"]["higher_saved_peak_kept"])
        self.assertNotIn("_netease_identity", merged)

    def test_manual_identity_or_rank_edit_invalidates_netease_labels(self):
        acct = self.account(netease_rank={"qualification": "estimated"}, netease_profile={"matches": 10})
        changed = tracker_app._account_from_payload({"rivals_platform": "xbox"}, b"fixture", existing=acct)
        self.assertIsNone(changed["netease_profile"])
        self.assertIsNone(changed["netease_rank"])
        changed = tracker_app._account_from_payload({"current_rank": "Gold I"}, b"fixture", existing=acct)
        self.assertIsNone(changed["netease_rank"])
        self.assertEqual(changed["netease_profile"], {"matches": 10})

    def test_lock_during_card_read_prevents_commit(self):
        p = self.configured()
        with tracker_app.app.test_request_context("/api/accounts/synthetic-card/refresh-stats", method="POST"), \
                patch.object(tracker_app, "_career_provider", p), \
                patch.object(tracker_app, "_require_key", side_effect=[(b"fixture", None), (None, ("locked", 401))]), \
                patch.object(tracker_app, "_read_vault", return_value={"accounts": [self.account()]}), \
                patch.object(tracker_app, "_refresh_account_stats", return_value={}), \
                patch.object(tracker_app, "_commit_updates") as commit:
            self.assertEqual(tracker_app.refresh_account_stats("synthetic-card"), ("locked", 401))
            commit.assert_not_called()

    def test_lock_during_refresh_all_stops_without_commit(self):
        with tracker_app.app.test_request_context("/api/accounts/refresh-all/stream", method="POST"), \
                patch.object(tracker_app, "_career_provider", self.configured()), \
                patch.object(tracker_app, "_require_key", side_effect=[(b"fixture", None), (None, ("locked", 401))]), \
                patch.object(tracker_app, "_refresh_account_stats", return_value={}), \
                patch.object(tracker_app, "_commit_updates") as commit:
            events = list(tracker_app._refresh_all_steps({"accounts": [self.account()]}, b"fixture"))
            self.assertEqual(events, [{"type": "locked"}])
            commit.assert_not_called()
