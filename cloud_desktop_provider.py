"""Private desktop adapter for the hosted, NetEase-only profile service.

The executable contains no service credential. A separate Windows-user DPAPI
file holds only the service origin and bearer, not AWS or NetEase credentials.
The vault must be unlocked before that file is decrypted or a lookup is sent.
"""

from collections import OrderedDict
import copy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid

from career_provider import CareerUnavailable
from standalone_web_contract import project_profile_envelope


DEFAULT_ORIGIN = "https://khvig9ub7b.execute-api.us-east-1.amazonaws.com"
CLOUD_CONFIG_FILE = "cloud-connection.dpapi"
MAX_RESPONSE_BYTES = 65536
CACHE_SECONDS = 30
MAX_CACHE = 100
LOOKUP_SECONDS = 90
MAX_ATTEMPTS = 40
_ENTROPY = b"MarvelAccountKeeper::cloud-connection::v1"
_UID = re.compile(r"[1-9][0-9]{5,10}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]{24,4096}={0,2}\Z")
_LOOKUP = re.compile(r"[0-9a-f]{32}\Z")
_MESSAGES = {
    "missing_identity": ("Enter a Marvel Rivals UID to look up this player.", 400),
    "career_uid_required": ("Enter the player's numeric UID. Name-only lookup is not available; no third-party search was made.", 400),
    "invalid_rivals_uid": ("Enter a numeric Marvel Rivals UID of 6 to 11 digits, without leading zeros.", 400),
    "career_platform_unavailable": ("The hosted NetEase connection currently supports PC players. Choose PC or Unknown.", 400),
    "locked": ("Unlock Marvel Tracker before refreshing players.", 401),
    "cloud_not_configured": ("This Windows user does not have the private cloud connection configured. Existing cards were kept.", 409),
    "cloud_config_invalid": ("The private cloud connection could not be opened for this Windows user. Existing cards and connection settings were kept.", 409),
    "career_closed": ("The cloud connection is closed. Reopen Marvel Tracker before refreshing.", 409),
    "career_busy": ("A cloud lookup is already running. Wait for it to finish before refreshing again.", 429),
    "cloud_access_rejected": ("The cloud service rejected its access credential. This does not mean the player's UID is invalid.", 503),
    "cloud_profile_not_returned": ("NetEase returned no profile for this UID. This does not establish that the UID is invalid. Saved card values were kept.", 404),
    "cloud_rate_limited": ("The cloud service is temporarily busy or rate-limited. Wait briefly and refresh again; saved card values were kept.", 429),
    "cloud_lookup_expired": ("The cloud lookup expired before a result was returned. Refresh again; saved card values were kept.", 503),
    "cloud_lookup_pending": ("The cloud lookup is still running. Wait briefly and refresh again to continue it; saved card values were kept.", 503),
    "cloud_lookup_unavailable": ("The cloud NetEase connection could not complete the lookup. This does not mean the player's UID is invalid. Saved card values were kept.", 503),
    "cloud_response_invalid": ("The cloud response could not be verified for this UID. Saved card values were kept.", 502),
    "cloud_request_failed": ("The cloud service could not be reached. Check your connection and retry; saved card values were kept.", 503),
    "cloud_managed_session": ("NetEase sign-in and session recovery are managed by the hosted backend. Refresh a UID card to check it.", 409),
}


def _error(code):
    message, status = _MESSAGES[code]
    return CareerUnavailable(code, message, status)


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError()
            value[key] = item
        return value

    def constant(_):
        raise ValueError()

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _config(value):
    if (type(value) is not dict or value.get("version") != 1 or type(value.get("version")) is not int
            or value.get("origin") not in (DEFAULT_ORIGIN, DEFAULT_ORIGIN + "/")
            or type(value.get("token")) is not str or _TOKEN.fullmatch(value["token"]) is None
            or set(value) != {"version", "origin", "token"}):
        raise ValueError()
    return {"version": 1, "origin": DEFAULT_ORIGIN, "token": value["token"]}


def _dpapi(data, *, decrypt=False):
    """User-scoped DPAPI, no interactive prompts and no plaintext fallback."""
    if sys.platform != "win32" or type(data) is not bytes or not data:
        raise ValueError()
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    incoming = ctypes.create_string_buffer(data)
    entropy = ctypes.create_string_buffer(_ENTROPY)
    source = Blob(len(data), ctypes.cast(incoming, ctypes.c_void_p))
    extra = Blob(len(_ENTROPY), ctypes.cast(entropy, ctypes.c_void_p))
    output = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    call = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                     ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    call.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    try:
        # CRYPTPROTECT_UI_FORBIDDEN; intentionally no LOCAL_MACHINE flag.
        if not call(ctypes.byref(source), None, ctypes.byref(extra), None, None, 1, ctypes.byref(output)):
            raise ValueError()
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.memset(incoming, 0, len(data))
        if output.pbData:
            ctypes.memset(output.pbData, 0, output.cbData)
            kernel32.LocalFree(output.pbData)


def provision_cloud_config(origin, token, path, *, protect=None):
    """Write a new private connection file; never overwrite an existing file.

    Only an operator provisioning this private desktop needs the service token.
    The desktop itself does not need AWS CLI, AWS keys, or administrator rights.
    All errors are fixed messages and never include supplied credential values.
    """
    temporary = None
    try:
        value = _config({"version": 1, "origin": origin, "token": token})
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        encrypted = (protect or _dpapi)(raw)
        if type(encrypted) is not bytes or not 1 <= len(encrypted) <= 16384:
            raise ValueError()
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError()
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        with temporary.open("xb") as handle:
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows rename refuses an existing destination, preserving its config.
        # link provides the same no-overwrite guarantee on other test platforms.
        if os.name == "nt":
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
            temporary.unlink()
        temporary = None
        return destination
    except Exception:
        raise _error("cloud_config_invalid") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _account_uid(account):
    if type(account) is not dict:
        raise _error("invalid_rivals_uid")
    platform = account.get("rivals_platform")
    if platform is not None and (type(platform) is not str or platform not in {"", "pc", "unknown"}):
        raise _error("career_platform_unavailable")
    uid = account.get("rivals_uid")
    if uid is None or type(uid) is str and not uid:
        name = account.get("in_game_name")
        raise _error("career_uid_required" if type(name) is str and name.strip() else "missing_identity")
    if type(uid) is int:
        if not 100000 <= uid <= 99999999999:
            raise _error("invalid_rivals_uid")
        uid = str(uid)
    if type(uid) is not str or not _UID.fullmatch(uid):
        raise _error("invalid_rivals_uid")
    return uid


def _http_get(url, headers, timeout, max_bytes):
    """No redirects, environment proxies, cookies, netrc, or persistent bearer."""
    import requests
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, headers=headers, timeout=(min(5.0, timeout), timeout),
                         allow_redirects=False, stream=True) as response:
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                raw.extend(chunk)
                if len(raw) > max_bytes:
                    raise ValueError()
            return response.status_code, dict(response.headers), bytes(raw)


def _project(envelope, uid, now):
    if (type(envelope) is not dict or type(envelope.get("schemaVersion")) is not int
            or envelope["schemaVersion"] != 2 or envelope.get("source") != "netease"
            or envelope.get("uid") != uid or type(envelope.get("fetchedAt")) is not str
            or len(envelope["fetchedAt"]) > 64):
        raise ValueError()
    stamp = datetime.fromisoformat(envelope["fetchedAt"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError()
    timestamp = stamp.timestamp()
    if not math.isfinite(timestamp) or not 0 < timestamp <= now + 300 or timestamp < now - 86400:
        raise ValueError()
    projected = project_profile_envelope(envelope.get("nativeProfile"), int(uid), stamp)["nativeProfile"]
    return {"uid": uid, "platform": "pc", "in_game_name": projected["identity"]["in_game_name"],
            "fetched_at": int(timestamp), "native_profile": projected}


class CloudDesktopConnection:
    """Serialized bounded lookups; only strictly projected player data is cached."""

    def __init__(self, data_dir, get_key, *, transport=None, unprotect=None,
                 clock=time.time, monotonic=time.monotonic, wait=None):
        self._path = Path(data_dir) / CLOUD_CONFIG_FILE
        self._get_key = get_key
        self._transport = transport or _http_get
        self._unprotect = unprotect or (lambda blob: _dpapi(blob, decrypt=True))
        self._clock, self._monotonic, self._wait = clock, monotonic, wait
        self._guard = threading.RLock()
        self._work = threading.Lock()
        self._cancel = threading.Event()
        self._generation = 0
        self._closed = self._suspending = self._busy = False
        self._credentials = None
        self._upstream_verified = False
        self._outcome = None
        self._cache = OrderedDict()
        self._pending = {}

    def __repr__(self):
        return "<CloudDesktopConnection>"

    def _unlocked(self):
        try:
            key = self._get_key()
            return type(key) is bytes and len(key) == 32
        except Exception:
            return False

    def _load(self):
        with self._guard:
            if self._closed:
                raise _error("career_closed")
            if self._suspending or not self._unlocked():
                raise _error("locked")
            if self._credentials is not None:
                return self._credentials
            try:
                with self._path.open("rb") as handle:
                    encrypted = handle.read(16385)
                if not 1 <= len(encrypted) <= 16384:
                    raise ValueError()
                raw = self._unprotect(encrypted)
                if type(raw) is not bytes or not 1 <= len(raw) <= 8192:
                    raise ValueError()
                self._credentials = _config(_json(raw.decode("utf-8")))
            except FileNotFoundError:
                raise _error("cloud_not_configured") from None
            except Exception:
                raise _error("cloud_config_invalid") from None
            return self._credentials

    def status(self):
        outcome = None
        try:
            self._load()
        except CareerUnavailable as error:
            outcome = error.code
        with self._guard:
            configured = self._path.is_file()
            ready = self._credentials is not None and self._unlocked() and not (self._closed or self._suspending)
            state = ("closed" if self._closed else "unconfigured" if not configured else
                     "locked" if not self._unlocked() else "ready" if ready else "unavailable")
            if ready:
                message = ("Cloud access is ready. NetEase was verified on the last successful lookup. Refresh a PC UID card." if self._upstream_verified else
                           "Cloud access is configured. Refresh a PC UID card to verify the hosted NetEase connection.")
            else:
                message = _MESSAGES.get(outcome or "cloud_not_configured", _MESSAGES["cloud_config_invalid"])[0]
            return {"provider": "cloud", "configured": configured, "connected": ready,
                    "state": state, "busy": self._busy, "closed": self._closed,
                    "upstream_verified": self._upstream_verified and ready,
                    "outcome": outcome or self._outcome, "can_connect": False,
                    "connection_in_progress": False, "persistence": "windows_user_encrypted",
                    "message": message}

    def can_refresh_account(self, account):
        try:
            _account_uid(account)
            self._load()
            return True
        except CareerUnavailable:
            return False

    def cached_account(self, account):
        try:
            uid = _account_uid(account)
        except CareerUnavailable:
            return None
        with self._guard:
            if self._closed or self._suspending or not self._unlocked():
                return None
            return copy.deepcopy(self._cache.get(uid))

    def _active(self, generation):
        with self._guard:
            if self._closed:
                raise _error("career_closed")
            if self._suspending or generation != self._generation or self._cancel.is_set() or not self._unlocked():
                raise _error("locked")

    def _pause(self, headers, generation, deadline):
        retry = next((value for key, value in headers.items() if str(key).lower() == "retry-after"), "2")
        try:
            delay = float(retry)
        except (ValueError, TypeError):
            delay = 2.0
        if not math.isfinite(delay) or delay < 0:
            delay = 2.0
        delay = max(1.0, delay)
        if delay > 30 or self._monotonic() + delay >= deadline:
            return False
        if self._wait is None:
            self._cancel.wait(delay)
        else:
            self._wait(delay)
        self._active(generation)
        return True

    def _lookup(self, uid, credentials, generation):
        deadline = self._monotonic() + LOOKUP_SECONDS
        with self._guard:
            lookup_id = self._pending.get(uid)
        last_retry = "cloud_lookup_pending" if lookup_id else "cloud_rate_limited"
        for _ in range(MAX_ATTEMPTS):
            self._active(generation)
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise _error(last_retry)
            url = credentials["origin"] + "/v1/players/" + uid
            if lookup_id:
                url += "?lookup=" + lookup_id
            try:
                status, headers, raw = self._transport(url,
                    {"Authorization": "Bearer " + credentials["token"], "Accept": "application/json"},
                    min(15.0, remaining), MAX_RESPONSE_BYTES)
            except Exception:
                raise _error("cloud_request_failed") from None
            self._active(generation)
            if type(status) is not int or type(headers) is not dict or type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES:
                raise _error("cloud_response_invalid")
            if status in (401, 403):
                raise _error("cloud_access_rejected")
            # Redirects must never be followed, including same-origin redirects.
            if 300 <= status < 400:
                raise _error("cloud_response_invalid")
            try:
                body = _json(raw.decode("utf-8"))
                if type(body) is not dict:
                    raise ValueError()
            except Exception:
                raise _error("cloud_response_invalid") from None
            if status == 200:
                try:
                    result = _project(body, uid, self._clock())
                except Exception:
                    raise _error("cloud_response_invalid") from None
                with self._guard:
                    self._pending.pop(uid, None)
                return result
            error = body.get("error")
            code = error.get("code") if type(error) is dict else None
            if status == 404 and code == "profile_not_returned" and body.get("uid") == uid:
                with self._guard:
                    self._pending.pop(uid, None)
                raise _error("cloud_profile_not_returned")
            if status == 410 and code == "lookup_expired":
                with self._guard:
                    self._pending.pop(uid, None)
                raise _error("cloud_lookup_expired")
            if status == 503 and code == "lookup_pending":
                next_id = body.get("lookupId")
                if (type(next_id) is not str or not _LOOKUP.fullmatch(next_id)
                        or lookup_id is not None and next_id != lookup_id):
                    raise _error("cloud_response_invalid")
                lookup_id = next_id
                with self._guard:
                    self._active(generation)
                    self._pending[uid] = lookup_id
                last_retry = "cloud_lookup_pending"
            elif status == 429 or status == 503 and code == "lookup_busy":
                last_retry = "cloud_rate_limited"
            elif status in (502, 503):
                with self._guard:
                    self._pending.pop(uid, None)
                raise _error("cloud_lookup_unavailable")
            else:
                raise _error("cloud_response_invalid")
            if not self._pause(headers, generation, deadline):
                raise _error(last_retry)
        raise _error(last_retry)

    def refresh_account(self, account):
        uid = _account_uid(account)
        if not self._work.acquire(blocking=False):
            raise _error("career_busy")
        try:
            credentials = self._load()
            with self._guard:
                self._busy = True
                generation = self._generation
                cached = self._cache.get(uid)
                if cached is not None and 0 <= self._clock() - cached["fetched_at"] < CACHE_SECONDS:
                    return copy.deepcopy(cached)
            result = self._lookup(uid, credentials, generation)
            with self._guard:
                self._active(generation)
                self._cache[uid] = copy.deepcopy(result)
                self._cache.move_to_end(uid)
                while len(self._cache) > MAX_CACHE:
                    self._cache.popitem(last=False)
                self._upstream_verified = True
                self._outcome = "cloud_lookup_succeeded"
            return result
        except CareerUnavailable as error:
            with self._guard:
                self._cache.pop(uid, None)
                self._outcome = error.code
                self._upstream_verified = False
            raise
        except Exception:
            with self._guard:
                self._cache.pop(uid, None)
                self._upstream_verified = False
                self._outcome = "cloud_request_failed"
            raise _error("cloud_request_failed") from None
        finally:
            with self._guard:
                self._busy = False
            self._work.release()

    def refresh(self, season=None):
        raise _error("career_uid_required")

    def connect(self, *, persist=False):
        raise _error("cloud_managed_session")

    def recover(self):
        raise _error("cloud_managed_session")

    def enable_browser(self, create_window):
        return None

    def clear(self):
        with self._guard:
            self._generation += 1
            self._cache.clear()

    def suspend(self):
        with self._guard:
            self._suspending = True
            self._generation += 1
            self._cancel.set()
            self._credentials = None
            self._cache.clear()
            self._pending.clear()
            self._upstream_verified = False
        # Drain an in-flight bounded HTTP call so its local bearer reference is
        # also released before suspend returns. No result can refill the cache.
        with self._work:
            with self._guard:
                self._cancel = threading.Event()
                self._suspending = False

    def close(self):
        with self._guard:
            self._closed = True
        self.suspend()
