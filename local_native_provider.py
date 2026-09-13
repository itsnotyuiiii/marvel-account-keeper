"""In-process desktop adapter for the verified, saved-session NetEase bundle.

No HTTP listener, capture/log discovery, target-account credentials, or fallback
provider. The caller provisions the store/key and explicitly bootstraps it. One
operation owns the session manager at a time; closing drains it before releasing
the store's lifetime ownership. Only projected player snapshots are cached.
"""

from collections import OrderedDict
import copy
from datetime import datetime, timezone
import math
import re
import threading
import time

from career_provider import CareerUnavailable
from standalone_native_runtime import NativeProfileQuery
from standalone_session_manager import OWNER_UID, OUTCOMES, OwnerSessionManager, SessionLookupResult
from standalone_web_contract import project_profile_envelope


CACHE_SECONDS = 30
MAX_CACHE = 100
_UID = re.compile(r"[1-9][0-9]{5,10}\Z")
_STATES = frozenset({"unconfigured", "unavailable", "ready", "refresh_in_flight",
    "candidate_pending", "candidate_verifying", "candidate_recovery_required", "rotation_unknown"})
MESSAGES = {
    "missing_identity": ("Enter a Marvel Rivals UID to look up this player.", 400),
    "career_uid_required": ("This NetEase connection currently looks up numeric UIDs, not in-game names. Enter the player's UID; no third-party search was made.", 400),
    "invalid_rivals_uid": ("Enter a numeric Marvel Rivals UID of 6 to 11 digits, without leading zeros.", 400),
    "career_platform_unavailable": ("This NetEase connection currently supports PC players only. Choose PC or Unknown.", 400),
    "career_busy": ("A NetEase operation is already running. Wait for it to finish before refreshing again.", 429),
    "career_closed": ("The local NetEase connection is closing or closed. Reopen it before refreshing.", 409),
    "career_config_invalid": ("The local NetEase runtime or session store could not be configured. No lookup was made.", 500),
    "career_session_in_use": ("Another local process owns this NetEase session. Close that process before connecting here.", 409),
    "career_not_configured": ("Connect your authorized NetEase session before refreshing players. No third-party lookup was made.", 409),
    "career_already_configured": ("A NetEase session is already saved. It was not replaced.", 409),
    "career_bootstrap_invalid": ("The supplied NetEase connection information could not be verified. Existing session data was not replaced.", 400),
    "career_session_rotation_unknown": ("The previous NetEase renewal did not finish with a saved candidate. Reconnect explicitly; the previous refresh token will not be retried.", 409),
    "career_session_recovery_required": ("The saved NetEase candidate needs explicit recovery before another refresh. This does not mean the player's UID is invalid.", 409),
    "career_no_pending_session": ("There is no saved NetEase candidate to recover. No renewal was attempted.", 409),
    "career_session_unverified": ("NetEase did not verify the connection account. This does not mean the requested player's UID is invalid.", 502),
    "career_store_unavailable": ("The saved NetEase session could not be read or updated. No earlier refresh token was retried.", 503),
    "career_identity_unavailable": ("NetEase did not return a verified profile for the requested UID. This does not establish that the UID is invalid.", 502),
    "career_request_failed": ("The NetEase lookup did not complete. Saved card values were kept; no third-party lookup was made.", 502),
}
_SESSION_ERRORS = {
    "session_unconfigured": "career_not_configured",
    "session_already_configured": "career_already_configured",
    "session_context_invalid": "career_bootstrap_invalid",
    "session_rotation_unknown": "career_session_rotation_unknown",
    "session_candidate_pending": "career_session_recovery_required",
    "session_verification_in_flight": "career_session_recovery_required",
    "session_recovery_required": "career_session_recovery_required",
    "session_exclusive_ownership_required": "career_session_recovery_required",
    "session_no_pending_candidate": "career_no_pending_session",
    "session_owner_unverified": "career_session_unverified",
    "session_access_rejected": "career_session_unverified",
    **{name: "career_store_unavailable" for name in (
        "session_store_unavailable", "session_store_record_invalid", "session_store_write_failed")},
}


def _error(code):
    message, status = MESSAGES[code]
    return CareerUnavailable(code, message, status)


def _account_uid(account):
    if type(account) is not dict:
        raise _error("invalid_rivals_uid")
    platform = account.get("rivals_platform")
    if platform is not None and (type(platform) is not str or platform not in {"", "pc", "unknown"}):
        raise _error("career_platform_unavailable")
    value = account.get("rivals_uid")
    if value is None or type(value) is str and not value:
        name = account.get("in_game_name")
        raise _error("career_uid_required" if type(name) is str and name.strip() else "missing_identity")
    if type(value) is int:
        # Validate before converting, including hostile oversized Python ints.
        if not 100000 <= value <= 99999999999:
            raise _error("invalid_rivals_uid")
        value = str(value)
    if type(value) is not str or _UID.fullmatch(value) is None:
        raise _error("invalid_rivals_uid")
    return int(value)


def _status(raw=None, *, busy=False, closing=False, closed=False):
    raw = raw if type(raw) is dict else {}
    valid_owner = type(raw.get("owner_uid")) is int and raw["owner_uid"] == OWNER_UID
    state = raw.get("state")
    state = state if valid_owner and type(state) is str and state in _STATES else "unavailable"
    outcome = raw.get("outcome")
    outcome = outcome if valid_owner and type(outcome) is str and outcome in OUTCOMES else "session_store_unavailable"
    configured = valid_owner and raw.get("configured") is True
    authenticated = configured and state == "ready" and raw.get("owner_authenticated") is True
    return {"provider": "native", "uid": str(OWNER_UID), "configured": configured,
        "connected": authenticated and not (busy or closing or closed),
        "state": "closed" if closed else "closing" if closing else state, "outcome": outcome,
        "owner_authenticated": authenticated and not closed,
        "busy": busy, "closing": closing, "closed": closed,
        "candidate_saved": valid_owner and raw.get("candidate_saved") is True,
        "rotation_uncertain": valid_owner and raw.get("rotation_uncertain") is True,
        "requires_exclusive_recovery": valid_owner and raw.get("requires_exclusive_recovery") is True,
        "sdk_expiry_known": False, "automatic_renewal_verified": False}


class LocalNativeCareerProvider:
    """The supplied store must implement exclusive_owner/load/compare_and_swap.

    refresh_account reuses a successful snapshot for at most 30 seconds. The
    drawer's cached_account may return it later, keeping its original fetched_at
    (never claiming a fresh read). Failure clears only that target's snapshot.
    clear removes this safe cache, not persisted authentication or active work.
    """

    def __init__(self, store, *, query=None, exchange=None, runtime_root=None, clock=time.time):
        self._state_lock = threading.Lock()
        self._work_lock = threading.Lock()
        self._closing = self._closed = False
        self._cache = OrderedDict()
        self._cache_generation = 0
        self._last_status = {}
        self._owner_context = self._manager = self._store = None
        self._clock = clock
        context = None
        entered = False
        try:
            if not callable(clock) or not callable(getattr(store, "exclusive_owner", None)):
                raise ValueError()
            runtime = NativeProfileQuery(root=runtime_root) if query is None else query
            manager = OwnerSessionManager(store, query=runtime, exchange=exchange)
            context = store.exclusive_owner()
            context.__enter__()
            entered = True
            self._owner_context, self._manager, self._store = context, manager, store
        except Exception as error:
            if entered:
                context.__exit__(None, None, None)
            code = getattr(error, "code", None)
            raise _error("career_session_in_use" if type(code) is str and code == "session_store_in_use"
                         else "career_config_invalid") from None

    def __repr__(self):
        return "<LocalNativeCareerProvider>"

    def _begin(self):
        with self._state_lock:
            if self._closing or self._closed:
                raise _error("career_closed")
            if not self._work_lock.acquire(blocking=False):
                raise _error("career_busy")

    def _finish(self):
        try:
            try:
                raw = self._manager.status()
            except Exception:
                raw = None
            safe = _status(raw)
            # Store only the fixed status projection, never an injected/raw map.
            with self._state_lock:
                self._last_status = dict(safe, owner_uid=OWNER_UID)
        finally:
            self._work_lock.release()

    def _now(self):
        stamp = self._clock()
        if type(stamp) not in (int, float) or not 0 < stamp < 253402300800 or not math.isfinite(stamp):
            raise _error("career_request_failed")
        return stamp

    @staticmethod
    def _result(result, success):
        if type(result) is not SessionLookupResult:
            raise _error("career_request_failed")
        outcome = result.outcome
        if type(outcome) is not str or outcome != success:
            code = _SESSION_ERRORS.get(outcome, "career_request_failed") if type(outcome) is str else "career_request_failed"
            raise _error(code)
        if success == "session_lookup_complete" and result.owner_authenticated is not True:
            raise _error("career_session_unverified")
        return {"outcome": success, "owner_authenticated": result.owner_authenticated is True}

    def bootstrap(self, bundle):
        self._begin()
        try:
            result = self._result(self._manager.bootstrap(bundle), "session_bootstrapped")
            self.clear()
            return result
        except CareerUnavailable:
            raise
        except Exception:
            raise _error("career_bootstrap_invalid") from None
        finally:
            self._finish()

    def recover(self):
        self._begin()
        try:
            return self._result(self._manager.recover_pending(exclusive_ownership=True), "session_lookup_complete")
        except CareerUnavailable:
            raise
        except Exception:
            raise _error("career_request_failed") from None
        finally:
            self._finish()

    def status(self):
        try:
            self._begin()
        except CareerUnavailable:
            with self._state_lock:
                return _status(self._last_status, busy=self._work_lock.locked(),
                               closing=self._closing, closed=self._closed)
        try:
            raw = self._manager.status()
            return _status(raw)
        except Exception:
            return _status()
        finally:
            self._finish()

    def can_refresh_account(self, account):
        try:
            _account_uid(account)
        except CareerUnavailable:
            return False
        with self._state_lock:
            return not (self._closing or self._closed)

    def cached_account(self, account):
        try:
            uid = _account_uid(account)
        except CareerUnavailable:
            return None
        with self._state_lock:
            if self._closing or self._closed or uid not in self._cache:
                return None
            self._cache.move_to_end(uid)
            return copy.deepcopy(self._cache[uid][1])

    def refresh_account(self, account):
        uid = _account_uid(account)
        self._begin()
        try:
            now = self._now()
            with self._state_lock:
                generation = self._cache_generation
                previous = self._cache.get(uid)
                if previous is not None and 0 <= now - previous[0] < CACHE_SECONDS:
                    self._cache.move_to_end(uid)
                    return copy.deepcopy(previous[1])
            result = self._manager.lookup(uid)
            self._result(result, "session_lookup_complete")
            if type(result.profile) is not dict:
                raise _error("career_identity_unavailable")
            fetched = self._now()
            try:
                envelope = project_profile_envelope(result.profile, uid, datetime.fromtimestamp(fetched, timezone.utc))
                profile = envelope["nativeProfile"]
                snapshot = {"uid": str(uid), "platform": "pc", "in_game_name": profile["identity"]["in_game_name"],
                            "fetched_at": int(fetched), "native_profile": profile}
            except Exception:
                raise _error("career_identity_unavailable") from None
            with self._state_lock:
                if generation == self._cache_generation and not self._closing:
                    self._cache[uid] = (fetched, copy.deepcopy(snapshot))
                    self._cache.move_to_end(uid)
                    while len(self._cache) > MAX_CACHE:
                        self._cache.popitem(last=False)
            return snapshot
        except CareerUnavailable:
            with self._state_lock:
                self._cache.pop(uid, None)
            raise
        except Exception:
            with self._state_lock:
                self._cache.pop(uid, None)
            raise _error("career_request_failed") from None
        finally:
            self._finish()

    def clear(self):
        with self._state_lock:
            self._cache.clear()
            self._cache_generation += 1

    def close(self):
        with self._state_lock:
            self._closing = True
        # Never interrupt an exchange/query or release ownership during it.
        with self._work_lock:
            with self._state_lock:
                if self._closed:
                    return
                context = self._owner_context
                self._cache.clear()
                self._cache_generation += 1
            try:
                context.__exit__(None, None, None)
            except Exception:
                raise _error("career_store_unavailable") from None
            finally:
                with self._state_lock:
                    self._manager = self._store = self._owner_context = None
                    self._last_status = {}
                    self._closed = True
