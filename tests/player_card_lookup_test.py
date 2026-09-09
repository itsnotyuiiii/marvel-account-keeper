"""One-step player lookup. Synthetic sessions/vaults; no real network or accounts."""
import copy
import json
import unittest
from unittest.mock import patch

import career_provider as provider
import career_provider_test as fixtures
from career_rank_test import rank_reply
from stats_provider_test import tracker_app


def user(uid="456789", name="ExampleIGN", settings=None, os_id="1"):
    return {"aid": "1_" + uid, "name": name, "config_server": {
        "login_os": os_id, "career_settings": settings or {}, "unrelated": "never-export"},
        "third_party_account_info": {"token": "never-export"}, "login_ts": 9999}


def configured():
    value = fixtures.CareerProviderTest().configured()
    value._session.rank_request = {"synthetic": True}
    value._session.captured_seasons = (19, 17)
    return value


def account(**updates):
    return {"id": "new-card", "rivals_uid": "456789", "rivals_platform": "unknown",
            "in_game_name": "", "username": "local-credential", "password": "never-export", **updates}


class PlayerCardLookupTest(unittest.TestCase):
    def test_invalid_name_is_explained_before_loading_a_session(self):
        for name in ("x" * 65, "bad\nname", "bad\x7fname", "\ud800"):
            p = configured()
            with patch.object(p, "_load") as load, patch.object(provider, "probe_socket") as read, \
                    self.assertRaises(provider.CareerUnavailable) as error:
                p.refresh_account(account(rivals_uid=None, in_game_name=name))
            self.assertEqual(error.exception.code, "invalid_in_game_name")
            load.assert_not_called()
            read.assert_not_called()

    def test_uid_lookup_returns_name_platform_and_ranks_without_target_login(self):
        p = configured()
        replies = [({"target_uid_echoed": True}, {"client_info": user()}),
                   ({"target_request_bound": True}, rank_reply())]
        with patch.object(provider, "probe_socket", side_effect=replies) as read:
            value = p.refresh_account(account())
        self.assertEqual(value["uid"], "456789")
        self.assertEqual(value["in_game_name"], "ExampleIGN")
        self.assertEqual(value["platform"], "pc")
        self.assertEqual(value["ranks"]["current"]["rank"], "Diamond II")
        self.assertIsNone(value["profile"])
        self.assertIn("not connected", value["profile_error"])
        self.assertEqual([call.kwargs for call in read.call_args_list], [
            {"kind": "player_user", "target_uid": "456789"},
            {"kind": "player_ranks", "target_uid": "456789"}])
        self.assertNotIn("never-export", json.dumps(value))
        self.assertNotIn("never-export", repr(read.call_args_list))

    def test_ign_requires_one_exact_name_and_backfills_uid(self):
        p = configured()
        data = {"users": [user("111111", "NotExample"), user()], "cursor": -1}
        with patch.object(provider, "probe_socket", side_effect=[
                ({"search_result_received": True}, data), ({"target_request_bound": True}, rank_reply())]) as read:
            value = p.refresh_account(account(rivals_uid=None, in_game_name="exampleign"))
        self.assertEqual(value["uid"], "456789")
        self.assertEqual(value["in_game_name"], "ExampleIGN")
        self.assertEqual(read.call_args_list[0].kwargs, {"kind": "player_search", "search_name": "exampleign"})
        self.assertEqual(read.call_args_list[1].kwargs["target_uid"], "456789")

    def test_no_exact_or_multiple_name_matches_never_guess_or_read_ranks(self):
        for rows, cursor, code in [([user(name="ExampleIGN2")], -1, "player_not_found"),
                                   ([user(), user("222222")], -1, "player_name_ambiguous"),
                                   ([], 10, "player_name_ambiguous")]:
            p = configured()
            with patch.object(provider, "probe_socket", return_value=({"search_result_received": True},
                    {"users": rows, "cursor": cursor})) as read, self.assertRaises(provider.CareerUnavailable) as error:
                p.refresh_account(account(rivals_uid=None, in_game_name="ExampleIGN"))
            self.assertEqual(error.exception.code, code)
            self.assertEqual(read.call_count, 1)

    def test_private_or_unknown_overview_never_triggers_rank_or_career_read(self):
        for setting in (1, 2, 3, 4, True, 99, "0"):
            p = configured()
            response = {"client_info": user(settings={"CareerOverviewIsVisibleToOther_1": setting})}
            with patch.object(provider, "probe_socket", return_value=({"target_uid_echoed": True}, response)) as read:
                value = p.refresh_account(account())
            self.assertIsNone(value["ranks"])
            self.assertIsNone(value["profile"])
            self.assertEqual(value["lookup_code"], "career_profile_restricted")
            self.assertEqual(read.call_count, 1)

    def test_platform_specific_setting_overrides_legacy_permission(self):
        value = provider.project_player_identity(user(settings={"CareerOverviewIsVisibleToOther": 1,
            "CareerOverviewIsVisibleToOther_1": 0}), 1)
        self.assertEqual(value["permissions"], {"pc": "public", "ps": "restricted", "xbox": "restricted"})

    def test_wrong_uid_zone_and_malformed_identity_do_not_supply_ranks(self):
        for changes in ({"aid": "1_111111"}, {"aid": "2_456789"}, {"aid": "bad"},
                        {"name": "bad\nname"}, {"config_server": []}):
            p = configured()
            with patch.object(provider, "probe_socket", return_value=({"target_uid_echoed": True},
                    {"client_info": {**user(), **changes}})) as read, self.assertRaises(provider.CareerUnavailable):
                p.refresh_account(account())
            self.assertEqual(read.call_count, 1)

    def test_cache_is_per_player_platform_and_cannot_be_mutated_by_caller(self):
        p = configured()
        replies = [({"target_uid_echoed": True}, {"client_info": user()}), ({"target_request_bound": True}, rank_reply()),
                   ({"target_uid_echoed": True}, {"client_info": user("222222")}), ({"target_request_bound": True}, rank_reply())]
        with patch.object(provider, "probe_socket", side_effect=replies) as read:
            first = p.refresh_account(account())
            first["uid"] = "bad"
            first["ranks"]["current"]["points"] = -1
            cached = p.refresh_account(account())
            second = p.refresh_account(account(rivals_uid="222222"))
        self.assertEqual(cached["uid"], "456789")
        self.assertEqual(cached["ranks"]["current"]["points"], 4350)
        self.assertEqual(second["uid"], "222222")
        self.assertEqual(read.call_count, 4)

    def test_service_rate_limit_pauses_other_player_requests(self):
        p = configured()
        with patch.object(provider, "probe_socket", return_value=({"upstream_code": 429}, None)) as read:
            for uid in ("456789", "222222"):
                with self.assertRaises(provider.CareerUnavailable) as error:
                    p.refresh_account(account(rivals_uid=uid))
                self.assertEqual(error.exception.status, 429)
        self.assertEqual(read.call_count, 1)

    def test_session_failure_never_calls_an_external_fallback(self):
        p = configured()
        with patch.object(p, "_load", side_effect=provider.CareerUnavailable("career_session_expired", "Expired")), \
                patch.object(provider, "probe_socket") as read, patch.object(tracker_app, "_career_provider", p), \
                patch.object(tracker_app, "_try_tracker") as tracker:
            value = tracker_app._refresh_account_stats(account())
        self.assertEqual(value["last_refresh_code"], "career_session_expired")
        read.assert_not_called()
        tracker.assert_not_called()


class OneStepCardApiTest(unittest.TestCase):
    def test_single_create_action_populates_card_without_reading_real_vault(self):
        p = configured()
        payload = {"in_game_name": "exampleign", "refresh_stats": True, "password": ""}
        replies = [({"search_result_received": True}, {"users": [user()], "cursor": -1}),
                   ({"target_request_bound": True}, rank_reply())]
        with tracker_app.app.test_request_context("/api/accounts", method="POST", json=payload), \
                patch.object(tracker_app, "_require_key", return_value=(b"fixture", None)), \
                patch.object(tracker_app, "_career_provider", p), patch.object(tracker_app, "_try_tracker") as tracker, \
                patch.object(tracker_app, "_read_vault", return_value={"accounts": []}), \
                patch.object(tracker_app, "_write_vault") as write, \
                patch.object(provider, "probe_socket", side_effect=replies):
            result = tracker_app.create_account().get_json()
        card = result["account"]
        self.assertEqual((card["rivals_uid"], card["in_game_name"], card["rivals_platform"]),
                         ("456789", "ExampleIGN", "pc"))
        self.assertEqual(card["current_rank"], "Diamond II")
        self.assertEqual(card["peak_rank"], "Celestial I")
        self.assertIsNone(card["netease_profile"])
        self.assertIn("not connected", card["netease_profile_note"])
        self.assertEqual(len(write.call_args.args[0]["accounts"]), 1)
        self.assertNotIn("_netease_identity", json.dumps(write.call_args.args[0]))
        self.assertNotIn("never-export", json.dumps(card))
        tracker.assert_not_called()

    def test_lock_during_new_card_lookup_prevents_card_creation(self):
        with tracker_app.app.test_request_context("/api/accounts", method="POST",
                json={"rivals_uid": "456789", "refresh_stats": True}), \
                patch.object(tracker_app, "_require_key", side_effect=[(b"fixture", None), (None, ("locked", 401))]), \
                patch.object(tracker_app, "_career_provider", configured()), \
                patch.object(tracker_app, "_refresh_account_stats", return_value={}), \
                patch.object(tracker_app, "_write_vault") as write:
            self.assertEqual(tracker_app.create_account(), ("locked", 401))
        write.assert_not_called()

    def test_ign_or_platform_edit_during_lookup_discards_all_old_results(self):
        original = account(rivals_uid=None, in_game_name="ExampleIGN")
        updates = {"_netease_identity": ("", "unknown", "ExampleIGN"), "rivals_uid": "456789",
                   "current_rank": "Diamond II", "rivals_platform": "pc"}
        for changed in ({"in_game_name": "Different"}, {"rivals_platform": "xbox"}, {"rivals_uid": "222222"}):
            card = {**copy.deepcopy(original), **changed}
            expected = copy.deepcopy(card)
            tracker_app._apply_refresh_updates({"accounts": [card]}, card["id"], updates)
            self.assertEqual(card, expected)
