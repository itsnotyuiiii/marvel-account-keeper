"""Research-only normal Steam saved-account token exchange; no credential lookup.

Public source: mpay-h5-sdk-oversea.umd.js v1.4.2, SHA256
062cb07880c31960f10ef5f5931b0c13e01825b0f426f952d8c56b1e61e4fa35:
loginWithHistory 809699, apiPost 796451, getRequiredKey 799719.
Store wrapper module.894e7ca6.js, SHA256
0084da0bb7730900d18a8c3d6fa529570d3ae53bc86d85498417faccd9bfc2d9:
history adapter 147658 and __createSauthJson 158242.

The saved account refreshToken is submitted as token, not sessionid or a game
UID. HTTP device_id/ci are explicitly supplied Mpay context; neither is inferred
from similarly named SAUTH fields. The source has no per-request signature or
encryption beyond HTTPS. Public source does not prove server-side acceptance.

Success returns only transient replacement tokens and request-bound context.
A separate pure builder can reproduce a NEW wrapper's SAUTH using explicitly
supplied wrapper UDID, clock and random samples. It does not assume the current
browser wrapper's UID/SESSION properties or change that wrapper's state.
Fresh normal game AUTH for expected_owner_uid is still required. There is no
storage access, device initialization, OAuth follow-up, login UI, retry, fallback,
token-lifetime claim, game lookup or production integration.
"""
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from urllib import error as urlerror
from urllib import request as urlrequest

from game_login_probe import LoginError
from web_sdk_auth_probe import EXPECTED_OWNER_UID, build_web_login_inputs


HISTORY_ENDPOINT = "https://sdk-os.mpsdk.easebar.com/api/users/login/v2/sdk_token"
TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 64 * 1024
MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_TOKEN_BYTES = 32768
MAX_DATE_MILLISECONDS = 8640000000000000  # ECMAScript Date's finite range.
ACCOUNT_FIELDS = frozenset({"userId", "refreshToken", "method", "clientPlatform", "deviceId"})
PUBLIC_CONTEXT = {
    "game_id": "502", "cp": "w", "cv": "3.1.5", "gv": "1.0.0",
    "app_channel": "netease.allysdk_global", "debug_mode": "0",
}
CONTEXT_FIELDS = frozenset(PUBLIC_CONTEXT) | {"ci", "lang", "device_id"}
CONTEXT_ORDER = ("game_id", "cp", "cv", "gv", "ci", "app_channel", "lang",
                 "device_id", "debug_mode", "package_name")
ERROR_CODES = frozenset({
    "history_context_invalid", "history_account_unreviewed", "history_owner_binding_invalid",
    "history_numeric_id_unsupported", "history_form_value_unsupported", "history_http_error",
    "history_response_bound", "history_response_json", "history_response_shape",
    "history_service_rejected", "history_response_token_invalid", "history_request_failed",
    "history_sauth_context_invalid", "history_serial_context_invalid",
})


class HistoryLoginError(ValueError):
    def __init__(self, code, *, request_attempted=False, http_status=None, application_code=None):
        self.code = code if type(code) is str and code in ERROR_CODES else "history_request_failed"
        self.request_attempted = request_attempted if type(request_attempted) is bool else None
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.application_code = (application_code if type(application_code) is int
                                 and -(1 << 31) <= application_code < (1 << 31) else None)
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class HistoryLoginInputs:
    expected_owner_uid: int
    sauth_sdkuid: str
    saved_user_id: int
    saved_device_id: str
    refresh_token: str
    # Immutable snapshot of actual supplied getRequiredKey values, not defaults.
    mpay_context: tuple


@dataclass(frozen=True, repr=False)
class HistoryLoginCandidate:
    expected_owner_uid: int
    sdkuid: str
    saved_device_id: str
    access_token: str
    refresh_token: str
    platform: str = field(default="pc", init=False)
    method: str = field(default="steam", init=False)
    owner_authentication_verified: bool = field(default=False, init=False)
    automatic_renewal_verified: bool = field(default=False, init=False)
    sauth_constructed: bool = field(default=False, init=False)


@dataclass(frozen=True, repr=False)
class HistorySauthCandidate:
    expected_owner_uid: int
    sauth: dict = field(repr=False)
    refresh_token: str = field(repr=False)
    owner_authentication_verified: bool = field(default=False, init=False)
    automatic_renewal_verified: bool = field(default=False, init=False)
    sauth_constructed: bool = field(default=True, init=False)


def _bounded_string(value, limit, code, *, allow_empty=False):
    if type(value) is not str or not allow_empty and not value.strip():
        raise HistoryLoginError(code)
    try:
        if len(value.encode("utf-8")) > limit:
            raise HistoryLoginError(code)
    except UnicodeError:
        raise HistoryLoginError(code) from None
    return value


def _device_string(value):
    # The source uses String(savedAccount.deviceId) for outgoing SAUTH. HTTP
    # serialization similarly stringifies a supplied number; never invent one.
    if type(value) is int:
        if not 0 < value <= MAX_SAFE_INTEGER:
            raise HistoryLoginError("history_numeric_id_unsupported")
        value = str(value)
    return _bounded_string(value, 512, "history_context_invalid")


def _form_value(value, limit=512, *, allow_empty=False):
    _bounded_string(value, limit, "history_form_value_unsupported", allow_empty=allow_empty)
    # Source qs encode:false sends raw values. Do not silently transform a
    # credential or allow its form-decoded value/field count to change.
    if any(not 0x21 <= ord(char) <= 0x7e or char in "&+%" for char in value):
        raise HistoryLoginError("history_form_value_unsupported")
    return value


def _checked_inputs(inputs):
    if type(inputs) is not HistoryLoginInputs:
        raise HistoryLoginError("history_context_invalid")
    if type(inputs.expected_owner_uid) is not int or inputs.expected_owner_uid != EXPECTED_OWNER_UID:
        raise HistoryLoginError("history_owner_binding_invalid")
    sdkuid = inputs.sauth_sdkuid
    if (type(sdkuid) is not str or re.fullmatch(r"[1-9][0-9]{0,15}", sdkuid) is None
            or int(sdkuid) > MAX_SAFE_INTEGER
            or type(inputs.saved_user_id) is not int or not 0 < inputs.saved_user_id <= MAX_SAFE_INTEGER):
        raise HistoryLoginError("history_numeric_id_unsupported")
    if sdkuid != str(inputs.saved_user_id):
        raise HistoryLoginError("history_owner_binding_invalid")
    _bounded_string(inputs.saved_device_id, 512, "history_context_invalid")
    _form_value(inputs.refresh_token, MAX_TOKEN_BYTES)
    pairs = inputs.mpay_context
    if type(pairs) is not tuple or len(pairs) not in (len(CONTEXT_FIELDS), len(CONTEXT_FIELDS) + 1):
        raise HistoryLoginError("history_context_invalid")
    context = {}
    for pair in pairs:
        if type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str or pair[0] in context:
            raise HistoryLoginError("history_context_invalid")
        context[pair[0]] = pair[1]
    if not CONTEXT_FIELDS <= context.keys() or context.keys() - (CONTEXT_FIELDS | {"package_name"}):
        raise HistoryLoginError("history_context_invalid")
    for key, value in PUBLIC_CONTEXT.items():
        if type(context[key]) is not str or context[key] != value:
            raise HistoryLoginError("history_context_invalid")
    if type(context["lang"]) is not str or re.fullmatch(r"[a-z]{2,3}_[A-Za-z0-9]{2,8}(?:_[A-Za-z0-9]{2,8})?", context["lang"]) is None:
        raise HistoryLoginError("history_context_invalid")
    for key, value in context.items():
        _form_value(value, allow_empty=key == "package_name")
    return context


def build_history_login_inputs(previous_sauth, saved_account, mpay_context):
    """Bind an exact projected saved Steam account to prior observed SDK identity.

    All context must be supplied explicitly. This compares the SDK account IDs,
    not the public game UID; expected_owner_uid is a later game-AUTH constraint,
    not a claim that this pure builder has authenticated the owner. Additional
    saved-record display fields must be projected out by the caller.
    """
    try:
        prior = json.loads(build_web_login_inputs(previous_sauth).auth["sauth_json"])
    except (LoginError, ValueError, TypeError, RecursionError):
        raise HistoryLoginError("history_context_invalid") from None
    if type(saved_account) is not dict or saved_account.keys() != ACCOUNT_FIELDS:
        raise HistoryLoginError("history_account_unreviewed")
    account = saved_account.copy()
    if (type(account["method"]) is not str or account["method"] != "steam"
            or type(account["clientPlatform"]) is not str or account["clientPlatform"] != "p"):
        raise HistoryLoginError("history_account_unreviewed")
    if type(mpay_context) is not dict:
        raise HistoryLoginError("history_context_invalid")
    context = mpay_context.copy()
    if "device_id" in context:
        context["device_id"] = _device_string(context["device_id"])
    inputs = HistoryLoginInputs(EXPECTED_OWNER_UID, prior["sdkuid"], account["userId"],
                                _device_string(account["deviceId"]), account["refreshToken"], tuple(context.items()))
    _checked_inputs(inputs)
    return inputs


def build_history_form(inputs):
    """Exact scalar-field order and raw-string semantics of the source merge."""
    context = _checked_inputs(inputs)
    fields = {"user_id": str(inputs.saved_user_id), "token": inputs.refresh_token,
              "cp": context["cp"], "platform": "p", "scene": "login"}
    # JS object spread overwrites cp without moving its initial position.
    fields.update((key, context[key]) for key in CONTEXT_ORDER if key in context)
    return "&".join(key + "=" + value for key, value in fields.items()).encode("ascii")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise HistoryLoginError("history_response_json")
        value[key] = item
    return value


def _nonfinite(_value):
    raise HistoryLoginError("history_response_json")


def validate_history_response(inputs, response_bytes, *, http_status):
    _checked_inputs(inputs)
    if type(http_status) is not int or not 200 <= http_status < 300:
        raise HistoryLoginError("history_http_error", http_status=http_status)
    if type(response_bytes) is not bytes or not 0 < len(response_bytes) <= MAX_RESPONSE_BYTES:
        raise HistoryLoginError("history_response_bound", http_status=http_status)
    try:
        result = json.loads(response_bytes.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=_nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise HistoryLoginError("history_response_json", http_status=http_status) from None
    if type(result) is not dict or type(result.get("code")) is not int:
        raise HistoryLoginError("history_response_shape", http_status=http_status)
    if result["code"] != 0:
        # A rejection can be transient, rate-related or account-related. The
        # SDK's login fallback is not proof that a credential is invalid.
        raise HistoryLoginError("history_service_rejected", http_status=http_status,
                                application_code=result["code"])
    user = result.get("user")
    if type(user) is not dict:
        raise HistoryLoginError("history_response_shape", http_status=http_status)
    try:
        access = _bounded_string(user.get("login_token"), MAX_TOKEN_BYTES, "history_response_token_invalid")
        refresh = _bounded_string(user.get("token"), MAX_TOKEN_BYTES, "history_response_token_invalid")
    except HistoryLoginError as error:
        raise HistoryLoginError(error.code, http_status=http_status) from None
    # The SDK also preserves request userId/deviceId. Extra response identity,
    # expiry, messages and flags are not projected or accepted as proof.
    return HistoryLoginCandidate(inputs.expected_owner_uid, inputs.sauth_sdkuid,
                                 inputs.saved_device_id, access, refresh)


def build_fresh_wrapper_sauth(candidate, *, wrapper_udid, clock_ms, random_samples):
    """Reproduce __createSauthJson for a fresh, initialized store SDK wrapper.

    Pinned wrapper: base constructor 88196 omits UID/SESSION; _ntInit 103291
    sets UDID from UNISDK_UDID, not Mpay's UDID. Store initialization does not
    override UID/SESSION. _generateClientLoginSn 91239 reads those properties
    before __createSauthJson 158242 calls __setUserInfo (which later sets UID
    from SAUTH.udid). Thus both || "" inputs are empty for this NEW context.
    This is not an inference about a reused browser wrapper's current state.

    Supply eight Math.random-equivalent samples first, then the Date.getTime
    millisecond value. Positive Math.round uses floor(x + 0.5), NOT Python's
    ties-to-even round. MD5's ordinary UTF-8 lowercase hex output is retained.
    This function reads no clock, RNG, browser, files, network or credentials.
    """
    if type(candidate) is not HistoryLoginCandidate:
        raise HistoryLoginError("history_sauth_context_invalid")
    if type(candidate.expected_owner_uid) is not int or candidate.expected_owner_uid != EXPECTED_OWNER_UID:
        raise HistoryLoginError("history_owner_binding_invalid")
    if (type(candidate.sdkuid) is not str
            or re.fullmatch(r"[1-9][0-9]{0,15}", candidate.sdkuid) is None
            or int(candidate.sdkuid) > MAX_SAFE_INTEGER):
        raise HistoryLoginError("history_numeric_id_unsupported")
    _bounded_string(candidate.saved_device_id, 512, "history_sauth_context_invalid")
    _bounded_string(candidate.access_token, MAX_TOKEN_BYTES, "history_response_token_invalid")
    _bounded_string(candidate.refresh_token, MAX_TOKEN_BYTES, "history_response_token_invalid")
    _bounded_string(wrapper_udid, 512, "history_sauth_context_invalid")
    if type(clock_ms) is not int or not 0 <= clock_ms <= MAX_DATE_MILLISECONDS:
        raise HistoryLoginError("history_serial_context_invalid")
    if type(random_samples) not in (list, tuple) or len(random_samples) != 8:
        raise HistoryLoginError("history_serial_context_invalid")
    samples = tuple(random_samples)
    if any(type(value) not in (int, float) or not 0 <= value < 1 for value in samples):
        raise HistoryLoginError("history_serial_context_invalid")
    digits = "".join(str(math.floor(9 * value + 0.5)) for value in samples)
    serial = hashlib.md5(f"{wrapper_udid}_{clock_ms}_{digits}__".encode("utf-8"),
                         usedforsecurity=False).hexdigest()
    # Exact insertion order/ten-field mapping at __createSauthJson. Public
    # constants are the current Marvel store's initialization values, not the
    # Mpay HTTP platform/cv or the native game build version.
    sauth = {
        "gameid": "x20naxx2gb", "login_channel": "netease_global",
        "app_channel": "netease.allysdk_global", "platform": "pc",
        "sdkuid": str(candidate.sdkuid), "sessionid": candidate.access_token,
        "deviceid": str(candidate.saved_device_id), "sdk_version": "1.0.0",
        "udid": wrapper_udid, "client_login_sn": serial,
    }
    try:
        build_web_login_inputs(sauth)
    except (LoginError, ValueError, TypeError, RecursionError):
        raise HistoryLoginError("history_sauth_context_invalid") from None
    return HistorySauthCandidate(EXPECTED_OWNER_UID, sauth, candidate.refresh_token)


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def login_history_once(inputs):
    """One bounded HTTPS exchange. Never invokes OAuth, SDK UI or game AUTH."""
    body = build_history_form(inputs)  # Complete all input checks before IO.
    attempted, status = False, None
    try:
        opener = urlrequest.build_opener(urlrequest.ProxyHandler({}), _NoRedirect(), urlrequest.HTTPSHandler())
        request = urlrequest.Request(HISTORY_ENDPOINT, data=body, method="POST", headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        })
        attempted = True
        with opener.open(request, timeout=TIMEOUT_SECONDS) as reply:
            status = reply.status
            if type(status) is not int or not 200 <= status < 300:
                raise HistoryLoginError("history_http_error", http_status=status)
            raw = reply.read(MAX_RESPONSE_BYTES + 1)
    except urlerror.HTTPError as error:
        status = error.code
        try:
            error.close()
        except Exception:
            pass
        raise HistoryLoginError("history_http_error", request_attempted=attempted, http_status=status) from None
    except HistoryLoginError as error:
        raise HistoryLoginError(error.code, request_attempted=attempted, http_status=status) from None
    except Exception:
        raise HistoryLoginError("history_request_failed", request_attempted=attempted, http_status=status) from None
    try:
        return validate_history_response(inputs, raw, http_status=status)
    except HistoryLoginError as error:
        raise HistoryLoginError(error.code, request_attempted=True, http_status=status,
                                application_code=error.application_code) from None
