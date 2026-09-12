"""One-use, loopback-only handoff for an owner-authorized SDK AUTH experiment.

Preview by default. --serve-once accepts one bounded in-memory SDK bundle from
the official store origin, sends one normal NetEase AUTH request, verifies the
expected existing own UID, and exits. Explicit --enter additionally verifies
one normal gateway login for that same owner and closes. No role creation, stats,
refresh, game files, SDK logs, captures, credential files or production changes.
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
                 runtime_version="3825464", enter_gateway=False):
    # Validate before consulting any file or opening a network connection.
    probe.build_web_login_inputs(sauth)
    provenance, pin = runtime.reviewed_runtime_pin(runtime_version)
    if type(enter_gateway) is not bool or (enter_gateway and (
            runtime_version != "3848703" or not with_reviewed_pc_envelope)):
        raise ValueError("gateway_requires_current_reviewed_pc_envelope")
    public = runtime.load_runtime_manifest(
        Path(__file__).resolve().parents[1] / "runtime-inputs" / (runtime_version + ".json"),
        expected_sha256=pin, expected_provenance=provenance)
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
    if enter_gateway:
        return probe.run_gateway_once(
            sauth, public.configuration, server,
            client_version=public.provenance.client_version, os_name="pc")
    return probe.run_auth_once(
        sauth, public.configuration, server,
        client_version=public.provenance.client_version
        if with_reviewed_version or with_reviewed_pc_envelope else None,
        os_name="pc" if with_reviewed_pc_envelope else None)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def make_handler(path, attempt=attempt_auth):
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
                # Refresh tokens stay in the browser for either bounded test.
                if type(value["sauth"]) is not dict or "refresh_token" in value["sauth"]:
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
    args = parser.parse_args(argv)
    if args.enter and (args.runtime_version != "3848703" or not args.with_reviewed_pc_envelope):
        parser.error("--enter requires --runtime-version 3848703 --with-reviewed-pc-envelope")
    if not args.serve_once:
        print(json.dumps({"outcome": "preview", "network_attempted": False,
                          "credentials_read": False, "files_read": False,
                          "with_reviewed_version": args.with_reviewed_version or args.with_reviewed_pc_envelope,
                          "with_reviewed_pc_envelope": args.with_reviewed_pc_envelope,
                          "enter_gateway": args.enter,
                          "runtime_version": args.runtime_version}))
        return 0
    path = "/auth-once/" + secrets.token_urlsafe(32)
    def attempt(sauth):
        return attempt_auth(sauth, with_reviewed_version=args.with_reviewed_version,
                            with_reviewed_pc_envelope=args.with_reviewed_pc_envelope,
                            runtime_version=args.runtime_version, enter_gateway=args.enter)

    with HTTPServer(("127.0.0.1", 0), make_handler(path, attempt=attempt)) as server:
        server.completed, server.report, server.timeout = False, None, 1
        server.payload_read_started = False
        print(json.dumps({"outcome": "awaiting_one_owner_session",
                          "url": "http://127.0.0.1:" + str(server.server_port) + path,
                          "with_reviewed_version": args.with_reviewed_version or args.with_reviewed_pc_envelope,
                          "with_reviewed_pc_envelope": args.with_reviewed_pc_envelope,
                          "enter_gateway": args.enter,
                          "runtime_version": args.runtime_version,
                          "expires_in_seconds": WAIT_SECONDS}), flush=True)
        deadline = time.monotonic() + WAIT_SECONDS
        while not server.completed and time.monotonic() < deadline:
            server.handle_request()
        print(json.dumps(server.report or expiry_report(server)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
