"""One-use, loopback-only handoff for an owner-authorized SDK AUTH experiment.

Preview by default. --serve-once accepts one bounded in-memory SDK bundle from
the official store origin, sends one normal NetEase AUTH request, verifies the
expected existing own UID, and exits. Explicit --enter additionally verifies
one normal gateway login for that same owner and closes. No role creation, stats,
game files, SDK logs, captures, credential files or production changes.
--collect-social-token also waits passively for the normal owner-bound token
push, checks its claims in memory, then closes without any SocialMatrix request.
--verify-social-session additionally establishes one normal SocialMatrix setup
using that fresh token and a packaged public parameter; no player queries.
--lookup-uid explicitly adds one current mesh user lookup to that setup. Only
the exact bound name/platform and independent visibility projection is returned;
no Career, ranks, matches or punishment records are queried by this flag.
--own-career adds one unfiltered PC Career read after exact owner mesh identity
verification. It cannot select another player's owner-only Career operation.
--other-career uses the same verified game connection for one explicit other
UID's native aggregate, after that UID's fresh mesh overview permission passes.
--other-matches selects one native first match page instead, independently gated
by history visibility. It cannot be combined with either Career flag.
--refresh-before-auth explicitly uses one normal OAuth refresh first. Only this
mode accepts a renewal token in the one-use handoff; nothing is saved or retried.
Missing native metadata is omitted, never fabricated. --with-reviewed-version
adds only client_version and engine_ver from the pinned public manifest. It
does not claim that this reviewed build remains current. Rejection is ambiguous:
the token, omitted envelope fields or game version may be incompatible.
--with-reviewed-pc-envelope additionally supplies the independently reviewed
native SDC_LOG_OS_NAME getter's pc value, not guessed device metadata.
--runtime-version explicitly selects an independently reviewed packaged build;
the legacy default remains unchanged. No installed game is read at runtime.
"""
import sys

sys.dont_write_bytecode = True

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import portable_game_runtime as runtime
import web_sdk_auth_probe as probe


STORE_ORIGIN = "https://pay.neteasegames.com"
MAX_HANDOFF_BYTES = 16 * 1024
WAIT_SECONDS = 75


def attempt_auth(sauth, *, with_reviewed_version=False, with_reviewed_pc_envelope=False,
                 runtime_version="3825464", enter_gateway=False, collect_social_token=False,
                 refresh_before_auth=False, verify_social_session=False, lookup_uid=None, own_career=False,
                 other_career=False, other_matches=False):
    # Validate before consulting any file or opening a network connection.
    probe.build_web_login_inputs(sauth)
    provenance, pin = runtime.reviewed_runtime_pin(runtime_version)
    if any(type(flag) is not bool for flag in (own_career, other_career, other_matches)):
        raise ValueError("uid_lookup_inputs_invalid")
    if other_matches and (own_career or other_career):
        raise ValueError("select_one_native_section")
    if (other_career or other_matches) and (own_career or type(lookup_uid) is not int
                         or not 0 < lookup_uid < 2**63 or lookup_uid == 1370598422):
        raise ValueError("other_career_requires_explicit_other_uid")
    if own_career:
        if lookup_uid is not None and (type(lookup_uid) is not int or lookup_uid != 1370598422):
            raise ValueError("own_career_requires_owner_uid")
        lookup_uid = 1370598422
    if lookup_uid is not None and (type(lookup_uid) is not int or not 0 < lookup_uid < 2**64):
        raise ValueError("uid_lookup_inputs_invalid")
    if (any(type(flag) is not bool for flag in (enter_gateway, collect_social_token, refresh_before_auth,
                                               verify_social_session))
            or ((enter_gateway or collect_social_token or refresh_before_auth or verify_social_session
                 or lookup_uid is not None) and (
            runtime_version != "3848703" or not with_reviewed_pc_envelope))):
        raise ValueError("gateway_requires_current_reviewed_pc_envelope")
    if refresh_before_auth and ("refresh_token" not in sauth or not sauth["refresh_token"].strip()):
        raise ValueError("web_refresh_context_invalid")
    public = runtime.load_runtime_manifest(
        Path(__file__).resolve().parents[1] / "runtime-inputs" / (runtime_version + ".json"),
        expected_sha256=pin, expected_provenance=provenance)
    social_public = None
    if verify_social_session or lookup_uid is not None:
        from portable_social_runtime import load_social_runtime
        social_public = load_social_runtime(
            Path(__file__).resolve().parents[1] / "runtime-inputs" / (runtime_version + "-social.json"))
    import requests
    with requests.Session() as session:
        # No netrc credentials, proxy credentials, cookies or alternate URL.
        session.trust_env = False
        with session.get(public.login_list_url, timeout=10,
                         allow_redirects=False, stream=True) as response:
            if response.status_code != 200 or response.url != runtime.LOGIN_LIST_URL:
                raise ValueError("official_server_list_unavailable")
            raw = response.raw.read(4097, decode_content=True)
    server = runtime.project_login_server(raw)
    refresh_report = None
    if refresh_before_auth:
        import web_sdk_renewal as renewal
        try:
            candidate = renewal.refresh_once(sauth, configuration=renewal.OAuthRefreshConfiguration(
                renewal.OAUTH_REFRESH_ENDPOINT, renewal.OAUTH_APPID))
        except renewal.WebSdkRenewalError as exc:
            result = probe._report("web_refresh_failed")
            result["sdk_refresh"] = {
                "outcome": exc.code, "request_attempted": exc.request_attempted,
                "http_status": exc.http_status,
                "candidate_received": False, "owner_authentication_verified": False,
                "automatic_renewal_verified": False, "secrets_saved": False}
            return result
        sauth = candidate.sauth
        refresh_report = {"outcome": "web_refresh_candidate_received", "request_attempted": True,
                          "candidate_received": True, "owner_authentication_verified": False,
                          "automatic_renewal_verified": False, "secrets_saved": False}
    if verify_social_session or lookup_uid is not None:
        from game_social_token_probe import run_social_token_once
        from standalone_social_transport import run_social_setup_once
        setup_report, consumed, native_result = None, False, None

        def on_token(fresh_session):
            nonlocal setup_report, consumed
            if consumed:
                raise ValueError("social_setup_already_consumed")
            consumed = True
            if lookup_uid is not None:
                from standalone_profile_probe import run_uid_lookup_once
                career_options = {"own_career": True} if own_career else {}
                setup_report = run_uid_lookup_once(fresh_session, parameter=social_public.parameter,
                                                   target_uid=lookup_uid, **career_options)
            else:
                setup_report = run_social_setup_once(fresh_session, parameter=social_public.parameter)

        def on_game_connection(connection, transport, zone):
            nonlocal native_result
            from standalone_native_sections import read_other_career_once, read_other_matches_once
            read_section = read_other_matches_once if other_matches else read_other_career_once
            native_result = read_section(connection, transport, zone, public.career_methods,
                                         setup_report=setup_report, target_uid=lookup_uid)

        game_options = {"on_game_connection": on_game_connection} if other_career or other_matches else {}
        report = run_social_token_once(sauth, public.configuration, server,
                                       client_version=public.provenance.client_version,
                                       on_token=on_token, **game_options)
        token_report = report.get("social_token", {})
        if (report.get("game_login_verified") is True and type(token_report) is dict
                and token_report.get("owner_claim_matches") is True
                and token_report.get("consumer_completed") is True and setup_report is not None):
            report["social_setup"] = setup_report
            career_result = setup_report.get("career_lookup", {})
            if (own_career and type(career_result) is dict and career_result.get("career_verified") is True
                    and career_result.get("identity_verified") is True
                    and career_result.get("uid") == "1370598422"):
                report["career_verified"] = True
            if ((other_career or other_matches) and token_report.get("on_game_connection_completed") is True
                    and type(native_result) is dict):
                report["other_matches" if other_matches else "other_career"] = native_result
                verified_key = "matches_verified" if other_matches else "career_verified"
                if (native_result.get(verified_key) is True
                        and native_result.get("request_bound") is True
                        and native_result.get("identity_verified") is True
                        and native_result.get("uid") == str(lookup_uid)):
                    report[verified_key] = True
    elif collect_social_token:
        from game_social_token_probe import run_social_token_once
        report = run_social_token_once(sauth, public.configuration, server,
                                       client_version=public.provenance.client_version)
    elif enter_gateway:
        report = probe.run_gateway_once(
            sauth, public.configuration, server,
            client_version=public.provenance.client_version, os_name="pc")
    else:
        report = probe.run_auth_once(
            sauth, public.configuration, server,
            client_version=public.provenance.client_version
            if with_reviewed_version or with_reviewed_pc_envelope else None,
            os_name="pc" if with_reviewed_pc_envelope else None)
    if refresh_report is not None:
        refresh_report["owner_authentication_verified"] = (
            report.get("auth_service_accepted") is True and report.get("own_role_verified") is True)
        report["sdk_refresh"] = refresh_report
    return report


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def make_handler(path, attempt=attempt_auth, *, allow_refresh=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log URLs, payloads or request headers.

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def valid_source(self):
            return (self.client_address[0] == "127.0.0.1"
                    and self.headers.get("Origin") == STORE_ORIGIN
                    and self.headers.get("Host") == "127.0.0.1:" + str(self.server.server_port)
                    and secrets.compare_digest(self.path, path)
                    and not self.server.completed)

        def reply(self, status, value, *, cors=False):
            raw = json.dumps(value, separators=(",", ":")).encode("ascii")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            if cors:
                self.send_header("Access-Control-Allow-Origin", STORE_ORIGIN)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()
            self.wfile.write(raw)
            self.close_connection = True

        def do_OPTIONS(self):
            if not self.valid_source():
                self.reply(403, {"outcome": "handoff_rejected"})
                return
            if (self.headers.get("Access-Control-Request-Method") != "POST"
                    or self.headers.get("Access-Control-Request-Headers", "").lower() != "content-type"):
                self.reply(403, {"outcome": "handoff_rejected"})
                return
            self.reply(200, {"outcome": "handoff_ready"}, cors=True)

        def do_GET(self):
            self.reply(405, {"outcome": "handoff_post_required"})

        def do_POST(self):
            if not self.valid_source():
                self.reply(403, {"outcome": "handoff_rejected"})
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
                        or self.headers.get("Transfer-Encoding") is not None
                        or self.headers.get("Content-Type", "").lower() != "application/json"):
                    raise ValueError
                length = int(lengths[0])
                if not 0 < length <= MAX_HANDOFF_BYTES:
                    raise ValueError
                # Even an invalid/partial payload may contain credentials.
                # Do not later describe an expired handoff as unread.
                self.server.payload_read_started = True
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError
                value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
                if (type(value) is not dict or set(value) != {"selected_uid", "sauth"}
                        or type(value["selected_uid"]) is not int or value["selected_uid"] != 1370598422):
                    raise ValueError
                # Only the explicit normal-renewal mode accepts this field.
                if type(value["sauth"]) is not dict:
                    raise ValueError
                if (not allow_refresh and "refresh_token" in value["sauth"]
                        or allow_refresh and (type(value["sauth"].get("refresh_token")) is not str
                                              or not value["sauth"]["refresh_token"].strip())):
                    raise ValueError
                probe.build_web_login_inputs(value["sauth"])
            except Exception:
                self.reply(400, {"outcome": "handoff_invalid_payload"}, cors=True)
                return
            # Consume before any downstream I/O: no second attempt on error.
            self.server.completed = True
            try:
                report = attempt(value["sauth"])
            except Exception:
                report = {"outcome": "auth_probe_failed", "secrets_saved": False}
            finally:
                value.clear()
                raw = b""
            self.server.report = report
            self.reply(200, report, cors=True)

    return Handler


def expiry_report(server):
    return {"outcome": "handoff_expired",
            "credentials_read": None if server.payload_read_started else False,
            "payload_read_started": server.payload_read_started}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-once", action="store_true")
    parser.add_argument("--with-reviewed-version", action="store_true")
    parser.add_argument("--with-reviewed-pc-envelope", action="store_true")
    parser.add_argument("--runtime-version", default="3825464", choices=("3825464", "3848703"))
    parser.add_argument("--enter", action="store_true",
                        help="Also verify one normal owner gateway login, then close; no stats queries.")
    parser.add_argument("--collect-social-token", action="store_true",
                        help="Also passively receive one normal owner social token; no social requests.")
    parser.add_argument("--verify-social-session", action="store_true",
                        help="Also verify one normal fresh-token SocialMatrix setup; no player queries.")
    parser.add_argument("--lookup-uid", type=int,
                        help="Read one UID's normal mesh identity/visibility; no Career or history queries.")
    parser.add_argument("--own-career", action="store_true",
                        help="Also read the verified owner's unfiltered PC Career aggregate, not other players.")
    parser.add_argument("--other-career", action="store_true",
                        help="Read the explicit other UID's native aggregate if its overview is available.")
    parser.add_argument("--other-matches", action="store_true",
                        help="Read one first match page for the other UID if its history is available.")
    parser.add_argument("--refresh-before-auth", action="store_true",
                        help="Use one normal OAuth refresh, then verify the renewed owner session.")
    args = parser.parse_args(argv)
    if args.other_matches and (args.own_career or args.other_career):
        parser.error("select only one Career or matches section per research read")
    if (args.other_career or args.other_matches) and (args.own_career or args.lookup_uid is None
                             or not 0 < args.lookup_uid < 2**63 or args.lookup_uid == 1370598422):
        parser.error("native other-player sections require --lookup-uid with an explicit other-player UID")
    if args.own_career:
        if args.lookup_uid not in (None, 1370598422):
            parser.error("--own-career only supports the authenticated owner UID")
        args.lookup_uid = 1370598422
    if args.lookup_uid is not None and not 0 < args.lookup_uid < 2**64:
        parser.error("lookup UID must be a positive uint64")
    if (args.enter or args.collect_social_token or args.refresh_before_auth or args.verify_social_session
            or args.lookup_uid is not None) and (
            args.runtime_version != "3848703" or not args.with_reviewed_pc_envelope):
        parser.error("gateway actions require --runtime-version 3848703 --with-reviewed-pc-envelope")
    if not args.serve_once:
        print(json.dumps({"outcome": "preview", "network_attempted": False,
                          "credentials_read": False, "files_read": False,
                          "with_reviewed_version": args.with_reviewed_version or args.with_reviewed_pc_envelope,
                          "with_reviewed_pc_envelope": args.with_reviewed_pc_envelope,
                          "enter_gateway": args.enter or args.collect_social_token or args.verify_social_session or args.lookup_uid is not None,
                          "collect_social_token": args.collect_social_token or args.verify_social_session or args.lookup_uid is not None,
                          "verify_social_session": args.verify_social_session or args.lookup_uid is not None,
                          "lookup_uid": args.lookup_uid,
                          "own_career": args.own_career,
                          "other_career": args.other_career,
                          "other_matches": args.other_matches,
                          "refresh_before_auth": args.refresh_before_auth,
                          "runtime_version": args.runtime_version}))
        return 0
    path = "/auth-once/" + secrets.token_urlsafe(32)
    def attempt(sauth):
        lookup_options = {"lookup_uid": args.lookup_uid} if args.lookup_uid is not None else {}
        if args.own_career:
            lookup_options["own_career"] = True
        if args.other_career:
            lookup_options["other_career"] = True
        if args.other_matches:
            lookup_options["other_matches"] = True
        return attempt_auth(sauth, with_reviewed_version=args.with_reviewed_version,
                            with_reviewed_pc_envelope=args.with_reviewed_pc_envelope,
                            runtime_version=args.runtime_version, enter_gateway=args.enter,
                            collect_social_token=args.collect_social_token,
                            verify_social_session=args.verify_social_session,
                            refresh_before_auth=args.refresh_before_auth, **lookup_options)

    with HTTPServer(("127.0.0.1", 0), make_handler(path, attempt=attempt,
                                               allow_refresh=args.refresh_before_auth)) as server:
        server.completed, server.report, server.timeout = False, None, 1
        server.payload_read_started = False
        print(json.dumps({"outcome": "awaiting_one_owner_session",
                          "url": "http://127.0.0.1:" + str(server.server_port) + path,
                          "with_reviewed_version": args.with_reviewed_version or args.with_reviewed_pc_envelope,
                          "with_reviewed_pc_envelope": args.with_reviewed_pc_envelope,
                          "enter_gateway": args.enter or args.collect_social_token or args.verify_social_session or args.lookup_uid is not None,
                          "collect_social_token": args.collect_social_token or args.verify_social_session or args.lookup_uid is not None,
                          "verify_social_session": args.verify_social_session or args.lookup_uid is not None,
                          "lookup_uid": args.lookup_uid,
                          "own_career": args.own_career,
                          "other_career": args.other_career,
                          "other_matches": args.other_matches,
                          "refresh_before_auth": args.refresh_before_auth,
                          "runtime_version": args.runtime_version,
                          "expires_in_seconds": WAIT_SECONDS}), flush=True)
        deadline = time.monotonic() + WAIT_SECONDS
        while not server.completed and time.monotonic() < deadline:
            server.handle_request()
        print(json.dumps(server.report or expiry_report(server)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
