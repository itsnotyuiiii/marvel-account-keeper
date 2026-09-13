"""One normal owner rank-panel RPC on an already verified fresh gateway.

Current 3848703 archive SHA256:
07a3dcf20d539b9a613246047f5f91e4c7b44de62e96e24c47acea1cef5d1f9f.
1874 MosClient_Rank.c_req_rank_info:96 sends RankGame.C_OPEN_PANEL(1);
on_avatar_created:262 makes this normal read. 5439 RankGameComponent:15
has VAR_NAME='RankGame'; S_OPEN_PANEL:61 accepts one Dict. 5782:283 and
5784:109 register the component prefix; 5781:32/44/48 supplies the reviewed
default RPC index algorithm/salts. No startup code or hotfix is executed.
Current5113 avatar_pto keeps main3/sub1 and sub101 with uint32 field1 RPC
index and blob field2 args. Unrelated argument blobs are not MessagePack-decoded.

1874 s_open_panel:188 reads cur_game_id, left_time, open, optional start_time,
and cur_data.level/rank_score. This path avoids the unreviewed native property
blob decoder. It is NOT the synchronized all-season RankGame.ranks collection.

Caller must invoke once on its exact-owner LOGIN-verified fresh connection,
with no previous/pending rank-panel operation. The protocol has no request
nonce or required UID echo: same connection + exact method + one operation is
the available correlation, not proof that an old queued reply is impossible.
Caller owns socket cleanup and must not reuse it after an unsuccessful read.
No authentication, target UID argument, file access or automatic call exists.
"""
from __future__ import annotations

import math
import struct
import time

import msgpack

import game_career_probe as career
import game_login_probe as login
from nex_rpc_probe import decode_avatar_rpc


REVIEWED_BUILD = "3848703"
REQUEST_INDEX = 1543545906  # RankGame.C_OPEN_PANEL
REPLY_INDEX = 1616581796  # RankGame.S_OPEN_PANEL
NORMAL_RANK_TYPE = 1
MAX_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 128
MAX_HOTFIX_RECORDS = 16
TIMEOUT_SECONDS = 15
MAX_POINTS = 1_000_000
RPC_ENVELOPE = login.fields((1, "rpc_index", 6), (2, "args", 10))
ERROR_CODES = frozenset({
    "rank_panel_inputs_invalid", "rank_panel_argument_shape", "rank_panel_season_invalid",
    "rank_panel_receive_bound", "rank_panel_record_bound",
    "rank_panel_startup_rejected", "rank_panel_hotfix_bound", "rank_panel_hotfix_shape",
    "rank_panel_unrecognized_startup_command", "rank_panel_timeout",
    "rank_panel_connection_closed", "rank_panel_network_error", "rank_panel_reply_unvalidated",
    "rank_panel_unexpected", "rank_panel_rank_shape", "rank_panel_level_invalid",
    "rank_panel_score_invalid", "rank_panel_deadline_invalid",
})


class RankPanelError(ValueError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in ERROR_CODES else "rank_panel_reply_unvalidated"
        super().__init__(self.code)


def _kind(container, key):
    if type(container) is not dict or key not in container:
        return "missing"
    return {type(None): "null", bool: "bool", int: "int", float: "float", str: "str",
            dict: "dict", list: "list", bytes: "bytes"}.get(type(container[key]), "other")


def _number(value, low, high):
    # Check magnitude before isfinite: arbitrarily large ints must not overflow.
    return (value if type(value) in (int, float) and low <= value <= high
            and math.isfinite(value) else None)


def _integer(value, low, high):
    number = _number(value, low, high)
    return int(number) if number is not None and int(number) == number else None


def make_rank_panel_request():
    """The normal no-target request: a single integer rank-game type argument."""
    packed = msgpack.packb([NORMAL_RANK_TYPE], use_bin_type=True)
    body = b"\x61" + struct.pack("<I", REQUEST_INDEX) + b"\xe2" + struct.pack("<I", len(packed)) + packed
    return struct.pack("<IHB", len(body) + 7, 3, 1) + body


def project_rank_panel_args(args):
    """Pure numeric projection, without claiming owner/transport provenance.

    A valid current-season reply remains useful if optional own rank data is
    absent or malformed. Native score fallback 0.0 is not evidence of a score;
    this projection keeps a missing or invalid score unknown instead.
    """
    if type(args) is not list or len(args) != 1 or type(args[0]) is not dict or len(args[0]) > 512:
        raise RankPanelError("rank_panel_argument_shape")
    info = args[0]
    season = _integer(info.get("cur_game_id"), 1, 999)
    if season is None:
        raise RankPanelError("rank_panel_season_invalid")
    open_value = info.get("open")
    open_int = _integer(open_value, 0, 1)
    is_open = open_value if type(open_value) is bool else (bool(open_int) if open_int is not None else None)
    shape = {name + "_type": _kind(info, name) for name in (
        "cur_game_id", "left_time", "open", "start_time", "cur_data")}
    data = info.get("cur_data")
    shape.update({name + "_type": _kind(data, name) for name in ("level", "rank_score")})
    result = {
        "current_season_verified": True, "current_season": season, "season_open": is_open,
        "seconds_left": _number(info.get("left_time"), -(10**10), 10**10),
        "start_time": _number(info.get("start_time"), 0, 10**12),
        "own_rank_verified": False, "own_rank_present": bool(data), "own_rank_error": None,
        "current": None, "rank_labels_verified": False, "overall_peak_verified": False,
        "rank_history_verified": False, "shape": shape,
    }
    if data is None or data == {}:
        return result
    if type(data) is not dict or len(data) > 512:
        result["own_rank_error"] = "rank_panel_rank_shape"
        return result
    level = _integer(data.get("level"), 0, 65535)
    points = _number(data.get("rank_score"), 0, MAX_POINTS)
    if level is None:
        result["own_rank_error"] = "rank_panel_level_invalid"
        return result
    result["current"] = {"season": season, "level": level, "points": points,
                         "points_basis": "server_rank_score", "points_verified": points is not None}
    if points is None:
        result["own_rank_error"] = "rank_panel_score_invalid"
    else:
        result["own_rank_verified"] = True
    return result


def _report():
    return {
        "outcome": "rank_panel_not_sent", "owner_context_valid": False, "uid": None,
        "request_attempted": False, "request_sent": False, "request_count": 0,
        "application_bytes_sent": 0, "bytes_received": 0, "records_received": 0,
        "reply_method_received": False, "rank_panel_verified": False,
        "current_season_verified": False, "current_season": None, "own_rank_verified": False,
        "current": None, "own_rank_error": None, "other_rpc_count": 0,
        "startup_record_count": 0, "entity_create_records": 0, "entity_destroy_records": 0,
        "startup_hotfix_records": 0, "startup_hotfix_bytes": 0,
        "identity_basis": "caller_verified_owner_gateway", "response_identity_echo_verified": False,
        "correlation_basis": "fresh_connection_single_operation_exact_method", "nonce_correlated": False,
        "rank_labels_verified": False, "rank_history_verified": False, "overall_peak_verified": False,
        "connection_reuse_allowed": True, "remote_code_executed": False, "secrets_saved": False,
    }


def read_rank_panel_once(connection, transport, zone, *, client_version=REVIEWED_BUILD, deadline=None):
    """Send exactly one normal C_OPEN_PANEL(1), then return only fixed/numeric data.

    `zone` is the trusted normal AUTH-selected owner context passed by the
    gateway's after-LOGIN callback; this helper does not itself authenticate it.
    It does not close or replace the caller's connection. A successful result
    permits the owning orchestration to continue its separate authorized work.
    Optional deadline is absolute monotonic time and only shortens the normal
    15-second send/receive budget. A no-cipher/no-I/O skip leaves it untouched.
    """
    report = _report()
    if (type(client_version) is not str or client_version != REVIEWED_BUILD
            or type(zone) is not dict or type(zone.get("zone_id")) is not int
            or not 0 < zone["zone_id"] < 2**31 or type(zone.get("role_uid")) is not int
            or zone["role_uid"] != login.EXPECTED_OWN_UID):
        report["outcome"] = "rank_panel_inputs_invalid"
        return report
    report.update(owner_context_valid=True, uid=str(zone["role_uid"]))
    try:
        deadline = career._query_deadline(deadline, "rank_panel")
    except career.CareerError:
        report["outcome"] = "rank_panel_deadline_invalid"
        return report
    except TimeoutError:
        report["outcome"] = "rank_panel_timeout"
        return report
    try:
        request = make_rank_panel_request()
        report["connection_reuse_allowed"] = False
        encrypted = transport.encrypt(request)
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            raise TimeoutError
        connection.settimeout(remaining_time)
        report.update(request_attempted=True, request_sent=None, request_count=1,
                      application_bytes_sent=None)
        connection.sendall(encrypted)
        report.update(request_sent=True, application_bytes_sent=len(request))
        for _ in range(MAX_RECORDS):
            remaining = MAX_BYTES - report["bytes_received"]
            if remaining < 7:
                raise RankPanelError("rank_panel_receive_bound")
            try:
                record = login.receive_record(connection, transport, deadline, report, max_size=remaining)
            except login.LoginError:
                size = report.get("unrecognized_record_size")
                if type(size) is int and size > remaining:
                    raise RankPanelError("rank_panel_receive_bound") from None
                raise
            report["records_received"] += 1
            if report["bytes_received"] > MAX_BYTES:
                raise RankPanelError("rank_panel_receive_bound")
            telemetry = {"own_entity_created": False, "startup_record_count": 0}
            if career.consume_startup_record(record, telemetry):
                # Engine eid is independent of account uid; never compare them.
                report["startup_record_count"] += 1
                if record.sub_cmd == 113:
                    report["entity_create_records"] += 1
                elif record.sub_cmd == 114:
                    report["entity_destroy_records"] += 1
                continue
            if (record.main_cmd, record.sub_cmd) == (4, 102):
                if report["startup_hotfix_records"] >= MAX_HOTFIX_RECORDS:
                    raise RankPanelError("rank_panel_hotfix_bound")
                update = login.decode_body(login.HOTFIX_PUSH, record.body)
                if set(update) != {field.name for field in login.HOTFIX_PUSH}:
                    raise RankPanelError("rank_panel_hotfix_shape")
                report["startup_hotfix_records"] += 1
                report["startup_hotfix_bytes"] += len(update["content"])
                continue
            if (record.main_cmd, record.sub_cmd) != (3, 101):
                raise RankPanelError("rank_panel_unrecognized_startup_command")
            envelope = login.decode_body(RPC_ENVELOPE, record.body, max_body=MAX_BYTES)
            if set(envelope) != {"rpc_index", "args"}:
                raise RankPanelError("rank_panel_reply_unvalidated")
            if envelope["rpc_index"] != REPLY_INDEX:
                report["other_rpc_count"] += 1
                continue
            report["reply_method_received"] = True
            rpc = decode_avatar_rpc(record, direction="in")
            report.update(project_rank_panel_args(rpc.args))
            report.update(outcome="owner_rank_panel_verified", rank_panel_verified=True,
                          connection_reuse_allowed=True)
            return report
        raise RankPanelError("rank_panel_record_bound")
    except RankPanelError as error:
        report["outcome"] = error.code
    except (career.CareerError, login.LoginError):
        report["outcome"] = "rank_panel_startup_rejected"
    except TimeoutError:
        report["outcome"] = "rank_panel_timeout"
    except EOFError:
        report["outcome"] = "rank_panel_connection_closed"
    except OSError:
        report["outcome"] = "rank_panel_network_error"
    except (ValueError, TypeError, OverflowError, RecursionError):
        report["outcome"] = "rank_panel_reply_unvalidated"
    except Exception:
        report["outcome"] = "rank_panel_unexpected"
    return report
