import copy
import unittest

from native_rank_display import current_rank_display


def profile(*, count=0, owner=True, season=20, level=14, points=4350):
    row = {"season": season, "level": level, "points": points,
           "peak_level": level, "peak_points": points, "placements_completed": count}
    current = {"season": season, "level": level, "points": points,
               "points_basis": "server_rank_score"}
    if owner:
        current["points_verified"] = points is not None
    else:
        current.update(row)
    return {
        "uid": "1370598422", "platform": "pc", "identity_verified": True,
        "identity": {"uid": "1370598422", "platform": "pc", "is_owner": owner,
                     "visibility": {"overview": {"state": "allowed"}}},
        "current_season": season, "current_season_verified": True,
        "current_rank": current,
        "current_rank_source": "owner_panel" if owner else "target_season_history",
        "ranks_verified": True,
        "ranks": {"status": "available", "data": {
            "uid": "1370598422", "platform": "pc", "points_basis": "server_rank_score",
            "seasons": [row], "season_count": 1}},
    }


class NativeRankDisplayTests(unittest.TestCase):
    def test_zero_placements_is_known_estimate_not_missing(self):
        result = current_rank_display(profile())
        self.assertEqual(result["uid"], "1370598422")
        self.assertEqual(result["season"], 20)
        self.assertEqual(result["status"], "estimated")
        self.assertIs(result["estimated"], True)
        self.assertEqual(result["placements_label"], "Placements 0/10")
        self.assertEqual(result["label"], "Rank level 14")
        self.assertIsNone(result["rank_name"])
        self.assertFalse(result["rank_name_verified"])
        self.assertEqual(result["server_rank_score"], 4350)

    def test_nine_remains_estimate(self):
        result = current_rank_display(profile(count=9, owner=False))
        self.assertEqual(result["status"], "estimated")
        self.assertEqual(result["placements_completed"], 9)

    def test_ten_or_more_is_completed_without_inventing_forecast(self):
        for count in (10, 11, 400):
            with self.subTest(count=count):
                result = current_rank_display(profile(count=count))
                self.assertEqual(result["status"], "ranked")
                self.assertIs(result["estimated"], False)
                self.assertEqual(result["placements_completed"], 10)
                self.assertEqual(result["placements_reported"], count)
                self.assertEqual(result["placements_total"], 10)

    def test_no_current_season_never_uses_latest_history(self):
        data = profile(count=10)
        data["current_season_verified"] = False
        result = current_rank_display(data)
        self.assertIsNone(result["season"])
        self.assertIsNone(result["level"])
        self.assertIsNone(result["placements_completed"])
        self.assertIsNone(result["estimated"])

    def test_history_only_old_season_is_unknown(self):
        data = profile(count=10)
        data["ranks"]["data"]["seasons"][0]["season"] = 19
        result = current_rank_display(data)
        self.assertEqual(result["level"], 14)
        self.assertEqual(result["reason"], "current_season_row_missing")
        self.assertIsNone(result["estimated"])

    def test_mixed_history_uses_exact_current_season(self):
        data = profile(count=3)
        data["ranks"]["data"]["seasons"].append({
            "season": 99, "level": 23, "points": 5500, "placements_completed": 10})
        result = current_rank_display(data)
        self.assertEqual(result["placements_completed"], 3)
        self.assertEqual(result["level"], 14)

    def test_duplicate_current_season_is_unknown_even_when_identical(self):
        data = profile()
        data["ranks"]["data"]["seasons"] *= 2
        result = current_rank_display(data)
        self.assertEqual(result["reason"], "current_season_row_conflict")
        self.assertIsNone(result["placements_completed"])

    def test_conflicting_level_or_score_preserves_reported_rank_not_status(self):
        for key, value in (("level", 23), ("points", 5500)):
            data = profile()
            data["ranks"]["data"]["seasons"][0][key] = value
            result = current_rank_display(data)
            self.assertEqual(result["level"], 14)
            self.assertEqual(result["server_rank_score"], 4350)
            self.assertEqual(result["reason"], "current_rank_history_conflict")
            self.assertIsNone(result["estimated"])

    def test_missing_count_not_filled_from_record_or_peak(self):
        data = profile(count=None)
        row = data["ranks"]["data"]["seasons"][0]
        row.update(record={"matches": 273, "wins": 149}, peak_level=23)
        result = current_rank_display(data)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["placements_completed"])

    def test_invalid_counts_remain_unknown(self):
        for count in (None, True, False, -1, 1.5, "10", 10**12 + 1, {}, []):
            with self.subTest(count=count):
                self.assertIsNone(current_rank_display(profile(count=count))["estimated"])

    def test_source_count_conflict_is_unknown(self):
        data = profile(count=9, owner=False)
        data["current_rank"]["placements_completed"] = 10
        result = current_rank_display(data)
        self.assertEqual(result["reason"], "placement_count_conflict")
        self.assertIsNone(result["estimated"])

    def test_uid_mismatch_does_not_join_owner_or_other_player_history(self):
        data = profile()
        data["ranks"]["data"]["uid"] = "1822797559"
        self.assertIsNone(current_rank_display(data)["placements_completed"])
        data["identity"]["uid"] = "1822797559"
        self.assertIsNone(current_rank_display(data)["level"])

    def test_restricted_overview_hides_rank_and_progress(self):
        for permission in ("restricted", "unknown", None):
            data = profile()
            data["identity"]["visibility"]["overview"]["state"] = permission
            result = current_rank_display(data)
            self.assertFalse(result["available"])
            self.assertIsNone(result["level"])
            self.assertIsNone(result["placements_completed"])

    def test_unverified_history_hides_progress(self):
        data = profile()
        data["ranks_verified"] = False
        self.assertEqual(current_rank_display(data)["status"], "unknown")

    def test_unknown_or_wrong_current_season_hides_rank(self):
        for value in (None, False, "20", 19, 1000):
            data = profile()
            data["current_rank"]["season"] = value
            self.assertIsNone(current_rank_display(data)["level"])

    def test_invalid_current_score_or_level_never_leaks_nonfinite_json(self):
        for key, value in (("points", float("nan")), ("points", float("inf")),
                           ("points", True), ("points", -1), ("points", 10**1000), ("level", True),
                           ("level", "14"), ("level", 65536)):
            data = profile()
            data["current_rank"][key] = value
            result = current_rank_display(data)
            self.assertIsNone(result["level"])
            self.assertIsNone(result["server_rank_score"])

    def test_zero_or_unmapped_level_is_numeric_never_bronze_guess(self):
        for level in (0, 14, 23, 999):
            result = current_rank_display(profile(level=level))
            self.assertEqual(result["label"], f"Rank level {level}")
            self.assertIsNone(result["rank_name"])

    def test_owner_missing_score_can_join_consistent_season_level(self):
        data = profile()
        data["current_rank"].update(points=None, points_verified=False)
        result = current_rank_display(data)
        self.assertIsNone(result["server_rank_score"])
        self.assertEqual(result["placements_completed"], 0)

    def test_source_owner_mismatch_and_points_verification_fail_closed(self):
        data = profile()
        data["identity"]["is_owner"] = False
        self.assertFalse(current_rank_display(data)["available"])
        data = profile()
        data["current_rank"]["points_verified"] = False
        self.assertFalse(current_rank_display(data)["available"])

    def test_malformed_saved_profiles_are_safe(self):
        for value in (None, [], "", True, 1, {}, {"uid": []}, {"uid": "1370598422"}):
            with self.subTest(value=value):
                result = current_rank_display(value)
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["level"])
        for field in ("identity", "current_rank", "ranks"):
            data = profile()
            data[field] = []
            self.assertEqual(current_rank_display(data)["status"], "unknown")

    def test_does_not_mutate_or_alias_source(self):
        data = profile()
        before = copy.deepcopy(data)
        result = current_rank_display(data)
        self.assertEqual(data, before)
        result["level"] = 123
        self.assertEqual(data, before)


if __name__ == "__main__":
    unittest.main()
