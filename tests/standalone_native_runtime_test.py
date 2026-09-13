from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

import portable_game_runtime as game
from standalone_native_runtime import NativeProfileQuery, NativeRuntimeError
from tests.web_sdk_auth_probe_test import fixture


class NativeRuntimeTest(unittest.TestCase):
    def runtime(self, *, status=200, url=game.LOGIN_LIST_URL, raw=None):
        response = Mock()
        response.status_code, response.url = status, url
        response.raw.read.return_value = raw if raw is not None else b"prod 8.8.8.8 10001 13001 1 1700000000 10001-10003"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        http = Mock()
        http.get.return_value = response
        http.__enter__ = Mock(return_value=http)
        http.__exit__ = Mock(return_value=None)
        query = Mock(return_value={"owner_authentication_verified": True, "uid": "1822797559"})
        return NativeProfileQuery(http_factory=lambda: http, query=query), http, response, query

    def test_only_public_list_and_pinned_inputs_are_used(self):
        runtime, http, response, query = self.runtime()
        result = runtime(fixture(), 1822797559)
        self.assertTrue(result.owner_authenticated)
        self.assertEqual(result.owner_uid, 1370598422)
        self.assertFalse(http.trust_env)
        http.get.assert_called_once_with(game.LOGIN_LIST_URL, timeout=10, allow_redirects=False, stream=True)
        response.raw.read.assert_called_once_with(4097, decode_content=True)
        self.assertEqual(query.call_args.kwargs["client_version"], "3848703")
        self.assertEqual(query.call_args.kwargs["target_uid"], 1822797559)
        self.assertEqual(len(query.call_args.kwargs["parameter"]), 17)

    def test_invalid_inputs_prevent_any_http(self):
        for uid in (True, 0, "1822797559", 2**63):
            runtime, http, _, query = self.runtime()
            with self.assertRaises(NativeRuntimeError):
                runtime(fixture(), uid)
            http.get.assert_not_called()
            query.assert_not_called()
        runtime, http, _, _ = self.runtime()
        with self.assertRaises(NativeRuntimeError):
            runtime({}, 1822797559)
        http.get.assert_not_called()

    def test_server_list_errors_do_not_call_native_or_export_messages(self):
        for options in ({"status": 302}, {"url": "https://elsewhere.example"}, {"raw": b"private diagnostics"}):
            runtime, _, _, query = self.runtime(**options)
            with self.assertRaisesRegex(NativeRuntimeError, "native_server_list_unavailable"):
                runtime(fixture(), 1822797559)
            query.assert_not_called()

    def test_later_profile_failure_preserves_exact_owner_authentication(self):
        runtime, _, _, query = self.runtime()
        query.return_value = {"owner_authentication_verified": True, "identity_verified": False, "uid": "1822797559"}
        self.assertTrue(runtime(fixture(), 1822797559).owner_authenticated)
        query.return_value = {"owner_authentication_verified": 1}
        self.assertFalse(runtime(fixture(), 1822797559).owner_authenticated)

    def test_only_typed_correlated_bundle_rejection_reaches_manager(self):
        runtime, _, _, query = self.runtime()
        rejected = {"owner_authentication_verified": False, "game_login_verified": False,
                    "auth_rejection_code": 253}
        for code in (253, 1911, -1):
            query.return_value = {**rejected, "auth_rejection_code": code}
            self.assertEqual(runtime(fixture(), 1822797559).auth_rejection_code, code)
        for changes in ({"auth_rejection_code": True}, {"auth_rejection_code": "253"},
                        {"auth_rejection_code": 0}, {"auth_rejection_code": 2**31},
                        {"auth_rejection_code": None}, {"owner_authentication_verified": True},
                        {"owner_authentication_verified": 0}, {"game_login_verified": True},
                        {"game_login_verified": None}):
            query.return_value = {**rejected, **changes}
            self.assertIsNone(runtime(fixture(), 1822797559).auth_rejection_code)

    def test_cli_check_needs_no_credentials_or_network(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-B", str(root / "scripts/serve-native-api.py"), "--check"],
                                cwd=root, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"credentials_read": false', result.stdout)
        self.assertIn('"network_attempted": false', result.stdout)


if __name__ == "__main__":
    unittest.main()
