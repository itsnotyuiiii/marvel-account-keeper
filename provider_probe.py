"""Explicit, read-only probes for observed Marvel Rivals API routes.

This is a research tool, not a working general-player NetEase provider. It
does not import app.py, read the vault, acquire cookies, retry, or save bodies.
See API_RESEARCH.md for evidence and scope limitations.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import re
import urllib.error
import urllib.parse
import urllib.request


NETEASE_BASE = "https://interact32-h.webapp.easebar.com/x20namwss202505/"
TRACKER_BASE = "https://api.tracker.gg/api/v2/marvel-rivals/standard/"
RIVALSDATA_BASE = "https://api.rivalsdata.com/"
CONTROL_REPLAY_ID = "10361658731"
CONTROL_MATCH_ID = "6687808_1749775929_1310002_12001_11"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ProbeResult:
    provider: str
    lookup_kind: str
    url: str
    outcome: str
    message: str
    http_status: int | None = None
    application_code: int | str | None = None
    match_count: int | None = None


def _digits(value: str, label: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        rf"[0-9]{{{minimum},{maximum}}}", value
    ):
        raise ValueError(f"{label} must contain {minimum}-{maximum} ASCII digits.")
    return value


def _classify(provider: str, kind: str, url: str, status: int, raw: bytes) -> ProbeResult:
    def result(outcome, message, **fields):
        return ProbeResult(provider, kind, url, outcome, message, status, **fields)

    if status in (401, 403):
        return result("access_blocked", "The provider rejected this client; this does not establish an invalid player or match ID.")
    if status == 429:
        return result("rate_limited", "The provider rate limited the request. No automatic retry was made.")
    if status != 200:
        return result("http_error", "The provider did not return HTTP 200.")
    if len(raw) > MAX_RESPONSE_BYTES:
        return result("response_too_large", "The response exceeded the diagnostic size limit.")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return result("invalid_response", "HTTP 200 did not contain readable JSON.")
    if not isinstance(payload, dict):
        return result("invalid_response", "Expected a JSON object.")
    if provider != "netease_esports":
        # Do not label an arbitrary JSON document a successful player lookup.
        # These probes only verify transport; app.py owns Tracker rank parsing.
        return result("json_received", "JSON received; player identity and usable statistics are not validated by this probe.")
    code = payload.get("code")
    code = code if type(code) in (int, str) else None
    if payload.get("success") is not True or code != 200:
        return result("application_error", "The NetEase envelope did not report success.", application_code=code)
    data = payload.get("data")
    if not isinstance(data, dict) or "matches" not in data:
        return result("invalid_response", "The NetEase response has no data.matches field.", application_code=code)
    matches = data["matches"]
    if matches is None or matches == []:
        return result("no_matches", "This route returned no matches. It does not validate a player UID or establish that the match does not exist elsewhere.", application_code=code, match_count=0)
    if not isinstance(matches, list) or not all(
        isinstance(match, dict)
        and isinstance(match.get("match_uid"), str)
        and bool(match["match_uid"])
        and isinstance(match.get("match_players"), list)
        and bool(match["match_players"])
        for match in matches
    ):
        return result("invalid_response", "The NetEase match collection has an unexpected shape.", application_code=code)
    return result("matches_received", "Match records received; ordinary-player coverage is not established by this result.", application_code=code, match_count=len(matches))


def _request(provider: str, kind: str, url: str, *, body: dict | None = None,
             timeout: float = 15, opener=None) -> ProbeResult:
    headers = {"Accept": "application/json", "User-Agent": "MarvelAccountKeeper-API-Probe/1.0"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            return _classify(provider, kind, url, response.status,
                             response.read(MAX_RESPONSE_BYTES + 1))
    except urllib.error.HTTPError as exc:
        # Don't read/log an HTML challenge body or reflect provider error text.
        try:
            return _classify(provider, kind, url, exc.code, b"")
        finally:
            exc.close()
    except (OSError, urllib.error.URLError):
        return ProbeResult(provider, kind, url, "network_error",
                           "Connection, TLS, or timeout failure; no HTTP response was available.")


def probe_replay(replay_id: str, *, zone_id: str = "12001", china_fallback: bool = False,
                 **transport) -> ProbeResult:
    """Replay code, NOT player UID. The official page hardcodes zone 12001."""
    _digits(replay_id, "Replay code", 6, 20)
    _digits(zone_id, "Zone ID", 5, 5)
    route = "x20mwss202505_zh_get_history_by_replay_id" if china_fallback else "x20mwss202505_get_history_by_replay_id"
    query = urllib.parse.urlencode({"zone_id": zone_id, "replay_id": replay_id})
    return _request("netease_esports", "replay_code", f"{NETEASE_BASE}{route}/?{query}", **transport)


def probe_match(match_id: str, **transport) -> ProbeResult:
    if not isinstance(match_id, str) or not re.fullmatch(r"[0-9]+(?:_[0-9]+){4}", match_id) or len(match_id) > 100:
        raise ValueError("Use the five-part internal match ID, not a player UID or replay code.")
    query = urllib.parse.urlencode({"match_uids": match_id})
    return _request("netease_esports", "internal_match_id",
                    f"{NETEASE_BASE}x20mwss202505_get_history/?{query}", **transport)


def probe_tracker(ign: str, **transport) -> ProbeResult:
    if not isinstance(ign, str) or not ign.strip() or len(ign) > 100 or any(ord(c) < 32 for c in ign):
        raise ValueError("Supply a nonempty in-game name of at most 100 characters.")
    url = TRACKER_BASE + "profile/ign/" + urllib.parse.quote(ign.strip(), safe="")
    return _request("tracker", "in_game_name", url, **transport)


def probe_rivalsdata(uid: str, **transport) -> ProbeResult:
    _digits(uid, "Player UID", 6, 11)
    return _request("rivalsdata", "player_uid", RIVALSDATA_BASE + "player",
                    body={"uid": int(uid)}, **transport)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    lookup = parser.add_mutually_exclusive_group(required=True)
    lookup.add_argument("--replay-code", help="NetEase replay code, NOT player UID")
    lookup.add_argument("--match-id", help="NetEase five-part internal match ID")
    lookup.add_argument("--tracker-ign", help="Tracker in-game-name transport test")
    lookup.add_argument("--rivalsdata-uid", help="RivalsData player-UID transport test")
    lookup.add_argument("--control", action="store_true", help="Test a known public tournament replay")
    parser.add_argument("--zone-id", default="12001", help="Replay lookup only; official page uses 12001")
    parser.add_argument("--china-fallback", action="store_true", help="Replay lookup only; select the observed zh route")
    args = parser.parse_args(argv)
    if not (args.replay_code or args.control) and (args.zone_id != "12001" or args.china_fallback):
        parser.error("Replay options require --replay-code or --control.")
    try:
        if args.replay_code or args.control:
            result = probe_replay(args.replay_code or CONTROL_REPLAY_ID, zone_id=args.zone_id,
                                  china_fallback=args.china_fallback)
        elif args.match_id:
            result = probe_match(args.match_id)
        elif args.tracker_ign:
            result = probe_tracker(args.tracker_ign)
        else:
            result = probe_rivalsdata(args.rivalsdata_uid)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.outcome in {"matches_received", "json_received"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
