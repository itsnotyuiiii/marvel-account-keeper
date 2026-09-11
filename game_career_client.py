"""App-owned, bounded other-player Career connection.

One daemon worker owns normal LOGIN and its gateway while servicing sequential
requests. Idle connections close instead of inventing heartbeat traffic.
Desktop defaults use the installed game image/config/SDK-log locations and the
selected script archive. Explicit providers can supply the same validated
inputs without installed files. Inputs are loaded for each new connection;
this client does not acquire credentials or implement session renewal.
"""
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
import socket
import threading
import time

import game_career_probe as career
import game_login_probe as login
from game_transport_probe import load_public_configuration


MESSAGES = {
    "game_career_invalid_target": "Detailed Career needs a valid other-player UID.",
    "game_career_invalid_season": "Detailed Career needs a supported season ID.",
    "game_career_platform_unavailable": "Direct other-player Career is currently connected for PC only.",
    "game_career_busy": "The shared Career connection is busy. Try again shortly.",
    "game_career_cooldown": "The shared Career connection failed. Wait 30 seconds before trying again.",
    "game_career_session_unavailable": "The shared game session could not be verified. A current normal game login may be needed.",
    "game_career_dependency_missing": "This app build is missing a required Career component. Use an updated build; logging in again or changing the target UID will not fix it.",
    "game_career_session_expired": "The saved own-game login is outside its freshness window (36 hours). Log into your own account once, exit the game, and retry. This does not mean the target UID is invalid.",
    "game_career_login_needed": "No usable own-game login was found. Log into your own account once, exit the game, and retry. Target players do not need to log in on this PC.",
    "game_career_build_unavailable": "The installed game files do not match the supported build. Game compatibility needs updating; saved stats were kept.",
    "game_career_session_rejected": "NetEase rejected the shared own-account session. Log into your own account once, exit the game, and retry. This does not mean the target UID is invalid.",
    "game_career_own_role_mismatch": "The game login did not return the configured connection account. Log into that own account and retry; no target player's login is needed.",
    "game_career_unavailable": "NetEase did not return verified detailed Career stats. Available ranks are independent.",
    "game_career_shape_changed": "NetEase returned unfamiliar Career statistics. Saved stats were kept.",
    "game_career_timeout": "The detailed Career read timed out. Saved stats were kept.",
    "game_career_closed": "The shared Career connection was closed. Refresh again when the app is unlocked.",
    "game_career_session_limit": "The shared Career connection reached its request limit. Refresh again shortly.",
}


class GameCareerUnavailable(Exception):
    """Only fixed diagnostics may cross the worker/application boundary."""

    def __init__(self, code="game_career_unavailable"):
        self.code = code if code in MESSAGES else "game_career_unavailable"
        self.message = MESSAGES[self.code]
        super().__init__(self.message)


_SESSION_ERRORS = {
    "own_sdk_login_too_old": "game_career_session_expired",
    "own_sdk_bundle_not_found": "game_career_login_needed",
    "unsupported_sdk_build": "game_career_build_unavailable",
    "rpc_configuration_changed": "game_career_build_unavailable",
    "Unrecognized archive build; revalidate the format before decoding": "game_career_build_unavailable",
    "Unrecognized archive size": "game_career_build_unavailable",
    "Unrecognized native image size": "game_career_build_unavailable",
    "Unrecognized native image build": "game_career_build_unavailable",
    "Unrecognized handshake parameter": "game_career_build_unavailable",
    "index checksum mismatch": "game_career_build_unavailable",
    "config checksum mismatch": "game_career_build_unavailable",
    "server_rejected_authentication": "game_career_session_rejected",
    "gateway_rejected_login": "game_career_session_rejected",
    "expected_role_missing_or_ambiguous": "game_career_own_role_mismatch",
    "gateway_role_mismatch": "game_career_own_role_mismatch",
}


def _session_error(value):
    # Match only exact known fixed helper codes. Never stringify an arbitrary
    # exception/report or pass its underlying text into application output.
    if isinstance(value, ImportError):
        return GameCareerUnavailable("game_career_dependency_missing")
    if isinstance(value, ValueError):
        value = value.args[0] if len(value.args) == 1 else None
    code = _SESSION_ERRORS.get(value) if type(value) is str else None
    return GameCareerUnavailable(code or "game_career_session_unavailable")


@dataclass
class _Request:
    uid: int
    season: int
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    result: dict | None = None
    error: GameCareerUnavailable | None = None
    cancelled: bool = False


def _project_profile(data, uid):
    row = data.get("career") if isinstance(data, dict) else None
    if not isinstance(row, dict) or type(row.get("player_uid")) is not int or row["player_uid"] != uid:
        raise GameCareerUnavailable("game_career_shape_changed")
    result = {}
    for name, source in (("matches", "total_match_count"), ("wins", "total_win_count"),
                         ("kills", "k"), ("deaths", "d"), ("assists", "a")):
        value = row.get(source)
        if type(value) is not int or not 0 <= value <= 10**12:
            raise GameCareerUnavailable("game_career_shape_changed")
        result[name] = value
    if result["wins"] > result["matches"]:
        raise GameCareerUnavailable("game_career_shape_changed")
    return result


class GameCareerClient:
    IDLE_SECONDS = 15.0
    REQUEST_WAIT_SECONDS = 60.0
    MAX_REQUESTS = 32
    MAX_PENDING = 8
    FAILURE_COOLDOWN_SECONDS = 30.0

    def __init__(self, own_uid, archive=None, *, configuration_provider=None,
                 career_methods_provider=None, own_credentials_provider=None,
                 server_provider=None):
        """Providers are zero-argument callables, invoked lazily per connection.

        They return PublicConfiguration, CareerMethods, LoginInputs and
        LoginServer respectively. Providers own input provenance/freshness
        validation; normal login, exact own-role and Career checks still run.
        Omit archive only when all four providers are supplied, preventing an
        unintended fallback to desktop files in a fully injected client.
        """
        if type(own_uid) is not int or not 0 < own_uid < 2**63:
            raise GameCareerUnavailable("game_career_invalid_target")
        providers = (configuration_provider, career_methods_provider,
                     own_credentials_provider, server_provider)
        if any(provider is not None and not callable(provider) for provider in providers):
            raise ValueError("Career providers must be callable")
        if archive is None and any(provider is None for provider in providers):
            raise ValueError("An archive or all four Career providers are required")
        self.own_uid = own_uid
        self.archive = None if archive is None else Path(archive)
        self._configuration_provider = configuration_provider
        self._career_methods_provider = career_methods_provider
        self._own_credentials_provider = own_credentials_provider
        self._server_provider = server_provider
        self._condition = threading.Condition()
        self._pending = deque()
        self._worker = None
        self._active = None
        self._socket = None
        self._stop = False
        self._accepting = False
        self._connected = False
        self._requests = 0
        self._last_error = None
        self._cooldown_until = 0.0
        self._stage = "idle"
        self._failure_kind = None

    def status(self):
        with self._condition:
            remaining = max(0.0, self._cooldown_until - time.monotonic())
            return {"configured": True, "connected": self._connected, "last_error": self._last_error,
                    "cooldown_remaining_s": int(remaining) + bool(remaining % 1),
                    "shared_session": True, "platforms": ["pc"],
                    "stage": self._stage, "failure_kind": self._failure_kind,
                    "automatic_renewal": False}

    def _set_stage(self, stage):
        with self._condition:
            self._stage = stage

    @staticmethod
    def _complete(request, *, result=None, error=None):
        if not request.done.is_set():
            request.result, request.error = result, error
            request.done.set()

    def _failure(self, error):
        # Caller holds the condition; one failure backs off every target.
        self._last_error = error.code
        self._cooldown_until = time.monotonic() + self.FAILURE_COOLDOWN_SECONDS
        self._accepting = False

    @staticmethod
    def _shutdown(connection):
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _connect(self, *args, **kwargs):
        connection = socket.create_connection(*args, **kwargs)
        with self._condition:
            if self._stop:
                connection.close()
                raise GameCareerUnavailable("game_career_closed")
            self._socket = connection
        return connection

    def read_profile(self, uid, season, platform="pc"):
        # Do not load credentials/configuration or create a worker for invalid
        # targets. Only the configured app account is used for authentication.
        if type(uid) is not int or not 0 < uid < 2**63 or uid == self.own_uid:
            raise GameCareerUnavailable("game_career_invalid_target")
        if type(season) is not int or not 0 <= season < 1000:
            raise GameCareerUnavailable("game_career_invalid_season")
        if platform != "pc":
            raise GameCareerUnavailable("game_career_platform_unavailable")
        request = _Request(uid, season)
        with self._condition:
            if time.monotonic() < self._cooldown_until:
                raise GameCareerUnavailable("game_career_cooldown")
            live = self._worker is not None and self._worker.is_alive()
            if live and (not self._accepting or len(self._pending) >= self.MAX_PENDING):
                raise GameCareerUnavailable("game_career_busy")
            if live and self._requests + len(self._pending) >= self.MAX_REQUESTS:
                raise GameCareerUnavailable("game_career_session_limit")
            self._pending.append(request)
            if not live:
                self._stop = False
                self._accepting = True
                self._requests = 0
                self._worker = threading.Thread(target=self._run, name="NetEaseCareer", daemon=True)
                self._worker.start()
            self._condition.notify_all()
        if not request.done.wait(self.REQUEST_WAIT_SECONDS):
            error = None
            with self._condition:
                if not request.done.is_set():
                    request.cancelled = True
                    error = GameCareerUnavailable("game_career_timeout")
                    self._failure(error)
                    self._stop = True
                    connection = self._socket
                    self._complete(request, error=error)
                    self._condition.notify_all()
            if error is not None:
                self._shutdown(connection)
                raise error
        if request.error is not None:
            raise request.error
        if request.result is None:
            raise GameCareerUnavailable()
        return dict(request.result)

    def _serve(self, connection, transport, zone, methods):
        with self._condition:
            if self._stop:
                return {"career_verified": False}
            self._socket = connection
            self._connected = True  # This callback runs only after verified LOGIN.
            self._last_error = None
            self._stage = "career"
            self._failure_kind = None
        verified = False
        for _ in range(self.MAX_REQUESTS):
            with self._condition:
                ready = self._condition.wait_for(lambda: self._stop or self._pending, timeout=self.IDLE_SECONDS)
                if not ready or self._stop:
                    self._accepting = False
                    break
                request = self._pending.popleft()
                self._active = request
                self._requests += 1
            try:
                projected = None

                def receive_verified(data):
                    nonlocal projected
                    if projected is not None:
                        raise GameCareerUnavailable("game_career_shape_changed")
                    projected = _project_profile(data, request.uid)

                report = career.read_one_career(connection, transport, zone, methods,
                    request.uid, request.season, on_verified=receive_verified, own_uid=self.own_uid)
                if report.get("career_verified") is not True or projected is None:
                    raise GameCareerUnavailable()
                with self._condition:
                    if not self._stop and not request.cancelled:
                        self._complete(request, result=projected)
                        verified = True
            except Exception as exc:
                # No arbitrary exception text, raw report or response is ever
                # exported, including unexpected library errors.
                error = exc if isinstance(exc, GameCareerUnavailable) else GameCareerUnavailable()
                with self._condition:
                    if not self._stop:
                        self._failure(error)
                        self._complete(request, error=error)
                    self._accepting = False
                break
            finally:
                with self._condition:
                    self._active = None
        with self._condition:
            self._accepting = False
        return {"career_verified": verified}

    def _run(self):
        error = GameCareerUnavailable("game_career_session_unavailable")
        try:
            with self._condition:
                if self._stop:
                    return
            self._set_stage("installed_configuration" if self._configuration_provider is None else "configuration")
            configuration = (load_public_configuration(archive=self.archive)
                             if self._configuration_provider is None else self._configuration_provider())
            self._set_stage("career_methods")
            methods = (career.load_career_methods(self.archive)
                       if self._career_methods_provider is None else self._career_methods_provider())
            with self._condition:
                if self._stop:
                    return
            self._set_stage("own_sdk_login" if self._own_credentials_provider is None else "own_credentials")
            inputs = (login.load_own_login_inputs()
                      if self._own_credentials_provider is None else self._own_credentials_provider())
            with self._condition:
                if self._stop:
                    return
            self._set_stage("advertised_server")
            server = login.advertised_server() if self._server_provider is None else self._server_provider()
            with self._condition:
                if self._stop:
                    return
            self._set_stage("authentication")
            report = login.check_login(configuration, server, inputs=inputs,
                expected_uid=self.own_uid, enter=True, connect=self._connect,
                after_login=lambda connection, transport, zone: self._serve(connection, transport, zone, methods))
            with self._condition:
                if not self._stop and report.get("game_login_verified") is not True:
                    error = _session_error(report.get("outcome"))
                    self._failure(error)
        except Exception as exc:
            error = _session_error(exc)
            with self._condition:
                self._failure_kind = ("dependency_missing" if isinstance(exc, ImportError) else
                    "local_file_unavailable" if isinstance(exc, OSError) else
                    "validation_failed" if isinstance(exc, ValueError) else "internal_error")
                if not self._stop:
                    self._failure(error)
        finally:
            with self._condition:
                self._connected = False
                self._accepting = False
                self._socket = None
                if self._stop:
                    error = GameCareerUnavailable("game_career_closed")
                elif self._last_error:
                    error = GameCareerUnavailable(self._last_error)
                else:
                    error = GameCareerUnavailable("game_career_session_limit")
                if self._active is not None:
                    self._complete(self._active, error=error)
                    self._active = None
                while self._pending:
                    self._complete(self._pending.popleft(), error=error)
                self._condition.notify_all()

    def close(self):
        """Cancel this session; a later read may start one after its worker exits."""
        with self._condition:
            self._stop = True
            self._accepting = False
            self._connected = False
            error = GameCareerUnavailable("game_career_closed")
            if self._active is not None:
                self._active.cancelled = True
                self._complete(self._active, error=error)
            while self._pending:
                pending = self._pending.popleft()
                pending.cancelled = True
                self._complete(pending, error=error)
            connection, worker = self._socket, self._worker
            self._condition.notify_all()
        self._shutdown(connection)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.0)
