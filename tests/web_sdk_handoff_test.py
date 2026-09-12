"""Synthetic loopback handler tests; no listeners, accounts or network."""
from contextlib import redirect_stdout
from http.client import HTTPMessage
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "probe-web-sdk-login.py"
spec = importlib.util.spec_from_file_location("web_sdk_handoff", SCRIPT)
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


class WebSDKHandoffTest(unittest.TestCase):
    def payload(self):
        return {"selected_uid": 1370598422, "sauth": {
            "gameid": "x20naxx2gb", "platform": "pc", "login_channel": "netease_global",
            "app_channel": "netease.allysdk_global", "sdk_version": "1.0.0",
            "sdkuid": "fixture-user", "sessionid": "fixture-secret", "deviceid": "fixture-device",
            "udid": "fixture-udid", "client_login_sn": "fixture-login-sn"}}

    def handler(self, payload=None, raw=None):
        attempt = Mock(return_value={"outcome": "own_existing_role_verified", "secrets_saved": False})
        cls = handoff.make_handler("/auth-once/fixture-capability", attempt)
        value = cls.__new__(cls)
        value.path = "/auth-once/fixture-capability"
        value.server = SimpleNamespace(server_port=12345, completed=False, report=None,
                                       payload_read_started=False)
        value.client_address = ("127.0.0.1", 54321)
        if raw is None:
            raw = json.dumps(self.payload() if payload is None else payload).encode()
        value.headers = HTTPMessage()
        for key, item in {"Origin": handoff.STORE_ORIGIN, "Host": "127.0.0.1:12345",
                          "Content-Type": "application/json", "Content-Length": str(len(raw))}.items():
            value.headers[key] = item
        value.rfile = io.BytesIO(raw)
        value.reply = Mock()
        return value, attempt

    def test_preview_does_not_open_listener_or_load_runtime(self):
        with patch.object(handoff, "HTTPServer") as listener, patch.object(handoff, "attempt_auth") as attempt:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(handoff.main([]), 0)
            self.assertFalse(json.loads(out.getvalue())["network_attempted"])
            listener.assert_not_called()
            attempt.assert_not_called()

    def test_versioned_preview_still_reads_no_files_or_network(self):
        with patch.object(handoff, "HTTPServer") as listener, \
                patch.object(handoff.runtime, "load_runtime_manifest") as loaded:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(handoff.main(["--with-reviewed-version"]), 0)
            report = json.loads(out.getvalue())
        self.assertTrue(report["with_reviewed_version"])
        self.assertFalse(report["network_attempted"])
        self.assertFalse(report["files_read"])
        listener.assert_not_called()
        loaded.assert_not_called()

    def test_versioned_attempt_uses_only_explicit_packaged_provenance(self):
        public = SimpleNamespace(
            configuration=object(), login_list_url=handoff.runtime.LOGIN_LIST_URL,
            provenance=handoff.runtime.SUPPORTED_PROVENANCE)
        server = object()
        for enabled, pc in ((False, False), (True, False), (False, True)):
            with self.subTest(enabled=enabled, pc=pc):
                session_factory = MagicMock()
                session = session_factory.return_value.__enter__.return_value
                response = session.get.return_value.__enter__.return_value
                response.status_code, response.url = 200, handoff.runtime.LOGIN_LIST_URL
                response.raw.read.return_value = b"synthetic-server-list"
                with patch.object(handoff.runtime, "load_runtime_manifest", return_value=public) as loaded, \
                        patch.object(handoff.runtime, "project_login_server", return_value=server) as project, \
                        patch.object(handoff.probe, "run_auth_once", return_value={"outcome": "fixture"}) as run, \
                        patch("requests.Session", session_factory):
                    handoff.attempt_auth(self.payload()["sauth"], with_reviewed_version=enabled,
                                        with_reviewed_pc_envelope=pc)
                run.assert_called_once_with(
                    self.payload()["sauth"], public.configuration, server,
                    client_version=public.provenance.client_version if enabled or pc else None,
                    os_name="pc" if pc else None)
                loaded.assert_called_once_with(
                    SCRIPT.parents[1] / "runtime-inputs" / "3825464.json",
                    expected_sha256=handoff.runtime.REVIEWED_MANIFEST_SHA256,
                    expected_provenance=handoff.runtime.SUPPORTED_PROVENANCE)
                self.assertFalse(session.trust_env)
                session.get.assert_called_once_with(
                    handoff.runtime.LOGIN_LIST_URL, timeout=10, allow_redirects=False, stream=True)
                response.raw.read.assert_called_once_with(4097, decode_content=True)
                project.assert_called_once_with(b"synthetic-server-list")

    def test_exact_origin_host_capability_and_loopback_required(self):
        for field, change in (("origin", "https://other.invalid"), ("host", "example.invalid:12345"),
                              ("path", "/wrong"), ("peer", "192.0.2.1")):
            value, attempt = self.handler()
            if field in ("origin", "host"):
                value.headers.replace_header(field, change)
            elif field == "path":
                value.path = change
            else:
                value.client_address = (change, 54321)
            value.do_POST()
            self.assertEqual(value.reply.call_args.args[0], 403)
            attempt.assert_not_called()

    def test_preflight_does_not_consume_or_authenticate(self):
        value, attempt = self.handler()
        value.headers["Access-Control-Request-Method"] = "POST"
        value.headers["Access-Control-Request-Headers"] = "content-type"
        value.do_OPTIONS()
        value.reply.assert_called_once_with(200, {"outcome": "handoff_ready"}, cors=True)
        self.assertFalse(value.server.completed)
        attempt.assert_not_called()

    def test_expiry_does_not_claim_rejected_payload_was_unread(self):
        payload = self.payload()
        payload["selected_uid"] = 123
        value, attempt = self.handler(payload)
        self.assertFalse(handoff.expiry_report(value.server)["credentials_read"])
        value.do_POST()
        self.assertEqual(value.reply.call_args.args[0], 400)
        self.assertTrue(value.server.payload_read_started)
        self.assertFalse(value.server.completed)
        self.assertIsNone(handoff.expiry_report(value.server)["credentials_read"])
        attempt.assert_not_called()

    def test_valid_payload_is_consumed_once_and_report_contains_no_bundle(self):
        value, attempt = self.handler()
        value.do_POST()
        self.assertTrue(value.server.completed)
        attempt.assert_called_once_with(self.payload()["sauth"])
        self.assertEqual(value.server.report, attempt.return_value)
        self.assertNotIn("fixture-secret", json.dumps(value.reply.call_args.args))
        value.do_POST()
        self.assertEqual(value.reply.call_args.args[0], 403)
        attempt.assert_called_once()

    def test_refresh_tokens_and_wrong_uid_never_leave_browser_handoff(self):
        for change in ("refresh", "uid", "uid_bool", "extra", "wrong_game"):
            payload = self.payload()
            if change == "refresh":
                payload["sauth"]["refresh_token"] = "fixture-refresh-secret"
            elif change in ("uid", "uid_bool"):
                payload["selected_uid"] = 123 if change == "uid" else True
            elif change == "extra":
                payload["extra"] = "fixture-secret"
            else:
                payload["sauth"]["gameid"] = "wrong-game"
            value, attempt = self.handler(payload)
            value.do_POST()
            self.assertEqual(value.reply.call_args.args[0], 400)
            self.assertNotIn("fixture-", json.dumps(value.reply.call_args.args))
            attempt.assert_not_called()

    def test_invalid_frames_are_bounded_and_rejected(self):
        for raw in (b"{", b"null", b"{\"selected_uid\":1370598422,\"selected_uid\":1370598422}",
                    b"x" * (handoff.MAX_HANDOFF_BYTES + 1)):
            value, attempt = self.handler(raw=raw)
            value.do_POST()
            self.assertEqual(value.reply.call_args.args[0], 400)
            attempt.assert_not_called()
        for header, item in (("Transfer-Encoding", "chunked"), ("Content-Length", "1")):
            value, attempt = self.handler()
            value.headers[header] = item
            value.do_POST()
            self.assertEqual(value.reply.call_args.args[0], 400)
            attempt.assert_not_called()

    def test_attempt_failure_consumes_slot_and_hides_exception(self):
        value, attempt = self.handler()
        attempt.side_effect = ValueError("fixture-secret")
        value.do_POST()
        self.assertTrue(value.server.completed)
        self.assertEqual(value.server.report, {"outcome": "auth_probe_failed", "secrets_saved": False})
        value.do_POST()
        attempt.assert_called_once()

    def test_invalid_bundle_rejected_before_runtime_or_network(self):
        with patch.object(handoff.runtime, "load_runtime_manifest") as loaded:
            with self.assertRaises(Exception):
                handoff.attempt_auth({})
            loaded.assert_not_called()


if __name__ == "__main__":
    unittest.main()
