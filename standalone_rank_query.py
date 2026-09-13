"""Pure current-build other-player namecard rank request and numeric projection.

Current3848703:1838 c_request_rank_data:479 sends full GetData for OTHER
players; its own branch only reads synchronized RankGame state.103/3520 supply
the normal XYQ wrapper.5535 defines OS/type/season keys,5523 platform IDs.
No capture, game files, credentials, sockets or table-derived labels are used.
The caller must establish accepted-session/RPC/target/overview provenance.
"""
import base64
import json
import math
import re

from standalone_career_query import CareerQueryError, decode_xyq_response

MAX_JSON_BYTES = 1024 * 1024
MAX_SEASONS = 100
MAX_COUNT = 10**12
MAX_POINTS = 1_000_000
PLATFORM_OS = {"pc": 1, "playstation": 2, "xbox": 4}
NORMAL_RANK_TYPE = 1
ERROR_CODES = frozenset({
    "rank_inputs_invalid", "rank_response_shape", "rank_response_bound",
    "rank_response_json", "rank_season_identity_mismatch", "rank_statistics_shape",
    "rank_service_mismatch", "rank_http_rejected", "rank_application_rejected", "rank_response_encoding",
})
SHAPE_TYPES = frozenset({"missing", "null", "bool", "int", "float", "str", "dict", "list", "bytes", "other"})
ROW_FIELDS = ("rank_game_id", "level", "rank_score", "max_level", "max_rank_score",
              "update_time", "fixed_battle_count", "battle_count", "win_count")
SHAPE_KEYS = frozenset({"data_type", "rank_data_type", "rank_data_chars", "collection_count",
    "selected_count", "row_index", "row_type", "row_chars", "row_field_count",
    "service_type", "status_code_type", "response_body_type", "response_body_chars"}
    | {name + "_type" for name in ROW_FIELDS})


class RankQueryError(ValueError):
    def __init__(self, code, *, shape=None, http_status=None, application_code=None):
        self.code = code if type(code) is str and code in ERROR_CODES else "rank_response_shape"
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.application_code = (application_code if type(application_code) is int
                                 and -(10**9) <= application_code <= 10**9 else None)
        self.shape = _safe_shape(shape)
        super().__init__(self.code)


def _safe_shape(shape):
    if type(shape) is not dict:
        return {}
    return {key: value for key, value in shape.items() if type(key) is str and key in SHAPE_KEYS
            and ((type(value) is str and value in SHAPE_TYPES)
                 or (type(value) is int and 0 <= value <= MAX_JSON_BYTES + 1))}


def _kind(container, key=None):
    if key is not None:
        if type(container) is not dict or key not in container:
            return "missing"
        container = container[key]
    return {type(None): "null", bool: "bool", int: "int", float: "float", str: "str",
            dict: "dict", list: "list", bytes: "bytes"}.get(type(container), "other")


def _uid(value):
    if type(value) is not int or not 0 < value < 2**64:
        raise RankQueryError("rank_inputs_invalid")
    return value


def make_other_rank_request(target_uid, zone_id, own_uid):
    """Normal full namecard read; host INSIDE post_body is the zone integer."""
    target_uid, zone_id, own_uid = _uid(target_uid), _uid(zone_id), _uid(own_uid)
    if target_uid == own_uid:
        raise RankQueryError("rank_inputs_invalid")
    body = {"host": zone_id, "tag": "player", "id": f"{zone_id}_{target_uid}"}
    return {"service": "xyq-namecard", "headers": {
        "Content-Type": "application/json", "Accept-Encoding": "gzip"},
        "uri": "GetData", "method": "POST", "querystring": None,
        "post_body": base64.b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8")).decode("ascii"),
        "host": None, "protocol": None, "version": None}


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _bad_constant(_value):
    raise ValueError


def _json_object(value):
    #1838 explicitly json.loads both layers. Do not silently reinterpret a
    #different encoding or treat malformed/missing row values as empty ranks.
    if type(value) is not str:
        raise RankQueryError("rank_response_shape")
    if not value or len(value) > MAX_JSON_BYTES:
        raise RankQueryError("rank_response_bound")
    try:
        if len(value.encode("utf-8")) > MAX_JSON_BYTES:
            raise RankQueryError("rank_response_bound")
        result = json.loads(value, object_pairs_hook=_unique, parse_constant=_bad_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        if type(error) is RankQueryError:
            raise
        raise RankQueryError("rank_response_json") from None
    if type(result) is not dict:
        raise RankQueryError("rank_response_shape")
    pending, count = [(result, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 8192 or depth > 16:
            raise RankQueryError("rank_response_bound")
        if type(item) is dict:
            if len(item) > 512:
                raise RankQueryError("rank_response_bound")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            if len(item) > 1024:
                raise RankQueryError("rank_response_bound")
            pending.extend((child, depth + 1) for child in item)
    return result


def _numeric_code(value, maximum):
    if (type(value) not in (int, float) or not 0 <= value <= maximum
            or not math.isfinite(value) or int(value) != value):
        return None
    return int(value)


def _level(value):
    result = _numeric_code(value, 65535)
    if result is None:
        raise RankQueryError("rank_statistics_shape")
    return result


def _points(value):
    # Bounds precede isfinite to avoid huge-int float-conversion overflow.
    if (type(value) not in (int, float) or not 0 <= value <= MAX_POINTS
            or not math.isfinite(value)):
        raise RankQueryError("rank_statistics_shape")
    return value


def _optional_count(value):
    return value if type(value) is int and 0 <= value <= MAX_COUNT else None


def _record(row):
    matches, wins = _optional_count(row.get("battle_count")), _optional_count(row.get("win_count"))
    return {"matches": matches, "wins": wins} if matches is not None and wins is not None and wins <= matches else None


def project_other_rank_data(data, target_uid, zone_id, platform="pc"):
    """Project numeric seasons after caller-verified service/RPC/target binding.

    The normal response handler establishes no aid/uid/id/host/tag echo schema.
    Consequently supplied target UID is request context, never an invented
    response identity. Unknown identity-like fields are neither trusted nor
    exported. Current season and normalized all-season peak remain unavailable.
    """
    shape = {"data_type": _kind(data), "rank_data_type": _kind(data, "rank_game_season")}
    if type(data) is dict and type(data.get("rank_game_season")) is str:
        shape["rank_data_chars"] = min(len(data["rank_game_season"]), MAX_JSON_BYTES + 1)
    try:
        return _project_rank_data(data, target_uid, zone_id, platform, shape)
    except RankQueryError as error:
        error.shape = _safe_shape(shape)
        raise


def _project_rank_data(data, target_uid, zone_id, platform, shape):
    target_uid, zone_id = _uid(target_uid), _uid(zone_id)
    if type(platform) is not str or platform not in PLATFORM_OS:
        raise RankQueryError("rank_inputs_invalid")
    if type(data) is not dict or len(data) > 512:
        raise RankQueryError("rank_response_shape")
    #1838 s_request_rank_data:499 uses the '{}' default only for a missing key.
    ranks = _json_object(data.get("rank_game_season", "{}"))
    shape.update(collection_count=len(ranks), selected_count=0)
    if len(ranks) > MAX_SEASONS:
        raise RankQueryError("rank_response_bound")
    seasons = []
    for index, (key, encoded) in enumerate(ranks.items()):
        if type(key) is not str or re.fullmatch(r"[1-9][0-9]{0,8}", key) is None:
            raise RankQueryError("rank_season_identity_mismatch")
        os_id, rest = divmod(int(key), 1_000_000)
        rank_type, season = divmod(rest, 1000)
        if os_id != PLATFORM_OS[platform] or rank_type != NORMAL_RANK_TYPE:
            continue
        if season == 0:
            raise RankQueryError("rank_season_identity_mismatch")
        shape.update(row_index=index, row_type=_kind(encoded))
        if type(encoded) is str:
            shape["row_chars"] = min(len(encoded), MAX_JSON_BYTES + 1)
        row = _json_object(encoded)
        shape["row_field_count"] = len(row)
        shape.update({name + "_type": _kind(row, name) for name in ROW_FIELDS})
        if _numeric_code(row.get("rank_game_id"), 999) != season:
            raise RankQueryError("rank_season_identity_mismatch")
        seasons.append({
            "season": season, "level": _level(row.get("level")),
            "points": _points(row.get("rank_score")),
            "peak_level": _level(row.get("max_level")),
            "peak_points": _points(row.get("max_rank_score")),
            "updated_at": _optional_count(row.get("update_time")),
            "placements_completed": _optional_count(row.get("fixed_battle_count")),
            "record": _record(row),
        })
        shape["selected_count"] = len(seasons)
    seasons.sort(key=lambda row: row["season"], reverse=True)
    return {"outcome": "other_rank_history_projected", "uid": str(target_uid),
            "platform": platform, "ranks_verified": True, "seasons": seasons,
            "season_count": len(seasons), "rank_data_present": "rank_game_season" in data,
            "current_season": None, "current": None, "peak": None,
            "current_season_verified": False, "overall_peak_verified": False,
            "points_basis": "server_rank_score", "rank_labels_verified": False,
            "identity_basis": "caller_bound_request", "response_identity_echo_verified": False,
            "shape": _safe_shape(shape),
            "history_completeness": "not_established", "secrets_saved": False}


def project_other_rank_response(wrapper, target_uid, zone_id, platform="pc"):
    """Decode the standard XYQ response before the numeric rank projection."""
    _uid(target_uid)
    _uid(zone_id)
    if type(platform) is not str or platform not in PLATFORM_OS:
        raise RankQueryError("rank_inputs_invalid")
    try:
        data, status_source = decode_xyq_response(wrapper, "xyq-namecard")
    except CareerQueryError as error:
        code = "rank_" + error.code.removeprefix("xyq_")
        shape = {name + "_type": _kind(wrapper, name)
                 for name in ("service", "status_code", "response_body")}
        if type(wrapper) is dict and type(wrapper.get("response_body")) is str:
            shape["response_body_chars"] = min(len(wrapper["response_body"]), MAX_JSON_BYTES + 1)
        raise RankQueryError(code, shape=shape, http_status=error.http_status,
                             application_code=error.application_code) from None
    result = project_other_rank_data(data, target_uid, zone_id, platform)
    result.update(http_status=200, application_status_source=status_source)
    return result
