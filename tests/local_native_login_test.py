"""Synthetic child windows and isolated JS fixtures; no browser or network."""
import copy
import io
import json
import shutil
import subprocess
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock

import local_native_login as login
from web_sdk_history_login_test import account, context, previous


def bundle():
    sauth = previous()
    saved = account()
    saved["deviceId"] = sauth["deviceid"]
    return {"selected_uid": 1370598422, "sauth": sauth, "saved_account": saved,
            "mpay_context": context(), "wrapper_udid": sauth["udid"]}


class Event:
    def __init__(self, set_=False):
        self._event = threading.Event()
        self.callbacks = []
        if set_:
            self._event.set()

    def is_set(self):
        return self._event.is_set()

    def set(self):
        self._event.set()
        for callback in list(self.callbacks):
            callback()

    def __iadd__(self, callback):
        self.callbacks.append(callback)
        return self

    def __isub__(self, callback):
        self.callbacks.remove(callback)
        return self


class Window:
    def __init__(self, result=None, url=login.OFFICIAL_URL, loaded=True):
        self.events = SimpleNamespace(loaded=Event(loaded), closed=Event())
        self.url = url
        self.result = {"state": "pending"} if result is None else result
        self.scripts = []
        self.destroyed = threading.Event()

    def get_current_url(self):
        return self.url

    def evaluate_js(self, script):
        self.scripts.append(script)
        if isinstance(self.result, Exception):
            raise self.result
        return copy.deepcopy(self.result)

    def destroy(self):
        self.destroyed.set()
        self.events.closed.set()


def wait_for(controller, predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status = controller.status()
        if predicate(status):
            return status
        time.sleep(0.005)
    raise AssertionError(f"Controller did not reach expected fixed state: {controller.status()}")


class LocalLoginTest(unittest.TestCase):
    def controller(self, window=None, callback=None, active=None, **options):
        window = Window() if window is None else window
        factory = Mock(return_value=window)
        callback = Mock(return_value=True) if callback is None else callback
        active = Mock(return_value=True) if active is None else active
        controller = login.LocalNativeLogin(factory, on_bundle=callback, is_active=active,
                                            poll_interval=0.01, **options)
        self.addCleanup(controller.cancel)
        return controller, window, factory, callback

    def test_exact_handoff_on_worker_once_closes_and_clears_owned_references(self):
        value = bundle()
        received = []
        callback = Mock(side_effect=lambda item: received.append(copy.deepcopy(item)) or True)
        controller, window, factory, _ = self.controller(Window({"state": "ready", "bundle": value}), callback)
        creation_threads = []
        factory.side_effect = lambda *a, **k: creation_threads.append(threading.current_thread().name) or window
        controller.start()
        result = wait_for(controller, lambda s: not s["running"])
        self.assertEqual(result["outcome"], "local_login_succeeded")
        self.assertTrue(result["bundle_delivered"])
        self.assertFalse(result["owner_authentication_verified"])
        self.assertEqual(received, [value])
        self.assertIsNot(callback.call_args.args[0], value)
        self.assertNotEqual(creation_threads, ["MainThread"])
        self.assertEqual(factory.call_args.args[1], login.OFFICIAL_URL)
        self.assertIsNone(factory.call_args.kwargs["js_api"])
        self.assertEqual(len(window.scripts), 1)
        self.assertTrue(window.destroyed.wait(1))
        self.assertEqual(window.events.closed.callbacks, [])
        for key in ("_window", "_on_bundle", "_is_active", "_create_window"):
            self.assertIsNone(getattr(controller, key))
        controller.start()
        controller.cancel()
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(factory.call_count, 1)
        self.assertNotIn(value["sauth"]["sessionid"], repr(controller) + json.dumps(result))

    def test_vault_locked_before_start_never_opens(self):
        controller, _, factory, callback = self.controller(active=Mock(return_value=False))
        self.assertEqual(controller.start()["outcome"], "local_login_vault_locked")
        factory.assert_not_called()
        callback.assert_not_called()

    def test_vault_lock_after_read_drops_bundle(self):
        active = [True]
        window = Window({"state": "ready", "bundle": bundle()})
        original = window.evaluate_js
        def evaluate(script):
            result = original(script)
            active[0] = False
            return result
        window.evaluate_js = evaluate
        controller, _, _, callback = self.controller(window, active=lambda: active[0])
        controller.start()
        result = wait_for(controller, lambda s: not s["running"])
        self.assertEqual(result["outcome"], "local_login_vault_locked")
        callback.assert_not_called()
        self.assertTrue(window.destroyed.wait(1))

    def test_wrong_origin_never_evaluated_then_official_return_can_complete(self):
        controller, window, _, callback = self.controller(Window({"state": "ready", "bundle": bundle()},
                                                               url="https://steamcommunity.com/openid/login"))
        controller.start()
        wait_for(controller, lambda s: s["outcome"] == "local_login_waiting_for_store")
        self.assertEqual(window.scripts, [])
        callback.assert_not_called()
        window.url = login.OFFICIAL_URL
        result = wait_for(controller, lambda s: not s["running"])
        self.assertEqual(result["outcome"], "local_login_succeeded")

    def test_navigation_between_checks_discards_ready_result(self):
        window = Window({"state": "ready", "bundle": bundle()})
        original = window.evaluate_js
        def evaluate(script):
            value = original(script)
            window.url = "https://example.invalid/"
            return value
        window.evaluate_js = evaluate
        controller, _, _, callback = self.controller(window)
        controller.start()
        wait_for(controller, lambda s: s["outcome"] == "local_login_waiting_for_store")
        callback.assert_not_called()
        controller.cancel()

    def test_in_page_origin_guard_can_decline_after_native_check(self):
        controller, _, _, callback = self.controller(Window({"state": "wrong_origin"}))
        controller.start()
        wait_for(controller, lambda s: s["outcome"] == "local_login_waiting_for_store")
        callback.assert_not_called()

    def test_loaded_event_prevents_early_script_or_blocking_url_query(self):
        window = Window({"state": "ready", "bundle": bundle()}, loaded=False)
        window.get_current_url = Mock(return_value=login.OFFICIAL_URL)
        controller, _, _, callback = self.controller(window)
        controller.start()
        time.sleep(0.03)
        window.get_current_url.assert_not_called()
        self.assertEqual(window.scripts, [])
        window.events.loaded.set()
        self.assertEqual(wait_for(controller, lambda s: not s["running"])["outcome"], "local_login_succeeded")

    def test_cancel_is_terminal_and_closes_child(self):
        controller, window, _, callback = self.controller()
        controller.start()
        wait_for(controller, lambda s: s["outcome"] == "local_login_store_not_ready")
        self.assertEqual(controller.cancel()["outcome"], "local_login_cancelled")
        self.assertTrue(window.destroyed.wait(1))
        callback.assert_not_called()

    def test_user_closing_child_stops_polling(self):
        controller, window, _, callback = self.controller()
        controller.start()
        wait_for(controller, lambda s: s["outcome"] == "local_login_store_not_ready")
        window.events.closed.set()
        self.assertEqual(controller.status()["outcome"], "local_login_cancelled")
        callback.assert_not_called()

    def test_deadline_closes_pending_window(self):
        controller, window, _, callback = self.controller(timeout_seconds=0.06)
        controller.start()
        self.assertEqual(wait_for(controller, lambda s: not s["running"])["outcome"], "local_login_timed_out")
        self.assertTrue(window.destroyed.wait(1))
        callback.assert_not_called()

    def test_cancel_during_native_call_discards_late_secret_result(self):
        entered, release = threading.Event(), threading.Event()
        window = Window()
        def evaluate(script):
            entered.set()
            release.wait(2)
            return {"state": "ready", "bundle": bundle()}
        window.evaluate_js = evaluate
        controller, _, _, callback = self.controller(window)
        controller.start()
        self.assertTrue(entered.wait(1))
        self.assertEqual(controller.cancel()["outcome"], "local_login_cancelled")
        release.set()
        time.sleep(0.03)
        callback.assert_not_called()

    def test_timeout_remains_bounded_if_native_evaluation_stalls(self):
        entered, release = threading.Event(), threading.Event()
        window = Window()
        def evaluate(script):
            entered.set()
            release.wait(2)
            return {"state": "ready", "bundle": bundle()}
        window.evaluate_js = evaluate
        controller, _, _, callback = self.controller(window, timeout_seconds=0.06)
        controller.start()
        self.assertTrue(entered.wait(1))
        self.assertEqual(wait_for(controller, lambda s: not s["running"])["outcome"], "local_login_timed_out")
        self.assertTrue(window.destroyed.wait(1))
        release.set()
        time.sleep(0.02)
        callback.assert_not_called()

    def test_cancel_during_url_query_never_starts_extraction(self):
        entered, release = threading.Event(), threading.Event()
        window = Window({"state": "ready", "bundle": bundle()})
        def get_url():
            entered.set()
            release.wait(2)
            return login.OFFICIAL_URL
        window.get_current_url = get_url
        controller, _, _, callback = self.controller(window)
        controller.start()
        self.assertTrue(entered.wait(1))
        controller.cancel()
        release.set()
        time.sleep(0.03)
        self.assertEqual(window.scripts, [])
        callback.assert_not_called()

    def test_late_window_creation_after_cancellation_is_closed_without_reading(self):
        entered, release = threading.Event(), threading.Event()
        controller, window, factory, callback = self.controller(Window({"state": "ready", "bundle": bundle()}))
        def create(*args, **kwargs):
            entered.set()
            release.wait(2)
            return window
        factory.side_effect = create
        controller.start()
        self.assertTrue(entered.wait(1))
        controller.cancel()
        release.set()
        self.assertTrue(window.destroyed.wait(1))
        self.assertEqual(window.scripts, [])
        callback.assert_not_called()

    def test_callback_declines_or_raises_with_fixed_status_no_output(self):
        for response, outcome in [(False, "local_login_declined"), (None, "local_login_declined"),
                                  ("truthy", "local_login_declined"),
                                  (RuntimeError("PRIVATE_CALLBACK_DETAIL"), "local_login_callback_failed")]:
            with self.subTest(outcome=outcome):
                callback = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
                controller, window, _, _ = self.controller(Window({"state": "ready", "bundle": bundle()}), callback)
                output = io.StringIO()
                with redirect_stdout(output), redirect_stderr(output):
                    controller.start()
                    result = wait_for(controller, lambda s: not s["running"])
                self.assertEqual(result["outcome"], outcome)
                self.assertFalse(result["bundle_delivered"])
                self.assertEqual(callback.call_count, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertTrue(window.destroyed.wait(1))

    def test_script_failure_never_exports_exception_or_data(self):
        controller, _, _, callback = self.controller(Window(RuntimeError("PRIVATE_SCRIPT_DETAIL")))
        controller.start()
        status = wait_for(controller, lambda s: not s["running"])
        self.assertEqual(status["outcome"], "local_login_script_failed")
        self.assertNotIn("PRIVATE", json.dumps(status))
        callback.assert_not_called()

    def test_creation_failure_is_redacted(self):
        controller, _, factory, callback = self.controller()
        factory.side_effect = RuntimeError("PRIVATE_BACKEND_DETAIL")
        controller.start()
        self.assertEqual(wait_for(controller, lambda s: not s["running"])["outcome"], "local_login_window_failed")
        callback.assert_not_called()

    def test_invalid_contract_is_rejected_before_callback(self):
        cases = []
        for path, value in [(('selected_uid',), 1822797559), (('wrapper_udid',), 'other'),
                            (('saved_account', 'userId'), 999), (('saved_account', 'deviceId'), 'other'),
                            (('saved_account', 'method'), 'epic'), (('mpay_context', 'cp'), 'x')]:
            item = bundle()
            if len(path) == 1:
                item[path[0]] = value
            else:
                item[path[0]][path[1]] = value
            cases.append(item)
        cases.append(dict(bundle(), raw_storage="PRIVATE"))
        item = bundle()
        item["sauth"]["refresh_token"] = "PRIVATE"
        cases.append(item)
        item = bundle()
        item["wrapper_udid"] = item["sauth"]["udid"] = ""
        cases.append(item)
        for value in cases:
            with self.subTest(value_type=type(value).__name__):
                controller, _, _, callback = self.controller(Window({"state": "ready", "bundle": value}))
                controller.start()
                self.assertEqual(wait_for(controller, lambda s: not s["running"])["outcome"], "local_login_bundle_invalid")
                callback.assert_not_called()

    def test_fixed_js_status_allowlist(self):
        for value, expected in [({"state": "invalid"}, "local_login_bundle_invalid"),
                                ({"state": "ambiguous"}, "local_login_accounts_ambiguous"),
                                ({"state": "PRIVATE"}, "local_login_script_failed"),
                                ({"state": "pending", "raw": "PRIVATE"}, "local_login_script_failed"),
                                ([], "local_login_script_failed")]:
            controller, _, _, callback = self.controller(Window(value))
            controller.start()
            self.assertEqual(wait_for(controller, lambda s: not s["running"])["outcome"], expected)
            callback.assert_not_called()

    def test_exact_origin_path_gate(self):
        for value in (None, 1, "https://pay.neteasegames.com.evil.invalid/marvelrivals/topup",
                      "https://pay.neteasegames.com@evil.invalid/marvelrivals/topup",
                      "http://pay.neteasegames.com/marvelrivals/topup",
                      "https://pay.neteasegames.com/other", "https://pay.neteasegames.com:444/marvelrivals/topup",
                      "https://pay.neteasegames.com/marvelrivals/topup/"):
            self.assertFalse(login._official_url(value))
        self.assertTrue(login._official_url(login.OFFICIAL_URL + "?return=1#signin"))

    def test_invalid_configuration(self):
        for kwargs in ({"timeout_seconds": 0}, {"timeout_seconds": 301}, {"timeout_seconds": float('nan')},
                       {"poll_interval": True}, {"poll_interval": 0}):
            with self.assertRaisesRegex(ValueError, "^local_login_configuration_invalid$"):
                login.LocalNativeLogin(Mock(), on_bundle=Mock(), is_active=Mock(), **kwargs)


@unittest.skipUnless(shutil.which("node"), "Node optional: isolated JavaScript fixture runner")
class StoreProjectionJavaScriptTest(unittest.TestCase):
    def storage(self):
        value = bundle()
        return {"netease_global_sauth_json": json.dumps({"x20naxx2gb": dict(value["sauth"], extra="NEVER_EXPORT")}),
                "mpay_h5sdk_os_account_list_map": json.dumps({"502": [dict(value["saved_account"], displayName="NEVER_EXPORT")]}),
                "UDID": json.dumps(value["mpay_context"]["ci"]),
                "DEVICE_ID": json.dumps(value["mpay_context"]["device_id"]),
                "UNISDK_UDID": json.dumps(value["wrapper_udid"]),
                "persist:gameclub": json.dumps({"appConfig": json.dumps({"lan": "en-US", "extra": "NEVER_EXPORT"})})}

    def evaluate(self, storage, url=login.OFFICIAL_URL):
        runner = """const vm=require('node:vm'),fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));let reads=[];
const result=vm.runInNewContext(process.argv[1], {location:new URL(input.url),
localStorage:{getItem:key=>{reads.push(key);return input.storage[key]??null;}}}, {timeout:1000});
process.stdout.write(JSON.stringify({result,reads}));"""
        result = subprocess.run([shutil.which("node"), "-e", runner, login._READ_BUNDLE_JS],
                                input=json.dumps({"url": url, "storage": storage}), text=True,
                                capture_output=True, timeout=5, check=True)
        return json.loads(result.stdout)

    def test_exact_projection_and_language_without_unknown_fields(self):
        result = self.evaluate(self.storage())
        self.assertEqual(result["result"], {"state": "ready", "bundle": bundle()})
        self.assertNotIn("NEVER_EXPORT", json.dumps(result))
        self.assertEqual(set(result["reads"]), set(self.storage()))

    def test_nested_sauth_json_and_exact_decimal_account_id_are_supported(self):
        storage = self.storage()
        value = bundle()
        storage["netease_global_sauth_json"] = json.dumps({"x20naxx2gb": json.dumps(value["sauth"])})
        storage["mpay_h5sdk_os_account_list_map"] = json.dumps({
            "502": [dict(value["saved_account"], userId=str(value["saved_account"]["userId"]))]})
        result = self.evaluate(storage)["result"]
        self.assertEqual(result, {"state": "ready", "bundle": value})
        self.assertIs(type(result["bundle"]["saved_account"]["userId"]), int)
        self.assertEqual(login._validated_bundle(result["bundle"]), value)

    def test_nested_sauth_malformed_or_nonobject_never_exports(self):
        for source in ("not-json", "[]", "null", '"double-nested"', "x" * 131073):
            storage = self.storage()
            storage["netease_global_sauth_json"] = json.dumps({"x20naxx2gb": source})
            with self.subTest(source_kind=source[:12]):
                self.assertNotEqual(self.evaluate(storage)["result"].get("state"), "ready")

    def test_account_id_normalization_is_not_coercion_or_truncation(self):
        canonical = str(bundle()["saved_account"]["userId"])
        for invalid in ("0" + canonical, canonical + ".0", canonical + "x", " " + canonical,
                        "+" + canonical, "1e5", "9007199254740993", 9007199254740992,
                        123.5, True, None, "", "0", "-1"):
            storage = self.storage()
            value = bundle()
            # Make even the potentially coercible identity match the supplied
            # SAUTH text, so rejection cannot just be an unrelated UID mismatch.
            if isinstance(invalid, str):
                value["sauth"]["sdkuid"] = invalid
            elif type(invalid) in (int, float):
                value["sauth"]["sdkuid"] = str(invalid)
            storage["netease_global_sauth_json"] = json.dumps({"x20naxx2gb": value["sauth"]})
            storage["mpay_h5sdk_os_account_list_map"] = json.dumps({
                "502": [dict(value["saved_account"], userId=invalid)]})
            with self.subTest(id_kind=type(invalid).__name__):
                result = self.evaluate(storage)["result"]
                self.assertNotEqual(result.get("state"), "ready")
                self.assertNotIn("bundle", result)

    def test_duplicate_account_ids_across_supported_types_stay_ambiguous(self):
        storage = self.storage()
        value = bundle()["saved_account"]
        storage["mpay_h5sdk_os_account_list_map"] = json.dumps({
            "502": [value, dict(value, userId=str(value["userId"]))]})
        self.assertEqual(self.evaluate(storage)["result"], {"state": "ambiguous"})

    def test_wrong_origin_reads_no_storage(self):
        result = self.evaluate(self.storage(), "https://steamcommunity.com/openid/login")
        self.assertEqual(result, {"result": {"state": "wrong_origin"}, "reads": []})

    def test_missing_invalid_or_unreviewed_storage_cannot_export_partial_bundle(self):
        for key in self.storage():
            storage = self.storage()
            storage[key] = "not-json"
            with self.subTest(key=key):
                self.assertEqual(self.evaluate(storage)["result"], {"state": "pending"})
        storage = self.storage()
        saved = bundle()["saved_account"]
        storage["mpay_h5sdk_os_account_list_map"] = json.dumps({"502": [saved, saved]})
        self.assertEqual(self.evaluate(storage)["result"], {"state": "ambiguous"})

    def test_matching_sdk_and_device_binding_not_game_uid_guess(self):
        for change in ({"userId": 1822797559}, {"deviceId": "different-device"}, {"method": "epic"}):
            storage = self.storage()
            storage["mpay_h5sdk_os_account_list_map"] = json.dumps({"502": [dict(bundle()["saved_account"], **change)]})
            self.assertNotEqual(self.evaluate(storage)["result"].get("state"), "ready")


if __name__ == "__main__":
    unittest.main()
