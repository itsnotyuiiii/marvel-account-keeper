"""Own-account projection and app integration; all network and vault writes mocked."""
import base64
import json
import unittest
from unittest.mock import Mock, patch

import career_provider as provider
from career_socket_probe_test import fixture_session
from socialmatrix_capture_probe_test import packed
from stats_provider_test import tracker_app


def profile():
    return {"career": {"player_uid": 123, "total_match_count": 10, "total_win_count": 6,
                       "k": 36, "d": 6, "a": 7, "unrelated_secret": "never-export"}}


def summary():
    return {"match_info": [{"match_time_stamp": 1700000000, "match_season": "17", "game_mode_id": 2,
                            "match_play_duration": 817.7, "other_participant": {"player_uid": 999, "name": "never-export"},
                            "match_player": {"player_uid": 123, "is_win": 1, "k": 36, "d": 6, "a": 7}}]}


class CareerProviderTest(unittest.TestCase):
    def configured(self):
        session = fixture_session()
        session.request["post_body"] = base64.b64encode(packed({"player_uid": 123, "zone_id": 1, "device_type": "pc", "match_season": 19})).decode()
        p = provider.CapturedCareerProvider("fixture.etl", "fixture.uep", "123")
        p._session = session
        return p

    def test_projection_retains_only_own_stats(self):
        self.assertEqual(provider.project_profile(profile(), 123)["matches"], 10)
        matches = provider.project_matches(summary(), 123)
        self.assertEqual(matches[0]["duration_seconds"], 818)
        self.assertEqual(matches[0]["kills"], 36)
        self.assertEqual(matches[0]["result"], "win")
        self.assertNotIn("never-export", json.dumps(matches))
        self.assertNotIn("other_participant", json.dumps(matches))
        for fn, data in [(provider.project_profile, profile()), (provider.project_matches, summary())]:
            with self.assertRaises(ValueError):
                fn(data, 456)

    def test_unknown_or_unbounded_numbers_rejected(self):
        for v in [-1, True, "36", float("inf"), 10**13]:
            data = profile()
            data["career"]["k"] = v
            with self.assertRaises(ValueError):
                provider.project_profile(data, 123)
        data = summary()
        data["match_info"][0]["match_play_duration"] = float("nan")
        with self.assertRaises(ValueError):
            provider.project_matches(data, 123)

    def test_refresh_calls_two_reads_then_caches_without_secrets(self):
        p = self.configured()
        good = {"own_uid_matches": True}
        with patch.object(provider, "probe_socket", side_effect=[(good, profile()), (good, summary())]) as call:
            value = p.refresh()
            self.assertEqual(call.call_count, 2)
            self.assertEqual(call.call_args_list[1].kwargs, {"kind": "summary"})
            self.assertEqual(p.status()["snapshot"], value)
            with self.assertRaises(provider.CareerUnavailable) as error:
                p.refresh()
            self.assertEqual(error.exception.code, "career_cooldown")
        public = json.dumps(p.status())
        self.assertNotIn(p._session.setup["header"]["token"], public)
        self.assertNotIn("never-export", public)

    def test_failed_refresh_keeps_previous_snapshot(self):
        p = self.configured()
        p._snapshot = {"older": True}
        with patch.object(provider, "probe_socket", return_value=({"own_uid_matches": False, "stage": "session_setup"}, None)) as call:
            with self.assertRaises(provider.CareerUnavailable) as error:
                p.refresh()
            self.assertIn("session_setup", error.exception.message)
            call.assert_called_once()
        self.assertEqual(p._snapshot, {"older": True})

    def test_expired_and_busy_states(self):
        p = self.configured()
        p._lock.acquire()
        try:
            with self.assertRaises(provider.CareerUnavailable) as error:
                p.refresh()
            self.assertEqual(error.exception.code, "career_busy")
        finally:
            p._lock.release()
        p._session = fixture_session(exp=1)
        with self.assertRaises(provider.CareerUnavailable) as error:
            p.status()
        self.assertEqual(error.exception.code, "career_session_expired")
        self.assertIsNone(p._session)


class CareerRoutesTest(unittest.TestCase):
    def test_routes_require_unlocked_vault(self):
        fake = Mock()
        with tracker_app.app.test_request_context("/api/career/status"), \
                patch.object(tracker_app, "_require_key", return_value=(None, ("locked", 401))), \
                patch.object(tracker_app, "_career_provider", fake):
            self.assertEqual(tracker_app.career_status(), ("locked", 401))
            self.assertEqual(tracker_app.refresh_career(), ("locked", 401))
        fake.status.assert_not_called()
        fake.refresh.assert_not_called()

    def test_success_never_writes_vault(self):
        fake = Mock()
        fake.refresh.return_value = {"uid": "123", "profile": {"kills": 36}}
        with tracker_app.app.test_request_context("/api/career/refresh", method="POST"), \
                patch.object(tracker_app, "_require_key", return_value=(b"fixture", None)), \
                patch.object(tracker_app, "_career_provider", fake), \
                patch.object(tracker_app, "_write_vault") as write:
            result = tracker_app.refresh_career()
            self.assertEqual(result.get_json()["profile"]["kills"], 36)
            write.assert_not_called()

    def test_lock_during_read_suppresses_result(self):
        with tracker_app.app.test_request_context("/api/career/refresh", method="POST"), \
                patch.object(tracker_app, "_require_key", side_effect=[(b"fixture", None), (None, ("locked", 401))]), \
                patch.object(tracker_app, "_career_provider", Mock()):
            self.assertEqual(tracker_app.refresh_career(), ("locked", 401))

    def test_cooldown_and_not_configured_are_actionable(self):
        with tracker_app.app.test_request_context("/api/career/refresh", method="POST"), \
                patch.object(tracker_app, "_require_key", return_value=(b"fixture", None)), \
                patch.object(tracker_app, "_career_provider", None):
            self.assertEqual(tracker_app.career_status().get_json(), {"configured": False})
            self.assertEqual(tracker_app.refresh_career()[1], 409)
        fake = Mock()
        fake.refresh.side_effect = provider.CareerUnavailable("career_cooldown", "Wait 30 seconds.", 429)
        with tracker_app.app.test_request_context("/api/career/refresh", method="POST"), \
                patch.object(tracker_app, "_require_key", return_value=(b"fixture", None)), \
                patch.object(tracker_app, "_career_provider", fake):
            result = tracker_app.refresh_career()
            self.assertEqual(result.status_code, 429)
            self.assertEqual(result.headers["Retry-After"], "30")
