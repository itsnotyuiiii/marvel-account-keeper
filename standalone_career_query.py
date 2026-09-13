"""Current normal own-Career request and bounded exact-owner projection.

Pure functions only: no credentials, files, sockets, provider fallback or saved
responses. Other-player Career belongs to the separate native RPC path.
"""
import base64
import json
import zlib


MAX_RESPONSE_BYTES = 4 * 1024 * 1024
STAT_FIELDS = {"matches": "total_match_count", "wins": "total_win_count",
               "kills": "k", "deaths": "d", "assists": "a"}
ERROR_CODES = frozenset({
    "own_career_inputs_invalid", "own_career_service_mismatch", "own_career_http_rejected",
    "own_career_response_shape", "own_career_response_bound", "own_career_response_encoding",
    "own_career_response_json", "own_career_application_rejected", "own_career_identity_mismatch",
    "own_career_statistics_shape",
    "other_career_inputs_invalid", "other_career_response_shape",
    "other_career_identity_mismatch", "other_career_statistics_shape",
    "xyq_inputs_invalid", "xyq_service_mismatch", "xyq_http_rejected",
    "xyq_response_shape", "xyq_response_bound", "xyq_response_encoding",
    "xyq_response_json", "xyq_application_rejected",
})


class CareerQueryError(ValueError):
    def __init__(self, code, *, http_status=None, application_code=None):
        self.code = code if type(code) is str and code in ERROR_CODES else "own_career_response_shape"
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.application_code = (application_code if type(application_code) is int
                                 and -(10**9) <= application_code <= 10**9 else None)
        super().__init__(self.code)


def _uid(value):
    if type(value) is not int or not 0 < value < 2**64:
        raise CareerQueryError("own_career_inputs_invalid")
    return value


def make_own_career_request(own_uid, zone_id):
    """Current1838 omission branches request an unfiltered PC aggregate.

    No season0 literal, guessed UI defaults or captured request template is used.
    Current103/3520 include the null wrapper fields below on this socket variant.
    """
    body = {"player_uid": _uid(own_uid), "zone_id": _uid(zone_id), "device_type": "pc"}
    return {"service": "xyq-career", "headers": {
        "Content-Type": "application/json", "Accept-Encoding": "gzip"},
        "uri": "query_my_player_career", "method": "POST", "querystring": None,
        "post_body": base64.b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8")).decode("ascii"),
        "host": None, "protocol": None, "version": None}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _bad_constant(_value):
    raise ValueError


def _response_json(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_RESPONSE_BYTES:
        raise CareerQueryError("own_career_response_bound")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_bad_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise CareerQueryError("own_career_response_json") from None
    if type(value) is not dict:
        raise CareerQueryError("own_career_response_shape")
    return value


def _decode_body(wrapper):
    body = wrapper.get("response_body")
    headers = wrapper.get("headers")
    if type(body) is not str or type(headers) is not dict:
        raise CareerQueryError("own_career_response_shape")
    if len(body) > (MAX_RESPONSE_BYTES + 2) // 3 * 4:
        raise CareerQueryError("own_career_response_bound")
    try:
        raw = base64.b64decode(body, validate=True)
    except (ValueError, UnicodeError):
        raise CareerQueryError("own_career_response_encoding") from None
    if base64.b64encode(raw).decode("ascii") != body:
        raise CareerQueryError("own_career_response_encoding")
    # Source tests this exact header/value, not body magic or a guessed codec.
    if headers.get("Content-Encoding") == "gzip":
        try:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            raw = decoder.decompress(raw, MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES or decoder.unconsumed_tail:
                raise CareerQueryError("own_career_response_bound")
            if not decoder.eof or decoder.unused_data:
                raise CareerQueryError("own_career_response_encoding")
        except zlib.error:
            raise CareerQueryError("own_career_response_encoding") from None
    return _response_json(raw)


def decode_xyq_response(wrapper, expected_service):
    """Decode the two reviewed XYQ services; no UID or request-binding claim.

    The caller must independently correlate its RPC and project the requested
    target. This function does not execute or choose any service operation.
    """
    if type(expected_service) is not str or expected_service not in {"xyq-career", "xyq-namecard"}:
        raise CareerQueryError("xyq_inputs_invalid")
    if type(wrapper) is not dict:
        raise CareerQueryError("xyq_response_shape")
    if wrapper.get("service") != expected_service:
        raise CareerQueryError("xyq_service_mismatch")
    status = wrapper.get("status_code")
    if type(status) is not int or status != 200:
        raise CareerQueryError("xyq_http_rejected", http_status=status)
    try:
        response = _decode_body(wrapper)
    except CareerQueryError as error:
        raise CareerQueryError(error.code.replace("own_career_", "xyq_", 1), http_status=200) from None
    # Current103 wrap_callback: code wins, then error_code, else implicit data.
    if "code" in response:
        code, expected, status_source = response["code"], 200, "code"
    elif "error_code" in response:
        code, expected, status_source = response["error_code"], 0, "error_code"
    else:
        code, expected, status_source = 0, 0, "implicit_data"
    if type(code) is not int or code != expected:
        raise CareerQueryError("xyq_application_rejected", http_status=200, application_code=code)
    return response.get("data"), status_source


def project_own_career_response(wrapper, own_uid):
    """Validate service/HTTP/application/owner independently, then project five totals."""
    own_uid = _uid(own_uid)
    try:
        data, status_source = decode_xyq_response(wrapper, "xyq-career")
    except CareerQueryError as error:
        # Preserve the existing own-Career diagnostic contract for its callers.
        raise CareerQueryError(error.code.replace("xyq_", "own_career_", 1),
                               http_status=error.http_status, application_code=error.application_code) from None
    result = _project_totals(data, own_uid, kind="own")
    result.update(http_status=200, application_status_source=status_source)
    return result


def project_other_career_response(data, target_uid):
    """Project native RPC callback data AFTER request/context/UID correlation.

    This pure projection does not authenticate a connection or grant visibility.
    The caller must retain those independent gates before publishing it.
    """
    if type(target_uid) is not int or not 0 < target_uid < 2**63:
        raise CareerQueryError("other_career_inputs_invalid")
    return _project_totals(data, target_uid, kind="other")


def _project_totals(data, target_uid, *, kind):
    career = data.get("career") if type(data) is dict else None
    if type(career) is not dict or len(career) > 512:
        raise CareerQueryError(kind + "_career_response_shape")
    if type(career.get("player_uid")) is not int or career["player_uid"] != target_uid:
        raise CareerQueryError(kind + "_career_identity_mismatch")
    stats = {}
    for name, key in STAT_FIELDS.items():
        value = career.get(key)
        if type(value) is not int or not 0 <= value <= 10**12:
            raise CareerQueryError(kind + "_career_statistics_shape")
        stats[name] = value
    if stats["wins"] > stats["matches"]:
        raise CareerQueryError(kind + "_career_statistics_shape")
    return {"outcome": kind + "_career_verified", "identity_verified": True,
            "career_verified": True, "uid": str(target_uid), "platform": "pc", "statistics": stats,
            "response_field_count": len(career), "projected_statistic_count": len(stats),
            "request_scope": "unfiltered", "history_completeness": "not_established",
            "ranks_verified": False, "matches_verified": False, "secrets_saved": False}
