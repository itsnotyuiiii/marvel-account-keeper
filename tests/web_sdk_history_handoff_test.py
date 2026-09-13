"""Synthetic saved-login handoff and candidate-only AUTH; no real listeners or IO."""
import copy
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from http.client import HTTPMessage
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import web_sdk_history_login as history
from web_sdk_history_login_test import account, context, previous, response


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "probe-web-sdk-login.py"
spec = importlib.util.spec_from_file_location("web_sdk_history_handoff", SCRIPT)
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)
OPTIONS = {"runtime_version": "3848703", "with_reviewed_pc_envelope": True}
CLI = ["--history-login-before-auth", "--runtime-version", "3848703", "--with-reviewed-pc-envelope"]


def bundle():
    sauth = previous()
    return {"selected_uid": 1370598422, "sauth": sauth, "saved_account": account(),
            "mpay_context": context(), "wrapper_udid": sauth["udid"]}


def candidate():
    value = bundle()
    inputs = history.build_history_login_inputs(value["sauth"], value["saved_account"], value["mpay_context"])
    return history.validate_history_response(inputs, response(), http_status=200)


def handler(value=None, *, allow_history=True, allow_refresh=False, attempt=None, raw=None):
    if attempt is None:
        attempt = Mock(return_value={"outcome": "own_existing_role_verified", "secrets_saved": False})
    cls = handoff.make_handler("/auth-once/fixture-capability", attempt,
                               allow_history=allow_history, allow_refresh=allow_refresh)
    instance = cls.__new__(cls)
    instance.path = "/auth-once/fixture-capability"
    instance.server = SimpleNamespace(server_port=12345, completed=False, report=None, payload_read_started=False)
    instance.client_address = ("127.0.0.1", 54321)
    if raw is None:
        raw = json.dumps(bundle() if value is None else value).encode()
    instance.headers = HTTPMessage()
    for key, item in {"Origin": handoff.STORE_ORIGIN, "Host": "127.0.0.1:12345",
                      "Content-Type": "application/json", "Content-Length": str(len(raw))}.items():
        instance.headers[key] = item
    instance.rfile = io.BytesIO(raw)
    instance.reply = Mock()
    return instance, attempt


class HistoryHandoffHandlerTest(unittest.TestCase):
    def test_exact_projected_bundle_consumed_before_dispatch_and_only_once(self):
        seen = []
        instance, attempt = handler()

        def accept(value):
            self.assertTrue(instance.server.completed)
            seen.append(copy.deepcopy(value))
            return {"outcome": "own_existing_role_verified", "secrets_saved": False}

        attempt.side_effect = accept
        instance.do_POST()
        self.assertEqual(seen, [bundle()])
        self.assertEqual(instance.reply.call_args.args[0], 200)
        self.assertNotIn("fixture-", json.dumps(instance.server.report))
        # The actual parsed handoff object is cleared after the synchronous call.
        self.assertEqual(attempt.call_args.args[0], {})
        instance.do_POST()
        self.assertEqual(instance.reply.call_args.args[0], 403)
        attempt.assert_called_once()

    def test_default_mode_rejects_history_fields_and_history_requires_exact_bundle(self):
        instance, attempt = handler(allow_history=False)
        instance.do_POST()
        self.assertEqual(instance.reply.call_args.args[0], 400)
        attempt.assert_not_called()
        cases = []
        for key in bundle():
            value = bundle()
            del value[key]
            cases.append(value)
        cases.append(dict(bundle(), extra="fixture-secret"))
        for value in cases:
            with self.subTest(keys=list(value)):
                instance, attempt = handler(value)
                instance.do_POST()
                self.assertEqual(instance.reply.call_args.args[0], 400)
                self.assertFalse(instance.server.completed)
                attempt.assert_not_called()

    def test_wrong_identity_context_or_token_mode_never_dispatches(self):
        cases = []
        for change in ("owner", "owner_bool", "sdkuid", "wrapper", "game", "refresh", "saved_extra", "context_extra"):
            value = bundle()
            if change == "owner":
                value["selected_uid"] = 999
            elif change == "owner_bool":
                value["selected_uid"] = True
            elif change == "sdkuid":
                value["saved_account"]["userId"] += 1
            elif change == "wrapper":
                value["wrapper_udid"] = "fixture-other-udid"
            elif change == "game":
                value["sauth"]["gameid"] = "fixture-other-game"
            elif change == "refresh":
                value["sauth"]["refresh_token"] = "fixture-other-renewal-token"
            elif change == "saved_extra":
                value["saved_account"]["nickname"] = "fixture-name"
            else:
                value["mpay_context"]["signature"] = "fixture-secret"
            cases.append(value)
        with patch.object(history, "login_history_once") as exchange, patch.object(handoff, "attempt_auth") as auth:
            for value in cases:
                instance, attempt = handler(value)
                instance.do_POST()
                self.assertEqual(instance.reply.call_args.args[0], 400)
                self.assertNotIn("fixture-", json.dumps(instance.reply.call_args.args))
                attempt.assert_not_called()
        exchange.assert_not_called()
        auth.assert_not_called()

    def test_only_exact_origin_host_path_and_loopback_can_submit(self):
        for field in ("origin", "host", "path", "peer"):
            instance, attempt = handler()
            if field == "origin":
                instance.headers.replace_header("Origin", "https://other.invalid")
            elif field == "host":
                instance.headers.replace_header("Host", "example.invalid:12345")
            elif field == "path":
                instance.path += "-wrong"
            else:
                instance.client_address = ("192.0.2.1", 54321)
            instance.do_POST()
            self.assertEqual(instance.reply.call_args.args[0], 403)
            self.assertFalse(instance.server.payload_read_started)
            attempt.assert_not_called()

    def test_preflight_does_not_consume_and_attempt_error_cannot_retry(self):
        instance, attempt = handler()
        instance.headers["Access-Control-Request-Method"] = "POST"
        instance.headers["Access-Control-Request-Headers"] = "content-type"
        instance.do_OPTIONS()
        self.assertFalse(instance.server.completed)
        attempt.assert_not_called()
        attempt.side_effect = RuntimeError("fixture-access-token")
        instance.do_POST()
        self.assertTrue(instance.server.completed)
        self.assertEqual(instance.server.report, {"outcome": "auth_probe_failed", "secrets_saved": False})
        instance.do_POST()
        attempt.assert_called_once()

    def test_no_simultaneous_refresh_and_history_mode(self):
        with self.assertRaisesRegex(ValueError, "select_one_renewal_path"):
            handler(allow_history=True, allow_refresh=True)
        for value in (1, None, "true"):
            with self.assertRaises(ValueError):
                handler(allow_history=value)

    def test_duplicate_fields_and_oversize_payloads_are_rejected(self):
        raw = json.dumps(bundle()).encode()
        duplicate = raw[:-1] + b',"selected_uid":1370598422}'
        for value in (duplicate, b"x" * (handoff.MAX_HANDOFF_BYTES + 1)):
            instance, attempt = handler(raw=value)
            instance.do_POST()
            self.assertEqual(instance.reply.call_args.args[0], 400)
            attempt.assert_not_called()


class HistoryAuthPipelineTest(unittest.TestCase):
    def test_only_new_candidate_sauth_reaches_auth_and_one_success_does_not_prove_renewal(self):
        value, events = bundle(), []
        before = copy.deepcopy(value)
        fresh = candidate()

        def random_bits(bits):
            events.append("random")
            self.assertEqual(bits, 53)
            return 1 << 52

        def clock():
            events.append("clock")
            return 1700000000123000000

        with patch.object(history, "login_history_once", return_value=fresh) as exchange, \
                patch.object(handoff.secrets, "randbits", side_effect=random_bits), \
                patch.object(handoff.time, "time_ns", side_effect=clock), \
                patch.object(handoff, "attempt_auth", return_value={"outcome": "own_existing_role_verified",
                    "auth_service_accepted": True, "own_role_verified": True}) as auth:
            report = handoff.attempt_history_auth(value, **OPTIONS)
        self.assertEqual(events, ["random"] * 8 + ["clock"])
        exchange.assert_called_once()
        sent = auth.call_args.args[0]
        self.assertEqual(auth.call_count, 1)
        self.assertEqual(auth.call_args.kwargs, OPTIONS)
        self.assertEqual(sent["sessionid"], "fixture-access-token")
        self.assertNotEqual(sent["sessionid"], value["sauth"]["sessionid"])
        self.assertEqual(sent["sdkuid"], value["sauth"]["sdkuid"])
        self.assertEqual(sent["deviceid"], str(value["saved_account"]["deviceId"]))
        self.assertEqual(sent["udid"], value["wrapper_udid"])
        self.assertNotIn("refresh_token", sent)
        self.assertEqual(len(sent), 10)
        self.assertEqual(value, before)
        metadata = report["sdk_history_login"]
        self.assertTrue(metadata["owner_authentication_verified"])
        self.assertTrue(metadata["candidate_received"])
        self.assertTrue(metadata["sauth_constructed"])
        self.assertFalse(metadata["automatic_renewal_verified"])
        self.assertFalse(metadata["browser_storage_changed"])
        self.assertFalse(metadata["secrets_saved"])
        self.assertNotIn("fixture-", json.dumps(report))

    def test_sdk_failure_preserves_fixed_status_and_application_code_without_auth(self):
        for attempted in (True, False, None):
            error = history.HistoryLoginError("history_service_rejected", request_attempted=attempted,
                                               http_status=200, application_code=1000)
            with patch.object(history, "login_history_once", side_effect=error) as exchange, \
                    patch.object(history, "build_fresh_wrapper_sauth") as build, \
                    patch.object(handoff, "attempt_auth") as auth:
                report = handoff.attempt_history_auth(bundle(), **OPTIONS)
            exchange.assert_called_once()
            build.assert_not_called()
            auth.assert_not_called()
            self.assertEqual(report["outcome"], "web_history_login_failed")
            self.assertFalse(report["credentials_sent"])
            metadata = report["sdk_history_login"]
            self.assertEqual(metadata["http_status"], 200)
            self.assertEqual(metadata["application_code"], 1000)
            self.assertIs(metadata["request_attempted"], attempted)
            self.assertFalse(metadata["candidate_received"])
            self.assertFalse(metadata["owner_authentication_verified"])
            self.assertNotIn("expired", json.dumps(report))

    def test_http_rejection_has_no_old_session_fallback(self):
        error = history.HistoryLoginError("history_http_error", request_attempted=True, http_status=403)
        with patch.object(history, "login_history_once", side_effect=error), \
                patch.object(handoff, "attempt_auth") as auth:
            report = handoff.attempt_history_auth(bundle(), **OPTIONS)
        auth.assert_not_called()
        self.assertEqual(report["sdk_history_login"]["http_status"], 403)
        self.assertIsNone(report["sdk_history_login"]["application_code"])

    def test_builder_failure_does_not_erase_exchange_attempt_or_call_auth(self):
        with patch.object(history, "login_history_once", return_value=candidate()), \
                patch.object(history, "build_fresh_wrapper_sauth", side_effect=history.HistoryLoginError(
                    "history_serial_context_invalid")), patch.object(handoff, "attempt_auth") as auth:
            report = handoff.attempt_history_auth(bundle(), **OPTIONS)
        auth.assert_not_called()
        metadata = report["sdk_history_login"]
        self.assertTrue(metadata["request_attempted"])
        self.assertTrue(metadata["candidate_received"])
        self.assertFalse(metadata["sauth_constructed"])
        self.assertFalse(metadata["owner_authentication_verified"])

    def test_auth_rejection_diagnostics_survive_and_do_not_retry_or_prove_owner(self):
        auth_report = {"outcome": "server_rejected_authentication", "server_error_code": 1911,
                       "auth_service_accepted": False, "own_role_verified": False, "connections_attempted": 1}
        with patch.object(history, "login_history_once", return_value=candidate()) as exchange, \
                patch.object(handoff, "attempt_auth", return_value=copy.deepcopy(auth_report)) as auth:
            report = handoff.attempt_history_auth(bundle(), **OPTIONS)
        exchange.assert_called_once()
        auth.assert_called_once()
        for key, expected in auth_report.items():
            self.assertEqual(report[key], expected)
        self.assertTrue(report["sdk_history_login"]["candidate_received"])
        self.assertFalse(report["sdk_history_login"]["owner_authentication_verified"])
        self.assertFalse(report["sdk_history_login"]["automatic_renewal_verified"])

    def test_auth_exception_is_redacted_without_retry(self):
        with patch.object(history, "login_history_once", return_value=candidate()) as exchange, \
                patch.object(handoff, "attempt_auth", side_effect=RuntimeError("fixture-access-token")) as auth:
            report = handoff.attempt_history_auth(bundle(), **OPTIONS)
        exchange.assert_called_once()
        auth.assert_called_once()
        self.assertEqual(report["outcome"], "web_history_candidate_auth_failed")
        # A broad AUTH exception can occur after transmission. Unknown is
        # honest; false would incorrectly assert no game credential was sent.
        self.assertIsNone(report["credentials_sent"])
        self.assertTrue(report["sdk_history_login"]["candidate_received"])
        self.assertFalse(report["sdk_history_login"]["owner_authentication_verified"])
        self.assertNotIn("fixture-", json.dumps(report))

    def test_bad_bindings_and_options_are_rejected_before_exchange_or_other_io(self):
        values = [dict(bundle(), selected_uid=1), dict(bundle(), selected_uid=True),
                  dict(bundle(), wrapper_udid="fixture-wrong"), dict(bundle(), extra="fixture-secret")]
        value = bundle()
        value["saved_account"]["userId"] += 1
        values.append(value)
        options = [dict(OPTIONS, runtime_version="3825464"), dict(OPTIONS, with_reviewed_pc_envelope=False),
                   dict(OPTIONS, refresh_before_auth=True), dict(OPTIONS, lookup_uid=True),
                   dict(OPTIONS, enter_gateway="yes"), dict(OPTIONS, unreviewed="fixture-secret"),
                   dict(OPTIONS, other_career=True, other_ranks=True, lookup_uid=10826896958)]
        targets = ("builtins.open", "pathlib.Path.open", "socket.create_connection", "requests.Session")
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(target)) for target in targets]
            exchange = stack.enter_context(patch.object(history, "login_history_once"))
            auth = stack.enter_context(patch.object(handoff, "attempt_auth"))
            loaded = stack.enter_context(patch.object(handoff.runtime, "load_runtime_manifest"))
            for value, kwargs in [(value, OPTIONS) for value in values] + [(bundle(), option) for option in options]:
                with self.subTest(keys=list(kwargs)), self.assertRaises((ValueError, TypeError)):
                    handoff.attempt_history_auth(value, **kwargs)
        for guard in guards + [exchange, auth, loaded]:
            guard.assert_not_called()

    def test_pipeline_can_use_existing_current_runtime_auth_without_old_credentials(self):
        provenance, _ = handoff.runtime.reviewed_runtime_pin("3848703")
        public = SimpleNamespace(configuration=object(), login_list_url=handoff.runtime.LOGIN_LIST_URL,
                                 provenance=provenance)
        server = object()
        factory = MagicMock()
        session = factory.return_value.__enter__.return_value
        reply = session.get.return_value.__enter__.return_value
        reply.status_code, reply.url = 200, handoff.runtime.LOGIN_LIST_URL
        reply.raw.read.return_value = b"fixture-server-list"
        with patch.object(history, "login_history_once", return_value=candidate()) as exchange, \
                patch.object(handoff.runtime, "load_runtime_manifest", return_value=public), \
                patch.object(handoff.runtime, "project_login_server", return_value=server), \
                patch("requests.Session", factory), \
                patch.object(handoff.probe, "run_auth_once", return_value={"outcome": "own_existing_role_verified",
                    "auth_service_accepted": True, "own_role_verified": True}) as auth:
            report = handoff.attempt_history_auth(bundle(), **OPTIONS)
        exchange.assert_called_once()
        auth.assert_called_once()
        self.assertEqual(auth.call_args.args[0]["sessionid"], "fixture-access-token")
        self.assertEqual(auth.call_args.args[1:], (public.configuration, server))
        self.assertEqual(auth.call_args.kwargs, {"client_version": "3848703", "os_name": "pc"})
        self.assertFalse(session.trust_env)
        self.assertTrue(report["sdk_history_login"]["owner_authentication_verified"])
        self.assertFalse(report["sdk_history_login"]["automatic_renewal_verified"])


class HistoryCLISelectionTest(unittest.TestCase):
    def test_serve_mode_wires_history_runner_with_synthetic_server_only(self):
        # Exercise main's selection without constructing any real HTTPServer.
        server = SimpleNamespace(server_port=12345)
        server.handle_request = Mock()
        factory = MagicMock()
        factory.return_value.__enter__.return_value = server
        selected = []

        def dispatch(value, **kwargs):
            selected.append((copy.deepcopy(value), kwargs))
            return {"outcome": "own_existing_role_verified", "secrets_saved": False}

        def deliver():
            cls = factory.call_args.args[1]
            base, _ = handler()
            instance = cls.__new__(cls)
            instance.__dict__.update(base.__dict__)
            instance.server = server
            instance.do_POST()

        server.handle_request.side_effect = deliver
        output = io.StringIO()
        with patch.object(handoff, "HTTPServer", factory), \
                patch.object(handoff.secrets, "token_urlsafe", return_value="fixture-capability"), \
                patch.object(handoff, "attempt_history_auth", side_effect=dispatch) as history_attempt, \
                patch.object(handoff, "attempt_auth") as ordinary_attempt, redirect_stdout(output):
            self.assertEqual(handoff.main(CLI + ["--serve-once"]), 0)
        history_attempt.assert_called_once()
        ordinary_attempt.assert_not_called()
        self.assertEqual(selected[0][0], bundle())
        self.assertEqual(selected[0][1]["runtime_version"], "3848703")
        self.assertTrue(selected[0][1]["with_reviewed_pc_envelope"])
        self.assertFalse(selected[0][1]["refresh_before_auth"])
        self.assertTrue(server.completed)
        server.handle_request.assert_called_once()
        reports = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(reports[0]["history_login_before_auth"])
        self.assertEqual(reports[-1]["outcome"], "own_existing_role_verified")
        self.assertNotIn("fixture-saved-token", output.getvalue())

    def test_current_reviewed_preview_does_not_open_files_listener_or_network(self):
        targets = ("builtins.open", "pathlib.Path.open", "socket.create_connection", "requests.Session")
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(target)) for target in targets]
            guards.append(stack.enter_context(patch.object(handoff, "HTTPServer")))
            guards.append(stack.enter_context(patch.object(handoff.runtime, "load_runtime_manifest")))
            guards.append(stack.enter_context(patch.object(history, "login_history_once")))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(handoff.main(CLI), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["history_login_before_auth"])
        self.assertFalse(report["refresh_before_auth"])
        self.assertEqual(report["runtime_version"], "3848703")
        for key in ("network_attempted", "credentials_read", "files_read", "enter_gateway"):
            self.assertFalse(report[key])
        for guard in guards:
            guard.assert_not_called()

    def test_invalid_cli_modes_fail_before_listener_or_exchange(self):
        cases = [["--history-login-before-auth"],
                 ["--history-login-before-auth", "--runtime-version", "3848703"],
                 ["--history-login-before-auth", "--with-reviewed-pc-envelope"],
                 CLI + ["--refresh-before-auth"]]
        with patch.object(handoff, "HTTPServer") as listener, \
                patch.object(history, "login_history_once") as exchange, \
                patch.object(handoff.runtime, "load_runtime_manifest") as loaded:
            for args in cases:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    handoff.main(args)
        listener.assert_not_called()
        exchange.assert_not_called()
        loaded.assert_not_called()


if __name__ == "__main__":
    unittest.main()
