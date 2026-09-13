"""User-driven official-store sign-in in an existing local pywebview GUI.

No browser JS API, HTTP handoff, credential files, sign-in automation, or game
request is provided here. The callback owns any accepted bundle; it must copy
what it needs and return True only while its vault is still unlocked. Receiving
SDK data is NOT proof of the selected game UID (normal AUTH verifies that later).

Reviewed installed pywebview 6.2.1: __init__.py:create_window creates a child
from a worker after the main-thread GUI loop starts; window.py:get_current_url,
evaluate_js and destroy use readiness events and backend GUI dispatch. Its
WebView2 backend may open OAuth popups in the external browser by default. This
module does not alter popup/OAuth behavior or claim a popup login was verified.
The application must keep pywebview's private_mode=True and debug=False.
"""
from __future__ import annotations

import math
import threading
from urllib.parse import urlsplit

from web_sdk_auth_probe import EXPECTED_OWNER_UID, SDK_FIELDS, build_web_login_inputs
from web_sdk_history_login import ACCOUNT_FIELDS, CONTEXT_FIELDS, build_history_login_inputs


OFFICIAL_URL = "https://pay.neteasegames.com/marvelrivals/topup"
OUTCOMES = frozenset({
    "local_login_idle", "local_login_waiting", "local_login_waiting_for_store",
    "local_login_store_not_ready", "local_login_succeeded", "local_login_cancelled",
    "local_login_timed_out", "local_login_vault_locked", "local_login_declined",
    "local_login_callback_failed", "local_login_window_failed", "local_login_script_failed",
    "local_login_bundle_invalid", "local_login_accounts_ambiguous",
})
_TERMINAL = OUTCOMES - {
    "local_login_idle", "local_login_waiting", "local_login_waiting_for_store",
    "local_login_store_not_ready",
}
_BUNDLE_FIELDS = frozenset({"selected_uid", "sauth", "saved_account", "mpay_context", "wrapper_udid"})

# This expression runs only in the top-level official page, not its Steam
# iframe. The in-page check closes the navigation race after get_current_url.
# Known keys are parsed in-page; no raw localStorage snapshot crosses to Python.
_READ_BUNDLE_JS = r"""(() => {
  if (location.origin !== 'https://pay.neteasegames.com' ||
      location.pathname !== '/marvelrivals/topup') return {state: 'wrong_origin'};
  try {
    const read = key => {
      const raw = localStorage.getItem(key);
      if (raw === null || raw.length > 262144) return null;
      return JSON.parse(raw);
    };
    const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
    const accountId = value => {
      if (typeof value === 'string') {
        if (!/^[1-9][0-9]{0,15}$/.test(value)) return null;
        const numeric = Number(value);
        return Number.isSafeInteger(numeric) && String(numeric) === value ? numeric : null;
      }
      return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : null;
    };
    const allSauth = read('netease_global_sauth_json');
    let source = object(allSauth) ? allSauth['x20naxx2gb'] : null;
    if (typeof source === 'string') {
      if (source.length > 131072) return {state: 'invalid'};
      source = JSON.parse(source);
    }
    const accountMap = read('mpay_h5sdk_os_account_list_map');
    const accounts = object(accountMap) ? accountMap['502'] : null;
    if (!object(source) || !Array.isArray(accounts)) return {state: 'pending'};
    if (accounts.length > 64) return {state: 'invalid'};
    const fields = ['gameid', 'platform', 'login_channel', 'app_channel', 'sdk_version',
                    'sdkuid', 'sessionid', 'deviceid', 'udid', 'client_login_sn'];
    const sauth = {};
    for (const key of fields) {
      if (typeof source[key] !== 'string' || source[key].length > 32768)
        return {state: 'pending'};
      sauth[key] = source[key];
    }
    if (!/^[1-9][0-9]{0,15}$/.test(sauth.sdkuid)) return {state: 'invalid'};
    const matches = accounts.filter(a => object(a) && a.method === 'steam' &&
      a.clientPlatform === 'p' && accountId(a.userId) !== null &&
      String(accountId(a.userId)) === sauth.sdkuid);
    if (matches.length === 0) return {state: 'pending'};
    if (matches.length !== 1) return {state: 'ambiguous'};
    const account = matches[0];
    if ((typeof account.deviceId !== 'string' &&
         !(Number.isSafeInteger(account.deviceId) && account.deviceId > 0)) ||
        String(account.deviceId) !== sauth.deviceid) return {state: 'invalid'};
    if (typeof account.refreshToken !== 'string' || !account.refreshToken ||
        account.refreshToken.length > 32768) return {state: 'pending'};
    const ci = read('UDID'), deviceId = read('DEVICE_ID'), wrapperUdid = read('UNISDK_UDID');
    if ([ci, deviceId, wrapperUdid].some(v => typeof v !== 'string' || !v || v.length > 512))
      return {state: 'pending'};
    if (wrapperUdid !== sauth.udid) return {state: 'invalid'};
    const persisted = read('persist:gameclub');
    if (!object(persisted) || typeof persisted.appConfig !== 'string' ||
        persisted.appConfig.length > 65536) return {state: 'pending'};
    const config = JSON.parse(persisted.appConfig);
    if (!object(config) || typeof config.lan !== 'string' || config.lan.length > 40)
      return {state: 'pending'};
    const bundle = {
      selected_uid: 1370598422, sauth,
      saved_account: {userId: accountId(account.userId), refreshToken: account.refreshToken,
                     method: account.method, clientPlatform: account.clientPlatform,
                     deviceId: account.deviceId},
      mpay_context: {game_id: '502', cp: 'w', cv: '3.1.5', gv: '1.0.0',
                     app_channel: 'netease.allysdk_global', debug_mode: '0',
                     ci, lang: config.lan.replace('-', '_'), device_id: deviceId},
      wrapper_udid: wrapperUdid
    };
    if (JSON.stringify(bundle).length > 98304) return {state: 'invalid'};
    return {state: 'ready', bundle};
  } catch (_) { return {state: 'pending'}; }
})()"""


def _official_url(value):
    if type(value) is not str or len(value) > 8192:
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and url.netloc == "pay.neteasegames.com"
                and url.path == "/marvelrivals/topup")
    except ValueError:
        return False


def _validated_bundle(value):
    """Copy exactly the projected contract; never retain an untrusted result."""
    if (type(value) is not dict or value.keys() != _BUNDLE_FIELDS
            or type(value["selected_uid"]) is not int or value["selected_uid"] != EXPECTED_OWNER_UID
            or type(value["sauth"]) is not dict or value["sauth"].keys() != SDK_FIELDS
            or type(value["saved_account"]) is not dict or value["saved_account"].keys() != ACCOUNT_FIELDS
            or type(value["mpay_context"]) is not dict or value["mpay_context"].keys() != CONTEXT_FIELDS
            or type(value["wrapper_udid"]) is not str
            or not value["wrapper_udid"].strip()
            or value["wrapper_udid"] != value["sauth"].get("udid")):
        raise ValueError("local_login_bundle_invalid")
    sauth = value["sauth"].copy()
    account = value["saved_account"].copy()
    context = value["mpay_context"].copy()
    try:
        build_web_login_inputs(sauth)
        inputs = build_history_login_inputs(sauth, account, context)
        if inputs.saved_device_id != sauth["deviceid"]:
            raise ValueError("local_login_bundle_invalid")
    except (ValueError, TypeError, RecursionError):
        raise ValueError("local_login_bundle_invalid") from None
    return {"selected_uid": EXPECTED_OWNER_UID, "sauth": sauth,
            "saved_account": account, "mpay_context": context,
            "wrapper_udid": value["wrapper_udid"]}


class LocalNativeLogin:
    """One cancellable local child window; no credential-bearing public API.

    start() is nonblocking and requires an already running pywebview GUI.
    on_bundle is a short, trusted Python callback. It must atomically recheck
    vault activity when accepting; False/None declines the handoff. cancel()
    cannot revoke a bundle a callback has already accepted. A stuck native GUI
    call may outlive cancellation on a daemon, but can never deliver afterward.
    """

    def __init__(self, create_window, *, on_bundle, is_active,
                 timeout_seconds=300, poll_interval=0.5):
        if not all(callable(v) for v in (create_window, on_bundle, is_active)):
            raise ValueError("local_login_configuration_invalid")
        for value, lower, upper in ((timeout_seconds, 0, 300), (poll_interval, 0, 5)):
            if type(value) not in (int, float) or not math.isfinite(value) or not lower < value <= upper:
                raise ValueError("local_login_configuration_invalid")
        self._create_window = create_window
        self._on_bundle = on_bundle
        self._is_active = is_active
        self._timeout = float(timeout_seconds)
        self._interval = float(poll_interval)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._window = None
        self._outcome = "local_login_idle"
        self._delivered = False
        self._callback_attempted = False

    def status(self):
        with self._lock:
            return {"outcome": self._outcome,
                    "running": self._outcome not in _TERMINAL and self._outcome != "local_login_idle",
                    "callback_attempted": self._callback_attempted,
                    "bundle_delivered": self._delivered,
                    "owner_authentication_verified": False}

    def start(self):
        with self._lock:
            if self._outcome != "local_login_idle":
                return self.status()
            self._outcome = "local_login_waiting"
            if not self._active():
                return self.status()
            threading.Thread(target=self._deadline, name="local-store-login-deadline", daemon=True).start()
            threading.Thread(target=self._run, name="local-store-login", daemon=True).start()
            return self.status()

    def cancel(self):
        self._finish("local_login_cancelled")
        return self.status()

    close = cancel

    def _active(self):
        with self._lock:
            if self._stop.is_set():
                return False
            try:
                active = self._is_active() is True
            except Exception:
                active = False
            if not active:
                self._finish("local_login_vault_locked")
            return active

    def _set_waiting(self, outcome):
        with self._lock:
            if not self._stop.is_set():
                self._outcome = outcome

    def _deadline(self):
        if not self._stop.wait(self._timeout):
            self._finish("local_login_timed_out")

    @staticmethod
    def _destroy(window):
        try:
            window.destroy()
        except Exception:
            pass  # Never expose native exceptions, URLs, or page data.

    def _on_closed(self):
        self._finish("local_login_cancelled", destroy=False)

    def _finish(self, outcome, *, delivered=False, destroy=True):
        with self._lock:
            if self._stop.is_set():
                return
            self._outcome = outcome
            self._delivered = delivered
            self._stop.set()
            window, self._window = self._window, None
            self._create_window = self._on_bundle = self._is_active = None
        if window is not None:
            try:
                window.events.closed -= self._on_closed
            except Exception:
                pass
            if destroy:
                threading.Thread(target=self._destroy, args=(window,),
                                 name="local-store-login-close", daemon=True).start()

    def _handoff(self, bundle):
        # Serialize acceptance with cancellation; the callback must be short.
        with self._lock:
            if not self._active() or self._callback_attempted:
                return
            self._callback_attempted = True
            try:
                accepted = self._on_bundle(bundle) is True
            except Exception:
                self._finish("local_login_callback_failed")
                return
            self._finish("local_login_succeeded" if accepted else "local_login_declined", delivered=accepted)

    def _run(self):
        window = result = bundle = None
        try:
            with self._lock:
                if self._stop.is_set():
                    return
                factory = self._create_window
            try:
                window = factory("Connect Marvel Rivals — official sign-in", OFFICIAL_URL,
                                 width=1060, height=820, min_size=(760, 600), js_api=None)
                factory = None
                if window is None:
                    raise ValueError("local_login_window_failed")
                with self._lock:
                    if self._stop.is_set():
                        self._destroy(window)
                        return
                    self._window = window
                    window.events.closed += self._on_closed
            except Exception:
                self._finish("local_login_window_failed")
                return
            while not self._stop.is_set():
                if not self._active():
                    return
                try:
                    if window.events.closed.is_set():
                        self._on_closed()
                        return
                    if not window.events.loaded.is_set():
                        self._stop.wait(self._interval)
                        continue
                    if not _official_url(window.get_current_url()):
                        self._set_waiting("local_login_waiting_for_store")
                    else:
                        if not self._active():
                            return
                        result = window.evaluate_js(_READ_BUNDLE_JS)
                        if self._stop.is_set() or not self._active():
                            return
                        # Reject any cross-origin result after a navigation,
                        # even if the original in-page guard already passed.
                        if not _official_url(window.get_current_url()):
                            result = None
                            self._set_waiting("local_login_waiting_for_store")
                            continue
                        if type(result) is not dict or type(result.get("state")) is not str:
                            self._finish("local_login_script_failed")
                            return
                        state = result["state"]
                        if state == "ready" and result.keys() == {"state", "bundle"}:
                            try:
                                bundle = _validated_bundle(result["bundle"])
                            except (ValueError, TypeError, RecursionError):
                                self._finish("local_login_bundle_invalid")
                                return
                            result = None
                            self._handoff(bundle)
                            return
                        if result.keys() != {"state"} or state not in {"pending", "wrong_origin", "invalid", "ambiguous"}:
                            self._finish("local_login_script_failed")
                            return
                        if state in {"invalid", "ambiguous"}:
                            self._finish("local_login_accounts_ambiguous" if state == "ambiguous"
                                         else "local_login_bundle_invalid")
                            return
                        self._set_waiting("local_login_waiting_for_store" if state == "wrong_origin"
                                          else "local_login_store_not_ready")
                except Exception:
                    self._finish("local_login_script_failed")
                    return
                finally:
                    result = bundle = None
                self._stop.wait(self._interval)
        finally:
            window = result = bundle = None
