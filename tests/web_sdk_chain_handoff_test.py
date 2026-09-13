"""Synthetic two-round helper/CLI integration; never opens a real listener."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import web_sdk_history_chain as chain
import web_sdk_history_login as history
from web_sdk_history_chain_test import sdk_candidate
from web_sdk_history_handoff_test import bundle, handler


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "probe-web-sdk-login.py"
spec = importlib.util.spec_from_file_location("web_sdk_chain_handoff", SCRIPT)
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)
OPTIONS = {"runtime_version": "3848703", "with_reviewed_pc_envelope": True}
CLI = ["--verify-history-renewal", "--runtime-version", "3848703", "--with-reviewed-pc-envelope"]


def auth_report(*, enter=False, accepted=True):
    result = handoff.probe._report("own_existing_role_verified" if accepted else "server_rejected_authentication")
    result.update(prepare_verified=True, auth_service_accepted=accepted, own_role_verified=accepted,
                  credentials_sent=True, connections_attempted=1, application_bytes_sent=625,
                  handshake_bytes_sent=286, bytes_received=2398,
                  raw_session="fixture-never-export", arbitrary={"refresh_token": "fixture-never-export"})
    if not accepted:
        result["server_error_code"] = 1911
    if enter and accepted:
        result.update(outcome="own_gateway_login_verified", game_login_verified=True, gateway={
            "outcome": "own_gateway_login_verified", "game_login_verified": True,
            "connections_attempted": 1, "application_bytes_sent": 71, "handshake_bytes_sent": 286,
            "bytes_received": 39295, "credentials_sent": True, "login_result_uid_present": True,
            "login_result_uid_matches": True, "login_key_present": True,
            "raw_login_key": "fixture-never-export"})
    return result


class ChainHelperTest(unittest.TestCase):
    def execute(self, *, enter=False, fail_sdk_round=None, fail_auth_round=None, auth_exception=False):
        events, requests, auth_calls = [], [], []

        def exchange(inputs):
            events.append("sdk")
            requests.append(inputs)
            if len(requests) == fail_sdk_round:
                raise history.HistoryLoginError("history_service_rejected", request_attempted=True,
                                                http_status=200, application_code=1000)
            return sdk_candidate(inputs, len(requests))

        def authenticate(sauth, **options):
            events.append("auth")
            auth_calls.append((copy.deepcopy(sauth), options))
            if len(auth_calls) == fail_auth_round and auth_exception:
                raise RuntimeError("fixture-session-secret")
            return auth_report(enter=enter, accepted=len(auth_calls) != fail_auth_round)

        def random_bits(bits):
            self.assertEqual(bits, 53)
            events.append("random")
            return 1 << 52

        def clock():
            events.append("clock")
            return 1700000000123000000 + 1000000 * len(auth_calls)

        options = dict(OPTIONS, enter_gateway=True) if enter else OPTIONS
        with patch.object(history, "login_history_once", side_effect=exchange), \
                patch.object(handoff, "attempt_auth", side_effect=authenticate), \
                patch.object(handoff.secrets, "randbits", side_effect=random_bits), \
                patch.object(handoff.time, "time_ns", side_effect=clock):
            result = handoff.attempt_history_chain(bundle(), **options)
        return result, events, requests, auth_calls

    def test_real_chain_uses_two_fresh_candidates_and_only_returned_refresh(self):
        result, events, requests, auth_calls = self.execute()
        self.assertEqual(events, ["sdk"] + ["random"] * 8 + ["clock", "auth"]
                         + ["sdk"] + ["random"] * 8 + ["clock", "auth"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].refresh_token, "fixture-saved-token")
        self.assertEqual(requests[1].refresh_token, "fixture-new-refresh-1")
        self.assertEqual([args[0]["sessionid"] for args in auth_calls],
                         ["fixture-new-access-1", "fixture-new-access-2"])
        for sauth, options in auth_calls:
            self.assertEqual(len(sauth), 10)
            self.assertNotIn("refresh_token", sauth)
            self.assertEqual(options["runtime_version"], "3848703")
            self.assertIs(options["with_reviewed_pc_envelope"], True)
            self.assertFalse(options.get("enter_gateway", False))
            self.assertLessEqual(set(options), {"runtime_version", "with_reviewed_pc_envelope",
                                               "with_reviewed_version", "enter_gateway"})
        self.assertTrue(result["renewal_chain_verified"])
        self.assertFalse(result["automatic_renewal_verified"])
        self.assertEqual(result["sdk_history_chain"]["rounds_completed"], 2)
        self.assertTrue(result["sdk_history_chain"]["replacement_token_used"])
        self.assertTrue(result["sdk_history_chain"]["replacement_token_accepted"])
        self.assertFalse(result["sdk_history_chain"]["automatic_renewal_verified"])
        self.assertEqual(len(result["auth_rounds"]), 2)
        self.assertNotIn("fixture-", json.dumps(result))

    def test_gateway_option_is_current_owner_only_and_nested_report_is_projected(self):
        result, _, _, auth_calls = self.execute(enter=True)
        self.assertEqual(len(auth_calls), 2)
        self.assertTrue(all(options["enter_gateway"] for _, options in auth_calls))
        for report in result["auth_rounds"]:
            self.assertTrue(report["game_login_verified"])
            self.assertTrue(report["gateway"]["game_login_verified"])
            self.assertEqual(report["gateway"]["bytes_received"], 39295)
            self.assertNotIn("raw_login_key", report["gateway"])
        self.assertTrue(result["renewal_chain_verified"])
        self.assertFalse(result["automatic_renewal_verified"])
        self.assertNotIn("fixture-", json.dumps(result))

    def test_first_sdk_rejection_produces_no_auth_or_fallback(self):
        result, _, requests, auth_calls = self.execute(fail_sdk_round=1)
        self.assertEqual(len(requests), 1)
        self.assertEqual(auth_calls, [])
        self.assertEqual(result["auth_rounds"], [])
        self.assertFalse(result["renewal_chain_verified"])
        metadata = result["sdk_history_chain"]
        self.assertEqual(metadata["rounds_completed"], 0)
        self.assertEqual(metadata["rounds"][0]["http_status"], 200)
        self.assertEqual(metadata["rounds"][0]["application_code"], 1000)

    def test_second_sdk_rejection_keeps_first_verified_round_and_stops(self):
        result, _, requests, auth_calls = self.execute(fail_sdk_round=2)
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(auth_calls), 1)
        self.assertEqual(len(result["auth_rounds"]), 1)
        self.assertEqual(result["sdk_history_chain"]["rounds_completed"], 1)
        self.assertTrue(result["sdk_history_chain"]["replacement_token_used"])
        self.assertFalse(result["sdk_history_chain"]["replacement_token_accepted"])
        self.assertFalse(result["renewal_chain_verified"])

    def test_auth_rejection_retains_projected_diagnostics_and_never_advances_failed_round(self):
        for failed_round in (1, 2):
            result, _, requests, auth_calls = self.execute(fail_auth_round=failed_round)
            self.assertEqual(len(requests), failed_round)
            self.assertEqual(len(auth_calls), failed_round)
            self.assertEqual(result["sdk_history_chain"]["rounds_completed"], failed_round - 1)
            self.assertEqual(result["auth_rounds"][-1]["server_error_code"], 1911)
            self.assertFalse(result["renewal_chain_verified"])
            self.assertFalse(result["automatic_renewal_verified"])
            self.assertNotIn("fixture-", json.dumps(result))

    def test_auth_exception_never_retries_or_exports_exception_text(self):
        result, _, requests, auth_calls = self.execute(fail_auth_round=1, auth_exception=True)
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(auth_calls), 1)
        self.assertFalse(result["renewal_chain_verified"])
        self.assertEqual(result["sdk_history_chain"]["rounds_completed"], 0)
        self.assertNotIn("fixture-", json.dumps(result))
        for report in result["auth_rounds"]:
            self.assertIsNone(report["credentials_sent"])

    def test_unrecognized_auth_reports_are_projected_before_the_owner_gate(self):
        cases = [{"outcome": "fixture-unknown", "auth_service_accepted": True,
                  "own_role_verified": True, "raw_session": "fixture-secret"},
                 ["fixture-secret"], "fixture-secret", None]
        for raw in cases:
            count = 0

            def exchange(inputs):
                nonlocal count
                count += 1
                return sdk_candidate(inputs, count)

            with patch.object(history, "login_history_once", side_effect=exchange) as sdk, \
                    patch.object(handoff, "attempt_auth", return_value=raw):
                result = handoff.attempt_history_chain(bundle(), **OPTIONS)
            sdk.assert_called_once()
            self.assertFalse(result["renewal_chain_verified"])
            self.assertEqual(result["sdk_history_chain"]["rounds_completed"], 0)
            self.assertEqual(len(result["auth_rounds"]), 1)
            self.assertIsInstance(result["auth_rounds"][0], dict)
            self.assertFalse(result["auth_rounds"][0]["own_role_verified"])
            self.assertNotIn("fixture-", json.dumps(result))

    def test_build_flags_types_and_other_actions_rejected_before_any_io(self):
        options = [dict(OPTIONS, runtime_version="3825464"), dict(OPTIONS, runtime_version="unknown"),
                   dict(OPTIONS, with_reviewed_pc_envelope=False), dict(OPTIONS, with_reviewed_pc_envelope=1),
                   dict(OPTIONS, with_reviewed_version="yes"), dict(OPTIONS, enter_gateway="yes"),
                   dict(OPTIONS, unknown="fixture-private"), dict(OPTIONS, lookup_uid=1370598422)]
        options += [dict(OPTIONS, **{key: True}) for key in ("own_career", "other_career", "other_matches",
            "other_ranks", "collect_social_token", "verify_social_session", "refresh_before_auth")]
        with patch.object(history, "login_history_once") as sdk, \
                patch.object(chain, "run_history_chain") as runner, \
                patch.object(handoff, "attempt_auth") as auth, \
                patch.object(handoff.runtime, "load_runtime_manifest") as loaded, \
                patch("requests.Session") as session:
            for option in options:
                with self.subTest(keys=list(option)), self.assertRaises((ValueError, TypeError)):
                    handoff.attempt_history_chain(bundle(), **option)
        for mock in (sdk, runner, auth, loaded, session):
            mock.assert_not_called()

    def test_invalid_owner_bundle_cannot_trigger_sdk_or_auth(self):
        values = [dict(bundle(), selected_uid=1), dict(bundle(), selected_uid=True),
                  dict(bundle(), wrapper_udid="fixture-other"), dict(bundle(), extra="fixture-secret")]
        mismatched = bundle()
        mismatched["saved_account"]["userId"] += 1
        values.append(mismatched)
        with patch.object(history, "login_history_once") as sdk, \
                patch.object(handoff, "attempt_auth") as auth, \
                patch.object(handoff.runtime, "load_runtime_manifest") as loaded:
            for value in values:
                try:
                    report = handoff.attempt_history_chain(value, **OPTIONS)
                except ValueError:
                    continue
                self.assertFalse(report["renewal_chain_verified"])
                self.assertEqual(report["auth_rounds"], [])
                self.assertNotIn("fixture-", json.dumps(report))
        sdk.assert_not_called()
        auth.assert_not_called()
        loaded.assert_not_called()


class ChainCLISelectionTest(unittest.TestCase):
    def test_preview_requires_current_pc_and_is_offline(self):
        with patch.object(handoff, "HTTPServer") as listener, \
                patch.object(handoff, "attempt_history_chain") as attempt, \
                patch.object(handoff.runtime, "load_runtime_manifest") as loaded, \
                patch.object(history, "login_history_once") as sdk:
            for enter in (False, True):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(handoff.main(CLI + (["--enter"] if enter else [])), 0)
                report = json.loads(output.getvalue())
                self.assertTrue(report["verify_history_renewal"])
                self.assertEqual(report["runtime_version"], "3848703")
                self.assertEqual(report["enter_gateway"], enter)
                for key in ("network_attempted", "files_read", "credentials_read"):
                    self.assertFalse(report[key])
        for mock in (listener, attempt, loaded, sdk):
            mock.assert_not_called()

    def test_other_renewal_or_research_cli_modes_cannot_be_combined(self):
        cases = [["--verify-history-renewal"],
                 ["--verify-history-renewal", "--runtime-version", "3848703"],
                 ["--verify-history-renewal", "--with-reviewed-pc-envelope"]]
        cases += [CLI + [flag] for flag in ("--history-login-before-auth", "--refresh-before-auth",
            "--collect-social-token", "--verify-social-session", "--own-career", "--other-career",
            "--other-matches", "--other-ranks")]
        cases.append(CLI + ["--lookup-uid", "1370598422"])
        with patch.object(handoff, "HTTPServer") as listener, \
                patch.object(history, "login_history_once") as sdk, \
                patch.object(handoff.runtime, "load_runtime_manifest") as loaded:
            for args in cases:
                with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    handoff.main(args)
        for mock in (listener, sdk, loaded):
            mock.assert_not_called()

    def test_synthetic_serve_mode_selects_history_bundle_and_chain_runner_once(self):
        server = SimpleNamespace(server_port=12345)
        factory = MagicMock()
        factory.return_value.__enter__.return_value = server
        received = []

        def attempt(value, **options):
            received.append((copy.deepcopy(value), options))
            return {"renewal_chain_verified": True, "automatic_renewal_verified": False}

        def deliver():
            cls = factory.call_args.args[1]
            base, _ = handler()
            value = cls.__new__(cls)
            value.__dict__.update(base.__dict__)
            value.server = server
            value.do_POST()

        server.handle_request = Mock(side_effect=deliver)
        output = io.StringIO()
        with patch.object(handoff, "HTTPServer", factory), \
                patch.object(handoff.secrets, "token_urlsafe", return_value="fixture-capability"), \
                patch.object(handoff, "attempt_history_chain", side_effect=attempt) as selected, \
                patch.object(handoff, "attempt_history_auth") as single, \
                patch.object(handoff, "attempt_auth") as ordinary, redirect_stdout(output):
            self.assertEqual(handoff.main(CLI + ["--serve-once", "--enter"]), 0)
        selected.assert_called_once()
        single.assert_not_called()
        ordinary.assert_not_called()
        self.assertEqual(received[0][0], bundle())
        self.assertTrue(received[0][1]["enter_gateway"])
        self.assertEqual(received[0][1]["runtime_version"], "3848703")
        self.assertTrue(server.completed)
        server.handle_request.assert_called_once()
        self.assertNotIn("fixture-saved-token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
