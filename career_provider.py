"""Shared-session NetEase provider. Only projected player data leaves memory."""
import base64
from collections import OrderedDict
import copy
import math
import re
import threading
import time

from career_socket_probe import prepare_capture_session, probe_socket, validate_capture_session
from socialmatrix_capture_probe import bounded_json


class CareerUnavailable(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def count(value):
    if type(value) is not int or not 0 <= value <= 10**12:
        raise ValueError("Unexpected count in Career response")
    return value


def project_profile(data, target_uid):
    career = data.get("career")
    if not isinstance(career, dict) or str(career.get("player_uid")) != str(target_uid):
        raise ValueError("Profile identity mismatch")
    return {name: count(career.get(field)) for name, field in {
        "matches": "total_match_count", "wins": "total_win_count", "kills": "k", "deaths": "d", "assists": "a"}.items()}


def project_matches(data, own_uid):
    matches = data.get("match_info")
    if not isinstance(matches, list) or len(matches) > 50:
        raise ValueError("Unexpected match summary")
    result = []
    for match in matches:
        player = match.get("match_player") if isinstance(match, dict) else None
        if not isinstance(player, dict) or str(player.get("player_uid")) != str(own_uid):
            raise ValueError("Match identity mismatch")
        season = match.get("match_season")
        if not isinstance(season, (int, str)) or not str(season).isascii() or not str(season).isdigit() or len(str(season)) > 4:
            raise ValueError("Unexpected match season")
        duration = match.get("match_play_duration")
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 <= duration <= 86400:
            raise ValueError("Unexpected match duration")
        result.append({"played_at": count(match.get("match_time_stamp")), "season": int(season),
                       "mode_id": count(match.get("game_mode_id")), "duration_seconds": round(duration),
                       "result": "win" if player.get("is_win") == 1 else "loss" if player.get("is_win") == 0 else "unknown",
                       "kills": count(player.get("k")), "deaths": count(player.get("d")), "assists": count(player.get("a"))})
    return result


# One-based levels, matching the game's ordered rank divisions. Never infer a
# tier from points: protection, placements and top-rank rules can differ.
RANK_NAMES = tuple(f"{tier} {division}" for tier in
    ("Bronze", "Silver", "Gold", "Platinum", "Diamond", "Grandmaster", "Celestial")
    for division in ("III", "II", "I")) + ("Eternity", "One Above All")
PLATFORM_OS = {"pc": 1, "ps": 2, "xbox": 4}  # Installed const_common.ELoginOS.
ACCOUNT_PLATFORMS = {"pc": "pc", "playstation": "ps", "xbox": "xbox"}
OS_ACCOUNT_PLATFORMS = {1: "pc", 2: "playstation", 4: "xbox"}


def project_player_identity(user, zone, *, expected_uid=None):
    """Discard social/presence/third-party-account fields from SvcUser replies."""
    if not isinstance(user, dict):
        raise ValueError("Unexpected player identity")
    aid = user.get("aid")
    match = re.fullmatch(r"([1-9][0-9]{0,19})_([1-9][0-9]{0,19})", aid) if isinstance(aid, str) else None
    name = user.get("name")
    if (not match or int(match[1]) != zone or expected_uid is not None and match[2] != str(expected_uid) or
            not isinstance(name, str) or not name or len(name) > 64 or
            any(ord(ch) < 32 or ord(ch) == 127 for ch in name)):
        raise ValueError("Returned player identity does not match the request")
    config = user.get("config_server")
    if not isinstance(config, dict):
        raise ValueError("Player platform/settings are unavailable")
    login_os = config.get("login_os")
    if type(login_os) is str and login_os in {"1", "2", "4"}:
        login_os = int(login_os)
    platform = OS_ACCOUNT_PLATFORMS.get(login_os) if type(login_os) is int else None
    settings = config.get("career_settings", {})
    if isinstance(settings, str):
        settings = bounded_json(settings)
    if not isinstance(settings, dict) or len(settings) > 100:
        raise ValueError("Unexpected Career settings")
    # const_career (5465) and career_utils.get_permission (4617): per-platform
    # key first, legacy key second, then OPEN=0. Other values restrict access.
    permissions = {}
    for key, os_id in PLATFORM_OS.items():
        value = settings.get(f"CareerOverviewIsVisibleToOther_{os_id}",
                             settings.get("CareerOverviewIsVisibleToOther", 0))
        permissions[key] = "public" if type(value) is int and value == 0 else \
                           "restricted" if type(value) is int and 1 <= value <= 4 else "unknown"
    return {"uid": match[2], "in_game_name": name, "platform": platform, "permissions": permissions}


def rank_name(level):
    if type(level) is not int or not 0 <= level <= len(RANK_NAMES):
        raise ValueError("Unknown NetEase rank level")
    return RANK_NAMES[level - 1] if level else None


def rank_points(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1_000_000:
        raise ValueError("Unexpected rank points")
    return math.floor(value)


def project_rank_record(row):
    """Optional normal-ranked season counts, not full Career totals.

    The namecard's battle_count/win_count agree with the independently read
    own Career season. Missing or inconsistent counts must not become zeros
    or discard an otherwise valid rank.
    """
    try:
        matches, wins = count(row.get("battle_count")), count(row.get("win_count"))
    except ValueError:
        return None
    return {"matches": matches, "wins": wins} if wins <= matches else None


def project_ranks(data, platform, current_season):
    """Project only the linked platform's normal ranked seasons.

    Installed const_rank.get_rank_key = OS*1,000,000 + type*1,000 + season.
    rank_utils.get_highest_rank_info_of_login_os compares max_level, then
    max_rank_score. season_max_level is a different (reward) field.
    GetData contains JSON inside JSON; neither raw layer is exported.
    """
    if platform not in PLATFORM_OS or type(current_season) is not int or not 1 <= current_season < 1000:
        raise ValueError("Unknown rank platform or season")
    ranks = bounded_json(data.get("rank_game_season"))
    if not isinstance(ranks, dict) or len(ranks) > 100:
        raise ValueError("Unexpected season rank collection")
    seasons = []
    for key, encoded in ranks.items():
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]{0,8}", key):
            raise ValueError("Unexpected season rank key")
        os_id, rest = divmod(int(key), 1_000_000)
        mode, season = divmod(rest, 1000)
        if os_id != PLATFORM_OS[platform] or mode != 1:
            continue
        row = bounded_json(encoded)
        if (not isinstance(row, dict) or type(row.get("rank_game_id")) is not int or
                row["rank_game_id"] != season or season < 1):
            raise ValueError("Season identity mismatch")
        level, peak_level = row.get("level"), row.get("max_level")
        current_name, peak_name = rank_name(level), rank_name(peak_level)
        fixed = row.get("fixed_finished")
        if fixed is not None and (type(fixed) is not int or fixed not in (0, 1)):
            raise ValueError("Unknown placement state")
        placement_count = row.get("fixed_battle_count")
        if placement_count is not None:
            placement_count = count(placement_count)
            if placement_count > 1000:
                raise ValueError("Unexpected placement count")
        seasons.append({"season": season, "level": level, "rank": current_name,
            "points": rank_points(row.get("rank_score")), "peak_level": peak_level,
            "peak_rank": peak_name, "peak_points": rank_points(row.get("max_rank_score")),
            "updated_at": count(row.get("update_time")),
            "qualification": "estimated" if fixed == 0 else "ranked" if fixed == 1 else "unknown",
            "placements_completed": placement_count, "record": project_rank_record(row)})
    seasons.sort(key=lambda row: row["season"], reverse=True)
    current = next((row for row in seasons if row["season"] == current_season), None)
    peak = max((row for row in seasons if row["peak_level"] > 0),
               key=lambda row: (row["peak_level"], row["peak_points"]), default=None)
    return {"current_season": current_season, "current": current, "peak": peak, "seasons": seasons}


class CapturedCareerProvider:
    def __init__(self, trace, archive, own_uid, *, game_reader=None):
        self._game_reader = game_reader
        self._paths = (trace, archive, own_uid)
        self._session = None
        self._snapshot = None
        self._lock = threading.Lock()
        self._last_attempt = None
        self._snapshot_attempt = None
        self._card_cache = OrderedDict()
        self._card_attempts = OrderedDict()
        self._remote_cooldown_until = 0

    def _read(self, session, **kwargs):
        if time.monotonic() < self._remote_cooldown_until:
            raise CareerUnavailable("career_rate_limited", "NetEase requested a pause. Wait before refreshing another player.", 429)
        try:
            report, data = probe_socket(session, **kwargs)
        except ValueError:
            raise CareerUnavailable("career_request_unavailable", "The session or request could not be validated. Saved values were kept; reconnect if the session expired.", 409) from None
        if report.get("upstream_code") == 429:
            self._remote_cooldown_until = time.monotonic() + 30
            raise CareerUnavailable("career_rate_limited", "NetEase is rate limiting this connection. Wait 30 seconds before refreshing.", 429)
        return report, data

    def _load(self):
        if self._session is None:
            try:
                self._session = prepare_capture_session(*self._paths)
            except (ValueError, OSError, KeyError, TypeError, IndexError, ImportError):
                raise CareerUnavailable("career_session_unavailable", "The selected local capture cannot supply a current own-account session. Reconnect using a fresh approved game-start capture.") from None
        try:
            claims = validate_capture_session(self._session)
        except ValueError:
            self._session = None
            raise CareerUnavailable("career_session_expired", "The game session expired or is no longer valid. A fresh approved game-start capture is needed.") from None
        return self._session, claims

    def status(self):
        with self._lock:
            session, claims = self._load()
            post = bounded_json(base64.b64decode(session.request["post_body"], validate=True))
            return {"configured": True, "uid": str(session.own_uid), "platform": post.get("device_type"),
                    "season": session.captured_seasons[-1] if session.captured_seasons else post.get("match_season"),
                    "seasons": list(session.captured_seasons), "expires_at": claims["exp"],
                    "rank_available": session.rank_request is not None,
                    "rank_season": post.get("match_season"),
                    "player_lookup_available": True,
                    "game_career": self._game_reader.status() if self._game_reader is not None else {"configured": False},
                    "snapshot": self._snapshot, "experimental": True}

    def matches_account(self, account):
        # No session loading/networking for an unrelated saved account. A UID
        # by itself is not a platform attestation.
        return (str(account.get("rivals_uid") or "") == str(self._paths[2]) and
                account.get("rivals_platform") == "pc")

    def can_refresh_account(self, account):
        return bool(str(account.get("rivals_uid") or "").strip() or
                    str(account.get("in_game_name") or "").strip())

    def clear(self):
        with self._lock:
            if self._game_reader is not None:
                self._game_reader.close()
            self._session = self._snapshot = None
            self._snapshot_attempt = None
            self._card_cache.clear()
            self._card_attempts.clear()

    def refresh(self, season=None, *, include_matches=True, reuse_recent=False):
        if not self._lock.acquire(blocking=False):
            raise CareerUnavailable("career_busy", "A Career read is already running.", 429)
        try:
            if self._last_attempt is not None and time.monotonic() - self._last_attempt < 30:
                if (reuse_recent and self._snapshot is not None and
                        self._snapshot_attempt == self._last_attempt and
                        self._snapshot.get("season") == season and not include_matches):
                    self._load()  # Never reuse an expired/invalid connection.
                    return self._snapshot
                raise CareerUnavailable("career_cooldown", "Wait 30 seconds between NetEase refreshes.", 429)
            session, _ = self._load()
            if season is None and session.captured_seasons:
                season = session.captured_seasons[-1]
            if season is not None and (type(season) is not int or season not in session.captured_seasons):
                raise CareerUnavailable("career_unknown_season", "Select a season observed in the connected own-account capture.", 400)
            self._last_attempt = time.monotonic()
            profile_report, profile_data = self._read(session, season=season)
            if not profile_report.get("own_uid_matches") or profile_data is None:
                stage = profile_report.get("stage")
                stage = stage if stage in {"connect", "secure_opening", "serialization_negotiation", "session_setup", "profile_query"} else "response"
                raise CareerUnavailable("career_request_failed", "NetEase did not return a verified own-profile response at the " + stage + " step. Saved account details were not changed.", 502)
            post = bounded_json(base64.b64decode(session.request["post_body"], validate=True))
            try:
                snapshot = {"uid": str(session.own_uid), "platform": post["device_type"], "season": season if season is not None else post.get("match_season"),
                            "fetched_at": int(time.time()), "source": "netease", "profile": project_profile(profile_data, session.own_uid),
                            "recent_matches": [], "matches_loaded": False, "ranks": None,
                            "rank_error": None, "match_error": None}
            except (ValueError, KeyError, TypeError):
                raise CareerUnavailable("career_shape_changed", "NetEase returned an unfamiliar stats format. The previous snapshot was kept.", 502) from None
            if session.rank_request is not None:
                rank_report, rank_data = self._read(session, kind="ranks")
                try:
                    if not rank_report.get("own_request_bound") or rank_data is None:
                        raise ValueError("Rank request did not complete")
                    snapshot["ranks"] = project_ranks(rank_data, post["device_type"], post.get("match_season"))
                except (ValueError, KeyError, TypeError):
                    snapshot["rank_error"] = "NetEase rank data was unavailable or unfamiliar. Saved ranks were kept."
            else:
                snapshot["rank_error"] = "This connection has no verified own-account rank request. Saved ranks were kept."
            if include_matches:
                summary_report, summary_data = self._read(session, kind="summary")
                try:
                    if not summary_report.get("own_uid_matches") or summary_data is None:
                        raise ValueError("Match summary did not complete")
                    snapshot["recent_matches"] = project_matches(summary_data, session.own_uid)
                    snapshot["matches_loaded"] = True
                except (ValueError, KeyError, TypeError):
                    snapshot["match_error"] = "Recent matches were unavailable; profile and rank results are independent."
            self._snapshot = snapshot
            self._snapshot_attempt = self._last_attempt
            return snapshot
        finally:
            self._lock.release()

    def refresh_account(self, account):
        uid = str(account.get("rivals_uid") or "").strip()
        name = str(account.get("in_game_name") or "").strip()
        if not uid and not name:
            raise CareerUnavailable("missing_identity", "Enter a UID or in-game name to look up.", 400)
        if not uid:
            try:
                valid_name = len(name) <= 64 and len(name.encode("utf-8")) <= 256 and not any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
            except UnicodeError:
                valid_name = False
            if not valid_name:
                raise CareerUnavailable("invalid_in_game_name", "Enter one in-game name of at most 64 characters, without control characters.", 400)
        if uid and not re.fullmatch(r"[1-9][0-9]{0,19}", uid):
            raise CareerUnavailable("invalid_rivals_uid", "Enter a numeric Marvel Rivals UID.", 400)
        configured_platform = account.get("rivals_platform") or "unknown"
        if configured_platform not in {"unknown", *ACCOUNT_PLATFORMS}:
            raise CareerUnavailable("career_platform_required", "Select PC, PlayStation, Xbox, or Unknown.", 400)
        if not self._lock.acquire(blocking=False):
            raise CareerUnavailable("career_busy", "A NetEase read is already running.", 429)
        try:
            session, _ = self._load()
            post = bounded_json(base64.b64decode(session.request["post_body"], validate=True))
            season = post.get("match_season")
            cache_key = ("uid", uid, configured_platform, season) if uid else ("ign", name.casefold(), configured_platform, season)
            now = time.monotonic()
            previous = self._card_attempts.get(cache_key)
            if previous is not None and now - previous < 30:
                cached = self._card_cache.get(cache_key)
                if cached is not None and cached[0] == previous:
                    return copy.deepcopy(cached[1])
                raise CareerUnavailable("career_cooldown", "Wait 30 seconds before repeating this NetEase lookup.", 429)
            self._card_attempts[cache_key] = now
            self._card_attempts.move_to_end(cache_key)
            while len(self._card_attempts) > 100:
                old_key, _ = self._card_attempts.popitem(last=False)
                self._card_cache.pop(old_key, None)
            result = self._fetch_card(session, post, uid, name, configured_platform)
            self._card_cache[cache_key] = (now, copy.deepcopy(result))
            return result
        finally:
            self._lock.release()

    def _fetch_card(self, session, post, uid, name, configured_platform):
        zone, season = post.get("zone_id"), post.get("match_season")
        if uid:
            report, data = self._read(session, kind="player_user", target_uid=uid)
            if not report.get("target_uid_echoed") or data is None:
                raise CareerUnavailable("career_identity_unavailable", "NetEase did not return that UID's player identity. This does not prove the UID is invalid. Saved values were kept.", 502)
            try:
                identity = project_player_identity(data.get("client_info"), zone, expected_uid=uid)
            except ValueError:
                raise CareerUnavailable("career_shape_changed", "NetEase returned an unfamiliar player identity. Saved values were kept.", 502) from None
        else:
            report, data = self._read(session, kind="player_search", search_name=name)
            if not report.get("search_result_received") or data is None:
                raise CareerUnavailable("career_search_unavailable", "NetEase player search is unavailable. Try a numeric UID; no third-party lookup was made.", 502)
            matches = {}
            try:
                for row in data["users"]:
                    if isinstance(row.get("name"), str) and row["name"].casefold() == name.casefold():
                        candidate = project_player_identity(row, zone)
                        matches[candidate["uid"]] = candidate
            except (ValueError, KeyError, TypeError):
                raise CareerUnavailable("career_shape_changed", "NetEase returned an unfamiliar search identity. Saved values were kept.", 502) from None
            if len(matches) != 1:
                code = "player_not_found" if not matches and data.get("cursor") == -1 else "player_name_ambiguous"
                raise CareerUnavailable(code, "No unique exact IGN match was verified. Check the name or enter the numeric UID.", 404 if code == "player_not_found" else 409)
            identity = next(iter(matches.values()))
            uid = identity["uid"]
        platform = identity["platform"] if configured_platform == "unknown" else configured_platform
        if platform not in ACCOUNT_PLATFORMS:
            raise CareerUnavailable("career_platform_required", "NetEase did not identify a supported platform. Select it on the card.", 409)
        api_platform = ACCOUNT_PLATFORMS[platform]
        own = uid == str(session.own_uid)
        visibility = "own" if own else identity["permissions"][api_platform]
        snapshot = {"uid": uid, "in_game_name": identity["in_game_name"], "platform": platform,
                    "season": season, "fetched_at": int(time.time()), "source": "netease",
                    "profile": None, "ranks": None, "rank_error": None, "profile_error": None,
                    "visibility": visibility, "recent_matches": [], "matches_loaded": False}
        if visibility not in {"own", "public"}:
            snapshot.update(rank_error="This Career overview is restricted or its visibility could not be verified. Saved ranks were kept.",
                            profile_error="Career data is not public for this platform.", lookup_code="career_profile_restricted")
            return snapshot
        # Keep the verified own-Career route. Other PC profiles use the normal
        # game Career RPC with one shared authenticated session, not a rewritten
        # query_my_* request. Identity and privacy were checked above.
        if own and api_platform == post.get("device_type"):
            report, data = self._read(session, season=season)
            try:
                if not report.get("own_uid_matches") or data is None:
                    raise ValueError("Own Career read failed")
                snapshot["profile"] = project_profile(data, session.own_uid)
            except (ValueError, KeyError, TypeError):
                snapshot["profile_error"] = "Detailed Career stats were unavailable. Rank refresh is independent."
        elif not own and api_platform == "pc" and self._game_reader is not None:
            from game_career_client import GameCareerUnavailable
            try:
                profile = self._game_reader.read_profile(int(uid), season, platform=api_platform)
                snapshot["profile"] = {key: count(profile.get(key)) for key in
                    ("matches", "wins", "kills", "deaths", "assists")}
            except GameCareerUnavailable as error:
                snapshot["profile_error"] = error.message
            except (ValueError, KeyError, TypeError):
                snapshot["profile_error"] = "NetEase returned an unfamiliar Career format. Saved stats were kept; rank refresh is independent."
        elif api_platform != "pc":
            snapshot["profile_error"] = "Detailed Career reads are currently verified for PC only. Rank refresh is independent."
        else:
            snapshot["profile_error"] = "Identity and rank lookup are connected; detailed Career stats for this player are not connected yet."
        if session.rank_request is None:
            snapshot["rank_error"] = "This session has no verified rank request template. Saved ranks were kept."
            return snapshot
        report, data = self._read(session, kind="ranks") if own else \
                       self._read(session, kind="player_ranks", target_uid=uid)
        try:
            if not report.get("own_request_bound" if own else "target_request_bound") or data is None:
                raise ValueError("Rank read failed")
            snapshot["ranks"] = project_ranks(data, api_platform, season)
        except (ValueError, KeyError, TypeError):
            snapshot["rank_error"] = "NetEase rank data was unavailable or unfamiliar. Saved ranks were kept."
        return snapshot
