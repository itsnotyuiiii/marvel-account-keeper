#!/usr/bin/env python3
"""Private native NetEase service, deployed on an independent TCP-capable host.

No credential discovery: the encryption key/API token are supplied by the host
secret manager; bootstrap consumes one explicitly supplied JSON document on stdin.
There are no HTTP login/configuration/credential endpoints. Default operation is
loopback-only behind that host's HTTPS proxy, not a laptop bridge.
"""
import argparse
import base64
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import sys
import signal
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from standalone_native_runtime import NativeProfileQuery
from standalone_session_store import EncryptedSqliteSessionStore


def _key(value):
    if type(value) is not str:
        raise ValueError()
    key = base64.b64decode(value, altchars=b"-_", validate=True)
    if len(key) != 32:
        raise ValueError()
    return key


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


@contextmanager
def _termination_requested():
    """Normal host termination stops admission without interrupting rotation.

    Keep these non-throwing handlers installed through the ExitStack drain.
    Forced termination/power loss still requires the durable recovery rules.
    """
    stopped = threading.Event()
    previous = {}
    try:
        for kind in (signal.SIGINT, signal.SIGTERM):
            previous[kind] = signal.signal(kind, lambda *_args: stopped.set())
        yield stopped
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate packaged runtime inputs only; no keys, persistence or network.")
    parser.add_argument("--database", help="Explicit encrypted session SQLite path; parent directory must already exist.")
    parser.add_argument("--port", type=int, default=8080, help="Loopback port behind the independent host's HTTPS proxy (default8080).")
    parser.add_argument("--initialize-from-stdin", action="store_true", help="Persist one explicitly authorized SDK bootstrap bundle from stdin, then exit.")
    parser.add_argument("--recover-pending", action="store_true", help="Under exclusive ownership, reverify a persisted candidate without replaying SDK rotation.")
    args = parser.parse_args(argv)
    if args.check:
        if args.initialize_from_stdin or args.recover_pending or args.database is not None:
            parser.error("--check cannot access session state")
        try:
            NativeProfileQuery()
            print(json.dumps({"outcome": "native_public_inputs_verified", "reviewed_build": "3848703", "network_attempted": False, "credentials_read": False}))
            return 0
        except Exception:
            print(json.dumps({"outcome": "native_public_inputs_unavailable"}))
            return 1
    if not args.database or not 1 <= args.port <= 65535 or args.initialize_from_stdin and args.recover_pending:
        parser.error("Choose an explicit database and one valid operation")
    # Importing does not read credentials or start networking.
    from standalone_session_manager import OwnerSessionManager
    from standalone_http_api import ProfileLookupService, make_http_server
    try:
        key = _key(os.environ.get("NETEASE_SESSION_KEY"))
        token = os.environ.get("NETEASE_API_TOKEN")
        if not args.initialize_from_stdin and (type(token) is not str or not 32 <= len(token) <= 4096 or not token.isascii() or any(ord(char) <= 32 or ord(char) >= 127 for char in token)):
            raise ValueError()
        runtime = NativeProfileQuery()
    except Exception:
        print(json.dumps({"outcome": "native_service_configuration_invalid"}))
        return 1
    try:
        with _termination_requested() as stopped, ExitStack() as stack:
            store = EncryptedSqliteSessionStore(args.database, key)
            stack.enter_context(store.exclusive_owner())
            manager = OwnerSessionManager(store, query=runtime)
            if args.initialize_from_stdin:
                raw = sys.stdin.buffer.read(256 * 1024 + 1)
                if not 0 < len(raw) <= 256 * 1024:
                    raise ValueError()
                bundle = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
                result = manager.bootstrap(bundle)
                # Only a fixed classification is emitted; never the record.
                print(json.dumps({"outcome": result.outcome}))
                return 0 if result.outcome == "session_bootstrapped" else 1
            if args.recover_pending:
                result = manager.recover_pending(exclusive_ownership=True)
                print(json.dumps({"outcome": result.outcome, "owner_authenticated": result.owner_authenticated}))
                return 0 if result.owner_authenticated else 1
            service = ProfileLookupService(manager)
            stack.callback(service.close)
            server = make_http_server(service, bearer_token=token, host="127.0.0.1", port=args.port)
            stack.callback(server.server_close)
            print(json.dumps({"outcome": "native_http_listening", "host": "127.0.0.1", "port": server.server_port, "tls_proxy_required": True}), flush=True)
            server.timeout = 0.25
            while not stopped.is_set():
                server.handle_request()
        return 0
    except Exception:
        print(json.dumps({"outcome": "native_service_unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
