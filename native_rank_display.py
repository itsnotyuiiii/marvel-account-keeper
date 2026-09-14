"""Pure, conservative display metadata for a projected NetEase profile.

The current installed public script archive (SHA256
07a3dcf20d539b9a613246047f5f91e4c7b44de62e96e24c47acea1cef5d1f9f)
defines QUALIFICATION_REQUARE_BATTLE_NUM = 10 in entry 5525. Entry 4672
compares fixed_battle_count against it, but obtains English rank labels from
external MarvelRankLevelTable/RankUITable rows that have not been verified.
Consequently this module does not guess named ranks or normalize raw scores.

NetEase's placement explanation distinguishes its provisional rank from the
final placement; the Season 7 update retains ten matches and adds a final
performance adjustment. This is a display of NetEase data, not an MMR model:
https://www.marvelrivals.com/20251114/41525_1270590.html
https://www.marvelrivals.com/gameupdate/20260318/41548_1291772.html
"""

import math
import re


PLACEMENTS_TOTAL = 10
_UID = re.compile(r"[1-9][0-9]{5,10}\Z")


def _integer(value, low=0, high=10**12):
    return type(value) is int and low <= value <= high


def _score(value):
    return type(value) in (int, float) and 0 <= value <= 10**6 and math.isfinite(value)


def _mapping(value):
    return value if type(value) is dict else {}


def _unknown(uid=None, reason="profile_unavailable"):
    return {
        "uid": uid,
        "season": None,
        "available": False,
        "level": None,
        "label": "Rank unavailable",
        "rank_name": None,
        "rank_name_verified": False,
        "server_rank_score": None,
        "points_basis": "server_rank_score",
        "placements_completed": None,
        "placements_reported": None,
        "placements_total": PLACEMENTS_TOTAL,
        "placements_label": "Placements unknown",
        "status": "unknown",
        "estimated": None,
        "reason": reason,
        "detail": "NetEase did not provide verified current-season placement progress.",
    }


def current_rank_display(profile):
    """Return fresh, JSON-safe display metadata without mutating the profile.

    Input is the nativeProfile object from the strict schemaVersion2 envelope.
    Missing/malformed old saved snapshots return unknown rather than raising.
    Only a uniquely matching, UID-bound current-season history row can supply
    placements; previous seasons, match totals, and peak ranks are never used.
    A conflicting row leaves the independently reported current numeric rank
    visible, but its estimated/ranked status remains unknown.

    ``placements_reported`` preserves the source count. ``placements_completed``
    is progress capped at ten for X/10 display (counts >= ten mean completed).
    ``estimated`` is None when completion is unknown, never a guessed False.
    """
    report = _mapping(profile)
    uid = report.get("uid")
    if type(uid) is not str or not _UID.fullmatch(uid):
        return _unknown(reason="profile_identity_unavailable")
    result = _unknown(uid)
    identity = _mapping(report.get("identity"))
    if (report.get("identity_verified") is not True or identity.get("uid") != uid
            or report.get("platform") != "pc" or identity.get("platform") != "pc"):
        result["reason"] = "profile_identity_unverified"
        return result
    visibility = _mapping(identity.get("visibility"))
    if _mapping(visibility.get("overview")).get("state") != "allowed":
        result["reason"] = "overview_not_available"
        return result
    season = report.get("current_season")
    if report.get("current_season_verified") is not True or not _integer(season, 1, 999):
        result["reason"] = "current_season_unverified"
        return result
    result["season"] = season
    current = _mapping(report.get("current_rank"))
    level, points = current.get("level"), current.get("points")
    source = report.get("current_rank_source")
    if (not _integer(current.get("season"), 1, 999) or current["season"] != season
            or not _integer(level, 0, 65535)
            or current.get("points_basis") != "server_rank_score"
            or (points is not None and not _score(points))
            or source not in ("owner_panel", "target_season_history")):
        result["reason"] = "current_rank_unavailable"
        return result
    if ((source == "owner_panel" and identity.get("is_owner") is not True)
            or (source == "target_season_history" and identity.get("is_owner") is not False)):
        result["reason"] = "current_rank_source_conflict"
        return result
    if source == "owner_panel" and current.get("points_verified") is not (points is not None):
        result["reason"] = "current_rank_score_unverified"
        return result
    result.update(available=True, level=level, label=f"Rank level {level}", server_rank_score=points)
    ranks = _mapping(report.get("ranks"))
    data = _mapping(ranks.get("data"))
    rows = data.get("seasons")
    if (report.get("ranks_verified") is not True or ranks.get("status") != "available"
            or data.get("uid") != uid or data.get("platform") != "pc"
            or data.get("points_basis") != "server_rank_score"
            or type(rows) is not list or len(rows) > 100):
        result["reason"] = "placement_history_unavailable"
        return result
    matching = [row for row in rows if type(row) is dict
                and _integer(row.get("season"), 1, 999) and row["season"] == season]
    if len(matching) != 1:
        result["reason"] = "current_season_row_missing" if not matching else "current_season_row_conflict"
        return result
    row = matching[0]
    if (not _integer(row.get("level"), 0, 65535) or row["level"] != level
            or not _score(row.get("points")) or (points is not None and row["points"] != points)):
        result["reason"] = "current_rank_history_conflict"
        return result
    count = row.get("placements_completed")
    if not _integer(count):
        result["reason"] = "placement_count_unavailable"
        return result
    # Target current_rank includes this field too. A mismatch in old or edited
    # snapshots must not make an estimate appear to be a completed placement.
    if (source == "target_season_history" and "placements_completed" in current
            and (type(current["placements_completed"]) is not int
                 or current["placements_completed"] != count)):
        result["reason"] = "placement_count_conflict"
        return result
    estimated = count < PLACEMENTS_TOTAL
    progress = min(count, PLACEMENTS_TOTAL)
    result.update(
        placements_completed=progress,
        placements_reported=count,
        placements_label=f"Placements {progress}/{PLACEMENTS_TOTAL}",
        status="estimated" if estimated else "ranked",
        estimated=estimated,
        reason="verified_current_season",
        detail=("NetEase's estimated rank during placements; the final placed rank may differ."
                if estimated else "NetEase reports completed placements for this season."),
    )
    return result
