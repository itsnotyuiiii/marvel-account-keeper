"""Bounded normal OAuth refresh and pure validation for the official store.

Source: module.894e7ca6.js __getAccessToken, SHA256
0084da0bb7730900d18a8c3d6fa529570d3ae53bc86d85498417faccd9bfc2d9.
That path submits game_id/user_id/refresh_token, accepts integer code 0,
replaces outgoing SAUTH.sessionid with access_token, and removes refresh_token
from outgoing SAUTH. It retains the existing stored renewal token rather than
reading a replacement from this response. The distinct sdk_token history path
is not an automatic fallback and is not implemented here.

refresh_once makes one explicitly configured official request, without browser
state, cookies, environment credentials, redirects, retries or endpoint fallback.
There is no saved-credential read/write, login, token-expiry inference or credential
acquisition. Complete fresh normal game AUTH for the expected owner before
accepting the returned candidate. An SDK success code is not proof of game
authentication, token lifetime, or independent long-term renewal.
"""
from dataclasses import dataclass, field
import json
import re

import requests

from game_login_probe import LoginError
from web_sdk_auth_probe import build_web_login_inputs


MAX_RESPONSE_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 10
MAX_JS_SAFE_INTEGER = (1 << 53) - 1
# Current unauthenticated store config, /gameclub/games/marvelrivals?lan_code=en-US,
# returns data.uni_appid == "502" (not the game ID x20naxx2gb). main.cfbf45bf.js
# SHA256 40ce4d341e2fba9923d869cfffe6bb52ce839bf2a0a95b6ec3e3b1db33224083
# passes uni_appid to APPID and sets this origin WITH its trailing slash.
# The wrapper appends /api/... directly. Preserve that exact double-slash URL.
OAUTH_APPID = "502"
OAUTH_REFRESH_ENDPOINT = "https://sdk-os.mpsdk.easebar.com//api/users/login/v2/oauth/refresh_token"
ERROR_CODES = frozenset({
    "web_refresh_context_invalid", "web_refresh_response_json", "web_refresh_http_error",
    "web_refresh_response_bound", "web_refresh_response_shape", "web_refresh_reauthentication_required",
    "web_refresh_rotation_unverified", "web_refresh_access_token_shape",
    "web_refresh_configuration_unreviewed", "web_refresh_numeric_sdkuid_unsupported",
    "web_refresh_form_token_unsupported", "web_refresh_request_failed",
})


class WebSdkRenewalError(ValueError):
    """Fixed diagnostic codes only, never server text or credentials."""

    def __init__(self, code, *, request_attempted=False, http_status=None):
        self.code = code if type(code) is str and code in ERROR_CODES else "web_refresh_request_failed"
        self.request_attempted = request_attempted if type(request_attempted) is bool else None
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        super().__init__(self.code)


@dataclass(frozen=True)
class OAuthRefreshConfiguration:
    endpoint: str
    appid: str


class _NoRedirectSession(requests.Session):
    def resolve_redirects(self, *_args, **_kwargs):
        # Requests otherwise eagerly reads a redirect body to populate _next,
        # even with allow_redirects=False. Keep every response read bounded.
        return iter(())


@dataclass(frozen=True, repr=False)
class OAuthRefreshCandidate:
    sauth: dict = field(repr=False)
    refresh_token: str = field(repr=False)
    # Request context is preserved, but this HTTP contract has no independently
    # verified game-UID echo. Acceptance must be checked by normal game AUTH.
    owner_authentication_verified: bool = field(default=False, init=False)
    automatic_renewal_verified: bool = field(default=False, init=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WebSdkRenewalError("web_refresh_response_json")
        result[key] = value
    return result


def _reject_nonfinite(_value):
    raise WebSdkRenewalError("web_refresh_response_json")


def _validated_context(previous_sauth):
    try:
        if type(previous_sauth) is not dict:
            raise WebSdkRenewalError("web_refresh_context_invalid")
        previous = previous_sauth.copy()
        if ("refresh_token" not in previous or type(previous["refresh_token"]) is not str
                or not previous["refresh_token"].strip()):
            raise WebSdkRenewalError("web_refresh_context_invalid")
        inputs = build_web_login_inputs(previous)
        outgoing = json.loads(inputs.auth["sauth_json"])
    except (LoginError, ValueError, TypeError, RecursionError):
        raise WebSdkRenewalError("web_refresh_context_invalid") from None
    return outgoing, previous["refresh_token"]


def refresh_once(previous_sauth, *, configuration):
    """One normal HTTP refresh, returning only an unverified in-memory candidate.

    configuration must explicitly name the reviewed exact endpoint and APPID.
    The wrapper uses Number(sdkuid), not the game's role UID. Only canonical
    positive decimal SDK IDs that JS can represent losslessly are accepted.
    Its form body is raw string concatenation: reject tokens whose form-decoded
    value could differ, instead of changing credentials or guessing an encoding.
    This function neither authenticates the game owner nor follows SDK failure
    with interactive login, sdk_token, a second request, or saved-state changes.
    """
    if (type(configuration) is not OAuthRefreshConfiguration
            or type(configuration.endpoint) is not str
            or configuration.endpoint != OAUTH_REFRESH_ENDPOINT
            or type(configuration.appid) is not str or configuration.appid != OAUTH_APPID):
        raise WebSdkRenewalError("web_refresh_configuration_unreviewed")
    outgoing, refresh_token = _validated_context(previous_sauth)
    sdkuid = outgoing["sdkuid"]
    if (re.fullmatch(r"[1-9][0-9]{0,15}", sdkuid) is None
            or int(sdkuid) > MAX_JS_SAFE_INTEGER):
        raise WebSdkRenewalError("web_refresh_numeric_sdkuid_unsupported")
    if any(not 0x21 <= ord(char) <= 0x7e or char in "&+%" for char in refresh_token):
        raise WebSdkRenewalError("web_refresh_form_token_unsupported")
    # A private snapshot binds the response to precisely the submitted context,
    # even if another caller changes its input dictionary during the request.
    request_context = dict(outgoing, refresh_token=refresh_token)
    body = "game_id=" + configuration.appid + "&user_id=" + sdkuid + "&refresh_token=" + refresh_token
    request_attempted = False
    status = None
    try:
        with _NoRedirectSession() as session:
            session.trust_env = False
            session.auth = None
            session.proxies.clear()
            session.cookies.clear()
            session.headers.clear()
            session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
            request_attempted = True
            with session.post(configuration.endpoint, data=body, headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded",
            }, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=False, stream=True, verify=True) as reply:
                status = reply.status_code
                if type(status) is not int or not 200 <= status < 300:
                    raise WebSdkRenewalError("web_refresh_http_error", http_status=status)
                chunks, size = [], 0
                for chunk in reply.iter_content(chunk_size=4096):
                    if type(chunk) is not bytes:
                        raise WebSdkRenewalError("web_refresh_response_bound")
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise WebSdkRenewalError("web_refresh_response_bound")
                    if chunk:
                        chunks.append(chunk)
                response_bytes = b"".join(chunks)
    except WebSdkRenewalError as error:
        raise WebSdkRenewalError(error.code, request_attempted=request_attempted, http_status=status) from None
    except Exception:
        # Requests exceptions can embed request/response bodies, credentials or
        # URLs. Do not propagate those objects or messages into normal reports.
        raise WebSdkRenewalError("web_refresh_request_failed", request_attempted=request_attempted,
                                http_status=status) from None
    try:
        return validate_oauth_refresh_response(request_context, response_bytes, http_status=status)
    except WebSdkRenewalError as error:
        raise WebSdkRenewalError(error.code, request_attempted=True, http_status=status) from None


def validate_oauth_refresh_response(previous_sauth, response_bytes, *, http_status):
    """Return an unverified in-memory candidate, without mutating either input.

    previous_sauth is the request's complete observed ten-field SDK context
    plus its existing refresh_token. HTTP success follows the public SDK's
    Axios 2xx policy. Only the access_token response field is projected; free
    text and unrelated response fields never leave this validator. A response
    advertising refresh_token needs separate rotation-contract verification,
    not silent replacement or reuse of a potentially invalidated token.
    """
    outgoing, refresh_token = _validated_context(previous_sauth)
    if type(http_status) is not int or not 200 <= http_status < 300:
        raise WebSdkRenewalError("web_refresh_http_error", http_status=http_status)
    if type(response_bytes) is not bytes or not 0 < len(response_bytes) <= MAX_RESPONSE_BYTES:
        raise WebSdkRenewalError("web_refresh_response_bound")
    try:
        response = json.loads(response_bytes.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_reject_nonfinite)
    except (UnicodeError, ValueError, RecursionError):
        raise WebSdkRenewalError("web_refresh_response_json") from None
    if type(response) is not dict or type(response.get("code")) is not int:
        raise WebSdkRenewalError("web_refresh_response_shape")
    if response["code"] != 0:
        # The official wrapper clears its stored state and opens normal login.
        # This pure function only reports that condition; it never logs in,
        # deletes a saved token, retries, or tries a second endpoint.
        raise WebSdkRenewalError("web_refresh_reauthentication_required")
    if "refresh_token" in response:
        raise WebSdkRenewalError("web_refresh_rotation_unverified")
    session = response.get("access_token")
    if type(session) is not str or not session.strip():
        raise WebSdkRenewalError("web_refresh_access_token_shape")
    outgoing["sessionid"] = session
    try:
        # Recheck the outgoing string and complete wire-size bounds. No normal
        # game metadata or gas token is invented by the OAuth response parser.
        build_web_login_inputs(outgoing)
    except (LoginError, ValueError, TypeError, RecursionError):
        raise WebSdkRenewalError("web_refresh_access_token_shape") from None
    return OAuthRefreshCandidate(outgoing, refresh_token)
