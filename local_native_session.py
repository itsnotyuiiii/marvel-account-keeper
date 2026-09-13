"""Desktop-only NetEase connection lifecycle, separate from account metadata.

The vault key comes from the unlocked application. Persistent bootstrap is an
explicit user choice; no browser data, SDK log, capture or process is discovered.
An optional volatile session is discarded on lock/exit and cannot recover after
a process crash. Both modes use the same reviewed normal SDK/profile operations.
"""
from contextlib import contextmanager
import copy
from pathlib import Path
import threading

from career_provider import CareerUnavailable
from local_native_provider import LocalNativeCareerProvider
from standalone_session_store import EncryptedSqliteSessionStore


class MemorySessionStore:
    """Explicit session-only storage; never a durable server-store substitute."""
    def __init__(self):
        self._record = None
        self._owner = False

    def __repr__(self):
        return "<MemorySessionStore>"

    def load(self, owner_uid):
        if self._record is None:
            return None
        owner, revision, record = self._record
        return (revision, copy.deepcopy(record)) if owner == owner_uid else None

    def compare_and_swap(self, owner_uid, expected_revision, replacement):
        current = self.load(owner_uid)
        if (current[0] if current else None) != expected_revision:
            raise ValueError("session_store_conflict")
        revision = 1 if current is None else current[0] + 1
        self._record = (owner_uid, revision, copy.deepcopy(replacement))
        return revision

    @contextmanager
    def exclusive_owner(self):
        if self._owner:
            raise ValueError("session_store_in_use")
        self._owner = True
        try:
            yield self
        finally:
            self._owner = False
            self._record = None


class LocalNativeConnection:
    """App provider facade; lazily opens a saved store after vault unlock."""
    def __init__(self, data_dir, runtime_root, get_key, *, provider_factory=LocalNativeCareerProvider,
                 store_factory=EncryptedSqliteSessionStore, login_factory=None):
        self._database = Path(data_dir) / "netease-session.sqlite3"
        self._runtime_root = runtime_root
        self._get_key = get_key
        self._provider_factory, self._store_factory = provider_factory, store_factory
        self._login_factory = login_factory
        self._create_window = None
        self._provider = self._key = self._login = None
        self._persistence = "none"
        self._guard = threading.RLock()
        self._lifecycle = threading.Lock()
        self._connect_guard = threading.Lock()
        self._generation = 0
        self._closed = self._suspending = False

    def __repr__(self):
        return "<LocalNativeConnection>"

    def enable_browser(self, create_window):
        with self._guard:
            self._create_window = create_window

    def _unlocked_key(self):
        key = self._get_key()
        if type(key) is not bytes or len(key) != 32:
            raise CareerUnavailable("locked", "Unlock Marvel Tracker before using the NetEase connection.", 401)
        return key

    def _get_provider(self, *, optional=False):
        key = self._unlocked_key()
        with self._guard:
            if self._closed or self._suspending:
                raise CareerUnavailable("career_busy", "The NetEase connection is finishing its current operation.", 429)
            if self._provider is not None:
                if key != self._key:
                    raise CareerUnavailable("career_session_changed", "Lock and unlock the app to reconnect this vault.", 409)
                return self._provider
            if self._database.exists():
                try:
                    store = self._store_factory(self._database, key)
                    self._provider = self._provider_factory(store, runtime_root=self._runtime_root)
                    self._key, self._persistence = key, "encrypted_local"
                    return self._provider
                except CareerUnavailable:
                    raise
                except Exception:
                    raise CareerUnavailable("career_session_unavailable", "The saved NetEase session could not be opened. It was not overwritten.", 409) from None
            if optional:
                return None
        raise CareerUnavailable("career_not_configured", "Connect NetEase once in the connection panel, then add or refresh a UID card.", 409)

    def status(self):
        provider = self._get_provider(optional=True)
        with self._guard:
            login, browser, persistence = self._login, self._create_window, self._persistence
        # The login callback acquires our guard. Never hold it while calling
        # login.status/start/cancel (which acquire the login's own lock).
        login = login.status() if login is not None else {}
        result = provider.status() if provider is not None else {
            "configured": False, "connected": False, "state": "unconfigured", "uid": "1370598422"}
        running = login.get("running") is True
        message = "Connect NetEase, then use Add or Refresh on any PC UID card."
        if browser is None:
            message = "Open the native desktop window to sign in to NetEase."
        if result.get("state") == "ready":
            message = ("NetEase is connected. Add or refresh a PC UID card." if result.get("connected") else
                       "Sign-in saved. Refresh a PC UID card to verify the game connection.")
        elif result.get("state") in {"rotation_unknown", "refresh_in_flight"} and not result.get("busy"):
            message = "A previous renewal stopped before its new session was saved. Old tokens will not be retried. This build cannot replace that saved connection yet; it does not mean your player UID is invalid."
        elif result.get("state") in {"candidate_pending", "candidate_verifying", "candidate_recovery_required"}:
            message = "A saved renewal candidate needs recovery. Use Recover saved session before refreshing cards."
        if running:
            message = "Complete sign-in in the official NetEase window. Keep the vault unlocked."
        elif login.get("outcome") not in (None, "local_login_succeeded", "local_login_idle"):
            message = "NetEase sign-in did not finish. You can reopen the connection window and try again."
        return {**result, "provider": "native", "configured": True,
                "can_connect": browser is not None and result.get("state") == "unconfigured" and not running,
                "connection_in_progress": running, "connection_outcome": login.get("outcome"),
                "persistence": persistence, "message": message}

    def connect(self, *, persist=False):
        if type(persist) is not bool:
            raise CareerUnavailable("career_invalid_request", "Choose whether to save the local connection.", 400)
        if not self._connect_guard.acquire(blocking=False):
            raise CareerUnavailable("career_busy", "A NetEase sign-in request is already running.", 429)
        try:
            return self._connect(persist=persist)
        finally:
            self._connect_guard.release()

    def _connect(self, *, persist):
        key = self._unlocked_key()
        provider = self._get_provider(optional=True)
        if provider is not None and provider.status().get("state") != "unconfigured":
            raise CareerUnavailable("career_already_connected", "A NetEase session is already configured. Use Refresh or Recover.", 409)
        if provider is not None and not persist:
            raise CareerUnavailable("career_persistence_required", "An empty encrypted session store already exists. Select encrypted local saving to use it; no login was saved.", 409)
        with self._guard:
            previous_login, generation = self._login, self._generation
        if previous_login is not None and previous_login.status().get("running"):
            raise CareerUnavailable("career_busy", "A NetEase sign-in window is already open.", 429)
        with self._guard:
            if (self._closed or self._suspending or generation != self._generation or
                    self._provider is not provider or self._get_key() != key):
                raise CareerUnavailable("locked", "Unlock Marvel Tracker before connecting NetEase.", 401)
            if self._create_window is None:
                raise CareerUnavailable("career_browser_unavailable", "Sign-in requires the native desktop window.", 409)

            def active():
                with self._guard:
                    return (not self._closed and not self._suspending and generation == self._generation
                            and self._get_key() == key)

            def accept(bundle):
                with self._guard:
                    if not active() or self._provider is not provider:
                        return False
                    # Validate before creating a file. A pre-existing empty
                    # encrypted store is reusable, but a configured one never
                    # gets overwritten by this bootstrap path.
                    from standalone_session_manager import _bootstrap_record
                    _bootstrap_record(bundle)
                    current = provider
                    if current is None:
                        store = self._store_factory(self._database, key) if persist else MemorySessionStore()
                        current = self._provider_factory(store, runtime_root=self._runtime_root)
                    try:
                        current.bootstrap(bundle)
                    except BaseException:
                        if provider is None:
                            current.close()
                        raise
                    self._provider, self._key = current, key
                    self._persistence = "encrypted_local" if persist else "memory"
                    return True

            factory = self._login_factory
            if factory is None:
                from local_native_login import LocalNativeLogin
                factory = LocalNativeLogin
            self._login = factory(self._create_window, on_bundle=accept, is_active=active)
            login = self._login
        login.start()
        return {"ok": True, "connecting": True}

    def refresh_account(self, account):
        return self._get_provider().refresh_account(account)

    def can_refresh_account(self, account):
        try:
            provider = self._get_provider(optional=True)
            return provider is not None and provider.can_refresh_account(account)
        except CareerUnavailable:
            return False

    def cached_account(self, account):
        try:
            provider = self._get_provider(optional=True)
            return provider.cached_account(account) if provider is not None else None
        except CareerUnavailable:
            return None

    def refresh(self, season=None):
        raise CareerUnavailable("career_use_card", "Use the card's Refresh button. Native Career totals are unfiltered, not a season selector.", 409)

    def recover(self):
        return self._get_provider().recover()

    def clear(self):
        with self._guard:
            if self._provider is not None:
                self._provider.clear()

    def suspend(self):
        # A second lock/exit waits for the first drain; it must not clear the
        # suspending flag early and let a new provider take the same store.
        with self._lifecycle:
            self._suspend()

    def _suspend(self):
        with self._guard:
            self._generation += 1
            self._suspending = True
            login, provider = self._login, self._provider
            self._login = self._provider = self._key = None
            self._persistence = "none"
        try:
            if login is not None:
                login.cancel()
            if provider is not None:
                provider.close()  # Drain an admitted rotation before dropping its key.
        finally:
            with self._guard:
                self._suspending = False

    def close(self):
        with self._guard:
            self._closed = True
        self.suspend()
