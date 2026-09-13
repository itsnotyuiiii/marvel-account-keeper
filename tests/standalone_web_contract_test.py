from datetime import datetime, timezone
import json
import unittest

from standalone_web_contract import WebProfileError, project_profile_envelope


UID = 1370598422
NOW = datetime(2026, 9, 12, 20, tzinfo=timezone.utc)


def bundle():
    uid = str(UID)
    return {"uid": uid, "platform": "pc", "owner_authentication_verified": True, "game_login_verified": True,
        "social_session_accepted": True, "identity_verified": True,
        "identity": {"uid": uid, "in_game_name": "Synthetic ゆい", "platform": "pc", "level": 32, "is_owner": True,
                     "visibility": {"overview": {"state": "allowed"}, "history": {"state": "allowed"}}},
        "current_season_verified": True, "current_season": 20, "current_rank_source": "owner_panel",
        "current_rank": {"season": 20, "level": 14, "points": 4350, "points_basis": "server_rank_score", "points_verified": True},
        "career_verified": True, "ranks_verified": False, "matches_verified": True,
        "career": {"status": "available", "data": {"uid": uid, "request_scope": "unfiltered",
            "statistics": {"matches": 273, "wins": 149, "kills": 4165, "deaths": 1609, "assists": 3145}}},
        "ranks": {"status": "unavailable", "data": None},
        "matches": {"status": "available", "data": {"uid": uid, "page": 0, "page_size": 50, "has_more": None,
            "history_completeness": "first_page_only", "match_count": 1, "matches": [{"uid": uid, "match_uid": "synthetic_match",
                "played_at": 1767225600, "season": 19, "mode_id": 2, "duration_seconds": 818, "result": "win",
                "kills": 36, "deaths": 6, "assists": 7, "rank_change_verified": True,
                "rank_change": {"level_before": None, "level_after": 17, "points_before": 4639, "points_after": 4659,
                    "points_delta": 20, "placements_completed": None}}]}}}


class WebContractTest(unittest.TestCase):
    def test_exact_profile_projection_and_unknown_field_removal(self):
        source = bundle()
        source["refresh_token"] = "do-not-export"
        source["auth_rejection_code"] = 253
        source["identity"]["password"] = "do-not-export"
        source["career"]["data"]["statistics"]["raw"] = "do-not-export"
        result = project_profile_envelope(source, UID, NOW)
        self.assertNotIn("do-not-export", json.dumps(result))
        self.assertNotIn("auth_rejection_code", json.dumps(result))
        self.assertEqual(result["schemaVersion"], 2)
        self.assertEqual(result["nativeProfile"]["career"]["data"]["statistics"]["matches"], 273)
        self.assertEqual(result["nativeProfile"]["matches"]["data"]["matches"][0]["rank_change"]["points_delta"], 20)
        self.assertEqual(result["nativeProfile"]["ranks"]["status"], "unavailable")

    def test_independent_visibility_prevents_hidden_fields(self):
        source = bundle()
        source["identity"]["visibility"]["overview"]["state"] = "restricted"
        source["career"] = {"anything": "discard"}
        result = project_profile_envelope(source, UID, NOW)["nativeProfile"]
        self.assertIsNone(result["career"]["data"])
        self.assertIsNone(result["current_rank"])
        self.assertTrue(result["matches_verified"])
        source = bundle()
        source["identity"]["visibility"]["history"]["state"] = "unknown"
        self.assertIsNone(project_profile_envelope(source, UID, NOW)["nativeProfile"]["matches"]["data"])

    def test_owner_and_section_binding(self):
        for key in ("owner_authentication_verified", "game_login_verified", "social_session_accepted", "identity_verified"):
            source = bundle(); source[key] = False
            with self.assertRaises(WebProfileError):
                project_profile_envelope(source, UID, NOW)
        source = bundle(); source["matches"]["data"]["matches"][0]["uid"] = "123"
        with self.assertRaises(WebProfileError):
            project_profile_envelope(source, UID, NOW)
        for patch in ({"uid": "123"}, {"is_owner": False}, {"in_game_name": "\ud800"}):
            source = bundle(); source["identity"].update(patch)
            with self.assertRaises(WebProfileError):
                project_profile_envelope(source, UID, NOW)

    def test_numeric_scope_and_duplicate_match_guards(self):
        for patch in ({"played_at": 0}, {"played_at": 9999999999}, {"season": True}, {"kills": "12"},
                      {"duration_seconds": -1}):
            source = bundle(); source["matches"]["data"]["matches"][0].update(patch)
            with self.assertRaises(WebProfileError):
                project_profile_envelope(source, UID, NOW)
        source = bundle(); source["matches"]["data"]["matches"] *= 2; source["matches"]["data"]["match_count"] = 2
        with self.assertRaises(WebProfileError):
            project_profile_envelope(source, UID, NOW)
        for patch in ({"page": 1}, {"has_more": False}, {"page_size": 100}):
            source = bundle(); source["matches"]["data"].update(patch)
            with self.assertRaises(WebProfileError):
                project_profile_envelope(source, UID, NOW)

    def test_unverified_delta_is_omitted_not_invented(self):
        source = bundle(); source["matches"]["data"]["matches"][0]["rank_change_verified"] = False
        result = project_profile_envelope(source, UID, NOW)
        self.assertIsNone(result["nativeProfile"]["matches"]["data"]["matches"][0]["rank_change"])
        source = bundle(); source["matches"]["data"]["matches"][0]["rank_change"]["points_delta"] = 1
        with self.assertRaises(WebProfileError):
            project_profile_envelope(source, UID, NOW)

    def test_missing_optional_match_id_preserves_profile_and_basic_match(self):
        source = bundle()
        source["matches"]["data"]["matches"][0]["match_uid"] = None
        result = project_profile_envelope(source, UID, NOW)["nativeProfile"]
        self.assertTrue(result["career_verified"])
        self.assertTrue(result["matches_verified"])
        row = result["matches"]["data"]["matches"][0]
        self.assertEqual(row["kills"], 36)
        self.assertIsNone(row["match_uid"])
        self.assertFalse(row["rank_change_verified"])
        self.assertIsNone(row["rank_change"])

    def test_missing_season_retains_other_sections_and_no_fake_current(self):
        source = bundle(); source["current_season_verified"] = False
        result = project_profile_envelope(source, UID, NOW)["nativeProfile"]
        self.assertIsNone(result["current_rank"])
        self.assertIsNone(result["current_season"])
        self.assertTrue(result["career_verified"])

    def test_other_rank_row_keeps_raw_score_and_optional_zero_time_is_unknown(self):
        uid = 1822797559
        source = bundle()
        source["uid"] = source["identity"]["uid"] = str(uid)
        source["identity"]["is_owner"] = False
        source["career"]["data"]["uid"] = str(uid)
        source["matches"]["data"]["uid"] = str(uid)
        source["matches"]["data"]["matches"][0]["uid"] = str(uid)
        row = {"season": 20, "level": 23, "points": 5560.6176, "peak_level": 23,
               "peak_points": 5570.4144, "updated_at": 0, "record": None, "placements_completed": None}
        source["ranks_verified"] = True
        source["ranks"] = {"status": "available", "data": {"uid": str(uid), "platform": "pc",
            "points_basis": "server_rank_score", "season_count": 1, "seasons": [row]}}
        source["current_rank_source"] = "target_season_history"
        source["current_rank"] = {**row, "points_basis": "server_rank_score"}
        result = project_profile_envelope(source, uid, NOW)["nativeProfile"]
        self.assertEqual(result["current_rank"]["points"], 5560.6176)
        self.assertIsNone(result["ranks"]["data"]["seasons"][0]["updated_at"])
        source["current_rank"]["points"] = 5561
        with self.assertRaises(WebProfileError):
            project_profile_envelope(source, uid, NOW)


if __name__ == "__main__":
    unittest.main()
