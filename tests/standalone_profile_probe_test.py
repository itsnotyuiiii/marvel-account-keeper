import copy
import json
import unittest
from unittest.mock import Mock, patch

import standalone_profile_probe as profile
from tests.standalone_social_transport_test import session, PARAMETER
from tests.standalone_career_query_test import wrapped


def user_data(*, uid=456, zone=1, config=None):
    config = {"login_os": "1", "career_settings": json.dumps({
        "CareerOverviewIsVisibleToOther_1": 0, "CareerHeroDataIsVisibleToOther_1": 1,
        "BattleHistoryIsVisibleToOther": 2, "TimelineIsVisibleToOther": 0})} if config is None else config
    return {"data": [{"aid": f"{zone}_{uid}", "name": "FixturePlayer", "grade": 32,
                      "config_server": config, "private_account": "SECRET_PRIVATE_ACCOUNT"}],
            "private_status": "SECRET_PRIVATE_STATUS"}


class StandaloneProfileProbeTest(unittest.TestCase):
    def project(self, data=None, **kwargs):
        return profile.project_mesh_user(user_data() if data is None else data,
            target_uid=kwargs.pop("target_uid", 456), own_uid=kwargs.pop("own_uid", 123),
            zone_id=kwargs.pop("zone_id", 1), **kwargs)

    def test_exact_bound_identity_level_and_independent_section_settings(self):
        original = user_data()
        result = self.project(original)
        self.assertEqual(original, user_data())
        self.assertEqual(result["uid"], "456")
        self.assertEqual(result["in_game_name"], "FixturePlayer")
        self.assertEqual(result["level"], 32)
        self.assertEqual(result["platform"], "pc")
        self.assertEqual({key: value["state"] for key, value in result["visibility"].items()},
                         {"overview": "allowed", "heroes": "restricted", "history": "restricted", "timeline": "allowed"})
        self.assertFalse(result["career_verified"])
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("1_456", json.dumps(result))

    def test_payload_row_count_and_exact_target_uid_are_not_owner_header_identity(self):
        cases = [(None, "uid_lookup_payload_shape"), ({}, "uid_lookup_payload_shape"),
                 ({"data": []}, "uid_lookup_no_data"),
                 ({"data": user_data()["data"] * 2}, "uid_lookup_row_count"),
                 (user_data(uid=123), "uid_lookup_identity_mismatch"),
                 (user_data(zone=2), "uid_lookup_identity_mismatch")]
        for data, code in cases:
            with self.subTest(code=code), self.assertRaises(profile.ProfileLookupError) as error:
                profile.project_mesh_user(data, target_uid=456, own_uid=123, zone_id=1)
            self.assertEqual(error.exception.code, code)
        for value in (True, 0, -1, "456", 1 << 64):
            with self.assertRaises(profile.ProfileLookupError):
                self.project(target_uid=value)

    def test_malformed_or_unavailable_configuration_does_not_become_public(self):
        for setting in (None, "{", "[]", "x" * 8193,
                        '{"BattleHistoryIsVisibleToOther":0,"BattleHistoryIsVisibleToOther":1}'):
            result = self.project(user_data(config={"career_settings": setting}))
            self.assertTrue(result["identity_verified"])
            self.assertTrue(all(row["state"] == "unknown" for row in result["visibility"].values()))
        data = user_data()
        del data["data"][0]["config_server"]
        result = self.project(data)
        self.assertTrue(result["identity_verified"])
        self.assertIsNone(result["platform"])
        self.assertEqual(result["visibility"]["history"]["state"], "unknown")

    def test_valid_settings_map_uses_documented_open_default_for_missing_keys(self):
        for config in ({}, {"career_settings": {}}, {"career_settings": "{}"}):
            result = self.project(user_data(config=config))
            self.assertTrue(result["shape"]["settings_decoded"])
            self.assertEqual(result["shape"]["settings_defaulted"], "career_settings" not in config)
            self.assertTrue(all(row == {"state": "allowed", "basis": "native_default", "permission": 0}
                                for row in result["visibility"].values()))

    def test_platform_key_precedes_legacy_but_history_and_timeline_are_global(self):
        settings = {"CareerOverviewIsVisibleToOther": 0, "CareerOverviewIsVisibleToOther_1": 1,
                    "CareerOverviewIsVisibleToOther_2": 0, "CareerHeroDataIsVisibleToOther": 4,
                    "BattleHistoryIsVisibleToOther": 1, "BattleHistoryIsVisibleToOther_1": 0,
                    "TimelineIsVisibleToOther": 3, "TimelineIsVisibleToOther_1": 0}
        result = self.project(user_data(config={"login_os": 2, "career_settings": settings}))
        self.assertEqual(result["platform"], "playstation")
        self.assertTrue(all(value["state"] == "restricted" for value in result["visibility"].values()))
        console = self.project(user_data(config={"career_settings": settings}), platform="playstation")
        self.assertEqual(console["visibility"]["overview"]["state"], "allowed")
        settings["CareerOverviewIsVisibleToOther_1"] = True
        result = self.project(user_data(config={"career_settings": settings}))
        self.assertEqual(result["visibility"]["overview"]["state"], "unknown")
        self.assertEqual(result["visibility"]["heroes"]["permission"], 4)

    def test_owner_access_is_independent_of_other_player_settings(self):
        result = self.project(user_data(uid=123, config={}), target_uid=123)
        self.assertTrue(result["is_owner"])
        self.assertTrue(all(row == {"state": "allowed", "basis": "owner", "permission": None}
                            for row in result["visibility"].values()))

    def test_unrecognized_optional_name_level_platform_do_not_invalidate_uid(self):
        for name, grade, os_id in ((None, True, True), ("bad\nname", -1, "bogus"),
                                  ("\ud800", "32", 0), ("x" * 65, 1_000_001, 99)):
            data = user_data(config={"login_os": os_id})
            data["data"][0].update(name=name, grade=grade, level=999)
            result = self.project(data)
            self.assertTrue(result["identity_verified"])
            self.assertIsNone(result["in_game_name"])
            self.assertIsNone(result["level"])
            self.assertIsNone(result["platform"])

    def test_one_transient_query_is_projected_only_after_verified_parent(self):
        owner = session()
        context = Mock()
        context.read_mesh_user.return_value = user_data()
        parent = {"social_session_accepted": True, "setup_consumer_completed": True,
                  "mesh_request_bound": True}
        def setup(*args, **kwargs):
            self.assertEqual(args, (owner,))
            self.assertEqual(kwargs["parameter"], PARAMETER)
            kwargs["on_setup"](context)
            return copy.deepcopy(parent)
        with patch.object(profile, "run_social_setup_once", side_effect=setup) as run:
            result = profile.run_uid_lookup_once(owner, parameter=PARAMETER, target_uid=456)
        context.read_mesh_user.assert_called_once_with(456)
        self.assertTrue(result["profile_lookup"]["identity_verified"])
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("SECRET", json.dumps(result))
        for key in parent:
            for unverified in (False, 1, None):
                saved = parent[key]
                parent[key] = unverified
                with patch.object(profile, "run_social_setup_once", side_effect=setup):
                    result = profile.run_uid_lookup_once(owner, parameter=PARAMETER, target_uid=456)
                self.assertFalse(result["profile_lookup"]["identity_verified"])
                parent[key] = saved

    def test_invalid_context_prevents_setup_and_empty_rows_are_not_invalid_uid(self):
        with patch.object(profile, "run_social_setup_once") as setup:
            for target in (0, -1, True, "456"):
                with self.assertRaises(profile.ProfileLookupError):
                    profile.run_uid_lookup_once(session(), parameter=PARAMETER, target_uid=target)
            setup.assert_not_called()
        def no_data(owner, **kwargs):
            context = Mock()
            context.read_mesh_user.return_value = {"data": []}
            kwargs["on_setup"](context)
            return {"social_session_accepted": True, "setup_consumer_completed": True,
                    "mesh_request_bound": True}
        with patch.object(profile, "run_social_setup_once", side_effect=no_data):
            result = profile.run_uid_lookup_once(session(), parameter=PARAMETER, target_uid=456)
        self.assertEqual(result["profile_lookup"]["outcome"], "uid_lookup_no_data")
        self.assertFalse(result["profile_lookup"]["identity_verified"])

    def test_career_requires_exact_owner_and_preserves_identity_on_section_failure(self):
        owner = session()
        with patch.object(profile, "run_social_setup_once") as setup:
            for target, mode in ((456, True), (123, 1), (123, "true")):
                with self.assertRaises(profile.ProfileLookupError):
                    profile.run_uid_lookup_once(owner, parameter=PARAMETER, target_uid=target, own_career=mode)
            setup.assert_not_called()
        for failure in (False, "transport", "identity"):
            context = Mock()
            context.read_mesh_user.return_value = user_data(uid=123)
            context.read_own_career.return_value = wrapped()
            if failure == "transport":
                context.read_own_career.side_effect = ValueError("PRIVATE_EXCEPTION")
            if failure == "identity":
                from tests.standalone_career_query_test import payload
                context.read_own_career.return_value = wrapped(payload(player_uid=456))
            def setup_once(owner, **kwargs):
                kwargs["on_setup"](context)
                return {"social_session_accepted": True, "setup_consumer_completed": True,
                        "mesh_request_bound": True, "career_reply_bound": failure != "transport"}
            with patch.object(profile, "run_social_setup_once", side_effect=setup_once):
                result = profile.run_uid_lookup_once(owner, parameter=PARAMETER, target_uid=123, own_career=True)
            self.assertTrue(result["profile_lookup"]["identity_verified"])
            self.assertEqual(result["career_lookup"]["career_verified"], failure is False)
            self.assertNotIn("PRIVATE", json.dumps(result))
            context.read_own_career.assert_called_once_with()

    def test_missing_owner_identity_never_queries_career(self):
        context = Mock()
        context.read_mesh_user.return_value = user_data(uid=456)
        def setup_once(owner, **kwargs):
            kwargs["on_setup"](context)
            return {"social_session_accepted": True, "setup_consumer_completed": True,
                    "mesh_request_bound": True, "career_reply_bound": False}
        with patch.object(profile, "run_social_setup_once", side_effect=setup_once):
            result = profile.run_uid_lookup_once(session(), parameter=PARAMETER, target_uid=123, own_career=True)
        self.assertFalse(result["profile_lookup"]["identity_verified"])
        self.assertFalse(result["career_lookup"]["career_verified"])
        context.read_own_career.assert_not_called()


if __name__ == "__main__":
    unittest.main()
