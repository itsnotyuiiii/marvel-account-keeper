"""Synthetic helper dispatch to the separately tested redacted profile bundle."""
import copy
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import web_sdk_history_login as history
from web_sdk_history_chain_test import sdk_candidate
from web_sdk_history_handoff_test import bundle, handler


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "probe-web-sdk-login.py"
spec = importlib.util.spec_from_file_location("web_sdk_bundle_handoff", SCRIPT)
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)
OWNER, OTHER = 1370598422, 10826896958
OPTIONS = {"runtime_version": "3848703", "with_reviewed_pc_envelope": True}
CLI = ["--profile-bundle", "--runtime-version", "3848703", "--with-reviewed-pc-envelope"]


def profile_report(uid=OTHER):
    # The library owns detailed source-field projection. This helper receives
    # its fixed redacted report; it must not reintroduce SAUTH/configuration.
    return {"outcome": "profile_bundle_complete", "uid": str(uid), "platform": "pc",
            "owner_authentication_verified": True, "game_login_verified": True, "identity_verified": True,
            "career_verified": True, "ranks_verified": True, "matches_verified": True,
            "current_season_verified": True, "current_season": 37, "current_rank": None,
            "identity": {"uid": str(uid), "platform": "pc", "name": "Synthetic Player"},
            "career": {"status": "available", "outcome": "career_available", "data": {}},
            "ranks": {"status": "available", "outcome": "ranks_available", "data": {}},
            "matches": {"status": "available", "outcome": "matches_available", "data": {}},
            "native_connection_reusable": True, "secrets_saved": False, "remote_code_executed": False}


class BundleHelperTest(unittest.TestCase):
    def setup_calls(self, stack, combined):
        provenance, pin = handoff.runtime.reviewed_runtime_pin("3848703")
        public = SimpleNamespace(configuration=object(), career_methods=object(),
            login_list_url=handoff.runtime.LOGIN_LIST_URL, provenance=provenance)
        social = SimpleNamespace(parameter=object())
        server = object()
        module = ModuleType("standalone_profile_bundle")
        module.run_profile_bundle = Mock(return_value=combined)
        stack.enter_context(patch.dict("sys.modules", {"standalone_profile_bundle": module}))
        loaded = stack.enter_context(patch.object(handoff.runtime, "load_runtime_manifest", return_value=public))
        social_loaded = stack.enter_context(patch("portable_social_runtime.load_social_runtime", return_value=social))
        projected = stack.enter_context(patch.object(handoff.runtime, "project_login_server", return_value=server))
        factory = MagicMock()
        session = factory.return_value.__enter__.return_value
        reply = session.get.return_value.__enter__.return_value
        reply.status_code, reply.url = 200, handoff.runtime.LOGIN_LIST_URL
        reply.raw.read.return_value = b"fixture-server-list"
        stack.enter_context(patch("requests.Session", factory))
        separate = [stack.enter_context(patch(target)) for target in (
            "game_social_token_probe.run_social_token_once", "standalone_profile_probe.run_uid_lookup_once")]
        ordinary = stack.enter_context(patch.object(handoff.probe, "run_auth_once", return_value={"outcome": "fixture-auth-only"}))
        gateway = stack.enter_context(patch.object(handoff.probe, "run_gateway_once"))
        return SimpleNamespace(public=public, social=social, server=server, pin=pin,
            run=module.run_profile_bundle, loaded=loaded, social_loaded=social_loaded,
            projected=projected, factory=factory, session=session, reply=reply,
            separate=separate, ordinary=ordinary, gateway=gateway)

    def test_owner_and_other_uid_dispatch_once_with_exact_packaged_arguments(self):
        for target in (OWNER, OTHER):
            sauth = bundle()["sauth"]
            before = copy.deepcopy(sauth)
            combined = profile_report(target)
            with ExitStack() as stack:
                calls = self.setup_calls(stack, combined)
                result = handoff.attempt_auth(sauth, **OPTIONS, profile_bundle=True, lookup_uid=target)
            calls.run.assert_called_once_with(sauth, calls.public.configuration, calls.server,
                parameter=calls.social.parameter, career_methods=calls.public.career_methods,
                target_uid=target, client_version="3848703")
            calls.loaded.assert_called_once_with(SCRIPT.parents[1] / "runtime-inputs" / "3848703.json",
                expected_sha256=calls.pin, expected_provenance=calls.public.provenance)
            calls.social_loaded.assert_called_once_with(SCRIPT.parents[1] / "runtime-inputs" / "3848703-social.json")
            calls.projected.assert_called_once_with(b"fixture-server-list")
            calls.session.get.assert_called_once_with(handoff.runtime.LOGIN_LIST_URL,
                timeout=10, allow_redirects=False, stream=True)
            calls.reply.raw.read.assert_called_once_with(4097, decode_content=True)
            self.assertFalse(calls.session.trust_env)
            for unused in calls.separate + [calls.ordinary, calls.gateway]:
                unused.assert_not_called()
            self.assertEqual(sauth, before)
            self.assertEqual(result["profile_bundle"], combined)
            for key in ("auth_service_accepted", "own_role_verified", "game_login_verified", "identity_verified",
                        "career_verified", "ranks_verified", "matches_verified", "current_season_verified"):
                self.assertIs(result[key], True)
            self.assertNotIn("current_season", result)
            self.assertEqual(result["profile_bundle"]["current_season"], 37)
            self.assertNotIn("fixture-", json.dumps(result))

    def test_wrong_uid_or_non_dict_report_is_not_returned_or_lifted(self):
        values = [None, ["fixture-private"], "fixture-private", dict(profile_report(), uid=str(OWNER)),
                  dict(profile_report(), uid=OTHER), dict(profile_report(), uid=True)]
        for combined in values:
            with ExitStack() as stack:
                self.setup_calls(stack, combined)
                result = handoff.attempt_auth(bundle()["sauth"], **OPTIONS, profile_bundle=True, lookup_uid=OTHER)
            self.assertNotIn("profile_bundle", result)
            for key in ("auth_service_accepted", "own_role_verified", "game_login_verified", "identity_verified",
                        "career_verified", "ranks_verified", "matches_verified", "current_season_verified"):
                self.assertFalse(result.get(key, False))
            self.assertNotIn("fixture-", json.dumps(result))

    def test_all_section_flags_require_exact_boolean_owner_game_and_identity_proof(self):
        for gate in ("owner_authentication_verified", "game_login_verified", "identity_verified"):
            for value in (False, None, 1, "true"):
                combined = dict(profile_report(), **{gate: value})
                with ExitStack() as stack:
                    self.setup_calls(stack, combined)
                    result = handoff.attempt_auth(bundle()["sauth"], **OPTIONS, profile_bundle=True, lookup_uid=OTHER)
                self.assertEqual(result["profile_bundle"], combined)
                for key in ("identity_verified", "career_verified", "ranks_verified", "matches_verified", "current_season_verified"):
                    self.assertIs(result[key], False)
                if gate == "owner_authentication_verified":
                    self.assertIs(result["auth_service_accepted"], False)
                    self.assertIs(result["own_role_verified"], False)
                    self.assertIs(result["game_login_verified"], False)

    def test_independent_sections_are_preserved_without_turning_unknown_or_restricted_into_stats(self):
        combined = profile_report()
        combined.update(career_verified=False, matches_verified=False, current_season_verified=False,
                        current_season=None, current_rank=None,
                        career={"status": "restricted", "outcome": "career_restricted", "data": None},
                        matches={"status": "unknown", "outcome": "matches_unknown", "data": None})
        with ExitStack() as stack:
            self.setup_calls(stack, combined)
            result = handoff.attempt_auth(bundle()["sauth"], **OPTIONS, profile_bundle=True, lookup_uid=OTHER)
        self.assertTrue(result["ranks_verified"])
        self.assertFalse(result["career_verified"])
        self.assertFalse(result["matches_verified"])
        self.assertFalse(result["current_season_verified"])
        self.assertEqual(result["profile_bundle"]["career"], combined["career"])
        self.assertIsNone(result["profile_bundle"]["matches"]["data"])
        for key in ("career_verified", "ranks_verified", "matches_verified", "current_season_verified"):
            for value in (1, "true", None):
                untyped = dict(profile_report(), **{key: value})
                with ExitStack() as stack:
                    self.setup_calls(stack, untyped)
                    result = handoff.attempt_auth(bundle()["sauth"], **OPTIONS, profile_bundle=True, lookup_uid=OTHER)
                self.assertIs(result[key], False)

    def test_invalid_modes_uid_or_old_runtime_fail_before_runtime_or_network(self):
        base = dict(OPTIONS, profile_bundle=True, lookup_uid=OTHER)
        cases = [dict(base, profile_bundle=value) for value in (None, 1, "true")]
        cases += [dict(base, lookup_uid=value) for value in (None, True, 0, -1, "123", 1 << 63)]
        cases += [dict(base, **{key: True}) for key in ("own_career", "other_career", "other_matches", "other_ranks", "rank_panel")]
        cases += [dict(base, runtime_version="3825464"), dict(base, with_reviewed_pc_envelope=False)]
        with patch.object(handoff.runtime, "load_runtime_manifest") as loaded, \
                patch("portable_social_runtime.load_social_runtime") as social, patch("requests.Session") as session:
            for options in cases:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    handoff.attempt_auth(bundle()["sauth"], **options)
        for unused in (loaded, social, session):
            unused.assert_not_called()

    def test_default_auth_path_does_not_load_social_or_call_bundle(self):
        with ExitStack() as stack:
            calls = self.setup_calls(stack, profile_report())
            result = handoff.attempt_auth(bundle()["sauth"], **OPTIONS)
        calls.ordinary.assert_called_once_with(bundle()["sauth"], calls.public.configuration, calls.server,
                                               client_version="3848703", os_name="pc")
        calls.run.assert_not_called()
        calls.social_loaded.assert_not_called()
        self.assertEqual(result, {"outcome": "fixture-auth-only"})

    def test_history_login_passes_only_renewed_sauth_into_one_profile_bundle(self):
        for target in (OWNER, OTHER):
            value = bundle()
            seen = []

            def exchange(inputs):
                seen.append(inputs)
                return sdk_candidate(inputs, 1)

            with ExitStack() as stack:
                calls = self.setup_calls(stack, profile_report(target))
                sdk = stack.enter_context(patch.object(history, "login_history_once", side_effect=exchange))
                result = handoff.attempt_history_auth(value, **OPTIONS, profile_bundle=True, lookup_uid=target)
            sdk.assert_called_once()
            calls.run.assert_called_once()
            sent = calls.run.call_args.args[0]
            self.assertEqual(sent["sessionid"], "fixture-new-access-1")
            self.assertNotEqual(sent["sessionid"], value["sauth"]["sessionid"])
            self.assertEqual(calls.run.call_args.kwargs["target_uid"], target)
            self.assertTrue(result["sdk_history_login"]["owner_authentication_verified"])
            self.assertFalse(result["sdk_history_login"]["automatic_renewal_verified"])
            self.assertNotIn("fixture-", json.dumps(result))

    def test_chain_verification_rejects_profile_bundle_before_sdk_io(self):
        with patch.object(history, "login_history_once") as sdk, \
                patch.object(handoff.runtime, "load_runtime_manifest") as loaded, \
                patch("requests.Session") as session, self.assertRaises(ValueError):
            handoff.attempt_history_chain(bundle(), **OPTIONS, profile_bundle=True, lookup_uid=OTHER)
        for unused in (sdk, loaded, session):
            unused.assert_not_called()


class BundleCLITest(unittest.TestCase):
    def test_owner_other_and_history_preview_are_offline(self):
        with patch.object(handoff, "HTTPServer") as listener, \
                patch.object(handoff.runtime, "load_runtime_manifest") as loaded, \
                patch.object(history, "login_history_once") as sdk, patch("requests.Session") as session:
            for target in (OWNER, OTHER):
                for history_mode in (False, True):
                    output = io.StringIO()
                    args = CLI + ["--lookup-uid", str(target)]
                    if history_mode:
                        args += ["--history-login-before-auth"]
                    with redirect_stdout(output):
                        self.assertEqual(handoff.main(args), 0)
                    report = json.loads(output.getvalue())
                    self.assertTrue(report["profile_bundle"])
                    self.assertEqual(report["lookup_uid"], target)
                    self.assertEqual(report["history_login_before_auth"], history_mode)
                    for field in ("network_attempted", "credentials_read", "files_read"):
                        self.assertFalse(report[field])
        for unused in (listener, loaded, sdk, session):
            unused.assert_not_called()

    def test_missing_uid_individual_flags_chain_or_old_runtime_are_rejected(self):
        base = CLI + ["--lookup-uid", str(OTHER)]
        cases = [CLI, ["--profile-bundle", "--lookup-uid", str(OTHER)],
                 ["--profile-bundle", "--lookup-uid", str(OTHER), "--runtime-version", "3848703"],
                 ["--profile-bundle", "--lookup-uid", str(OTHER), "--with-reviewed-pc-envelope"]]
        cases += [base + [flag] for flag in ("--own-career", "--other-career", "--other-matches",
                                             "--other-ranks", "--rank-panel", "--verify-history-renewal")]
        with patch.object(handoff, "HTTPServer") as listener, \
                patch.object(handoff.runtime, "load_runtime_manifest") as loaded:
            for args in cases:
                with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    handoff.main(args)
        listener.assert_not_called()
        loaded.assert_not_called()

    def test_synthetic_oneuse_ready_fields_and_runner_selection(self):
        for history_mode in (False, True):
            server = SimpleNamespace(server_port=12345)
            factory = MagicMock()
            factory.return_value.__enter__.return_value = server
            selected = []

            def attempt(value, **options):
                selected.append((copy.deepcopy(value), options))
                return {"outcome": "profile_bundle_returned", "secrets_saved": False}

            def deliver():
                payload = bundle() if history_mode else {key: bundle()[key] for key in ("selected_uid", "sauth")}
                base, _ = handler(payload, allow_history=history_mode)
                cls = factory.call_args.args[1]
                value = cls.__new__(cls)
                value.__dict__.update(base.__dict__)
                value.server = server
                value.do_POST()

            server.handle_request = Mock(side_effect=deliver)
            output = io.StringIO()
            args = CLI + ["--lookup-uid", str(OTHER), "--serve-once"]
            if history_mode:
                args += ["--history-login-before-auth"]
            with patch.object(handoff, "HTTPServer", factory), \
                    patch.object(handoff.secrets, "token_urlsafe", return_value="fixture-capability"), \
                    patch.object(handoff, "attempt_auth", side_effect=attempt) as normal, \
                    patch.object(handoff, "attempt_history_auth", side_effect=attempt) as renewed, \
                    patch.object(handoff, "attempt_history_chain") as chain, redirect_stdout(output):
                self.assertEqual(handoff.main(args), 0)
            (renewed if history_mode else normal).assert_called_once()
            (normal if history_mode else renewed).assert_not_called()
            chain.assert_not_called()
            self.assertEqual(selected[0][0], bundle() if history_mode else bundle()["sauth"])
            self.assertIs(selected[0][1]["profile_bundle"], True)
            self.assertEqual(selected[0][1]["lookup_uid"], OTHER)
            self.assertTrue(server.completed)
            server.handle_request.assert_called_once()
            reports = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertTrue(reports[0]["profile_bundle"])
            self.assertEqual(reports[0]["lookup_uid"], OTHER)
            self.assertNotIn("fixture-saved-token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
