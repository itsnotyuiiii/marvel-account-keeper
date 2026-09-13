"""Explicit UID handoff wiring; fixtures never open network connections."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from tests.web_sdk_social_handoff_test import handoff, sauth, PARAMETER


class WebSDKUIDHandoffTest(unittest.TestCase):
    def test_preview_selects_uid_lookup_and_remains_offline(self):
        with patch.object(handoff, "HTTPServer") as listener, \
                patch.object(handoff.runtime, "load_runtime_manifest") as files, \
                patch("requests.Session") as network:
            out = io.StringIO()
            with redirect_stdout(out):
                handoff.main(["--lookup-uid", "1370598422", "--runtime-version", "3848703",
                              "--with-reviewed-pc-envelope"])
        value = json.loads(out.getvalue())
        self.assertEqual(value["lookup_uid"], 1370598422)
        self.assertTrue(value["verify_social_session"])
        self.assertTrue(value["enter_gateway"])
        self.assertFalse(value["files_read"])
        self.assertFalse(value["network_attempted"])
        for mock in (listener, files, network):
            mock.assert_not_called()

    def test_invalid_uid_or_unreviewed_mode_stops_before_io(self):
        with patch.object(handoff.runtime, "load_runtime_manifest") as files, \
                patch("requests.Session") as network:
            for value in (True, "456", -1, 0, 1 << 64):
                with self.assertRaises(ValueError):
                    handoff.attempt_auth(sauth(), lookup_uid=value, runtime_version="3848703",
                                         with_reviewed_pc_envelope=True)
            with self.assertRaises(ValueError):
                handoff.attempt_auth(sauth(), lookup_uid=456)
            for argv in (["--lookup-uid", "0"], ["--lookup-uid", "456"]):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    handoff.main(argv)
        files.assert_not_called()
        network.assert_not_called()

    def test_own_career_preview_selects_owner_and_rejects_other_targets(self):
        with patch.object(handoff, "HTTPServer") as listener, \
                patch("requests.Session") as network:
            out = io.StringIO()
            with redirect_stdout(out):
                handoff.main(["--own-career", "--runtime-version", "3848703", "--with-reviewed-pc-envelope"])
            for argv in (["--own-career", "--lookup-uid", "1822797559"], ["--own-career"]):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    handoff.main(argv)
            for target, mode in ((1822797559, True), (None, 1), (None, "true")):
                with self.assertRaises(ValueError):
                    handoff.attempt_auth(sauth(), lookup_uid=target, own_career=mode,
                                         runtime_version="3848703", with_reviewed_pc_envelope=True)
        value = json.loads(out.getvalue())
        self.assertEqual(value["lookup_uid"], 1370598422)
        self.assertTrue(value["own_career"])
        self.assertFalse(value["network_attempted"])
        listener.assert_not_called()
        network.assert_not_called()

    def test_target_is_explicit_and_setup_only_path_is_not_repeated(self):
        provenance, _ = handoff.runtime.reviewed_runtime_pin("3848703")
        public = SimpleNamespace(configuration=object(), login_list_url=handoff.runtime.LOGIN_LIST_URL,
                                 provenance=provenance)
        artifact = SimpleNamespace(parameter=PARAMETER)
        factory = MagicMock()
        response = factory.return_value.__enter__.return_value.get.return_value.__enter__.return_value
        response.status_code, response.url = 200, public.login_list_url
        response.raw.read.return_value = b"fixture-list"
        fresh = object()
        expected = {"social_session_accepted": True, "profile_lookup": {"identity_verified": True}}
        def collect(*args, **kwargs):
            kwargs["on_token"](fresh)
            return {"game_login_verified": True,
                    "social_token": {"owner_claim_matches": True, "consumer_completed": True}}
        with patch.object(handoff.runtime, "load_runtime_manifest", return_value=public), \
                patch("portable_social_runtime.load_social_runtime", return_value=artifact), \
                patch.object(handoff.runtime, "project_login_server", return_value=object()), \
                patch("requests.Session", factory), \
                patch("game_social_token_probe.run_social_token_once", side_effect=collect) as collector, \
                patch("standalone_profile_probe.run_uid_lookup_once", return_value=expected) as lookup, \
                patch("standalone_social_transport.run_social_setup_once") as setup, \
                patch.object(handoff.probe, "run_gateway_once") as gateway, \
                patch.object(handoff.probe, "run_auth_once") as auth:
            result = handoff.attempt_auth(sauth(), lookup_uid=456, runtime_version="3848703",
                                          with_reviewed_pc_envelope=True)
        lookup.assert_called_once_with(fresh, parameter=PARAMETER, target_uid=456)
        self.assertEqual(collector.call_count, 1)
        self.assertIs(result["social_setup"], expected)
        for mock in (setup, gateway, auth):
            mock.assert_not_called()

    def test_other_career_requires_explicit_other_uid_before_io(self):
        with patch.object(handoff.runtime, "load_runtime_manifest") as files, \
                patch("requests.Session") as network:
            for target, own, other in ((None, False, True), (1370598422, False, True),
                                        (123, True, True), (123, False, 1), (1 << 63, False, True)):
                with self.assertRaises(ValueError):
                    handoff.attempt_auth(sauth(), lookup_uid=target, own_career=own, other_career=other,
                                         runtime_version="3848703", with_reviewed_pc_envelope=True)
            for argv in (["--other-career"], ["--other-career", "--lookup-uid", "1370598422"],
                         ["--other-career", "--own-career", "--lookup-uid", "123"]):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    handoff.main(argv)
            out = io.StringIO()
            with redirect_stdout(out):
                handoff.main(["--other-career", "--lookup-uid", "123", "--runtime-version", "3848703",
                              "--with-reviewed-pc-envelope"])
            result = json.loads(out.getvalue())
            self.assertTrue(result["other_career"])
            self.assertFalse(result["network_attempted"])
        files.assert_not_called()
        network.assert_not_called()

    def test_other_matches_is_explicit_offline_and_not_combined(self):
        with patch.object(handoff.runtime, "load_runtime_manifest") as files, \
                patch("requests.Session") as network:
            for target, own, other in ((None, False, False), (1370598422, False, False),
                                        (123, True, False), (123, False, True)):
                with self.assertRaises(ValueError):
                    handoff.attempt_auth(sauth(), lookup_uid=target, own_career=own, other_career=other,
                                         other_matches=True, runtime_version="3848703", with_reviewed_pc_envelope=True)
            for argv in (["--other-matches"], ["--other-matches", "--lookup-uid", "1370598422"],
                         ["--other-matches", "--other-career", "--lookup-uid", "123"]):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    handoff.main(argv)
            out = io.StringIO()
            with redirect_stdout(out):
                handoff.main(["--other-matches", "--lookup-uid", "123", "--runtime-version", "3848703",
                              "--with-reviewed-pc-envelope"])
            result = json.loads(out.getvalue())
            self.assertTrue(result["other_matches"])
            self.assertFalse(result["other_career"])
            self.assertFalse(result["network_attempted"])
        files.assert_not_called()
        network.assert_not_called()

    def test_other_career_uses_one_gateway_and_retains_parent_provenance(self):
        provenance, _ = handoff.runtime.reviewed_runtime_pin("3848703")
        public = SimpleNamespace(configuration=object(), login_list_url=handoff.runtime.LOGIN_LIST_URL,
                                 provenance=provenance, career_methods=object())
        factory = MagicMock()
        response = factory.return_value.__enter__.return_value.get.return_value.__enter__.return_value
        response.status_code, response.url = 200, public.login_list_url
        response.raw.read.return_value = b"fixture-list"
        accepted, conn, transport, zone = {"profile_lookup": {"uid": "123"}}, object(), object(), object()
        projected = {"career_verified": True, "request_bound": True, "identity_verified": True, "uid": "123"}
        for parent_ok in (True, False):
            def collect(*args, **kwargs):
                kwargs["on_token"](object())
                kwargs["on_game_connection"](conn, transport, zone)
                return {"game_login_verified": parent_ok, "career_verified": False,
                        "social_token": {"owner_claim_matches": True, "consumer_completed": True,
                                         "on_game_connection_completed": True}}
            with patch.object(handoff.runtime, "load_runtime_manifest", return_value=public), \
                    patch("portable_social_runtime.load_social_runtime", return_value=SimpleNamespace(parameter=PARAMETER)), \
                    patch.object(handoff.runtime, "project_login_server", return_value=object()), \
                    patch("requests.Session", factory), \
                    patch("game_social_token_probe.run_social_token_once", side_effect=collect) as collector, \
                    patch("standalone_profile_probe.run_uid_lookup_once", return_value=accepted), \
                    patch("standalone_native_sections.read_other_career_once", return_value=projected) as native, \
                    patch.object(handoff.probe, "run_gateway_once") as gateway:
                result = handoff.attempt_auth(sauth(), lookup_uid=123, other_career=True,
                                              runtime_version="3848703", with_reviewed_pc_envelope=True)
            native.assert_called_once_with(conn, transport, zone, public.career_methods,
                                           setup_report=accepted, target_uid=123)
            self.assertEqual(collector.call_count, 1)
            gateway.assert_not_called()
            self.assertEqual(result["career_verified"], parent_ok)
            self.assertEqual("other_career" in result, parent_ok)

    def test_match_dispatch_does_not_run_career_or_invent_career_success(self):
        provenance, _ = handoff.runtime.reviewed_runtime_pin("3848703")
        public = SimpleNamespace(configuration=object(), login_list_url=handoff.runtime.LOGIN_LIST_URL,
                                 provenance=provenance, career_methods=object())
        factory = MagicMock()
        response = factory.return_value.__enter__.return_value.get.return_value.__enter__.return_value
        response.status_code, response.url = 200, public.login_list_url
        response.raw.read.return_value = b"fixture-list"
        accepted, conn, transport, zone = {"profile_lookup": {"uid": "123"}}, object(), object(), object()
        projected = {"matches_verified": True, "request_bound": True, "identity_verified": True, "uid": "123"}
        for completed in (True, False):
            def collect(*args, **kwargs):
                kwargs["on_token"](object())
                kwargs["on_game_connection"](conn, transport, zone)
                return {"game_login_verified": True, "career_verified": False,
                        "social_token": {"owner_claim_matches": True, "consumer_completed": True,
                                         "on_game_connection_completed": completed}}
            with patch.object(handoff.runtime, "load_runtime_manifest", return_value=public), \
                    patch("portable_social_runtime.load_social_runtime", return_value=SimpleNamespace(parameter=PARAMETER)), \
                    patch.object(handoff.runtime, "project_login_server", return_value=object()), \
                    patch("requests.Session", factory), \
                    patch("game_social_token_probe.run_social_token_once", side_effect=collect) as collector, \
                    patch("standalone_profile_probe.run_uid_lookup_once", return_value=accepted), \
                    patch("standalone_native_sections.read_other_matches_once", return_value=projected) as native, \
                    patch("standalone_native_sections.read_other_career_once") as career:
                result = handoff.attempt_auth(sauth(), lookup_uid=123, other_matches=True,
                                              runtime_version="3848703", with_reviewed_pc_envelope=True)
            native.assert_called_once_with(conn, transport, zone, public.career_methods,
                                           setup_report=accepted, target_uid=123)
            self.assertEqual(collector.call_count, 1)
            career.assert_not_called()
            self.assertFalse(result["career_verified"])
            self.assertEqual(result.get("matches_verified", False), completed)
            self.assertEqual("other_matches" in result, completed)


if __name__ == "__main__":
    unittest.main()
