"""Private bounded HTTP adapter around the owner-scoped profile manager.

One worker owns all lookup/rotation work. HTTP timeouts and disconnects never
cancel it. Only a strict projected JSON snapshot, not manager/exception data,
may enter the short-lived cache or an HTTP response. No bootstrap/admin API,
credential loader, CORS, public listener default, or sensitive access log.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import re
import socket
from socketserver import ThreadingMixIn
import threading
import time

from standalone_session_manager import SessionLookupResult


MAX_PENDING = 4
MAX_CACHE = 100
CACHE_SECONDS = 30
MAX_WAIT_SECONDS = 15
MAX_RESPONSE_BYTES = 65536
MAX_HEADER_BYTES = 16 * 1024
RETRY_SECONDS = 2
_UID_PATH = re.compile(r"/v1/players/([0-9]{1,19})\Z")
_SESSION_UNAVAILABLE = frozenset({
    "session_unconfigured", "session_candidate_pending", "session_verification_in_flight",
    "session_rotation_unknown", "session_store_unavailable", "session_store_record_invalid",
    "session_store_write_failed", "session_exclusive_ownership_required", "session_no_pending_candidate",
    "session_access_rejected", "session_recovery_required",
})


def _project(profile, uid, fetched_at):
    # Keep construction/imports usable in an isolated synthetic test. The
    # production default is the separate strict, allowlisted web contract.
    from standalone_web_contract import project_profile_envelope
    return project_profile_envelope(profile, uid, fetched_at)


def _utcnow():
    return datetime.now(timezone.utc)


def _valid_uid(uid):
    return type(uid) is int and 1 <= uid <= 2**63 - 1


def _encoded(value):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("projected_response_bound")
    return raw


@dataclass(frozen=True)
class LookupResponse:
    status_code: int
    body: dict = field(repr=False)
    retry_after: int | None = None


@dataclass(frozen=True)
class _Completed:
    status_code: int
    payload: bytes = field(repr=False)
    retry_after: int | None = None

    def response(self):
        # Every caller receives its own plain JSON copy, not the cache object.
        return LookupResponse(self.status_code, json.loads(self.payload), self.retry_after)


def _error(code, status=503, *, retry=False):
    return _Completed(status, _encoded({"error": {"code": code}}), RETRY_SECONDS if retry else None)


class ProfileLookupService:
    """Serialized singleflight lookups; the manager alone owns credentials.

    clock returns an aware datetime for projected fetchedAt; monotonic controls
    cache expiry. manager.lookup(uid) returns owner_authenticated/profile fields.
    Arbitrary manager outcome text, status dictionaries and errors are ignored.
    """

    def __init__(self, manager, *, projector=_project, clock=_utcnow, monotonic=time.monotonic):
        if (not callable(getattr(manager, "lookup", None)) or not callable(projector)
                or not callable(clock) or not callable(monotonic)):
            raise ValueError("lookup_service_inputs_invalid")
        self._manager, self._projector, self._clock, self._monotonic = manager, projector, clock, monotonic
        self._lock = threading.Lock()
        self._pending = {}
        self._cache = OrderedDict()
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="owner-profile")

    @property
    def ready(self):
        with self._lock:
            return not self._closed

    def _now(self):
        value = self._monotonic()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("lookup_clock_invalid")
        return value

    def _work(self, uid):
        try:
            result = self._manager.lookup(uid)
            if type(result) is not SessionLookupResult:
                return _error("lookup_failed", 502)
            if type(result.outcome) is str and result.outcome in _SESSION_UNAVAILABLE:
                return _error("lookup_unavailable")
            if (type(result.outcome) is not str or result.outcome != "session_lookup_complete" or result.owner_authenticated is not True
                    or type(result.profile) is not dict):
                return _error("lookup_failed", 502)
            fetched_at = self._clock()
            if not isinstance(fetched_at, datetime) or fetched_at.utcoffset() is None:
                return _error("lookup_failed", 502)
            envelope = self._projector(result.profile, uid, fetched_at)
            if (type(envelope) is not dict or type(envelope.get("schemaVersion")) is not int
                    or envelope["schemaVersion"] != 2 or envelope.get("uid") != str(uid)
                    or envelope.get("source") != "netease"):
                return _error("lookup_failed", 502)
            # Serialization drops aliases and rejects NaN/cycles/unknown objects;
            # field selection belongs exclusively to the trusted projector.
            return _Completed(200, _encoded(envelope))
        except Exception:
            return _error("lookup_failed", 502)

    def _complete(self, uid, future):
        # The worker catches external failures; still never propagate callback
        # exception text (Future callback logging could otherwise expose it).
        try:
            result = future.result()
        except BaseException:
            result = _error("lookup_failed", 502)
        with self._lock:
            if self._pending.get(uid) is not future:
                return
            del self._pending[uid]
            if not self._closed and type(result) is _Completed and result.status_code == 200:
                try:
                    expires = self._now() + CACHE_SECONDS
                except Exception:
                    return
                self._cache[uid] = (expires, result)
                self._cache.move_to_end(uid)
                while len(self._cache) > MAX_CACHE:
                    self._cache.popitem(last=False)

    def lookup(self, uid, *, wait_seconds=MAX_WAIT_SECONDS):
        if not _valid_uid(uid):
            return _error("invalid_uid", 400).response()
        if (type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= MAX_WAIT_SECONDS
                or not math.isfinite(wait_seconds)):
            raise ValueError("lookup_wait_invalid")
        created = False
        with self._lock:
            if self._closed:
                return _error("service_unavailable").response()
            try:
                now = self._now()
            except Exception:
                return _error("lookup_unavailable").response()
            for key in [key for key, (expires, _) in self._cache.items() if expires <= now]:
                del self._cache[key]
            cached = self._cache.get(uid)
            if cached is not None:
                self._cache.move_to_end(uid)
                return cached[1].response()
            future = self._pending.get(uid)
            if future is None:
                if len(self._pending) >= MAX_PENDING:
                    return _error("lookup_busy", retry=True).response()
                future = self._executor.submit(self._work, uid)
                self._pending[uid] = future
                created = True
        if created:
            # Register outside the lock: an already-completed future invokes
            # callbacks synchronously, which must not deadlock this caller.
            future.add_done_callback(lambda completed: self._complete(uid, completed))
        try:
            return future.result(timeout=wait_seconds).response()
        except FutureTimeout:
            return _error("lookup_pending", retry=True).response()
        except BaseException:
            # No cancellation: the sole worker can finish rotation/persistence.
            return _error("lookup_failed", 502).response()

    def close(self, *, wait=True):
        with self._lock:
            self._closed = True
            self._cache.clear()
        self._executor.shutdown(wait=wait, cancel_futures=False)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(self.server.read_timeout)

    def log_message(self, *_args):
        pass

    def _reply(self, result):
        self.close_connection = True
        try:
            raw = _encoded(result.body)
            self.send_response_only(result.status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            if result.retry_after is not None:
                self.send_header("Retry-After", str(result.retry_after))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)
        except (OSError, ValueError):
            # Client departure does not touch the worker or its pending future.
            pass

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler otherwise reflects request/exception strings.
        self._reply(_error("invalid_request", 400).response())

    def _request_allowed(self):
        headers = list(self.headers.items())
        lengths = self.headers.get_all("Content-Length", [])
        hosts = self.headers.get_all("Host", [])
        if (len(headers) > 32 or sum(len(k) + len(v) for k, v in headers) > MAX_HEADER_BYTES
                or self.headers.get_all("Transfer-Encoding") is not None
                or len(lengths) > 1 or lengths and lengths != ["0"]
                or len(hosts) > 1 or self.request_version == "HTTP/1.1" and len(hosts) != 1
                or len(self.headers.get_all("Authorization", [])) > 1):
            self._reply(_error("invalid_request", 400).response())
            return False
        auth = self.headers.get_all("Authorization", [])
        supplied = auth[0] if len(auth) == 1 else ""
        # Hash to fixed-size bytes before constant-time comparison. Only this
        # server holds the expected digest; no auth value enters public output.
        candidate = supplied[7:] if supplied.startswith("Bearer ") else ""
        accepted = hmac.compare_digest(hashlib.sha256(candidate.encode("utf-8")).digest(), self.server.bearer_digest)
        if not accepted:
            self._reply(_error("unauthorized", 401).response())
            return False
        return True

    def do_GET(self):
        if not self._request_allowed():
            return
        if self.path == "/health":
            ready = self.server.service.ready
            self._reply(LookupResponse(200 if ready else 503, {"status": "ready" if ready else "unavailable"}))
            return
        match = _UID_PATH.fullmatch(self.path)
        if match is None:
            code = "invalid_uid" if self.path.startswith("/v1/players/") else "not_found"
            self._reply(_error(code, 400 if code == "invalid_uid" else 404).response())
            return
        self._reply(self.server.service.lookup(int(match[1]), wait_seconds=MAX_WAIT_SECONDS))

    def _unsupported(self):
        if self._request_allowed():
            self._reply(_error("method_not_allowed", 405).response())

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _unsupported

    def handle_expect_100(self):
        self._reply(_error("invalid_request", 400).response())
        return False


class _PrivateHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def process_request(self, request, client_address):
        if not self.handler_slots.acquire(blocking=False):
            try:
                # Brief bounded admission response, not another handler. Drain
                # only the small request-header prefix after a write shutdown
                # so closing unread GET bytes does not reset away our 503.
                deadline = time.monotonic() + min(self.read_timeout, 0.05)
                request.settimeout(min(self.read_timeout, 0.05))
                raw = _encoded({"error": {"code": "lookup_busy"}})
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n"
                    b"Content-Type: application/json\r\nRetry-After: 2\r\nContent-Length: "
                    + str(len(raw)).encode("ascii") + b"\r\n\r\n" + raw)
                request.shutdown(socket.SHUT_WR)
                prefix = bytearray()
                while len(prefix) < MAX_HEADER_BYTES and b"\r\n\r\n" not in prefix:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    request.settimeout(remaining)
                    chunk = request.recv(min(4096, MAX_HEADER_BYTES - len(prefix)))
                    if not chunk:
                        break
                    prefix.extend(chunk)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.handler_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.handler_slots.release()

    def handle_error(self, request, client_address):
        # Never print request headers, manager exception text or tracebacks.
        pass


def make_http_server(service, *, bearer_token, host="127.0.0.1", port=0, max_handlers=8, read_timeout=5.0):
    """Construct a private HTTP listener; caller runs serve_forever/shutdown.

    The independent host terminates HTTPS at its reverse proxy. Closing this
    listener never closes/cancels the service; call service.close(wait=True)
    separately to drain any pending credential rotation before process exit.
    """
    if (type(service) is not ProfileLookupService or type(bearer_token) is not str
            or not 1 <= len(bearer_token) <= 4096 or any(not 33 <= ord(c) <= 126 for c in bearer_token)
            or type(host) is not str or not host or type(port) is not int or not 0 <= port <= 65535
            or type(max_handlers) is not int or not 1 <= max_handlers <= 32
            or type(read_timeout) not in (int, float) or not 0 < read_timeout <= 15 or not math.isfinite(read_timeout)):
        raise ValueError("http_server_inputs_invalid")
    server = _PrivateHTTPServer((host, port), _Handler, bind_and_activate=False)
    server.service = service
    server.bearer_digest = hashlib.sha256(bearer_token.encode("ascii")).digest()
    server.handler_slots = threading.BoundedSemaphore(max_handlers)
    server.read_timeout = read_timeout
    try:
        server.server_bind()
        server.server_activate()
    except BaseException:
        server.server_close()
        raise
    return server
