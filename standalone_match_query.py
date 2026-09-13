"""Pure first-page native Career match projection; no files, tokens or sockets.

Call only after game_career_probe.verify_match_summary_args has bound the normal
query_match_summary reply and the caller has checked independent history access.
Current3848703 sources:1838 MosClient_Career.s_get_history_matches:893 uses
match_uid and match_player.dynamic_fields;1712 refresh_rank_dan_level:488 uses
the selected player's rank fields;5500 const_match.EGameModeType:110 is Ranking2.
Basic fields follow career_provider.project_matches without exporting raw rows.
"""
import math


MAX_MATCHES = 50
MAX_COUNT = 10**12
MAX_RANK_POINTS = 1_000_000
RANKED_MODE = 2
RANK_SHAPE_FIELDS = ("dynamic_fields", "new_level", "new_score", "add_score", "rankscore_info")
ERROR_CODES = frozenset({
    "match_inputs_invalid", "match_response_shape", "match_identity_mismatch",
    "match_statistics_shape",
})


class MatchQueryError(ValueError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in ERROR_CODES else "match_response_shape"
        super().__init__(self.code)


def _count(value):
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise MatchQueryError("match_statistics_shape")
    return value


def _match_id(value):
    # Source uses this exact server field as the history dictionary key. No
    # fabricated index/timestamp ID or guessed UUID format substitutes for it.
    if (type(value) is not str or not value or len(value) > 128
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        return None
    try:
        return value if len(value.encode("utf-8")) <= 512 else None
    except UnicodeError:
        return None


def _finite_points(value, *, signed=False):
    if type(value) not in (int, float):
        return False
    lower = -MAX_RANK_POINTS if signed else 0
    # Bound before math.isfinite so arbitrarily large Python integers cannot
    # overflow its float conversion. These bounds are projection limits.
    return lower <= value <= MAX_RANK_POINTS and math.isfinite(value)


def _new_rank_level(value):
    #1712 explicitly int() converts new_level before its positive-level gate.
    # Preserve that conversion for bounded source types, not bool/custom objects.
    if type(value) is int:
        result = value
    elif type(value) is float:
        if not math.isfinite(value) or not -65536 <= value <= 65536:
            return None
        result = int(value)
    elif type(value) is str and 0 < len(value) <= 32:
        try:
            result = int(value)
        except ValueError:
            return None
    else:
        return None
    return result if 0 <= result <= 65535 else None


def _shape_kind(container, key):
    if type(container) is not dict:
        return "unavailable"
    if key not in container:
        return "missing"
    value = container[key]
    return {type(None): "null", bool: "bool", int: "int", float: "float",
            str: "str", dict: "dict", list: "list", bytes: "bytes"}.get(type(value), "other")


def _rank_shape_counts(rows):
    # Fixed expected field names and fixed type labels only. Count raw types
    # before normalization; no values, unknown keys or type names are exported.
    result = {key: {} for key in RANK_SHAPE_FIELDS}
    for row in rows:
        if row["game_mode_id"] != RANKED_MODE:
            continue
        player = row["match_player"]
        fields = player.get("dynamic_fields")
        for key in RANK_SHAPE_FIELDS:
            kind = _shape_kind(player if key == "dynamic_fields" else fields, key)
            result[key][kind] = result[key].get(kind, 0) + 1
    return result


def _rank_change(player, mode):
    if mode != RANKED_MODE:
        return None, "not_ranked"
    fields = player.get("dynamic_fields")
    if type(fields) is not dict:
        return None, "unavailable"
    level = _new_rank_level(fields.get("new_level"))
    score, added = fields.get("new_score"), fields.get("add_score")
    if (level is None or level <= 0
            or not _finite_points(score) or not _finite_points(added, signed=True)):
        return None, "unavailable"
    previous = score - added
    if not _finite_points(previous):
        return None, "unavailable"
    before, after = math.floor(previous), math.floor(score)
    old_level = fields.get("level")
    if type(old_level) is not int or not 0 <= old_level <= 65535:
        old_level = None
    info = fields.get("rankscore_info")
    placements = info.get("fixed_battle_count") if type(info) is dict else None
    if type(placements) is not int or not 0 <= placements <= MAX_COUNT:
        placements = None
    #1712 computes floor(new_score)-floor(new_score-add_score), not floor(add).
    # The placement threshold/qualifying label and rank names are not inferred.
    return {"level_before": old_level, "level_after": level,
            "points_before": before, "points_after": after,
            "points_delta": after - before, "placements_completed": placements}, "verified"


def _project_match(row, target_uid):
    player = row.get("match_player") if type(row) is dict else None
    if (type(player) is not dict or type(player.get("player_uid")) is not int
            or player["player_uid"] != target_uid):
        raise MatchQueryError("match_identity_mismatch")
    season = row.get("match_season")
    valid_season = (type(season) is int and 0 <= season <= 9999) or (
        type(season) is str and 0 < len(season) <= 4
        and season.isascii() and season.isdigit())
    if not valid_season:
        raise MatchQueryError("match_statistics_shape")
    duration = row.get("match_play_duration")
    if (type(duration) not in (int, float) or not 0 <= duration <= 86400
            or not math.isfinite(duration)):
        raise MatchQueryError("match_statistics_shape")
    mode = _count(row.get("game_mode_id"))
    win = player.get("is_win")
    result = "win" if type(win) is int and win == 1 else \
             "loss" if type(win) is int and win == 0 else "unknown"
    rank_change, rank_state = _rank_change(player, mode)
    match_uid = _match_id(row.get("match_uid"))
    return {"match_uid": match_uid, "match_uid_available": match_uid is not None,
            "played_at": _count(row.get("match_time_stamp")), "season": int(season),
            "mode_id": mode, "duration_seconds": round(duration), "result": result,
            "kills": _count(player.get("k")), "deaths": _count(player.get("d")),
            "assists": _count(player.get("a")), "rank_change": rank_change,
            "rank_change_verified": rank_change is not None, "rank_change_state": rank_state}


def project_match_summary_response(data, target_uid):
    """Project one already-correlated page, never infer access or completeness.

    Empty pages are valid only because the caller first checks native request
    context/nonce. Every nonempty row independently requires the exact target.
    Missing required basic values fail; optional IDs/rank changes stay nullable.
    """
    if type(target_uid) is not int or not 0 < target_uid < 2**63:
        raise MatchQueryError("match_inputs_invalid")
    rows = data.get("match_info") if type(data) is dict else None
    if type(rows) is not list or len(rows) > MAX_MATCHES:
        raise MatchQueryError("match_response_shape")
    matches = [_project_match(row, target_uid) for row in rows]
    verified_changes = sum(row["rank_change_verified"] for row in matches)
    return {"outcome": "match_summary_verified", "uid": str(target_uid),
            "identity_verified": True, "matches_verified": True, "matches": matches,
            "match_count": len(matches), "page": 0, "page_size": MAX_MATCHES,
            "has_more": None, "history_completeness": "first_page_only",
            "rank_changes_verified": verified_changes > 0,
            "rank_change_count": verified_changes, "ranks_verified": False,
            "rank_shape_counts": _rank_shape_counts(rows),
            "secrets_saved": False}
