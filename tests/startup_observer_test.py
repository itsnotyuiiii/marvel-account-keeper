"""Synthetic lifecycle observers; no game files, credentials or network calls."""
from dataclasses import FrozenInstanceError, fields
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import game_career_probe as career
import game_login_probe as login
import game_rank_panel_probe as rank
import game_social_token_probe as social
from nex_rpc_probe import split_plaintext_records
from game_rank_panel_probe_test import FakeSocket, panel, record, rpc


ZONE = {"zone_id": 11001, "role_uid": login.EXPECTED_OWN_UID}
ENTITY_ID = 90001  # Deliberately not the account UID.
PRIVATE_DATA = b"PRIVATE_STARTUP_PAYLOAD"


def lifecycle(sub, values=None):
    schema = {111: career.MSG_ACK, 112: career.MSG_RESET,
              113: career.ENTITY_CREATE, 114: career.ENTITY_DESTROY,
              120: career.ENTITY_PROP}[sub]
    defaults = {
        111: {"ack": 4}, 112: {"reset": 1},
        113: {"eid": ENTITY_ID, "classname": "GameServiceAvatar", "initdata": PRIVATE_DATA},
        114: {"eid": ENTITY_ID},
        120: {"entity_id": ENTITY_ID, "sync_msg": PRIVATE_DATA},
    }
    return record(65534, sub, login.encode_body(schema, defaults[sub] if values is None else values))


def telemetry():
    return {"own_entity_created": False, "startup_record_count": 0}


def transport():
    return SimpleNamespace(encrypt=lambda value: value, decrypt=lambda value: value)


class StartupEventTest(unittest.TestCase):
    def consume(self, wire, callback=None, report=None):
        item, = split_plaintext_records(wire)
        report = telemetry() if report is None else report
        result = career.consume_startup_record(item, report, on_startup=callback)
        return result, report

    def test_event_is_frozen_with_no_payload_identity_or_class_in_repr(self):
        event = career.StartupEvent("create", ENTITY_ID, "PRIVATE_CLASS", PRIVATE_DATA)
        self.assertEqual([field.name for field in fields(event)],
                         ["kind", "entity_id", "classname", "data"])
        for text in (str(ENTITY_ID), "PRIVATE_CLASS", "PRIVATE_STARTUP_PAYLOAD"):
            self.assertNotIn(text, repr(event))
        with self.assertRaises(FrozenInstanceError):
            event.data = b"changed"
        empty = career.StartupEvent("reset")
        self.assertIsNone(empty.entity_id)
        self.assertIsNone(empty.classname)
        self.assertIsNone(empty.data)

    def test_create_delivers_exact_transient_engine_context_not_uid_telemetry(self):
        callback = Mock()
        consumed, report = self.consume(lifecycle(113), callback)
        self.assertTrue(consumed)
        callback.assert_called_once()
        self.assertEqual(callback.call_args.args, (
            career.StartupEvent("create", ENTITY_ID, "GameServiceAvatar", PRIVATE_DATA),))
        self.assertFalse(report["own_entity_created"])
        self.assertEqual(report["startup_record_count"], 1)
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_destroy_and_property_events_have_only_their_fields(self):
        cases = [(114, career.StartupEvent("destroy", ENTITY_ID)),
                 (120, career.StartupEvent("property", ENTITY_ID, data=PRIVATE_DATA))]
        for sub, expected in cases:
            with self.subTest(sub=sub):
                callback = Mock()
                consumed, report = self.consume(lifecycle(sub), callback)
                self.assertTrue(consumed)
                callback.assert_called_once_with(expected)
                self.assertEqual(report["startup_record_count"], 1)
                self.assertNotIn("PRIVATE", json.dumps(report))

    def test_ack_and_unknown_commands_do_not_notify(self):
        for wire, expected_count, expected in ((lifecycle(111), 1, True),
                (record(65534, 119, b""), 0, False), (record(4, 102, b""), 0, False)):
            callback = Mock()
            consumed, report = self.consume(wire, callback)
            self.assertIs(consumed, expected)
            self.assertEqual(report["startup_record_count"], expected_count)
            callback.assert_not_called()

    def test_malformed_or_incomplete_envelopes_do_not_notify(self):
        cases = [lifecycle(113, {"eid": ENTITY_ID, "classname": "GameServiceAvatar"}),
                 lifecycle(113, {"eid": 0, "classname": "GameServiceAvatar", "initdata": b""}),
                 lifecycle(113, {"eid": ENTITY_ID, "classname": "", "initdata": b""}),
                 lifecycle(114, {"eid": 0}), lifecycle(120, {"entity_id": ENTITY_ID}),
                 record(65534, 113, b"\xe3\x05"), lifecycle(112, {})]
        for wire in cases:
            with self.subTest(wire_size=len(wire)):
                callback = Mock()
                with self.assertRaises((career.CareerError, login.LoginError, ValueError)):
                    self.consume(wire, callback)
                callback.assert_not_called()

    def test_observer_exception_is_redacted_and_does_not_break_consumption(self):
        callback = Mock(side_effect=RuntimeError("PRIVATE_OBSERVER_EXCEPTION"))
        consumed, report = self.consume(lifecycle(113), callback)
        self.assertTrue(consumed)
        self.assertIs(report["startup_observer_failed"], True)
        self.assertEqual(report["startup_record_count"], 1)
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_reset_invalidates_before_the_existing_sequence_error(self):
        state = {"usable": True}
        events = []
        def observe(event):
            events.append(event)
            state["usable"] = False
        with self.assertRaisesRegex(career.CareerError, "server_sequence_reset_requires_handling"):
            self.consume(lifecycle(112), observe)
        self.assertFalse(state["usable"])
        self.assertEqual(events, [career.StartupEvent("reset")])

    def test_reset_observer_exception_preserves_the_sequence_error_and_failure_flag(self):
        report = telemetry()
        callback = Mock(side_effect=RuntimeError("PRIVATE_RESET_FAILURE"))
        with self.assertRaisesRegex(career.CareerError, "server_sequence_reset_requires_handling"):
            self.consume(lifecycle(112), callback, report)
        callback.assert_called_once_with(career.StartupEvent("reset"))
        self.assertIs(report["startup_observer_failed"], True)
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_default_none_preserves_old_telemetry_without_added_payload_fields(self):
        expected = telemetry()
        expected["startup_record_count"] = 1
        self.assertEqual(self.consume(lifecycle(113))[1], expected)

    def test_invalid_observer_rejected_before_envelope_decode(self):
        with patch.object(login, "decode_body") as decode:
            with self.assertRaisesRegex(career.CareerError, "startup_observer_inputs_invalid"):
                self.consume(lifecycle(113), callback="PRIVATE_NOT_CALLABLE")
        decode.assert_not_called()


class StartupReaderHookTest(unittest.TestCase):
    def read_token(self, wire, observer=None):
        sock, session, consumer = FakeSocket(wire), object(), Mock()
        with patch.object(social.social, "parse_gateway_social_token", return_value=session) as parse:
            result = social.read_social_token(sock, transport(), ZONE,
                token_rpc_index=social.REVIEWED_TOKEN_RPC_INDEX, on_token=consumer,
                on_startup=observer)
        self.assertFalse(sock.sent)
        self.assertNotIn("PRIVATE", json.dumps(result))
        return result, parse, consumer, session

    def token_reply(self):
        return rpc(social.REVIEWED_TOKEN_RPC_INDEX, ["PRIVATE_TRANSIENT_TOKEN"])

    def test_token_reader_forwards_startup_events_before_its_consumer(self):
        events = []
        result, parse, consumer, session = self.read_token(
            lifecycle(113) + lifecycle(120) + lifecycle(114) + lifecycle(111) + self.token_reply(),
            events.append)
        self.assertEqual([event.kind for event in events], ["create", "property", "destroy"])
        self.assertTrue(result["owner_claim_matches"])
        self.assertTrue(result["consumer_completed"])
        self.assertEqual(result["outcome"], "social_token_claims_validated")
        self.assertEqual(result["startup_record_count"], 4)
        self.assertFalse(result.get("startup_observer_failed", False))
        consumer.assert_called_once_with(session)
        parse.assert_called_once()

    def test_token_observer_failure_does_not_prevent_verified_token_consumer(self):
        observer = Mock(side_effect=RuntimeError("PRIVATE_OBSERVER_FAILURE"))
        result, _, consumer, session = self.read_token(lifecycle(113) + self.token_reply(), observer)
        self.assertIs(result["startup_observer_failed"], True)
        self.assertEqual(result["outcome"], "social_token_claims_validated")
        self.assertTrue(result["owner_claim_matches"])
        self.assertTrue(result["consumer_completed"])
        consumer.assert_called_once_with(session)

    def test_token_reset_event_precedes_rejection_and_never_consumes_later_token(self):
        events = []
        result, parse, consumer, _ = self.read_token(lifecycle(112) + self.token_reply(), events.append)
        self.assertEqual(events, [career.StartupEvent("reset")])
        self.assertEqual(result["outcome"], "social_token_startup_rejected")
        self.assertFalse(result["social_token_received"])
        parse.assert_not_called()
        consumer.assert_not_called()

    def test_token_reset_observer_failure_still_reaches_public_failure_metadata(self):
        observer = Mock(side_effect=RuntimeError("PRIVATE_RESET_FAILURE"))
        result, _, consumer, _ = self.read_token(lifecycle(112), observer)
        self.assertEqual(result["outcome"], "social_token_startup_rejected")
        self.assertIs(result["startup_observer_failed"], True)
        consumer.assert_not_called()

    def test_invalid_token_observer_never_receives_or_parses(self):
        with patch.object(login, "receive_record") as receive:
            result, parse, consumer, _ = self.read_token(b"", "PRIVATE_NOT_CALLABLE")
        self.assertEqual(result["outcome"], "social_token_reader_inputs_invalid")
        receive.assert_not_called()
        parse.assert_not_called()
        consumer.assert_not_called()

    def test_panel_forwards_lifecycle_events_and_retains_primary_success(self):
        events = []
        sock = FakeSocket(lifecycle(113) + lifecycle(120) + lifecycle(114) + lifecycle(111)
                          + rpc(rank.REPLY_INDEX, [panel()]))
        result = rank.read_rank_panel_once(sock, transport(), ZONE, on_startup=events.append)
        self.assertEqual([event.kind for event in events], ["create", "property", "destroy"])
        self.assertEqual(len(sock.sent), 1)
        self.assertTrue(result["rank_panel_verified"])
        self.assertTrue(result["current_season_verified"])
        self.assertTrue(result["connection_reuse_allowed"])
        self.assertEqual(result["startup_record_count"], 4)
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_panel_observer_failure_is_independent_of_primary_rank_verification(self):
        observer = Mock(side_effect=RuntimeError("PRIVATE_OBSERVER_FAILURE"))
        sock = FakeSocket(lifecycle(120) + rpc(rank.REPLY_INDEX, [panel()]))
        result = rank.read_rank_panel_once(sock, transport(), ZONE, on_startup=observer)
        self.assertIs(result["startup_observer_failed"], True)
        self.assertEqual(result["outcome"], "owner_rank_panel_verified")
        self.assertTrue(result["rank_panel_verified"])
        self.assertTrue(result["connection_reuse_allowed"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_panel_reset_invalidates_and_rejects_instead_of_accepting_later_reply(self):
        for failure in (False, True):
            with self.subTest(observer_fails=failure):
                observer = Mock(side_effect=RuntimeError("PRIVATE_RESET_FAILURE") if failure else None)
                sock = FakeSocket(lifecycle(112) + rpc(rank.REPLY_INDEX, [panel()]))
                result = rank.read_rank_panel_once(sock, transport(), ZONE, on_startup=observer)
                observer.assert_called_once_with(career.StartupEvent("reset"))
                self.assertEqual(result["outcome"], "rank_panel_startup_rejected")
                self.assertFalse(result["rank_panel_verified"])
                self.assertFalse(result["connection_reuse_allowed"])
                if failure:
                    self.assertIs(result["startup_observer_failed"], True)
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_invalid_panel_observer_rejected_before_encrypt_send_or_receive(self):
        sock, cipher = FakeSocket(b""), Mock()
        with patch.object(login, "receive_record") as receive:
            result = rank.read_rank_panel_once(sock, cipher, ZONE, on_startup="PRIVATE_NOT_CALLABLE")
        self.assertEqual(result["outcome"], "rank_panel_inputs_invalid")
        self.assertFalse(result["request_attempted"])
        self.assertFalse(sock.sent)
        cipher.encrypt.assert_not_called()
        receive.assert_not_called()


class StartupWrapperHookTest(unittest.TestCase):
    def test_invalid_wrapper_observer_rejected_before_authentication_preparation(self):
        with patch.object(social.web_auth, "build_web_login_inputs") as build, \
                patch.object(login, "check_login") as check:
            result = social.run_social_token_once({}, None, None, client_version="3848703",
                                                 on_startup="PRIVATE_NOT_CALLABLE")
        self.assertEqual(result["outcome"], "social_token_reader_inputs_invalid")
        self.assertFalse(result["credentials_sent"])
        build.assert_not_called()
        check.assert_not_called()

    def test_same_observer_spans_wrapper_token_and_following_panel_on_one_gateway(self):
        order, events, panel_reports = [], [], []
        wire = (lifecycle(113) + rpc(social.REVIEWED_TOKEN_RPC_INDEX, ["PRIVATE_TOKEN"])
                + lifecycle(120) + lifecycle(114) + rpc(rank.REPLY_INDEX, [panel()]))
        sock, cipher, session = FakeSocket(wire), transport(), object()
        def observe(event):
            events.append(event)
            order.append(event.kind)
        def token_consumer(value):
            self.assertIs(value, session)
            order.append("token")
        def game_consumer(connection, current_transport, zone):
            self.assertIs(connection, sock)
            self.assertIs(current_transport, cipher)
            self.assertIs(zone, ZONE)
            order.append("panel")
            panel_reports.append(rank.read_rank_panel_once(connection, current_transport, zone,
                                                           on_startup=observe))
        def gateway(configuration, server, *, after_login, **kwargs):
            self.assertTrue(kwargs["enter"])
            self.assertEqual(kwargs["expected_uid"], login.EXPECTED_OWN_UID)
            after_login(sock, cipher, ZONE)
            return {}
        with patch.object(social.web_auth, "build_web_login_inputs", return_value=object()), \
                patch.object(login, "check_login", side_effect=gateway) as check, \
                patch.object(social.web_auth, "_project_gateway_report",
                             return_value={"game_login_verified": True}), \
                patch.object(social.social, "parse_gateway_social_token", return_value=session):
            result = social.run_social_token_once({}, None, None, client_version="3848703",
                on_token=token_consumer, on_game_connection=game_consumer, on_startup=observe)
        check.assert_called_once()
        self.assertEqual(order, ["create", "token", "panel", "property", "destroy"])
        self.assertEqual([event.entity_id for event in events], [ENTITY_ID] * 3)
        self.assertTrue(result["social_token"]["consumer_completed"])
        self.assertTrue(result["social_token"]["on_game_connection_completed"])
        self.assertTrue(panel_reports[0]["rank_panel_verified"])
        self.assertFalse(sock.incoming)
        self.assertEqual(len(sock.sent), 1)  # Only the normal panel request in this supplied gateway.
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("PRIVATE", json.dumps(panel_reports))


if __name__ == "__main__":
    unittest.main()
