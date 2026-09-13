"""Synthetic MessagePack property streams only; no game, account or network."""

import copy
import hashlib
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import msgpack

import game_career_probe as career
import game_property_codec as codec
import owner_rank_snapshot as owner_rank


OWNER = 1370598422
ZONE = 11001
EID = 90001  # Deliberately not a player UID.


def row(season=19, **changes):
    return {"rank_game_id": season, "level": 14, "rank_score": 4350.75,
            "max_level": 17, "max_rank_score": 4670.25, "update_time": 1700000000,
            "fixed_battle_count": 10, "battle_count": 27, "win_count": 21,
            "season_max_level": 23, "private": "fixture-private-row", **changes}


def named(classname, fields):
    pairs = fields.items() if type(fields) is dict else fields
    values = [classname]
    for key, value in pairs:
        values.extend((key, value))
    return msgpack.ExtType(0, b"".join(msgpack.packb(value, use_bin_type=True) for value in values))


def root(*, ranks=None, uid=OWNER, zone_id=ZONE, root_class="GameServiceAvatar",
         component_class="RankGameComponent", component_fields=None, root_fields=None):
    if ranks is None:
        ranks = {1001019: row(), 1001017: row(17)}
    component = named(component_class, {"ranks": ranks, "open_games": {"private": "fixture-private-open"}}
                      if component_fields is None else component_fields)
    fields = {"uid": uid, "zone_id": zone_id, "RankGame": component,
              "private": {"access_token": "fixture-private-token", "array": [1, 2, 3]}}
    return msgpack.packb(named(root_class, fields if root_fields is None else root_fields), use_bin_type=True)


def create(data=None, *, eid=EID, classname="GameServiceAvatar"):
    return career.StartupEvent("create", eid, classname, root() if data is None else data)


def event(kind, eid=EID, *, data=None):
    return career.StartupEvent(kind, eid, None, data)


def sync_message(*entries, operation=1):
    values = [operation]
    for path, value in entries:
        values.extend((len(path), *path, value))
    return msgpack.packb([msgpack.ExtType(2, b"".join(
        msgpack.packb(value, use_bin_type=True) for value in values))], use_bin_type=True)


class OwnerRankSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.observer = owner_rank.OwnerRankObserver(OWNER)

    def snapshot(self):
        result = self.observer.snapshot(zone_id=ZONE)
        self.assertNotIn("fixture-private", json.dumps(result, allow_nan=False))
        self.assertNotIn("fixture-private", repr(self.observer))
        return result

    def project(self, data=None):
        self.observer.observe(create(data))
        return self.snapshot()

    def test_exact_named_root_component_and_numeric_pc_normal_rows(self):
        data = root()
        with patch.object(codec, "decode_named_fields", wraps=codec.decode_named_fields) as decode:
            result = self.project(data)
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(decode.call_args_list[0].kwargs,
                         {"expected_class": "GameServiceAvatar", "wanted_names": ("uid", "zone_id", "RankGame")})
        self.assertEqual(decode.call_args_list[1].kwargs,
                         {"expected_class": "RankGameComponent", "wanted_names": ("ranks",)})
        self.assertIs(type(decode.call_args_list[1].args[0]), codec.OpaqueExtension)
        self.assertEqual(result["outcome"], "owner_rank_snapshot_projected")
        self.assertTrue(result["owner_identity_verified"])
        self.assertTrue(result["ranks_verified"])
        self.assertTrue(result["rank_data_present"])
        self.assertEqual(result["uid"], str(OWNER))
        self.assertEqual([item["season"] for item in result["seasons"]], [19, 17])
        self.assertEqual(result["seasons"][0], {
            "season": 19, "level": 14, "points": 4350.75, "peak_level": 17, "peak_points": 4670.25,
            "updated_at": 1700000000, "placements_completed": 10, "record": {"matches": 27, "wins": 21}})
        self.assertEqual(result["identity_basis"], "owner_initial_properties")
        self.assertEqual(result["points_basis"], "server_rank_score")
        for name in ("current_season", "current", "peak", "decode_outcome"):
            self.assertIsNone(result[name])
        for name in ("current_season_verified", "overall_peak_verified", "rank_labels_verified",
                     "secrets_saved", "remote_code_executed"):
            self.assertFalse(result[name])
        for name in ("auth_service_accepted", "request_bound", "response_identity_echo_verified", "rank_reply_bound"):
            self.assertNotIn(name, result)

    def test_pc_normal_filter_only_no_opened_or_largest_season_inference(self):
        result = self.project(root(ranks={1001001: row(1), 1001999: row(999),
                                         2001019: row(19, level=22), 4001019: row(19, level=23),
                                         1002019: row(19, level=21)}))
        self.assertTrue(result["ranks_verified"])
        self.assertEqual([item["season"] for item in result["seasons"]], [999, 1])
        self.assertIsNone(result["current_season"])
        self.assertEqual(result["history_completeness"], "not_established")

    def test_missing_rank_field_and_explicit_empty_map_are_distinct(self):
        result = self.project(root(component_fields={"open_games": {}}))
        self.assertEqual(result["outcome"], "owner_rank_map_missing")
        self.assertFalse(result["ranks_verified"])
        result = self.project(root(ranks={}))
        self.assertTrue(result["ranks_verified"])
        self.assertTrue(result["rank_data_present"])
        self.assertEqual(result["seasons"], [])
        self.assertEqual(result["season_count"], 0)
        self.assertNotIn("unranked", json.dumps(result))

    def test_source_missing_root_identity_or_component_never_becomes_empty_history(self):
        for fields in ({}, {"uid": OWNER}, {"uid": OWNER, "zone_id": ZONE},
                       {"zone_id": ZONE, "RankGame": named("RankGameComponent", {"ranks": {}})}):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            result = self.project(root(root_fields=fields))
            self.assertEqual(result["outcome"], "owner_rank_fields_missing")
            self.assertFalse(result["owner_identity_verified"])
            self.assertFalse(result["ranks_verified"])

    def test_engine_eid_is_not_owner_uid_proof(self):
        self.assertTrue(self.project()["ranks_verified"])
        self.observer = owner_rank.OwnerRankObserver(OWNER)
        self.observer.observe(create(root(uid=1822797559), eid=OWNER))
        result = self.snapshot()
        self.assertEqual(result["outcome"], "owner_rank_owner_mismatch")
        self.assertFalse(result["owner_identity_verified"])

    def test_root_uid_is_exact_int_without_sentinel_or_string_conversion(self):
        for value in (True, str(OWNER), float(OWNER), None, "(None)", "(True)"):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            result = self.project(root(uid=value))
            self.assertEqual(result["outcome"], "owner_rank_owner_mismatch")
            self.assertFalse(result["ranks_verified"])

    def test_root_zone_and_caller_zone_are_both_strict_and_must_match(self):
        for value in (True, "11001", 0, -1, 1 << 31, None):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            result = self.project(root(zone_id=value))
            self.assertEqual(result["outcome"], "owner_rank_zone_invalid")
        self.observer = owner_rank.OwnerRankObserver(OWNER)
        self.project()
        for value in (True, "11001", 0, -1, 1 << 31, None):
            result = self.observer.snapshot(zone_id=value)
            self.assertEqual(result["outcome"], "owner_rank_zone_invalid")
            self.assertFalse(result["owner_identity_verified"])
        mismatch = self.observer.snapshot(zone_id=ZONE + 1)
        self.assertEqual(mismatch["outcome"], "owner_rank_zone_mismatch")
        self.assertEqual(mismatch["seasons"], [])
        self.assertTrue(self.snapshot()["ranks_verified"])

    def test_root_and_component_classes_are_exact_and_extension_zero_required(self):
        for data, expected in ((root(root_class="fixture-private-class"), "property_class_mismatch"),
                                (root(component_class="RankGame"), "property_class_mismatch"),
                                (msgpack.packb({"uid": OWNER}), "property_outer_extension"),
                                (msgpack.packb(msgpack.ExtType(1, b"fixture-private-data")), "property_extension_type"),
                                (root(root_fields={"uid": OWNER, "zone_id": ZONE, "RankGame": {"ranks": {}}}),
                                 "property_input_invalid")):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            result = self.project(data)
            self.assertEqual(result["outcome"], "owner_rank_properties_unavailable")
            self.assertEqual(result["decode_outcome"], expected)
            self.assertFalse(result["ranks_verified"])

    def test_numeric_property_indices_are_unavailable_not_guessed(self):
        data = msgpack.packb(named("GameServiceAvatar", [(1, OWNER), (2, ZONE)]), use_bin_type=True)
        result = self.project(data)
        self.assertEqual(result["decode_outcome"], "property_numeric_key_unreviewed")
        self.assertFalse(result["ranks_verified"])
        nested = root(component_fields=[(1, {1001019: row()})])
        result = self.project(nested)
        self.assertEqual(result["decode_outcome"], "property_numeric_key_unreviewed")

    def test_rank_mapping_original_types_are_not_inferred_from_json_or_extensions(self):
        for value in (None, [], "{}", named("fixture-private-rankmap", {})):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            result = self.project(root(component_fields={"ranks": value}))
            self.assertEqual(result["outcome"], "owner_rank_map_unsupported")
        for key in ("1001019", "01001019", True, 1001019.0, 0, -1, 1_000_000_000):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            result = self.project(root(ranks={key: row()}))
            self.assertFalse(result["ranks_verified"])
        for value in (json.dumps(row()), [], named("fixture-private-rankrow", row())):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            result = self.project(root(ranks={1001019: value}))
            self.assertEqual(result["outcome"], "owner_rank_row_unsupported")

    def test_row_rank_game_id_must_match_selected_key(self):
        for key, value in ((1001000, row(0)), (1001019, row(18)), (1001019, row(True)),
                           (1001019, row("19")), (1001019, row(19.5))):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            result = self.project(root(ranks={key: value}))
            self.assertEqual(result["outcome"], "owner_rank_season_identity_mismatch")
        for name in owner_rank.REQUIRED_ROW_FIELDS:
            value = row()
            del value[name]
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            result = self.project(root(ranks={1001019: value}))
            self.assertEqual(result["outcome"], "owner_rank_row_field_missing")
            self.assertFalse(result["ranks_verified"])

    def test_bounded_levels_points_and_integral_float_codes(self):
        result = self.project(root(ranks={1001019: row(19.0, level=14.0, max_level=17.0)}))
        self.assertEqual(result["seasons"][0]["level"], 14)
        self.assertIs(type(result["seasons"][0]["level"]), int)
        for field, values in (("level", [True, "14", -1, 65536, 14.5, None]),
                              ("max_level", [True, -1, 65536]),
                              ("rank_score", [True, "4350", -1, 1000001, float("inf"), float("nan")]),
                              ("max_rank_score", [True, -1, 1000001, None])):
            for value in values:
                self.observer = owner_rank.OwnerRankObserver(OWNER)
                result = self.project(root(ranks={1001019: row(**{field: value})}))
                self.assertEqual(result["outcome"], "owner_rank_statistics_invalid")
                self.assertFalse(result["ranks_verified"])

    def test_optional_counts_do_not_erase_rank_or_invent_zeros(self):
        cases = [{"battle_count": None}, {"win_count": None}, {"battle_count": 20, "win_count": 21},
                 {"battle_count": True}, {"win_count": "2"}, {"battle_count": -1}, {"win_count": 1.5},
                 {"battle_count": owner_rank.MAX_COUNT + 1}]
        for changes in cases:
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            result = self.project(root(ranks={1001019: row(**changes)}))
            self.assertTrue(result["ranks_verified"])
            self.assertIsNone(result["seasons"][0]["record"])
        result = self.project(root(ranks={1001019: row(battle_count=0, win_count=0,
                                                       fixed_battle_count=True, update_time="fixture-private-time")}))
        self.assertEqual(result["seasons"][0]["record"], {"matches": 0, "wins": 0})
        self.assertIsNone(result["seasons"][0]["updated_at"])
        self.assertIsNone(result["seasons"][0]["placements_completed"])

    def test_collection_and_row_bounds_disable_only_rank_projection(self):
        result = self.project(root(ranks={1001000 + n: row(n) for n in range(1, owner_rank.MAX_SEASONS + 2)}))
        self.assertEqual(result["outcome"], "owner_rank_bound")
        self.observer = owner_rank.OwnerRankObserver(OWNER)
        huge_row = {**row(), **{f"private_{n}": n for n in range(owner_rank.MAX_ROW_FIELDS)}}
        result = self.project(root(ranks={1001019: huge_row}))
        self.assertEqual(result["outcome"], "owner_rank_bound")

    def test_destroy_before_create_and_unrelated_entities_are_harmless(self):
        self.observer.observe(event("destroy"))
        self.assertEqual(self.snapshot()["outcome"], "owner_rank_not_observed")
        self.project()
        with patch.object(codec, "decode_named_fields", wraps=codec.decode_named_fields) as decode:
            self.observer.observe(create(b"fixture-private-unparsed", eid=EID + 1, classname="OtherEntity"))
            self.observer.observe(event("property", EID + 1, data=b"fixture-private-sync"))
            self.observer.observe(event("destroy", EID + 1))
        decode.assert_not_called()
        self.assertTrue(self.snapshot()["ranks_verified"])

    def test_other_root_uid_does_not_overwrite_active_owner_projection(self):
        self.project()
        self.observer.observe(create(root(uid=1822797559), eid=EID + 1))
        self.assertTrue(self.snapshot()["ranks_verified"])
        self.assertEqual(self.snapshot()["uid"], str(OWNER))

    def test_selected_destroy_invalidates_until_new_full_create(self):
        self.project()
        self.observer.observe(event("destroy"))
        self.assertEqual(self.snapshot()["outcome"], "owner_rank_entity_destroyed")
        self.assertFalse(self.snapshot()["ranks_verified"])
        self.assertTrue(self.project()["ranks_verified"])

    def test_selected_property_update_invalidates_without_decoding_or_stale_rows(self):
        self.project()
        with patch.object(codec, "decode_named_fields") as decode:
            self.observer.observe(event("property", data=b"fixture-private-sync"))
        decode.assert_not_called()
        result = self.snapshot()
        self.assertEqual(result["outcome"], "owner_rank_property_update_unhandled")
        self.assertEqual(result["seasons"], [])
        self.assertFalse(result["ranks_verified"])
        self.assertTrue(self.project()["ranks_verified"])

    def test_reset_invalidates_selected_entity_and_does_not_restore_old_snapshot(self):
        self.project()
        self.observer.observe(event("reset", None))
        self.assertEqual(self.snapshot()["outcome"], "owner_rank_sequence_reset")
        self.observer.observe(event("property", data=b"fixture-private-sync"))
        self.assertFalse(self.snapshot()["ranks_verified"])
        self.assertTrue(self.project()["ranks_verified"])

    def test_same_eid_recreation_clears_before_class_or_payload_validation(self):
        for replacement in (create(b"fixture-private-bad"), create(root(uid=1822797559)),
                            create(b"fixture-private-other", classname="OtherEntity")):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            self.project()
            self.observer.observe(replacement)
            self.assertFalse(self.snapshot()["ranks_verified"])
            self.assertEqual(self.snapshot()["seasons"], [])
            self.assertTrue(self.project()["ranks_verified"])

    def test_multiple_owner_entities_are_ambiguous_without_resurrecting_stale_rows(self):
        self.project()
        self.observer.observe(create(eid=EID + 1))
        self.assertEqual(self.snapshot()["outcome"], "owner_rank_entities_ambiguous")
        self.assertFalse(self.snapshot()["ranks_verified"])
        self.observer.observe(event("destroy", EID + 1))
        self.assertFalse(self.snapshot()["ranks_verified"])
        self.assertTrue(self.project()["ranks_verified"])

    def test_decoder_failures_are_fixed_and_never_escape_or_include_private_values(self):
        self.project()
        for failure in (codec.PropertyCodecError("property_truncated"), RuntimeError("fixture-private-error")):
            with patch.object(codec, "decode_named_fields", side_effect=failure):
                self.observer.observe(create())
            result = self.snapshot()
            self.assertFalse(result["ranks_verified"])
            self.assertIn(result["outcome"], owner_rank.OUTCOMES)
            self.assertNotIn("fixture-private", repr(failure) if type(failure) is codec.PropertyCodecError else repr(result))
            self.assertTrue(self.project()["ranks_verified"])

    def test_manually_altered_error_metadata_cannot_escape_fixed_projection(self):
        codec_error = codec.PropertyCodecError("property_truncated")
        codec_error.code = "fixture-private-error-code"
        observer_error = owner_rank.OwnerRankError("owner_rank_bound")
        observer_error.outcome = "fixture-private-outcome"
        for error in (codec_error, observer_error):
            with patch.object(codec, "decode_named_fields", side_effect=error):
                self.observer.observe(create())
            result = self.snapshot()
            self.assertIn(result["outcome"], owner_rank.OUTCOMES)
            self.assertIsNone(result["decode_outcome"])
            self.assertFalse(result["ranks_verified"])

    def test_duplicate_named_fields_and_selected_map_keys_are_unavailable(self):
        data = root(root_fields=[("uid", OWNER), ("uid", OWNER), ("zone_id", ZONE)])
        result = self.project(data)
        self.assertEqual(result["decode_outcome"], "property_duplicate_field")
        # Construct an actual duplicate key map; a Python dict would erase it.
        packed_key = msgpack.packb(1001019)
        packed_row = msgpack.packb(row(), use_bin_type=True)
        ranks_map = b"\x82" + packed_key + packed_row + packed_key + packed_row
        component = msgpack.ExtType(0, msgpack.packb("RankGameComponent") + msgpack.packb("ranks") + ranks_map)
        data = root(root_fields={"uid": OWNER, "zone_id": ZONE, "RankGame": component})
        result = self.project(data)
        self.assertEqual(result["decode_outcome"], "property_duplicate_map_key")

    def test_observer_record_byte_and_entity_limits_remain_terminal_across_reset(self):
        with patch.object(owner_rank, "MAX_EVENTS", 1):
            self.project()
            self.observer.observe(event("reset", None))
            self.assertEqual(self.snapshot()["outcome"], "owner_rank_bound")
        self.assertFalse(self.project()["ranks_verified"])
        self.observer = owner_rank.OwnerRankObserver(OWNER)
        with patch.object(owner_rank, "MAX_BYTES", 16):
            result = self.project()
            self.assertEqual(result["outcome"], "owner_rank_bound")
        self.assertFalse(self.project()["ranks_verified"])
        self.observer = owner_rank.OwnerRankObserver(OWNER)
        with patch.object(owner_rank, "MAX_OWNER_ENTITIES", 1):
            self.project()
            self.observer.observe(create(eid=EID + 1))
            self.assertEqual(self.snapshot()["outcome"], "owner_rank_bound")
        self.assertFalse(self.project()["ranks_verified"])

    def test_invalid_event_object_or_engine_id_never_escapes(self):
        for value in (None, {}, SimpleNamespace(kind="create"),
                      event("unknown"), event("destroy", True), event("property", -1, data=b"x"),
                      replace(create(), data="fixture-private-value")):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            self.observer.observe(value)
            self.assertEqual(self.snapshot()["outcome"], "owner_rank_snapshot_invalid")

    def test_constructor_and_unobserved_snapshot_do_not_claim_rank_or_authentication(self):
        for uid in (None, True, "1370598422", 0, -1, 1 << 63):
            with self.assertRaisesRegex(owner_rank.OwnerRankError, "^owner_rank_inputs_invalid$"):
                owner_rank.OwnerRankObserver(uid)
        for version in (None, True, "3825464", "3848704"):
            with self.assertRaisesRegex(owner_rank.OwnerRankError, "^owner_rank_inputs_invalid$"):
                owner_rank.OwnerRankObserver(OWNER, client_version=version)
        result = self.snapshot()
        self.assertEqual(result["outcome"], "owner_rank_not_observed")
        self.assertFalse(result["ranks_verified"])
        self.assertFalse(result["owner_identity_verified"])

    def test_initial_decode_diagnostic_survives_update_without_retaining_rows(self):
        for data, expected in ((root(), "owner_rank_snapshot_projected"),
                               (root(ranks={"1001019": row()}), "owner_rank_season_identity_mismatch"),
                               (root(component_fields=[(1, {})]), "owner_rank_properties_unavailable")):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            self.project(data)
            self.observer.observe(event("property", data=b"fixture-private-sync"))
            result = self.snapshot()
            self.assertEqual(result["outcome"], "owner_rank_property_update_unhandled")
            self.assertEqual(result["initialization_outcome"], expected)
            self.assertFalse(result["ranks_verified"])
            self.assertEqual(result["seasons"], [])
            if expected == "owner_rank_properties_unavailable":
                self.assertEqual(result["initialization_decode_outcome"], "property_numeric_key_unreviewed")

    def test_reviewed_exact_scalar_changes_preserve_history(self):
        for operation in (1, 2, 3):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            before = self.project()
            self.observer.observe(event("property", data=sync_message(
                (("name",), "fixture-private-name"), (("level",), 32),
                (("score",), 1), (("exp",), 400), (("create_time",), 1700000000), operation=operation)))
            result = self.snapshot()
            self.assertTrue(result["ranks_verified"])
            self.assertEqual(result["seasons"], before["seasons"])
            self.assertEqual(result["sync_outcome"], "owner_rank_sync_unrelated")
            self.assertEqual(result["unrelated_sync_records"], 1)

    def test_tracked_unknown_alias_nested_or_wrong_kind_changes_invalidate(self):
        for path, value, code in (
            (("RankGame", "ranks", 1001019), {}, "owner_rank_sync_relevant"),
            (("uid",), OWNER, "owner_rank_sync_relevant"),
            (("zone_id",), ZONE, "owner_rank_sync_relevant"),
            (("fixture-private-unknown-root",), 1, "owner_rank_sync_root_unreviewed"),
            ((1,), 1, "owner_rank_sync_root_unreviewed"),
            (("client_red_infos", "entity", "RankGame"), 1, "owner_rank_sync_root_unreviewed"),
            (("level", "entity", "uid"), 1, "owner_rank_sync_path_unreviewed"),
            (("score",), {"fixture-private-value": 1}, "owner_rank_sync_value_unreviewed"),
            (("name",), msgpack.ExtType(0, b"fixture-private-ext"), "owner_rank_sync_value_unreviewed")):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            self.project()
            self.observer.observe(event("property", data=sync_message((path, value))))
            result = self.snapshot()
            self.assertFalse(result["ranks_verified"])
            self.assertEqual(result["sync_outcome"], code)
            self.assertEqual(result["initialization_season_count"], 2)

    def test_declared_component_field_replacements_leave_rank_history_intact(self):
        examples = {"map": {"fixture-private-key": "fixture-private-value"},
                    "array": ["fixture-private-value"], "str": "fixture-private-value"}
        for operation in (1, 2, 3):
            for path, kind in owner_rank.UNRELATED_COMPONENT_FIELDS.items():
                self.observer = owner_rank.OwnerRankObserver(OWNER)
                before = self.project()["seasons"]
                self.observer.observe(event("property", data=sync_message((path, examples[kind]), operation=operation)))
                result = self.snapshot()
                self.assertTrue(result["ranks_verified"], path)
                self.assertEqual(result["seasons"], before)
                self.assertEqual(result["sync_root_shapes"], [])

    def test_status_direct_dictionary_entries_not_arbitrary_component_paths(self):
        for operation, value in ((1, {"fixture-private": True}), (3, {"fixture-private": False}), (2, None)):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            self.project()
            self.observer.observe(event("property", data=sync_message(
                (("Status", "state_infos", "fixture-private-key"), value), operation=operation)))
            self.assertTrue(self.snapshot()["ranks_verified"])
        for path, value in (
            (("Status", "entity", "uid"), OWNER),
            (("Career", "entity", "RankGame"), {}),
            (("Traceroute", "rules", "entity", "RankGame"), {}),
            (("Status", "state_infos", "key", "entity", "uid"), OWNER),
            (("League", "__dict__"), {}), (("Status",), {}),
            (("Career", "not_declared"), {}), (("League", "league_regions"), {}),
            (("Status", "state_infos", "key"), msgpack.ExtType(0, b"fixture-private-ext"))):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            self.project()
            self.observer.observe(event("property", data=sync_message((path, value))))
            self.assertFalse(self.snapshot()["ranks_verified"], path)

    def test_mixed_unrelated_and_rank_update_is_all_or_nothing(self):
        self.project()
        self.observer.observe(event("property", data=sync_message(
            (("Traceroute", "rules"), {}), (("RankGame", "ranks"), {}),
            (("League", "league_regions"), []))))
        self.assertFalse(self.snapshot()["ranks_verified"])
        self.assertEqual(self.snapshot()["sync_outcome"], "owner_rank_sync_relevant")

    def test_auxiliary_traceroute_exception_is_exact_not_unknown_field_wildcard(self):
        name = "fixture_auxiliary_field"
        digest = hashlib.sha256(name.encode()).hexdigest()
        with patch.object(owner_rank, "TRACEROUTE_AUX_MAP_FIELD_SHA256", digest):
            self.project()
            self.observer.observe(event("property", data=sync_message((("Traceroute", name), {"fixture-private": 1}))))
            self.assertTrue(self.snapshot()["ranks_verified"])
            for path, operation, value in (
                (("Traceroute", "another_unknown"), 1, {}), (("League", name), 1, {}),
                (("Traceroute", name, "entity"), 1, {}), (("Traceroute", name), 2, {}),
                (("Traceroute", name), 3, {}), (("Traceroute", name), 1, []),
                (("Traceroute", name), 1, msgpack.ExtType(0, b"fixture-private-ext"))):
                self.observer = owner_rank.OwnerRankObserver(OWNER)
                self.project()
                self.observer.observe(event("property", data=sync_message((path, value), operation=operation)))
                self.assertFalse(self.snapshot()["ranks_verified"], path)
        for name in ("entity", "__dict__", "__class__"):
            self.observer = owner_rank.OwnerRankObserver(OWNER)
            with patch.object(owner_rank, "TRACEROUTE_AUX_MAP_FIELD_SHA256", hashlib.sha256(name.encode()).hexdigest()):
                self.project()
                self.observer.observe(event("property", data=sync_message((("Traceroute", name), {}))))
                self.assertFalse(self.snapshot()["ranks_verified"])

    def test_empty_sync_noop_and_unrelated_updates_cannot_restore_stale_rows(self):
        self.project()
        for data in (msgpack.packb([]), sync_message()):
            self.observer.observe(event("property", data=data))
            self.assertTrue(self.snapshot()["ranks_verified"])
        self.observer.observe(event("property", data=sync_message((("RankGame",), {}))))
        self.observer.observe(event("property", data=sync_message((("level",), 33))))
        result = self.snapshot()
        self.assertFalse(result["ranks_verified"])
        self.assertEqual(result["sync_outcome"], "owner_rank_sync_relevant")

    def test_complete_sync_is_checked_before_retaining_snapshot(self):
        self.project()
        data = sync_message((("level",), 32)) + b"fixture-private-trailing"
        self.observer.observe(event("property", data=data))
        self.assertFalse(self.snapshot()["ranks_verified"])
        self.assertEqual(self.snapshot()["unrelated_sync_records"], 0)

    def test_unreviewed_roots_are_hashed_or_bounded_indices_without_path_values(self):
        self.project()
        private_root = "fixture-private-root"
        self.observer.observe(event("property", data=sync_message(
            ((private_root, "fixture-private-field"), "fixture-private-value"),
            ((1 << 40,), 1), ((123,), 2))))
        result = self.snapshot()
        self.assertNotIn(str(1 << 40), json.dumps(result))
        self.assertEqual(result["sync_roots"], ["unreviewed_root"])
        shapes = result["sync_root_shapes"]
        string_shape = next(shape for shape in shapes if shape["kind"] == "str")
        self.assertEqual(string_shape, {"kind": "str", "sha256": hashlib.sha256(private_root.encode()).hexdigest(),
            "index": None, "field_sha256": hashlib.sha256(b"fixture-private-field").hexdigest(),
            "segments": 2, "operation": 1, "value_kind": "str"})
        self.assertEqual({shape["index"] for shape in shapes if shape["kind"] == "int"}, {None, 123})

    def test_full_create_resets_prior_generation_diagnostics(self):
        self.project()
        self.observer.observe(event("property", data=sync_message((("fixture-private-root",), 1))))
        self.assertTrue(self.snapshot()["sync_root_shapes"])
        result = self.project()
        self.assertTrue(result["ranks_verified"])
        self.assertEqual(result["sync_roots"], [])
        self.assertEqual(result["sync_root_shapes"], [])
        self.assertEqual(result["sync_outcome"], "owner_rank_sync_not_observed")

    def test_failed_recreate_starts_clean_diagnostic_generation(self):
        self.project()
        self.observer.observe(event("property", data=sync_message((("fixture-private-root",), 1))))
        self.observer.observe(create(b"fixture-private-malformed"))
        result = self.snapshot()
        self.assertEqual(result["initialization_outcome"], "owner_rank_properties_unavailable")
        self.assertEqual(result["sync_roots"], [])
        self.assertEqual(result["sync_root_shapes"], [])
        self.assertEqual(result["sync_records"], 0)
        self.assertEqual(result["unrelated_sync_records"], 0)
        self.assertEqual(result["sync_outcome"], "owner_rank_sync_not_observed")

    def test_other_uid_create_does_not_overwrite_owner_initialization_evidence(self):
        self.project()
        self.observer.observe(event("property", data=sync_message((("fixture-private-root",), 1))))
        self.observer.observe(create(root(uid=OWNER + 1), eid=EID + 1))
        result = self.snapshot()
        self.assertEqual(result["initialization_outcome"], "owner_rank_snapshot_projected")
        self.assertEqual(result["initialization_season_count"], 2)

    def test_snapshot_is_independent_and_raw_records_are_not_retained(self):
        result = self.project()
        pristine = copy.deepcopy(result)
        result["seasons"][0]["level"] = 65535
        result["seasons"][0]["record"]["wins"] = 100
        self.assertEqual(self.snapshot(), pristine)
        self.assertNotIn("fixture-private", repr(vars(self.observer)))
        self.assertNotIn("RankGame", repr(vars(self.observer)))
        self.assertNotIn("initdata", json.dumps(pristine))

    def test_projection_never_calls_json_provider_files_or_network(self):
        data = root()
        with patch("builtins.open", side_effect=AssertionError("no file reads")), \
                patch("socket.create_connection", side_effect=AssertionError("no network")), \
                patch("standalone_rank_query.project_other_rank_data", side_effect=AssertionError("not JSON route")):
            result = self.project(data)
        self.assertTrue(result["ranks_verified"])


if __name__ == "__main__":
    unittest.main()
