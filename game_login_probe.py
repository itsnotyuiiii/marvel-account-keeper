"""Normal game LOGIN protocol with a preview-first diagnostic CLI.

Preview by default. --prepare checks the advertised production LOGIN service
without credentials. --authenticate uses the user's existing game-specific SDK
log in memory, sends one normal C_LOGIN_AUTH, verifies the expected existing UID,
and closes. --enter additionally uses the returned gateway and auth binding
for one normal login to the verified own existing role, then closes. These CLI
modes send no Career calls. The shared Career client can use a post-login
callback on this verified connection. No retries, credential exports, game or
process access, C_LOGIN_RELOGIN, attestation changes, vault or Desktop writes.

Schema: installed archive entry5050 login_pto.py; normal flow entry7800.
Native 0x5485340/0x5485ba4 maps schema types to wire widths. Tag writer
0x5484cd0 uses BIG-endian two-byte extended tags (ids16+), while lengths and
integers are little-endian. Structs and arrays use byte lengths, not counts;
array dictionaries are individually length-prefixed (0x5481480/0x5483b60).
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
import datetime as dt
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import secrets
import socket
import struct
import time

from game_config_probe import load_config_index, read_config, SHIPPING_CONFIG, shipping_login_url
from game_transport_probe import load_public_configuration, prepare_transport, _receive_exact
from nex_rpc_probe import split_plaintext_records


MAX_BODY = 64 * 1024
MAX_ITEMS = 32
EXPECTED_OWN_UID = 1370598422
CLIENT_VERSION = "3825464"  # Hash-pinned native build string: 1.1.3825464.3825464.
INTEGER_FORMATS = {1: "b", 2: "B", 3: "h", 4: "H", 5: "i", 6: "I", 12: "q"}
WIRE_TYPES = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 8: 7, 9: 7, 10: 7, 11: 7, 12: 4}


class LoginError(ValueError):
    """Fixed diagnostics only; never include credential-bearing values."""


@dataclass(frozen=True)
class Field:
    number: int
    name: str
    kind: int
    children: tuple = ()


def fields(*rows):
    return tuple(Field(*row) for row in rows)


VERSION = fields((1, "platform", 8), (2, "version", 8))
PREPARE = fields((1, "version_list", 9, VERSION), (2, "args", 10),
                 (3, "is_test_branch", 1), (4, "timestamp", 5), (5, "timezone", 1))
ROLE_INFO = fields((1, "name", 8), (2, "grade", 3))
EXT_INFO = fields((1, "ds_version_info", 8), (2, "mos_commit_time", 8),
                  (3, "mos_branch_name", 8), (4, "zone_env", 4),
                  (5, "p4_submit_time", 8), (6, "snap_tag", 8))
ZONE = fields((1, "zone_id", 5), (2, "zone_name", 8), (3, "gate_ip", 8),
              (4, "gate_port", 4), (5, "role_uid", 12), (6, "role_info", 11, ROLE_INFO),
              (7, "role_inited", 1), (8, "ext_info", 11, EXT_INFO))
AUTH_REPLY = fields((1, "err_code", 5), (2, "sdk_login_str", 8), (3, "auth_hostid", 6),
                    (4, "auth_sn", 8), (5, "zone_list", 9, ZONE), (6, "origin_account", 8),
                    (7, "aid", 12), (8, "sdk_username", 8), (9, "timestamp", 12),
                    (10, "timestamp2", 12), (11, "recommend_username", 8))
ENTER_REQUEST = fields((1, "auth_hostid", 6), (2, "auth_sn", 8), (3, "device_id", 8),
                       (4, "basic_info", 8), (5, "uid", 12))
LOGIN_INFO = fields((1, "client_ver", 8), (2, "login_key", 8), (3, "uid", 12))
LOGIN_REPLY = fields((1, "errcode", 5), (2, "relogin", 1), (3, "info", 11, LOGIN_INFO),
                     (4, "timestamp", 12), (5, "timestamp2", 12), (6, "reason", 8))
HOTFIX_PUSH = fields((1, "split", 1), (2, "path", 8), (3, "content", 10), (4, "signature", 10))
CLIENT_INFO = fields((1, "ipv6", 8), (2, "nation", 8), (3, "old_accountid", 8),
                     (4, "location", 8), (5, "local_host_name", 8), (6, "bar_name", 8),
                     (7, "platform_type", 1))
AUTH_NAMES = (
    "sauth_json", "os_name", "channel", "sdkuid", "device_id", "app_channel",
    "client_version", "sdk_version", "udid", "ip_info", "ip", "device_model",
    "device_height", "device_width", "os_ver", "mac_addr", "isp", "network",
    "app_ver", "transid", "unisdk_deviceid", "is_emulator", "is_root", "imei",
    "country_code", "oaid", "engine_ver",
)
AUTH_REQUEST = tuple(Field(n, name, 5 if n in (13, 14) else 1 if n in (22, 23) else 8)
                     for n, name in enumerate(AUTH_NAMES, 1)) + fields(
                         (28, "info", 11, CLIENT_INFO), (29, "client_full_version", 8), (30, "is_gfn", 1))


def _tag(number, kind):
    if not 1 <= number < 4096 or kind not in WIRE_TYPES:
        raise LoginError("unsupported_field")
    wire = WIRE_TYPES[kind]
    return bytes([(wire << 5) | number]) if number <= 15 else struct.pack(
        ">H", (wire << 13) | 0x1000 | number)


def encode_body(schema, value, *, depth=0):
    if depth > 4 or not isinstance(value, dict):
        raise LoginError("invalid_body")
    if set(value) - {item.name for item in schema}:
        raise LoginError("unknown_field")
    result = bytearray()
    for item in schema:
        if item.name not in value:
            continue
        v = value[item.name]
        result.extend(_tag(item.number, item.kind))
        if item.kind in INTEGER_FORMATS:
            if type(v) is not int:
                raise LoginError("integer_type")
            try:
                result.extend(struct.pack("<" + INTEGER_FORMATS[item.kind], v))
            except struct.error as exc:
                raise LoginError("integer_range") from exc
            continue
        if item.kind == 8:
            if not isinstance(v, str):
                raise LoginError("string_type")
            raw = v.encode("utf-8")
        elif item.kind == 10:
            if not isinstance(v, bytes):
                raise LoginError("blob_type")
            raw = v
        elif item.kind == 11:
            raw = encode_body(item.children, v, depth=depth + 1)
        elif item.kind == 9:
            if not isinstance(v, list) or len(v) > MAX_ITEMS:
                raise LoginError("array_bound")
            parts = [encode_body(item.children, element, depth=depth + 1) for element in v]
            raw = b"".join(struct.pack("<I", len(part)) + part for part in parts)
        else:
            raise LoginError("unsupported_field")
        if len(raw) > MAX_BODY:
            raise LoginError("field_bound")
        result.extend(struct.pack("<I", len(raw)))
        result.extend(raw)
        if len(result) > MAX_BODY:
            raise LoginError("body_bound")
    return bytes(result)


def decode_body(schema, data, *, depth=0, max_body=MAX_BODY):
    if (type(max_body) is not int or not 1 <= max_body <= 4 * 1024 * 1024
            or depth > 4 or not isinstance(data, bytes) or len(data) > max_body):
        raise LoginError("body_bound")
    by_number = {item.number: item for item in schema}
    result, pos = {}, 0

    def take(size):
        nonlocal pos
        if not 0 <= size <= len(data) - pos:
            raise LoginError("truncated_body")
        raw = data[pos:pos + size]
        pos += size
        return raw

    while pos < len(data):
        first = take(1)[0]
        if first & 16:
            tag = (first << 8) | take(1)[0]
            number, wire = tag & 4095, tag >> 13
            if number <= 15:
                raise LoginError("noncanonical_tag")
        else:
            number, wire = first & 15, first >> 5
        item = by_number.get(number)
        if item is None or item.name in result or wire != WIRE_TYPES[item.kind]:
            raise LoginError("unknown_duplicate_or_mistyped_field")
        if item.kind in INTEGER_FORMATS:
            fmt = "<" + INTEGER_FORMATS[item.kind]
            value = struct.unpack(fmt, take(struct.calcsize(fmt)))[0]
        else:
            raw = take(struct.unpack("<I", take(4))[0])
            if item.kind == 8:
                try:
                    value = raw.decode("utf-8")
                except UnicodeError as exc:
                    raise LoginError("string_encoding") from exc
            elif item.kind == 10:
                value = raw
            elif item.kind == 11:
                value = decode_body(item.children, raw, depth=depth + 1, max_body=max_body)
            elif item.kind == 9:
                value, at = [], 0
                while at < len(raw):
                    if len(value) >= MAX_ITEMS or len(raw) - at < 4:
                        raise LoginError("array_bound")
                    length = struct.unpack_from("<I", raw, at)[0]
                    at += 4
                    if length > len(raw) - at:
                        raise LoginError("truncated_array")
                    value.append(decode_body(item.children, raw[at:at + length], depth=depth + 1, max_body=max_body))
                    at += length
            else:
                raise LoginError("unsupported_field")
        result[item.name] = value
    return result


@dataclass(frozen=True)
class LoginServer:
    host: str
    host_number: int
    ports: tuple[int, ...]


def parse_server_list(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 4096:
        raise LoginError("server_list_bound")
    try:
        rows = [line.split() for line in raw.decode("ascii").splitlines() if line.strip()]
        if len(rows) != 1 or len(rows[0]) != 7:
            raise LoginError("server_list_shape")
        name, host, port, host_number, status, start, port_range = rows[0]
        port, host_number, status, start = map(int, (port, host_number, status, start))
        available, *excluded = port_range.split(":")
        first, last = map(int, available.split("-"))
        ip = ipaddress.ip_address(host)
        if (name != "prod" or not ip.is_global or ip.version != 4 or status != 1
                or not 1 <= first <= port <= last <= 65535 or last - first > 31
                or not 1 <= host_number <= 65535 or start <= 0):
            raise LoginError("server_list_values")
        exclusions = {int(value) for value in excluded}
        if not exclusions <= set(range(first, last + 1)):
            raise LoginError("server_list_exclusions")
        ports = tuple(value for value in range(first, last + 1) if value not in exclusions)
        if not ports:
            raise LoginError("server_list_no_ports")
        return LoginServer(host, host_number, ports)
    except (UnicodeError, ValueError) as exc:
        raise LoginError("unrecognized_server_list") from exc


def advertised_server():
    import requests
    index = load_config_index()
    url = shipping_login_url(read_config(index, SHIPPING_CONFIG))
    with requests.get(url, timeout=10, allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            raise LoginError("production_list_unavailable")
        raw = response.raw.read(4097, decode_content=True)
    return parse_server_list(raw)


def receive_record(connection, transport, deadline, report, *, max_size=MAX_BODY):
    if type(max_size) is not int or not 7 <= max_size <= 4 * 1024 * 1024:
        raise LoginError("record_size_bound")
    prefix = transport.decrypt(_receive_exact(connection, 4, deadline, report))
    word = struct.unpack("<I", prefix)[0]
    size, flags = word & 0x0FFFFFFF, word >> 28
    if flags not in (0, 8) or not (9 if flags == 8 else 7) <= size <= max_size:
        report.update(unrecognized_record_flags=flags, unrecognized_record_size=size)
        raise LoginError("record_framing")
    # Avatar initialization can exceed LOGIN's64KiB bound. Preserve the same
    # directional cipher stream while decrypting separately bounded chunks.
    parts, remaining = [prefix], size - 4
    while remaining:
        count = min(remaining, MAX_BODY)
        parts.append(transport.decrypt(_receive_exact(connection, count, deadline, report)))
        remaining -= count
    raw = b"".join(parts)
    record, = split_plaintext_records(raw)
    return record


def verify_prepare(record, *, now=None):
    if (record.main_cmd, record.sub_cmd) != (1, 101):
        raise LoginError("unexpected_prepare_command")
    value = decode_body(PREPARE, record.body)
    now = time.time() if now is None else now
    if (set(value) != {item.name for item in PREPARE} or value["is_test_branch"] != 0
            or abs(value["timestamp"] - now) > 600):
        raise LoginError("unvalidated_prepare")
    # Do not execute server args or invent version/attestation responses.
    if value["args"] or value["version_list"]:
        raise LoginError("additional_prepare_requirements")
    return value


def verify_auth_role(reply, expected_uid):
    if type(expected_uid) is not int or expected_uid <= 0:
        raise LoginError("expected_uid_required")
    if reply.get("err_code") != 0:
        raise LoginError("auth_not_successful")
    if (type(reply.get("auth_hostid")) is not int or reply["auth_hostid"] <= 0
            or not isinstance(reply.get("auth_sn"), str) or not reply["auth_sn"]):
        raise LoginError("missing_auth_binding")
    zones = reply.get("zone_list")
    if not isinstance(zones, list):
        raise LoginError("missing_existing_roles")
    selected = [zone for zone in zones if zone.get("role_uid") == expected_uid]
    if len(selected) != 1:
        raise LoginError("expected_role_missing_or_ambiguous")
    zone = selected[0]
    if zone.get("role_inited") != 1:
        raise LoginError("existing_role_not_initialized")
    try:
        if (not ipaddress.ip_address(zone["gate_ip"]).is_global
                or type(zone["gate_port"]) is not int or not 1 <= zone["gate_port"] <= 65535):
            raise ValueError
    except (KeyError, ValueError, TypeError) as exc:
        raise LoginError("unvalidated_gateway") from exc
    return zone


def redacted_sdk_error(raw):
    """Expose only numeric SDK diagnostics, never the credential-bearing JSON.

    Entry7800 show_sauth_err_msg reads code, subcode and channel_msg.msg.
    Free-form messages are reduced to fixed keyword flags, not printed.
    """
    result = {"sdk_error_json_present": False}
    if not isinstance(raw, str) or not 0 < len(raw) <= MAX_BODY:
        return result
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return result
    if not isinstance(value, dict):
        return result
    result["sdk_error_json_present"] = True
    for key in ("code", "subcode"):
        number = value.get(key)
        if type(number) is int and abs(number) <= 2**31 - 1:
            result["sdk_" + key] = number
        elif isinstance(number, str) and re.fullmatch(r"-?\d{1,9}", number):
            result["sdk_" + key] = int(number)
    channel = value.get("channel_msg")
    message = channel.get("msg", "") if isinstance(channel, dict) else ""
    if isinstance(message, str):
        message = message.lower()
        result["sdk_message_mentions_expiry"] = any(word in message for word in ("expired", "expire", "\u8fc7\u671f"))
        result["sdk_message_mentions_session"] = "session" in message
        result["sdk_message_mentions_signature"] = "signature" in message
    return result


@dataclass(repr=False)
class LoginInputs:
    auth: dict = field(repr=False)
    source_timestamp: str


def _sdk_helper():
    path = Path(__file__).parent / "scripts" / "inspect-sdk-log.py"
    spec = importlib.util.spec_from_file_location("own_game_sdk_inspector", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)  # Our inspector, not recovered game code.
    return helper


SDK_PROPERTIES = {"SDC_LOG_OS_NAME", "SDC_LOG_DEVICE_MODEL", "SDC_LOG_OS_VER",
                  "SDC_LOG_NETWORK", "SDC_LOG_APP_VER", "DEVICE_DESKTOP_RESOLUTION",
                  "SDC_LOG_MAC_ADDR", "SDC_LOG_APP_ISP", "TRANS_ID"}


def _properties(obj):
    todo, found, count = [obj], {}, 0
    while todo and count < 200:
        value = todo.pop()
        count += 1
        if isinstance(value, dict):
            for key, child in value.items():
                if key in SDK_PROPERTIES and isinstance(child, str) and len(child) <= 512:
                    found[key] = child
                elif isinstance(child, (dict, list)):
                    todo.append(child)
                elif isinstance(child, str) and child.startswith("{") and len(child) <= MAX_BODY:
                    try:
                        todo.append(json.loads(child))
                    except (ValueError, RecursionError):
                        pass
        elif isinstance(value, list):
            todo.extend(value[:50])
    return found


def build_auth(bundle, properties):
    required = {"sdkuid", "gas_token", "sessionid", "gameid", "deviceid", "ip",
                "sdk_version", "app_channel", "login_channel", "udid", "aim_info"}
    if (not isinstance(bundle, dict) or not required <= bundle.keys()
            or bundle.get("gameid") != "x20naxx2gb" or bundle.get("platform") != "pc"
            or any(not isinstance(bundle[key], str) for key in required)
            or any(not bundle[key] for key in ("sdkuid", "sessionid", "deviceid"))):
        raise LoginError("incomplete_own_sdk_bundle")
    # This must be the POST-SDK bundle, not the earlier uni_sauth HTTP request.
    # gen_auth_info forwards sauth_json; it does not manufacture a gas token.
    if properties.get("SDC_LOG_OS_NAME") != "pc":
        raise LoginError("pc_sdk_properties_missing")
    dimensions = re.fullmatch(r"(\d{1,5})\*(\d{1,5})", properties.get("DEVICE_DESKTOP_RESOLUTION", ""))
    if dimensions is None:
        raise LoginError("sdk_display_properties_missing")
    # Same assignment order as gen_auth_info: first resolution component is height.
    height, width = map(int, dimensions.groups())
    auth = {item.name: 0 if item.kind in INTEGER_FORMATS else "" for item in AUTH_REQUEST}
    auth.update(sauth_json=json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
                os_name=properties["SDC_LOG_OS_NAME"], sdkuid=bundle["sdkuid"],
                channel=bundle["login_channel"], app_channel=bundle["app_channel"],
                device_id=bundle["deviceid"], unisdk_deviceid=bundle["deviceid"],
                sdk_version=bundle["sdk_version"], udid=bundle["udid"],
                ip=bundle["ip"], ip_info=bundle["aim_info"],
                client_version=CLIENT_VERSION, engine_ver=CLIENT_VERSION,
                device_height=height, device_width=width)
    for target, source in (("device_model", "SDC_LOG_DEVICE_MODEL"), ("os_ver", "SDC_LOG_OS_VER"),
                           ("network", "SDC_LOG_NETWORK"), ("app_ver", "SDC_LOG_APP_VER"),
                           ("mac_addr", "SDC_LOG_MAC_ADDR"), ("isp", "SDC_LOG_APP_ISP"), ("transid", "TRANS_ID")):
        auth[target] = properties.get(source, "")
    # Shipping gen_auth_info blanks local_host_name; EPlatformType.PC == 0,
    # not ELoginOS.PC == 1. Other optional SDK properties retain empty defaults.
    auth["info"] = {item.name: 0 if item.kind == 1 else "" for item in CLIENT_INFO}
    encode_body(AUTH_REQUEST, auth)  # Validate before opening a socket.
    return auth


def _sdk_json(raw, *, encoded=False):
    """Bounded JSON parsing with fixed errors, including duplicate-key rejection."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    if not isinstance(raw, str) or not 0 < len(raw) <= MAX_BODY:
        raise LoginError("sdk_response_shape")
    try:
        if encoded:
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        result = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError):
        raise LoginError("sdk_response_shape") from None
    if not isinstance(result, dict):
        raise LoginError("sdk_response_shape")
    return result


def finalize_sdk_bundle(bundle, response):
    """Reproduce the verified normal SDK response updates, entirely in memory.

    Hash-pinned NtUniSdkBase.dll: 0x416c0 base64-decodes UNISDK_LOGIN_JSON;
    0x4195b updates SAUTH_JSON.sdkuid through 0x42d50. 0x41fb2 parses the
    extra.channel_raw_info JSON string; 0x42208 prefers nonempty login_token
    over token, then 0x422de updates SAUTH_JSON.sessionid. Optional sdk_token
    fills an empty gas_token at 0x41a71/0x42740, NOT the OAuth access token.
    Returned role UID must still be verified by the actual login service.
    """
    if (not isinstance(bundle, dict) or not isinstance(response, dict)
            or type(response.get("code")) is not int or response["code"] != 200
            or response.get("status") != "ok"):
        raise LoginError("sdk_response_not_successful")
    inner = _sdk_json(response.get("unisdk_login_json"), encoded=True)
    channel, extra, data = inner.get("chl_sdk_json"), inner.get("extra"), response.get("data")
    if not all(isinstance(value, dict) for value in (channel, extra, data)):
        raise LoginError("sdk_response_binding_missing")
    # Correlate the same-time request/response by game, channel, device and
    # the response's internal identity. These are consistency checks, not
    # cryptographic signature verification or proof of the in-game role UID.
    for name in ("gameid", "login_channel", "udid"):
        if (not isinstance(bundle.get(name), str) or not bundle[name]
                or channel.get(name) != bundle[name]):
            raise LoginError("sdk_response_binding_mismatch")
    uid = inner.get("sdkuid")
    if (not isinstance(uid, str) or not 0 < len(uid) <= 256
            or response.get("sdkuid") != uid or channel.get("sdkuid") != uid
            or data.get("app_channel") != bundle.get("app_channel")):
        raise LoginError("sdk_response_binding_mismatch")
    aid = inner.get("aid")
    if (type(aid) not in (str, int) or not str(aid)
            or any(type(value) not in (str, int) or str(value) != str(aid)
                   for value in (response.get("aid"), channel.get("aid")))):
        raise LoginError("sdk_response_binding_mismatch")
    raw = _sdk_json(extra.get("channel_raw_info"))
    token, login_token = raw.get("token", ""), raw.get("login_token", "")
    if not isinstance(token, str) or not isinstance(login_token, str):
        raise LoginError("sdk_session_shape")
    selected = login_token or token
    if not 0 < len(selected) <= MAX_BODY // 2:
        raise LoginError("sdk_session_missing")
    result = dict(bundle)
    result.update(sdkuid=uid, sessionid=selected)
    sdk_token = inner.get("sdk_token", "")
    if not isinstance(sdk_token, str):
        raise LoginError("sdk_token_shape")
    if sdk_token and not result.get("gas_token"):
        result["gas_token"] = sdk_token
    return result


def select_sdk_login(records, *, now=None):
    """Select one unambiguous, same-time SDK transaction; never mix accounts.

    The observed log writes its response before a telemetry copy of the input
    bundle, so adjacency or forward-only pairing is incorrect. Exact timestamp
    plus finalize_sdk_bundle's channel/device checks bind the observed pair.
    A later incomplete/ambiguous transaction fails rather than reusing an older
    account. This deliberately does not implement session renewal.
    """
    groups, properties = {}, {}
    for stamp, obj in records:
        properties.update(_properties(obj))
        if not isinstance(obj, dict):
            continue
        kind = None
        if {"sdkuid", "gas_token", "sessionid", "gameid"} <= obj.keys() and obj.get("gameid") == "x20naxx2gb":
            kind = "bundles"
        elif "unisdk_login_json" in obj:
            kind = "responses"
        if not kind:
            continue
        try:
            stamp = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            raise LoginError("sdk_timestamp_shape") from None
        group = groups.setdefault(stamp, {"bundles": [], "responses": []})
        if obj not in group[kind]:
            group[kind].append(obj)
        if len(groups) > 64 or len(group[kind]) > 4:
            raise LoginError("sdk_transaction_bound")
    if not groups:
        raise LoginError("own_sdk_bundle_not_found")
    stamp = max(groups)
    group = groups[stamp]
    if len(group["bundles"]) != 1 or len(group["responses"]) != 1:
        raise LoginError("sdk_transaction_missing_or_ambiguous")
    now = dt.datetime.now() if now is None else now
    # A local staleness bound is NOT token/server-validity verification.
    if not -3600 <= (now - stamp).total_seconds() <= 36 * 3600:
        raise LoginError("own_sdk_login_too_old")
    final = finalize_sdk_bundle(group["bundles"][0], group["responses"][0])
    return LoginInputs(build_auth(final, properties), stamp.strftime("%Y-%m-%d %H:%M:%S"))


def _own_sdk_records(helper, key, current):
    for path in (current.with_name(current.name + ".1"), current):
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            total = 0
            for number in range(helper.MAX_RECORDS + 1):
                encoded = stream.readline(helper.MAX_ENCODED_RECORD_BYTES + 3)
                if not encoded:
                    break
                total += len(encoded)
                if total > helper.MAX_LOG_BYTES or number == helper.MAX_RECORDS:
                    raise LoginError("sdk_log_bound")
                try:
                    line = helper.decode_record(encoded, key)
                except ValueError:
                    continue
                obj = helper.first_json_object(line)
                stamp = helper.TIMESTAMP.search(line[:80])
                if stamp:
                    yield stamp.group(0), obj


def load_own_login_inputs():
    helper = _sdk_helper()
    key = helper.load_log_parameter()
    return select_sdk_login(_own_sdk_records(helper, key, helper.default_log_path()))


def verify_gateway_login(reply, expected_uid):
    if type(expected_uid) is not int or expected_uid <= 0:
        raise LoginError("expected_uid_required")
    if type(reply.get("errcode")) is not int or reply["errcode"] != 0:
        raise LoginError("gateway_login_not_successful")
    # Entry7800 s_login_res uses msg.get('relogin', False) only for logging;
    # it is not an authentication gate or permission to send C_LOGIN_RELOGIN.
    # The server can report an already-resident avatar after C_LOGIN_ENTER.
    relogin = reply.get("relogin", 0)
    if type(relogin) is not int or relogin not in (0, 1):
        raise LoginError("unexpected_relogin_result")
    info = reply.get("info")
    if (not isinstance(info, dict) or type(info.get("uid")) is not int
            or info["uid"] != expected_uid):
        raise LoginError("gateway_role_mismatch")
    if not isinstance(info.get("login_key"), str) or not info["login_key"]:
        raise LoginError("gateway_login_binding_missing")


def check_gateway(configuration, auth_reply, *, expected_uid=EXPECTED_OWN_UID,
                  connect=socket.create_connection, after_login=None):
    """One fresh gateway connection, bound to a successful own-role auth reply.

    Entry7800 send_login_enter leaves device_id/basic_info empty and sends
    main1/sub3 after the normal handshake. Never choose a different role, create
    one, retry, send relogin, or output/save the returned game login_key.
    """
    zone = verify_auth_role(auth_reply, expected_uid)
    body = encode_body(ENTER_REQUEST, {"auth_hostid": auth_reply["auth_hostid"],
                       "auth_sn": auth_reply["auth_sn"], "device_id": "",
                       "basic_info": "", "uid": expected_uid})
    report = {"outcome": "gateway_not_connected", "connections_attempted": 0,
              "bytes_received": 0, "handshake_bytes_sent": 0, "application_bytes_sent": 0,
              "credentials_sent": False, "game_login_verified": False,
              "career_verified": False, "secrets_saved": False, "startup_hotfix_records": 0,
              "startup_hotfix_bytes": 0, "remote_code_executed": False}
    stage = "gateway_connect"
    try:
        report["connections_attempted"] = 1
        deadline = time.monotonic() + 15
        with connect((zone["gate_ip"], zone["gate_port"]), timeout=5) as connection:
            stage = "gateway_opening"
            opening = _receive_exact(connection, 28, deadline, report)
            transport = prepare_transport(opening, configuration)
            connection.sendall(transport.acknowledgement)
            report["handshake_bytes_sent"] = len(transport.acknowledgement)
            stage = "gateway_login"
            request = struct.pack("<IHB", len(body) + 7, 1, 3) + body
            report["credentials_sent"] = True
            connection.sendall(transport.encrypt(request))
            report["application_bytes_sent"] = len(request)
            for _ in range(17):
                record = receive_record(connection, transport, deadline, report)
                if (record.main_cmd, record.sub_cmd) != (4, 102):
                    break
                # Entry5049: the gateway sends S_PUSH_CLIENT_HOTFIX before its
                # LOGIN result. Validate/bound the envelope, never execute it.
                if report["startup_hotfix_records"] >= 16:
                    raise LoginError("startup_hotfix_bound")
                update = decode_body(HOTFIX_PUSH, record.body)
                if set(update) != {item.name for item in HOTFIX_PUSH}:
                    raise LoginError("startup_hotfix_shape")
                report["startup_hotfix_records"] += 1
                report["startup_hotfix_bytes"] += len(update["content"])
            if (record.main_cmd, record.sub_cmd) != (1, 103):
                report.update(reply_main=record.main_cmd, reply_sub=record.sub_cmd)
                raise LoginError("unexpected_gateway_login_command")
            reply = decode_body(LOGIN_REPLY, record.body)
            if "errcode" not in reply:
                raise LoginError("missing_gateway_login_result")
            if reply["errcode"] != 0:
                report.update(outcome="gateway_rejected_login", server_error_code=reply["errcode"])
                return report
            info = reply.get("info")
            report.update(reply_relogin=reply.get("relogin", 0),
                          login_result_uid_present=isinstance(info, dict) and "uid" in info,
                          login_result_uid_matches=isinstance(info, dict) and info.get("uid") == expected_uid,
                          login_key_present=isinstance(info, dict) and isinstance(info.get("login_key"), str)
                          and bool(info["login_key"]))
            verify_gateway_login(reply, expected_uid)
            report.update(outcome="own_gateway_login_verified", game_login_verified=True)
            if after_login is not None:
                stage = "career"
                result = after_login(connection, transport, zone)
                report["career"] = result
                report["career_verified"] = result.get("career_verified") is True
            # Socket closure ends this check. No relogin or role creation.
    except LoginError as exc:
        report["outcome"] = str(exc)
    except TimeoutError:
        report["outcome"] = stage + "_timeout"
    except EOFError:
        report["outcome"] = stage + "_closed"
    except OSError:
        report["outcome"] = stage + "_network_error"
    except ValueError:
        report["outcome"] = stage + "_unvalidated"
    return report


def check_login(configuration, server, *, inputs=None, expected_uid=EXPECTED_OWN_UID,
                connect=socket.create_connection, enter=False, after_login=None):
    report = {"outcome": "not_connected", "connections_attempted": 0, "bytes_received": 0,
              "handshake_bytes_sent": 0, "application_bytes_sent": 0, "credentials_sent": False,
              "prepare_verified": False, "auth_service_accepted": False, "own_role_verified": False,
              "game_login_verified": False, "career_verified": False, "secrets_saved": False}
    if (enter and inputs is None) or (after_login is not None and not enter):
        raise LoginError("gateway_requires_authentication")
    body = encode_body(AUTH_REQUEST, inputs.auth) if inputs is not None else None
    if body is not None and len(body) + 7 > MAX_BODY:
        raise LoginError("auth_message_bound")
    peer = (server.host, secrets.choice(server.ports))
    stage = "connect"
    try:
        report["connections_attempted"] = 1
        deadline = time.monotonic() + 15
        with connect(peer, timeout=5) as connection:
            stage = "opening"
            opening = _receive_exact(connection, 28, deadline, report)
            transport = prepare_transport(opening, configuration)
            connection.sendall(transport.acknowledgement)
            report["handshake_bytes_sent"] = len(transport.acknowledgement)
            stage = "prepare"
            record = receive_record(connection, transport, deadline, report)
            verify_prepare(record)
            report.update(prepare_verified=True, outcome="login_prepare_verified")
            if body is None:
                return report
            request = struct.pack("<IHB", len(body) + 7, 1, 1) + body
            stage = "authentication"
            # Mark attempted credential transmission before sendall, which can fail partially.
            report["credentials_sent"] = True
            connection.sendall(transport.encrypt(request))
            report["application_bytes_sent"] = len(request)
            record = receive_record(connection, transport, deadline, report)
            if (record.main_cmd, record.sub_cmd) != (1, 102):
                raise LoginError("unexpected_auth_command")
            reply = decode_body(AUTH_REPLY, record.body)
            if "err_code" not in reply:
                raise LoginError("missing_auth_result")
            if reply["err_code"] != 0:
                report.update(outcome="server_rejected_authentication", server_error_code=reply["err_code"])
                if reply["err_code"] == 253:  # Installed share/errcode.py entry875.
                    report["game_error_label"] = "LOGIN_ERR_AUTH_FAIL"
                    report.update(redacted_sdk_error(reply.get("sdk_login_str")))
                return report
            report["auth_service_accepted"] = True
            verify_auth_role(reply, expected_uid)
            report.update(outcome="own_existing_role_verified", own_role_verified=True)
            if enter:
                gateway = check_gateway(configuration, reply, expected_uid=expected_uid, connect=connect,
                                        after_login=after_login)
                report.update(gateway=gateway, game_login_verified=gateway["game_login_verified"],
                              career_verified=gateway["career_verified"], outcome=gateway["outcome"])
    except LoginError as exc:
        report["outcome"] = str(exc)
    except TimeoutError:
        report["outcome"] = stage + "_timeout"
    except EOFError:
        report["outcome"] = stage + "_closed"
    except OSError:
        report["outcome"] = stage + "_network_error"
    except ValueError:
        report["outcome"] = stage + "_unvalidated"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--prepare", action="store_true")
    actions.add_argument("--authenticate", action="store_true")
    actions.add_argument("--enter", action="store_true")
    args = parser.parse_args(argv)
    if not args.prepare and not args.authenticate and not args.enter:
        print(json.dumps({"outcome": "preview", "network_attempted": False, "credentials_read": False}))
        return 0
    try:
        configuration = load_public_configuration()  # Also gates the installed client-version constant.
        server = advertised_server()
        inputs = load_own_login_inputs() if args.authenticate or args.enter else None
        report = check_login(configuration, server, inputs=inputs, enter=args.enter)
        print(json.dumps(report, indent=2))
        return 0
    except LoginError as exc:
        print(json.dumps({"outcome": str(exc), "credentials_sent": False,
                          "game_login_verified": False, "career_verified": False}))
        return 1
    except (OSError, ValueError, ImportError):
        print(json.dumps({"outcome": "login_inputs_unavailable", "credentials_sent": False,
                          "game_login_verified": False, "career_verified": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
