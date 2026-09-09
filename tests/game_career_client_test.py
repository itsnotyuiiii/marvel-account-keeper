"""Shared Career client checks use synthetic login, sockets and player data."""
from contextlib import ExitStack
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import game_career_client as client


OWN = 1370598422
TARGET = 1822797559
OTHER = 1822797560
EXPECTED = {"matches": 10, "wins": 6, "kills": 30, "deaths": 12, "assists": 20}


def profile(uid):
    return {"career": {"player_uid": uid, "total_match_count": 10, "total_win_count": 6,
                       "k": 30, "d": 12, "a": 20, "extra": "fixture-never-export"},
            "token": "fixture-never-export"}


class Connection:
    def __init__(self):
        self.closed = False
        self.shutdown_called = False

    def shutdown(self, _how):
        self.shutdown_called = True

    def close(self):
        self.closed = True


class GameCareerClientTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.value = client.GameCareerClient(own_uid=OWN, archive=Path("fixture-script.uep"))
        self.value.IDLE_SECONDS = 0.15
        self.value.REQUEST_WAIT_SECONDS = 2.0
        self.addCleanup(self.value.close)
        self.config = self.stack.enter_context(patch.object(client, "load_public_configuration", return_value=object()))
        self.methods = self.stack.enter_context(patch.object(client.career, "load_career_methods", return_value=object()))
        self.inputs = self.stack.enter_context(patch.object(client.login, "load_own_login_inputs", return_value=object()))
        self.server = self.stack.enter_context(patch.object(client.login, "advertised_server", return_value=object()))
        self.connections = []
        self.check = self.stack.enter_context(patch.object(client.login, "check_login", side_effect=self.login))
        self.read = self.stack.enter_context(patch.object(client.career, "read_one_career", side_effect=self.read_career))

    def login(self, configuration, server, **kwargs):
        self.assertEqual(kwargs["expected_uid"], OWN)
        self.assertTrue(kwargs["enter"])
        connection = Connection()
        self.connections.append(connection)
        try:
            result = kwargs["after_login"](connection, object(), {"zone_id": 11001})
            return {"game_login_verified": True, "career": result}
        finally:
            connection.close()

    def read_career(self, connection, transport, zone, methods, uid, season, **kwargs):
        self.assertEqual(kwargs["own_uid"], OWN)
        kwargs["on_verified"](profile(uid))
        return {"career_verified": True, "unrelated": "fixture-never-export"}

    def test_two_other_players_share_one_login_and_only_projected_data_leaves(self):
        first = self.value.read_profile(TARGET, 19)
        first["kills"] = 999
        second = self.value.read_profile(OTHER, 19)
        self.assertEqual(second, EXPECTED)
        self.assertEqual(self.check.call_count, 1)
        self.assertEqual(self.inputs.call_count, 1)
        self.assertEqual([call.args[4] for call in self.read.call_args_list], [TARGET, OTHER])
        self.assertIs(self.read.call_args_list[0].args[0], self.read.call_args_list[1].args[0])
        self.assertEqual(self.config.call_args.kwargs, {"archive": Path("fixture-script.uep")})
        self.assertNotIn("fixture-never-export", json.dumps(second))
        self.assertNotIn("fixture-script", json.dumps(self.value.status()))
        self.assertTrue(self.value.status()["connected"])

    def test_simultaneous_callers_are_queued_on_one_serial_gateway(self):
        entered, release = threading.Event(), threading.Event()
        results, errors = {}, []
        order = []
        def delayed(connection, transport, zone, methods, uid, season, **kwargs):
            order.append(uid)
            if uid == TARGET:
                entered.set()
                release.wait(1.0)
            kwargs["on_verified"](profile(uid))
            return {"career_verified": True}
        self.read.side_effect = delayed
        def caller(uid):
            try:
                results[uid] = self.value.read_profile(uid, 19)
            except client.GameCareerUnavailable as error:
                errors.append(error.code)
        first = threading.Thread(target=caller, args=(TARGET,))
        second = threading.Thread(target=caller, args=(OTHER,))
        first.start()
        try:
            self.assertTrue(entered.wait(1.0))
            second.start()
            with self.value._condition:
                self.assertTrue(self.value._condition.wait_for(lambda: bool(self.value._pending), timeout=1.0))
            self.assertEqual(order, [TARGET])
        finally:
            release.set()
            first.join(timeout=1.0)
            if second.ident is not None:
                second.join(timeout=1.0)
        self.assertEqual(errors, [])
        self.assertEqual(results, {TARGET: EXPECTED, OTHER: EXPECTED})
        self.assertEqual(order, [TARGET, OTHER])
        self.assertEqual(self.check.call_count, 1)

    def test_status_alone_loads_no_credentials_and_makes_no_connection(self):
        status = self.value.status()
        self.assertTrue(status["configured"])
        self.assertFalse(status["connected"])
        self.assertFalse(status["automatic_renewal"])
        self.config.assert_not_called()
        self.inputs.assert_not_called()
        self.server.assert_not_called()
        self.check.assert_not_called()

    def test_invalid_target_season_platform_never_loads_credentials_or_network(self):
        cases = [(OWN, 19, "pc"), (True, 19, "pc"), (0, 19, "pc"),
                 ("1822797559", 19, "pc"), (2**63, 19, "pc"),
                 (TARGET, True, "pc"), (TARGET, -1, "pc"), (TARGET, 1000, "pc"),
                 (TARGET, 19, "ps"), (TARGET, 19, "xbox")]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(client.GameCareerUnavailable):
                self.value.read_profile(*args)
        for mocked in (self.config, self.methods, self.inputs, self.server, self.check, self.read):
            mocked.assert_not_called()
        self.assertIsNone(self.value._worker)

    def test_invalid_own_uid_rejected_at_construction(self):
        for uid in (True, 0, "1370598422", 2**63):
            with self.assertRaises(client.GameCareerUnavailable):
                client.GameCareerClient(uid, Path("fixture"))

    def test_idle_closes_and_later_read_reloads_current_login_inputs(self):
        self.value.read_profile(TARGET, 19)
        worker = self.value._worker
        worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.connections[0].closed)
        self.assertFalse(self.value.status()["connected"])
        self.assertEqual(self.value.read_profile(OTHER, 19), EXPECTED)
        self.assertEqual(self.inputs.call_count, 2)
        self.assertEqual(self.check.call_count, 2)

    def test_close_is_idempotent_and_reopens_only_after_worker_ends(self):
        self.value.read_profile(TARGET, 19)
        old_worker = self.value._worker
        self.value.close()
        self.value.close()
        self.assertFalse(old_worker.is_alive())
        self.assertTrue(self.connections[0].closed)
        self.assertTrue(self.connections[0].shutdown_called)
        self.assertFalse(self.value.status()["connected"])
        self.assertEqual(self.value.read_profile(OTHER, 19), EXPECTED)
        self.assertEqual(self.check.call_count, 2)

    def test_failed_auth_sets_cross_target_cooldown_without_retry(self):
        self.check.side_effect = None
        self.check.return_value = {"game_login_verified": False, "reason": "fixture-never-export"}
        with self.assertRaises(client.GameCareerUnavailable) as failure:
            self.value.read_profile(TARGET, 19)
        self.assertEqual(failure.exception.code, "game_career_session_unavailable")
        with self.assertRaises(client.GameCareerUnavailable) as cooldown:
            self.value.read_profile(OTHER, 19)
        self.assertEqual(cooldown.exception.code, "game_career_cooldown")
        self.assertEqual(self.check.call_count, 1)
        self.assertEqual(self.inputs.call_count, 1)
        self.read.assert_not_called()
        self.assertGreater(self.value.status()["cooldown_remaining_s"], 0)
        self.assertNotIn("fixture-never-export", str(failure.exception) + json.dumps(self.value.status()))

    def test_credential_loading_failure_is_fixed_and_redacted(self):
        self.inputs.side_effect = ValueError("fixture-never-export")
        with self.assertRaises(client.GameCareerUnavailable) as failure:
            self.value.read_profile(TARGET, 19)
        self.assertEqual(failure.exception.code, "game_career_session_unavailable")
        self.assertNotIn("fixture-never-export", repr(failure.exception))
        self.server.assert_not_called()
        self.check.assert_not_called()

    def test_expired_login_has_renewal_action_and_does_not_blame_target_uid(self):
        self.inputs.side_effect = client.login.LoginError("own_sdk_login_too_old")
        with self.assertRaises(client.GameCareerUnavailable) as failure:
            self.value.read_profile(TARGET, 19)
        self.assertEqual(failure.exception.code, "game_career_session_expired")
        self.assertIn("36 hours", failure.exception.message)
        self.assertIn("exit the game", failure.exception.message)
        self.assertIn("does not mean the target UID is invalid", failure.exception.message)
        self.server.assert_not_called()
        self.check.assert_not_called()

    def test_known_session_error_mapping_is_exact_and_fixed(self):
        for raw, code in (("own_sdk_bundle_not_found", "game_career_login_needed"),
                          ("unsupported_sdk_build", "game_career_build_unavailable"),
                          ("Unrecognized native image build", "game_career_build_unavailable"),
                          ("config checksum mismatch", "game_career_build_unavailable"),
                          ("expected_role_missing_or_ambiguous", "game_career_own_role_mismatch"),
                          ("gateway_role_mismatch", "game_career_own_role_mismatch")):
            with self.subTest(raw=raw):
                self.assertEqual(client._session_error(ValueError(raw)).code, code)
        for raw in ({"token": "fixture-never-export"}, ValueError("fixture-never-export"),
                    ValueError("own_sdk_login_too_old", "fixture-never-export")):
            error = client._session_error(raw)
            self.assertEqual(error.code, "game_career_session_unavailable")
            self.assertNotIn("fixture-never-export", str(error))

    def test_server_auth_rejection_has_own_session_action(self):
        self.check.side_effect = None
        self.check.return_value = {"game_login_verified": False,
                                   "outcome": "server_rejected_authentication"}
        with self.assertRaises(client.GameCareerUnavailable) as failure:
            self.value.read_profile(TARGET, 19)
        self.assertEqual(failure.exception.code, "game_career_session_rejected")
        self.assertIn("own account", failure.exception.message)

    def test_projection_requires_exact_target_and_bounded_integer_counts(self):
        for field, bad in (("player_uid", OWN), ("player_uid", str(TARGET)), ("player_uid", True),
                           ("k", True), ("d", -1), ("a", 1.5), ("k", 10**12 + 1),
                           ("total_win_count", 11), ("total_match_count", None)):
            data = profile(TARGET)
            data["career"][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(client.GameCareerUnavailable):
                client._project_profile(data, TARGET)

    def test_mismatched_uid_stops_connection_and_backs_off_other_targets(self):
        def wrong(*args, **kwargs):
            kwargs["on_verified"](profile(OWN))
            return {"career_verified": True}
        self.read.side_effect = wrong
        with self.assertRaises(client.GameCareerUnavailable) as failure:
            self.value.read_profile(TARGET, 19)
        self.assertEqual(failure.exception.code, "game_career_shape_changed")
        with self.assertRaises(client.GameCareerUnavailable):
            self.value.read_profile(OTHER, 19)
        self.assertEqual(self.read.call_count, 1)

    def test_verification_flag_without_projection_is_not_success(self):
        self.read.side_effect = None
        self.read.return_value = {"career_verified": True}
        with self.assertRaises(client.GameCareerUnavailable):
            self.value.read_profile(TARGET, 19)

    def test_projection_without_verified_report_is_not_success(self):
        def unverified(*args, **kwargs):
            kwargs["on_verified"](profile(TARGET))
            return {"career_verified": False, "reason": "fixture-never-export"}
        self.read.side_effect = unverified
        with self.assertRaises(client.GameCareerUnavailable) as failure:
            self.value.read_profile(TARGET, 19)
        self.assertNotIn("fixture-never-export", str(failure.exception))

    def test_connection_request_count_is_bounded(self):
        self.value.MAX_REQUESTS = 1
        self.assertEqual(self.value.read_profile(TARGET, 19), EXPECTED)
        worker = self.value._worker
        worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.connections[0].closed)
        self.assertEqual(self.value.read_profile(OTHER, 19), EXPECTED)
        self.assertEqual(self.check.call_count, 2)

    def test_close_cancels_inflight_result_and_prevents_callback_revival(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(2)
            kwargs["on_verified"](profile(TARGET))
            return {"career_verified": True}
        self.read.side_effect = delayed
        def caller():
            try:
                self.value.read_profile(TARGET, 19)
            except client.GameCareerUnavailable as error:
                errors.append(error.code)
        thread = threading.Thread(target=caller)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.value.close()
            thread.join(timeout=1.0)
            self.assertEqual(errors, ["game_career_closed"])
            self.assertFalse(self.value.status()["connected"])
            with self.assertRaises(client.GameCareerUnavailable) as busy:
                self.value.read_profile(OTHER, 19)
            self.assertEqual(busy.exception.code, "game_career_busy")
        finally:
            release.set()
            thread.join(timeout=1.0)
            self.value._worker.join(timeout=1.0)
        self.assertFalse(self.value.status()["connected"])

    def test_read_timeout_does_not_retry_or_publish_late_result(self):
        entered, release = threading.Event(), threading.Event()
        self.value.REQUEST_WAIT_SECONDS = 0.05
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(1)
            kwargs["on_verified"](profile(TARGET))
            return {"career_verified": True}
        self.read.side_effect = delayed
        try:
            with self.assertRaises(client.GameCareerUnavailable) as failure:
                self.value.read_profile(TARGET, 19)
            self.assertTrue(entered.is_set())
            self.assertEqual(failure.exception.code, "game_career_timeout")
            with self.assertRaises(client.GameCareerUnavailable):
                self.value.read_profile(OTHER, 19)
            self.assertEqual(self.read.call_count, 1)
        finally:
            release.set()
            self.value._worker.join(timeout=1.0)

    def test_unknown_exception_codes_cannot_export_arbitrary_text(self):
        error = client.GameCareerUnavailable("fixture-never-export")
        self.assertEqual(error.code, "game_career_unavailable")
        self.assertNotIn("fixture-never-export", str(error))

    def test_missing_bundled_component_is_not_reported_as_a_bad_uid_or_login(self):
        self.inputs.side_effect = ModuleNotFoundError("fixture-private-path")
        with self.assertRaises(client.GameCareerUnavailable) as failure:
            self.value.read_profile(TARGET, 19)
        self.assertEqual(failure.exception.code, "game_career_dependency_missing")
        self.assertNotIn("fixture-private-path", str(failure.exception))
        status = self.value.status()
        self.assertEqual(status["stage"], "own_sdk_login")
        self.assertEqual(status["failure_kind"], "dependency_missing")
        self.server.assert_not_called()


if __name__ == "__main__":
    unittest.main()
