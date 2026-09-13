"""Strict HTTP allowlist for the native bundle; no authentication or network.

This emits the website's private schemaVersion2 input, not raw game/SDK data.
Unknown fields, service messages and callbacks never enter the envelope. The
website applies a second projection before returning its public card contract.
"""
from datetime import datetime, timezone
import json
import math

from web_sdk_auth_probe import EXPECTED_OWNER_UID


class WebProfileError(ValueError):
    def __init__(self, code="profile_shape_invalid"):
        self.code = code if code in {
            "profile_shape_invalid", "profile_unverified", "profile_identity_mismatch",
            "profile_response_too_large",
        } else "profile_shape_invalid"
        super().__init__(self.code)


def _object(value):
    if type(value) is not dict:
        raise WebProfileError()
    return value


def _integer(value, low=0, high=10**12, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not int or not low <= value <= high:
        raise WebProfileError()
    return value


def _score(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10**6:
        raise WebProfileError()
    return value


def _text(value, limit, *, nullable=False):
    if value is None and nullable:
        return None
    if (type(value) is not str or not value.strip() or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise WebProfileError()
    return value


def _bound(value, uid):
    row = _object(value)
    if row.get("uid") != uid:
        raise WebProfileError("profile_identity_mismatch")
    return row


def _permission(value):
    row = _object(value)
    state = row.get("state")
    if state not in ("allowed", "restricted", "unknown"):
        raise WebProfileError()
    return {"state": state}


def _timestamp(value, now, *, nullable=False):
    stamp = _integer(value, 1, 10**12, nullable=nullable)
    if stamp is not None and stamp > now + 300:
        raise WebProfileError()
    return stamp


def _rank(row, now):
    row = _object(row)
    updated = row.get("updated_at")
    # Zero is allowed by the source count schema, but is not a usable event
    # timestamp. Expose unknown rather than losing the verified season row.
    if type(updated) is int and updated == 0:
        updated = None
    record = row.get("record")
    if record is not None:
        record = _object(record)
        record = {name: _integer(record.get(name)) for name in ("matches", "wins")}
        if record["wins"] > record["matches"]:
            raise WebProfileError()
    return {"season": _integer(row.get("season"), 1, 999), "level": _integer(row.get("level"), 0, 65535),
            "points": _score(row.get("points")), "peak_level": _integer(row.get("peak_level"), 0, 65535),
            "peak_points": _score(row.get("peak_points")), "updated_at": _timestamp(updated, now, nullable=True),
            "placements_completed": _integer(row.get("placements_completed"), nullable=True), "record": record}


def _match(row, uid, now):
    row = _bound(row, uid)
    match_uid = _text(row.get("match_uid"), 128, nullable=True)
    change = None
    # Native history permits a missing stable match ID. Keep the basic match,
    # but do not publish a rank change that the website cannot bind to that ID.
    if row.get("rank_change_verified") is True and match_uid is not None:
        change = _object(row.get("rank_change"))
        change = {"level_before": _integer(change.get("level_before"), 0, 65535, nullable=True),
                  "level_after": _integer(change.get("level_after"), 1, 65535),
                  "points_before": _integer(change.get("points_before"), 0, 10**6),
                  "points_after": _integer(change.get("points_after"), 0, 10**6),
                  "points_delta": _integer(change.get("points_delta"), -(10**6), 10**6),
                  "placements_completed": _integer(change.get("placements_completed"), nullable=True)}
        if change["points_after"] - change["points_before"] != change["points_delta"]:
            raise WebProfileError()
    result = row.get("result")
    if result not in ("win", "loss", "unknown"):
        raise WebProfileError()
    return {"uid": uid, "match_uid": match_uid, "played_at": _timestamp(row.get("played_at"), now),
            "season": _integer(row.get("season"), 0, 9999), "mode_id": _integer(row.get("mode_id")),
            "duration_seconds": _integer(row.get("duration_seconds"), 0, 86400), "result": result,
            **{name: _integer(row.get(name)) for name in ("kills", "deaths", "assists")},
            "rank_change_verified": change is not None, "rank_change": change}


def _section(report, name, permission, uid, now):
    if permission != "allowed":
        return {"status": permission, "data": None}
    section = _object(report.get(name))
    if section.get("status") != "available":
        if section.get("status") not in ("restricted", "unknown", "unavailable"):
            raise WebProfileError()
        return {"status": section["status"], "data": None}
    if report.get(name + "_verified") is not True:
        raise WebProfileError("profile_unverified")
    data = _bound(section.get("data"), uid)
    if name == "career":
        if data.get("request_scope") != "unfiltered":
            raise WebProfileError()
        stats = _object(data.get("statistics"))
        stats = {key: _integer(stats.get(key)) for key in ("matches", "wins", "kills", "deaths", "assists")}
        if stats["wins"] > stats["matches"]:
            raise WebProfileError()
        projected = {"uid": uid, "request_scope": "unfiltered", "statistics": stats}
    elif name == "ranks":
        rows = data.get("seasons")
        if (data.get("platform") != "pc" or data.get("points_basis") != "server_rank_score"
                or type(rows) is not list or len(rows) > 100
                or _integer(data.get("season_count")) != len(rows)):
            raise WebProfileError()
        rows = [_rank(row, now) for row in rows]
        if len({row["season"] for row in rows}) != len(rows):
            raise WebProfileError()
        projected = {"uid": uid, "platform": "pc", "points_basis": "server_rank_score", "seasons": rows, "season_count": len(rows)}
    else:
        rows = data.get("matches")
        if (type(data.get("page")) is not int or data["page"] != 0
                or type(data.get("page_size")) is not int or data["page_size"] != 50
                or data.get("has_more", True) is not None or data.get("history_completeness") != "first_page_only"
                or type(rows) is not list or len(rows) > 50 or _integer(data.get("match_count")) != len(rows)):
            raise WebProfileError()
        rows = [_match(row, uid, now) for row in rows]
        ids = [row["match_uid"] for row in rows if row["match_uid"] is not None]
        if len(set(ids)) != len(ids):
            raise WebProfileError()
        projected = {"uid": uid, "matches": rows, "match_count": len(rows), "page": 0,
                     "page_size": 50, "has_more": None, "history_completeness": "first_page_only"}
    return {"status": "available", "data": projected}


def project_profile_envelope(profile, uid, fetched_at):
    """Return bounded schemaVersion2 JSON data or a fixed, non-secret error."""
    try:
        target = str(_integer(uid, 1, 2**63 - 1))
        if type(fetched_at) is not datetime or fetched_at.tzinfo is None:
            raise WebProfileError()
        now = fetched_at.timestamp()
        if not math.isfinite(now) or now <= 0:
            raise WebProfileError()
        report = _bound(profile, target)
        if any(report.get(key) is not True for key in (
                "owner_authentication_verified", "game_login_verified", "social_session_accepted", "identity_verified")):
            raise WebProfileError("profile_unverified")
        identity = _bound(report.get("identity"), target)
        if (report.get("platform") != "pc" or identity.get("platform") != "pc"
                or identity.get("is_owner") is not (uid == EXPECTED_OWNER_UID)):
            raise WebProfileError("profile_identity_mismatch")
        visibility = _object(identity.get("visibility"))
        visibility = {name: _permission(visibility.get(name)) for name in ("overview", "history")}
        projected = {"uid": target, "platform": "pc", "owner_authentication_verified": True,
                     "game_login_verified": True, "social_session_accepted": True, "identity_verified": True,
                     "identity": {"uid": target, "platform": "pc", "is_owner": identity["is_owner"],
                                  "in_game_name": _text(identity.get("in_game_name"), 64),
                                  "level": _integer(identity.get("level"), 0, 10**6, nullable=True), "visibility": visibility}}
        for name in ("career", "ranks", "matches"):
            projected[name] = _section(report, name, visibility["history" if name == "matches" else "overview"]["state"], target, now)
            projected[name + "_verified"] = projected[name]["status"] == "available"
        season = (_integer(report.get("current_season"), 1, 999) if report.get("current_season_verified") is True else None)
        projected.update(current_season=season, current_season_verified=season is not None,
                         current_rank=None, current_rank_source=None)
        if season is not None and visibility["overview"]["state"] == "allowed" and report.get("current_rank") is not None:
            current = _object(report["current_rank"])
            if _integer(current.get("season"), 1, 999) != season or current.get("points_basis") != "server_rank_score":
                raise WebProfileError()
            level = _integer(current.get("level"), 0, 65535)
            points = _score(current.get("points"), nullable=True)
            if identity["is_owner"]:
                if report.get("current_rank_source") != "owner_panel" or current.get("points_verified") is not (points is not None):
                    raise WebProfileError()
                projected.update(current_rank={"season": season, "level": level, "points": points,
                    "points_basis": "server_rank_score", "points_verified": points is not None}, current_rank_source="owner_panel")
            else:
                if report.get("current_rank_source") != "target_season_history" or projected["ranks"]["status"] != "available":
                    raise WebProfileError()
                row = next((row for row in projected["ranks"]["data"]["seasons"] if row["season"] == season), None)
                if row is None or row["level"] != level or row["points"] != points:
                    raise WebProfileError()
                projected.update(current_rank={**row, "points_basis": "server_rank_score"}, current_rank_source="target_season_history")
        result = {"schemaVersion": 2, "source": "netease", "uid": target,
                  "fetchedAt": fetched_at.astimezone(timezone.utc).isoformat(), "nativeProfile": projected}
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 65536:
            raise WebProfileError("profile_response_too_large")
        return result
    except WebProfileError:
        raise
    except Exception:
        raise WebProfileError() from None
