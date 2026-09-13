"""One explicit normal Python SocialMatrix session without capture dependencies.

Reviewed current public source: Conf/Stub/SocketBase select JSON payload 1;
TCPConnection entry 40 sends the uncustomized Python hello on command 0;
SocialMatrixNet entry 3044 calls LIST_MODULE 259 with data command 0 and {}.
Constants entry 3498 identifies client version 3.10.1. This is that first-party
Python variant, not a claim that native overrides use the same hello/version.

Only a supplied fresh gateway token and supplied hash-pinned public parameter
are used. A single primary route connection is closed after setup, or after an
explicit transient callback makes at most one reviewed mesh identity read and
one authenticated-owner Career read and one other-player namecard rank read.
There are no local files, retries,
captured templates, HMAC signing, renewal, payload execution or credential
exports in reports. Career service-body decoding belongs to the caller.
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
from standalone_career_query import make_own_career_request
from standalone_rank_query import make_other_rank_request
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
    "social_setup_consumer_failed", "social_mesh_context_closed", "social_mesh_already_consumed",
    "social_mesh_target_invalid", "social_mesh_reply_invalid", "social_mesh_rejected",
    "social_mesh_identity_mismatch", "social_mesh_correlation_missing", "social_mesh_payload_invalid",
    "social_career_context_closed", "social_career_already_consumed", "social_career_module_unavailable",
    "social_career_request_invalid", "social_career_reply_invalid", "social_career_rejected",
    "social_career_identity_mismatch", "social_career_correlation_missing", "social_career_payload_invalid",
    "social_rank_context_closed", "social_rank_already_consumed", "social_rank_module_unavailable",
    "social_rank_target_invalid", "social_rank_request_invalid", "social_rank_reply_invalid",
    "social_rank_rejected", "social_rank_identity_mismatch", "social_rank_correlation_missing",
    "social_rank_payload_invalid",
    "social_query_connection_failed",
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
            "validation_reason": None, "setup_shape": {},
            "setup_consumer_attempted": False, "setup_consumer_completed": False,
            "mesh_query_attempted": False, "mesh_query_sent": False, "mesh_query_count": 0,
            "mesh_application_bytes_sent": 0, "mesh_request_bound": False,
            "mesh_reply_received": False, "mesh_reply_command": None, "mesh_ignored_records": 0,
            "mesh_error": None, "mesh_server_error_code": None, "mesh_status_layer": None,
            "mesh_validation_reason": None, "mesh_shape": {},
            "career_query_attempted": False, "career_query_sent": False, "career_query_count": 0,
            "career_application_bytes_sent": 0, "career_reply_bound": False,
            "career_reply_received": False, "career_reply_command": None, "career_ignored_records": 0,
            "career_error": None, "career_server_error_code": None, "career_status_layer": None,
            "career_validation_reason": None, "career_shape": {},
            "rank_query_attempted": False, "rank_query_sent": False, "rank_query_count": 0,
            "rank_application_bytes_sent": 0, "rank_reply_bound": False,
            "rank_reply_received": False, "rank_reply_command": None, "rank_ignored_records": 0,
            "rank_error": None, "rank_server_error_code": None, "rank_status_layer": None,
            "rank_validation_reason": None, "rank_shape": {}}


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

    def send(self, main, sub, body, *, carries_token=False, query=None):
        if len(body) + 7 > MAX_BYTES:
            raise SocialTransportError("social_stream_bound")
        self.remaining()
        packet = self.out_crypto.process(struct.pack("<IHB", len(body) + 7, main, sub) + body)
        previous = self.report["application_bytes_sent"]
        self.report["application_bytes_sent"] = None
        if carries_token:
            if self.report["token_sent"] is not True:
                self.report["token_sent"] = None
            if query is None:
                self.report["setup_request_sent"] = None
        if query is not None:
            self.report[f"{query}_query_sent"] = None
            self.report[f"{query}_application_bytes_sent"] = None
        self.sock.sendall(packet)
        self.report["application_bytes_sent"] = previous + len(packet)
        if carries_token:
            self.report["token_sent"] = True
            if query is None:
                self.report["setup_request_sent"] = True
        if query is not None:
            self.report[f"{query}_query_sent"] = True
            self.report[f"{query}_application_bytes_sent"] = len(packet)

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
    return selected


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
        modules = _module_metadata(data, report)
        report.update(module_count=len(modules), user_module_available=modules.get("svc-user", 0) > 0,
                      social_session_accepted=True, outcome="social_setup_verified", stage="complete")
        return header.get("streamid") or "", modules
    raise SocialTransportError("social_setup_correlation_missing")


class _SocialConnectionContext:
    """Only the current callback may use these three bounded read capabilities.

    Decoded data is deliberately returned only to that callback. It still needs
    the caller's exact-target projection; transport correlation is not identity
    verification of a returned player row. The context is invalidated on exit.
    """
    __slots__ = ("_wire", "_session", "_streamid", "_modules", "_now", "_active", "_consumed",
                 "_career_consumed", "_rank_consumed", "_next_rpc", "_connection_failed")

    def __init__(self, wire, session, streamid, modules, now):
        self._wire, self._session, self._streamid, self._now = wire, session, streamid, now
        self._modules = modules
        self._active, self._consumed = True, False
        self._career_consumed, self._next_rpc, self._connection_failed = False, 3, False
        self._rank_consumed = False

    def __reduce_ex__(self, protocol):
        raise SocialTransportError("social_mesh_context_closed")

    def _deactivate(self):
        self._active = False
        self._wire = self._session = self._streamid = self._modules = self._now = None

    def _status(self, value, layer, kind):
        code = value.get("errorcode", 0)
        if type(code) is not int or not -(10**9) <= code <= 10**9:
            raise SocialTransportError(f"social_{kind}_reply_invalid")
        if code != 0:
            self._wire.report[f"{kind}_server_error_code"] = code
            self._wire.report[f"{kind}_status_layer"] = layer
            raise SocialTransportError(f"social_{kind}_rejected")

    def _read_reply(self, kind, expected_main, expected_sub, rpcid):
        wire, report, session = self._wire, self._wire.report, self._session
        for _ in range(MAX_SETUP_RECORDS):
            main, sub, raw = wire.record()
            if (main, sub) != (expected_main, expected_sub):
                report["ignored_records"] += 1
                report[f"{kind}_ignored_records"] += 1
                continue
            envelope = _json(raw)
            if type(envelope) is not dict or type(envelope.get("header")) is not dict:
                raise SocialTransportError(f"social_{kind}_reply_invalid")
            header = envelope["header"]
            if type(header.get("rpcid")) is not str or header["rpcid"] != rpcid:
                report["ignored_records"] += 1
                report[f"{kind}_ignored_records"] += 1
                continue
            self._status(header, "header", kind)
            if "aid" in header and (type(header["aid"]) is not str or header["aid"] != session.aid):
                raise SocialTransportError(f"social_{kind}_identity_mismatch")
            body = envelope.get("body")
            if type(body) is not dict:
                raise SocialTransportError(f"social_{kind}_reply_invalid")
            self._status(body, "body", kind)
            command = body.get("cmd", 0)
            if type(command) is not int or not 0 <= command <= 0xFFFFFFFF:
                raise SocialTransportError(f"social_{kind}_reply_invalid")
            report[f"{kind}_reply_command"] = command
            projection = {"setup_shape": report[f"{kind}_shape"]}
            try:
                data = _setup_payload(body, projection)
            except SocialTransportError:
                report[f"{kind}_validation_reason"] = projection.get("validation_reason")
                raise SocialTransportError(f"social_{kind}_payload_invalid") from None
            # This marks request/reply correlation and owner-envelope checks,
            # not identity or service-status verification inside the payload.
            report[f"{kind}_reply_received"] = True
            report["mesh_request_bound" if kind == "mesh" else f"{kind}_reply_bound"] = True
            return data
        raise SocialTransportError(f"social_{kind}_correlation_missing")

    def _run_query(self, kind, main, sub, data):
        report = self._wire.report
        try:
            if self._connection_failed:
                raise SocialTransportError("social_query_connection_failed")
            session = self._session
            social_token_metadata(session, now=self._now)
            rpcid = str(self._next_rpc)
            self._next_rpc += 1
            header = {"version": CLIENT_VERSION, "rpcid": rpcid, "aid": session.aid}
            if self._streamid:
                header["streamid"] = self._streamid
            else:
                header["token"] = session.token
            payload = base64.b64encode(_packed(data)).decode("ascii")
            request = {"header": header, "body": {"cmd": 0, "payload": payload}}
            if kind in {"career", "rank"}:
                report["stats_requested"] = True
            self._wire.send(main, sub, _packed(request), carries_token=not self._streamid, query=kind)
            report["stage"] = f"{kind}_reply"
            response = self._read_reply(kind, main, sub, rpcid)
            report["stage"] = "setup_consumer"
            return response
        except Exception as error:
            # A partial send/read can advance either cipher. Never reuse that
            # connection for the other capability after an unsuccessful query.
            self._connection_failed = True
            if isinstance(error, SocialSessionError):
                code = error.code if error.code in TOKEN_ERROR_CODES else "social_transport_unexpected"
            elif isinstance(error, SocialTransportError):
                code = error.code
            elif isinstance(error, TimeoutError):
                code = "social_transport_timeout"
            elif isinstance(error, OSError):
                code = "social_transport_network_error"
            else:
                code = "social_transport_unexpected"
            report[f"{kind}_error"] = code
            raise

    def read_mesh_user(self, target_uid):
        """One source-backed mesh read; caller must verify the returned row aid."""
        if not self._active:
            raise SocialTransportError("social_mesh_context_closed")
        report = self._wire.report
        if self._consumed:
            report["mesh_error"] = "social_mesh_already_consumed"
            raise SocialTransportError("social_mesh_already_consumed")
        self._consumed = True
        report.update(mesh_query_attempted=True, mesh_query_count=1, stage="mesh_query")
        if type(target_uid) is not int or not 1 <= target_uid <= (1 << 64) - 1:
            report["mesh_error"] = "social_mesh_target_invalid"
            raise SocialTransportError("social_mesh_target_invalid")
        return self._run_query("mesh", 1, 4, {"aids": [f"{self._session.zone_id}_{target_uid}"]})

    def read_own_career(self):
        """One normal Career service request, always bound to the token owner."""
        if not self._active:
            raise SocialTransportError("social_career_context_closed")
        report = self._wire.report
        if self._career_consumed:
            report["career_error"] = "social_career_already_consumed"
            raise SocialTransportError("social_career_already_consumed")
        self._career_consumed = True
        report.update(career_query_attempted=True, career_query_count=1, stage="career_query")
        command = self._modules.get("mesh")
        if type(command) is not int or not 1 <= command <= 65535:
            report["career_error"] = "social_career_module_unavailable"
            raise SocialTransportError("social_career_module_unavailable")
        try:
            data = make_own_career_request(self._session.own_uid, self._session.zone_id)
        except Exception:
            report["career_error"] = "social_career_request_invalid"
            raise SocialTransportError("social_career_request_invalid") from None
        return self._run_query("career", command, 2, data)

    def read_other_ranks(self, target_uid):
        """One normal other-player namecard request, not an access grant.

        The caller must first bind fresh mesh identity and independent overview
        access. This method only binds transport; rank projection is separate.
        """
        if not self._active:
            raise SocialTransportError("social_rank_context_closed")
        report = self._wire.report
        if self._rank_consumed:
            report["rank_error"] = "social_rank_already_consumed"
            raise SocialTransportError("social_rank_already_consumed")
        self._rank_consumed = True
        report.update(rank_query_attempted=True, rank_query_count=1, stage="rank_query")
        if (type(target_uid) is not int or not 0 < target_uid < 2**64
                or target_uid == self._session.own_uid):
            report["rank_error"] = "social_rank_target_invalid"
            raise SocialTransportError("social_rank_target_invalid")
        command = self._modules.get("mesh")
        if type(command) is not int or not 1 <= command <= 65535:
            report["rank_error"] = "social_rank_module_unavailable"
            raise SocialTransportError("social_rank_module_unavailable")
        try:
            data = make_other_rank_request(target_uid, self._session.zone_id, self._session.own_uid)
        except Exception:
            report["rank_error"] = "social_rank_request_invalid"
            raise SocialTransportError("social_rank_request_invalid") from None
        return self._run_query("rank", command, 2, data)


def run_social_setup_once(session, *, parameter, connect=socket.create_connection,
                          now=None, monotonic=time.monotonic, on_setup=None):
    """Return redacted setup diagnostics; close the only connection on every exit.

    Optional aid/streamid/module echoes are reported separately. Successful
    setup does not imply any query was made or that player stats are available.
    Explicit on_setup receives a transient one-mesh/one-own-Career/one-other-rank
    context; its return value and decoded player data never enter this report. This function cannot
    establish the original gateway callback provenance.
    """
    report, sock = _report(), None
    try:
        if (type(session) is not GatewaySocialToken or not callable(connect) or not callable(monotonic)
                or (on_setup is not None and not callable(on_setup))):
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
        streamid, modules = _accept_setup(wire, session)
        if on_setup is not None:
            context = _SocialConnectionContext(wire, session, streamid, modules, now)
            report.update(setup_consumer_attempted=True, stage="setup_consumer")
            try:
                on_setup(context)
            except Exception:
                report["outcome"] = "social_setup_consumer_failed"
            else:
                report.update(setup_consumer_completed=True, stage="complete")
            finally:
                context._deactivate()
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
