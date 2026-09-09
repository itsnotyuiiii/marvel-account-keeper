"""Synthetic/offline checks only; these do not validate the live NetEase API."""

import base64
import contextlib
import gzip
import io
import json
import ssl
import unittest
from unittest.mock import Mock, patch

import career_probe as p


GATEWAY = "synthetic-gateway.easebar.com"  # Mock destination; never contacted.


def token_for(**updates):
    claims = {"aid": "synthetic-account", "websocket": {"addr": GATEWAY, "tls": True}, "exp": 4102444800}
    claims.update(updates)
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return "eyJhbGciOiJIUzI1NiJ9." + encoded + ".c3ludGhldGlj"


class CareerProbeTest(unittest.TestCase):
    def query(self, kind="profile", **kwargs):
        return p.CareerQuery(kind, "123456789", "11001", **kwargs)

    def classify(self, payload, kind="profile", status=200, encoding=""):
        raw = json.dumps(payload).encode()
        if encoding == "gzip":
            raw = gzip.compress(raw)
        return p.classify_response(kind, status, raw, encoding)

    def test_profile_request_is_the_observed_minimal_pc_schema(self):
        self.assertEqual(self.query().body(), {"player_uid": 123456789, "zone_id": 11001, "device_type": "pc"})
        self.assertEqual(self.query(season=19).body()["match_season"], 19)
        self.assertEqual(self.query().path, "/xyq_service/xyq-career/query_my_player_career")
        self.assertNotIn("sort_hero", self.query().body())

    def test_summary_and_match_are_distinct_from_replay_lookup(self):
        self.assertEqual(self.query("summary").body(), {"player_uid": 123456789, "zone_id": 11001, "page": 0, "page_size": 50})
        self.assertTrue(self.query("summary").path.endswith("query_my_match_summary"))
        query = self.query("match", match_id="1_2_3_11001_5")
        self.assertEqual(query.body()["match_uids"], ["1_2_3_11001_5"])
        self.assertTrue(query.path.endswith("uid_query_my_match"))
        with self.assertRaises(ValueError):
            self.query("match", match_id="12345678901").body()

    def test_reject_invalid_or_silently_ignored_options(self):
        cases = [p.CareerQuery("profile", True, 11001), p.CareerQuery("profile", "１２３", 11001),
                 p.CareerQuery("profile", 123456789, 0), self.query(season=0), self.query(platform="steam"),
                 self.query("summary", season=19), self.query("summary", platform="ps"),
                 self.query(match_id="1_2_3_4_5"), self.query("other")]
        for query in cases:
            with self.subTest(query=query), self.assertRaises(ValueError):
                query.body()

    def test_token_routing_hints_must_match_explicit_gateway(self):
        p.validate_session(token_for(), GATEWAY, now=1000)
        p.validate_session(token_for(), GATEWAY.upper() + ":443", now=1000)
        with self.assertRaises(ValueError):
            p.validate_session(token_for(), "different.easebar.com", now=1000)

    def test_reject_non_tls_expired_not_yet_valid_or_malformed_claims(self):
        for updates in ({"aid": None}, {"websocket": []}, {"websocket": {"addr": GATEWAY, "tls": False}},
                        {"exp": 1000}, {"exp": True}, {"exp": float("nan")}, {"nbf": 1001}, {"exp": "later"}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                p.validate_session(token_for(**updates), GATEWAY, now=1000)
        for token in ("", "Bearer " + token_for(), token_for() + "\r\nInjected: x", "a.b.c"):
            with self.subTest(token_length=len(token)), self.assertRaises(ValueError):
                p.validate_session(token, GATEWAY)

    def test_gateway_rejects_other_destinations_and_url_injection(self):
        for host in ("https://" + GATEWAY, "evil.test", "easebar.com.evil.test", "localhost", "127.0.0.1",
                     GATEWAY + "/path", GATEWAY + "@evil.test", GATEWAY + ":0", GATEWAY + ":65536",
                     "bad..easebar.com", "bad_.easebar.com", "-bad.easebar.com", GATEWAY + "\n", None):
            with self.subTest(host=host), self.assertRaises(ValueError):
                p.gateway_authority(host)

    def test_transport_sends_one_json_post_with_unmodified_authorization(self):
        response = Mock(status=200)
        response.read.return_value = b'{"code":200,"data":{"career":{}}}'
        response.getheader.return_value = ""
        connection = Mock()
        connection.getresponse.return_value = response
        factory = Mock(return_value=connection)
        token = token_for()
        result = p.probe_career(self.query(), token, GATEWAY, connection_factory=factory)
        factory.assert_called_once()
        self.assertEqual(factory.call_args.args, (GATEWAY, 443))
        self.assertEqual(factory.call_args.kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(factory.call_args.kwargs["context"].check_hostname)
        connection.request.assert_called_once()
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ("POST", self.query().path))
        self.assertEqual(json.loads(kwargs["body"]), self.query().body())
        self.assertEqual(kwargs["headers"]["Authorization"], token)
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        response.read.assert_called_once_with(p.MAX_BYTES + 1)
        connection.close.assert_called_once()
        self.assertEqual(result.outcome, "json_received")
        self.assertFalse(result.summary()["identity_verified"])
        self.assertFalse(result.summary()["statistics_verified"])

    def test_invalid_input_never_opens_connection(self):
        factory = Mock()
        with self.assertRaises(ValueError):
            p.probe_career(self.query(), token_for(exp=1), GATEWAY, connection_factory=factory)
        factory.assert_not_called()

    def test_redirects_and_access_errors_are_not_retried_or_invalid_uid(self):
        for status, outcome in [(302, "redirect_refused"), (401, "access_blocked"), (403, "access_blocked"),
                                (429, "rate_limited"), (500, "http_error")]:
            response = Mock(status=status)
            response.getheader.return_value = ""
            connection = Mock()
            connection.getresponse.return_value = response
            result = p.probe_career(self.query(), token_for(), GATEWAY, connection_factory=Mock(return_value=connection))
            self.assertEqual(result.outcome, outcome)
            response.read.assert_not_called()
            connection.request.assert_called_once()
            connection.close.assert_called_once()

    def test_network_errors_do_not_echo_credentials(self):
        secret = token_for()
        connection = Mock()
        connection.request.side_effect = OSError(secret)
        result = p.probe_career(self.query(), secret, GATEWAY, connection_factory=Mock(return_value=connection))
        self.assertEqual(result.outcome, "network_error")
        self.assertNotIn(secret, repr(result))
        connection.close.assert_called_once()

    def test_cleanup_failure_does_not_replace_redacted_network_result(self):
        connection = Mock()
        connection.request.side_effect = OSError("synthetic-secret")
        connection.close.side_effect = OSError("synthetic-secret")
        result = p.probe_career(self.query(), token_for(), GATEWAY, connection_factory=Mock(return_value=connection))
        self.assertEqual(result.outcome, "network_error")
        self.assertNotIn("synthetic-secret", repr(result))

    def test_hidden_prompt_failure_never_sends_or_echoes_input(self):
        import getpass
        args = ["profile", "--own-uid", "123456789", "--zone-id", "11001", "--send", "--gateway", GATEWAY]
        error = io.StringIO()
        with patch.object(p.sys.stdin, "isatty", return_value=True), patch.object(p.getpass, "getpass", side_effect=getpass.GetPassWarning("synthetic-secret")), patch.object(p, "probe_career") as send, contextlib.redirect_stderr(error):
            self.assertEqual(p.main(args), 2)
        send.assert_not_called()
        self.assertNotIn("synthetic-secret", error.getvalue())
        self.assertIn("hidden token prompt", error.getvalue())

    def test_envelope_success_and_expected_fields_are_separate_checks(self):
        for envelope in ({"code": 200}, {"error_code": 0}, {"code": 200, "error_code": 0}):
            for kind, field in [("profile", "career"), ("summary", "match_info"), ("match", "matches")]:
                result = self.classify({**envelope, "data": {field: []}}, kind)
                self.assertEqual(result.outcome, "json_received")
                self.assertEqual(result.expected_fields, (field,))
        for envelope in ({"code": "200"}, {"error_code": False}, {"code": 200, "error_code": 1}):
            self.assertEqual(self.classify({**envelope, "data": {"career": {}}}).outcome, "application_error")
        self.assertEqual(self.classify({"data": {"career": {}}}).outcome, "unverified_envelope")
        self.assertEqual(self.classify({"code": 200, "data": []}).outcome, "unexpected_data_shape")
        self.assertEqual(self.classify({"code": 200, "data": {"unrelated": 1}}).outcome, "unexpected_data_shape")

    def test_gzip_decoded_once_without_sdk_base64_layer(self):
        self.assertEqual(self.classify({"code": 200, "data": {"career": {}}}, encoding="gzip").outcome, "json_received")
        raw = base64.b64encode(b'{"code":200,"data":{"career":{}}}')
        self.assertEqual(p.classify_response("profile", 200, raw).outcome, "invalid_response")

    def test_response_limits_and_malformed_encoding(self):
        for raw, encoding, expected in [(b"x" * (p.MAX_BYTES + 1), "", "response_too_large"),
                                        (gzip.compress(b"x" * (p.MAX_BYTES + 1)), "gzip", "response_too_large"),
                                        (b"garbage", "gzip", "invalid_response"),
                                        (gzip.compress(b"{}")[:-1], "gzip", "invalid_response"),
                                        (gzip.compress(b"{}") + b"tail", "gzip", "invalid_response"),
                                        (b"<html>", "", "invalid_response"),
                                        (b"[]", "", "invalid_response"), (b"{}", "br", "unsupported_encoding")]:
            self.assertEqual(p.classify_response("profile", 200, raw, encoding).outcome, expected)

    def test_summary_and_repr_never_dump_remote_data(self):
        secret = token_for()
        result = self.classify({"code": 200, "data": {"career": {"echo": secret}, secret: secret}})
        self.assertNotIn(secret, repr(result))
        self.assertNotIn(secret, json.dumps(result.summary()))
        self.assertEqual(result.data["career"]["echo"], secret)  # Only explicit in-memory access.

    def test_profile_uid_matches_only_the_requested_account(self):
        for uid in (123456789, "123456789"):
            result = self.classify({"code": 200, "data": {"career": {"player_uid": uid}}})
            evidence = p.profile_identity_evidence(self.query(), result)
            self.assertEqual(evidence["profile_uid_check"], "matches_requested_uid")
            self.assertTrue(evidence["usable_profile_identity"])
            self.assertFalse(result.summary()["identity_verified"])
            self.assertFalse(result.summary()["statistics_verified"])

    def test_different_uid_is_not_usable_or_echoed(self):
        result = self.classify({"code": 200, "data": {"career": {"player_uid": "987654321"}}})
        evidence = p.profile_identity_evidence(self.query(), result)
        self.assertEqual(evidence["profile_uid_check"], "different_uid")
        self.assertFalse(evidence["usable_profile_identity"])
        self.assertNotIn("987654321", json.dumps(evidence))

    def test_profile_uid_check_does_not_accept_missing_malformed_or_unrelated_ids(self):
        for profile in (None, [], {}, {"player_uid": True}, {"player_uid": 123456789.0},
                        {"player_uid": "１２３４５６７８９"}, {"player_uid": token_for()},
                        {"uid": "123456789"}):
            result = self.classify({"code": 200, "data": {"player_uid": 123456789, "career": profile}})
            evidence = p.profile_identity_evidence(self.query(), result)
            self.assertFalse(evidence["usable_profile_identity"])
            self.assertNotIn(token_for(), json.dumps(evidence))

    def test_profile_uid_check_needs_successful_profile_response(self):
        result = p.CareerProbeResult("application_error", 200, data={"career": {"player_uid": 123456789}})
        self.assertEqual(p.profile_identity_evidence(self.query(), result)["profile_uid_check"], "not_available")
        result.outcome = "json_received"
        self.assertFalse(p.profile_identity_evidence(self.query("summary"), result)["usable_profile_identity"])

    def test_profile_cli_rejects_different_uid_despite_http_success(self):
        result = self.classify({"code": 200, "data": {"career": {"player_uid": 987654321}}})
        out = io.StringIO()
        with patch.object(p.sys.stdin, "isatty", return_value=True), patch.object(p.getpass, "getpass", return_value=token_for()), patch.object(p, "probe_career", return_value=result), contextlib.redirect_stdout(out):
            status = p.main(["profile", "--own-uid", "123456789", "--zone-id", "11001", "--send", "--gateway", GATEWAY])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(out.getvalue())["profile_uid_check"], "different_uid")
        self.assertNotIn("987654321", out.getvalue())

    def test_cli_default_only_previews_without_prompting_or_network(self):
        out = io.StringIO()
        with patch.object(p, "probe_career") as send, patch.object(p.getpass, "getpass") as prompt, contextlib.redirect_stdout(out):
            self.assertEqual(p.main(["profile", "--own-uid", "123456789", "--zone-id", "11001"]), 0)
        send.assert_not_called()
        prompt.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())["mode"], "preview_only")

    def test_cli_send_requires_gateway_and_hidden_interactive_prompt(self):
        base = ["profile", "--own-uid", "123456789", "--zone-id", "11001", "--send"]
        with patch.object(p, "probe_career") as send, patch.object(p.sys.stdin, "isatty", return_value=False), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(p.main(base), 2)
            self.assertEqual(p.main(base + ["--gateway", GATEWAY]), 2)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
