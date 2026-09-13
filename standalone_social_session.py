"""Pure checks for a fresh, gateway-delivered SocialMatrix token.

Call only inside the verified owner's gateway callback. This module cannot
establish that provenance: decoding a JWT does NOT verify its signature, grant
account access, or prove SocialMatrix server acceptance. The immutable result
belongs to the caller's transient callback scope, not a persisted provider.

There are no files, captures, sockets, DNS lookups, credential stores, setup
messages or implicit renewals here. In particular, no captured hello/version,
profile template or fixed peer is reused. Exact normal hello/setup and optional
transport behavior still require a separately reviewed adapter.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import ipaddress
import json
import math
import re
import time


MAX_TOKEN_BYTES = 32768
MAX_JSON_BYTES = 24576
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 2048
MAX_ID = (1 << 64) - 1
MAX_TIMESTAMP = (1 << 53) - 1
ERROR_CODES = frozenset({
    "social_token_bound", "social_token_format", "social_token_json",
    "social_token_identity", "social_token_time", "social_token_route",
    "social_token_transport", "social_session_context", "social_session_invalid",
    "social_session_persistence_unsupported",
})


class SocialSessionError(ValueError):
    """Only fixed codes, never upstream data, addresses, identities or tokens."""

    def __init__(self, code):
        self.code = code if type(code) is str and code in ERROR_CODES else "social_session_invalid"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False, slots=True)
class SocialRoute:
    """One locally checked public IPv4 endpoint, not a trusted-server assertion."""

    host: str
    port: int


@dataclass(frozen=True, repr=False, slots=True)
class GatewaySocialToken:
    """Callback-owned secret material; do not log, serialize or retain globally.

    No raw claims/optional settings are exposed to an eventual connection
    adapter. Route support is intentionally narrower than the SDK's parser.
    Python strings cannot be reliably wiped; scope discipline is not zeroization.
    """

    token: str
    own_uid: int
    zone_id: int
    aid: str
    route: SocialRoute
    issued_at: int
    expires_at: int
    not_before: int | None

    def __reduce_ex__(self, protocol):
        raise SocialSessionError("social_session_persistence_unsupported")


def _identity(value):
    if type(value) is int:
        result = value
    elif type(value) is str and re.fullmatch(r"[1-9][0-9]{0,19}", value):
        result = int(value)
    else:
        raise SocialSessionError("social_session_context")
    if not 1 <= result <= MAX_ID:
        raise SocialSessionError("social_session_context")
    return result


def _now(value):
    value = time.time() if value is None else value
    if type(value) not in (int, float) or not 0 <= value <= MAX_TIMESTAMP or not math.isfinite(value):
        raise SocialSessionError("social_session_context")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SocialSessionError("social_token_json")
        result[key] = value
    return result


def _reject_constant(value):
    raise SocialSessionError("social_token_json")


def _check_json_tree(value):
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise SocialSessionError("social_token_json")
        if type(item) is dict:
            if len(item) > 256:
                raise SocialSessionError("social_token_json")
            for key, child in item.items():
                if type(key) is not str or len(key) > 256:
                    raise SocialSessionError("social_token_json")
                pending.extend(((key, depth + 1), (child, depth + 1)))
        elif type(item) is list:
            if len(item) > 512:
                raise SocialSessionError("social_token_json")
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            try:
                if len(item.encode("utf-8")) > MAX_JSON_BYTES:
                    raise SocialSessionError("social_token_json")
            except UnicodeError:
                raise SocialSessionError("social_token_json") from None
        elif type(item) is float:
            if not math.isfinite(item):
                raise SocialSessionError("social_token_json")
        elif item is not None and type(item) not in (int, bool):
            raise SocialSessionError("social_token_json")


def _segment(value, bound):
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError):
        raise SocialSessionError("social_token_format") from None
    if not decoded or len(decoded) > bound:
        raise SocialSessionError("social_token_bound")
    if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise SocialSessionError("social_token_format")
    return decoded


def _json_object(raw):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise SocialSessionError("social_token_json") from None
    if type(value) is not dict:
        raise SocialSessionError("social_token_json")
    _check_json_tree(value)
    return value


def _route(value):
    if type(value) is not dict:
        raise SocialSessionError("social_token_route")
    # This bounded subset matches the reviewed raw Magic-RC4 route. An absent
    # tcp.tls is not fabricated into a claim; an explicit true is unsupported.
    if value.get("rc4") is not True or ("tls" in value and value["tls"] is not False):
        raise SocialSessionError("social_token_transport")
    address = value.get("addr")
    if type(address) is not str or len(address) > 21:
        raise SocialSessionError("social_token_route")
    matched = re.fullmatch(r"([0-9]+(?:\.[0-9]+){3}):([1-9][0-9]{0,4})", address)
    if matched is None:
        raise SocialSessionError("social_token_route")
    host, port_text = matched.groups()
    try:
        ip = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        raise SocialSessionError("social_token_route") from None
    port = int(port_text)
    if (not 1 <= port <= 65535 or str(ip) != host or not ip.is_global or ip.is_private
            or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified):
        raise SocialSessionError("social_token_route")
    # Optional addition_addrs/signature and unknown claims are bounded JSON,
    # but deliberately not projected or used. They need separate source review
    # before any connector is built; no fallback/failover is enabled here.
    return SocialRoute(host, port)


def parse_gateway_social_token(token, *, own_uid, zone_id, now=None):
    """Check exact owner/zone, local time claims and a supported route in memory.

    The caller must establish a fresh, verified gateway callback first. JWT
    header/signature bytes are checked for format only, not cryptographic truth.
    Unknown optional claim values are bounded and discarded, never configuration.
    DNS/IPv6 or another transport fail as unsupported, not as an invalid UID.
    """
    own_uid, zone_id, current = _identity(own_uid), _identity(zone_id), _now(now)
    if type(token) is not str:
        raise SocialSessionError("social_token_format")
    if not 0 < len(token) <= MAX_TOKEN_BYTES:
        raise SocialSessionError("social_token_bound")
    if re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token) is None:
        raise SocialSessionError("social_token_format")
    head, body, signature = token.split(".")
    _json_object(_segment(head, 4096))
    claims = _json_object(_segment(body, MAX_JSON_BYTES))
    _segment(signature, 8192)
    aid = claims.get("aid")
    if type(aid) is not str or aid != f"{zone_id}_{own_uid}":
        raise SocialSessionError("social_token_identity")
    issued, expiry, not_before = claims.get("iat"), claims.get("exp"), claims.get("nbf")
    required_times = (issued, expiry) + ((not_before,) if "nbf" in claims else ())
    if (any(type(item) is not int or not 0 <= item <= MAX_TIMESTAMP for item in required_times)
            or not issued <= current < expiry or not issued < expiry
            or ("nbf" in claims and (not_before > current or not_before >= expiry))):
        raise SocialSessionError("social_token_time")
    route = _route(claims.get("tcp"))
    return GatewaySocialToken(token, own_uid, zone_id, aid, route, issued, expiry, not_before)


def social_token_metadata(session, *, now=None):
    """Recheck and emit only typed non-secret claims-check diagnostics."""
    if type(session) is not GatewaySocialToken:
        raise SocialSessionError("social_session_context")
    checked = parse_gateway_social_token(session.token, own_uid=session.own_uid,
                                         zone_id=session.zone_id, now=now)
    if checked != session:
        raise SocialSessionError("social_session_context")
    return {"token_bytes": len(checked.token), "expires_at": checked.expires_at,
            "identity_claim_matches": True, "time_claims_current": True,
            "tcp_route_supported": True, "jwt_signature_verified": False,
            "social_session_accepted": False}
