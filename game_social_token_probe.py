"""Passive, bounded social-token delivery on a verified owner game connection.

The caller must supply the reviewed token RPC index and invoke this reader only
after the normal gateway has verified its owner. The reader sends no bytes,
requests no refresh, and never executes startup code. Explicit trusted callback
work is separate from the reader's byte counters. JWT claims are local consistency
checks, not signature verification or SocialMatrix service acceptance.
"""
from __future__ import annotations

import time

import game_career_probe as career
import game_login_probe as login
from nex_rpc_probe import decode_avatar_rpc
import standalone_social_session as social
import web_sdk_auth_probe as web_auth


MAX_STARTUP_BYTES = 4 * 1024 * 1024
MAX_STARTUP_RECORDS = 128
# Build 3848703: PlayerAvatar (3781) -> ActorEntity (8862) -> Entity (8865).
# Registration 5782/5784 and component decorator 8858 establish the bare name,
# not a component prefix. The reviewed native image has no VAR_NAME identifier.
# Independent native-hash transcription agrees with the existing RPC helper.
REVIEWED_BUILD = "3848703"
REVIEWED_TOKEN_RPC_INDEX = 1538836248  # S_REFRESH_SOCIAL_TOKEN(token: Str)


def _report():
    return {
        "outcome": "social_token_not_received", "social_token_received": False,
        "token_rpc_received": False,
        "owner_claim_matches": False, "time_claims_valid": False,
        "route_shape_valid": False, "social_session_verified": False,
        "bytes_received": 0, "records_received": 0, "other_rpc_count": 0,
        "startup_record_count": 0, "entity_create_records": 0, "entity_destroy_records": 0,
        "startup_hotfix_records": 0, "startup_hotfix_bytes": 0,
        "application_bytes_sent": 0, "consumer_completed": False,
        "on_game_connection_attempted": False, "on_game_connection_completed": False,
        "on_game_connection_error": None,
        "career_verified": False, "secrets_saved": False, "remote_code_executed": False,
    }


def read_social_token(connection, transport, zone, *, token_rpc_index, on_token=None, on_startup=None):
    """Read one normal token push, then return fixed metadata only.

    Only a trusted local callback can receive the transient parsed session.
    It must not persist or print it. The callback has no implied authority to
    choose a different owner, endpoint, token source or downstream operation.
    The owning login helper closes the connection when this callback returns.
    """
    report = _report()
    if (type(token_rpc_index) is not int or not 0 < token_rpc_index < 2**31
            or type(zone) is not dict or type(zone.get("zone_id")) is not int
            or not 0 < zone["zone_id"] < 2**31
            or type(zone.get("role_uid")) is not int
            or zone["role_uid"] != login.EXPECTED_OWN_UID
            or (on_token is not None and not callable(on_token))
            or (on_startup is not None and not callable(on_startup))):
        report["outcome"] = "social_token_reader_inputs_invalid"
        return report
    try:
        deadline = time.monotonic() + 15
        for _ in range(MAX_STARTUP_RECORDS):
            record = login.receive_record(connection, transport, deadline, report,
                                          max_size=MAX_STARTUP_BYTES)
            report["records_received"] += 1
            if report["bytes_received"] > MAX_STARTUP_BYTES:
                report["outcome"] = "social_token_receive_bound"
                return report
            # Entity.eid is an engine identifier, not PlayerAvatar.uid. Entry
            # 8865 assigns it separately from decoded entity properties; entry
            # 8793 dispatches incoming RPCs to this connection's PlayerAvatar.
            # Retain bounded lifecycle parsing, never use eid==uid as an auth
            # gate. An optional owner-only observer sees validated envelopes.
            telemetry = {"own_entity_created": False, "startup_record_count": 0}
            try:
                startup = career.consume_startup_record(record, telemetry,
                    **({"on_startup": on_startup} if on_startup is not None else {}))
            finally:
                if telemetry.get("startup_observer_failed") is True:
                    report["startup_observer_failed"] = True
            if startup:
                report["startup_record_count"] += 1
                if record.sub_cmd == 113:
                    report["entity_create_records"] += 1
                elif record.sub_cmd == 114:
                    report["entity_destroy_records"] += 1
                continue
            if (record.main_cmd, record.sub_cmd) == (4, 102):
                if report["startup_hotfix_records"] >= 16:
                    report["outcome"] = "social_token_hotfix_bound"
                    return report
                update = login.decode_body(login.HOTFIX_PUSH, record.body)
                if set(update) != {field.name for field in login.HOTFIX_PUSH}:
                    report["outcome"] = "social_token_hotfix_shape"
                    return report
                report["startup_hotfix_records"] += 1
                report["startup_hotfix_bytes"] += len(update["content"])
                continue
            if (record.main_cmd, record.sub_cmd) != (3, 101):
                report.update(outcome="social_token_unrecognized_startup_command",
                              unexpected_main=record.main_cmd, unexpected_sub=record.sub_cmd)
                return report
            rpc = decode_avatar_rpc(record, direction="in")
            if rpc.index != token_rpc_index:
                report["other_rpc_count"] += 1
                continue
            report["token_rpc_received"] = True
            if len(rpc.args) != 1 or type(rpc.args[0]) is not str:
                report["outcome"] = "social_token_argument_shape"
                return report
            report["social_token_received"] = True
            if not rpc.args[0]:
                report["outcome"] = "social_token_empty"
                return report
            session = social.parse_gateway_social_token(
                rpc.args[0], own_uid=login.EXPECTED_OWN_UID, zone_id=zone["zone_id"])
            report.update(outcome="social_token_claims_validated", owner_claim_matches=True,
                          time_claims_valid=True, route_shape_valid=True)
            if on_token is not None:
                try:
                    on_token(session)
                except Exception:
                    # Callback failures can contain secrets; do not stringify.
                    report["outcome"] = "social_token_consumer_failed"
                    return report
                report["consumer_completed"] = True
            return report
        report["outcome"] = "social_token_record_bound"
    except social.SocialSessionError as exc:
        report["outcome"] = "social_token_claims_rejected"
        if type(exc.code) is str and exc.code in social.ERROR_CODES:
            report["claim_error"] = exc.code
    except (career.CareerError, login.LoginError):
        report["outcome"] = "social_token_startup_rejected"
    except TimeoutError:
        report["outcome"] = "social_token_timeout"
    except EOFError:
        report["outcome"] = "social_token_connection_closed"
    except OSError:
        report["outcome"] = "social_token_network_error"
    except (ValueError, TypeError):
        report["outcome"] = "social_token_reply_unvalidated"
    return report


def run_social_token_once(sauth, configuration, server, *, client_version, on_token=None,
                          on_game_connection=None, on_startup=None):
    """One current-build owner login, then passive token delivery.

    Public configuration is an explicit input. The exact RPC index is pinned
    for the selected build. No installed-file, SDK-log, browser or capture fallback exists.
    A claims-valid token alone never sets social_session_verified to true.

    Optional on_game_connection(connection, transport, zone) is a trusted local
    continuation, not a second login. It is invoked only after a claims-valid
    token and successful on_token callback. Its return is discarded; the caller
    owns operation-specific request authority, reviewed methods and projection.
    Reader byte counters exclude callback I/O. The login helper closes the same
    connection when this continuation returns or raises.
    """
    collected = None
    if (type(client_version) is not str
            or client_version != REVIEWED_BUILD
            or (on_token is not None and not callable(on_token))
            or (on_startup is not None and not callable(on_startup))
            or (on_game_connection is not None
                and (not callable(on_game_connection) or not callable(on_token)))):
        return web_auth._report("social_token_reader_inputs_invalid")
    try:
        inputs = web_auth.build_web_login_inputs(sauth, client_version=client_version, os_name="pc")
    except Exception:
        return web_auth._report("web_sdk_inputs_invalid")

    def after_login(connection, transport, zone):
        nonlocal collected
        if collected is not None:
            raise ValueError("social_token_reader_already_consumed")
        collected = read_social_token(connection, transport, zone,
                                      token_rpc_index=REVIEWED_TOKEN_RPC_INDEX, on_token=on_token,
                                      **({"on_startup": on_startup} if on_startup is not None else {}))
        if (on_game_connection is not None
                and collected.get("outcome") == "social_token_claims_validated"
                and all(collected.get(key) is True for key in (
                    "token_rpc_received", "social_token_received", "owner_claim_matches",
                    "time_claims_valid", "route_shape_valid", "consumer_completed"))):
            collected["on_game_connection_attempted"] = True
            try:
                on_game_connection(connection, transport, zone)
            except Exception:
                # Preserve established token/owner provenance. Continuation
                # exceptions and return values may contain raw response data.
                collected.update(outcome="social_token_game_consumer_failed",
                                 on_game_connection_error="social_token_game_consumer_failed")
            else:
                collected["on_game_connection_completed"] = True
        return collected

    try:
        raw = login.check_login(configuration, server, inputs=inputs,
                                expected_uid=login.EXPECTED_OWN_UID,
                                enter=True, after_login=after_login)
        report = web_auth._project_gateway_report(raw)
        # The collector is invoked only inside the exact-owner gateway check.
        # Keep game login and the passive token outcome separate.
        if report["game_login_verified"] and collected is not None:
            report["social_token"] = collected
        else:
            report["social_token"] = _report()
        return report
    except Exception:
        report = web_auth._report("social_token_probe_failed", transmission_unknown=True)
        report["social_token"] = _report()
        return report
