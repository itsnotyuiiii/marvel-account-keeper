"""One research-only AUTH check using the actual official browser SDK contract.

This is not a production credential provider or a browser-to-native conversion.
Missing native metadata stays absent. Whether the LOGIN service accepts this
owner-authorized SDK session and minimal envelope is the experiment, not an
assumption. There are no file reads, credential exports, retries, gateway entry,
game/SDK-log fallbacks, session renewal, or profile queries in this module.
"""
from __future__ import annotations

import json

import game_login_probe as login


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
    if client_version is not None and (
            type(client_version) is not str or client_version != login.CLIENT_VERSION):
        raise login.LoginError("web_sdk_version_unreviewed")
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
