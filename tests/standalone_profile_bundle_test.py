"""Synthetic sequential bundle composition; no credentials or network."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import msgpack

from game_career_probe import StartupEvent
import portable_game_runtime as runtime
import standalone_profile_bundle as bundle
from standalone_social_session import GatewaySocialToken, SocialRoute
from tests.standalone_social_transport_test import PARAMETER
from tests.web_sdk_auth_probe_test import fixture as sauth


OWNER, TARGET = 1370598422, 1822797559
ZONE = {"role_uid": OWNER, "zone_id": 11001}


def token_session():
    return GatewaySocialToken("PRIVATE_TOKEN", OWNER, 11001, f"11001_{OWNER}",
                              SocialRoute("8.8.8.8", 10001), 990, 1100, None)


def permission(state="allowed"):
    return {"state": state, "basis": "global_setting", "permission": 0 if state == "allowed" else 1}


def career(uid=TARGET):
    return {"outcome": "other_career_verified", "career_verified": True, "identity_verified": True,
        "request_bound": True, "reply_context_verified": True, "uid": str(uid), "request_scope": "unfiltered",
        "statistics": {"matches": 100, "wins": 60, "kills": 900, "deaths": 300, "assists": 700,
                       "private": "PRIVATE_STAT"}, "connection_reuse_allowed": True,
        "private": "PRIVATE_CAREER"}


def ranks(uid=TARGET):
    return {"ranks_verified": True, "identity_verified": True, "request_bound": True,
        "uid": str(uid), "platform": "pc", "rank_data_present": True,
        "seasons": [{"season": 19, "level": 23, "points": 5560.6176, "peak_level": 23,
            "peak_points": 5570.4144, "placements_completed": 10, "updated_at": 1700000000,
            "record": {"matches": 28, "wins": 22, "private": "PRIVATE_RECORD"}, "private": "PRIVATE_RANK"}],
        "private": "PRIVATE_RANKS"}


def matches(uid=TARGET):
    return {"matches_verified": True, "identity_verified": True, "request_bound": True, "reply_context_verified": True,
        "uid": str(uid), "page": 0, "page_size": 50, "match_count": 1,
        "matches": [{"match_uid": "fixture-match", "played_at": 1700000000, "season": 19,
            "mode_id": 2, "duration_seconds": 600, "result": "win", "kills": 22, "deaths": 3, "assists": 10,
            "rank_change_verified": True, "rank_change": {"level_before": 23, "level_after": 23,
                "points_before": 5500, "points_after": 5515, "points_delta": 15,
                "placements_completed": 10, "private": "PRIVATE_DELTA"}, "private": "PRIVATE_MATCH"}],
        "has_more": True, "connection_reuse_allowed": True, "private": "PRIVATE_MATCHES"}


def setup(uid=TARGET):
    return {"outcome": "social_setup_verified", "social_session_accepted": True, "setup_consumer_completed": True, "mesh_request_bound": True,
        "career_reply_bound": True, "rank_reply_bound": True, "own_match_reply_bound": True,
        "profile_lookup": {"outcome": "uid_profile_identity_verified", "identity_verified": True, "uid": str(uid), "is_owner": uid == OWNER,
            "requested_platform": "pc", "platform": "pc", "in_game_name": "FixturePlayer", "level": 63,
            "visibility": {name: permission() for name in ("overview", "heroes", "history", "timeline")},
            "private": "PRIVATE_IDENTITY"}, "career_lookup": career(uid), "rank_lookup": ranks(uid),
        "match_lookup": {key: value for key, value in matches(uid).items() if key != "reply_context_verified"},
        "private": "PRIVATE_SETUP"}


def panel():
    return {"owner_context_valid": True, "uid": str(OWNER), "rank_panel_verified": True,
        "current_season_verified": True, "current_season": 19, "own_rank_verified": True,
        "current": {"season": 19, "level": 5, "points": 1234.5, "points_verified": True,
                    "private": "PRIVATE_PANEL"}, "connection_reuse_allowed": True}


def auth():
    return {"auth_service_accepted": True, "own_role_verified": True, "game_login_verified": True,
        "social_token": {"outcome": "social_token_claims_validated", **{key: True for key in ("token_rpc_received", "social_token_received",
            "owner_claim_matches", "time_claims_valid", "route_shape_valid", "consumer_completed",
            "on_game_connection_completed")}}, "sauth": "PRIVATE_AUTH"}


def owner_create(*, uid=OWNER, zone_id=11001, eid=98765, rank_map=None):
    def properties(class_name, fields):
        data = msgpack.packb(class_name, use_bin_type=True)
        for name, value in fields.items():
            data += msgpack.packb(name, use_bin_type=True) + msgpack.packb(value, use_bin_type=True)
        return msgpack.ExtType(0, data)

    if rank_map is None:
        rank_map = {1001019: {"rank_game_id": 19, "level": 14, "rank_score": 4350.5,
            "max_level": 15, "max_rank_score": 4500.25, "battle_count": 100, "win_count": 60,
            "private": "PRIVATE_ROW"}}
    component = properties("RankGameComponent", {"ranks": rank_map, "private": "PRIVATE_COMPONENT"})
    root = properties("GameServiceAvatar", {"uid": uid, "zone_id": zone_id,
        "RankGame": component, "private": ["PRIVATE_ROOT", {"key": "PRIVATE_VALUE"}]})
    return StartupEvent("create", eid, "GameServiceAvatar", msgpack.packb(root, use_bin_type=True))


class StandaloneProfileBundleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        provenance, digest = runtime.reviewed_runtime_pin("3848703")
        cls.public = runtime.load_runtime_manifest(Path(__file__).resolve().parents[1] / "runtime-inputs" / "3848703.json",
            expected_sha256=digest, expected_provenance=provenance)
        cls.server = bundle.login.LoginServer("8.8.8.8", 13001, (10001,))

    def run_case(self, *, uid=TARGET, accepted=None, rank_panel=None, career_result=None, match_result=None,
                 auth_result=None, zone=None, clock=None, token=None, invoke_game=True,
                 twice_token=False, twice_game=False, startup_events=(), panel_events=()):
        accepted = setup(uid) if accepted is None else accepted
        rank_panel = panel() if rank_panel is None else rank_panel
        career_result = career(uid) if career_result is None else career_result
        match_result = matches(uid) if match_result is None else match_result
        auth_result = auth() if auth_result is None else auth_result
        conn, transport, calls = object(), object(), []
        observer = None
        selected_zone = ZONE if zone is None else zone

        def social_lookup(session, **options):
            calls.append("social")
            self.assertEqual(options, {"parameter": PARAMETER, "target_uid": uid, "platform": "pc",
                **({"own_career": True, "own_matches": True} if uid == OWNER else {"other_ranks": True})})
            return accepted

        def read_panel(connection, cipher, received_zone, **options):
            calls.append("panel")
            self.assertIs(connection, conn)
            self.assertIs(cipher, transport)
            self.assertIs(received_zone, selected_zone)
            if uid == OWNER:
                self.assertIs(options.pop("on_startup"), observer)
                for event in panel_events:
                    observer(event)
            self.assertEqual(options, {"client_version": "3848703", "deadline": 115})
            if isinstance(rank_panel, Exception):
                raise rank_panel
            return rank_panel

        def native(result, name):
            def read(connection, cipher, received_zone, methods, **options):
                calls.append(name)
                self.assertIs(connection, conn)
                self.assertIs(cipher, transport)
                self.assertIs(received_zone, selected_zone)
                self.assertIs(methods, self.public.career_methods)
                self.assertIs(options["setup_report"], accepted)
                self.assertEqual(options["target_uid"], uid)
                self.assertEqual(options["deadline"], 115)
                if isinstance(result, Exception):
                    raise result
                return result
            return read

        def run(given_sauth, configuration, server, **options):
            nonlocal observer
            calls.append("login")
            self.assertEqual(given_sauth, sauth())
            self.assertIs(configuration, self.public.configuration)
            self.assertIs(server, self.server)
            self.assertEqual(options["client_version"], "3848703")
            if uid == OWNER:
                observer = options["on_startup"]
                self.assertTrue(callable(observer))
                for event in startup_events:
                    observer(event)
            else:
                self.assertNotIn("on_startup", options)
            current_token = token_session() if token is None else token
            options["on_token"](current_token)
            if twice_token:
                with self.assertRaises(bundle.ProfileBundleError):
                    options["on_token"](current_token)
            if invoke_game:
                options["on_game_connection"](conn, transport, selected_zone)
                if twice_game:
                    with self.assertRaises(bundle.ProfileBundleError):
                        options["on_game_connection"](conn, transport, selected_zone)
            return auth_result

        with patch.object(bundle, "run_social_token_once", side_effect=run) as login_mock, \
             patch.object(bundle, "run_uid_lookup_once", side_effect=social_lookup) as social_mock, \
             patch.object(bundle, "read_rank_panel_once", side_effect=read_panel) as panel_mock, \
             patch.object(bundle, "read_other_career_once", side_effect=native(career_result, "career")) as career_mock, \
             patch.object(bundle, "read_other_matches_once", side_effect=native(match_result, "matches")) as matches_mock, \
             patch.object(bundle.time, "monotonic", side_effect=clock, return_value=100):
            result = bundle.run_profile_bundle(sauth(), self.public.configuration, self.server, parameter=PARAMETER,
                career_methods=self.public.career_methods, target_uid=uid)
        self.assertNotIn("PRIVATE", json.dumps(result, allow_nan=False))
        self.assertFalse(result["secrets_saved"])
        self.assertFalse(result["remote_code_executed"])
        return result, calls, (login_mock, social_mock, panel_mock, career_mock, matches_mock)

    def test_one_login_one_social_then_same_gateway_sequential_three_reads(self):
        result, calls, mocks = self.run_case()
        self.assertEqual(calls, ["login", "social", "panel", "career", "matches"])
        for method in mocks:
            self.assertEqual(method.call_count, 1)
        self.assertEqual(result["outcome"], "profile_bundle_verified")
        for key in ("owner_authentication_verified", "game_login_verified", "identity_verified",
                    "career_verified", "ranks_verified", "matches_verified", "current_season_verified"):
            self.assertTrue(result[key])
        self.assertEqual(result["identity"]["uid"], str(TARGET))
        self.assertEqual(result["current_rank"]["level"], 23)
        self.assertEqual(result["current_rank"]["points"], 5560.6176)
        self.assertEqual(result["current_rank_source"], "target_season_history")
        self.assertNotIn(str(OWNER), json.dumps(result))
        self.assertIsNone(result["matches"]["data"]["has_more"])
        row = result["matches"]["data"]["matches"][0]
        self.assertEqual(row["uid"], str(TARGET))
        self.assertEqual(row["rank_change"]["points_delta"], 15)
        self.assertFalse(result["rank_labels_verified"])
        self.assertFalse(result["overall_peak_verified"])
        self.assertEqual(result["identity_outcome"], "uid_profile_identity_verified")
        self.assertEqual(result["token_outcome"], "social_token_claims_validated")
        self.assertEqual(result["social_setup_outcome"], "social_setup_verified")

    def test_only_correlated_login_auth_rejection_code_is_retained(self):
        rejected = {"outcome": "server_rejected_authentication", "prepare_verified": True,
            "credentials_sent": True, "auth_service_accepted": False, "own_role_verified": False,
            "game_login_verified": False, "server_error_code": 253, "sdk_code": 253}
        for code in (253, 1911, -1):
            result, _, _ = self.run_case(auth_result={**rejected, "server_error_code": code}, invoke_game=False)
            self.assertEqual(result["auth_rejection_code"], code)
            self.assertFalse(result["owner_authentication_verified"])
        for changes in ({"outcome": "authentication_timeout"}, {"prepare_verified": False},
                        {"credentials_sent": 1}, {"auth_service_accepted": True},
                        {"own_role_verified": True}, {"game_login_verified": True},
                        {"server_error_code": True}, {"server_error_code": "253"},
                        {"server_error_code": None}, {"server_error_code": 0},
                        {"server_error_code": 2**31}, {"server_error_code": -(2**31)-1}):
            result, _, _ = self.run_case(auth_result={**rejected, **changes}, invoke_game=False)
            self.assertIsNone(result["auth_rejection_code"])
        for missing in ("prepare_verified", "credentials_sent", "auth_service_accepted", "own_role_verified", "game_login_verified"):
            value = {key: val for key, val in rejected.items() if key != missing}
            result, _, _ = self.run_case(auth_result=value, invoke_game=False)
            self.assertIsNone(result["auth_rejection_code"])

    def test_owner_uses_social_career_matches_and_panel_not_other_native_sections(self):
        result, calls, mocks = self.run_case(uid=OWNER)
        self.assertEqual(calls, ["login", "social", "panel"])
        self.assertTrue(result["career_verified"])
        self.assertEqual(result["current_rank"]["level"], 5)
        self.assertEqual(result["current_rank_source"], "owner_panel")
        self.assertEqual(result["ranks"]["outcome"], "owner_rank_not_observed")
        self.assertEqual(result["matches"]["outcome"], "profile_matches_verified")
        self.assertFalse(result["ranks_verified"])
        self.assertTrue(result["matches_verified"])
        self.assertEqual(result["matches"]["data"]["uid"], str(OWNER))
        self.assertEqual(result["matches"]["data"]["history_completeness"], "first_page_only")
        mocks[3].assert_not_called()
        mocks[4].assert_not_called()

    def test_owner_startup_ranks_bind_decoded_uid_zone_not_engine_id(self):
        result, calls, _ = self.run_case(uid=OWNER, startup_events=[owner_create()])
        self.assertEqual(result["outcome"], "profile_bundle_verified")
        self.assertEqual(calls, ["login", "social", "panel"])
        self.assertTrue(result["ranks_verified"])
        self.assertEqual(result["ranks"]["data"]["seasons"][0]["points"], 4350.5)
        self.assertEqual(result["current_rank"]["points"], 1234.5)
        self.assertEqual(result["current_rank_source"], "owner_panel")

    def test_owner_entity_can_arrive_during_panel_read(self):
        result, _, _ = self.run_case(uid=OWNER, panel_events=[owner_create()])
        self.assertTrue(result["ranks_verified"])

    def test_owner_reviewed_scalar_update_preserves_available_history(self):
        payload = b"".join(msgpack.packb(value, use_bin_type=True) for value in (1, 1, "level", 32))
        event = StartupEvent("property", 98765, data=msgpack.packb([msgpack.ExtType(2, payload)]))
        result, _, _ = self.run_case(uid=OWNER, startup_events=[owner_create()], panel_events=[event])
        self.assertEqual(result["outcome"], "profile_bundle_verified")
        self.assertTrue(result["ranks_verified"])
        self.assertEqual(result["ranks"]["data"]["seasons"][0]["points"], 4350.5)

    def test_owner_unknown_update_only_exposes_fixed_diagnostics(self):
        payload = b"".join(msgpack.packb(value, use_bin_type=True) for value in (
            1, 1, "PRIVATE_UNKNOWN_ROOT", "PRIVATE_VALUE"))
        event = StartupEvent("property", 98765, data=msgpack.packb([msgpack.ExtType(2, payload)]))
        result, _, _ = self.run_case(uid=OWNER, startup_events=[owner_create()], panel_events=[event])
        self.assertFalse(result["ranks_verified"])
        self.assertEqual(result["ranks"]["sync_roots"], ["unreviewed_root"])
        self.assertEqual(result["ranks"]["sync_outcome"], "owner_rank_sync_root_unreviewed")
        self.assertEqual(result["ranks"]["initialization_season_count"], 1)

    def test_owner_lifecycle_after_snapshot_cannot_leave_stale_ranks(self):
        for event in (StartupEvent("destroy", 98765), StartupEvent("property", 98765, data=b"PRIVATE_SYNC"),
                      StartupEvent("reset"), StartupEvent("create", 98765, "OtherClass", b"PRIVATE")):
            with self.subTest(event=event):
                result, _, _ = self.run_case(uid=OWNER, startup_events=[owner_create()], panel_events=[event])
                self.assertFalse(result["ranks_verified"])
                self.assertEqual(result["ranks"]["initialization_outcome"],
                    "owner_rank_entity_recreated" if event.kind == "create" else "owner_rank_snapshot_projected")
                self.assertTrue(result["career_verified"])
                self.assertTrue(result["matches_verified"])
                self.assertIsNotNone(result["current_rank"])

    def test_owner_identity_shape_and_zone_fail_without_breaking_other_sections(self):
        for event in (owner_create(uid=TARGET), owner_create(zone_id=11002),
                      owner_create(rank_map={"1001019": {}}),
                      StartupEvent("create", 98765, "GameServiceAvatar", b"PRIVATE_BAD_WIRE")):
            result, _, _ = self.run_case(uid=OWNER, startup_events=[event])
            self.assertFalse(result["ranks_verified"])
            self.assertTrue(result["career_verified"])
            self.assertTrue(result["matches_verified"])

    def test_owner_optional_observer_failure_never_exposes_prior_snapshot(self):
        value = auth()
        value["social_token"]["startup_observer_failed"] = True
        for options in ({"auth_result": value}, {"rank_panel": {**panel(), "startup_observer_failed": True}}):
            result, _, _ = self.run_case(uid=OWNER, startup_events=[owner_create()], **options)
            self.assertEqual(result["ranks"]["outcome"], "profile_owner_rank_observer_failed")
            self.assertTrue(result["career_verified"])

    def test_owner_snapshot_not_exposed_after_unverified_stream_or_authentication(self):
        failed_auth = auth()
        failed_auth["game_login_verified"] = False
        for options in ({"rank_panel": {"outcome": "rank_panel_timeout", "connection_reuse_allowed": False}},
                        {"rank_panel": RuntimeError("PRIVATE")}, {"invoke_game": False},
                        {"auth_result": failed_auth}):
            result, _, _ = self.run_case(uid=OWNER, startup_events=[owner_create()], **options)
            self.assertFalse(result["ranks_verified"])
            self.assertIsNone(result["ranks"]["data"])

    def test_owner_empty_rank_map_is_available_not_missing(self):
        result, _, _ = self.run_case(uid=OWNER, startup_events=[owner_create(rank_map={})])
        self.assertTrue(result["ranks_verified"])
        self.assertEqual(result["ranks"]["data"]["seasons"], [])

    def test_owner_match_failure_preserves_identity_career_and_current_panel(self):
        value = setup(OWNER)
        value["match_lookup"] = {"outcome": "xyq_http_rejected", "http_status": 500,
                                 "matches_verified": False, "private": "PRIVATE_FAILURE"}
        result, calls, _ = self.run_case(uid=OWNER, accepted=value)
        self.assertTrue(result["identity_verified"])
        self.assertTrue(result["career_verified"])
        self.assertTrue(result["current_season_verified"])
        self.assertFalse(result["matches_verified"])
        self.assertEqual(result["matches"]["outcome"], "xyq_http_rejected")
        self.assertEqual(calls, ["login", "social", "panel"])

    def test_owner_match_available_independently_of_career_and_native_panel_failures(self):
        value = setup(OWNER)
        value["career_lookup"] = {"career_verified": False, "outcome": "own_career_statistics_shape"}
        result, _, _ = self.run_case(uid=OWNER, accepted=value,
                                     rank_panel={"outcome": "rank_panel_timeout", "connection_reuse_allowed": False})
        self.assertTrue(result["identity_verified"])
        self.assertTrue(result["matches_verified"])
        self.assertFalse(result["career_verified"])
        self.assertFalse(result["current_season_verified"])
        raw_auth = auth()
        raw_auth["social_token"]["on_game_connection_completed"] = False
        result, _, _ = self.run_case(uid=OWNER, auth_result=raw_auth)
        self.assertTrue(result["matches_verified"])
        self.assertFalse(result["current_season_verified"])

    def test_owner_matches_require_same_social_reply_and_exact_projected_uid(self):
        for key, invalid in (("uid", str(TARGET)), ("identity_verified", False),
                             ("request_bound", False), ("matches_verified", 1),
                             ("page", 1), ("page_size", 51)):
            value = setup(OWNER)
            value["match_lookup"][key] = invalid
            result, _, _ = self.run_case(uid=OWNER, accepted=value)
            self.assertTrue(result["identity_verified"])
            self.assertTrue(result["career_verified"])
            self.assertFalse(result["matches_verified"])
        value = setup(OWNER)
        value["own_match_reply_bound"] = False
        result, _, _ = self.run_case(uid=OWNER, accepted=value)
        self.assertFalse(result["matches_verified"])
        self.assertTrue(result["identity_verified"])

    def test_owner_empty_page_is_available_but_not_complete_history(self):
        value = setup(OWNER)
        value["match_lookup"].update(matches=[], match_count=0)
        result, _, _ = self.run_case(uid=OWNER, accepted=value)
        self.assertTrue(result["matches_verified"])
        self.assertEqual(result["matches"]["data"]["match_count"], 0)
        self.assertEqual(result["matches"]["data"]["history_completeness"], "first_page_only")
        self.assertIsNone(result["matches"]["data"]["has_more"])

    def test_owner_points_honor_explicit_source_verification(self):
        value = panel()
        value["current"]["points_verified"] = False
        result, _, _ = self.run_case(uid=OWNER, rank_panel=value)
        self.assertEqual(result["current_rank"]["level"], 5)
        self.assertIsNone(result["current_rank"]["points"])
        self.assertFalse(result["current_rank"]["points_verified"])

    def test_target_current_rank_requires_matching_verified_season_not_max_or_owner(self):
        value = panel()
        value["current_season"] = 20
        value["current"]["season"] = 20
        result, _, _ = self.run_case(rank_panel=value)
        self.assertTrue(result["ranks_verified"])
        self.assertEqual(result["current_season"], 20)
        self.assertIsNone(result["current_rank"])
        self.assertIsNone(result["current_rank_source"])

    def test_overview_restriction_does_not_suppress_public_matches(self):
        value = setup()
        value["profile_lookup"]["visibility"]["overview"] = permission("restricted")
        result, calls, mocks = self.run_case(accepted=value)
        self.assertEqual(calls, ["login", "social", "panel", "matches"])
        self.assertEqual(result["career"]["status"], "restricted")
        self.assertEqual(result["ranks"]["status"], "restricted")
        self.assertTrue(result["matches_verified"])
        mocks[3].assert_not_called()

    def test_history_restriction_does_not_suppress_overview_or_rank_history(self):
        value = setup()
        value["profile_lookup"]["visibility"]["history"] = permission("restricted")
        result, calls, mocks = self.run_case(accepted=value)
        self.assertTrue(result["career_verified"])
        self.assertTrue(result["ranks_verified"])
        self.assertEqual(result["matches"]["status"], "restricted")
        self.assertEqual(calls, ["login", "social", "panel", "career"])
        mocks[4].assert_not_called()

    def test_unknown_settings_never_authorize_native_section_and_do_not_erase_identity(self):
        value = setup()
        value["profile_lookup"]["visibility"] = {"overview": {"state": "PRIVATE"}}
        result, calls, _ = self.run_case(accepted=value)
        self.assertTrue(result["identity_verified"])
        self.assertEqual(calls, ["login", "social", "panel"])
        self.assertTrue(all(result[key]["status"] == "unknown" for key in ("career", "ranks", "matches")))

    def test_correlated_service_error_and_projection_failure_preserve_later_sections(self):
        for failure in ({"outcome": "other_career_reply_unverified", "connection_reuse_allowed": True},
                        {"outcome": "other_career_statistics_shape", "connection_reuse_allowed": True}):
            result, calls, _ = self.run_case(career_result=failure)
            self.assertFalse(result["career_verified"])
            self.assertTrue(result["ranks_verified"])
            self.assertTrue(result["matches_verified"])
            self.assertEqual(calls[-1], "matches")
            self.assertTrue(result["native_connection_reusable"])

    def test_uncertain_native_stream_stops_next_operation_without_erasing_social_sections(self):
        for failure in ({"outcome": "other_career_read_failed", "connection_reuse_allowed": False},
                        OSError("PRIVATE_PARTIAL_SEND")):
            result, calls, mocks = self.run_case(career_result=failure)
            self.assertEqual(calls, ["login", "social", "panel", "career"])
            self.assertTrue(result["identity_verified"])
            self.assertTrue(result["ranks_verified"])
            self.assertTrue(result["current_season_verified"])
            self.assertFalse(result["native_connection_reusable"])
            self.assertEqual(result["matches"]["outcome"], "profile_native_connection_unusable")
            mocks[4].assert_not_called()

    def test_fixed_native_rejection_code_is_actionable_without_free_server_text(self):
        cases = [("career", "career_server_error", "profile_career_service_rejected"),
                 ("matches", "match_summary_server_error", "profile_matches_service_rejected")]
        for kind, native_outcome, expected in cases:
            value = {"outcome": "other_" + kind + "_reply_unverified", "native_outcome": native_outcome,
                     "server_error_code": 1911, "server_error_message": "PRIVATE_SERVER_MESSAGE",
                     "connection_reuse_allowed": True, "reply_context_verified": True}
            result, _, _ = self.run_case(**{"career_result" if kind == "career" else "match_result": value})
            self.assertEqual(result[kind]["outcome"], expected)
            self.assertEqual(result[kind]["source_error_code"], 1911)
            self.assertTrue(result["identity_verified"])
        for code in (True, "PRIVATE_CODE", 10**100, 0):
            value = {"outcome": "PRIVATE", "native_outcome": "career_server_error", "server_error_code": code,
                     "connection_reuse_allowed": True, "reply_context_verified": True}
            result, _, _ = self.run_case(career_result=value)
            self.assertNotIn("source_error_code", result["career"])
            self.assertEqual(result["career"]["outcome"], "profile_career_not_verified")

    def test_panel_failure_stops_native_reads_but_preserves_identity_and_ranks(self):
        for value in ({"outcome": "rank_panel_timeout", "connection_reuse_allowed": False},
                      RuntimeError("PRIVATE_PANEL_FAILURE")):
            result, calls, _ = self.run_case(rank_panel=value)
            self.assertEqual(calls, ["login", "social", "panel"])
            self.assertTrue(result["identity_verified"])
            self.assertTrue(result["ranks_verified"])
            self.assertFalse(result["current_season_verified"])
            self.assertIsNone(result["current_rank"])

    def test_shared_native_deadline_skips_later_requests_and_retains_completed_sections(self):
        result, calls, _ = self.run_case(clock=[100, 116, 116])
        self.assertEqual(calls, ["login", "social", "panel"])
        self.assertEqual(result["career"]["outcome"], "profile_native_deadline")
        self.assertEqual(result["matches"]["outcome"], "profile_native_deadline")
        self.assertTrue(result["current_season_verified"])
        result, calls, _ = self.run_case(clock=[100, 101, 116])
        self.assertEqual(calls, ["login", "social", "panel", "career"])
        self.assertTrue(result["career_verified"])
        self.assertEqual(result["matches"]["outcome"], "profile_native_deadline")

    def test_rank_service_failure_does_not_erase_identity_or_native_sections(self):
        value = setup()
        value["rank_lookup"] = {"outcome": "rank_response_json", "ranks_verified": False}
        result, calls, _ = self.run_case(accepted=value)
        self.assertTrue(result["career_verified"])
        self.assertTrue(result["matches_verified"])
        self.assertEqual(result["ranks"]["outcome"], "rank_response_json")
        self.assertEqual(calls[-2:], ["career", "matches"])

    def test_independent_reply_provenance_required_despite_claimed_nested_success(self):
        value = setup()
        value["rank_reply_bound"] = False
        native_career, native_matches = career(), matches()
        native_career["reply_context_verified"] = False
        native_matches["reply_context_verified"] = False
        result, _, _ = self.run_case(accepted=value, career_result=native_career, match_result=native_matches)
        self.assertTrue(result["identity_verified"])
        self.assertFalse(result["ranks_verified"])
        self.assertFalse(result["career_verified"])
        self.assertFalse(result["matches_verified"])

    def test_missing_rank_field_is_unknown_not_an_available_empty_history(self):
        value = setup()
        value["rank_lookup"].update(rank_data_present=False, seasons=[])
        result, _, _ = self.run_case(accepted=value)
        self.assertEqual(result["ranks"]["status"], "unknown")
        self.assertFalse(result["ranks_verified"])
        self.assertIsNone(result["current_rank"])

    def test_malformed_optional_projection_does_not_escape_or_invalidate_other_sections(self):
        value = setup()
        value["rank_lookup"]["seasons"][0]["points"] = float("nan")
        native = matches()
        native["matches"][0]["rank_change"]["points_delta"] = "PRIVATE"
        result, _, _ = self.run_case(accepted=value, match_result=native)
        self.assertFalse(result["ranks_verified"])
        self.assertTrue(result["career_verified"])
        self.assertTrue(result["matches_verified"])
        self.assertIsNone(result["matches"]["data"]["matches"][0]["rank_change"])

    def test_all_parent_auth_and_token_gates_required_before_any_sections_escape(self):
        cases = []
        for key in ("auth_service_accepted", "own_role_verified", "game_login_verified"):
            value = auth()
            value[key] = False
            cases.append(value)
        for key in ("token_rpc_received", "social_token_received", "owner_claim_matches", "time_claims_valid",
                    "route_shape_valid", "consumer_completed"):
            value = auth()
            value["social_token"][key] = False
            cases.append(value)
        for value in cases:
            result, _, _ = self.run_case(auth_result=value)
            self.assertFalse(result["identity_verified"])
            self.assertIsNone(result["identity"])
            self.assertFalse(any(result[key] for key in ("career_verified", "ranks_verified", "matches_verified", "current_season_verified")))

    def test_incomplete_game_callback_drops_only_native_sections(self):
        value = auth()
        value["social_token"]["on_game_connection_completed"] = False
        result, _, _ = self.run_case(auth_result=value)
        self.assertTrue(result["identity_verified"])
        self.assertTrue(result["ranks_verified"])
        self.assertFalse(result["career_verified"])
        self.assertFalse(result["matches_verified"])
        self.assertFalse(result["current_season_verified"])

    def test_target_setup_gates_prevent_native_queries(self):
        cases = []
        for key in ("social_session_accepted", "setup_consumer_completed", "mesh_request_bound"):
            value = setup()
            value[key] = False
            cases.append(value)
        for key, invalid in (("uid", str(OWNER)), ("identity_verified", False), ("is_owner", True),
                             ("requested_platform", "xbox")):
            value = setup()
            value["profile_lookup"][key] = invalid
            cases.append(value)
        for value in cases:
            result, calls, _ = self.run_case(accepted=value)
            self.assertEqual(calls, ["login", "social"])
            self.assertFalse(result["identity_verified"])

    def test_empty_target_reply_does_not_erase_verified_social_session_or_infer_invalid_uid(self):
        value = setup()
        value["profile_lookup"] = {"identity_verified": False, "outcome": "uid_lookup_no_data"}
        result, calls, _ = self.run_case(accepted=value)
        self.assertEqual(calls, ["login", "social"])
        self.assertTrue(result["owner_authentication_verified"])
        self.assertTrue(result["game_login_verified"])
        self.assertTrue(result["social_session_accepted"])
        self.assertFalse(result["identity_verified"])
        self.assertIsNone(result["identity"])
        self.assertEqual(result["token_outcome"], "social_token_claims_validated")
        self.assertEqual(result["social_setup_outcome"], "social_setup_verified")
        self.assertEqual(result["identity_outcome"], "uid_lookup_no_data")
        self.assertNotIn("uid_invalid", json.dumps(result))

    def test_mesh_service_rejection_preserves_only_correlated_fixed_numeric_diagnosis(self):
        value = setup()
        value.update(outcome="social_setup_consumer_failed", setup_consumer_completed=False,
            mesh_request_bound=False, mesh_error="social_mesh_rejected", mesh_server_error_code=1006,
            mesh_query_sent=True, mesh_status_layer="body", private="PRIVATE_FREE_SERVER_TEXT")
        value["profile_lookup"] = {"identity_verified": False, "outcome": "uid_lookup_not_completed"}
        result, calls, _ = self.run_case(accepted=value)
        self.assertEqual(calls, ["login", "social"])
        self.assertTrue(result["social_session_accepted"])
        self.assertEqual(result["identity_outcome"], "social_mesh_rejected")
        self.assertEqual(result["social_setup_outcome"], "social_setup_consumer_failed")
        self.assertEqual(result["mesh_error_code"], 1006)
        for changes in ({"mesh_server_error_code": True}, {"mesh_server_error_code": "PRIVATE"},
                        {"mesh_server_error_code": 10**100}, {"mesh_server_error_code": 0},
                        {"mesh_query_sent": False}, {"mesh_status_layer": "PRIVATE"},
                        {"mesh_error": "PRIVATE"}, {"social_session_accepted": False}):
            result, _, _ = self.run_case(accepted=value | changes)
            self.assertNotIn("mesh_error_code", result)

    def test_unknown_stage_outcomes_and_success_claim_without_identity_provenance_are_not_exported(self):
        value, raw_auth = setup(), auth()
        value.update(outcome="PRIVATE_SETUP_OUTCOME", mesh_error="PRIVATE_MESH_OUTCOME")
        value["profile_lookup"] = {"identity_verified": False, "outcome": "PRIVATE_IDENTITY_OUTCOME"}
        raw_auth["social_token"]["outcome"] = "PRIVATE_TOKEN_OUTCOME"
        result, _, _ = self.run_case(accepted=value, auth_result=raw_auth)
        self.assertTrue(result["social_session_accepted"])
        self.assertEqual(result["identity_outcome"], "uid_lookup_not_completed")
        self.assertEqual(result["social_setup_outcome"], "profile_social_setup_outcome_unavailable")
        self.assertEqual(result["token_outcome"], "profile_token_outcome_unavailable")
        value["profile_lookup"]["outcome"] = "uid_profile_identity_verified"
        result, _, _ = self.run_case(accepted=value)
        self.assertEqual(result["identity_outcome"], "uid_lookup_not_completed")

    def test_setup_failure_and_token_failure_remain_different_stages(self):
        value = setup()
        value.update(outcome="social_hello_rejected", social_session_accepted=False,
                     profile_lookup={"identity_verified": False, "outcome": "uid_lookup_not_completed"})
        result, _, _ = self.run_case(accepted=value)
        self.assertEqual(result["token_outcome"], "social_token_claims_validated")
        self.assertEqual(result["social_setup_outcome"], "social_hello_rejected")
        self.assertFalse(result["social_session_accepted"])
        raw_auth = auth()
        raw_auth["social_token"].update(outcome="social_token_claims_rejected", owner_claim_matches=False)
        result, _, _ = self.run_case(auth_result=raw_auth)
        self.assertEqual(result["token_outcome"], "social_token_claims_rejected")
        self.assertEqual(result["social_setup_outcome"], "social_setup_not_started")
        self.assertFalse(result["social_session_accepted"])

    def test_consumer_failure_can_preserve_setup_acceptance_without_releasing_identity(self):
        raw_auth = auth()
        raw_auth["social_token"].update(outcome="social_token_consumer_failed", consumer_completed=False)
        result, _, _ = self.run_case(auth_result=raw_auth)
        self.assertEqual(result["token_outcome"], "social_token_consumer_failed")
        self.assertTrue(result["social_session_accepted"])
        self.assertFalse(result["identity_verified"])
        self.assertIsNone(result["identity"])

    def test_gateway_zone_must_match_fresh_token_owner_zone(self):
        for zone in ({**ZONE, "zone_id": 42}, {**ZONE, "role_uid": TARGET}, {**ZONE, "zone_id": True}):
            result, calls, _ = self.run_case(zone=zone)
            self.assertEqual(calls, ["login", "social"])
            self.assertTrue(result["ranks_verified"])
            self.assertFalse(result["current_season_verified"])
            self.assertFalse(result["career_verified"])

    def test_one_shot_callbacks_and_no_callback_data_without_invocation(self):
        result, calls, mocks = self.run_case(twice_token=True, twice_game=True)
        self.assertEqual(calls, ["login", "social", "panel", "career", "matches"])
        self.assertTrue(result["matches_verified"])
        for mock in mocks:
            self.assertEqual(mock.call_count, 1)
        result, calls, _ = self.run_case(invoke_game=False)
        self.assertEqual(calls, ["login", "social"])
        self.assertTrue(result["ranks_verified"])
        self.assertFalse(result["current_season_verified"])

    def test_invalid_inputs_are_rejected_before_auth_or_callbacks(self):
        args = dict(parameter=PARAMETER, career_methods=self.public.career_methods, target_uid=TARGET)
        cases = [{"target_uid": value} for value in (True, "123", 0, -1, 2**63)]
        cases += [{"client_version": value} for value in ("3825464", 3848703)]
        cases += [{"platform": "xbox"}, {"parameter": b"wrong"}, {"career_methods": object()},
                  {"career_methods": bundle.CareerMethods(True, 1)}]
        with patch.object(bundle, "run_social_token_once") as run:
            for change in cases:
                with self.assertRaisesRegex(bundle.ProfileBundleError, "profile_bundle_inputs_invalid"):
                    bundle.run_profile_bundle(sauth(), self.public.configuration, self.server, **(args | change))
            for login_input, config, server in (({}, self.public.configuration, self.server),
                    (sauth(), None, self.server), (sauth(), self.public.configuration, None),
                    (sauth(), self.public.configuration, bundle.login.LoginServer("127.0.0.1", 1, (10001,)))):
                with self.assertRaises(bundle.ProfileBundleError):
                    bundle.run_profile_bundle(login_input, config, server, **args)
        run.assert_not_called()

    def test_projected_source_mutation_is_not_shared_with_returned_bundle(self):
        accepted, native = setup(), matches()
        result, _, _ = self.run_case(accepted=accepted, match_result=native)
        original = copy.deepcopy(result)
        accepted["rank_lookup"]["seasons"][0]["record"]["wins"] = 0
        accepted["profile_lookup"]["visibility"]["history"]["state"] = "PRIVATE"
        native["matches"][0]["rank_change"]["points_delta"] = 999
        self.assertEqual(result, original)

    def test_no_credential_lookup_file_io_or_independent_socket_creation(self):
        with patch("builtins.open") as opened, patch("pathlib.Path.open") as path_open, \
                patch("socket.create_connection") as connected:
            result, _, _ = self.run_case()
        self.assertTrue(result["matches_verified"])
        opened.assert_not_called()
        path_open.assert_not_called()
        connected.assert_not_called()


if __name__ == "__main__":
    unittest.main()
