"""Bounded, in-memory two-round normal saved-account renewal experiment.

Uses the public store SDK contracts in web_sdk_history_login. The second
exchange uses only the first exchange's returned renewal token, and starts only
after the first replacement session has authenticated the exact expected owner.
No browser/storage/credential lookup, retry, alternate grant or old-token
fallback. authenticate must be the caller's owner-bound normal AUTH operation;
its arbitrary return fields are never exported. It receives only fresh SAUTH.

replacement_token_used means selected as input to the second exchange invocation;
that round's request_attempted separately records transmission uncertainty.
replacement_token_accepted means the second SDK response passed normal code-zero
and token-shape validation, not that its game owner was verified. Only both
owner-authenticated rounds establish renewal_chain_verified. This does not test
expiry, restart persistence or long-term renewal, so automatic_renewal_verified
always remains false. The caller supplies randomness first, then the clock.
"""

import web_sdk_history_login as history


BUNDLE_FIELDS = frozenset({"selected_uid", "sauth", "saved_account", "mpay_context", "wrapper_udid"})


def _summary():
    return {
        "outcome": "history_chain_context_invalid", "rounds_requested": 2,
        "rounds_completed": 0, "replacement_token_used": False,
        "replacement_token_accepted": False, "renewal_chain_verified": False,
        "automatic_renewal_verified": False, "secrets_saved": False,
        "browser_storage_changed": False, "rounds": [],
    }


def _round(number):
    return {
        "round": number, "outcome": "history_chain_round_started",
        "request_attempted": False, "candidate_received": False,
        "sauth_constructed": False, "auth_attempted": False,
        "owner_authentication_verified": False,
        "http_status": None, "application_code": None,
    }


def _project_error(error, report):
    # Even public exception objects may be manually constructed or altered.
    # Retain only the fixed SDK code and exact bounded numeric metadata.
    code = error.code
    report["outcome"] = code if type(code) is str and code in history.ERROR_CODES else "history_request_failed"
    status, application = error.http_status, error.application_code
    report["http_status"] = status if type(status) is int and 100 <= status <= 599 else None
    report["application_code"] = (application if type(application) is int
                                  and -(1 << 31) <= application < (1 << 31) else None)


def run_history_chain(bundle, *, authenticate, clock_ms, random_samples, rounds=2):
    """Return only a fixed redacted summary; never return a token or AUTH report.

    Exactly two rounds are permitted. Invalid owner/source inputs or providers
    return a context-invalid summary before any exchange. Providers take no
    arguments: random_samples returns eight [0,1) numbers; clock_ms returns an
    integer millisecond timestamp. authenticate receives one independent dict
    containing the freshly generated ten-field SAUTH. Its exact boolean
    auth_service_accepted and own_role_verified fields are the owner proof.
    """
    result = _summary()
    if (type(rounds) is not int or rounds != 2 or not callable(authenticate)
            or not callable(clock_ms) or not callable(random_samples)):
        return result
    try:
        if (type(bundle) is not dict or bundle.keys() != BUNDLE_FIELDS
                or type(bundle["selected_uid"]) is not int
                or bundle["selected_uid"] != history.EXPECTED_OWNER_UID
                or type(bundle["sauth"]) is not dict
                or type(bundle["saved_account"]) is not dict
                or type(bundle["mpay_context"]) is not dict
                or "refresh_token" in bundle["sauth"]
                or type(bundle["wrapper_udid"]) is not str
                or not bundle["wrapper_udid"].strip()
                or len(bundle["wrapper_udid"].encode("utf-8")) > 512
                or bundle["wrapper_udid"] != bundle["sauth"].get("udid")):
            return result
        working_sauth = bundle["sauth"].copy()
        working_saved = bundle["saved_account"].copy()
        working_context = bundle["mpay_context"].copy()
        wrapper_udid = bundle["wrapper_udid"]
        inputs = history.build_history_login_inputs(working_sauth, working_saved, working_context)
    except Exception:
        return result

    for number in (1, 2):
        report = _round(number)
        result["rounds"].append(report)
        if number == 2:
            try:
                inputs = history.build_history_login_inputs(working_sauth, working_saved, working_context)
            except history.HistoryLoginError as error:
                _project_error(error, report)
                result["outcome"] = "history_chain_context_invalid"
                return result
            except Exception:
                report["outcome"] = "history_context_invalid"
                return result
            result["replacement_token_used"] = True
        try:
            candidate = history.login_history_once(inputs)
        except history.HistoryLoginError as error:
            report["request_attempted"] = error.request_attempted if type(error.request_attempted) is bool else None
            _project_error(error, report)
            result["outcome"] = "history_chain_sdk_failed"
            return result
        except Exception:
            report.update(outcome="history_request_failed", request_attempted=None)
            result["outcome"] = "history_chain_sdk_failed"
            return result
        report.update(request_attempted=True, candidate_received=True)
        if number == 2:
            result["replacement_token_accepted"] = True
        try:
            # Preserve source evaluation order: sample the eight digits before
            # reading Date.getTime; fresh-wrapper UID/SESSION remain absent.
            samples = random_samples()
            timestamp = clock_ms()
            renewed = history.build_fresh_wrapper_sauth(candidate, wrapper_udid=wrapper_udid,
                                                       clock_ms=timestamp, random_samples=samples)
        except history.HistoryLoginError as error:
            _project_error(error, report)
            result["outcome"] = "history_chain_sauth_failed"
            return result
        except Exception:
            report["outcome"] = "history_serial_context_invalid"
            result["outcome"] = "history_chain_sauth_failed"
            return result
        report["sauth_constructed"] = True
        report["auth_attempted"] = True
        try:
            # The callback may keep its own already-projected diagnostics, but
            # cannot mutate the SAUTH used to bind the next round's SDK request.
            auth_report = authenticate(renewed.sauth.copy())
        except Exception:
            report["outcome"] = "history_chain_auth_failed"
            result["outcome"] = "history_chain_auth_failed"
            return result
        if (type(auth_report) is not dict or auth_report.get("auth_service_accepted") is not True
                or auth_report.get("own_role_verified") is not True):
            report["outcome"] = "history_chain_auth_unverified"
            result["outcome"] = "history_chain_auth_unverified"
            return result
        report.update(outcome="history_chain_owner_verified", owner_authentication_verified=True)
        result["rounds_completed"] += 1
        # Never use any callback-supplied token or old saved-account token.
        working_sauth = renewed.sauth.copy()
        working_saved = dict(working_saved, refreshToken=renewed.refresh_token)

    result.update(outcome="history_chain_verified", renewal_chain_verified=True)
    return result
