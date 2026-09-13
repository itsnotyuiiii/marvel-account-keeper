"""Synthetic match rows only; no game, files, credentials or network."""
import copy
import json
import math
import unittest

import standalone_match_query as query


def match(**changes):
    row = {"match_uid": "match-example-01", "match_time_stamp": 1700000000,
           "match_season": "19", "game_mode_id": 2, "match_play_duration": 817.7,
           "match_player": {"player_uid": 123, "is_win": 1, "k": 36, "d": 6, "a": 7},
           "private_raw": "PRIVATE_ROW", "other_participant": {"player_uid": 456}}
    row.update(changes)
    return row


def ranked(**changes):
    row = match()
    row["match_player"]["dynamic_fields"] = {
        "new_level": 14, "level": 13, "new_score": 4200.1, "add_score": 0.2,
        "rankscore_info": {"fixed_battle_count": 10, "private": "PRIVATE_RANK"},
        "unknown": "PRIVATE_FIELD", **changes}
    return row


def project(*rows):
    return query.project_match_summary_response({"match_info": list(rows)}, 123)


class StandaloneMatchQueryTest(unittest.TestCase):
    def test_basic_projection_uses_exact_server_match_id_and_drops_unknown_fields(self):
        result = project(match())
        row = result["matches"][0]
        self.assertEqual({key: row[key] for key in
                          ("match_uid", "played_at", "season", "mode_id", "duration_seconds",
                           "result", "kills", "deaths", "assists")},
                         {"match_uid": "match-example-01", "played_at": 1700000000,
                          "season": 19, "mode_id": 2, "duration_seconds": 818,
                          "result": "win", "kills": 36, "deaths": 6, "assists": 7})
        self.assertTrue(row["match_uid_available"])
        self.assertFalse(row["rank_change_verified"])
        self.assertIsNone(row["rank_change"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("other_participant", json.dumps(result))
        self.assertEqual(result["history_completeness"], "first_page_only")
        self.assertIsNone(result["has_more"])
        self.assertFalse(result["ranks_verified"])

    def test_empty_page_is_valid_but_not_proof_of_complete_history_or_rank_data(self):
        result = project()
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["match_count"], 0)
        self.assertEqual(result["rank_change_count"], 0)
        self.assertFalse(result["rank_changes_verified"])
        self.assertIsNone(result["has_more"])

    def test_input_and_page_bounds_are_exact(self):
        for uid in (True, "123", 0, -1, 2**63):
            with self.subTest(uid=uid), self.assertRaises(query.MatchQueryError) as error:
                query.project_match_summary_response({"match_info": []}, uid)
            self.assertEqual(error.exception.code, "match_inputs_invalid")
        for data in (None, [], {}, {"match_info": None}, {"match_info": {}},
                     {"match_info": [match()] * 51}):
            with self.assertRaises(query.MatchQueryError) as error:
                query.project_match_summary_response(data, 123)
            self.assertEqual(error.exception.code, "match_response_shape")
        self.assertEqual(project(*[match(match_uid=f"match-{i}") for i in range(50)])["match_count"], 50)

    def test_every_row_needs_exact_integer_target_even_after_one_valid_row(self):
        for uid in (456, "123", True, None):
            row = match()
            row["match_player"]["player_uid"] = uid
            with self.assertRaises(query.MatchQueryError) as error:
                project(match(), row)
            self.assertEqual(error.exception.code, "match_identity_mismatch")
        for row in (None, [], match(match_player=None)):
            with self.assertRaises(query.MatchQueryError):
                project(row)

    def test_missing_or_bad_required_counts_are_never_zero(self):
        for key in ("k", "d", "a"):
            for value in (None, True, "1", -1, 1.5, math.inf, 10**12 + 1):
                row = match()
                row["match_player"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(query.MatchQueryError) as error:
                    project(row)
                self.assertEqual(error.exception.code, "match_statistics_shape")
            row = match()
            del row["match_player"][key]
            with self.assertRaises(query.MatchQueryError):
                project(row)
        row = match()
        row["match_player"].update(k=0, d=0, a=0)
        self.assertEqual(project(row)["matches"][0]["kills"], 0)

    def test_time_season_mode_and_duration_validation(self):
        for key, values in {
            "match_time_stamp": [None, True, "123", -1],
            "game_mode_id": [None, True, -1],
            "match_season": [None, True, -1, 1.5, "١٩", "10000"],
            "match_play_duration": [None, True, "1", -1, 86401, math.nan, math.inf, 10**1000],
        }.items():
            for value in values:
                with self.subTest(key=key), self.assertRaises(query.MatchQueryError):
                    project(match(**{key: value}))
        self.assertEqual(project(match(match_season=19))["matches"][0]["season"], 19)

    def test_result_requires_integer_win_flag_other_values_remain_unknown(self):
        for value, expected in ((0, "loss"), (1, "win"), (True, "unknown"), (False, "unknown"),
                                (None, "unknown"), ("1", "unknown"), (2, "unknown")):
            row = match()
            row["match_player"]["is_win"] = value
            self.assertEqual(project(row)["matches"][0]["result"], expected)

    def test_invalid_optional_match_id_does_not_replace_it_with_a_synthetic_id(self):
        for value in (None, 123, True, "", " leading", "bad\nID", "bad\x7fID", "x" * 129, "\ud800"):
            row = project(match(match_uid=value))["matches"][0]
            self.assertIsNone(row["match_uid"])
            self.assertFalse(row["match_uid_available"])
            self.assertEqual(row["kills"], 36)

    def test_rank_delta_follows_two_floors_not_floor_of_delta(self):
        result = project(ranked())
        change = result["matches"][0]["rank_change"]
        self.assertEqual(change, {"level_before": 13, "level_after": 14,
                                 "points_before": 4199, "points_after": 4200,
                                 "points_delta": 1, "placements_completed": 10})
        self.assertNotEqual(change["points_delta"], math.floor(0.2))
        self.assertEqual(result["rank_change_count"], 1)
        self.assertTrue(result["rank_changes_verified"])
        self.assertFalse(result["ranks_verified"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(project(ranked(new_score=100.1, add_score=-0.8))["matches"][0]
                         ["rank_change"]["points_delta"], 0)

    def test_only_ranked_mode_and_selected_players_dynamic_fields_supply_delta(self):
        row = ranked()
        row["game_mode_id"] = 1
        projected = project(row)["matches"][0]
        self.assertIsNone(projected["rank_change"])
        self.assertEqual(projected["rank_change_state"], "not_ranked")
        row = match(dynamic_fields=ranked()["match_player"]["dynamic_fields"])
        self.assertIsNone(project(row)["matches"][0]["rank_change"])

    def test_invalid_optional_rank_data_never_discards_valid_match_statistics(self):
        invalid = [{"new_level": value} for value in
                   (None, True, "invalid", "14.0", "1" * 33, 0, -1, 65536,
                    math.nan, math.inf, 0.9, 10**1000)]
        invalid += [{key: value} for key in ("new_score", "add_score")
                    for value in (None, True, "1", math.nan, math.inf, 10**1000)]
        invalid += [{"new_score": -1}, {"new_score": 10, "add_score": 11}]
        for changes in invalid:
            with self.subTest(changes=changes):
                result = project(ranked(**changes))
                row = result["matches"][0]
                self.assertTrue(result["matches_verified"])
                self.assertEqual(row["kills"], 36)
                self.assertIsNone(row["rank_change"])
                self.assertFalse(row["rank_change_verified"])
        for fields in (None, [], "{\"new_level\":14}"):
            row = match()
            row["match_player"]["dynamic_fields"] = fields
            self.assertIsNone(project(row)["matches"][0]["rank_change"])

    def test_missing_rank_fields_never_become_zero(self):
        for key in ("new_level", "new_score", "add_score"):
            row = ranked()
            del row["match_player"]["dynamic_fields"][key]
            self.assertIsNone(project(row)["matches"][0]["rank_change"])
        change = project(ranked(level=None, rankscore_info=None))["matches"][0]["rank_change"]
        self.assertIsNone(change["level_before"])
        self.assertIsNone(change["placements_completed"])
        self.assertEqual(change["points_delta"], 1)
        change = project(ranked(rankscore_info={"fixed_battle_count": False}))["matches"][0]["rank_change"]
        self.assertIsNone(change["placements_completed"])

    def test_optional_rank_verification_is_per_row_not_entire_history(self):
        result = project(ranked(), match(match_uid="match-02"), ranked(new_level=0))
        self.assertEqual(result["match_count"], 3)
        self.assertEqual(result["rank_change_count"], 1)
        self.assertEqual([row["rank_change_verified"] for row in result["matches"]], [True, False, False])

    def test_new_level_uses_bounded_source_int_conversion_without_coercing_old_level(self):
        for value in ("14", " 14 ", "+14", 14.0, 14.9):
            with self.subTest(value=value):
                change = project(ranked(new_level=value, level="13"))["matches"][0]["rank_change"]
                self.assertEqual(change["level_after"], 14)
                self.assertEqual(change["points_delta"], 1)
                self.assertIsNone(change["level_before"])
        self.assertEqual(project(ranked(new_level=65535.9))["matches"][0]["rank_change"]["level_after"], 65535)

    def test_rank_shape_counts_are_fixed_raw_kinds_before_conversion_ranked_rows_only(self):
        numeric = ranked(new_level="14")
        fractional = ranked(new_level=14.9, rankscore_info=None)
        malformed = match()
        malformed["match_player"]["dynamic_fields"] = "PRIVATE_RAW_JSON"
        missing = match()
        partial = ranked(new_level=None, new_score="PRIVATE_VALUE", add_score=math.nan)
        del partial["match_player"]["dynamic_fields"]["rankscore_info"]
        nonranked = ranked()
        nonranked["game_mode_id"] = 1
        result = project(numeric, fractional, malformed, missing, partial, nonranked)
        self.assertEqual(result["rank_shape_counts"], {
            "dynamic_fields": {"dict": 3, "str": 1, "missing": 1},
            "new_level": {"str": 1, "float": 1, "unavailable": 2, "null": 1},
            "new_score": {"float": 2, "unavailable": 2, "str": 1},
            "add_score": {"float": 3, "unavailable": 2},
            "rankscore_info": {"dict": 1, "null": 1, "unavailable": 2, "missing": 1},
        })
        self.assertEqual(result["rank_change_count"], 2)
        self.assertEqual(result["match_count"], 6)
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("unknown", json.dumps(result["rank_shape_counts"]))
        self.assertEqual(project()["rank_shape_counts"], {key: {} for key in query.RANK_SHAPE_FIELDS})

    def test_input_is_not_mutated_and_output_does_not_alias_raw_dictionaries(self):
        data = {"match_info": [ranked()]}
        before = copy.deepcopy(data)
        result = query.project_match_summary_response(data, 123)
        self.assertEqual(data, before)
        result["matches"][0]["rank_change"]["points_after"] = 5
        self.assertEqual(data, before)

    def test_exception_contains_only_fixed_code(self):
        self.assertEqual(str(query.MatchQueryError("PRIVATE_ERROR")), "match_response_shape")


if __name__ == "__main__":
    unittest.main()
