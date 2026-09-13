"""One current mesh UID read with exact identity and section visibility.

Only the supplied accepted owner session is used. Raw user/configuration rows
stay inside the transient callback. This module does not acquire credentials,
load game files/captures, store profiles, or query ranks/history. An explicit
owner-only option adds one source-supported Career aggregate after identity.
"""
from __future__ import annotations

import json

from standalone_social_session import GatewaySocialToken
from standalone_social_transport import run_social_setup_once
from standalone_career_query import CareerQueryError, project_own_career_response
from standalone_rank_query import RankQueryError, project_other_rank_response


MAX_UID = (1 << 64) - 1
PLATFORM_IDS = {"pc": 1, "playstation": 2, "xbox": 4}
OS_PLATFORMS = {value: key for key, value in PLATFORM_IDS.items()}
SECTION_KEYS = {
    "overview": "CareerOverviewIsVisibleToOther",
    "heroes": "CareerHeroDataIsVisibleToOther",
    "history": "BattleHistoryIsVisibleToOther",
    "timeline": "TimelineIsVisibleToOther",
}
ERROR_CODES = frozenset({
    "uid_lookup_inputs_invalid", "uid_lookup_payload_shape", "uid_lookup_no_data",
    "uid_lookup_row_count", "uid_lookup_identity_mismatch",
})


class ProfileLookupError(ValueError):
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "uid_lookup_payload_shape"
        super().__init__(self.code)


def _uid(value):
    if type(value) is not int or not 0 < value <= MAX_UID:
        raise ProfileLookupError("uid_lookup_inputs_invalid")
    return value


def _kind(value):
    return {type(None): "null", str: "string", dict: "object", list: "array",
            int: "integer", float: "number", bool: "boolean"}.get(type(value), "unsupported")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _bad_constant(_value):
    raise ValueError


def _settings(value):
    if type(value) is str:
        if len(value) > 8192:
            return None
        try:
            value = json.loads(value, object_pairs_hook=_unique, parse_constant=_bad_constant)
        except (ValueError, RecursionError):
            return None
    return value if type(value) is dict and len(value) <= 100 else None


def _visibility(settings, section, *, platform, own):
    # Current4677 gives the owner access independently of other-player settings.
    if own:
        return {"state": "allowed", "basis": "owner", "permission": None}
    if settings is None:
        return {"state": "unknown", "basis": "settings_unavailable", "permission": None}
    key = SECTION_KEYS[section]
    platform_key = f"{key}_{PLATFORM_IDS[platform]}"
    if section in ("overview", "heroes") and platform_key in settings:
        value, basis = settings[platform_key], "platform_setting"
    elif key in settings:
        value, basis = settings[key], "global_setting"
    else:
        # Current4677 explicitly uses OPEN=0 for an absent key in a successfully
        # decoded settings map. Preserve that contract, not a fabricated denial.
        return {"state": "allowed", "basis": "native_default", "permission": 0}
    if type(value) is not int or not 0 <= value <= 4:
        return {"state": "unknown", "basis": "setting_unrecognized", "permission": None}
    return {"state": "allowed" if value == 0 else "restricted",
            "basis": basis, "permission": value}


def project_mesh_user(data, *, target_uid, own_uid, zone_id, platform="pc"):
    """Project exactly one requested row, never the session header's owner aid.

    Unknown/missing configuration does not invalidate a correctly bound UID.
    Malformed/missing configuration leaves section evidence unavailable. A valid
    settings map uses the native documented OPEN default for missing keys.
    Relationship permissions remain restricted without proven relationship data.
    """
    target_uid, own_uid, zone_id = _uid(target_uid), _uid(own_uid), _uid(zone_id)
    if type(platform) is not str or platform not in PLATFORM_IDS:
        raise ProfileLookupError("uid_lookup_inputs_invalid")
    if type(data) is not dict or type(data.get("data")) is not list:
        raise ProfileLookupError("uid_lookup_payload_shape")
    rows = data["data"]
    if not rows:
        raise ProfileLookupError("uid_lookup_no_data")
    if len(rows) != 1:
        raise ProfileLookupError("uid_lookup_row_count")
    row = rows[0]
    if type(row) is not dict or row.get("aid") != f"{zone_id}_{target_uid}":
        raise ProfileLookupError("uid_lookup_identity_mismatch")
    name = row.get("name")
    if (type(name) is not str or not name or len(name) > 64
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        name = None
    elif any(0xD800 <= ord(char) <= 0xDFFF for char in name):
        name = None
    # Current1850 PlayerState_Base.set_user:486 assigns level from user.grade.
    grade = row.get("grade")
    level = grade if type(grade) is int and 0 <= grade <= 1_000_000 else None
    config = row.get("config_server")
    config_dict = config if type(config) is dict else {}
    login_os = config_dict.get("login_os")
    if type(login_os) is str and login_os in {"1", "2", "4"}:
        login_os = int(login_os)
    account_platform = OS_PLATFORMS.get(login_os) if type(login_os) is int else None
    raw_settings = config_dict.get("career_settings")
    settings_defaulted = type(config) is dict and "career_settings" not in config
    # Current1838 defaults a missing field within present config_server to '{}'.
    settings = _settings("{}" if settings_defaulted else raw_settings)
    own = target_uid == own_uid
    return {
        "outcome": "uid_profile_identity_verified", "identity_verified": True,
        "uid": str(target_uid), "in_game_name": name, "level": level, "platform": account_platform,
        "requested_platform": platform, "is_owner": own,
        "visibility": {section: _visibility(settings, section, platform=platform, own=own)
                       for section in SECTION_KEYS},
        "shape": {"name_present": name is not None, "grade_type": _kind(grade),
                  "config_server_type": _kind(config),
                  "login_os_type": _kind(config_dict.get("login_os")),
                  "career_settings_type": _kind(raw_settings), "settings_decoded": settings is not None,
                  "settings_defaulted": settings_defaulted},
        "career_verified": False, "ranks_verified": False, "matches_verified": False,
        "secrets_saved": False,
    }


def _not_completed(code="uid_lookup_not_completed"):
    return {"outcome": code, "identity_verified": False, "career_verified": False,
            "ranks_verified": False, "matches_verified": False, "secrets_saved": False}


def _failure_shape(data):
    rows = data.get("data") if type(data) is dict else None
    row = rows[0] if type(rows) is list and len(rows) == 1 else None
    return {"payload_type": _kind(data), "data_type": _kind(rows),
            "row_count": len(rows) if type(rows) is list else None,
            "row_type": _kind(row),
            "aid_type": _kind(row.get("aid")) if type(row) is dict else "unavailable"}


def run_uid_lookup_once(session, *, parameter, target_uid, platform="pc", own_career=False, other_ranks=False):
    """One mesh read, with an explicit optional owner aggregate or other ranks."""
    _uid(target_uid)
    if (type(session) is not GatewaySocialToken or type(platform) is not str
            or platform not in PLATFORM_IDS or type(own_career) is not bool or type(other_ranks) is not bool
            or own_career and (target_uid != session.own_uid or platform != "pc")
            or other_ranks and (own_career or target_uid == session.own_uid)):
        raise ProfileLookupError("uid_lookup_inputs_invalid")
    projected, career_result, rank_result = None, None, None

    def on_setup(connection):
        nonlocal projected, career_result, rank_result
        data = connection.read_mesh_user(target_uid)
        try:
            projected = project_mesh_user(data, target_uid=target_uid, own_uid=session.own_uid,
                                          zone_id=session.zone_id, platform=platform)
        except ProfileLookupError as error:
            projected = _not_completed(error.code)
            projected["shape"] = _failure_shape(data)
        if own_career and projected.get("identity_verified") is True and projected.get("is_owner") is True:
            try:
                career_data = connection.read_own_career()
                career_result = project_own_career_response(career_data, session.own_uid)
            except CareerQueryError as error:
                career_result = _not_completed(error.code)
                career_result.update(http_status=error.http_status, application_code=error.application_code)
            except Exception:
                # The transport has fixed per-section diagnostics. Preserve the
                # earlier verified identity without exporting exception text.
                career_result = _not_completed("own_career_read_failed")
        if other_ranks and projected.get("identity_verified") is True and projected.get("is_owner") is False:
            if projected["visibility"]["overview"]["state"] != "allowed":
                rank_result = _not_completed("other_rank_overview_not_available")
            else:
                try:
                    rank_data = connection.read_other_ranks(target_uid)
                    rank_result = project_other_rank_response(rank_data, target_uid, session.zone_id, platform=platform)
                except RankQueryError as error:
                    rank_result = _not_completed(error.code)
                    rank_result.update(http_status=error.http_status, application_code=error.application_code,
                                       shape=error.shape)
                except Exception:
                    rank_result = _not_completed("other_rank_read_failed")

    report = run_social_setup_once(session, parameter=parameter, on_setup=on_setup)
    # A callback alone is not enough: retain the accepted session and correlated
    # query provenance before any projected profile escapes the callback scope.
    if (report.get("social_session_accepted") is True
            and report.get("setup_consumer_completed") is True
            and report.get("mesh_request_bound") is True and projected is not None):
        report["profile_lookup"] = projected
    else:
        report["profile_lookup"] = _not_completed()
    if own_career:
        if (report.get("social_session_accepted") is True and report.get("setup_consumer_completed") is True
                and report.get("profile_lookup", {}).get("identity_verified") is True
                and report.get("career_reply_bound") is True and career_result is not None):
            report["career_lookup"] = career_result
        else:
            report["career_lookup"] = _not_completed("own_career_not_verified")
    if other_ranks:
        identity_ok = report.get("profile_lookup", {}).get("identity_verified") is True
        if (report.get("social_session_accepted") is True and report.get("setup_consumer_completed") is True
                and identity_ok and rank_result is not None):
            if report.get("rank_reply_bound") is True:
                report["rank_lookup"] = rank_result
                # Returned data need not echo a UID. Require the earlier exact
                # mesh identity PLUS this same-context target request/RPC.
                if rank_result.get("ranks_verified") is True:
                    rank_result.update(identity_verified=True, request_bound=True,
                                       identity_basis="mesh_identity_and_rank_request")
            elif rank_result.get("outcome") == "other_rank_overview_not_available":
                report["rank_lookup"] = rank_result
            else:
                report["rank_lookup"] = _not_completed("other_rank_not_verified")
        else:
            report["rank_lookup"] = _not_completed("other_rank_not_verified")
    return report
