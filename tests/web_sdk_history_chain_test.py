"""Two-round fake SDK/AUTH orchestration only, with no live account calls."""
import copy
from contextlib import ExitStack
from dataclasses import replace
import json
import unittest
from unittest.mock import Mock, patch

import web_sdk_history_chain as chain
import web_sdk_history_login as history
from web_sdk_history_handoff_test import bundle


def sdk_candidate(inputs, round_number):
    return history.validate_history_response(inputs, json.dumps({"code": 0, "user": {
        "login_token": "fixture-new-access-" + str(round_number),
        "token": "fixture-new-refresh-" + str(round_number)}}).encode(), http_status=200)


class HistoryChainTest(unittest.TestCase):
    def execute(self, value=None, *, sdk_side_effect=None, auth_side_effect=None, **overrides):
        events, submitted, authenticated = [], [], []

        def exchange(inputs):
            events.append("sdk")
            submitted.append(inputs)
            if sdk_side_effect is not None:
                return sdk_side_effect(inputs, len(submitted))
            return sdk_candidate(inputs, len(submitted))

        def authenticate(sauth):
            events.append("auth")
            authenticated.append(copy.deepcopy(sauth))
            if auth_side_effect is not None:
                return auth_side_effect(sauth, len(authenticated))
            return {"auth_service_accepted": True, "own_role_verified": True,
                    "raw_secret": "fixture-must-not-escape", "arbitrary": object()}

        def samples():
            events.append("random")
            return [.5] * 8

        def timestamp():
            events.append("clock")
            return 1700000000123 + len(authenticated)

        options = {"authenticate": authenticate, "random_samples": samples, "clock_ms": timestamp}
        options.update(overrides)
        with patch.object(history, "login_history_once", side_effect=exchange) as request:
            report = chain.run_history_chain(bundle() if value is None else value, **options)
        return report, events, submitted, authenticated, request

    def test_second_round_uses_only_returned_token_after_first_owner_auth(self):
        value = bundle()
        before = copy.deepcopy(value)
        result, events, submitted, authenticated, request = self.execute(value)
        self.assertEqual(events, ["sdk", "random", "clock", "auth"] * 2)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(submitted[0].refresh_token, "fixture-saved-token")
        self.assertEqual(submitted[1].refresh_token, "fixture-new-refresh-1")
        self.assertNotEqual(submitted[1].refresh_token, submitted[0].refresh_token)
        self.assertEqual(submitted[0].sauth_sdkuid, submitted[1].sauth_sdkuid)
        self.assertEqual(submitted[0].saved_device_id, submitted[1].saved_device_id)
        self.assertEqual(submitted[0].mpay_context, submitted[1].mpay_context)
        self.assertEqual([item["sessionid"] for item in authenticated],
                         ["fixture-new-access-1", "fixture-new-access-2"])
        self.assertTrue(all(len(item) == 10 and "refresh_token" not in item for item in authenticated))
        self.assertNotEqual(authenticated[0]["client_login_sn"], authenticated[1]["client_login_sn"])
        self.assertEqual(value, before)
        self.assertEqual(result["outcome"], "history_chain_verified")
        self.assertEqual(result["rounds_completed"], 2)
        self.assertTrue(result["replacement_token_used"])
        self.assertTrue(result["replacement_token_accepted"])
        self.assertTrue(result["renewal_chain_verified"])
        self.assertFalse(result["automatic_renewal_verified"])
        self.assertFalse(result["secrets_saved"])
        self.assertFalse(result["browser_storage_changed"])
        self.assertNotIn("fixture-", json.dumps(result))
        self.assertNotIn("raw_secret", json.dumps(result))

    def test_first_auth_unverified_stops_before_replacement_exchange(self):
        cases = [None, [], {"auth_service_accepted": True}, {"own_role_verified": True},
                 {"auth_service_accepted": True, "own_role_verified": False},
                 {"auth_service_accepted": 1, "own_role_verified": True},
                 {"auth_service_accepted": True, "own_role_verified": "true"}]
        for auth_result in cases:
            with self.subTest(result_type=type(auth_result).__name__):
                result, events, submitted, authenticated, request = self.execute(
                    auth_side_effect=lambda _sauth, _number: auth_result)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(result["rounds_completed"], 0)
                self.assertEqual(result["outcome"], "history_chain_auth_unverified")
                self.assertFalse(result["replacement_token_used"])
                self.assertFalse(result["renewal_chain_verified"])
                self.assertEqual(len(authenticated), 1)

    def test_second_auth_rejection_preserves_first_completed_round(self):
        result, _, _, authenticated, request = self.execute(auth_side_effect=lambda _sauth, number: {
            "auth_service_accepted": True, "own_role_verified": number == 1,
            "outcome": "fixture-private-reason"})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["rounds_completed"], 1)
        self.assertTrue(result["replacement_token_used"])
        self.assertTrue(result["replacement_token_accepted"])
        self.assertFalse(result["renewal_chain_verified"])
        self.assertTrue(result["rounds"][0]["owner_authentication_verified"])
        self.assertFalse(result["rounds"][1]["owner_authentication_verified"])
        self.assertEqual(len(authenticated), 2)
        self.assertNotIn("fixture-", json.dumps(result))

    def test_sdk_rejection_preserves_partial_progress_and_never_authenticates_failed_round(self):
        for fail_round in (1, 2):
            def exchange(inputs, number):
                if number == fail_round:
                    raise history.HistoryLoginError("history_service_rejected", request_attempted=True,
                                                     http_status=200, application_code=1000)
                return sdk_candidate(inputs, number)
            result, _, _, authenticated, request = self.execute(sdk_side_effect=exchange)
            self.assertEqual(request.call_count, fail_round)
            self.assertEqual(len(authenticated), fail_round - 1)
            self.assertEqual(result["rounds_completed"], fail_round - 1)
            self.assertFalse(result["replacement_token_accepted"])
            self.assertFalse(result["renewal_chain_verified"])
            self.assertEqual(result["outcome"], "history_chain_sdk_failed")
            self.assertEqual(result["rounds"][-1]["application_code"], 1000)
            self.assertEqual(result["rounds"][-1]["http_status"], 200)
            self.assertTrue(result["rounds"][-1]["request_attempted"])
            self.assertFalse(result["rounds"][-1]["auth_attempted"])

    def test_http_failure_and_unknown_exception_metadata_are_redacted_without_retry(self):
        errors = [history.HistoryLoginError("history_http_error", request_attempted=True, http_status=403),
                  history.HistoryLoginError("history_request_failed", request_attempted=False),
                  history.HistoryLoginError("history_request_failed", request_attempted=None),
                  RuntimeError("fixture-secret")]
        for error in errors:
            def fail(_inputs, _number):
                raise error
            result, _, _, authenticated, request = self.execute(sdk_side_effect=fail)
            request.assert_called_once()
            self.assertEqual(authenticated, [])
            self.assertNotIn("fixture-", json.dumps(result))
            self.assertEqual(result["rounds"][0]["request_attempted"],
                             error.request_attempted if isinstance(error, history.HistoryLoginError) else None)
            self.assertEqual(result["rounds"][0]["http_status"],
                             error.http_status if isinstance(error, history.HistoryLoginError) else None)

    def test_auth_exceptions_preserve_accepted_rounds_and_never_retry(self):
        for failed_round in (1, 2):
            def authenticate(_sauth, number):
                if number == failed_round:
                    raise RuntimeError("fixture-private-auth-error")
                return {"auth_service_accepted": True, "own_role_verified": True}
            result, _, _, _, request = self.execute(auth_side_effect=authenticate)
            self.assertEqual(request.call_count, failed_round)
            self.assertEqual(result["rounds_completed"], failed_round - 1)
            self.assertTrue(result["rounds"][-1]["auth_attempted"])
            self.assertEqual(result["outcome"], "history_chain_auth_failed")
            self.assertFalse(result["renewal_chain_verified"])
            self.assertNotIn("fixture-", json.dumps(result))

    def test_callback_cannot_change_the_next_request_binding_or_supply_a_token(self):
        def authenticate(sauth, _number):
            sauth.update(sdkuid="999", sessionid="fixture-attacker-access", udid="fixture-mutated")
            return {"auth_service_accepted": True, "own_role_verified": True,
                    "refresh_token": "fixture-wrong-refresh", "sauth": sauth}
        result, _, submitted, _, _ = self.execute(auth_side_effect=authenticate)
        self.assertTrue(result["renewal_chain_verified"])
        self.assertEqual(submitted[1].sauth_sdkuid, bundle()["sauth"]["sdkuid"])
        self.assertEqual(submitted[1].refresh_token, "fixture-new-refresh-1")
        self.assertNotIn("fixture-", json.dumps(result))

    def test_replacement_token_not_form_compatible_stops_before_second_exchange(self):
        def exchange(inputs, number):
            return replace(sdk_candidate(inputs, number), refresh_token="fixture+unsupported")
        result, _, _, authenticated, request = self.execute(sdk_side_effect=exchange)
        request.assert_called_once()
        self.assertEqual(len(authenticated), 1)
        self.assertEqual(result["rounds_completed"], 1)
        self.assertFalse(result["replacement_token_used"])
        self.assertFalse(result["renewal_chain_verified"])
        self.assertFalse(result["rounds"][-1]["request_attempted"])

    def test_bad_provider_results_or_exceptions_stop_before_auth(self):
        for options in ({"random_samples": lambda: [0] * 7}, {"clock_ms": lambda: True},
                        {"random_samples": Mock(side_effect=RuntimeError("fixture-secret"))},
                        {"clock_ms": Mock(side_effect=RuntimeError("fixture-secret"))}):
            result, _, _, authenticated, request = self.execute(**options)
            request.assert_called_once()
            self.assertEqual(authenticated, [])
            self.assertTrue(result["rounds"][0]["candidate_received"])
            self.assertFalse(result["rounds"][0]["sauth_constructed"])
            self.assertEqual(result["outcome"], "history_chain_sauth_failed")
            self.assertNotIn("fixture-", json.dumps(result))

    def test_second_sdk_acceptance_survives_later_serial_failure_without_claiming_owner(self):
        values = iter(([.5] * 8, [0] * 7))
        result, _, _, authenticated, request = self.execute(random_samples=lambda: next(values))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(authenticated), 1)
        self.assertTrue(result["replacement_token_accepted"])
        self.assertEqual(result["rounds_completed"], 1)
        self.assertFalse(result["renewal_chain_verified"])
        self.assertFalse(result["rounds"][1]["auth_attempted"])

    def test_returned_token_need_not_differ_to_be_used_without_fallback(self):
        def exchange(inputs, number):
            return replace(sdk_candidate(inputs, number), refresh_token=inputs.refresh_token)
        result, _, submitted, _, request = self.execute(sdk_side_effect=exchange)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(submitted[0].refresh_token, submitted[1].refresh_token)
        self.assertTrue(result["replacement_token_used"])
        self.assertTrue(result["renewal_chain_verified"])
        self.assertFalse(result["automatic_renewal_verified"])

    def test_wrong_owner_binding_source_context_and_modes_fail_before_any_io(self):
        values = [None, {}, dict(bundle(), selected_uid=True), dict(bundle(), selected_uid=999),
                  dict(bundle(), wrapper_udid="fixture-other"), dict(bundle(), wrapper_udid="\ud800"),
                  dict(bundle(), extra="fixture-secret"), dict(bundle(), saved_account=None),
                  dict(bundle(), mpay_context=[])]
        for path, field, value in (("sauth", "gameid", "fixture-game"),
                                   ("sauth", "refresh_token", "fixture-secret"),
                                   ("saved_account", "userId", 999), ("mpay_context", "lang", "")):
            item = bundle()
            item[path][field] = value
            values.append(item)
        options = [{"rounds": value} for value in (True, 1, 3, "2", None)]
        options += [{key: None} for key in ("authenticate", "clock_ms", "random_samples")]
        targets = ("builtins.open", "pathlib.Path.open", "socket.create_connection", "urllib.request.build_opener")
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(target)) for target in targets]
            request = stack.enter_context(patch.object(history, "login_history_once"))
            for value, override in [(value, {}) for value in values] + [(bundle(), option) for option in options]:
                providers = {"authenticate": Mock(), "clock_ms": Mock(), "random_samples": Mock()}
                providers.update(override)
                result = chain.run_history_chain(value, **providers)
                self.assertEqual(result["outcome"], "history_chain_context_invalid")
                self.assertEqual(result["rounds_completed"], 0)
                self.assertEqual(result["rounds"], [])
                for provider in providers.values():
                    if isinstance(provider, Mock):
                        provider.assert_not_called()
        for guard in guards + [request]:
            guard.assert_not_called()

    def test_altered_error_objects_cannot_export_arbitrary_metadata(self):
        error = history.HistoryLoginError("history_request_failed")
        error.code, error.http_status = "fixture-secret", True
        error.application_code, error.request_attempted = "fixture-secret", "fixture-secret"
        def fail(_inputs, _number):
            raise error
        result, _, _, _, _ = self.execute(sdk_side_effect=fail)
        metadata = result["rounds"][0]
        self.assertEqual(metadata["outcome"], "history_request_failed")
        self.assertIsNone(metadata["http_status"])
        self.assertIsNone(metadata["application_code"])
        self.assertIsNone(metadata["request_attempted"])
        self.assertNotIn("fixture-", json.dumps(result))

    def test_success_has_fixed_output_schema_and_no_implicit_runtime_dependencies(self):
        targets = ("builtins.open", "pathlib.Path.open", "socket.create_connection", "requests.Session",
                   "urllib.request.build_opener", "game_login_probe.load_own_login_inputs",
                   "game_login_probe.check_login", "game_transport_probe.load_public_configuration",
                   "time.time", "time.time_ns", "random.random", "os.urandom")
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(target, side_effect=AssertionError("unexpected dependency")))
                      for target in targets]
            result, _, _, _, request = self.execute()
        request.assert_called()
        self.assertTrue(result["renewal_chain_verified"])
        self.assertEqual(set(result), {"outcome", "rounds_requested", "rounds_completed", "replacement_token_used",
            "replacement_token_accepted", "renewal_chain_verified", "automatic_renewal_verified", "secrets_saved",
            "browser_storage_changed", "rounds"})
        for item in result["rounds"]:
            self.assertEqual(set(item), {"round", "outcome", "request_attempted", "candidate_received",
                "sauth_constructed", "auth_attempted", "owner_authentication_verified", "http_status", "application_code"})
        for guard in guards:
            guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
