"""Standalone own-account XYQ Career probe; not a production stats provider.

Default CLI mode only previews a request. Sending requires a separately verified
first-party gateway and an interactive, hidden prompt for a game-issued token.
This tool does not obtain credentials, inspect processes, import the app/vault,
write responses, retry requests, or follow redirects. Static evidence is recorded
in API_RESEARCH.md. A JSON response does not prove identity or correct statistics.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
import getpass
import http.client
import json
import math
import re
import ssl
import sys
import time
import warnings
import zlib


MAX_BYTES = 2 * 1024 * 1024
MAX_TOKEN_BYTES = 32768
OPERATIONS = {
    "profile": "query_my_player_career",
    "summary": "query_my_match_summary",
    "match": "uid_query_my_match",
}
EXPECTED_FIELDS = {
    "profile": ("career", "sort_hero", "hero_careers"),
    "summary": ("match_info",),
    "match": ("matches",),
}


def positive_id(value: str | int, label: str) -> int:
    if type(value) not in (str, int) or not re.fullmatch(r"[1-9][0-9]{0,19}", str(value)):
        raise ValueError(f"{label} must be a positive ASCII integer of at most 20 digits.")
    return int(value)


@dataclass(frozen=True)
class CareerQuery:
    kind: str
    own_uid: str | int
    zone_id: str | int
    platform: str = "pc"
    season: int | None = None
    match_id: str | None = None

    def body(self) -> dict:
        if self.kind not in OPERATIONS:
            raise ValueError("Choose profile, summary, or match.")
        body = {"player_uid": positive_id(self.own_uid, "Own player UID"),
                "zone_id": positive_id(self.zone_id, "Current zone")}
        if self.platform not in ("pc", "ps", "xbox"):
            raise ValueError("Platform must be pc, ps, or xbox.")
        if self.kind != "profile" and (self.season is not None or self.platform != "pc"):
            raise ValueError("Platform/season selection is supported only by the profile request.")
        if self.kind != "match" and self.match_id is not None:
            raise ValueError("A match ID is supported only by the match request.")
        if self.kind == "profile":
            body["device_type"] = self.platform
            if self.season is not None:
                body["match_season"] = positive_id(self.season, "Season")
        elif self.kind == "summary":
            body.update(page=0, page_size=50)
        else:
            if not isinstance(self.match_id, str) or len(self.match_id) > 100 or not re.fullmatch(
                    r"[0-9]+(?:_[0-9]+){4}", self.match_id):
                raise ValueError("Supply the five-part internal match ID, not a replay code.")
            body["match_uids"] = [self.match_id]
        return body

    @property
    def path(self) -> str:
        self.body()
        return "/xyq_service/xyq-career/" + OPERATIONS[self.kind]


def gateway_authority(value: str) -> tuple[str, int]:
    """Deliberately narrow until actual first-party gateway evidence is available."""
    if not isinstance(value, str) or len(value) > 260:
        raise ValueError("Supply a verified first-party DNS hostname, optionally with a port.")
    match = re.fullmatch(r"([a-zA-Z0-9.-]+)(?::([0-9]{1,5}))?", value)
    if not match:
        raise ValueError("Gateway must not include a scheme, path, credentials, or query.")
    host = match[1].lower()
    if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                              for label in host.split(".")):
        raise ValueError("Invalid gateway hostname.")
    if not any(host.endswith("." + suffix) for suffix in ("easebar.com", "netease.com")):
        raise ValueError("Gateway is outside the currently permitted first-party domains.")
    port = int(match[2] or 443)
    if not 1 <= port <= 65535:
        raise ValueError("Invalid gateway port.")
    return host, port


def validate_session(token: str, gateway: str, *, now: float | None = None) -> None:
    """Check routing/expiry hints, NOT JWT signature, account ownership or server acceptance."""
    destination = gateway_authority(gateway)
    if not isinstance(token, str) or len(token) > MAX_TOKEN_BYTES or not re.fullmatch(
            r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
        raise ValueError("Expected an unmodified game-issued JWT; no whitespace or Bearer prefix.")
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.b64decode(part + "=" * (-len(part) % 4), altchars=b"-_", validate=True))
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Session claims are not readable JSON.") from None
    if not isinstance(claims, dict) or not claims.get("aid"):
        raise ValueError("Missing SocialMatrix session identity field.")
    websocket = claims.get("websocket")
    if not isinstance(websocket, dict) or websocket.get("tls") is not True:
        raise ValueError("Session does not explicitly identify a TLS-enabled gateway.")
    if gateway_authority(websocket.get("addr")) != destination:
        raise ValueError("The independently verified gateway does not match the session routing hint.")
    current = time.time() if now is None else now
    for name in ("exp", "nbf"):
        value = claims.get(name)
        if name in claims:
            if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
                raise ValueError("Invalid session time claim.")
            if (name == "exp" and value <= current) or (name == "nbf" and value > current):
                raise ValueError("Session is expired or not yet valid.")


@dataclass
class CareerProbeResult:
    outcome: str
    http_status: int | None = None
    expected_fields: tuple[str, ...] = ()
    data: dict | None = field(default=None, repr=False)

    def summary(self) -> dict:
        # Never reflect arbitrary response values, keys, errors or credentials.
        return {"outcome": self.outcome, "http_status": self.http_status,
                "expected_fields": list(self.expected_fields),
                "identity_verified": False, "statistics_verified": False}


def profile_identity_evidence(query: CareerQuery, result: CareerProbeResult) -> dict:
    """Compare only data.career.player_uid, as consumed by client entry 1833.

    Matching the requested UID is NOT proof that it is the logged-in account,
    that the season/platform selection is correct, or that statistics are valid.
    Do not reflect an unexpected account ID or any arbitrary response values.
    """
    if query.kind != "profile" or result.outcome != "json_received":
        return {"profile_uid_check": "not_available", "usable_profile_identity": False}
    profile = result.data.get("career") if isinstance(result.data, dict) else None
    if not isinstance(profile, dict):
        return {"profile_uid_check": "missing_profile_object", "usable_profile_identity": False}
    if "player_uid" not in profile:
        return {"profile_uid_check": "missing_uid", "usable_profile_identity": False}
    try:
        returned = positive_id(profile["player_uid"], "Response player UID")
    except ValueError:
        return {"profile_uid_check": "invalid_uid_type_or_format", "usable_profile_identity": False}
    matches = returned == positive_id(query.own_uid, "Own player UID")
    return {"profile_uid_check": "matches_requested_uid" if matches else "different_uid",
            "usable_profile_identity": matches}


def classify_response(kind: str, status: int, raw: bytes, encoding: str = "") -> CareerProbeResult:
    def result(outcome, **extra):
        return CareerProbeResult(outcome, status, **extra)
    if status in (401, 403):
        return result("access_blocked")
    if status == 429:
        return result("rate_limited")
    if 300 <= status < 400:
        return result("redirect_refused")
    if status != 200:
        return result("http_error")
    if len(raw) > MAX_BYTES:
        return result("response_too_large")
    if encoding.strip().lower() == "gzip":
        try:
            decoder = zlib.decompressobj(31)
            raw = decoder.decompress(raw, MAX_BYTES + 1)
            if len(raw) > MAX_BYTES or decoder.unconsumed_tail:
                return result("response_too_large")
            if not decoder.eof or decoder.unused_data:
                return result("invalid_response")
        except zlib.error:
            return result("invalid_response")
    elif encoding.strip().lower() not in ("", "identity"):
        return result("unsupported_encoding")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return result("invalid_response")
    if not isinstance(payload, dict):
        return result("invalid_response")
    if not any(key in payload for key in ("code", "error_code")):
        return result("unverified_envelope")
    if any(key in payload and (type(payload[key]) is not int or payload[key] != success)
           for key, success in (("code", 200), ("error_code", 0))):
        return result("application_error")
    data = payload.get("data")
    if not isinstance(data, dict):
        return result("unexpected_data_shape")
    fields = tuple(name for name in EXPECTED_FIELDS[kind] if name in data)
    return result("json_received" if fields else "unexpected_data_shape", expected_fields=fields, data=data)


def probe_career(query: CareerQuery, token: str, gateway: str, *, timeout: float = 15,
                 connection_factory=None) -> CareerProbeResult:
    body = json.dumps(query.body(), separators=(",", ":")).encode("utf-8")
    validate_session(token, gateway)
    host, port = gateway_authority(gateway)
    connection = None
    try:
        connection = (connection_factory or http.client.HTTPSConnection)(
            host, port, timeout=timeout, context=ssl.create_default_context())
        # http.client neither follows redirects nor forwards this header elsewhere.
        connection.request("POST", query.path, body=body, headers={
            "Authorization": token, "Content-Type": "application/json", "Accept-Encoding": "gzip"})
        response = connection.getresponse()
        raw = response.read(MAX_BYTES + 1) if response.status == 200 else b""
        return classify_response(query.kind, response.status, raw, response.getheader("Content-Encoding", ""))
    except (OSError, http.client.HTTPException):
        # Exceptions can include remote text; never reflect them in diagnostics.
        return CareerProbeResult("network_error")
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=OPERATIONS)
    parser.add_argument("--own-uid", required=True)
    parser.add_argument("--zone-id", required=True)
    parser.add_argument("--platform", default="pc", choices=("pc", "ps", "xbox"))
    parser.add_argument("--season", type=int)
    parser.add_argument("--match-id")
    parser.add_argument("--send", action="store_true", help="Send one request; otherwise only preview")
    parser.add_argument("--gateway", help="Independently verified first-party hostname[:port]; required with --send")
    args = parser.parse_args(argv)
    query = CareerQuery(args.kind, args.own_uid, args.zone_id, args.platform, args.season, args.match_id)
    try:
        body = query.body()
        if not args.send:
            print(json.dumps({"mode": "preview_only", "method": "POST", "path": query.path, "body": body}, indent=2))
            return 0
        if not args.gateway:
            raise ValueError("Sending requires an independently verified --gateway.")
        gateway_authority(args.gateway)
        if not sys.stdin.isatty():
            raise ValueError("Sending requires an interactive hidden token prompt; do not put tokens in commands or files.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("Game-issued SocialMatrix token (hidden, not saved): ")
        result = probe_career(query, token, args.gateway)
        del token
        summary = result.summary()
        if query.kind == "profile":
            evidence = profile_identity_evidence(query, result)
            summary.update(evidence)
            print(json.dumps(summary, indent=2))
            return 0 if evidence["usable_profile_identity"] else 1
        print(json.dumps(summary, indent=2))
        return 0 if result.outcome == "json_received" else 1
    except ValueError as exc:
        # Validation errors above are fixed descriptions, never supplied token values.
        print(str(exc), file=sys.stderr)
        return 2
    except getpass.GetPassWarning:
        print("Cannot obtain a hidden token prompt; no request was sent.", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("Probe cancelled.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
