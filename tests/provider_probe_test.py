"""Offline regression tests; real player payloads and cookies are not fixtures."""

import io
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import provider_probe as probe


class Response(io.BytesIO):
    status = 200


class ProviderProbeTest(unittest.TestCase):
    def classify(self, payload, status=200):
        return probe._classify("netease_esports", "replay_code", "https://example.test/",
                               status, json.dumps(payload).encode())

    def test_success_requires_match_records_not_just_http_200(self):
        result = self.classify({"code": 200, "success": True, "data": {"matches": [
            {"match_uid": "synthetic", "match_players": [{"uid": "123456"}]}
        ]}})
        self.assertEqual(result.outcome, "matches_received")
        self.assertEqual(result.match_count, 1)
        self.assertNotIn("123456", repr(result))

    def test_null_and_empty_matches_are_not_invalid_player(self):
        for matches in (None, []):
            with self.subTest(matches=matches):
                result = self.classify({"code": 200, "success": True, "data": {"matches": matches}})
                self.assertEqual(result.outcome, "no_matches")
                self.assertEqual(result.match_count, 0)
                self.assertIn("does not validate a player UID", result.message)

    def test_application_error_is_not_empty_history(self):
        result = self.classify({"code": -8, "success": False, "data": {"matches": None}})
        self.assertEqual(result.outcome, "application_error")
        self.assertEqual(result.application_code, -8)

    def test_bad_envelopes_and_match_shapes(self):
        for payload in ([], {}, {"code": 200, "success": True, "data": {}},
                        {"code": 200, "success": True, "data": {"matches": {}}},
                        {"code": 200, "success": True, "data": {"matches": [None]}},
                        {"code": 200, "success": True, "data": {"matches": [{"match_uid": "m"}]}}):
            with self.subTest(payload=payload):
                self.assertIn(self.classify(payload).outcome, {"application_error", "invalid_response"})

    def test_http_classification_precedes_json_parsing(self):
        for status, expected in ((401, "access_blocked"), (403, "access_blocked"),
                                 (429, "rate_limited"), (404, "http_error"), (502, "http_error")):
            with self.subTest(status=status):
                self.assertEqual(probe._classify("tracker", "in_game_name", "url", status, b"<html>").outcome, expected)

    def test_html_200_and_size_limit(self):
        self.assertEqual(probe._classify("tracker", "in_game_name", "url", 200, b"<html>").outcome, "invalid_response")
        self.assertEqual(probe._classify("tracker", "in_game_name", "url", 200, b" " * (probe.MAX_RESPONSE_BYTES + 1)).outcome, "response_too_large")

    def test_json_received_does_not_claim_valid_player(self):
        result = probe._classify("rivalsdata", "player_uid", "url", 200, b'{"error":"unknown"}')
        self.assertEqual(result.outcome, "json_received")
        self.assertIn("not validated", result.message)

    def test_route_construction_and_no_credentials(self):
        opener = Mock(return_value=Response(b'{"code":200,"success":true,"data":{"matches":null}}'))
        result = probe.probe_replay("12345678901", zone_id="11001", china_fallback=True, opener=opener)
        request = opener.call_args.args[0]
        self.assertIn("x20mwss202505_zh_get_history_by_replay_id/?zone_id=11001&replay_id=12345678901", request.full_url)
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.get_header("Cookie"))
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(result.outcome, "no_matches")
        opener.assert_called_once()

    def test_match_ids_and_player_ids_use_different_routes(self):
        opener = Mock(side_effect=lambda *a, **k: Response(b'{}'))
        probe.probe_match(probe.CONTROL_MATCH_ID, opener=opener)
        self.assertIn("?match_uids=" + probe.CONTROL_MATCH_ID, opener.call_args.args[0].full_url)
        probe.probe_rivalsdata("123456789", opener=opener)
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.rivalsdata.com/player")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"uid": 123456789})

    def test_tracker_name_is_encoded(self):
        opener = Mock(return_value=Response(b'{}'))
        probe.probe_tracker("A/B ?", opener=opener)
        self.assertTrue(opener.call_args.args[0].full_url.endswith("A%2FB%20%3F"))

    def test_bad_identifiers_make_no_network_request(self):
        opener = Mock()
        for call, value in ((probe.probe_replay, "123&zone_id=1"), (probe.probe_match, "123456789"),
                            (probe.probe_rivalsdata, "１２３４５６"), (probe.probe_tracker, "\n")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                call(value, opener=opener)
        opener.assert_not_called()

    def test_transport_errors_do_not_leak_details_or_retry(self):
        for exc, outcome in ((URLError("sensitive diagnostic detail"), "network_error"),
                             (TimeoutError("secret"), "network_error"),
                             (HTTPError("url", 403, "secret", {}, io.BytesIO(b"secret")), "access_blocked")):
            with self.subTest(outcome=outcome):
                opener = Mock(side_effect=exc)
                result = probe.probe_tracker("Synthetic", opener=opener)
                self.assertEqual(result.outcome, outcome)
                self.assertNotIn("secret", repr(result))
                opener.assert_called_once()


if __name__ == "__main__":
    unittest.main()
