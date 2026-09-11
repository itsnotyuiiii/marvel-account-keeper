"""Bounded other-player Career RPC after verified normal login.

Preview by default; --read makes one LOGIN + gateway session and one Career
query for an explicit UID. No role creation, relogin, third-party provider,
remote-code execution, credential exports, vault or Desktop writes. The game
server decides availability/privacy; errors never become fabricated stats.

The library also supports the first other-player match-summary page. Its caller
must separately verify the global BattleHistoryIsVisibleToOther permission;
public Career overview visibility is insufficient. No match-summary CLI or
automatic live lookup is enabled here.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
import mmap
import secrets
import struct
import time

import msgpack

import game_login_probe as login
from game_transport_probe import ARCHIVE_PATH, _archive_helper, load_public_configuration
from nex_rpc_probe import decode_avatar_rpc


class CareerError(ValueError):
    """Fixed diagnostic codes only, never raw remote values."""


def rpc_index(name, salt):
    if not isinstance(name, str) or not isinstance(salt, str):
        raise CareerError("rpc_configuration_shape")
    digest = hashlib.md5((name + salt).encode("utf-8"), usedforsecurity=False).hexdigest()
    product = 1
    for offset in range(0, 16, 4):
        product *= int.from_bytes(digest[offset:offset + 4].encode("ascii"), "big") & 0x7FFFFFFF
    return ((product & 0x7FFFFFFF00000000) >> 32) ^ (product & 0x7FFFFFFF)


@dataclass(frozen=True)
class CareerMethods:
    request: int
    reply: int


ENTITY_CREATE = login.fields((1, "eid", 12), (2, "classname", 8), (3, "initdata", 10))
ENTITY_DESTROY = login.fields((1, "eid", 12))
ENTITY_PROP = login.fields((1, "entity_id", 12), (2, "sync_msg", 10))
MSG_ACK = login.fields((1, "ack", 6))
MSG_RESET = login.fields((1, "reset", 6))


def consume_startup_record(record, report):
    """Read only known data/lifecycle envelopes, never execute server code.

    Entry5053: destruction/creation synchronizes the local representation of
    an existing server entity; it does NOT request creation of an account role.
    An ACK is informational. A buffer-reset signal is not silently ignored.
    Initial own-entity data is not a source of other-player card statistics.
    """
    if record.main_cmd != 65534:
        return False
    if record.sub_cmd == 112:
        login.decode_body(MSG_RESET, record.body)
        raise CareerError("server_sequence_reset_requires_handling")
    schema = {111: MSG_ACK, 113: ENTITY_CREATE, 114: ENTITY_DESTROY, 120: ENTITY_PROP}.get(record.sub_cmd)
    if schema is None:
        return False
    value = login.decode_body(schema, record.body, max_body=4 * 1024 * 1024)
    if set(value) != {item.name for item in schema}:
        raise CareerError("startup_entity_shape")
    if record.sub_cmd in (113, 114):
        if value["eid"] <= 0:
            raise CareerError("startup_entity_id_shape")
        if record.sub_cmd == 113:
            if not 0 < len(value["classname"]) <= 128:
                raise CareerError("startup_entity_class_shape")
            report["own_entity_created"] |= value["eid"] == login.EXPECTED_OWN_UID
    report["startup_record_count"] += 1
    return True


def load_career_methods(archive=ARCHIVE_PATH):
    """Read pinned default method-index configuration; never execute game code."""
    helper = _archive_helper()
    with archive.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        payload, entries = helper.parse_index(data)
        decoder = helper.bootstrap_decoder(data, payload, entries)
        code = helper.parse_code(helper.reverse_module(helper.bounded_zlib(
            decoder.decrypt(helper.read_entry(data, payload, entries, 5719)))))
    # Both default salts use const3. Root assignments then copy those defaults
    # into RECV_RPC_SALT/SEND_RPC_SALT. Runtime hotfix overrides are not assumed.
    if (code.co_filename != "Scripts/core/rpc/rpc_index.py"
            or code.co_names[3:5] != ("DEFAULT_RECV_RPC_SALT", "DEFAULT_SEND_RPC_SALT")
            or code.co_code[18:26] != bytes((204, 3, 140, 3, 204, 3, 140, 4))
            or not isinstance(code.co_consts[3], str)):
        raise CareerError("rpc_configuration_changed")
    salt = code.co_consts[3]
    return CareerMethods(rpc_index("Career.C_QUERY_CAREER", salt), rpc_index("Career.S_QUERY_CAREER", salt))


def make_request(methods, uid, zone_id, season, nonce, *, own_uid=login.EXPECTED_OWN_UID):
    if (type(own_uid) is not int or own_uid <= 0
            or type(uid) is not int or not 0 < uid < 2**63 or uid == own_uid
            or type(zone_id) is not int or zone_id <= 0
            or type(season) is not int or not 0 <= season < 1000
            or not isinstance(nonce, str) or not 1 <= len(nonce) <= 64):
        raise CareerError("career_target_shape")
    # Entry1833 c_get_player_career_data defaults mode/play mode/sort count to0,
    # omits mode filters when0, uses current zone, and maps PC to device_type pc.
    data = {"player_uid": uid, "zone_id": zone_id, "device_type": "pc"}
    if season:
        data["match_season"] = season
    context = {"is_career": True, "career_req": dict(data), "mak_request_id": nonce}
    args = ["query_player_career", data, context]
    packed = msgpack.packb(args, use_bin_type=True)
    body = b"\x61" + struct.pack("<I", methods.request) + b"\xe2" + struct.pack("<I", len(packed)) + packed
    return struct.pack("<IHB", len(body) + 7, 3, 1) + body, data


def verify_career_args(args, requested, nonce):
    # Entries5308/1817: url, ex_info, err_code, err_msg, data. Validate the
    # method context AND returned Career UID, not just a hash or own login UID.
    if not isinstance(args, list) or len(args) != 5 or args[0] != "query_player_career":
        raise CareerError("career_reply_shape")
    context, code, _, data = args[1:]
    if (not isinstance(context, dict) or context.get("is_career") is not True
            or context.get("mak_request_id") != nonce or context.get("career_req") != requested):
        raise CareerError("career_request_context_mismatch")
    if type(code) is not int:
        raise CareerError("career_result_code_missing")
    if code != 0:
        return {"outcome": "career_server_error", "server_error_code": code, "career_verified": False}
    career = data.get("career") if isinstance(data, dict) else None
    if (not isinstance(career, dict) or type(career.get("player_uid")) is not int
            or career["player_uid"] != requested["player_uid"]):
        raise CareerError("career_uid_mismatch")
    return {"outcome": "other_player_career_verified", "career_verified": True,
            "career_field_count": len(career), "target_uid": requested["player_uid"],
            "season": requested.get("match_season", 0)}


def make_match_summary_request(methods, uid, zone_id, nonce, *, own_uid=login.EXPECTED_OWN_UID):
    """Build only page zero of the normal other-player match-summary RPC.

    The statically inspected match-history handler supplies player_uid, zone_id,
    page and page_size. Optional mode/season/device filters are omitted. This
    builder does not establish BattleHistoryIsVisibleToOther permission.
    """
    if (type(own_uid) is not int or not 0 < own_uid < 2**63
            or type(uid) is not int or not 0 < uid < 2**63 or uid == own_uid
            or type(zone_id) is not int or zone_id <= 0
            or not isinstance(nonce, str) or not 1 <= len(nonce) <= 64):
        raise CareerError("match_summary_target_shape")
    data = {"player_uid": uid, "zone_id": zone_id, "page": 0, "page_size": 50}
    context = {"is_career": True, "career_req": dict(data), "mak_request_id": nonce}
    packed = msgpack.packb(["query_match_summary", data, context], use_bin_type=True)
    body = b"\x61" + struct.pack("<I", methods.request) + b"\xe2" + struct.pack("<I", len(packed)) + packed
    return struct.pack("<IHB", len(body) + 7, 3, 1) + body, data


def verify_match_summary_args(args, requested, nonce):
    """Bind the whole first-page reply, including empty results, to its request.

    Each returned match_player must identify the requested other player, not
    the shared login owner. This validates transport correlation, not privacy
    permission, individual stat fields, pagination or history completeness.
    """
    if (not isinstance(requested, dict)
            or set(requested) != {"player_uid", "zone_id", "page", "page_size"}
            or type(requested["player_uid"]) is not int or not 0 < requested["player_uid"] < 2**63
            or type(requested["zone_id"]) is not int or requested["zone_id"] <= 0
            or type(requested["page"]) is not int or requested["page"] != 0
            or type(requested["page_size"]) is not int or requested["page_size"] != 50
            or not isinstance(nonce, str) or not 1 <= len(nonce) <= 64):
        raise CareerError("match_summary_request_shape")
    if not isinstance(args, list) or len(args) != 5 or args[0] != "query_match_summary":
        raise CareerError("match_summary_reply_shape")
    context, code, _, data = args[1:]
    echoed = context.get("career_req") if isinstance(context, dict) else None
    if (not isinstance(context, dict) or context.get("is_career") is not True
            or context.get("mak_request_id") != nonce or not isinstance(echoed, dict)
            or echoed != requested or any(type(echoed[key]) is not int for key in requested)):
        raise CareerError("match_summary_request_context_mismatch")
    if type(code) is not int:
        raise CareerError("match_summary_result_code_missing")
    if code != 0:
        return {"outcome": "match_summary_server_error", "server_error_code": code,
                "match_summary_verified": False}
    rows = data.get("match_info") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > 50:
        raise CareerError("match_summary_rows_shape")
    for row in rows:
        player = row.get("match_player") if isinstance(row, dict) else None
        if (not isinstance(player, dict) or type(player.get("player_uid")) is not int
                or player["player_uid"] != requested["player_uid"]):
            raise CareerError("match_summary_uid_mismatch")
    return {"outcome": "other_player_match_summary_verified", "match_summary_verified": True,
            "target_uid": requested["player_uid"], "match_count": len(rows),
            "page": 0, "page_size": 50, "has_more": None}


def read_one_career(connection, transport, zone, methods, uid, season, *, on_verified=None,
                    own_uid=login.EXPECTED_OWN_UID):
    nonce = secrets.token_hex(16)
    request, requested = make_request(methods, uid, zone.get("zone_id"), season, nonce, own_uid=own_uid)
    return _read_one_reply(connection, transport, methods, request, requested, nonce,
                           verify_career_args, "career", on_verified)


def read_one_match_summary(connection, transport, zone, methods, uid, *, on_verified=None,
                          own_uid=login.EXPECTED_OWN_UID):
    """Read one bounded first page after normal login and match-history permission.

    The caller must have independently verified global
    BattleHistoryIsVisibleToOther. A public Career overview is not sufficient.
    No authentication, permission default, retry or next-page fetch is performed.
    Only the trusted callback receives transient, request/UID-bound raw rows;
    individual statistic fields still require the caller's normal projection.
    """
    nonce = secrets.token_hex(16)
    request, requested = make_match_summary_request(methods, uid, zone.get("zone_id"), nonce, own_uid=own_uid)
    return _read_one_reply(connection, transport, methods, request, requested, nonce,
                           verify_match_summary_args, "match_summary", on_verified)


def _read_one_reply(connection, transport, methods, request, requested, nonce, verify, kind, on_verified):
    verified_key = kind + "_verified"
    report = {"outcome": kind + "_not_sent", verified_key: False, "bytes_received": 0,
              "application_bytes_sent": 0, "records_received": 0, "other_rpc_count": 0,
              "startup_record_count": 0, "own_entity_created": False,
              "remote_code_executed": False, "secrets_saved": False}
    try:
        connection.sendall(transport.encrypt(request))
        report["application_bytes_sent"] = len(request)
        deadline = time.monotonic() + 15
        for _ in range(128):
            record = login.receive_record(connection, transport, deadline, report, max_size=4 * 1024 * 1024)
            report["records_received"] += 1
            if report["bytes_received"] > 4 * 1024 * 1024:
                raise CareerError(kind + "_receive_bound")
            if consume_startup_record(record, report):
                continue
            if (record.main_cmd, record.sub_cmd) != (3, 101):
                report.update(unexpected_main=record.main_cmd, unexpected_sub=record.sub_cmd)
                raise CareerError("unrecognized_" + kind + "_startup_command")
            rpc = decode_avatar_rpc(record, direction="in")
            if rpc.index != methods.reply:
                report["other_rpc_count"] += 1
                continue  # Do not execute unrelated own-avatar initialization RPCs.
            report.update(verify(rpc.args, requested, nonce))
            if report[verified_key] and on_verified is not None:
                # Raw replies stay transient; only this trusted local projector
                # receives the UID/context-verified payload. CLI remains redacted.
                on_verified(rpc.args[4])
            return report
        raise CareerError(kind + "_record_bound")
    except (CareerError, login.LoginError) as exc:
        report["outcome"] = str(exc)
    except TimeoutError:
        report["outcome"] = kind + "_reply_timeout"
    except EOFError:
        report["outcome"] = kind + "_connection_closed"
    except OSError:
        report["outcome"] = kind + "_network_error"
    except ValueError:
        report["outcome"] = kind + "_reply_unvalidated"
    report[verified_key] = False
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", type=int, required=True)
    parser.add_argument("--season", type=int, default=19)
    parser.add_argument("--read", action="store_true")
    args = parser.parse_args(argv)
    # Validate even preview without loading credentials or contacting a server.
    try:
        make_request(CareerMethods(1, 2), args.uid, 1, args.season, "preview")
        if not args.read:
            print(json.dumps({"outcome": "preview", "network_attempted": False, "credentials_read": False}))
            return 0
        methods = load_career_methods()
        configuration = load_public_configuration()
        inputs = login.load_own_login_inputs()
        server = login.advertised_server()
        report = login.check_login(configuration, server, inputs=inputs, enter=True,
            after_login=lambda conn, transport, zone: read_one_career(conn, transport, zone, methods, args.uid, args.season))
        print(json.dumps(report, indent=2))
        return 0
    except (CareerError, login.LoginError) as exc:
        print(json.dumps({"outcome": str(exc), "career_verified": False}))
        return 1
    except (ValueError, OSError, ImportError):
        print(json.dumps({"outcome": "career_inputs_unavailable", "career_verified": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
