"""One bounded stats read using a captured, game-issued session.

Preview by default. --send creates one connection to the exact captured peer,
performs its normal authentication exchange and one profile query, then closes.
An explicit --target-uid selects the game's other-player rank read instead.
--target-career tests whether the other-player Career operation named in the
game's separate RPC path is accessible through this transport; that is not
assumed to work, and denial is never retried through an own-player operation.
No retry, logout, token export, game/process changes, or saved-account writes.
This build-specific research helper is not a background production provider.
"""
import argparse
import base64
from dataclasses import dataclass, field
import json
import re
import socket
import struct
import time
import uuid

from career_probe import MAX_TOKEN_BYTES, positive_id
from socialmatrix_capture_probe import (
    MAX_STREAM_BYTES, RC4Stream, ServiceReplyError, bounded_json, career_exchanges, decode_envelope,
    decode_payload, decode_response, fold_xor, load_build_parameter,
    load_capture_records, magic_signature, opening_fields,
)

PEER = ("35.236.255.68", 12101)


@dataclass(repr=False)
class CaptureSession:
    own_uid: int
    hello: dict = field(repr=False)
    setup: dict = field(repr=False)
    request: dict = field(repr=False)
    parameter: bytes = field(repr=False)
    captured_profile: dict = field(repr=False)
    summary_request: dict | None = field(default=None, repr=False)
    captured_seasons: tuple[int, ...] = ()
    rank_request: dict | None = field(default=None, repr=False)


def validate_capture_session(session, now=None):
    header = session.setup.get("header", {})
    token = header.get("token")
    if not isinstance(token, str) or len(token) > MAX_TOKEN_BYTES or not re.fullmatch(
            r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
        raise ValueError("Unverified session format")
    part = token.split(".")[1]
    claims = bounded_json(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    current = time.time() if now is None else now
    if (not isinstance(claims, dict) or type(claims.get("exp")) is not int or claims["exp"] <= current or
            type(claims.get("iat")) is not int or claims["iat"] > current or
            "nbf" in claims and (type(claims["nbf"]) is not int or claims["nbf"] > current)):
        raise ValueError("Session time claims are invalid or expired")
    aid = claims.get("aid")
    if (not isinstance(aid, str) or aid != header.get("aid") or
            not re.search(r"(?<![0-9])" + str(positive_id(session.own_uid, "Own UID")) + r"(?![0-9])", aid)):
        raise ValueError("Session identity does not match the captured own UID")
    route = claims.get("tcp")
    if (not isinstance(route, dict) or route.get("addr") != f"{PEER[0]}:{PEER[1]}" or
            route.get("tls") is not False or route.get("rc4") is not True):
        raise ValueError("Session does not identify the reviewed captured transport")
    if str(session.captured_profile.get("player_uid")) != str(session.own_uid):
        raise ValueError("Captured profile identity mismatch")
    if (session.request.get("service") != "xyq-career" or session.request.get("method") != "POST" or
            session.request.get("uri") != "query_my_player_career"):
        raise ValueError("Only the captured own-profile read may be sent")
    post = bounded_json(base64.b64decode(session.request.get("post_body", ""), validate=True))
    if not isinstance(post, dict) or positive_id(post.get("player_uid"), "Request UID") != session.own_uid:
        raise ValueError("Request identity mismatch")
    return claims


def prepare_capture_session(trace, archive, own_uid):
    own_uid = positive_id(own_uid, "Own UID")
    records = load_capture_records(trace, archive)
    exchanges = career_exchanges(records, own_uid)
    profiles = [item for item in exchanges if item["request"]["uri"] == "query_my_player_career"]
    setup = [decode_envelope(body) for main, sub, body in records["out"][1:] if (main, sub) == (1, 3)]
    if len(profiles) != 1 or len(setup) != 1:
        raise ValueError("Expected one captured own-profile request and login")
    session = CaptureSession(own_uid, bounded_json(records["out"][0][2]), setup[0], profiles[0]["request"],
                             load_build_parameter(archive), profiles[0]["data"].get("career", {}))
    summaries = [item for item in exchanges if item["request"]["uri"] == "query_my_match_summary"]
    if len(summaries) == 1:
        session.summary_request = summaries[0]["request"]
    session.captured_seasons = tuple(dict.fromkeys(item["post_body"]["match_season"] for item in exchanges
        if type(item["post_body"].get("match_season")) is int and item["post_body"]["match_season"] > 0))
    session.rank_request = captured_rank_request(records, session)
    validate_capture_session(session)
    return session


def captured_rank_request(records, session):
    """Use a successful own-player GetData template, selecting only rank seasons.

    The field is consumed by installed entry1833 s_request_rank_data. Unlike
    its other-player UI caller, this query stays bound to our captured own UID.
    No leaderboard, arbitrary ID, or namecard write is supported.
    """
    profile = bounded_json(base64.b64decode(session.request["post_body"], validate=True))
    zone = positive_id(profile.get("zone_id"), "Captured zone")
    expected_id = f"{zone}_{session.own_uid}"
    replies = {}
    for main, sub, body in records["in"][1:]:
        if (main, sub) == (1, 2):
            envelope = decode_envelope(body)
            rpcid = envelope["header"].get("rpcid")
            if isinstance(rpcid, str):
                replies[rpcid] = envelope
    for main, sub, body in records["out"][1:]:
        if (main, sub) != (1, 2):
            continue
        envelope = decode_envelope(body)
        payload = decode_payload(envelope)
        if (not isinstance(payload, dict) or payload.get("service") != "xyq-namecard" or
                payload.get("uri") != "GetData" or payload.get("method") != "POST"):
            continue
        post = bounded_json(base64.b64decode(payload.get("post_body", ""), validate=True))
        if (not isinstance(post, dict) or post.get("tag") != "player" or
                post.get("id") != expected_id or type(post.get("host")) is not int or post["host"] != zone):
            continue
        reply = replies.get(envelope["header"].get("rpcid"))
        if reply is None:
            continue
        decode_response(decode_payload(reply), service="xyq-namecard")
        content = {"host": zone, "tag": "player", "id": expected_id, "fields": "rank_game_season"}
        return {**payload, "post_body": base64.b64encode(json.dumps(content, separators=(",", ":")).encode()).decode()}
    return None


class Wire:
    def __init__(self, sock, seconds=20, monotonic=time.monotonic):
        self.sock, self.clock = sock, monotonic
        self.deadline = monotonic() + seconds
        self.in_crypto = self.out_crypto = None
        self.received = 0

    def exact(self, size, encrypted=False):
        if size < 0 or self.received + size > MAX_STREAM_BYTES:
            raise ValueError("Response exceeds limit")
        chunks = bytearray()
        while len(chunks) < size:
            remaining = self.deadline - self.clock()
            if remaining <= 0:
                raise TimeoutError("Response deadline reached")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(size - len(chunks))
            if not chunk:
                raise ValueError("Connection ended before a complete response")
            chunks.extend(chunk)
        self.received += size
        return self.in_crypto.process(chunks) if encrypted else bytes(chunks)

    def send(self, main, sub, body):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError("Request deadline reached")
        if len(body) > MAX_STREAM_BYTES - 7:
            raise ValueError("Request exceeds limit")
        self.sock.settimeout(remaining)
        self.sock.sendall(self.out_crypto.process(struct.pack("<IHB", len(body) + 7, main, sub) + body))

    def record(self):
        header = self.exact(7, True)
        size, main, sub = struct.unpack("<IHB", header)
        if size < 7:
            raise ValueError("Invalid record length")
        return main, sub, self.exact(size - 7, True)


def encoded_envelope(header, payload, *, data_cmd=0):
    return b"\0" + json.dumps({"header": header, "body": {"cmd": data_cmd, "payload": payload}},
                              separators=(",", ":")).encode()


def correlated_reply(wire, command, rpcid):
    for _ in range(16):
        main, sub, body = wire.record()
        if (main, sub) != command:
            continue
        envelope = decode_envelope(body)
        if envelope["header"].get("rpcid") == rpcid:
            return envelope
    raise ValueError("Expected correlated reply was not received")


def selected_request(session, kind, season=None):
    if kind == "profile":
        if season is not None:
            if type(season) is not int or season not in session.captured_seasons:
                raise ValueError("Select a season observed in this own-account capture")
            post = bounded_json(base64.b64decode(session.request["post_body"], validate=True))
            post["match_season"] = season
            return {**session.request, "post_body": base64.b64encode(json.dumps(post, separators=(",", ":")).encode()).decode()}
        return session.request
    if kind == "ranks":
        if season is not None or not isinstance(session.rank_request, dict):
            raise ValueError("No verified own-namecard request template")
        selected = session.rank_request
        post = bounded_json(base64.b64decode(selected.get("post_body", ""), validate=True))
        profile = bounded_json(base64.b64decode(session.request["post_body"], validate=True))
        zone = positive_id(profile.get("zone_id"), "Captured zone")
        if (selected.get("service") != "xyq-namecard" or selected.get("uri") != "GetData" or
                selected.get("method") != "POST" or not isinstance(post, dict) or
                set(post) != {"host", "tag", "id", "fields"} or type(post.get("host")) is not int or
                post["host"] != zone or post.get("tag") != "player" or
                post.get("id") != f"{zone}_{session.own_uid}" or post.get("fields") != "rank_game_season"):
            raise ValueError("Only the bounded own-account rank field may be read")
        return selected
    if season is not None:
        raise ValueError("Match summary is not season filtered")
    if kind != "summary" or not isinstance(session.summary_request, dict):
        raise ValueError("No verified request for the selected operation")
    selected = session.summary_request
    post = bounded_json(base64.b64decode(selected.get("post_body", ""), validate=True))
    if (selected.get("service") != "xyq-career" or selected.get("uri") != "query_my_match_summary" or
            selected.get("method") != "POST" or not isinstance(post, dict) or
            positive_id(post.get("player_uid"), "Request UID") != session.own_uid or
            type(post.get("page")) is not int or post["page"] != 0 or
            type(post.get("page_size")) is not int or not 1 <= post["page_size"] <= 50):
        raise ValueError("Only a captured bounded own-match-summary read may be sent")
    return selected


def summary_identity_matches(data, own_uid):
    matches = data.get("match_info")
    return (isinstance(matches, list) and len(matches) <= 50 and all(
        isinstance(item, dict) and isinstance(item.get("match_player"), dict) and
        str(item["match_player"].get("player_uid")) == str(own_uid) for item in matches))


def selected_player_rank_request(session, target_uid):
    """Reproduce entry1833's normal other-player namecard rank read.

    This separate research operation changes only the requested player ID.
    Session identity, captured zone, service, operation, and rank-only field
    stay fixed. The response must not be presented as the connected player's
    profile, an echoed target identity, or proof of Career visibility.
    """
    target_uid = positive_id(target_uid, "Target UID")
    if target_uid == session.own_uid:
        raise ValueError("Use the existing own-account rank operation for this UID")
    selected = selected_request(session, "ranks")
    post = bounded_json(base64.b64decode(selected["post_body"], validate=True))
    post["id"] = f"{post['host']}_{target_uid}"
    return {**selected, "post_body": base64.b64encode(json.dumps(post, separators=(",", ":")).encode()).decode()}


def selected_player_career_request(session, target_uid):
    """Capability test of the other-player operation, never query_my_*.

    Entry1833 sends query_player_career and the same profile query dictionary
    through C_QUERY_CAREER. Static evidence names the operation but does not
    prove it is exposed by the SocialMatrix transport.
    """
    target_uid = positive_id(target_uid, "Target UID")
    if target_uid == session.own_uid:
        raise ValueError("Use the existing own-account operation for this UID")
    selected = selected_request(session, "profile")
    post = bounded_json(base64.b64decode(selected["post_body"], validate=True))
    post["player_uid"] = target_uid
    return {**selected, "uri": "query_player_career",
            "post_body": base64.b64encode(json.dumps(post, separators=(",", ":")).encode()).decode()}


def selected_player_search_request(session, name):
    """Bounded first-page search, from SvcUser.UserClient.search_user (3012)."""
    if (not isinstance(name, str) or not name or name != name.strip() or len(name) > 64 or
            len(name.encode("utf-8")) > 256 or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)):
        raise ValueError("Supply one nonempty player name of at most 64 characters")
    post = bounded_json(base64.b64decode(session.request["post_body"], validate=True))
    return {"name": name, "cursor": 0, "limit": 10,
            "host": positive_id(post.get("zone_id"), "Captured zone")}


def selected_player_user_request(session, target_uid):
    """SvcUser._get_svc_user (entry3012), addressed using mos_client_utils."""
    target_uid = positive_id(target_uid, "Target UID")
    post = bounded_json(base64.b64decode(session.request["post_body"], validate=True))
    zone = positive_id(post.get("zone_id"), "Captured zone")
    return {"aid": f"{zone}_{target_uid}"}


def user_module_command(setup_data):
    """Use only the named service advertised by the current accepted session."""
    modules = setup_data.get("modules")
    if not isinstance(modules, list) or len(modules) > 100:
        raise ValueError("Unexpected session module list")
    matches = [row.get("main_cmd") for row in modules if isinstance(row, dict) and row.get("name") == "svc-user"]
    if len(matches) != 1 or type(matches[0]) is not int or not 1 <= matches[0] <= 65535:
        raise ValueError("The session does not advertise a usable player-search module")
    return (matches[0], 0)


def probe_socket(session, *, kind="profile", season=None, target_uid=None, search_name=None,
                 connection_factory=socket.create_connection):
    """Return response data in memory only. No errors or payloads are logged."""
    validate_capture_session(session)
    target_operation = kind in {"player_ranks", "player_profile", "player_user"}
    search_operation = kind == "player_search"
    user_operation = kind == "player_user"
    if search_operation:
        if target_uid is not None or season is not None:
            raise ValueError("Player search does not accept a target UID or season override")
        selected = selected_player_search_request(session, search_name)
    elif search_name is not None:
        raise ValueError("A search name cannot override another operation")
    elif target_operation:
        if season is not None:
            raise ValueError("The other-player probe does not accept a season override")
        select = {"player_ranks": selected_player_rank_request, "player_profile": selected_player_career_request,
                  "player_user": selected_player_user_request}[kind]
        selected = select(session, target_uid)
    else:
        if target_uid is not None:
            raise ValueError("A target UID cannot override an own-account Career operation")
        selected = selected_request(session, kind, season)
    report = {"request_attempted": False, "session_accepted": False, "profile_request_sent": False,
              "profile_uid_matches": False, "statistics_verified": False, "credentials_saved": False,
              "stage": "connect", "query_kind": kind, "own_uid_matches": False,
              "own_request_bound": False, "outcome": "not_started"}
    if target_operation:
        report.update(target_uid=str(target_uid), target_request_bound=False,
                      target_uid_echoed=False, career_visibility_verified=False)
    if search_operation:
        report.update(search_request_sent=False, search_result_received=False)
    if user_operation:
        report["user_request_sent"] = False
    sock = None
    try:
        report["request_attempted"] = True
        sock = connection_factory(PEER, timeout=10)
        wire = Wire(sock)
        report["stage"] = "secure_opening"
        prefix = wire.exact(4)
        size = struct.unpack("<I", prefix)[0]
        if not 12 <= size <= 4096:
            raise ValueError("Invalid server opening length")
        fields, _ = opening_fields(prefix + wire.exact(size - 4), True)
        if list(map(len, fields)) != [4, 4, 4]:
            raise ValueError("Unsupported server opening")
        signature = magic_signature(fields[0], session.parameter)
        sock.sendall(struct.pack("<IH", 14, 4) + signature + struct.pack("<HH", 0, 0))
        wire.in_crypto = RC4Stream(fold_xor(fields[1], session.parameter))
        wire.out_crypto = RC4Stream(fold_xor(fields[2], session.parameter))
        report["stage"] = "serialization_negotiation"
        wire.send(0, 0, json.dumps(session.hello, separators=(",", ":")).encode())
        main, sub, data = wire.record()
        hello = bounded_json(data)
        if (main, sub) != (0, 0) or not isinstance(hello, dict) or hello.get("code") != 0 or hello.get("content_encoding") != "uni_lz4":
            raise ValueError("Unsupported server negotiation")
        rpcid = uuid.uuid4().hex
        report["stage"] = "session_setup"
        header = {k: session.setup["header"][k] for k in ("aid", "token", "version")}
        wire.send(1, 3, encoded_envelope({**header, "rpcid": rpcid}, ""))
        setup = correlated_reply(wire, (1, 3), rpcid)
        streamid = setup["header"].get("streamid")
        setup_data = decode_payload(setup)
        if (setup["header"].get("aid") != header["aid"] or not isinstance(streamid, str) or not streamid or
                not isinstance(setup_data.get("modules"), list)):
            raise ValueError("Session setup was not accepted")
        report["session_accepted"] = True
        report["stage"] = "profile_query"
        command = user_module_command(setup_data) if search_operation or user_operation else (1, 2)
        request_id = uuid.uuid4().hex
        request_header = {"aid": header["aid"], "version": header["version"], "streamid": streamid,
                          "rpcid": request_id, "ackRpcId": [rpcid]}
        payload = base64.b64encode(json.dumps(selected, separators=(",", ":")).encode()).decode()
        # UserDataCmd.CmdSearchUser=15 (entry3010); its reply is16. This is
        # read-only search, not CmdLogin, CmdUpdateUser, or a friend mutation.
        wire.send(*command, encoded_envelope(request_header, payload,
                                            data_cmd=15 if search_operation else 7 if user_operation else 0))
        report["profile_request_sent"] = not (search_operation or user_operation)
        if search_operation:
            report["search_request_sent"] = True
        if user_operation:
            report["user_request_sent"] = True
        response = correlated_reply(wire, command, request_id)
        if search_operation:
            data = decode_payload(response)
            if (response["body"].get("cmd") != 16 or not isinstance(data, dict) or
                    not isinstance(data.get("users"), list) or len(data["users"]) > 10 or
                    not all(isinstance(row, dict) for row in data["users"])):
                raise ValueError("Unexpected player-search response")
            report.update(outcome="player_search_received", search_result_received=True, stage="complete")
            return report, data
        if user_operation:
            data = decode_payload(response)
            user = data.get("client_info") if isinstance(data, dict) else None
            if (response["body"].get("cmd") != 8 or not isinstance(user, dict) or
                    user.get("aid") != selected["aid"]):
                raise ValueError("Returned player identity mismatch")
            report.update(outcome="player_user_received", target_request_bound=True,
                          target_uid_echoed=True, stage="complete")
            return report, data
        data = decode_response(decode_payload(response), service=selected["service"])
        if kind in {"profile", "player_profile"}:
            profile = data.get("career")
            expected_uid = target_uid if target_operation else session.own_uid
            if not isinstance(profile, dict) or str(profile.get("player_uid")) != str(expected_uid):
                raise ValueError("Returned profile identity mismatch")
        elif kind in {"ranks", "player_ranks"}:
            # GetData echoes no UID. Identity evidence is the selected-ID request
            # plus fresh authenticated RPC correlation, not a returned UID.
            if not isinstance(data.get("rank_game_season"), str):
                raise ValueError("No season rank field in namecard reply")
        elif not summary_identity_matches(data, session.own_uid):
            raise ValueError("Returned summary identity mismatch")
        report.update(outcome={"profile": "own_profile_received", "summary": "own_summary_received",
                               "ranks": "own_ranks_received", "player_ranks": "player_ranks_received",
                               "player_profile": "player_profile_received"}[kind],
                      profile_uid_matches=kind == "profile", own_uid_matches=kind in {"profile", "summary"},
                      own_request_bound=not target_operation, stage="complete")
        if target_operation:
            report["target_request_bound"] = True
            report["target_uid_echoed"] = kind == "player_profile"
        return report, data
    except ServiceReplyError as exc:
        report.update(outcome="netease_request_rejected", upstream_layer=exc.layer, upstream_code=exc.code)
        return report, None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error):
        report["outcome"] = "bounded_socket_probe_failed"
        return report, None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace")
    parser.add_argument("archive")
    parser.add_argument("--own-uid", required=True)
    parser.add_argument("--target-uid", help="Explicit other player; reads rank seasons only, not Career stats")
    parser.add_argument("--target-career", action="store_true", help="Capability-test the separate other-player Career operation")
    parser.add_argument("--target-user", action="store_true", help="Read the public identity for --target-uid")
    parser.add_argument("--search-name", help="Read the first ten player-search results for this IGN")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    if args.target_career and args.target_uid is None:
        parser.error("--target-career requires --target-uid")
    if args.target_user and (args.target_uid is None or args.target_career):
        parser.error("--target-user requires --target-uid and cannot be combined with --target-career")
    if args.search_name is not None and args.target_uid is not None:
        parser.error("Choose --search-name or --target-uid, not both")
    try:
        session = prepare_capture_session(args.trace, args.archive, args.own_uid)
        kind = "player_search" if args.search_name is not None else "player_user" if args.target_user else "player_profile" if args.target_career else "player_ranks" if args.target_uid else "profile"
        if args.search_name is not None:
            selected_player_search_request(session, args.search_name)
        if args.target_uid is not None:
            select = selected_player_user_request if args.target_user else selected_player_career_request if args.target_career else selected_player_rank_request
            select(session, args.target_uid)
        if not args.send:
            print(json.dumps({"outcome": "preview_only", "request_attempted": False, "own_profile_capture_verified": True,
                              "query_kind": kind}))
            return 0
        report, data = probe_socket(session, kind=kind, target_uid=args.target_uid, search_name=args.search_name)
        if data is not None and kind == "profile":
            fields = ("total_match_count", "total_win_count", "k", "d", "a")
            report["captured_live_stats_match"] = all(data["career"].get(k) == session.captured_profile.get(k) for k in fields)
        print(json.dumps(report, indent=2))
        return 0 if report["profile_uid_matches"] or report.get("target_request_bound") or report.get("search_result_received") else 1
    except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error):
        print(json.dumps({"outcome": "capture_session_validation_failed", "request_attempted": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
