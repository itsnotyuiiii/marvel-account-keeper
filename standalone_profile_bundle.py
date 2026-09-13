"""One bounded UID profile through one owner login and sequential connections.

The caller supplies current reviewed public inputs and transient SAUTH. This
module never loads credentials, game files, captures, or runtime artifacts.
The normal Social connection performs mesh identity and allowed service reads;
the same already-authenticated game connection then performs the normal
owner panel and permitted native other-player reads, sequentially. Only typed
projections escape. No retry, relogin, persistence, or provider fallback exists.
"""
from __future__ import annotations

import hashlib
import ipaddress
import math
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import game_login_probe as login
from game_career_probe import CareerMethods
from game_rank_panel_probe import ERROR_CODES as PANEL_ERRORS, read_rank_panel_once
from game_social_token_probe import run_social_token_once
from game_transport_probe import PublicConfiguration, PARAMETER_SHA256, PUBLIC_KEY_SHA256
from portable_social_runtime import PARAMETER_SHA256 as SOCIAL_PARAMETER_SHA256
from standalone_career_query import ERROR_CODES as CAREER_ERRORS
from standalone_match_query import ERROR_CODES as MATCH_ERRORS
from standalone_native_sections import read_other_career_once, read_other_matches_once
from standalone_profile_probe import ERROR_CODES as IDENTITY_ERRORS, run_uid_lookup_once
from standalone_rank_query import ERROR_CODES as RANK_ERRORS
from standalone_social_session import ERROR_CODES as SESSION_ERRORS, GatewaySocialToken
from standalone_social_transport import ERROR_CODES as SOCIAL_ERRORS
from web_sdk_auth_probe import build_web_login_inputs


REVIEWED_BUILD = "3848703"
NATIVE_SECONDS = 15
MAX_COUNT = 10**12
MAX_POINTS = 1_000_000
ERROR_CODES = frozenset({"profile_bundle_inputs_invalid", "profile_bundle_callback_repeated"})
SECTION_ERRORS = frozenset(CAREER_ERRORS | MATCH_ERRORS | RANK_ERRORS | PANEL_ERRORS) | {
    "own_career_read_failed", "own_career_not_verified", "other_rank_read_failed",
    "own_match_read_failed", "own_match_not_verified",
    "other_rank_not_verified", "other_career_read_failed", "other_career_reply_unverified",
    "other_match_summary_read_failed", "other_match_summary_reply_unverified",
    "profile_native_deadline", "profile_native_connection_unusable", "profile_native_read_failed",
}
VISIBILITY_BASES = frozenset({"owner", "settings_unavailable", "platform_setting",
                            "global_setting", "native_default", "setting_unrecognized"})
NATIVE_ERRORS = frozenset(kind + suffix for kind in ("career", "match_summary") for suffix in (
    "_reply_timeout", "_connection_closed", "_network_error", "_reply_unvalidated", "_record_bound",
    "_receive_bound", "_request_context_mismatch", "_uid_mismatch", "_reply_shape",
    "_result_code_missing", "_rows_shape", "_deadline_invalid"))
TOKEN_OUTCOMES = frozenset({
    "social_token_not_received", "social_token_reader_inputs_invalid", "social_token_receive_bound",
    "social_token_hotfix_bound", "social_token_hotfix_shape", "social_token_unrecognized_startup_command",
    "social_token_argument_shape", "social_token_empty", "social_token_claims_validated",
    "social_token_consumer_failed", "social_token_record_bound", "social_token_claims_rejected",
    "social_token_startup_rejected", "social_token_timeout", "social_token_connection_closed",
    "social_token_network_error", "social_token_reply_unvalidated", "social_token_game_consumer_failed",
})
SETUP_OUTCOMES = SOCIAL_ERRORS | SESSION_ERRORS | {"social_setup_not_started", "social_setup_verified"}
MESH_OUTCOMES = frozenset(code for code in SOCIAL_ERRORS if code.startswith("social_mesh_"))
IDENTITY_OUTCOMES = IDENTITY_ERRORS | {"uid_lookup_not_completed"}


class ProfileBundleError(ValueError):
    def __init__(self, code="profile_bundle_inputs_invalid"):
        self.code = code if type(code) is str and code in ERROR_CODES else "profile_bundle_inputs_invalid"
        super().__init__(self.code)


def _integer(value, low=0, high=MAX_COUNT):
    return value if type(value) is int and low <= value <= high else None


def _points(value):
    return value if (type(value) in (int, float) and 0 <= value <= MAX_POINTS
                     and math.isfinite(value)) else None


def _text(value, limit):
    if (type(value) is not str or not value or len(value) > limit
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        return None
    return value


def _section(status="unavailable", outcome="profile_section_not_verified", data=None):
    return {"status": status, "outcome": outcome, "data": data}


def _failure(raw, fallback):
    if type(raw) is dict:
        native, code = raw.get("native_outcome"), raw.get("server_error_code")
        if type(native) is str and native in ("career_server_error", "match_summary_server_error"):
            if (raw.get("reply_context_verified") is True
                    and _integer(code, -(10**9), 10**9) is not None and code != 0):
                kind = "career" if native == "career_server_error" else "matches"
                result = _section(outcome="profile_" + kind + "_service_rejected")
                result["source_error_code"] = code
                return result
        if type(native) is str and native in NATIVE_ERRORS:
            return _section(outcome=native)
    value = raw.get("outcome") if type(raw) is dict else None
    return _section(outcome=value if type(value) is str and value in SECTION_ERRORS else fallback)


def _fixed_outcome(value, allowed, fallback):
    return value if type(value) is str and value in allowed else fallback


def _social_diagnostics(report, setup):
    """Keep session acceptance distinct from the target query's availability.

    Caller has already verified the owner gateway and delivered token claims.
    Fixed outcomes and a correlated numeric mesh rejection are not a finding
    that a UID is invalid. No upstream message, route, token or raw row escapes.
    """
    if type(setup) is not dict:
        return
    report["social_setup_outcome"] = _fixed_outcome(setup.get("outcome"), SETUP_OUTCOMES,
                                                     "profile_social_setup_outcome_unavailable")
    report["social_session_accepted"] = setup.get("social_session_accepted") is True
    profile = setup.get("profile_lookup")
    identity_code = profile.get("outcome") if type(profile) is dict else None
    report["identity_outcome"] = _fixed_outcome(identity_code, IDENTITY_OUTCOMES, "uid_lookup_not_completed")
    mesh = setup.get("mesh_error")
    if report["identity_outcome"] == "uid_lookup_not_completed":
        report["identity_outcome"] = _fixed_outcome(mesh, MESH_OUTCOMES, "uid_lookup_not_completed")
    code = setup.get("mesh_server_error_code")
    if (report["social_session_accepted"] and mesh == "social_mesh_rejected"
            and setup.get("mesh_query_sent") is True and setup.get("mesh_status_layer") in ("header", "body")
            and _integer(code, -(10**9), 10**9) is not None and code != 0):
        report["mesh_error_code"] = code


def _validate(sauth, configuration, server, parameter, methods, target_uid, version, platform):
    if (type(version) is not str or version != REVIEWED_BUILD or platform != "pc" or type(platform) is not str
            or type(target_uid) is not int or not 0 < target_uid < 2**63
            or type(configuration) is not PublicConfiguration or type(server) is not login.LoginServer
            or type(methods) is not CareerMethods
            or any(_integer(value, 1, 2**31 - 1) is None for value in (methods.request, methods.reply))
            or type(parameter) is not bytes or len(parameter) != 17
            or hashlib.sha256(parameter).hexdigest() != SOCIAL_PARAMETER_SHA256):
        raise ProfileBundleError()
    try:
        host = ipaddress.ip_address(server.host)
        if (type(server.host) is not str or host.version != 4 or not host.is_global
                or _integer(server.host_number, 1, 65535) is None
                or type(server.ports) is not tuple or not 1 <= len(server.ports) <= 32
                or len(set(server.ports)) != len(server.ports)
                or any(_integer(port, 1, 65535) is None for port in server.ports)
                or not isinstance(configuration.public_key, rsa.RSAPublicKey)
                or configuration.public_key.key_size != 1024
                or type(configuration.parameter) is not bytes or len(configuration.parameter) != 18
                or hashlib.sha256(configuration.parameter).hexdigest() != PARAMETER_SHA256):
            raise ValueError
        der = configuration.public_key.public_bytes(serialization.Encoding.DER,
                                                     serialization.PublicFormat.SubjectPublicKeyInfo)
        if hashlib.sha256(der).hexdigest() != PUBLIC_KEY_SHA256:
            raise ValueError
        build_web_login_inputs(sauth, client_version=version, os_name="pc")
    except Exception:
        raise ProfileBundleError() from None


def _identity(setup, target_uid):
    if (type(setup) is not dict or any(setup.get(key) is not True for key in (
            "social_session_accepted", "setup_consumer_completed", "mesh_request_bound"))):
        return None
    profile = setup.get("profile_lookup")
    if (type(profile) is not dict or profile.get("identity_verified") is not True
            or profile.get("uid") != str(target_uid) or profile.get("requested_platform") != "pc"
            or profile.get("is_owner") is not (target_uid == login.EXPECTED_OWN_UID)):
        return None
    visibility = {}
    raw = profile.get("visibility")
    for name in ("overview", "heroes", "history", "timeline"):
        row = raw.get(name) if type(raw) is dict else None
        if (type(row) is dict and row.get("state") in ("allowed", "restricted", "unknown")
                and type(row.get("basis")) is str and row["basis"] in VISIBILITY_BASES
                and (row.get("permission") is None or _integer(row["permission"], 0, 4) is not None)):
            visibility[name] = {"state": row["state"], "basis": row["basis"], "permission": row.get("permission")}
        else:
            visibility[name] = {"state": "unknown", "basis": "settings_unavailable", "permission": None}
    platform = profile.get("platform")
    return {"uid": str(target_uid), "in_game_name": _text(profile.get("in_game_name"), 64),
            "level": _integer(profile.get("level"), 0, 1_000_000),
            "platform": platform if type(platform) is str and platform in ("pc", "playstation", "xbox") else None,
            "requested_platform": "pc", "is_owner": target_uid == login.EXPECTED_OWN_UID,
            "visibility": visibility}


def _permission(identity, section):
    state = identity["visibility"][section]["state"]
    if state == "allowed":
        return None
    return _section("restricted" if state == "restricted" else "unknown", "profile_" + section + "_" + state)


def _career(raw, target_uid, *, own=False):
    if (type(raw) is not dict or raw.get("career_verified") is not True
            or raw.get("identity_verified") is not True or raw.get("uid") != str(target_uid)
            or not own and (raw.get("request_bound") is not True or raw.get("reply_context_verified") is not True)):
        return _failure(raw, "profile_career_not_verified")
    stats = raw.get("statistics")
    if (type(stats) is not dict or any(_integer(stats.get(key)) is None for key in (
            "matches", "wins", "kills", "deaths", "assists")) or stats["wins"] > stats["matches"]
            or raw.get("request_scope") != "unfiltered"):
        return _section(outcome="profile_career_projection_invalid")
    return _section("available", "profile_career_verified", {
        "uid": str(target_uid), "statistics": {key: stats[key] for key in (
            "matches", "wins", "kills", "deaths", "assists")},
        "request_scope": "unfiltered", "history_completeness": "not_established"})


def _rank_row(row):
    if type(row) is not dict:
        return None
    season, level, peak = (_integer(row.get("season"), 1, 999),
                           _integer(row.get("level"), 0, 65535), _integer(row.get("peak_level"), 0, 65535))
    points, peak_points = _points(row.get("points")), _points(row.get("peak_points"))
    if any(value is None for value in (season, level, peak, points, peak_points)):
        return None
    record = row.get("record")
    if (type(record) is dict and _integer(record.get("matches")) is not None
            and _integer(record.get("wins")) is not None and record["wins"] <= record["matches"]):
        record = {"matches": record["matches"], "wins": record["wins"]}
    else:
        record = None
    return {"season": season, "level": level, "points": points, "peak_level": peak, "peak_points": peak_points,
            "updated_at": _integer(row.get("updated_at")),
            "placements_completed": _integer(row.get("placements_completed")), "record": record}


def _ranks(raw, target_uid):
    if (type(raw) is not dict or any(raw.get(key) is not True for key in (
            "ranks_verified", "identity_verified", "request_bound")) or raw.get("uid") != str(target_uid)
            or raw.get("platform") != "pc"):
        return _failure(raw, "profile_ranks_not_verified")
    if raw.get("rank_data_present") is not True:
        return _section("unknown", "profile_rank_data_not_returned")
    rows = raw.get("seasons")
    if type(rows) is not list or len(rows) > 1000:
        return _section(outcome="profile_ranks_projection_invalid")
    projected = [_rank_row(row) for row in rows]
    if any(row is None for row in projected) or len({row["season"] for row in projected}) != len(projected):
        return _section(outcome="profile_ranks_projection_invalid")
    projected.sort(key=lambda row: row["season"], reverse=True)
    return _section("available", "profile_ranks_verified", {"uid": str(target_uid), "platform": "pc",
        "seasons": projected, "season_count": len(projected), "points_basis": "server_rank_score",
        "rank_labels_verified": False, "overall_peak_verified": False, "history_completeness": "not_established"})


def _rank_change(row):
    if type(row) is not dict:
        return None
    after, before = _integer(row.get("points_after"), 0, MAX_POINTS), _integer(row.get("points_before"), 0, MAX_POINTS)
    delta, level = _integer(row.get("points_delta"), -MAX_POINTS, MAX_POINTS), _integer(row.get("level_after"), 1, 65535)
    if any(value is None for value in (after, before, delta, level)) or after - before != delta:
        return None
    return {"level_before": _integer(row.get("level_before"), 0, 65535), "level_after": level,
            "points_before": before, "points_after": after, "points_delta": delta,
            "placements_completed": _integer(row.get("placements_completed"))}


def _matches(raw, target_uid, *, own=False):
    if (type(raw) is not dict or any(raw.get(key) is not True for key in (
            "matches_verified", "identity_verified", "request_bound")) or raw.get("uid") != str(target_uid)
            or not own and raw.get("reply_context_verified") is not True
            or type(raw.get("page")) is not int or raw["page"] != 0
            or type(raw.get("page_size")) is not int or raw["page_size"] != 50):
        return _failure(raw, "profile_matches_not_verified")
    rows = raw.get("matches")
    if type(rows) is not list or len(rows) > 50 or _integer(raw.get("match_count"), 0, 50) != len(rows):
        return _section(outcome="profile_matches_projection_invalid")
    projected = []
    for row in rows:
        if (type(row) is not dict or any(_integer(row.get(key)) is None for key in (
                "played_at", "mode_id", "kills", "deaths", "assists"))
                or _integer(row.get("season"), 0, 9999) is None
                or _integer(row.get("duration_seconds"), 0, 86400) is None
                or row.get("result") not in ("win", "loss", "unknown")):
            return _section(outcome="profile_matches_projection_invalid")
        change = _rank_change(row.get("rank_change")) if row.get("rank_change_verified") is True else None
        projected.append({"uid": str(target_uid), "match_uid": _text(row.get("match_uid"), 128),
            **{key: row[key] for key in ("played_at", "season", "mode_id", "duration_seconds", "result", "kills", "deaths", "assists")},
            "rank_change": change, "rank_change_verified": change is not None})
    return _section("available", "profile_matches_verified", {"uid": str(target_uid), "matches": projected,
        "match_count": len(projected), "page": 0, "page_size": 50, "has_more": None,
        "history_completeness": "first_page_only"})


def _panel(raw):
    if (type(raw) is not dict or any(raw.get(key) is not True for key in (
            "owner_context_valid", "rank_panel_verified", "current_season_verified"))
            or raw.get("uid") != str(login.EXPECTED_OWN_UID)):
        return None
    return _integer(raw.get("current_season"), 1, 999)


def _empty(target_uid):
    return {"outcome": "profile_bundle_unverified", "uid": str(target_uid), "platform": "pc",
        "owner_authentication_verified": False, "game_login_verified": False,
        "social_session_accepted": False, "identity_verified": False, "identity": None,
        "token_outcome": "social_token_not_received", "social_setup_outcome": "social_setup_not_started",
        "identity_outcome": "uid_lookup_not_completed",
        "career_verified": False, "ranks_verified": False, "matches_verified": False,
        "current_season_verified": False, "current_season": None, "current_rank": None,
        "current_rank_source": None, "career": _section(), "ranks": _section(), "matches": _section(),
        "native_connection_reusable": False, "native_outcome": "profile_native_not_completed",
        "panel_outcome": "profile_panel_not_completed",
        "rank_labels_verified": False, "overall_peak_verified": False,
        "secrets_saved": False, "remote_code_executed": False}


def run_profile_bundle(sauth, configuration, server, *, parameter, career_methods, target_uid,
                       client_version=REVIEWED_BUILD, platform="pc"):
    """One bounded PC profile, with separate section permission and provenance.

    Caller owns normal credential renewal. AUTH, gateway, and SocialMatrix each
    use their normal distinct connections; no section opens another login.
    Native panel/Career/matches share a 15-second window and one cipher stream.
    """
    _validate(sauth, configuration, server, parameter, career_methods, target_uid, client_version, platform)
    report = _empty(target_uid)
    setup, panel, native_career, native_matches = None, None, None, None
    consumed, game_consumed, session_zone = False, False, None
    reusable, native_outcome = False, "profile_native_not_completed"
    own = target_uid == login.EXPECTED_OWN_UID

    def on_token(session):
        nonlocal setup, consumed, session_zone
        if consumed:
            raise ProfileBundleError("profile_bundle_callback_repeated")
        consumed = True
        if (type(session) is not GatewaySocialToken or session.own_uid != login.EXPECTED_OWN_UID
                or _integer(session.zone_id, 1, 2**31 - 1) is None):
            raise ProfileBundleError()
        session_zone = session.zone_id
        setup = run_uid_lookup_once(session, parameter=parameter, target_uid=target_uid, platform="pc",
                                    **({"own_career": True, "own_matches": True} if own else {"other_ranks": True}))

    def on_game_connection(connection, transport, zone):
        nonlocal panel, native_career, native_matches, game_consumed, reusable, native_outcome
        if game_consumed:
            raise ProfileBundleError("profile_bundle_callback_repeated")
        game_consumed = True
        identity = _identity(setup, target_uid)
        if (identity is None or type(zone) is not dict or type(zone.get("role_uid")) is not int
                or zone["role_uid"] != login.EXPECTED_OWN_UID or type(zone.get("zone_id")) is not int
                or session_zone is None or zone["zone_id"] != session_zone):
            native_outcome = "profile_native_context_unverified"
            return
        started = time.monotonic()
        if not math.isfinite(started):
            native_outcome = "profile_native_clock_invalid"
            return
        deadline = started + NATIVE_SECONDS
        try:
            panel = read_rank_panel_once(connection, transport, zone, client_version=client_version, deadline=deadline)
        except Exception:
            native_outcome = "profile_panel_read_failed"
            return
        reusable = type(panel) is dict and panel.get("connection_reuse_allowed") is True
        native_outcome = "profile_native_completed" if reusable else "profile_native_connection_unusable"
        if own:
            return
        for section, reader in (("career", read_other_career_once), ("matches", read_other_matches_once)):
            permission = _permission(identity, "overview" if section == "career" else "history")
            if permission is not None:
                continue
            if not reusable:
                result = _section(outcome="profile_native_connection_unusable")
            else:
                now = time.monotonic()
                if not math.isfinite(now) or now >= deadline:
                    result = _section(outcome="profile_native_deadline")
                    native_outcome = "profile_native_deadline"
                else:
                    try:
                        result = reader(connection, transport, zone, career_methods, setup_report=setup,
                                        target_uid=target_uid, deadline=deadline)
                    except Exception:
                        result = _section(outcome="profile_native_read_failed")
                    reusable = type(result) is dict and result.get("connection_reuse_allowed") is True
                    if not reusable:
                        native_outcome = "profile_native_connection_unusable"
            if section == "career":
                native_career = result
            else:
                native_matches = result

    try:
        auth = run_social_token_once(sauth, configuration, server, client_version=client_version,
                                    on_token=on_token, on_game_connection=on_game_connection)
    except Exception:
        return report
    if type(auth) is not dict:
        return report
    report["owner_authentication_verified"] = all(auth.get(key) is True for key in ("auth_service_accepted", "own_role_verified"))
    report["game_login_verified"] = report["owner_authentication_verified"] and auth.get("game_login_verified") is True
    token = auth.get("social_token")
    if type(token) is dict:
        report["token_outcome"] = _fixed_outcome(token.get("outcome"), TOKEN_OUTCOMES,
                                                 "profile_token_outcome_unavailable")
    if (not report["game_login_verified"] or type(token) is not dict
            or any(token.get(key) is not True for key in ("token_rpc_received", "social_token_received",
                "owner_claim_matches", "time_claims_valid", "route_shape_valid"))):
        return report
    _social_diagnostics(report, setup)
    if token.get("consumer_completed") is not True:
        return report
    identity = _identity(setup, target_uid)
    if identity is None:
        return report
    report.update(identity=identity, identity_verified=True, social_session_accepted=True,
                  identity_outcome="uid_profile_identity_verified", outcome="profile_bundle_partial")
    if own:
        report["career"] = (_career(setup.get("career_lookup"), target_uid, own=True)
                            if setup.get("career_reply_bound") is True else _section(outcome="profile_career_not_verified"))
        report["ranks"] = _section(outcome="profile_owner_rank_history_not_implemented")
        report["matches"] = (_matches(setup.get("match_lookup"), target_uid, own=True)
            if setup.get("own_match_reply_bound") is True
            else _failure(setup.get("match_lookup"), "profile_matches_not_verified"))
    else:
        report["ranks"] = _permission(identity, "overview") or (
            _ranks(setup.get("rank_lookup"), target_uid) if setup.get("rank_reply_bound") is True
            else _failure(setup.get("rank_lookup"), "profile_ranks_not_verified"))
        report["career"] = _permission(identity, "overview") or _section(outcome="profile_career_not_verified")
        report["matches"] = _permission(identity, "history") or _section(outcome="profile_matches_not_verified")
    if token.get("on_game_connection_completed") is True and game_consumed:
        report.update(native_connection_reusable=reusable, native_outcome=native_outcome)
        season = _panel(panel)
        panel_code = panel.get("outcome") if type(panel) is dict else None
        report["panel_outcome"] = ("owner_rank_panel_verified" if season is not None
            else panel_code if type(panel_code) is str and panel_code in PANEL_ERRORS else "profile_panel_not_verified")
        if season is not None:
            report.update(current_season=season, current_season_verified=True)
        if own and season is not None and type(panel.get("current")) is dict:
            current = panel["current"]
            level = _integer(current.get("level"), 0, 65535)
            if type(current.get("season")) is int and current["season"] == season and level is not None:
                points = (_points(current.get("points")) if current.get("points_verified") is True
                          and panel.get("own_rank_verified") is True else None)
                report.update(current_rank={"season": season, "level": level, "points": points,
                    "points_basis": "server_rank_score", "points_verified": points is not None}, current_rank_source="owner_panel")
        if not own:
            if _permission(identity, "overview") is None:
                report["career"] = _career(native_career, target_uid)
            if _permission(identity, "history") is None:
                report["matches"] = _matches(native_matches, target_uid)
    for name in ("career", "ranks", "matches"):
        report[name + "_verified"] = report[name]["status"] == "available"
    if not own and report["ranks_verified"] and report["current_season_verified"]:
        current = next((row for row in report["ranks"]["data"]["seasons"]
                        if row["season"] == report["current_season"]), None)
        if current is not None:
            report.update(current_rank={**current, "points_basis": "server_rank_score"}, current_rank_source="target_season_history")
    if all(report[key] is True for key in ("identity_verified", "career_verified", "ranks_verified",
                                          "matches_verified", "current_season_verified")):
        report["outcome"] = "profile_bundle_verified"
    return report
