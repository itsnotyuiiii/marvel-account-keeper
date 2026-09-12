"""One research-only AUTH check using the actual official browser SDK contract.

This is not a production credential provider or a browser-to-native conversion.
Missing native metadata stays absent. Whether the LOGIN service accepts this
owner-authorized SDK session and minimal envelope is the experiment, not an
assumption. AUTH-only remains the default function. An explicit gateway-only
follow-up can enter the verified owner's existing role, then immediately close.
There are no file reads, credential exports, retries, game/SDK-log fallbacks,
session renewal, profile queries, or remote-code execution in this module.
"""
from __future__ import annotations

import json

import game_login_probe as login
import portable_game_runtime as runtime


EXPECTED_OWNER_UID = 1370598422
SDK_CONSTANTS = {
    "gameid": "x20naxx2gb",
    "platform": "pc",
    "login_channel": "netease_global",
    "app_channel": "netease.allysdk_global",
    "sdk_version": "1.0.0",
}
SDK_FIELDS = frozenset(SDK_CONSTANTS) | {
    "sdkuid", "sessionid", "deviceid", "udid", "client_login_sn",
}
FIELD_LIMITS = {
    **{key: 128 for key in SDK_CONSTANTS},
    "sdkuid": 256, "sessionid": 32768, "deviceid": 512,
    "udid": 512, "client_login_sn": 512, "refresh_token": 32768,
}
DIRECT_FIELDS = {
    "channel": "login_channel", "sdkuid": "sdkuid", "device_id": "deviceid",
    "app_channel": "app_channel", "sdk_version": "sdk_version", "udid": "udid",
    "unisdk_deviceid": "deviceid",
}
INPUT_OUTCOMES = frozenset({
    "web_sdk_shape", "web_sdk_field_type", "web_sdk_field_bound",
    "web_sdk_contract_mismatch", "web_sdk_identity_missing", "web_sdk_body_bound",
    "web_sdk_version_unreviewed",
    "web_sdk_platform_unreviewed",
})
LOGIN_OUTCOMES = frozenset({
    "not_connected", "login_prepare_verified", "own_existing_role_verified",
    "server_rejected_authentication", "unexpected_prepare_command",
    "unvalidated_prepare", "additional_prepare_requirements", "record_framing",
    "unexpected_auth_command", "missing_auth_result", "missing_auth_binding",
    "missing_existing_roles", "expected_role_missing_or_ambiguous",
    "existing_role_not_initialized", "unvalidated_gateway", "auth_not_successful",
    "body_bound", "truncated_body", "noncanonical_tag", "string_encoding",
    "unknown_duplicate_or_mistyped_field", "array_bound", "truncated_array",
}) | frozenset(
    stage + "_" + suffix
    for stage in ("connect", "opening", "prepare", "authentication")
    for suffix in ("timeout", "closed", "network_error", "unvalidated")
)
BOOL_FIELDS = frozenset({
    "credentials_sent", "prepare_verified", "auth_service_accepted", "own_role_verified",
    "sdk_error_json_present", "sdk_message_mentions_expiry", "sdk_message_mentions_session",
    "sdk_message_mentions_signature",
})
COUNT_FIELDS = frozenset({
    "connections_attempted", "bytes_received", "handshake_bytes_sent", "application_bytes_sent",
})
CODE_FIELDS = frozenset({"server_error_code", "sdk_code", "sdk_subcode"})


def build_web_login_inputs(sauth, *, client_version=None, os_name=None):
    """Validate this observed SDK contract; copy values, never invent native ones.

    The optional renewal token is accepted only to discard it, matching the
    official SDK's outgoing SAUTH behavior. The caller's object is not mutated.
    The returned LoginInputs contains secrets and deliberately has no repr.

    An explicit reviewed changelist adds only the two version fields assigned
    by pinned entry7800 gen_auth_info from utils.get_build_change_list(). None
    preserves the initial minimal request. No current-patch claim is inferred.
    Optional os_name uses only the pinned native SDC_LOG_OS_NAME getter's pc
    value. It is a separate outer field, not a relabel of browser SAUTH.
    """
    if client_version is not None:
        try:
            runtime.reviewed_runtime_pin(client_version)
        except runtime.RuntimeManifestError:
            raise login.LoginError("web_sdk_version_unreviewed") from None
    if os_name is not None and (type(os_name) is not str or os_name != "pc"):
        raise login.LoginError("web_sdk_platform_unreviewed")
    if (type(sauth) is not dict or not SDK_FIELDS <= sauth.keys()
            or sauth.keys() - (SDK_FIELDS | {"refresh_token"})):
        raise login.LoginError("web_sdk_shape")
    for key, value in sauth.items():
        if type(value) is not str:
            raise login.LoginError("web_sdk_field_type")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError:
            raise login.LoginError("web_sdk_field_type") from None
        if size > FIELD_LIMITS[key]:
            raise login.LoginError("web_sdk_field_bound")
    if any(sauth[key] != expected for key, expected in SDK_CONSTANTS.items()):
        raise login.LoginError("web_sdk_contract_mismatch")
    if any(not sauth[key].strip() for key in ("sdkuid", "sessionid", "deviceid")):
        raise login.LoginError("web_sdk_identity_missing")

    outgoing = {key: value for key, value in sauth.items() if key != "refresh_token"}
    auth = {target: outgoing[source] for target, source in DIRECT_FIELDS.items()}
    auth["sauth_json"] = json.dumps(outgoing, ensure_ascii=False, separators=(",", ":"))
    if client_version is not None:
        auth.update(client_version=client_version, engine_ver=client_version)
    if os_name is not None:
        auth["os_name"] = os_name
    try:
        body = login.encode_body(login.AUTH_REQUEST, auth)
    except (ValueError, UnicodeError):
        raise login.LoginError("web_sdk_body_bound") from None
    if len(body) + 7 > login.MAX_BODY:
        raise login.LoginError("web_sdk_body_bound")
    return login.LoginInputs(auth, "official-web-sdk-research")


def _report(outcome, *, transmission_unknown=False):
    return {
        "outcome": outcome,
        "connections_attempted": None if transmission_unknown else 0,
        "bytes_received": None if transmission_unknown else 0,
        "handshake_bytes_sent": None if transmission_unknown else 0,
        "application_bytes_sent": None if transmission_unknown else 0,
        "credentials_sent": None if transmission_unknown else False,
        "prepare_verified": False,
        "auth_service_accepted": False,
        "own_role_verified": False,
        "game_login_verified": False,
        "career_verified": False,
        "secrets_saved": False,
    }


def _project_report(raw):
    """Only fixed outcomes and typed numeric/boolean diagnostics leave the probe."""
    if type(raw) is not dict:
        return _report("web_auth_report_unrecognized", transmission_unknown=True)
    outcome = raw.get("outcome")
    if type(outcome) is not str or outcome not in LOGIN_OUTCOMES:
        return _report("web_auth_report_unrecognized", transmission_unknown=True)
    result = _report(outcome, transmission_unknown=True)
    for name in BOOL_FIELDS:
        value = raw.get(name)
        if type(value) is bool:
            result[name] = value
    for name in COUNT_FIELDS:
        value = raw.get(name)
        maximum = 1 if name == "connections_attempted" else 2**31 - 1
        if type(value) is int and 0 <= value <= maximum:
            result[name] = value
    for name in CODE_FIELDS:
        value = raw.get(name)
        if type(value) is int and -(2**31) <= value <= 2**31 - 1:
            result[name] = value
    if raw.get("game_error_label") == "LOGIN_ERR_AUTH_FAIL":
        result["game_error_label"] = "LOGIN_ERR_AUTH_FAIL"
    # No gateway callback is possible; do not accept upstream claims of stats,
    # gateway entry or saved secrets from an unexpected report shape.
    return result


def run_auth_once(sauth, configuration, server, *, client_version=None, os_name=None):
    """One normal AUTH attempt for the fixed owner UID; never enter the game.

    Configuration and the verified advertised server must be supplied by the
    caller. Nothing is loaded from disk and no alternate credential source is
    consulted. Unexpected post-dispatch errors have unknown transmission status,
    not a misleading claim that no credentials were sent.
    """
    try:
        inputs = build_web_login_inputs(sauth, client_version=client_version, os_name=os_name)
    except login.LoginError as exc:
        outcome = str(exc)
        return _report(outcome if outcome in INPUT_OUTCOMES else "web_sdk_inputs_invalid")
    except Exception:
        return _report("web_sdk_inputs_invalid")
    try:
        raw = login.check_login(
            configuration, server, inputs=inputs, expected_uid=EXPECTED_OWNER_UID,
            enter=False, after_login=None,
        )
        return _project_report(raw)
    except Exception:
        # Never include repr/str/traceback of a credential-bearing exception.
        return _report("web_auth_probe_failed", transmission_unknown=True)


GATEWAY_OUTCOMES = LOGIN_OUTCOMES | frozenset({
    "gateway_not_connected", "own_gateway_login_verified", "gateway_rejected_login",
    "startup_hotfix_bound", "startup_hotfix_shape", "unexpected_gateway_login_command",
    "missing_gateway_login_result", "gateway_login_not_successful", "unexpected_relogin_result",
    "gateway_role_mismatch", "gateway_login_binding_missing", "record_size_bound",
}) | frozenset(
    stage + "_" + suffix
    for stage in ("gateway_connect", "gateway_opening", "gateway_login")
    for suffix in ("timeout", "closed", "network_error", "unvalidated")
)
GATEWAY_BOOL_FIELDS = frozenset({
    "credentials_sent", "login_result_uid_present", "login_result_uid_matches", "login_key_present",
})


def _project_gateway_report(raw):
    """Project the existing LOGIN/gateway reports, never their auth bindings.

    Top-level connection/byte counters remain LOGIN-only; nested gateway
    counters describe its separate connection. Success requires the entire
    exact-owner chain, not merely an AUTH result or an isolated gateway flag.
    """
    if type(raw) is not dict or type(raw.get("outcome")) is not str or raw["outcome"] not in GATEWAY_OUTCOMES:
        result = _report("web_gateway_report_unrecognized", transmission_unknown=True)
        result["remote_code_executed"] = False
        return result
    # Reuse only AUTH's typed diagnostic projection; its success flags remain
    # separate from the gateway-specific validation below.
    result = _project_report(dict(raw, outcome="own_existing_role_verified"))
    result.update(outcome=raw["outcome"], remote_code_executed=False)
    nested = raw.get("gateway")
    if nested is None:
        if raw["outcome"] == "own_gateway_login_verified" or raw.get("game_login_verified") is True:
            result["outcome"] = "web_gateway_report_unrecognized"
        return result
    if (type(nested) is not dict or type(nested.get("outcome")) is not str
            or nested["outcome"] not in GATEWAY_OUTCOMES):
        result["outcome"] = "web_gateway_report_unrecognized"
        return result
    gateway = {
        "outcome": nested["outcome"], "connections_attempted": None,
        "bytes_received": None, "handshake_bytes_sent": None, "application_bytes_sent": None,
        "credentials_sent": None, "game_login_verified": False, "career_verified": False,
        "secrets_saved": False, "remote_code_executed": False,
    }
    for name in GATEWAY_BOOL_FIELDS:
        if type(nested.get(name)) is bool:
            gateway[name] = nested[name]
    limits = {
        **{name: 2**31 - 1 for name in COUNT_FIELDS}, "connections_attempted": 1,
        "startup_hotfix_records": 16, "startup_hotfix_bytes": 16 * login.MAX_BODY,
        "reply_relogin": 1,
        "reply_main": 65535, "reply_sub": 255,
        "unrecognized_record_flags": 15, "unrecognized_record_size": 2**28 - 1,
    }
    for name, maximum in limits.items():
        value = nested.get(name)
        if type(value) is int and 0 <= value <= maximum:
            gateway[name] = value
    code = nested.get("server_error_code")
    if type(code) is int and -(2**31) <= code <= 2**31 - 1:
        gateway["server_error_code"] = code
    verified = (
        raw["outcome"] == nested["outcome"] == "own_gateway_login_verified"
        and raw.get("game_login_verified") is True and nested.get("game_login_verified") is True
        and all(result.get(name) is True for name in (
            "prepare_verified", "auth_service_accepted", "own_role_verified", "credentials_sent"))
        and all(gateway.get(name) is True for name in GATEWAY_BOOL_FIELDS)
        and result["connections_attempted"] == gateway["connections_attempted"] == 1
    )
    if not verified and (raw["outcome"] == "own_gateway_login_verified"
                         or nested["outcome"] == "own_gateway_login_verified"):
        result["outcome"] = gateway["outcome"] = "web_gateway_report_unrecognized"
    result["game_login_verified"] = gateway["game_login_verified"] = verified
    result["gateway"] = gateway
    return result


def run_gateway_once(sauth, configuration, server, *, client_version, os_name="pc"):
    """One LOGIN and one owner gateway entry; no callbacks, stats or retries.

    Only the current explicitly reviewed version/PC envelope is eligible for
    this separate follow-up. The existing AUTH-only function is unchanged.
    Source provenance remains the caller's responsibility, as for AUTH-only.
    """
    try:
        if type(client_version) is not str or client_version != runtime.CURRENT_PROVENANCE.client_version:
            raise login.LoginError("web_sdk_version_unreviewed")
        if type(os_name) is not str or os_name != "pc":
            raise login.LoginError("web_sdk_platform_unreviewed")
        inputs = build_web_login_inputs(sauth, client_version=client_version, os_name=os_name)
    except login.LoginError as exc:
        outcome = str(exc)
        return _report(outcome if outcome in INPUT_OUTCOMES else "web_sdk_inputs_invalid")
    except Exception:
        return _report("web_sdk_inputs_invalid")
    try:
        raw = login.check_login(
            configuration, server, inputs=inputs, expected_uid=EXPECTED_OWNER_UID,
            enter=True, after_login=None,
        )
        return _project_gateway_report(raw)
    except Exception:
        result = _report("web_gateway_probe_failed", transmission_unknown=True)
        result["remote_code_executed"] = False
        return result
