"""Pure/mocked tests for exact-target, overview-gated season-rank handoff."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch

import standalone_profile_probe as profile
from tests.standalone_profile_probe_test import user_data
from tests.standalone_social_transport_test import session, PARAMETER
from tests.web_sdk_social_handoff_test import handoff, sauth


class RankProfileCompositionTest(unittest.TestCase):
    def lookup(self, *, data=None, binding=True, accepted=True, rank_failure=False):
        context = Mock()
        context.read_mesh_user.return_value = user_data() if data is None else data
        context.read_other_ranks.side_effect = RuntimeError("PRIVATE") if rank_failure else None
        def setup(fresh, **kwargs):
            kwargs["on_setup"](context)
            return {"social_session_accepted": accepted, "setup_consumer_completed": True,
                    "mesh_request_bound": True, "rank_reply_bound": binding}
        value = {"ranks_verified": True, "uid": "456", "seasons": [], "current_season": None,
                 "response_identity_echo_verified": False}
        with patch.object(profile, "run_social_setup_once", side_effect=setup), \
                patch.object(profile, "project_other_rank_response", return_value=value) as projector:
            report = profile.run_uid_lookup_once(session(), parameter=PARAMETER, target_uid=456, other_ranks=True)
        return report, context, projector

    def test_rank_projection_needs_fresh_mesh_and_exact_query_correlation(self):
        report, context, projector = self.lookup()
        self.assertTrue(report["profile_lookup"]["identity_verified"])
        self.assertTrue(report["rank_lookup"]["ranks_verified"])
        self.assertTrue(report["rank_lookup"]["request_bound"])
        self.assertTrue(report["rank_lookup"]["identity_verified"])
        self.assertFalse(report["rank_lookup"]["response_identity_echo_verified"])
        self.assertEqual(report["rank_lookup"]["identity_basis"], "mesh_identity_and_rank_request")
        context.read_other_ranks.assert_called_once_with(456)
        context.read_own_career.assert_not_called()
        projector.assert_called_once_with(context.read_other_ranks.return_value, 456, 1, platform="pc")
        for kwargs in ({"binding": False}, {"accepted": False}):
            report, _, _ = self.lookup(**kwargs)
            self.assertFalse(report["rank_lookup"]["ranks_verified"])

    def test_overview_permission_is_independent_of_history_permission(self):
        for permission in (1, None, True):
            data = user_data(config={"career_settings": json.dumps({
                "CareerOverviewIsVisibleToOther_1": permission, "BattleHistoryIsVisibleToOther": 0})})
            report, context, projector = self.lookup(data=data, binding=False)
            self.assertTrue(report["profile_lookup"]["identity_verified"])
            self.assertFalse(report["rank_lookup"]["ranks_verified"])
            self.assertEqual(report["rank_lookup"]["outcome"], "other_rank_overview_not_available")
            context.read_other_ranks.assert_not_called()
            projector.assert_not_called()

    def test_wrong_mesh_target_and_rank_failure_preserve_only_verified_identity(self):
        report, context, projector = self.lookup(data=user_data(uid=999), binding=False)
        self.assertFalse(report["profile_lookup"]["identity_verified"])
        self.assertFalse(report["rank_lookup"]["ranks_verified"])
        context.read_other_ranks.assert_not_called()
        projector.assert_not_called()
        report, _, _ = self.lookup(rank_failure=True, binding=False)
        self.assertTrue(report["profile_lookup"]["identity_verified"])
        self.assertFalse(report["rank_lookup"]["ranks_verified"])
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_other_rank_never_substitutes_owner_getter_or_untyped_option(self):
        with patch.object(profile, "run_social_setup_once") as setup:
            for target, own, ranks in ((123, False, True), (456, True, True), (456, False, 1)):
                with self.assertRaises(profile.ProfileLookupError):
                    profile.run_uid_lookup_once(session(), parameter=PARAMETER, target_uid=target,
                                                own_career=own, other_ranks=ranks)
        setup.assert_not_called()


class RankHandoffTest(unittest.TestCase):
    def test_preview_and_invalid_modes_never_use_files_or_network(self):
        with patch.object(handoff.runtime, "load_runtime_manifest") as files, \
                patch("requests.Session") as network, patch.object(handoff, "HTTPServer") as listener:
            output = io.StringIO()
            with redirect_stdout(output):
                handoff.main(["--other-ranks", "--lookup-uid", "456", "--runtime-version", "3848703",
                              "--with-reviewed-pc-envelope"])
            value = json.loads(output.getvalue())
            self.assertTrue(value["other_ranks"])
            self.assertTrue(value["verify_social_session"])
            self.assertFalse(value["network_attempted"])
            for argv in (["--other-ranks"], ["--other-ranks", "--lookup-uid", "1370598422"],
                         ["--other-ranks", "--other-matches", "--lookup-uid", "456"],
                         ["--other-ranks", "--other-career", "--lookup-uid", "456"]):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    handoff.main(argv)
            for kwargs in ({"lookup_uid": None}, {"lookup_uid": 1370598422},
                           {"lookup_uid": 456, "own_career": True},
                           {"lookup_uid": 456, "other_matches": True}):
                with self.assertRaises(ValueError):
                    handoff.attempt_auth(sauth(), other_ranks=True, runtime_version="3848703",
                                         with_reviewed_pc_envelope=True, **kwargs)
        for mock in (files, network, listener):
            mock.assert_not_called()

    def test_explicit_rank_dispatch_and_parent_auth_gating(self):
        provenance, _ = handoff.runtime.reviewed_runtime_pin("3848703")
        public = SimpleNamespace(configuration=object(), login_list_url=handoff.runtime.LOGIN_LIST_URL,
                                 provenance=provenance)
        factory = MagicMock()
        response = factory.return_value.__enter__.return_value.get.return_value.__enter__.return_value
        response.status_code, response.url = 200, public.login_list_url
        response.raw.read.return_value = b"fixture-list"
        fresh = object()
        for parent_ok in (True, False):
            def collect(*args, **kwargs):
                self.assertNotIn("on_game_connection", kwargs)
                kwargs["on_token"](fresh)
                return {"game_login_verified": parent_ok, "career_verified": False,
                        "social_token": {"owner_claim_matches": True, "consumer_completed": True}}
            rank = {"ranks_verified": True, "identity_verified": True, "request_bound": True, "uid": "456"}
            with patch.object(handoff.runtime, "load_runtime_manifest", return_value=public), \
                    patch("portable_social_runtime.load_social_runtime", return_value=SimpleNamespace(parameter=PARAMETER)), \
                    patch.object(handoff.runtime, "project_login_server", return_value=object()), \
                    patch("requests.Session", factory), \
                    patch("game_social_token_probe.run_social_token_once", side_effect=collect) as collector, \
                    patch("standalone_profile_probe.run_uid_lookup_once", return_value={"rank_lookup": rank}) as lookup:
                result = handoff.attempt_auth(sauth(), lookup_uid=456, other_ranks=True,
                                              runtime_version="3848703", with_reviewed_pc_envelope=True)
            self.assertEqual(collector.call_count, 1)
            lookup.assert_called_once_with(fresh, parameter=PARAMETER, target_uid=456, other_ranks=True)
            self.assertEqual(result.get("ranks_verified", False), parent_ok)
            self.assertFalse(result["career_verified"])


if __name__ == "__main__":
    unittest.main()
