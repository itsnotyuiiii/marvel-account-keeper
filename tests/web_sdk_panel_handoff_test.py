"""Mock-only owner mesh -> same-gateway rank-panel handoff and promotion gates."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from tests.web_sdk_social_handoff_test import handoff, sauth, PARAMETER


OWNER = 1370598422
ZONE = {"role_uid": OWNER, "zone_id": 11001}


def setup():
    return {"social_session_accepted": True, "setup_consumer_completed": True, "mesh_request_bound": True,
            "profile_lookup": {"identity_verified": True, "uid": str(OWNER)}}


def native_report():
    return {"outcome": "owner_rank_panel_verified", "owner_context_valid": True, "uid": str(OWNER),
            "rank_panel_verified": True, "current_season_verified": True, "current_season": 19,
            "own_rank_verified": True, "current": {"season": 19, "level": 14, "points": 2545.5},
            "response_identity_echo_verified": False, "nonce_correlated": False, "secrets_saved": False}


def parent_report():
    return {"game_login_verified": True, "career_verified": False,
            "social_token": {"owner_claim_matches": True, "consumer_completed": True,
                             "on_game_connection_completed": True}}


class WebSDKPanelHandoffTest(unittest.TestCase):
    def attempt(self, *, selected_setup=None, native=None, parent=None, invoke_token=True,
                invoke_gateway=True, native_error=None, rank_panel=True, lookup_uid=None):
        provenance, _ = handoff.runtime.reviewed_runtime_pin("3848703")
        public = SimpleNamespace(configuration=object(), login_list_url=handoff.runtime.LOGIN_LIST_URL,
                                 provenance=provenance)
        session_factory = MagicMock()
        response = session_factory.return_value.__enter__.return_value.get.return_value.__enter__.return_value
        response.status_code, response.url = 200, public.login_list_url
        response.raw.read.return_value = b"fixture-server-list"
        fresh, connection, transport, server = object(), object(), object(), object()
        selected_setup = setup() if selected_setup is None else selected_setup
        native = native_report() if native is None else native
        parent = parent_report() if parent is None else parent
        order = []

        def collect(sdk, configuration, selected_server, **kwargs):
            self.assertEqual(sdk, sauth())
            self.assertIs(configuration, public.configuration)
            self.assertIs(selected_server, server)
            self.assertEqual(kwargs["client_version"], "3848703")
            result = copy.deepcopy(parent)
            if invoke_token:
                order.append("token")
                kwargs["on_token"](fresh)
            if invoke_gateway and "on_game_connection" in kwargs:
                order.append("gateway")
                try:
                    kwargs["on_game_connection"](connection, transport, ZONE)
                except Exception:
                    # The real collector contains callback exceptions and
                    # makes no successful-completion claim for a failed call.
                    result["social_token"]["on_game_connection_completed"] = False
                    result["social_token"]["on_game_connection_error"] = "social_token_game_consumer_failed"
            return result

        with patch.object(handoff.runtime, "load_runtime_manifest", return_value=public), \
                patch("portable_social_runtime.load_social_runtime", return_value=SimpleNamespace(parameter=PARAMETER)), \
                patch.object(handoff.runtime, "project_login_server", return_value=server), \
                patch("requests.Session", session_factory), \
                patch("game_social_token_probe.run_social_token_once", side_effect=collect) as collector, \
                patch("standalone_profile_probe.run_uid_lookup_once", return_value=selected_setup) as lookup, \
                patch("game_rank_panel_probe.read_rank_panel_once", return_value=native,
                      side_effect=native_error) as reader, \
                patch.object(handoff.probe, "run_gateway_once") as gateway, \
                patch.object(handoff.probe, "run_auth_once") as auth, \
                patch("standalone_native_sections.read_other_career_once") as other_career, \
                patch("standalone_native_sections.read_other_matches_once") as other_matches:
            result = handoff.attempt_auth(sauth(), rank_panel=rank_panel, lookup_uid=lookup_uid,
                                          runtime_version="3848703", with_reviewed_pc_envelope=True)
        for method in (gateway, auth, other_career, other_matches):
            method.assert_not_called()
        self.assertNotIn("PRIVATE", json.dumps(result))
        return result, collector, lookup, reader, fresh, connection, transport, order

    def test_explicit_preview_implies_owner_lookup_but_is_offline(self):
        with patch.object(handoff, "HTTPServer") as listener, \
                patch.object(handoff.runtime, "load_runtime_manifest") as game, \
                patch("portable_social_runtime.load_social_runtime") as social, \
                patch("requests.Session") as network, \
                patch("game_rank_panel_probe.read_rank_panel_once") as reader:
            for extra in ([], ["--lookup-uid", str(OWNER)], ["--history-login-before-auth"]):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(handoff.main(["--rank-panel", "--runtime-version", "3848703",
                                                   "--with-reviewed-pc-envelope", *extra]), 0)
                result = json.loads(output.getvalue())
                self.assertTrue(result["rank_panel"])
                self.assertEqual(result["lookup_uid"], OWNER)
                for name in ("enter_gateway", "collect_social_token", "verify_social_session"):
                    self.assertIs(result[name], True)
                for name in ("network_attempted", "files_read", "credentials_read", "own_career",
                             "other_career", "other_matches", "other_ranks"):
                    self.assertIs(result[name], False)
        for method in (listener, game, social, network, reader):
            method.assert_not_called()

    def test_cli_rejects_other_uid_mixed_sections_and_unreviewed_runtime_before_io(self):
        prefix = ["--rank-panel", "--runtime-version", "3848703", "--with-reviewed-pc-envelope"]
        cases = [["--rank-panel"], ["--rank-panel", "--runtime-version", "3848703"],
                 ["--rank-panel", "--runtime-version", "3825464", "--with-reviewed-pc-envelope"],
                 prefix + ["--lookup-uid", "456"], prefix + ["--lookup-uid", "0"]]
        cases += [prefix + [flag] for flag in ("--own-career", "--other-career", "--other-matches", "--other-ranks")]
        with patch.object(handoff, "HTTPServer") as listener, \
                patch.object(handoff.runtime, "load_runtime_manifest") as game, \
                patch("requests.Session") as network:
            for argv in cases:
                with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    handoff.main(argv)
        for method in (listener, game, network):
            method.assert_not_called()

    def test_api_rejects_wrong_uid_mixed_modes_untyped_flags_and_old_or_unreviewed_pc_before_io(self):
        cases = [{"lookup_uid": value} for value in (456, 0, True, str(OWNER), 1 << 64)]
        cases += [{mode: True} for mode in ("own_career", "other_career", "other_matches", "other_ranks")]
        cases += [{"rank_panel": value} for value in (None, 1, "true")]
        cases += [{"runtime_version": "3825464"}, {"with_reviewed_pc_envelope": False}]
        with patch.object(handoff.runtime, "load_runtime_manifest") as game, \
                patch("portable_social_runtime.load_social_runtime") as social, \
                patch("requests.Session") as network:
            for changes in cases:
                kwargs = {"rank_panel": True, "runtime_version": "3848703", "with_reviewed_pc_envelope": True}
                kwargs.update(changes)
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    handoff.attempt_auth(sauth(), **kwargs)
        for method in (game, social, network):
            method.assert_not_called()

    def test_same_gateway_is_used_only_after_fresh_owner_mesh_lookup(self):
        result, collector, lookup, reader, fresh, connection, transport, order = self.attempt()
        self.assertEqual(order, ["token", "gateway"])
        collector.assert_called_once()
        lookup.assert_called_once_with(fresh, parameter=PARAMETER, target_uid=OWNER)
        reader.assert_called_once_with(connection, transport, ZONE, client_version="3848703")
        self.assertTrue(result["current_season_verified"])
        self.assertTrue(result["own_rank_verified"])
        self.assertEqual(result["rank_panel"], native_report())
        self.assertFalse(result["career_verified"])
        self.assertFalse(result.get("ranks_verified", False))
        self.assertNotIn("other_career", result)
        self.assertNotIn("other_matches", result)

    def test_missing_wrong_or_unverified_mesh_owner_prevents_native_reader(self):
        cases = [{}, {"profile_lookup": None}, {"profile_lookup": {"identity_verified": False, "uid": str(OWNER)}},
                 {"profile_lookup": {"identity_verified": 1, "uid": str(OWNER)}},
                 {"profile_lookup": {"identity_verified": True, "uid": "456"}},
                 {"profile_lookup": {"identity_verified": True, "uid": OWNER}}]
        for selected in cases:
            result, _, _, reader, *_ = self.attempt(selected_setup=selected)
            reader.assert_not_called()
            self.assertFalse(result.get("current_season_verified", False))
            self.assertFalse(result.get("own_rank_verified", False))
            self.assertEqual(result["rank_panel"]["outcome"], "rank_panel_owner_identity_unverified")
        result, _, lookup, reader, *_ = self.attempt(invoke_token=False)
        lookup.assert_not_called()
        reader.assert_not_called()
        self.assertFalse(result.get("current_season_verified", False))

    def test_every_parent_provenance_gate_is_required_before_promoting_native_results(self):
        cases = []
        for key in ("game_login_verified",):
            for value in (False, 1, None):
                item = parent_report()
                item[key] = value
                cases.append(item)
        for key in ("owner_claim_matches", "consumer_completed", "on_game_connection_completed"):
            for value in (False, 1, None):
                item = parent_report()
                item["social_token"][key] = value
                cases.append(item)
        for item in cases:
            result, _, _, reader, *_ = self.attempt(parent=item)
            self.assertEqual(reader.call_count, 1)  # Adversarial fixture; promotion still must fail.
            self.assertFalse(result.get("current_season_verified", False))
            self.assertFalse(result.get("own_rank_verified", False))
            self.assertNotIn("rank_panel", result)

    def test_native_owner_and_method_verification_are_required_for_promotion(self):
        cases = [{"owner_context_valid": value} for value in (False, 1, None)]
        cases += [{"rank_panel_verified": value} for value in (False, 1, None)]
        cases += [{"uid": value} for value in ("456", OWNER, None)]
        for changes in cases:
            value = native_report()
            value.update(changes)
            result, _, _, reader, *_ = self.attempt(native=value)
            reader.assert_called_once()
            self.assertEqual(result["rank_panel"], value)
            self.assertFalse(result.get("current_season_verified", False))
            self.assertFalse(result.get("own_rank_verified", False))
            self.assertTrue(result["social_setup"]["profile_lookup"]["identity_verified"])

    def test_current_season_and_own_rank_are_promoted_independently_and_require_true(self):
        for season, rank in ((True, False), (False, True), (None, True), (True, 1), (1, True)):
            value = native_report()
            value.update(current_season_verified=season, own_rank_verified=rank)
            result, _, _, _, *_ = self.attempt(native=value)
            self.assertIs(result["current_season_verified"], season is True)
            self.assertIs(result["own_rank_verified"], rank is True)
        value = native_report()
        value.update(own_rank_verified=False, own_rank_error="rank_panel_score_invalid")
        result, *_ = self.attempt(native=value)
        self.assertTrue(result["current_season_verified"])
        self.assertFalse(result["own_rank_verified"])

    def test_native_exception_preserves_verified_identity_but_no_panel_claim_or_fallback(self):
        result, collector, _, reader, *_ = self.attempt(native_error=RuntimeError("PRIVATE_NATIVE"))
        collector.assert_called_once()
        reader.assert_called_once()
        self.assertTrue(result["social_setup"]["profile_lookup"]["identity_verified"])
        self.assertFalse(result.get("current_season_verified", False))
        self.assertFalse(result.get("own_rank_verified", False))
        self.assertNotIn("rank_panel", result)

    def test_default_owner_lookup_has_no_native_callback_or_rank_panel_request(self):
        result, collector, lookup, reader, fresh, *_ = self.attempt(rank_panel=False, lookup_uid=OWNER)
        lookup.assert_called_once_with(fresh, parameter=PARAMETER, target_uid=OWNER)
        self.assertNotIn("on_game_connection", collector.call_args.kwargs)
        reader.assert_not_called()
        self.assertNotIn("rank_panel", result)
        self.assertFalse(result.get("current_season_verified", False))
        self.assertFalse(result.get("own_rank_verified", False))

    def test_untyped_native_result_cannot_promote_or_erase_owner_identity(self):
        for value in ([], 0, "PRIVATE_NATIVE_RESULT"):
            result, _, _, reader, *_ = self.attempt(native=value)
            reader.assert_called_once()
            self.assertNotIn("rank_panel", result)
            self.assertFalse(result.get("current_season_verified", False))
            self.assertFalse(result.get("own_rank_verified", False))
            self.assertTrue(result["social_setup"]["profile_lookup"]["identity_verified"])

    def test_serve_once_forwards_explicit_owner_panel_option_to_selected_auth_runner(self):
        for history in (False, True):
            pending = {}
            server = MagicMock()
            server.server_port = 12345

            def make_handler(path, *, attempt, **kwargs):
                pending["attempt"] = attempt
                self.assertEqual(kwargs["allow_history"], history)
                return object()

            def handle_request():
                server.report = pending["attempt"](sauth())
                server.completed = True

            server.handle_request.side_effect = handle_request
            with patch.object(handoff, "HTTPServer") as listener, \
                    patch.object(handoff, "make_handler", side_effect=make_handler), \
                    patch.object(handoff, "attempt_auth", return_value={"outcome": "fixture"}) as auth, \
                    patch.object(handoff, "attempt_history_auth", return_value={"outcome": "fixture"}) as history_auth:
                listener.return_value.__enter__.return_value = server
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(handoff.main(["--serve-once", "--rank-panel", "--runtime-version", "3848703",
                        "--with-reviewed-pc-envelope", *(["--history-login-before-auth"] if history else [])]), 0)
            selected, unused = (history_auth, auth) if history else (auth, history_auth)
            selected.assert_called_once_with(sauth(), with_reviewed_version=False,
                with_reviewed_pc_envelope=True, runtime_version="3848703", enter_gateway=False,
                collect_social_token=False, verify_social_session=False, refresh_before_auth=False,
                lookup_uid=OWNER, rank_panel=True)
            unused.assert_not_called()
            server.handle_request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
