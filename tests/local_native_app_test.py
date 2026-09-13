"""Native desktop card/route contract with synthetic data and no live vault."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from stats_provider_test import tracker_app as app
from career_provider import CareerUnavailable
from local_native_provider import LocalNativeCareerProvider
from local_native_session import MemorySessionStore
from standalone_session_manager import QueryResult
from tests.local_native_provider_test import account, profile
from tests.standalone_session_manager_test import OWNER, TARGET, bundle, candidate


class LocalNativeAppTest(unittest.TestCase):
    def setUp(self):
        self.key = b"A" * 32
        self.query = Mock(side_effect=lambda sauth, uid: QueryResult(OWNER, True, profile(uid)))
        self.provider = LocalNativeCareerProvider(MemorySessionStore(), query=self.query, exchange=candidate)
        self.provider.bootstrap(bundle())
        self.addCleanup(self.provider.close)
        for name, value in (("_career_provider", self.provider), ("_local_native_mode", True),
                            ("_netease_only", True)):
            change = patch.object(app, name, value)
            change.start()
            self.addCleanup(change.stop)
        for target in ("_try_tracker", "_match_index"):
            change = patch.object(app, target, side_effect=AssertionError("no external provider/index"))
            change.start()
            self.addCleanup(change.stop)
        change = patch("socket.socket", side_effect=AssertionError("no network"))
        change.start()
        self.addCleanup(change.stop)

    def card(self, uid=OWNER, **fields):
        return {"id": "native-fixture", **account(uid), "password_enc": None,
                "current_rank": "Bronze III", "current_points": 10,
                "peak_rank": "Diamond II", "peak_points": 90, **fields}

    def test_add_uid_populates_ign_stats_and_memory_matches_in_one_action(self):
        for uid in (OWNER, TARGET):
            with app.app.test_request_context("/api/accounts", method="POST", json={
                    "rivals_uid": str(uid), "refresh_stats": True, "password": ""}), \
                    patch.object(app, "_require_key", return_value=(self.key, None)), \
                    patch.object(app, "_read_vault", return_value={"accounts": []}), \
                    patch.object(app, "_write_vault") as write:
                response = app.create_account().get_json()
            self.assertTrue(response["ok"])
            card = response["account"]
            self.assertEqual(card["rivals_uid"], str(uid))
            self.assertEqual(card["in_game_name"], "Synthetic ゆい")
            self.assertEqual(card["rivals_platform"], "pc")
            self.assertEqual(card["last_refresh_status"], "ok")
            self.assertEqual(card["netease_profile"]["matches"], 273)
            self.assertIsNone(card["netease_profile"]["season"])
            self.assertEqual(card["netease_profile"]["request_scope"], "unfiltered")
            native = card["netease_native"]["profile"]
            self.assertTrue(native["matches_verified"])
            self.assertEqual(native["matches"]["data"]["match_count"], 1)
            persisted = write.call_args.args[0]
            self.assertNotIn("synthetic_match", json.dumps(persisted))
            self.assertNotIn("fixture-prior", json.dumps(persisted))
            self.assertIsNone(persisted["accounts"][0]["netease_native"]["profile"]["matches"]["data"])

    def test_raw_rank_does_not_overwrite_saved_manual_current_or_peak(self):
        card = self.card()
        updates = app._refresh_account_stats(card)
        app._apply_refresh_updates({"accounts": [card]}, card["id"], updates)
        self.assertEqual((card["current_rank"], card["current_points"]), ("Bronze III", 10))
        self.assertEqual((card["peak_rank"], card["peak_points"]), ("Diamond II", 90))
        self.assertEqual(card["netease_native"]["profile"]["current_rank"]["points"], 4350)
        self.assertIsNone(card["netease_rank"])

    def test_independent_restricted_overview_keeps_allowed_matches(self):
        source = profile(OWNER)
        source["identity"]["visibility"]["overview"]["state"] = "restricted"
        self.query.side_effect = lambda sauth, uid: QueryResult(OWNER, True, source)
        card = self.card(netease_profile={"matches": 999})
        updates = app._refresh_account_stats(card)
        app._apply_refresh_updates({"accounts": [card]}, card["id"], updates)
        public = app._public_account(card, self.key)
        self.assertIsNone(public["netease_profile"])
        native = public["netease_native"]["profile"]
        self.assertEqual(native["career"]["status"], "restricted")
        self.assertIsNone(native["current_rank"])
        self.assertTrue(native["matches_verified"])
        self.assertEqual(card["last_refresh_status"], "ok")

    def test_failure_keeps_saved_profile_and_does_not_invent_invalid_uid(self):
        card = self.card(netease_native={"fetched_at": 1, "profile": {"saved": True}})
        before = copy.deepcopy(card["netease_native"])
        self.query.side_effect = lambda sauth, uid: QueryResult(OWNER, True, None)
        updates = app._refresh_account_stats(card)
        app._apply_refresh_updates({"accounts": [card]}, card["id"], updates)
        self.assertEqual(card["netease_native"], before)
        self.assertEqual(card["last_refresh_code"], "career_identity_unavailable")
        self.assertIn("does not establish", card["last_refresh_error"])

    def test_uid_edit_during_lookup_discards_stale_card_updates(self):
        card = self.card()
        updates = app._refresh_account_stats(copy.deepcopy(card))
        card["rivals_uid"] = str(TARGET)
        app._apply_refresh_updates({"accounts": [card]}, card["id"], updates)
        self.assertNotIn("netease_native", card)
        self.assertEqual(card["rivals_uid"], str(TARGET))

    def test_uid_edit_clears_persisted_native_metadata(self):
        card = self.card(netease_native={"profile": {"old": True}})
        changed = app._account_from_payload({"rivals_uid": str(TARGET)}, self.key, existing=card)
        self.assertIsNone(changed["netease_native"])

    def test_cache_overlay_is_read_only_and_disappears_on_clear(self):
        card = self.card()
        updates = app._refresh_account_stats(card)
        app._apply_refresh_updates({"accounts": [card]}, card["id"], updates)
        before = copy.deepcopy(card)
        self.assertTrue(app._public_account(card, self.key)["netease_native"]["profile"]["matches_verified"])
        self.assertEqual(card, before)
        self.provider.clear()
        self.assertFalse(app._public_account(card, self.key)["netease_native"]["profile"]["matches_verified"])

    def test_reprojection_drops_unknown_fields_and_rejects_wrong_profile_binding(self):
        snapshot = self.provider.refresh_account(account())
        snapshot["native_profile"]["secret"] = "never-export"
        snapshot["native_profile"]["identity"]["token"] = "never-export"
        with patch.object(self.provider, "refresh_account", return_value=snapshot):
            updates = app._refresh_account_stats(self.card())
        self.assertNotIn("never-export", json.dumps(updates))
        snapshot["native_profile"]["identity"]["uid"] = str(TARGET)
        with patch.object(self.provider, "refresh_account", return_value=snapshot):
            updates = app._refresh_account_stats(self.card())
        self.assertNotIn("netease_native", updates)
        self.assertEqual(updates["last_refresh_status"], "error")

    def test_add_when_vault_locks_during_query_never_writes(self):
        with app.app.test_request_context("/api/accounts", method="POST", json={
                "rivals_uid": str(OWNER), "refresh_stats": True}), \
                patch.object(app, "_require_key", side_effect=[(self.key, None), (None, (app.jsonify({"error": "locked"}), 401))]), \
                patch.object(app, "_write_vault") as write:
            response, code = app.create_account()
        self.assertEqual(code, 401)
        write.assert_not_called()

    def test_connect_route_requires_explicit_boolean_and_does_not_accept_credentials(self):
        controller = Mock()
        controller.connect.return_value = {"ok": True, "connecting": True}
        for payload, status in (({"persist": True}, 200), ({"persist": False}, 200), ({}, 400),
                                ({"persist": "true"}, 400), ({"persist": True, "sessionid": "no"}, 400)):
            with app.app.test_request_context("/api/netease/connect", method="POST", json=payload), \
                    patch.object(app, "_require_key", return_value=(self.key, None)), \
                    patch.object(app, "_career_provider", controller):
                result = app.connect_native_netease()
            code = result[1] if isinstance(result, tuple) else result.status_code
            self.assertEqual(code, status)
        self.assertEqual(controller.connect.call_count, 2)

    def test_recover_does_not_return_result_after_vault_key_changes(self):
        controller = Mock()
        controller.recover.return_value = {"outcome": "session_lookup_complete"}
        with app.app.test_request_context("/api/netease/recover", method="POST", json={}), \
                patch.object(app, "_require_key", side_effect=[(self.key, None), (b"B" * 32, None)]), \
                patch.object(app, "_career_provider", controller):
            response, code = app.recover_native_netease()
        self.assertEqual(code, 401)

    def test_background_session_checks_do_not_extend_idle_unlock(self):
        with patch.object(app, "_state", {"key": self.key, "last_activity": 100}), \
                patch.object(app, "_idle_timeout_s", return_value=30), \
                patch.object(app.time, "time", return_value=110):
            self.assertEqual(app._current_key(touch=False), self.key)
            self.assertEqual(app._state["last_activity"], 100)
        with patch.object(app, "_state", {"key": self.key, "last_activity": 100}), \
                patch.object(app, "_idle_timeout_s", return_value=30), \
                patch.object(app.time, "time", return_value=140):
            self.assertIsNone(app._current_key(touch=False))
            self.assertIsNone(app._state["key"])

    def test_native_preview_cannot_be_replaced_by_standard_release(self):
        with app.app.test_request_context("/api/check-update"), \
                patch.object(app, "_fetch_latest_release") as fetch:
            self.assertFalse(app.check_update().get_json()["has_update"])
            fetch.assert_not_called()
        with app.app.test_request_context("/api/apply-update", method="POST"), \
                patch.object(app, "_require_key", return_value=(self.key, None)), \
                patch.object(app, "_fetch_latest_release") as fetch:
            response, code = app.apply_update()
            self.assertEqual(code, 409)
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
