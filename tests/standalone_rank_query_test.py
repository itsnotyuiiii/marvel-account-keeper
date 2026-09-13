"""Synthetic rank JSON only; no captured templates, accounts, files or network."""
import base64
import copy
import gzip
import json
import math
import unittest
from unittest.mock import patch

import standalone_rank_query as query


def row(season=19, **changes):
    return {"rank_game_id": season, "level": 14, "rank_score": 4350.75,
            "max_level": 17, "max_rank_score": 4670.25, "update_time": 1700000000,
            "fixed_battle_count": 10, "battle_count": 27, "win_count": 21,
            "season_max_level": 23, "private": "PRIVATE_ROW", **changes}


def data(rows=None, **changes):
    rows = {"1001019": row(), "1001017": row(17)} if rows is None else rows
    return {"rank_game_season": json.dumps({key: json.dumps(value) for key, value in rows.items()}),
            "private": "PRIVATE_ROOT", **changes}


def wrapper(value=None, *, compressed=False, status=200, code=200, **changes):
    raw = json.dumps({"code": code, "data": data() if value is None else value}).encode()
    if compressed:
        raw = gzip.compress(raw, mtime=0)
    return {"service": "xyq-namecard", "status_code": status,
            "headers": {"Content-Encoding": "gzip"} if compressed else {},
            "response_body": base64.b64encode(raw).decode(), **changes}


def project(value=None, **kwargs):
    return query.project_other_rank_data(data() if value is None else value, 123, 42, **kwargs)


class StandaloneRankQueryTest(unittest.TestCase):
    def test_request_is_exact_normal_full_getdata_not_captured_field_selector(self):
        value = query.make_other_rank_request(123, 42, 456)
        encoded = value.pop("post_body")
        self.assertEqual(json.loads(base64.b64decode(encoded)), {"host": 42, "tag": "player", "id": "42_123"})
        self.assertEqual(value, {"service": "xyq-namecard", "headers": {
            "Content-Type": "application/json", "Accept-Encoding": "gzip"},
            "uri": "GetData", "method": "POST", "querystring": None,
            "host": None, "protocol": None, "version": None})
        self.assertNotIn("fields", base64.b64decode(encoded).decode())
        self.assertNotIn("season", base64.b64decode(encoded).decode())

    def test_builder_rejects_owner_and_noncanonical_integer_inputs(self):
        for target, zone, owner in ((123, 42, 123), (True, 42, 456), ("123", 42, 456),
                                    (0, 42, 456), (2**64, 42, 456), (123, 0, 456),
                                    (123, True, 456), (123, 42, "456"), (123, 42, -1)):
            with self.subTest(target=target, zone=zone), self.assertRaises(query.RankQueryError) as error:
                query.make_other_rank_request(target, zone, owner)
            self.assertEqual(error.exception.code, "rank_inputs_invalid")
        self.assertIn("post_body", query.make_other_rank_request(2**64 - 1, 42, 456))

    def test_numeric_seasons_and_per_season_peak_without_current_overall_peak_or_labels(self):
        value = project()
        self.assertTrue(value["ranks_verified"])
        self.assertEqual(value["season_count"], 2)
        self.assertEqual([item["season"] for item in value["seasons"]], [19, 17])
        self.assertEqual(value["seasons"][0], {"season": 19, "level": 14, "points": 4350.75,
            "peak_level": 17, "peak_points": 4670.25, "updated_at": 1700000000,
            "placements_completed": 10, "record": {"matches": 27, "wins": 21}})
        for key in ("current_season", "current", "peak"):
            self.assertIsNone(value[key])
        for key in ("current_season_verified", "overall_peak_verified", "rank_labels_verified"):
            self.assertFalse(value[key])
        self.assertEqual(value["points_basis"], "server_rank_score")
        self.assertEqual(value["history_completeness"], "not_established")
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertNotIn("season_max_level", json.dumps(value))
        self.assertNotIn("Diamond", json.dumps(value))

    def test_source_missing_rank_field_default_and_explicit_empty_map_are_distinct(self):
        missing = project({})
        explicit = project({"rank_game_season": "{}"})
        self.assertFalse(missing["rank_data_present"])
        self.assertTrue(explicit["rank_data_present"])
        for result in (missing, explicit):
            self.assertEqual(result["seasons"], [])
            self.assertEqual(result["season_count"], 0)
            self.assertIsNone(result["current"])
            self.assertIsNone(result["peak"])
            self.assertNotIn("unranked", json.dumps(result))
        for value in (None, {}, [], "", "null"):
            with self.assertRaises(query.RankQueryError):
                project({"rank_game_season": value})

    def test_projection_never_invents_response_identity_echo(self):
        for fields in ({}, {"aid": "123"}, {"aid": "42_123"}, {"id": "42_123", "host": 42, "tag": "player"},
                       {"aid": "OTHER_IDENTITY", "uid": "OTHER_IDENTITY", "id": "PRIVATE_ID"}):
            result = project(data(**fields))
            self.assertEqual(result["uid"], "123")
            self.assertEqual(result["identity_basis"], "caller_bound_request")
            self.assertFalse(result["response_identity_echo_verified"])
            self.assertNotIn("identity_verified", result)
            self.assertNotIn("request_bound", result)
            self.assertNotIn("OTHER_IDENTITY", json.dumps(result))
            self.assertNotIn("PRIVATE_ID", json.dumps(result))

    def test_platform_and_type_selection_never_cross_contaminates_history(self):
        source = data({"1001019": row(), "2001018": row(18, level=20),
                       "4001017": row(17, level=22), "1002016": row(16, level=23)})
        for platform, season in (("pc", 19), ("playstation", 18), ("xbox", 17)):
            result = project(source, platform=platform)
            self.assertEqual([item["season"] for item in result["seasons"]], [season])
        for platform in ("ps", "PC", None, True):
            with self.assertRaises(query.RankQueryError):
                project(source, platform=platform)

    def test_rank_keys_and_selected_row_season_identity_are_exact(self):
        for key, value in (("01001019", row()), ("1001019 ", row()), ("1e6", row()),
                           ("١٠٠١٠١٩", row()), ("1001000", row(0)),
                           ("1001019", row(18)), ("1001019", row(True)),
                           ("1001019", row("19")), ("1001019", row(19.5))):
            with self.subTest(key=key), self.assertRaises(query.RankQueryError) as error:
                project(data({key: value}))
            self.assertEqual(error.exception.code, "rank_season_identity_mismatch")
        value = row()
        del value["rank_game_id"]
        with self.assertRaises(query.RankQueryError):
            project(data({"1001019": value}))

    def test_integral_float_numeric_codes_are_normalized_without_truncating_fractions(self):
        result = project(data({"1001019": row(19.0, level=14.0, max_level=17.0)}))
        self.assertEqual(result["seasons"][0]["level"], 14)
        self.assertIs(type(result["seasons"][0]["level"]), int)
        self.assertEqual(result["shape"]["rank_game_id_type"], "float")
        self.assertEqual(result["shape"]["level_type"], "float")
        for field in ("level", "max_level"):
            for bad in (None, True, "14", -1, 65536, 14.5, 10**1000):
                with self.subTest(field=field), self.assertRaises(query.RankQueryError) as error:
                    project(data({"1001019": row(**{field: bad})}))
                self.assertEqual(error.exception.code, "rank_statistics_shape")

    def test_points_remain_finite_bounded_source_values_not_guessed_display_scores(self):
        for field in ("rank_score", "max_rank_score"):
            for bad in (None, True, "500", -1, 1_000_001, 10**1000):
                with self.subTest(field=field), self.assertRaises(query.RankQueryError):
                    project(data({"1001019": row(**{field: bad})}))
            for bad in (math.inf, math.nan):
                with self.assertRaises(query.RankQueryError):
                    project(data({"1001019": row(**{field: bad})}))
        result = project(data({"1001019": row(rank_score=0, max_level=0, max_rank_score=0)}))
        self.assertEqual(result["seasons"][0]["points"], 0)
        self.assertEqual(result["seasons"][0]["peak_level"], 0)

    def test_optional_records_and_timestamps_never_erase_valid_rank(self):
        for changes in ({"battle_count": None}, {"win_count": None}, {"battle_count": 20, "win_count": 21},
                        {"battle_count": True}, {"win_count": "2"}, {"battle_count": -1},
                        {"win_count": 1.5}, {"battle_count": 10**13}):
            result = project(data({"1001019": row(**changes)}))
            self.assertIsNone(result["seasons"][0]["record"])
            self.assertEqual(result["seasons"][0]["level"], 14)
        result = project(data({"1001019": row(battle_count=0, win_count=0,
                                               update_time="PRIVATE_TIME", fixed_battle_count=True)}))
        selected = result["seasons"][0]
        self.assertEqual(selected["record"], {"matches": 0, "wins": 0})
        self.assertIsNone(selected["updated_at"])
        self.assertIsNone(selected["placements_completed"])

    def test_both_json_layers_are_strict_and_duplicates_never_overwrite(self):
        bad = [None, [], {"rank_game_season": "[]"}, {"rank_game_season": "{bad"},
               {"rank_game_season": '{"1001019":"{}","1001019":"{}"}'},
               {"rank_game_season": json.dumps({"1001019": row()})},
               {"rank_game_season": json.dumps({"1001019": "[]"})},
               {"rank_game_season": json.dumps({"1001019": '{"level":14,"level":15}'})}]
        for source in bad:
            with self.assertRaises(query.RankQueryError):
                query.project_other_rank_data(source, 123, 42)

    def test_collection_text_and_recursive_shapes_are_bounded(self):
        with self.assertRaises(query.RankQueryError) as error:
            project(data({str(1001000 + i): row(i) for i in range(1, 102)}))
        self.assertEqual(error.exception.code, "rank_response_bound")
        self.assertEqual(error.exception.shape["collection_count"], 101)
        with patch.object(query, "MAX_JSON_BYTES", 128):
            with self.assertRaises(query.RankQueryError) as error:
                project({"rank_game_season": " " * 129})
            self.assertEqual(error.exception.code, "rank_response_bound")
        nested = []
        for _ in range(20):
            nested = [nested]
        with self.assertRaises(query.RankQueryError) as error:
            project(data({"1001019": row(private=nested)}))
        self.assertEqual(error.exception.code, "rank_response_bound")

    def test_failure_diagnostics_only_export_expected_field_types_and_counts(self):
        with self.assertRaises(query.RankQueryError) as error:
            project(data({"1001019": row(level="PRIVATE_LEVEL", rank_score=4350.5)}))
        self.assertEqual(error.exception.shape["level_type"], "str")
        self.assertEqual(error.exception.shape["rank_score_type"], "float")
        self.assertEqual(error.exception.shape["collection_count"], 1)
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertNotIn("PRIVATE", json.dumps(error.exception.shape))
        raw = query.RankQueryError("PRIVATE_CODE", shape={"private": "PRIVATE_VALUE", "row_type": "SECRET",
            "row_index": True, "level_type": "float", "collection_count": 1}, http_status="SECRET", application_code=True)
        self.assertEqual(str(raw), "rank_response_shape")
        self.assertEqual(raw.shape, {"level_type": "float", "collection_count": 1})
        self.assertIsNone(raw.http_status)
        self.assertIsNone(raw.application_code)

    def test_wrapper_decodes_plain_gzip_and_all_source_application_status_paths(self):
        for compressed in (False, True):
            result = query.project_other_rank_response(wrapper(compressed=compressed), 123, 42)
            self.assertEqual(result["season_count"], 2)
            self.assertEqual(result["http_status"], 200)
            self.assertEqual(result["application_status_source"], "code")
        for application, expected in (({"error_code": 0, "data": data()}, "error_code"),
                                       ({"data": data()}, "implicit_data"),
                                       ({"code": 200, "error_code": 5, "data": data()}, "code")):
            value = wrapper(response_body=base64.b64encode(json.dumps(application).encode()).decode())
            result = query.project_other_rank_response(value, 123, 42)
            self.assertEqual(result["application_status_source"], expected)

    def test_wrapper_errors_keep_fixed_rank_codes_and_numeric_status_only(self):
        for value, code, http, application in ((wrapper(service="xyq-career"), "rank_service_mismatch", None, None),
                (wrapper(status=403), "rank_http_rejected", 403, None),
                (wrapper(code=500), "rank_application_rejected", 200, 500),
                (wrapper(response_body="PRIVATE_BAD_ENCODING"), "rank_response_encoding", 200, None),
                (wrapper(response_body=base64.b64encode(b'{bad').decode()), "rank_response_json", 200, None)):
            with self.assertRaises(query.RankQueryError) as error:
                query.project_other_rank_response(value, 123, 42)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(error.exception.http_status, http)
            self.assertEqual(error.exception.application_code, application)
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertNotIn("PRIVATE", json.dumps(error.exception.shape))

    def test_inputs_fail_before_wrapper_decoder_and_projection_has_no_io(self):
        with patch.object(query, "decode_xyq_response") as decoder:
            for target, zone, platform in ((True, 42, "pc"), (123, 0, "pc"), (123, 42, None)):
                with self.assertRaises(query.RankQueryError):
                    query.project_other_rank_response({}, target, zone, platform)
            decoder.assert_not_called()
        with patch("builtins.open", side_effect=AssertionError("file I/O")), \
                patch("socket.create_connection", side_effect=AssertionError("network I/O")):
            query.make_other_rank_request(123, 42, 456)
            self.assertEqual(project()["season_count"], 2)
            self.assertEqual(query.project_other_rank_response(wrapper(), 123, 42)["season_count"], 2)

    def test_projection_preserves_input_and_never_aliases_original_rows(self):
        value = data()
        original = copy.deepcopy(value)
        result = project(value)
        self.assertEqual(value, original)
        result["seasons"][0]["record"]["matches"] = 1
        self.assertEqual(value, original)


if __name__ == "__main__":
    unittest.main()
