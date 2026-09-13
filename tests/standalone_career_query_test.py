import base64
import gzip
import json
import unittest
from unittest.mock import patch

import standalone_career_query as career


def payload(**changes):
    row = {"player_uid": 123, "total_match_count": 100, "total_win_count": 60,
           "k": 500, "d": 200, "a": 300, "private_unmapped": "PRIVATE_DATA"}
    row.update(changes)
    return {"code": 200, "data": {"career": row}, "private_context": "PRIVATE_CONTEXT"}


def wrapped(data=None, *, compressed=False, raw=None, **changes):
    raw = json.dumps(payload() if data is None else data, separators=(",", ":")).encode() if raw is None else raw
    if compressed:
        raw = gzip.compress(raw, mtime=0)
    return {"service": "xyq-career", "status_code": 200,
            "headers": {"Content-Encoding": "gzip"} if compressed else {},
            "response_body": base64.b64encode(raw).decode(), **changes}


class StandaloneCareerQueryTest(unittest.TestCase):
    def test_request_matches_current_wrapper_and_uses_supplied_owner_zone(self):
        result = career.make_own_career_request(123, 42)
        self.assertEqual(result, {"service": "xyq-career", "uri": "query_my_player_career",
            "method": "POST", "headers": {"Content-Type": "application/json", "Accept-Encoding": "gzip"},
            "querystring": None, "host": None, "protocol": None, "version": None,
            "post_body": result["post_body"]})
        self.assertEqual(json.loads(base64.b64decode(result["post_body"])),
                         {"player_uid": 123, "zone_id": 42, "device_type": "pc"})
        for value in (True, "123", 0, -1, 1 << 64):
            with self.assertRaises(career.CareerQueryError):
                career.make_own_career_request(value, 42)

    def test_plain_and_header_selected_gzip_exact_owner_totals(self):
        for compressed in (False, True):
            result = career.project_own_career_response(wrapped(compressed=compressed), 123)
            self.assertTrue(result["career_verified"])
            self.assertEqual(result["uid"], "123")
            self.assertEqual(result["statistics"], {"matches": 100, "wins": 60, "kills": 500,
                                                    "deaths": 200, "assists": 300})
            self.assertEqual(result["response_field_count"], 7)
            self.assertEqual(result["history_completeness"], "not_established")
            self.assertFalse(result["matches_verified"])
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_application_precedence_matches_normal_callback(self):
        cases = [(payload(), "code"), ({"error_code": 0, "data": payload()["data"]}, "error_code"),
                 ({"data": payload()["data"]}, "implicit_data"),
                 (dict(payload(), error_code=9), "code")]
        for body, expected in cases:
            result = career.project_own_career_response(wrapped(body), 123)
            self.assertEqual(result["application_status_source"], expected)
        for body in (dict(payload(), code=500, error_code=0), dict(payload(), code=None),
                     {"error_code": False, "data": payload()["data"]},
                     {"error_code": 9, "data": payload()["data"]}):
            with self.assertRaises(career.CareerQueryError) as error:
                career.project_own_career_response(wrapped(body), 123)
            self.assertEqual(error.exception.code, "own_career_application_rejected")

    def test_service_http_and_target_validation_are_independent(self):
        for response, code in ((wrapped(service="xyq-other"), "own_career_service_mismatch"),
                               (wrapped(status_code=403), "own_career_http_rejected"),
                               (wrapped(status_code=True), "own_career_http_rejected"),
                               (wrapped(payload(player_uid=456)), "own_career_identity_mismatch"),
                               (wrapped(payload(player_uid="123")), "own_career_identity_mismatch")):
            with self.assertRaises(career.CareerQueryError) as error:
                career.project_own_career_response(response, 123)
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_bad_or_inconsistent_counts_are_not_zero_or_verified(self):
        for changes in ({"k": None}, {"a": True}, {"d": -1}, {"total_win_count": 101},
                        {"total_match_count": 1e2}, {"total_win_count": 10**12 + 1}):
            with self.assertRaises(career.CareerQueryError) as error:
                career.project_own_career_response(wrapped(payload(**changes)), 123)
            self.assertEqual(error.exception.code, "own_career_statistics_shape")

    def test_base64_json_and_gzip_are_bounded_without_magic_sniffing(self):
        bad = [wrapped(response_body="invalid!"), wrapped(raw=b"{"),
               wrapped(raw=b'{"code":200,"code":200}'), wrapped(raw=b'NaN'),
               wrapped(compressed=True, headers={}),
               wrapped(raw=b"not gzip", headers={"Content-Encoding": "gzip"}),
               wrapped(raw=gzip.compress(b"{}") + b"trailing", headers={"Content-Encoding": "gzip"})]
        for response in bad:
            with self.assertRaises(career.CareerQueryError):
                career.project_own_career_response(response, 123)
        with patch.object(career, "MAX_RESPONSE_BYTES", 256):
            with self.assertRaises(career.CareerQueryError) as error:
                career.project_own_career_response(wrapped(raw=b"x" * 257, compressed=True), 123)
            self.assertEqual(error.exception.code, "own_career_response_bound")


if __name__ == "__main__":
    unittest.main()
