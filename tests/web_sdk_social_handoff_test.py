"""Fresh SocialMatrix handoff wiring, with all file/network work substituted."""
import base64
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import portable_social_runtime as social_runtime


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "probe-web-sdk-login.py"
spec = importlib.util.spec_from_file_location("web_sdk_social_handoff_script", SCRIPT)
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)
PARAMETER = base64.b64decode("bmV0ZWFzZW5hc2RhcTIwMjA=")


def sauth():
    return {"gameid": "x20naxx2gb", "platform": "pc", "login_channel": "netease_global",
            "app_channel": "netease.allysdk_global", "sdk_version": "1.0.0",
            "sdkuid": "fixture-user", "sessionid": "fixture-sdk-secret",
            "deviceid": "fixture-device", "udid": "fixture-udid",
            "client_login_sn": "fixture-login-sn"}


def verified_report():
    return {"outcome": "own_gateway_login_verified", "game_login_verified": True,
            "social_token": {"owner_claim_matches": True, "consumer_completed": True}}


class WebSDKSocialHandoffTest(unittest.TestCase):
    def test_verify_preview_implies_collection_and_gateway_but_remains_offline(self):
        with patch.object(handoff, "HTTPServer") as listener, \
                patch.object(handoff.runtime, "load_runtime_manifest") as game, \
                patch.object(social_runtime, "load_social_runtime") as social, \
                patch("requests.Session") as network, \
                patch("standalone_social_transport.run_social_setup_once") as setup:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(handoff.main(["--verify-social-session", "--runtime-version", "3848703",
                                               "--with-reviewed-pc-envelope"]), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["runtime_version"], "3848703")
        for name in ("verify_social_session", "collect_social_token", "enter_gateway"):
            self.assertIs(report[name], True)
        for name in ("files_read", "network_attempted", "credentials_read"):
            self.assertIs(report[name], False)
        for mocked in (listener, game, social, network, setup):
            mocked.assert_not_called()

    def test_old_or_unreviewed_cli_selection_fails_before_listener_or_files(self):
        with patch.object(handoff, "HTTPServer") as listener, \
                patch.object(handoff.runtime, "load_runtime_manifest") as game, \
                patch.object(social_runtime, "load_social_runtime") as social, \
                patch("requests.Session") as network:
            for args in (["--verify-social-session"],
                         ["--verify-social-session", "--with-reviewed-pc-envelope"],
                         ["--verify-social-session", "--runtime-version", "3848703"],
                         ["--verify-social-session", "--runtime-version", "3825464", "--with-reviewed-pc-envelope"]):
                with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    handoff.main(args)
        for mocked in (listener, game, social, network):
            mocked.assert_not_called()

    def test_invalid_api_mode_rejects_before_artifact_or_network_access(self):
        with patch.object(handoff.runtime, "load_runtime_manifest") as game, \
                patch.object(social_runtime, "load_social_runtime") as social, \
                patch("requests.Session") as network:
            for version, pc, enabled in (("3825464", True, True), ("3848703", False, True),
                                         ("3848703", True, 1), ("3848703", True, "true"),
                                         ("3848703", True, None)):
                with self.subTest(version=version, pc=pc, enabled=enabled), self.assertRaises(ValueError):
                    handoff.attempt_auth(sauth(), runtime_version=version,
                                         with_reviewed_pc_envelope=pc, verify_social_session=enabled)
        for mocked in (game, social, network):
            mocked.assert_not_called()

    def attempt(self, *, returned=None, invoke=True, repeat=False, setup_failure=None,
                verify=True, collect=False, social_failure=None):
        provenance, pin = handoff.runtime.reviewed_runtime_pin("3848703")
        public = SimpleNamespace(configuration=object(), login_list_url=handoff.runtime.LOGIN_LIST_URL,
                                 provenance=provenance)
        artifact = social_runtime.SocialRuntime("3848703", social_runtime.TOKEN_RPC_INDEX, PARAMETER)
        server, fresh, events = object(), object(), []
        parent = verified_report() if returned is None else returned
        setup_result = {"outcome": "social_setup_verified", "social_session_accepted": True,
                        "stats_requested": False, "secrets_saved": False}
        network = MagicMock()
        http = network.return_value.__enter__.return_value
        response = http.get.return_value.__enter__.return_value
        response.status_code, response.url = 200, handoff.runtime.LOGIN_LIST_URL
        response.raw.read.return_value = b"synthetic-server-list"
        network.side_effect = lambda: (events.append("http"), network.return_value)[1]

        def load_game(*args, **kwargs):
            events.append("game")
            return public

        def load_social(*args, **kwargs):
            events.append("social")
            if social_failure is not None:
                raise social_failure
            return artifact

        def run_setup(*args, **kwargs):
            events.append("setup")
            if setup_failure is not None:
                raise setup_failure
            return setup_result

        def run_collector(*args, **kwargs):
            events.append("collector")
            callback = kwargs.get("on_token")
            if invoke and callback is not None:
                if setup_failure is not None:
                    with self.assertRaises(ValueError):
                        callback(fresh)
                else:
                    callback(fresh)
                if repeat:
                    with self.assertRaisesRegex(ValueError, "social_setup_already_consumed"):
                        callback(object())
            return parent

        with patch.object(handoff.runtime, "load_runtime_manifest", side_effect=load_game) as game, \
                patch.object(social_runtime, "load_social_runtime", side_effect=load_social) as social, \
                patch.object(handoff.runtime, "project_login_server", return_value=server) as project, \
                patch("game_social_token_probe.run_social_token_once", side_effect=run_collector) as collector, \
                patch("standalone_social_transport.run_social_setup_once", side_effect=run_setup) as setup, \
                patch.object(handoff.probe, "run_auth_once") as auth, \
                patch.object(handoff.probe, "run_gateway_once") as gateway, \
                patch("requests.Session", network):
            if social_failure is not None:
                with self.assertRaises(type(social_failure)):
                    handoff.attempt_auth(sauth(), runtime_version="3848703", with_reviewed_pc_envelope=True,
                                         verify_social_session=verify, collect_social_token=collect)
                result = None
            else:
                result = handoff.attempt_auth(sauth(), runtime_version="3848703", with_reviewed_pc_envelope=True,
                                              verify_social_session=verify, collect_social_token=collect)
        return SimpleNamespace(result=result, public=public, pin=pin, artifact=artifact, server=server,
            fresh=fresh, setup_result=setup_result, events=events, game=game, social=social, network=network,
            http=http, response=response, collector=collector, setup=setup, project=project,
            auth=auth, gateway=gateway)

    def test_social_artifact_failure_prevents_every_network_and_auth_step(self):
        result = self.attempt(social_failure=social_runtime.SocialRuntimeError("social_runtime_hash_mismatch"))
        self.assertEqual(result.events, ["game", "social"])
        result.social.assert_called_once_with(SCRIPT.parents[1] / "runtime-inputs" / "3848703-social.json")
        for mocked in (result.network, result.collector, result.setup, result.auth, result.gateway, result.project):
            mocked.assert_not_called()

    def test_current_artifacts_load_before_network_and_one_callback_uses_exact_parameter(self):
        result = self.attempt()
        self.assertEqual(result.events, ["game", "social", "http", "collector", "setup"])
        result.game.assert_called_once_with(SCRIPT.parents[1] / "runtime-inputs" / "3848703.json",
            expected_sha256=result.pin, expected_provenance=result.public.provenance)
        result.social.assert_called_once_with(SCRIPT.parents[1] / "runtime-inputs" / "3848703-social.json")
        result.collector.assert_called_once()
        args, kwargs = result.collector.call_args
        self.assertEqual(args, (sauth(), result.public.configuration, result.server))
        self.assertEqual(kwargs["client_version"], "3848703")
        self.assertEqual(set(kwargs), {"client_version", "on_token"})
        self.assertTrue(callable(kwargs["on_token"]))
        result.setup.assert_called_once_with(result.fresh, parameter=PARAMETER)
        self.assertIs(result.result["social_setup"], result.setup_result)
        self.assertFalse(result.http.trust_env)
        result.http.get.assert_called_once_with(handoff.runtime.LOGIN_LIST_URL, timeout=10,
                                               allow_redirects=False, stream=True)
        result.response.raw.read.assert_called_once_with(4097, decode_content=True)
        result.auth.assert_not_called()
        result.gateway.assert_not_called()
        self.assertNotIn("fixture-sdk-secret", json.dumps(result.result))

    def test_callback_is_consumed_before_setup_even_when_setup_raises(self):
        success = self.attempt(repeat=True)
        success.setup.assert_called_once()
        self.assertIn("social_setup", success.result)
        parent = verified_report()
        parent["social_token"]["consumer_completed"] = False
        failed = self.attempt(returned=parent, repeat=True, setup_failure=ValueError("synthetic setup failure"))
        failed.setup.assert_called_once()
        self.assertNotIn("social_setup", failed.result)

    def test_unverified_parent_results_never_attach_the_callback_result(self):
        cases = []
        for value in (False, 1, None):
            item = verified_report()
            item["game_login_verified"] = value
            cases.append(item)
            for field in ("owner_claim_matches", "consumer_completed"):
                item = verified_report()
                item["social_token"][field] = value
                cases.append(item)
        for value in (None, [], "not-a-report", {}):
            item = verified_report()
            item["social_token"] = value
            cases.append(item)
        for item in cases:
            with self.subTest(item=item):
                result = self.attempt(returned=item)
                result.setup.assert_called_once()
                self.assertNotIn("social_setup", result.result)

    def test_no_callback_means_no_setup_result_even_if_parent_claims_completion(self):
        result = self.attempt(invoke=False)
        result.setup.assert_not_called()
        self.assertNotIn("social_setup", result.result)

    def test_collect_only_keeps_existing_dispatch_and_never_loads_social_artifact(self):
        result = self.attempt(verify=False, collect=True)
        self.assertEqual(result.events, ["game", "http", "collector"])
        result.social.assert_not_called()
        result.setup.assert_not_called()
        result.collector.assert_called_once_with(sauth(), result.public.configuration, result.server,
                                                 client_version="3848703")
        result.auth.assert_not_called()
        result.gateway.assert_not_called()
        self.assertNotIn("social_setup", result.result)

    def test_serve_flag_forwarding_and_ready_metadata_need_no_actual_listener(self):
        captured = {}
        fake = MagicMock()
        fake.server_port = 12345

        def handler(path, attempt, **kwargs):
            captured["attempt"] = attempt
            return object()

        def handle_request():
            fake.report = captured["attempt"](sauth())
            fake.completed = True

        fake.handle_request.side_effect = handle_request
        factory = MagicMock()
        factory.return_value.__enter__.return_value = fake
        with patch.object(handoff, "HTTPServer", factory), \
                patch.object(handoff, "make_handler", side_effect=handler), \
                patch.object(handoff, "attempt_auth", return_value={"outcome": "fixture"}) as attempt:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(handoff.main(["--serve-once", "--verify-social-session", "--runtime-version", "3848703",
                                               "--with-reviewed-pc-envelope"]), 0)
        attempt.assert_called_once_with(sauth(), with_reviewed_version=False, with_reviewed_pc_envelope=True,
            runtime_version="3848703", enter_gateway=False, collect_social_token=False,
            verify_social_session=True, refresh_before_auth=False)
        ready, final = map(json.loads, out.getvalue().splitlines())
        self.assertEqual(ready["outcome"], "awaiting_one_owner_session")
        for name in ("verify_social_session", "collect_social_token", "enter_gateway"):
            self.assertIs(ready[name], True)
        self.assertEqual(final, {"outcome": "fixture"})


if __name__ == "__main__":
    unittest.main()
