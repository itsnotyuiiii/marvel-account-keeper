"""One explicit normal Python SocialMatrix setup, without a capture or query.

Reviewed current public source: Conf/Stub/SocketBase select JSON payload 1;
TCPConnection entry 40 sends the uncustomized Python hello on command 0;
SocialMatrixNet entry 3044 calls LIST_MODULE 259 with data command 0 and {}.
Constants entry 3498 identifies client version 3.10.1. This is that first-party
Python variant, not a claim that native overrides use the same hello/version.

Only a supplied fresh gateway token and supplied hash-pinned public parameter
are used. A single primary route connection is closed after setup. There are
no local files, retries, captured templates, HMAC signing, stats requests,
callbacks, renewal, payload execution or credential exports.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import socket
import struct
import time

from socialmatrix_capture_probe import RC4Stream, fold_xor, magic_signature, opening_fields
from standalone_social_session import (
    ERROR_CODES as TOKEN_ERROR_CODES, GatewaySocialToken, SocialSessionError,
    parse_gateway_social_token, social_token_metadata,
)


PARAMETER_SHA256 = "e7a322fd20ed8591c366213d4d4d9ca579a5c2d4e0ca11c8e7f63367c7aa5209"
MAX_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 20
MAX_SETUP_RECORDS = 16
CLIENT_VERSION = "3.10.1"
PYTHON_SOCKET_VERSION = "1.0.0"
# Source Task counter starts at zero: setup task consumes 1, LIST_MODULE uses 2.
SETUP_RPC_ID = "2"
ERROR_CODES = frozenset({
    "social_parameter_unreviewed", "social_transport_context", "social_json_invalid",
    "social_transport_signature_unsupported", "social_opening_invalid",
    "social_hello_invalid", "social_hello_rejected", "social_hello_codec_unsupported",
    "social_setup_invalid", "social_setup_rejected", "social_setup_identity_mismatch",
    "social_setup_correlation_missing", "social_stream_bound", "social_connection_closed",
    "social_transport_timeout", "social_transport_network_error", "social_transport_unexpected",
})
SETUP_VALIDATION_REASONS = frozenset({
    "payload_type", "str_payload_type", "payload_bound", "payload_missing",
    "payload_base64_invalid", "payload_base64_noncanonical", "payload_json_invalid",
    "data_nested_json_invalid", "data_not_object", "modules_type", "modules_bound",
    "module_not_object", "module_name_invalid", "module_command_type", "module_command_range",
})


class SocialTransportError(ValueError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in ERROR_CODES else "social_transport_unexpected"
        super().__init__(self.code)


def _report():
    return {"outcome": "social_setup_not_started", "stage": "validation",
            "connections_attempted": 0, "handshake_bytes_sent": 0,
            "application_bytes_sent": 0, "bytes_received": 0, "ignored_records": 0,
            "token_sent": False, "hello_accepted": False, "setup_request_sent": False,
            "setup_rpc_bound": False, "setup_aid_echoed": False, "streamid_present": False,
            "module_count": 0, "user_module_available": False, "setup_reply_command": None,
            "server_error_code": None, "social_session_accepted": False,
            "stats_requested": False, "secrets_saved": False,
            "validation_reason": None, "setup_shape": {}}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SocialTransportError("social_json_invalid")
        result[key] = value
    return result


def _bad_constant(value):
    raise SocialTransportError("social_json_invalid")


def _json(raw):
    if type(raw) is not bytes or not raw or len(raw) > MAX_BYTES:
        raise SocialTransportError("social_json_invalid")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_bad_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise SocialTransportError("social_json_invalid") from None
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 8192 or depth > 24:
            raise SocialTransportError("social_json_invalid")
        if type(item) is dict:
            if len(item) > 512:
                raise SocialTransportError("social_json_invalid")
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            if len(item) > 1024:
                raise SocialTransportError("social_json_invalid")
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise SocialTransportError("social_json_invalid") from None
        elif type(item) is float and not math.isfinite(item):
            raise SocialTransportError("social_json_invalid")
    return value


def _packed(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _check_signature_variant(session):
    # Optional signing is a distinct source-backed mode. This first adapter
    # does not silently ignore its HMAC metadata requirements.
    part = session.token.split(".")[1]
    claims = _json(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    tcp = claims["tcp"]
    if "Secure.SignatureEnabled" in tcp and tcp["Secure.SignatureEnabled"] is not False:
        raise SocialTransportError("social_transport_signature_unsupported")


def _clock_value(clock):
    value = clock()
    if type(value) not in (int, float) or not math.isfinite(value):
        raise SocialTransportError("social_transport_context")
    return value


class _Wire:
    def __init__(self, sock, report, clock, started):
        self.sock, self.report, self.clock = sock, report, clock
        self.deadline = started + MAX_SECONDS
        self.in_crypto = self.out_crypto = None

    def remaining(self):
        seconds = self.deadline - _clock_value(self.clock)
        if not 0 < seconds <= MAX_SECONDS:
            raise SocialTransportError("social_transport_timeout")
        self.sock.settimeout(seconds)

    def exact(self, size, *, encrypted=False):
        if type(size) is not int or size < 0 or self.report["bytes_received"] + size > MAX_BYTES:
            raise SocialTransportError("social_stream_bound")
        received = bytearray()
        while len(received) < size:
            self.remaining()
            chunk = self.sock.recv(size - len(received))
            if type(chunk) is not bytes or len(chunk) > size - len(received):
                raise SocialTransportError("social_connection_closed")
            if not chunk:
                raise SocialTransportError("social_connection_closed")
            received.extend(chunk)
            self.report["bytes_received"] += len(chunk)
        return self.in_crypto.process(received) if encrypted else bytes(received)

    def send_opening(self, data):
        self.remaining()
        self.report["handshake_bytes_sent"] = None
        self.sock.sendall(data)
        self.report["handshake_bytes_sent"] = len(data)

    def send(self, main, sub, body, *, carries_token=False):
        if len(body) + 7 > MAX_BYTES:
            raise SocialTransportError("social_stream_bound")
        self.remaining()
        packet = self.out_crypto.process(struct.pack("<IHB", len(body) + 7, main, sub) + body)
        previous = self.report["application_bytes_sent"]
        self.report["application_bytes_sent"] = None
        if carries_token:
            self.report["token_sent"] = None
            self.report["setup_request_sent"] = None
        self.sock.sendall(packet)
        self.report["application_bytes_sent"] = previous + len(packet)
        if carries_token:
            self.report["token_sent"] = True
            self.report["setup_request_sent"] = True

    def record(self):
        size, main, sub = struct.unpack("<IHB", self.exact(7, encrypted=True))
        if size < 7:
            raise SocialTransportError("social_stream_bound")
        return main, sub, self.exact(size - 7, encrypted=True)


def _status(container, report, failure):
    code = container.get("errorcode", 0)
    if type(code) is not int or not -(10**9) <= code <= 10**9:
        raise SocialTransportError("social_setup_invalid")
    if code != 0:
        report["server_error_code"] = code
        raise SocialTransportError(failure)


def _opaque(value, *, empty=False):
    if (type(value) is not str or (not value and not empty) or len(value) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise SocialTransportError("social_setup_invalid")
    return value


def _kind(value):
    return {type(None): "null", str: "string", bool: "boolean", int: "integer",
            float: "number", list: "array", dict: "object"}.get(type(value), "unsupported")


def _invalid(report, reason):
    if reason in SETUP_VALIDATION_REASONS:
        report["validation_reason"] = reason
    raise SocialTransportError("social_setup_invalid")


def _module_metadata(data, report=None):
    report = {"setup_shape": {}} if report is None else report
    shape = report["setup_shape"]
    shape["data_type"] = _kind(data)
    shape["nested_json_decoded"] = False
    if type(data) is str:
        # Entry 699 _on_list_module_cb deserializes non-dict data one more
        # time. This supports the normal nested JSON string, not recursion.
        try:
            data = _json(data.encode("utf-8"))
        except (SocialTransportError, UnicodeError):
            _invalid(report, "data_nested_json_invalid")
        shape["nested_json_decoded"] = True
        shape["nested_data_type"] = _kind(data)
    if type(data) is not dict:
        _invalid(report, "data_not_object")
    modules = data.get("modules")
    shape["modules_type"] = _kind(modules) if "modules" in data else "absent"
    if modules is None:
        modules = []
    if type(modules) is not list:
        _invalid(report, "modules_type")
    shape["modules_count"] = len(modules)
    if len(modules) > 100:
        _invalid(report, "modules_bound")
    selected = {}
    shape.update(skipped_modules=0, duplicate_modules=0)
    for index, module in enumerate(modules):
        shape.update(module_index=index, module_type=_kind(module), module_name_type="unobserved",
                     module_name_chars=None, module_command_type="unobserved", module_command_class="unobserved")
        if type(module) is not dict:
            _invalid(report, "module_not_object")
        name = module.get("name")
        shape.update(module_name_type=_kind(name) if "name" in module else "absent",
                     module_name_chars=len(name) if type(name) is str else None)
        # The normal callback skips falsey names before reading main_cmd.
        if not name:
            shape["skipped_modules"] += 1
            continue
        try:
            name = _opaque(name)
        except SocialTransportError:
            _invalid(report, "module_name_invalid")
        command = module.get("main_cmd", 0)
        shape["module_command_type"] = _kind(command) if "main_cmd" in module else "absent"
        if type(command) is not int:
            shape["module_command_class"] = "non_integer"
            _invalid(report, "module_command_type")
        shape["module_command_class"] = ("negative" if command < 0 else "zero" if command == 0
                                          else "in_range" if command <= 65535 else "above_max")
        if not 0 <= command <= 65535:
            _invalid(report, "module_command_range")
        if name in selected:
            shape["duplicate_modules"] += 1
        # Source map assignment overwrites a duplicate name; zero is allowed,
        # but does not establish an addressable positive service command.
        selected[name] = command
    return len(selected), selected.get("svc-user", 0) > 0


def _setup_payload(body, report=None):
    report = {"setup_shape": {}} if report is None else report
    shape = report["setup_shape"]
    value, plain = body.get("payload", ""), body.get("str_payload", "")
    shape.update(payload_type=_kind(value) if "payload" in body else "absent",
                 payload_chars=len(value) if type(value) is str else None,
                 str_payload_type=_kind(plain) if "str_payload" in body else "absent",
                 str_payload_chars=len(plain) if type(plain) is str else None)
    if value is not None and type(value) is not str:
        _invalid(report, "payload_type")
    if plain is not None and type(plain) is not str:
        _invalid(report, "str_payload_type")
    if (type(value) is str and len(value) > MAX_BYTES) or (type(plain) is str and len(plain) > MAX_BYTES):
        _invalid(report, "payload_bound")
    # Entry 3044 chooses a truthy payload first, otherwise str_payload. A
    # truthy str_payload disables base64 decoding even when payload won.
    shape["payload_selected"] = "payload" if value else "str_payload" if plain else "none"
    if not value:
        value = plain
    if not value:
        _invalid(report, "payload_missing")
    if plain:
        shape["payload_encoding"] = "direct_json"
        raw = value.encode("utf-8")
    else:
        shape["payload_encoding"] = "base64"
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, UnicodeError):
            _invalid(report, "payload_base64_invalid")
        if base64.b64encode(raw).decode("ascii") != value:
            _invalid(report, "payload_base64_noncanonical")
    shape["decoded_bytes"] = len(raw)
    try:
        return _json(raw)
    except SocialTransportError:
        report["validation_reason"] = "payload_json_invalid"
        raise


def _accept_setup(wire, session):
    report = wire.report
    for _ in range(MAX_SETUP_RECORDS):
        main, sub, raw = wire.record()
        if (main, sub) != (1, 3):
            report["ignored_records"] += 1
            continue
        envelope = _json(raw)
        if type(envelope) is not dict or type(envelope.get("header")) is not dict:
            raise SocialTransportError("social_setup_invalid")
        header = envelope["header"]
        if header.get("rpcid") != SETUP_RPC_ID or type(header.get("rpcid")) is not str:
            report["ignored_records"] += 1
            continue
        report["setup_rpc_bound"] = True
        _status(header, report, "social_setup_rejected")
        if "aid" in header:
            if type(header["aid"]) is not str or header["aid"] != session.aid:
                raise SocialTransportError("social_setup_identity_mismatch")
            report["setup_aid_echoed"] = True
        if header.get("streamid") is not None:
            report["streamid_present"] = bool(_opaque(header["streamid"], empty=True))
        body = envelope.get("body")
        if type(body) is not dict:
            raise SocialTransportError("social_setup_invalid")
        _status(body, report, "social_setup_rejected")
        command = body.get("cmd", 0)
        if type(command) is not int or not 0 <= command <= 0xFFFFFFFF:
            raise SocialTransportError("social_setup_invalid")
        # Source normalizes cmd-1 but correlates the callback by rpcid. Do not
        # invent a response-cmd==0 gate (normal wire cmd 1 maps to data cmd 0).
        report["setup_reply_command"] = command
        report["stage"] = "setup_payload"
        data = _setup_payload(body, report)
        report["stage"] = "setup_modules"
        count, user_available = _module_metadata(data, report)
        report.update(module_count=count, user_module_available=user_available,
                      social_session_accepted=True, outcome="social_setup_verified", stage="complete")
        return
    raise SocialTransportError("social_setup_correlation_missing")


def run_social_setup_once(session, *, parameter, connect=socket.create_connection,
                          now=None, monotonic=time.monotonic):
    """Return redacted setup diagnostics; close the only connection on every exit.

    Optional aid/streamid/module echoes are reported separately. Successful
    setup does not imply any query was made or that player stats are available.
    This function cannot establish the original gateway callback provenance.
    """
    report, sock = _report(), None
    try:
        if type(session) is not GatewaySocialToken or not callable(connect) or not callable(monotonic):
            raise SocialTransportError("social_transport_context")
        social_token_metadata(session, now=now)
        session = parse_gateway_social_token(session.token, own_uid=session.own_uid,
                                             zone_id=session.zone_id, now=now)
        if type(parameter) is not bytes or len(parameter) != 17 or hashlib.sha256(parameter).hexdigest() != PARAMETER_SHA256:
            raise SocialTransportError("social_parameter_unreviewed")
        _check_signature_variant(session)
        started = _clock_value(monotonic)
        report.update(stage="connect", connections_attempted=1)
        sock = connect((session.route.host, session.route.port), timeout=10)
        wire = _Wire(sock, report, monotonic, started)
        report["stage"] = "opening"
        prefix = wire.exact(4)
        size = struct.unpack("<I", prefix)[0]
        if not 12 <= size <= 4096:
            raise SocialTransportError("social_opening_invalid")
        try:
            fields, _ = opening_fields(prefix + wire.exact(size - 4), True)
        except ValueError:
            raise SocialTransportError("social_opening_invalid") from None
        if list(map(len, fields)) != [4, 4, 4]:
            raise SocialTransportError("social_opening_invalid")
        signature = magic_signature(fields[0], parameter)
        wire.send_opening(struct.pack("<IH", 14, 4) + signature + struct.pack("<HH", 0, 0))
        wire.in_crypto = RC4Stream(fold_xor(fields[1], parameter))
        wire.out_crypto = RC4Stream(fold_xor(fields[2], parameter))
        report["stage"] = "hello"
        wire.send(0, 0, _packed({"payload_type": 1, "social_python_socket_version": PYTHON_SOCKET_VERSION}))
        _, _, raw = wire.record()  # Source SYN ACK parses the first body, not its command.
        hello = _json(raw)
        if type(hello) is not dict or not hello:
            raise SocialTransportError("social_hello_invalid")
        code = hello.get("code", 0)
        if type(code) is not int or not -(10**9) <= code <= 10**9:
            raise SocialTransportError("social_hello_invalid")
        if code:
            report["server_error_code"] = code
            raise SocialTransportError("social_hello_rejected")
        if (("payload_type" in hello and (type(hello["payload_type"]) is not int or hello["payload_type"] != 1))
                or hello.get("content_encoding") not in (None, "")):
            raise SocialTransportError("social_hello_codec_unsupported")
        report["hello_accepted"] = True
        social_token_metadata(session, now=now)  # Recheck expiry before dispatching credentials.
        report["stage"] = "setup"
        request = {"header": {"version": CLIENT_VERSION, "rpcid": SETUP_RPC_ID,
                              "aid": session.aid, "token": session.token},
                   "body": {"cmd": 0, "payload": "e30="}}
        wire.send(1, 3, _packed(request), carries_token=True)
        _accept_setup(wire, session)
    except SocialSessionError as error:
        report["outcome"] = error.code if error.code in TOKEN_ERROR_CODES else "social_transport_unexpected"
    except SocialTransportError as error:
        report["outcome"] = error.code
    except TimeoutError:
        report["outcome"] = "social_transport_timeout"
    except OSError:
        report["outcome"] = "social_transport_network_error"
    except Exception:
        report["outcome"] = "social_transport_unexpected"
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
    return report
