"""Explicit other-player Career on the same verified owner game connection.

The caller supplies packaged methods and a freshly accepted mesh lookup. No
login, credential lookup, installed files, capture, retry or persistent provider
is created here. Only the native request's correlated projection is returned.
"""
import game_career_probe as career
from standalone_career_query import CareerQueryError, project_other_career_response
from standalone_match_query import MatchQueryError, project_match_summary_response


def _report(outcome):
    return {"outcome": outcome, "request_attempted": False, "career_verified": False,
            "identity_verified": False, "ranks_verified": False, "matches_verified": False,
            "secrets_saved": False, "remote_code_executed": False}


def read_other_career_once(connection, transport, zone, methods, *, setup_report, target_uid):
    """One unfiltered PC aggregate, independently gated by overview visibility."""
    return _read_native_section(connection, transport, zone, methods, setup_report=setup_report,
                                target_uid=target_uid, kind="career")


def read_other_matches_once(connection, transport, zone, methods, *, setup_report, target_uid):
    """One first match page, independently gated by global history visibility."""
    return _read_native_section(connection, transport, zone, methods, setup_report=setup_report,
                                target_uid=target_uid, kind="match_summary")


def _read_native_section(connection, transport, zone, methods, *, setup_report, target_uid, kind):
    report = _report("other_" + kind + "_prerequisites_unverified")
    if (type(zone) is not dict or type(zone.get("role_uid")) is not int
            or zone["role_uid"] <= 0 or type(zone.get("zone_id")) is not int or zone["zone_id"] <= 0
            or type(target_uid) is not int or not 0 < target_uid < 2**63
            or target_uid == zone["role_uid"]
            or type(methods) is not career.CareerMethods
            or any(type(value) is not int or not 0 < value < 2**31 for value in (methods.request, methods.reply))
            or type(setup_report) is not dict
            or any(setup_report.get(key) is not True for key in (
                "social_session_accepted", "setup_consumer_completed", "mesh_request_bound"))):
        return report
    profile = setup_report.get("profile_lookup")
    if (type(profile) is not dict or profile.get("identity_verified") is not True
            or profile.get("uid") != str(target_uid) or profile.get("is_owner") is not False
            or profile.get("requested_platform") != "pc"):
        return report
    report["identity_verified"] = True
    visibility = profile.get("visibility")
    section = "overview" if kind == "career" else "history"
    permission = visibility.get(section) if type(visibility) is dict else None
    if type(permission) is not dict or permission.get("state") != "allowed":
        report["outcome"] = "other_" + kind + "_" + section + "_not_available"
        return report
    projected, projection_error = None, None

    def on_verified(data):
        nonlocal projected, projection_error
        try:
            projector = project_other_career_response if kind == "career" else project_match_summary_response
            projected = projector(data, target_uid)
        except (CareerQueryError, MatchQueryError) as error:
            # The native reader catches ValueError. Retain this fixed projection
            # diagnosis here without conflating it with RPC correlation failure.
            projection_error = error.code

    report["request_attempted"] = True
    try:
        if kind == "career":
            native = career.read_one_career(connection, transport, zone, methods, target_uid, 0,
                                            own_uid=zone["role_uid"], on_verified=on_verified)
        else:
            native = career.read_one_match_summary(connection, transport, zone, methods, target_uid,
                                                   own_uid=zone["role_uid"], on_verified=on_verified)
    except (CareerQueryError, MatchQueryError) as error:
        report["outcome"] = error.code
        return report
    except Exception:
        report["outcome"] = "other_" + kind + "_read_failed"
        return report
    if type(native) is not dict:
        report["outcome"] = "other_" + kind + "_reply_unverified"
        return report
    # Fixed types/counts/status only. Never export arbitrary exception/server text.
    for key in ("bytes_received", "application_bytes_sent", "records_received", "other_rpc_count",
                "startup_record_count"):
        value = native.get(key)
        if type(value) is int and 0 <= value <= 16 * 1024 * 1024:
            report[key] = value
    code = native.get("server_error_code")
    if type(code) is int and -(10**9) <= code <= 10**9:
        report["server_error_code"] = code
    native_outcomes = {kind + suffix for suffix in (
        "_server_error", "_reply_timeout", "_connection_closed", "_network_error", "_reply_unvalidated",
        "_record_bound", "_receive_bound", "_request_context_mismatch", "_uid_mismatch", "_reply_shape",
        "_result_code_missing", "_rows_shape")}
    native_outcomes.update({"unrecognized_" + kind + "_startup_command", "other_player_" + kind + "_verified"})
    if type(native.get("outcome")) is str and native["outcome"] in native_outcomes:
        report["native_outcome"] = native["outcome"]
    if projection_error is not None:
        report["outcome"] = projection_error
        return report
    bound_scope = (type(native.get("season")) is int and native["season"] == 0) if kind == "career" else (
        type(native.get("page")) is int and native["page"] == 0
        and type(native.get("page_size")) is int and native["page_size"] == 50
        and type(native.get("match_count")) is int and projected is not None
        and native["match_count"] == projected["match_count"])
    if (native.get(kind + "_verified") is not True or type(native.get("target_uid")) is not int
            or native["target_uid"] != target_uid or not bound_scope or projected is None):
        report["outcome"] = "other_" + kind + "_reply_unverified"
        return report
    report.update(projected)
    report["request_bound"] = True
    return report
