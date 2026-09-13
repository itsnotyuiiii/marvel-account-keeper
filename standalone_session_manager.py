"""Durable, owner-bound saved-SDK renewal; no browser or credential discovery.

The caller supplies a durable store and an exclusively owned, serialized worker.
CAS claims also prevent two ordinary lookup workers from rotating the same saved
token. No token TTL has been established, so a ready session refreshes on use.
There is no same-lookup retry, old-token fallback, or claimed expiry verification.

Durable ordering is ready -> refresh_in_flight -> candidate_pending ->
candidate_verifying -> ready. The candidate write replaces the saved refresh
token before generating SAUTH or authenticating. A crash during the remote SDK
exchange can leave rotation unknown; the old token is NEVER automatically used
again. A saved candidate can instead be verified without another SDK exchange.
Recovering candidate_verifying requires the caller to hold an exclusive process
or owner-worker lock and attest that the former verifier can no longer run.
An unclassified query failure instead requires explicit candidate recovery. The
one reviewed exception is correlated game AUTH error 253: it permits the NEXT
lookup to exchange the latest returned refresh token, not the preceding token.

QueryResult and SessionLookupResult are PRIVATE, repr-hidden objects. Their
profile dictionaries are untrusted internal data, NOT redacted HTTP responses.
The HTTP layer must independently project their schema before serialization.
Only status() is a fixed, credential-free public diagnostic. This module does
not implement encryption, filesystem storage, HTTP serving or background jobs.
"""

from dataclasses import dataclass
import random
import time
from typing import Protocol

import web_sdk_history_login as history


OWNER_UID = history.EXPECTED_OWNER_UID
SCHEMA_VERSION = 1
MAX_REVISION = (1 << 63) - 1
BUNDLE_FIELDS = frozenset({"selected_uid", "sauth", "saved_account", "mpay_context", "wrapper_udid"})
RECORD_FIELDS = frozenset({"schema_version", "owner_uid", "state", "owner_verified", "sdkuid",
                           "saved_account", "mpay_context", "wrapper_udid", "candidate"})
STATES = frozenset({"ready", "refresh_in_flight", "candidate_pending", "candidate_verifying",
                    "candidate_recovery_required", "rotation_unknown"})
OUTCOMES = frozenset({
    "session_context_invalid", "session_uid_invalid", "session_bootstrapped", "session_already_configured",
    "session_unconfigured", "session_ready", "session_candidate_pending", "session_verification_in_flight",
    "session_rotation_unknown", "session_store_unavailable", "session_store_record_invalid",
    "session_store_write_failed", "session_sdk_not_attempted", "session_sauth_failed",
    "session_query_failed", "session_owner_unverified", "session_lookup_complete",
    "session_exclusive_ownership_required", "session_no_pending_candidate",
    "session_recovery_required", "session_access_rejected",
})


class SessionStore(Protocol):
    """Persistence must be durable before CAS returns; never log record values.

    load returns None or (nonnegative revision, record). compare_and_swap inserts
    only when expected_revision is None and no record exists, otherwise updates
    only the exact revision. Conflict/failure must raise, not silently overwrite.
    Its returned revision must be greater than the previous revision. The main
    application provides encryption and an exclusive lock for crash recovery.
    """

    def load(self, owner_uid: int) -> tuple[int, dict] | None: ...

    def compare_and_swap(self, owner_uid: int, expected_revision: int | None,
                         replacement: dict) -> int: ...


@dataclass(frozen=True, repr=False)
class QueryResult:
    """Trusted owner proof plus untrusted private profile, supplied by the caller.

    auth_rejection_code must come ONLY from a correlated normal AUTH rejection,
    with prepared connection, credentials sent and no owner proof. It is not an
    SDK application code, transport error, profile error or guessed expiry code.
    The production adapter performs that projection; this class does not parse
    raw server reports. Only exact int 253 has the reviewed recovery policy.
    """

    owner_uid: int
    owner_authenticated: bool
    profile: dict | None = None
    auth_rejection_code: int | None = None


@dataclass(frozen=True, repr=False)
class SessionLookupResult:
    """Internal only: project profile separately before any HTTP serialization."""

    outcome: str
    owner_authenticated: bool = False
    profile: dict | None = None


@dataclass(frozen=True, repr=False)
class _Stored:
    revision: int
    record: dict


class SessionManagerError(ValueError):
    def __init__(self, outcome):
        self.outcome = outcome if type(outcome) is str and outcome in OUTCOMES else "session_context_invalid"
        super().__init__(self.outcome)


def _text(value, bound):
    if type(value) is not str or not value.strip():
        raise SessionManagerError("session_context_invalid")
    try:
        if len(value.encode("utf-8")) > bound:
            raise SessionManagerError("session_context_invalid")
    except UnicodeError:
        raise SessionManagerError("session_context_invalid") from None
    return value


def _uid(value):
    # Public UID normalization is the HTTP layer's job; reject bool/float and
    # other lossy/coercible values before touching a store or remote service.
    return type(value) is int and 0 < value < (1 << 63)


def _snapshot(record):
    """Copy only the already validated scalar schema, not arbitrary objects."""
    return dict(record, saved_account=record["saved_account"].copy(),
                mpay_context=record["mpay_context"].copy(),
                candidate=None if record["candidate"] is None else record["candidate"].copy())


def _inputs(record):
    account = record["saved_account"]
    return history.HistoryLoginInputs(OWNER_UID, record["sdkuid"], account["userId"],
                                     account["deviceId"], account["refreshToken"],
                                     tuple(record["mpay_context"].items()))


def _validate_record(record):
    if (type(record) is not dict or record.keys() != RECORD_FIELDS
            or type(record["schema_version"]) is not int or record["schema_version"] != SCHEMA_VERSION
            or type(record["owner_uid"]) is not int or record["owner_uid"] != OWNER_UID
            or type(record["state"]) is not str or record["state"] not in STATES
            or type(record["owner_verified"]) is not bool
            or record["state"] != "ready" and record["owner_verified"]
            or type(record["saved_account"]) is not dict
            or record["saved_account"].keys() != history.ACCOUNT_FIELDS
            or type(record["mpay_context"]) is not dict):
        raise SessionManagerError("session_store_record_invalid")
    account = record["saved_account"]
    if (type(account["method"]) is not str or account["method"] != "steam"
            or type(account["clientPlatform"]) is not str or account["clientPlatform"] != "p"):
        raise SessionManagerError("session_store_record_invalid")
    _text(record["wrapper_udid"], 512)
    _text(account["refreshToken"], history.MAX_TOKEN_BYTES)
    item = _inputs(record)
    # A returned token is durably retained even if a future exchange cannot
    # represent it with the source's encode:false form. Validate static context
    # with a harmless placeholder, and validate the REAL form before SDK I/O.
    static = history.HistoryLoginInputs(item.expected_owner_uid, item.sauth_sdkuid,
                                        item.saved_user_id, item.saved_device_id,
                                        "validation-placeholder", item.mpay_context)
    history.build_history_form(static)
    pending = record["state"] in {"candidate_pending", "candidate_verifying", "candidate_recovery_required"}
    candidate = record["candidate"]
    if pending:
        if type(candidate) is not dict or candidate.keys() != {"access_token"}:
            raise SessionManagerError("session_store_record_invalid")
        _text(candidate["access_token"], history.MAX_TOKEN_BYTES)
    elif candidate is not None:
        raise SessionManagerError("session_store_record_invalid")
    return _snapshot(record)


def _bootstrap_record(bundle):
    if (type(bundle) is not dict or bundle.keys() != BUNDLE_FIELDS
            or type(bundle["selected_uid"]) is not int or bundle["selected_uid"] != OWNER_UID
            or type(bundle["sauth"]) is not dict or "refresh_token" in bundle["sauth"]
            or type(bundle["saved_account"]) is not dict or type(bundle["mpay_context"]) is not dict
            or type(bundle["wrapper_udid"]) is not str
            or bundle["wrapper_udid"] != bundle["sauth"].get("udid")):
        raise SessionManagerError("session_context_invalid")
    _text(bundle["wrapper_udid"], 512)
    item = history.build_history_login_inputs(bundle["sauth"], bundle["saved_account"],
                                             bundle["mpay_context"])
    return _validate_record({
        "schema_version": SCHEMA_VERSION, "owner_uid": OWNER_UID, "state": "ready",
        "owner_verified": False, "sdkuid": item.sauth_sdkuid,
        "saved_account": {"userId": item.saved_user_id, "refreshToken": item.refresh_token,
                          "method": "steam", "clientPlatform": "p", "deviceId": item.saved_device_id},
        "mpay_context": dict(item.mpay_context), "wrapper_udid": bundle["wrapper_udid"], "candidate": None,
    })


def _milliseconds():
    return time.time_ns() // 1_000_000


def _samples():
    return tuple(random.random() for _ in range(8))


class OwnerSessionManager:
    """Synchronous single-owner lifecycle, with no cached secret record or logs.

    query(sauth, target_uid) must run owner-bound AUTH and return QueryResult.
    Owner proof is checked independently of target profile availability. The
    callback gets only newly built SAUTH, never a refresh token or prior SAUTH.
    exchange, clock_ms and random_samples are injectable for offline tests.
    Every failed CAS stops that operation; there is no CAS retry loop.
    """

    def __init__(self, store, *, query, exchange=None, clock_ms=None, random_samples=None):
        self._store = store
        self._query = query
        self._exchange = history.login_history_once if exchange is None else exchange
        self._clock_ms = _milliseconds if clock_ms is None else clock_ms
        self._random_samples = _samples if random_samples is None else random_samples
        if (not callable(getattr(store, "load", None)) or not callable(getattr(store, "compare_and_swap", None))
                or not callable(query) or not callable(self._exchange)
                or not callable(self._clock_ms) or not callable(self._random_samples)):
            raise SessionManagerError("session_context_invalid")

    def _load(self):
        try:
            value = self._store.load(OWNER_UID)
        except Exception:
            raise SessionManagerError("session_store_unavailable") from None
        if value is None:
            return None
        try:
            if (type(value) is not tuple or len(value) != 2 or type(value[0]) is not int
                    or not 0 <= value[0] <= MAX_REVISION):
                raise ValueError
            return _Stored(value[0], _validate_record(value[1]))
        except Exception:
            raise SessionManagerError("session_store_record_invalid") from None

    def _write(self, expected_revision, record):
        try:
            # Validate before crossing the injected storage boundary, and do
            # not let a mutating store retain the caller's live dictionary.
            replacement = _validate_record(record)
            revision = self._store.compare_and_swap(OWNER_UID, expected_revision, replacement)
            if (type(revision) is not int or not 0 <= revision <= MAX_REVISION
                    or expected_revision is not None and revision <= expected_revision):
                raise ValueError
            return revision
        except Exception:
            raise SessionManagerError("session_store_write_failed") from None

    def bootstrap(self, bundle):
        """Insert an explicitly supplied bundle once; never replace old state.

        Only source-required account/context fields survive. Prior access token,
        prior SAUTH, serial, optional display fields and arbitrary extras are not
        stored. This validates binding, not live owner authentication.
        """
        try:
            record = _bootstrap_record(bundle)
        except Exception:
            return SessionLookupResult("session_context_invalid")
        try:
            if self._load() is not None:
                return SessionLookupResult("session_already_configured")
            self._write(None, record)
        except SessionManagerError as error:
            return SessionLookupResult(error.outcome)
        return SessionLookupResult("session_bootstrapped")

    def status(self):
        """Fixed redacted status only: no SDK IDs, tokens, device/context/profile."""
        result = {"outcome": "session_unconfigured", "owner_uid": OWNER_UID,
                  "state": "unconfigured", "configured": False, "revision": None,
                  "owner_authenticated": False, "candidate_saved": False,
                  "rotation_uncertain": False, "requires_exclusive_recovery": False,
                  "sdk_expiry_known": False, "automatic_renewal_verified": False}
        try:
            saved = self._load()
        except SessionManagerError as error:
            result.update(outcome=error.outcome, state="unavailable")
            return result
        if saved is None:
            return result
        record, state = saved.record, saved.record["state"]
        result.update(configured=True, revision=saved.revision, state=state,
                      owner_authenticated=state == "ready" and record["owner_verified"],
                      candidate_saved=record["candidate"] is not None,
                      rotation_uncertain=state in {"refresh_in_flight", "rotation_unknown"},
                      requires_exclusive_recovery=state in {"candidate_verifying", "candidate_recovery_required"})
        result["outcome"] = {
            "ready": "session_ready", "candidate_pending": "session_candidate_pending",
            "candidate_verifying": "session_verification_in_flight",
            "candidate_recovery_required": "session_recovery_required",
            "refresh_in_flight": "session_rotation_unknown", "rotation_unknown": "session_rotation_unknown",
        }[state]
        return result

    def lookup(self, uid):
        """One refresh-on-use and one owner-bound query; no retry or fallback."""
        if not _uid(uid):
            return SessionLookupResult("session_uid_invalid")
        try:
            saved = self._load()
            if saved is None:
                return SessionLookupResult("session_unconfigured")
            state = saved.record["state"]
            if state in {"refresh_in_flight", "rotation_unknown"}:
                return SessionLookupResult("session_rotation_unknown")
            if state == "candidate_verifying":
                return SessionLookupResult("session_verification_in_flight")
            if state == "candidate_recovery_required":
                return SessionLookupResult("session_recovery_required")
            if state == "candidate_pending":
                return self._verify(saved, uid)
            return self._refresh(saved, uid)
        except SessionManagerError as error:
            return SessionLookupResult(error.outcome)

    def recover_pending(self, *, exclusive_ownership=False):
        """Verify a saved candidate without SDK exchange after known restart.

        exact True is an attestation, not a lock implementation. The caller MUST
        already own an external exclusive owner/process lock and know the former
        verifier has stopped. Normal workers may not steal another verifier's
        claim. Unknown rotation without a candidate cannot be recovered here.
        Recovery queries the owner UID, never a previous caller's target UID.
        """
        if exclusive_ownership is not True:
            return SessionLookupResult("session_exclusive_ownership_required")
        try:
            saved = self._load()
            if saved is None:
                return SessionLookupResult("session_unconfigured")
            if saved.record["state"] in {"refresh_in_flight", "rotation_unknown"}:
                return SessionLookupResult("session_rotation_unknown")
            if saved.record["candidate"] is None:
                return SessionLookupResult("session_no_pending_candidate")
            return self._verify(saved, OWNER_UID)
        except SessionManagerError as error:
            return SessionLookupResult(error.outcome)

    def _refresh(self, saved, uid):
        record = _snapshot(saved.record)
        inputs = _inputs(record)
        try:
            history.build_history_form(inputs)
        except Exception:
            return SessionLookupResult("session_context_invalid")
        record.update(state="refresh_in_flight", owner_verified=False)
        revision = self._write(saved.revision, record)  # Must be durable before exchange.
        try:
            candidate = self._exchange(inputs)
        except history.HistoryLoginError as error:
            # Only a positively known pre-request failure permits retaining a
            # usable ready token. A service rejection is not proof of invalid
            # credentials, nor proof that an earlier token remains reusable.
            safe = error.request_attempted is False
            record.update(state="ready" if safe else "rotation_unknown",
                          owner_verified=saved.record["owner_verified"] if safe else False)
            self._write(revision, record)
            return SessionLookupResult("session_sdk_not_attempted" if safe else "session_rotation_unknown")
        except Exception:
            record.update(state="rotation_unknown")
            self._write(revision, record)
            return SessionLookupResult("session_rotation_unknown")
        try:
            if (type(candidate) is not history.HistoryLoginCandidate
                    or type(candidate.expected_owner_uid) is not int or candidate.expected_owner_uid != OWNER_UID
                    or type(candidate.sdkuid) is not str or candidate.sdkuid != inputs.sauth_sdkuid
                    or type(candidate.saved_device_id) is not str or candidate.saved_device_id != inputs.saved_device_id):
                raise ValueError
            _text(candidate.access_token, history.MAX_TOKEN_BYTES)
            _text(candidate.refresh_token, history.MAX_TOKEN_BYTES)
        except Exception:
            record.update(state="rotation_unknown")
            self._write(revision, record)
            return SessionLookupResult("session_rotation_unknown")
        record["saved_account"]["refreshToken"] = candidate.refresh_token
        record.update(state="candidate_pending", candidate={"access_token": candidate.access_token})
        # Persist BOTH returned credentials before sampling, building SAUTH, or
        # AUTH. If this CAS fails, stop; in-flight state prevents old-token reuse.
        revision = self._write(revision, record)
        return self._verify(_Stored(revision, record), uid)

    def _leave_pending(self, revision, record, outcome, *, recovery_required=False):
        record.update(state="candidate_recovery_required" if recovery_required else "candidate_pending",
                      owner_verified=False)
        self._write(revision, record)
        return SessionLookupResult(outcome)

    def _verify(self, saved, uid):
        record = _snapshot(saved.record)
        record.update(state="candidate_verifying", owner_verified=False)
        revision = self._write(saved.revision, record)
        candidate = history.HistoryLoginCandidate(OWNER_UID, record["sdkuid"],
                                                  record["saved_account"]["deviceId"],
                                                  record["candidate"]["access_token"],
                                                  record["saved_account"]["refreshToken"])
        try:
            samples = self._random_samples()  # Source samples first, then clock.
            timestamp = self._clock_ms()
            fresh = history.build_fresh_wrapper_sauth(candidate, wrapper_udid=record["wrapper_udid"],
                                                     clock_ms=timestamp, random_samples=samples)
        except Exception:
            return self._leave_pending(revision, record, "session_sauth_failed")
        try:
            result = self._query(fresh.sauth.copy(), uid)
        except Exception:
            return self._leave_pending(revision, record, "session_query_failed", recovery_required=True)
        owner_bound = (type(result) is QueryResult and type(result.owner_uid) is int
                       and result.owner_uid == OWNER_UID)
        if (owner_bound and result.owner_authenticated is False
                and type(result.auth_rejection_code) is int and result.auth_rejection_code == 253):
            # Exact, source-reviewed normal AUTH rejection only. Preserve the
            # newest refresh token, discard rejected access, and stop. A later
            # explicit lookup may perform one fresh SDK exchange; this says
            # nothing about TTL and never falls back to the original token.
            record.update(state="ready", candidate=None, owner_verified=False)
            self._write(revision, record)
            return SessionLookupResult("session_access_rejected")
        if (not owner_bound or result.owner_authenticated is not True
                or result.auth_rejection_code is not None):
            return self._leave_pending(revision, record, "session_owner_unverified", recovery_required=True)
        record.update(state="ready", candidate=None, owner_verified=True)
        try:
            self._write(revision, record)
        except SessionManagerError as error:
            # Proof happened, but publication must not hide a durability failure.
            return SessionLookupResult(error.outcome, owner_authenticated=True)
        profile = result.profile.copy() if type(result.profile) is dict else None
        return SessionLookupResult("session_lookup_complete", owner_authenticated=True, profile=profile)
