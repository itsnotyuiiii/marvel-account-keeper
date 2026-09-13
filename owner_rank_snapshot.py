"""Bounded owner-only rank projection from named native startup properties.

Candidate contract for reviewed build3848703, not a live delivery claim:
GameServiceAvatar's literal uid/zone_id/RankGame fields; nested Ext0 descriptor
RankGameComponent.ranks. Current4672 get_opened_season_ranks:444 only copies
the ranks mapping (or {}), with no extra season filter. Current5535 constructs
integer OS*1000000 + rank_type*1000 + season keys;5439/4672 consume ordinary
rank-row mappings with .get. This supports integer keys and plain mapping rows
only. String keys, numeric property indices and extension row classes are NOT
inferred from the separate namecard JSON representation.

Only PC1/normal1 season rows are retained. No SDK/authentication, sockets, file
reads, current-season inference, display labels, normalized overall peak or
history-completeness claim. The caller must separately establish owner login
and pass its verified zone to snapshot(). Initial entity EID is NOT player UID.
Unsupported selected-entity updates invalidate rank data, never other readers.
No raw initialization bytes, property maps or remote errors leave this module.
"""

import math

import game_career_probe as career
import game_property_codec as codec
from standalone_rank_query import MAX_COUNT, MAX_POINTS, MAX_SEASONS


REVIEWED_BUILD = "3848703"
ROOT_CLASS = "GameServiceAvatar"
RANK_CLASS = "RankGameComponent"
MAX_EVENTS = 256
MAX_BYTES = 4 * 1024 * 1024
MAX_OWNER_ENTITIES = 16
MAX_ROW_FIELDS = 512
REQUIRED_ROW_FIELDS = ("rank_game_id", "level", "rank_score", "max_level", "max_rank_score")
OUTCOMES = frozenset({
    "owner_rank_inputs_invalid", "owner_rank_not_observed", "owner_rank_snapshot_projected",
    "owner_rank_snapshot_invalid", "owner_rank_properties_unavailable", "owner_rank_fields_missing",
    "owner_rank_owner_mismatch", "owner_rank_zone_mismatch", "owner_rank_zone_invalid",
    "owner_rank_map_missing", "owner_rank_map_unsupported", "owner_rank_row_unsupported",
    "owner_rank_row_field_missing", "owner_rank_season_identity_mismatch", "owner_rank_statistics_invalid",
    "owner_rank_bound", "owner_rank_entity_destroyed", "owner_rank_entity_recreated",
    "owner_rank_entities_ambiguous", "owner_rank_property_update_unhandled", "owner_rank_sequence_reset",
})


class OwnerRankError(ValueError):
    def __init__(self, outcome):
        self.outcome = outcome if type(outcome) is str and outcome in OUTCOMES else "owner_rank_snapshot_invalid"
        super().__init__(self.outcome)


def _positive(value, bound):
    return type(value) is int and 0 < value <= bound


def _code(value, maximum):
    if (type(value) not in (int, float) or not 0 <= value <= maximum
            or not math.isfinite(value) or int(value) != value):
        raise OwnerRankError("owner_rank_statistics_invalid")
    return int(value)


def _points(value):
    if (type(value) not in (int, float) or not 0 <= value <= MAX_POINTS or not math.isfinite(value)):
        raise OwnerRankError("owner_rank_statistics_invalid")
    return value


def _count(value):
    return value if type(value) is int and 0 <= value <= MAX_COUNT else None


def _project_ranks(ranks):
    if type(ranks) is not dict:
        raise OwnerRankError("owner_rank_map_unsupported")
    if len(ranks) > MAX_SEASONS:
        raise OwnerRankError("owner_rank_bound")
    result = []
    for key, row in ranks.items():
        # Native get_rank_key returns an integer, not a decimal string. Never
        # silently normalize a different wire representation or bool key.
        if not _positive(key, 999_999_999):
            raise OwnerRankError("owner_rank_season_identity_mismatch")
        os_id, remaining = divmod(key, 1_000_000)
        rank_type, season = divmod(remaining, 1000)
        if os_id != 1 or rank_type != 1:
            continue
        if season == 0:
            raise OwnerRankError("owner_rank_season_identity_mismatch")
        if type(row) is not dict:
            raise OwnerRankError("owner_rank_row_unsupported")
        if len(row) > MAX_ROW_FIELDS:
            raise OwnerRankError("owner_rank_bound")
        if any(name not in row for name in REQUIRED_ROW_FIELDS):
            raise OwnerRankError("owner_rank_row_field_missing")
        try:
            supplied_season = _code(row["rank_game_id"], 999)
        except OwnerRankError:
            raise OwnerRankError("owner_rank_season_identity_mismatch") from None
        if supplied_season != season:
            raise OwnerRankError("owner_rank_season_identity_mismatch")
        matches, wins = _count(row.get("battle_count")), _count(row.get("win_count"))
        record = ({"matches": matches, "wins": wins}
                  if matches is not None and wins is not None and wins <= matches else None)
        result.append({"season": season, "level": _code(row["level"], 65535),
                       "points": _points(row["rank_score"]), "peak_level": _code(row["max_level"], 65535),
                       "peak_points": _points(row["max_rank_score"]),
                       "updated_at": _count(row.get("update_time")),
                       "placements_completed": _count(row.get("fixed_battle_count")), "record": record})
    result.sort(key=lambda row: row["season"], reverse=True)
    return result


def _copy_rows(rows):
    return [dict(row, record=None if row["record"] is None else row["record"].copy()) for row in rows]


class OwnerRankObserver:
    """Per-connection passive observer; no callback exception escapes observe.

    A new full create can recover an invalidated snapshot. Record/byte bound
    exhaustion is terminal for this observer, including across reset events.
    Multiple live entities decoded as this owner are ambiguous; destruction
    and a subsequent full unambiguous create are required to recover. Never
    restore a saved earlier generation after destroy/recreate or update.
    """

    def __init__(self, owner_uid, *, client_version=REVIEWED_BUILD):
        if (not _positive(owner_uid, (1 << 63) - 1)
                or type(client_version) is not str or client_version != REVIEWED_BUILD):
            raise OwnerRankError("owner_rank_inputs_invalid")
        self._owner_uid = owner_uid
        self._owner_entities = set()
        self._active_eid = None
        self._zone_id = None
        self._rows = None
        self._outcome = "owner_rank_not_observed"
        self._decode_outcome = None
        self._initialization_outcome = "owner_rank_not_observed"
        self._initialization_decode_outcome = None
        self._events = 0
        self._bytes = 0
        self._bounded = False

    def __repr__(self):
        return "<OwnerRankObserver>"

    def _invalidate(self, outcome, *, decode_outcome=None):
        self._rows, self._zone_id = None, None
        self._outcome = outcome if type(outcome) is str and outcome in OUTCOMES else "owner_rank_snapshot_invalid"
        self._decode_outcome = (decode_outcome if type(decode_outcome) is str
                                and decode_outcome in codec.ERROR_CODES else None)

    def observe(self, event):
        """Accept a trusted reader's typed lifecycle event, never raw reports."""
        if self._bounded:
            return
        try:
            self._observe(event)
        except codec.PropertyCodecError as error:
            self._invalidate("owner_rank_properties_unavailable", decode_outcome=getattr(error, "code", None))
        except OwnerRankError as error:
            self._invalidate(getattr(error, "outcome", None))
        except Exception:
            # Decoder/callback errors can contain private initialization bytes.
            # Never stringify them or let this optional observer abort readers.
            self._invalidate("owner_rank_snapshot_invalid")
        finally:
            if (type(event) is career.StartupEvent and event.kind == "create"
                    and event.classname == ROOT_CLASS):
                # Diagnostic only: preserve the initial decoder result when a
                # later unsupported sync invalidates it. This never preserves
                # the old rows or promotes them back into available history.
                self._initialization_outcome = self._outcome
                self._initialization_decode_outcome = self._decode_outcome

    def _observe(self, event):
        if (type(event) is not career.StartupEvent or type(event.kind) is not str
                or event.kind not in {"create", "destroy", "property", "reset"}):
            raise OwnerRankError("owner_rank_snapshot_invalid")
        self._events += 1
        if type(event.data) is bytes:
            self._bytes += len(event.data)
        elif event.data is not None:
            raise OwnerRankError("owner_rank_snapshot_invalid")
        if self._events > MAX_EVENTS or self._bytes > MAX_BYTES:
            self._bounded = True
            raise OwnerRankError("owner_rank_bound")
        if event.kind == "reset":
            self._owner_entities.clear()
            self._active_eid = None
            self._invalidate("owner_rank_sequence_reset")
            return
        if not _positive(event.entity_id, (1 << 63) - 1):
            raise OwnerRankError("owner_rank_snapshot_invalid")
        eid = event.entity_id
        if event.kind == "destroy":
            known = eid in self._owner_entities or eid == self._active_eid
            self._owner_entities.discard(eid)
            if eid == self._active_eid:
                self._active_eid = None
            if known:
                self._invalidate("owner_rank_entity_destroyed")
            return
        if event.kind == "property":
            if eid in self._owner_entities or eid == self._active_eid:
                self._invalidate("owner_rank_property_update_unhandled")
            return
        # Every same-EID creation begins a new generation, even when its new
        # class/identity/properties are unsupported. Do this BEFORE decoding.
        if eid in self._owner_entities or eid == self._active_eid:
            self._owner_entities.discard(eid)
            if eid == self._active_eid:
                self._active_eid = None
            self._invalidate("owner_rank_entity_recreated")
        if type(event.classname) is not str or event.classname != ROOT_CLASS:
            return
        if type(event.data) is not bytes:
            raise OwnerRankError("owner_rank_snapshot_invalid")
        root = codec.decode_named_fields(event.data, expected_class=ROOT_CLASS,
                                          wanted_names=("uid", "zone_id", "RankGame")).fields
        if type(root) is not dict or "uid" not in root:
            raise OwnerRankError("owner_rank_fields_missing")
        if type(root["uid"]) is not int or root["uid"] != self._owner_uid:
            # Another entity does not overwrite the selected owner's proof.
            if self._rows is None:
                self._invalidate("owner_rank_owner_mismatch")
            return
        self._owner_entities.add(eid)
        if len(self._owner_entities) > MAX_OWNER_ENTITIES:
            self._bounded = True
            raise OwnerRankError("owner_rank_bound")
        if len(self._owner_entities) != 1:
            self._active_eid = None
            raise OwnerRankError("owner_rank_entities_ambiguous")
        self._active_eid = eid
        if "zone_id" not in root or "RankGame" not in root:
            raise OwnerRankError("owner_rank_fields_missing")
        if not _positive(root["zone_id"], (1 << 31) - 1):
            raise OwnerRankError("owner_rank_zone_invalid")
        component = codec.decode_named_fields(root["RankGame"], expected_class=RANK_CLASS,
                                               wanted_names=("ranks",)).fields
        if type(component) is not dict or "ranks" not in component:
            raise OwnerRankError("owner_rank_map_missing")
        rows = _project_ranks(component["ranks"])
        self._rows, self._zone_id = rows, root["zone_id"]
        self._outcome, self._decode_outcome = "owner_rank_snapshot_projected", None

    def snapshot(self, *, zone_id):
        """Return fresh fixed metadata/season rows after caller-zone consistency.

        owner_identity_verified means decoded root UID and supplied zone match;
        it is NOT an authentication or request/response-correlation assertion.
        """
        result = {"outcome": self._outcome, "decode_outcome": self._decode_outcome,
                  "initialization_outcome": self._initialization_outcome,
                  "initialization_decode_outcome": self._initialization_decode_outcome,
                  "uid": str(self._owner_uid), "platform": "pc", "owner_identity_verified": False,
                  "ranks_verified": False, "rank_data_present": False, "seasons": [], "season_count": 0,
                  "current_season": None, "current": None, "peak": None,
                  "current_season_verified": False, "overall_peak_verified": False,
                  "rank_labels_verified": False, "points_basis": "server_rank_score",
                  "identity_basis": "owner_initial_properties", "history_completeness": "not_established",
                  "secrets_saved": False, "remote_code_executed": False}
        if not _positive(zone_id, (1 << 31) - 1):
            result["outcome"] = "owner_rank_zone_invalid"
            return result
        if self._rows is None:
            return result
        if self._zone_id != zone_id:
            result["outcome"] = "owner_rank_zone_mismatch"
            return result
        result.update(owner_identity_verified=True, ranks_verified=True, rank_data_present=True,
                      seasons=_copy_rows(self._rows), season_count=len(self._rows))
        return result
