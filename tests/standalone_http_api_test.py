"""Synthetic managers and loopback HTTP only; no real session or account calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
import io
import json
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import standalone_http_api as api
from standalone_session_manager import SessionLookupResult


NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
TOKEN = "synthetic-server-only-bearer"


def result(uid, **changes):
    values = {"outcome": "session_lookup_complete", "owner_authenticated": True,
              "profile": {"uid": str(uid), "name": "Fixture", "private": "PRIVATE_MANAGER_DATA"}}
    return SessionLookupResult(**(values | changes))


def project(profile, uid, fetched_at):
    if profile.get("uid") != str(uid):
        raise ValueError("PRIVATE_MISMATCH")
    return {"schemaVersion": 2, "source": "netease", "uid": str(uid),
            "fetchedAt": fetched_at.isoformat(), "nativeProfile": {"name": profile["name"]}}


class Manager:
    def __init__(self, callback=result):
        self.callback, self.calls = callback, []

    def lookup(self, uid):
        self.calls.append(uid)
        return self.callback(uid)


@contextmanager
def service_for(manager=None, **options):
    service = api.ProfileLookupService(manager or Manager(), projector=options.pop("projector", project),
                                       clock=options.pop("clock", lambda: NOW), **options)
    try:
        yield service
    finally:
        service.close()


@contextmanager
def server_for(service, **options):
    server = api.make_http_server(service, bearer_token=TOKEN, **options)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def request(server, path="/v1/players/123", *, method="GET", auth="Bearer " + TOKEN, headers=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=2)
    try:
        all_headers = {} if auth is None else {"Authorization": auth}
        all_headers.update(headers or {})
        connection.request(method, path, headers=all_headers)
        response = connection.getresponse()
        body = response.read()
        return response.status, dict(response.getheaders()), json.loads(body) if body else None
    finally:
        connection.close()


def raw_request(server, header_lines, *, first="GET /v1/players/123 HTTP/1.1"):
    with socket.create_connection(server.server_address, timeout=2) as conn:
        conn.sendall((first + "\r\nHost: localhost\r\n" + "\r\n".join(header_lines) + "\r\n\r\n").encode())
        response = http.client.HTTPResponse(conn)
        response.begin()
        return response.status, response.read()


def completed_job(service, lookup_id):
    """Wait for the completion callback before advancing a synthetic clock."""
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with service._lock:
            job = service._jobs.get(lookup_id)
            if job is not None and job.completed is not None:
                return job
        time.sleep(0.001)
    raise AssertionError("synthetic job callback did not finish")


class ProfileLookupServiceTest(unittest.TestCase):
    def test_exact_uid_validation_before_manager_and_fixed_errors(self):
        manager = Manager()
        with service_for(manager) as service:
            for uid in (True, False, "123", 1.0, 0, -1, 2**63, None):
                reply = service.lookup(uid)
                self.assertEqual(reply.status_code, 400)
                self.assertEqual(reply.body, {"error": {"code": "invalid_uid"}})
            for wait in (-1, 15.1, True, float("nan"), "1"):
                with self.assertRaisesRegex(ValueError, "lookup_wait_invalid"):
                    service.lookup(123, wait_seconds=wait)
        self.assertEqual(manager.calls, [])

    def test_resume_ids_are_strict_and_unknown_ids_never_submit(self):
        manager = Manager()
        with service_for(manager) as service:
            for lookup_id in (True, 1, b"a" * 32, "", "a" * 31, "a" * 33, "A" * 32,
                              "g" * 32, "a" * 32 + "\n", " " + "a" * 32):
                reply = service.lookup(123, lookup_id=lookup_id)
                self.assertEqual(reply.status_code, 400)
                self.assertEqual(reply.body, {"error": {"code": "invalid_lookup"}})
            reply = service.lookup(123, lookup_id="a" * 32)
            self.assertEqual(reply.status_code, 410)
            self.assertEqual(reply.body, {"error": {"code": "lookup_expired"}})
        self.assertEqual(manager.calls, [])

    def test_initial_and_resume_callers_coalesce_to_one_opaque_id(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(uid):
            entered.set()
            release.wait(2)
            return result(uid)
        manager = Manager(blocked)
        with service_for(manager) as service, ThreadPoolExecutor(max_workers=8) as callers:
            try:
                pending = service.lookup(123, wait_seconds=0)
                lookup_id = pending.body["lookupId"]
                self.assertRegex(lookup_id, r"\A[0-9a-f]{32}\Z")
                self.assertEqual(set(pending.body), {"error", "lookupId"})
                self.assertTrue(entered.wait(1))
                futures = [callers.submit(service.lookup, 123, wait_seconds=0,
                           **({"lookup_id": lookup_id} if n % 2 else {})) for n in range(8)]
                for future in futures:
                    reply = future.result(1)
                    self.assertEqual(reply, pending)
                self.assertEqual(len(service._jobs), 1)
                release.set()
                self.assertEqual(service.lookup(123, lookup_id=lookup_id).status_code, 200)
            finally:
                release.set()
        self.assertEqual(manager.calls, [123])

    def test_resume_preserves_original_failure_even_after_new_initial_success(self):
        release = threading.Event()
        calls = [0]
        def first_fails(uid):
            calls[0] += 1
            if calls[0] == 1:
                release.wait(2)
                raise RuntimeError("PRIVATE_REFRESH_FAILURE")
            return result(uid)
        manager = Manager(first_fails)
        with service_for(manager) as service:
            try:
                lookup_id = service.lookup(123, wait_seconds=0).body["lookupId"]
                release.set()
                original = service.lookup(123, lookup_id=lookup_id)
                self.assertEqual(original.status_code, 502)
                self.assertEqual(original.body, {"error": {"code": "lookup_failed"}})
                job = completed_job(service, lookup_id)
                self.assertIsNone(job.future)
                self.assertNotIn("PRIVATE", repr(job))
                self.assertNotIn("PRIVATE", job.completed.payload.decode())
                for _ in range(3):
                    self.assertEqual(service.lookup(123, lookup_id=lookup_id), original)
                self.assertEqual(manager.calls, [123])
                # Only an explicit fresh lookup may start another operation.
                self.assertEqual(service.lookup(123).status_code, 200)
                self.assertEqual(service.lookup(123, lookup_id=lookup_id), original)
                self.assertEqual(manager.calls, [123, 123])
            finally:
                release.set()

    def test_resume_preserves_fixed_unavailable_outcome_without_resubmission(self):
        release = threading.Event()
        def unavailable(uid):
            release.wait(2)
            return result(uid, outcome="session_recovery_required", profile=None)
        manager = Manager(unavailable)
        with service_for(manager) as service:
            try:
                lookup_id = service.lookup(123, wait_seconds=0).body["lookupId"]
                release.set()
                reply = service.lookup(123, lookup_id=lookup_id)
                self.assertEqual(reply.status_code, 503)
                self.assertEqual(reply.body, {"error": {"code": "lookup_unavailable"}})
                for _ in range(3):
                    self.assertEqual(service.lookup(123, lookup_id=lookup_id), reply)
            finally:
                release.set()
        self.assertEqual(manager.calls, [123])

    def test_job_ttl_starts_after_completion_and_outlives_success_cache(self):
        clock, release = [100.0], threading.Event()
        def blocked(uid):
            release.wait(2)
            return result(uid)
        manager = Manager(blocked)
        with service_for(manager, monotonic=lambda: clock[0]) as service:
            try:
                pending = service.lookup(123, wait_seconds=0)
                lookup_id = pending.body["lookupId"]
                clock[0] = 1000.0
                self.assertEqual(service.lookup(123, lookup_id=lookup_id, wait_seconds=0), pending)
                # A wrong target cannot observe or enqueue this owner's job.
                wrong = service.lookup(124, lookup_id=lookup_id, wait_seconds=0)
                self.assertEqual((wrong.status_code, wrong.body),
                                 (410, {"error": {"code": "lookup_expired"}}))
                release.set()
                original = service.lookup(123, lookup_id=lookup_id)
                self.assertEqual(original.status_code, 200)
                job = completed_job(service, lookup_id)
                self.assertEqual(job.expires_at, 1300.0)
                clock[0] = 1030.0
                reply = service.lookup(123, lookup_id=lookup_id)
                self.assertEqual(reply, original)
                self.assertNotIn(123, service._cache)
                reply.body["nativeProfile"]["name"] = "CHANGED_COPY"
                clock[0] = 1299.999
                self.assertEqual(service.lookup(123, lookup_id=lookup_id), original)
                clock[0] = 1300.0
                expired = service.lookup(123, lookup_id=lookup_id)
                self.assertEqual(expired.status_code, 410)
                self.assertEqual(expired.body, {"error": {"code": "lookup_expired"}})
                self.assertNotIn(lookup_id, service._jobs)
                self.assertEqual(manager.calls, [123])
            finally:
                release.set()

    def test_registry_is_bounded_to_100_and_evicts_completed_jobs_not_pending(self):
        release = threading.Event()
        def block_last(uid):
            if uid >= 100:
                release.wait(2)
            return result(uid)
        manager = Manager(block_last)
        ids = [f"{n:032x}" for n in range(1, 105)]
        with service_for(manager) as service, patch.object(api.uuid, "uuid4",
                side_effect=[SimpleNamespace(hex=value) for value in ids]):
            try:
                for uid in range(1, 100):
                    self.assertEqual(service.lookup(uid).status_code, 200)
                    completed_job(service, ids[uid - 1])
                pending_ids = []
                for uid in range(100, 104):
                    pending_ids.append(service.lookup(uid, wait_seconds=0).body["lookupId"])
                    self.assertLessEqual(len(service._jobs), 100)
                self.assertEqual(len(service._jobs), 100)
                self.assertTrue(all(service._jobs[key].completed is None for key in pending_ids))
                for lookup_id in ids[:3]:
                    self.assertNotIn(lookup_id, service._jobs)
                # Even if the short UID cache still contains this success, an
                # evicted ID never falls back to cache or starts another job.
                self.assertIn(1, service._cache)
                reply = service.lookup(1, lookup_id=ids[0])
                self.assertEqual(reply.status_code, 410)
                self.assertEqual(service.lookup(104, wait_seconds=0).body,
                                 {"error": {"code": "lookup_busy"}})
                self.assertEqual(len(service._pending), 4)
                release.set()
                for uid, lookup_id in zip(range(100, 104), pending_ids):
                    self.assertEqual(service.lookup(uid, lookup_id=lookup_id).status_code, 200)
            finally:
                release.set()
        self.assertEqual(manager.calls, list(range(1, 104)))

    def test_completed_failure_registry_expires_without_new_work(self):
        clock = [10.0]
        manager = Manager(lambda uid: result(uid, owner_authenticated=False))
        lookup_id = "a" * 32
        with service_for(manager, monotonic=lambda: clock[0]) as service, patch.object(
                api.uuid, "uuid4", return_value=SimpleNamespace(hex=lookup_id)):
            self.assertEqual(service.lookup(123).status_code, 502)
            completed_job(service, lookup_id)
            clock[0] = 309.999
            self.assertEqual(service.lookup(123, lookup_id=lookup_id).status_code, 502)
            clock[0] = 310.0
            self.assertEqual(service.lookup(123, lookup_id=lookup_id).status_code, 410)
            self.assertEqual(service._jobs, {})
        self.assertEqual(manager.calls, [123])

    def test_only_exact_authenticated_manager_result_enters_projector(self):
        outcomes = [SimpleNamespace(**result(123).__dict__), result(123, owner_authenticated=1),
                    result(123, owner_authenticated=False), result(123, profile=None),
                    result(123, outcome="PRIVATE_UNKNOWN_OUTCOME"), {"private": "PRIVATE_RAW"}]
        for outcome in outcomes:
            projector = Mock(side_effect=project)
            with service_for(Manager(lambda uid: outcome), projector=projector) as service:
                reply = service.lookup(123)
            self.assertEqual(reply.status_code, 502)
            self.assertEqual(reply.body, {"error": {"code": "lookup_failed"}})
            projector.assert_not_called()
        self.assertNotIn("PRIVATE", repr(result(123)))

    def test_known_recovery_states_are_fixed_503_and_never_claim_invalid_uid(self):
        for state in api._SESSION_UNAVAILABLE:
            with service_for(Manager(lambda uid: result(uid, outcome=state, profile=None))) as service:
                reply = service.lookup(123)
            self.assertEqual(reply.status_code, 503)
            self.assertEqual(reply.body, {"error": {"code": "lookup_unavailable"}})
            self.assertNotIn(state, json.dumps(reply.body))

    def test_exception_and_projector_failures_are_not_cached_or_exposed(self):
        def failed(uid):
            raise RuntimeError("PRIVATE_CREDENTIAL_EXCEPTION")
        manager = Manager(failed)
        with service_for(manager) as service:
            self.assertEqual(service.lookup(123).status_code, 502)
            self.assertEqual(service.lookup(123).status_code, 502)
        self.assertEqual(manager.calls, [123, 123])
        for projector in (lambda *_: {"schemaVersion": True}, lambda *_: ["PRIVATE_RAW"],
                          lambda p, uid, stamp: {"schemaVersion": 2, "uid": "456", "source": "netease"},
                          lambda p, uid, stamp: {**project(p, uid, stamp), "nativeProfile": float("nan")},
                          lambda p, uid, stamp: {**project(p, uid, stamp), "large": "x" * api.MAX_RESPONSE_BYTES}):
            with service_for(projector=projector) as service:
                reply = service.lookup(123)
            self.assertEqual(reply.status_code, 502)
            self.assertNotIn("PRIVATE", json.dumps(reply.body))

    def test_clock_must_be_aware_and_bad_clock_does_not_emit_exception_text(self):
        for clock in (lambda: datetime(2026, 1, 1), lambda: "PRIVATE_TIME"):
            with service_for(clock=clock) as service:
                self.assertEqual(service.lookup(123).status_code, 502)
        with service_for(monotonic=lambda: float("nan")) as service:
            self.assertEqual(service.lookup(123).status_code, 503)

    def test_same_uid_coalesces_while_distinct_uids_serialize_in_one_worker(self):
        entered, release = threading.Event(), threading.Event()
        active, maximum, guard = [0], [0], threading.Lock()
        def blocked(uid):
            with guard:
                active[0] += 1
                maximum[0] = max(maximum[0], active[0])
            if uid == 123:
                entered.set()
                release.wait(2)
            with guard:
                active[0] -= 1
            return result(uid)
        manager = Manager(blocked)
        try:
            with service_for(manager) as service:
                self.assertEqual(service.lookup(123, wait_seconds=0).body["error"]["code"], "lookup_pending")
                self.assertTrue(entered.wait(1))
                for _ in range(5):
                    self.assertEqual(service.lookup(123, wait_seconds=0).retry_after, 2)
                for uid in (124, 125, 126):
                    self.assertEqual(service.lookup(uid, wait_seconds=0).body["error"]["code"], "lookup_pending")
                overflow = service.lookup(127, wait_seconds=0)
                self.assertEqual(overflow.status_code, 503)
                self.assertEqual(overflow.body["error"]["code"], "lookup_busy")
                release.set()
                for uid in (123, 124, 125, 126):
                    self.assertEqual(service.lookup(uid).status_code, 200)
        finally:
            release.set()
        self.assertEqual(manager.calls, [123, 124, 125, 126])
        self.assertEqual(maximum[0], 1)

    def test_concurrent_same_uid_http_waiters_share_one_future(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(uid):
            entered.set()
            release.wait(2)
            return result(uid)
        manager = Manager(blocked)
        with service_for(manager) as service, ThreadPoolExecutor(max_workers=6) as callers:
            futures = [callers.submit(service.lookup, 123) for _ in range(6)]
            self.assertTrue(entered.wait(1))
            release.set()
            replies = [future.result(2) for future in futures]
        self.assertTrue(all(reply.status_code == 200 for reply in replies))
        self.assertEqual(manager.calls, [123])

    def test_cache_is_projected_copy_lru_100_and_expires_at_30_seconds(self):
        clock = [100.0]
        manager = Manager()
        with service_for(manager, monotonic=lambda: clock[0]) as service:
            reply = service.lookup(1)
            self.assertNotIn("PRIVATE", json.dumps(reply.body))
            reply.body["nativeProfile"]["name"] = "MUTATED"
            self.assertEqual(service.lookup(1).body["nativeProfile"]["name"], "Fixture")
            clock[0] = 129.999
            service.lookup(1)
            self.assertEqual(manager.calls, [1])
            clock[0] = 130
            service.lookup(1)
            self.assertEqual(manager.calls, [1, 1])
            for uid in range(2, 102):
                service.lookup(uid)
            self.assertLessEqual(len(service._cache), 100)
            calls = len(manager.calls)
            service.lookup(101)
            self.assertEqual(len(manager.calls), calls)
            service.lookup(1)
            self.assertEqual(len(manager.calls), calls + 1)

    def test_timeout_and_close_do_not_cancel_running_or_queued_rotation(self):
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        def blocked(uid):
            entered.set()
            release.wait(2)
            completed.set()
            return result(uid)
        manager = Manager(blocked)
        service = api.ProfileLookupService(manager, projector=project, clock=lambda: NOW)
        try:
            pending = service.lookup(123, wait_seconds=0.01)
            self.assertEqual(pending.status_code, 503)
            self.assertEqual(pending.body["error"]["code"], "lookup_pending")
            self.assertTrue(entered.wait(1))
            service.lookup(124, wait_seconds=0)
            service.close(wait=False)
            self.assertEqual(service.lookup(123).body["error"]["code"], "service_unavailable")
            release.set()
            self.assertTrue(completed.wait(1))
        finally:
            release.set()
            service.close()
        self.assertEqual(manager.calls, [123, 124])


class PrivateHTTPTest(unittest.TestCase):
    def test_real_default_projector_filters_internal_fields_before_loopback_response_and_cache(self):
        from tests.standalone_web_contract_test import bundle, UID
        source = bundle()
        source["private_token"] = "PRIVATE_RAW_TOKEN"
        source["identity"]["private"] = "PRIVATE_RAW_IDENTITY"
        manager = Manager(lambda uid: result(uid, profile=source))
        with service_for(manager, projector=api._project) as service, server_for(service) as server:
            status, _, body = request(server, f"/v1/players/{UID}")
            self.assertEqual(status, 200)
            self.assertEqual(body["nativeProfile"]["identity"]["uid"], str(UID))
            self.assertNotIn("PRIVATE", json.dumps(body))
            source["identity"]["in_game_name"] = "Changed raw object"
            self.assertEqual(request(server, f"/v1/players/{UID}")[2], body)
        self.assertEqual(manager.calls, [UID])
        source["identity_verified"] = False
        with service_for(manager, projector=api._project) as service, server_for(service) as server:
            status, _, body = request(server, f"/v1/players/{UID}")
            self.assertEqual(status, 502)
            self.assertEqual(body, {"error": {"code": "lookup_failed"}})

    def test_loopback_exact_schema_only_and_auth_required_for_profiles_and_health(self):
        manager = Manager()
        with service_for(manager) as service, server_for(service) as server:
            self.assertEqual(server.server_address[0], "127.0.0.1")
            for path in ("/v1/players/123", "/health"):
                for auth in (None, "Bearer wrong", "Basic " + TOKEN, "bearer " + TOKEN):
                    status, headers, body = request(server, path, auth=auth)
                    self.assertEqual(status, 401)
                    self.assertEqual(body, {"error": {"code": "unauthorized"}})
                    self.assertFalse(any(key.lower().startswith("access-control") for key in headers))
            self.assertEqual(manager.calls, [])
            status, headers, body = request(server)
            self.assertEqual(status, 200)
            self.assertEqual(body["schemaVersion"], 2)
            self.assertEqual(body["uid"], "123")
            self.assertNotIn("PRIVATE", json.dumps(body))
            self.assertNotIn(TOKEN, json.dumps(body))
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(request(server, "/health")[2], {"status": "ready"})

    def test_auth_uses_constant_time_digest_comparison(self):
        original = api.hmac.compare_digest
        with service_for() as service, server_for(service) as server, patch.object(api.hmac, "compare_digest", wraps=original) as compare:
            self.assertEqual(request(server)[0], 200)
            self.assertEqual(request(server, auth="Bearer wrong")[0], 401)
        self.assertEqual(compare.call_count, 2)
        for call in compare.call_args_list:
            self.assertEqual([len(arg) for arg in call.args], [32, 32])

    def test_bad_numeric_paths_never_call_manager_and_no_extra_endpoints(self):
        manager = Manager()
        with service_for(manager) as service, server_for(service) as server:
            for uid in ("0", "-1", "1.0", "1e3", "true", "9223372036854775808", "%31", "123?x=1", "123/", "١٢٣"):
                if not uid.isascii():
                    continue  # Raw non-ASCII request-target tested separately below.
                self.assertEqual(request(server, "/v1/players/" + uid)[0], 400)
            for path in ("/bootstrap", "/admin", "/accounts", "/v1/players", "/"):
                self.assertEqual(request(server, path)[0], 404)
            self.assertEqual(manager.calls, [])
            self.assertEqual(request(server, "/v1/players/9223372036854775807")[0], 200)

    def test_duplicate_auth_and_ambiguous_body_framing_are_rejected(self):
        manager = Manager()
        with service_for(manager) as service, server_for(service) as server:
            auth = "Authorization: Bearer " + TOKEN
            cases = [[auth, auth], [auth, "Content-Length: 0", "Content-Length: 0"],
                     [auth, "Transfer-Encoding: chunked"], [auth, "Transfer-Encoding: identity", "Content-Length: 0"],
                     [auth, "Content-Length: 1"], [auth, "Content-Length: -1"],
                     [auth, "Host: second"], [auth, "Content-Length: 00"],
                     [auth, "Expect: 100-continue", "Content-Length: 1"]]
            for headers in cases:
                status, body = raw_request(server, headers)
                self.assertEqual(status, 400)
                self.assertNotIn(TOKEN.encode(), body)
            self.assertEqual(manager.calls, [])

    def test_missing_host_and_oversized_headers_are_fixed_rejections(self):
        manager = Manager()
        with service_for(manager) as service, server_for(service) as server:
            auth = "Authorization: Bearer " + TOKEN
            status, _ = raw_request(server, [auth] + [f"X-{n}: value" for n in range(33)])
            self.assertEqual(status, 400)
            status, _ = raw_request(server, [auth, "X-Bound: " + "x" * api.MAX_HEADER_BYTES])
            self.assertEqual(status, 400)
            with socket.create_connection(server.server_address, timeout=2) as conn:
                conn.sendall(("GET /health HTTP/1.1\r\n" + auth + "\r\n\r\n").encode())
                response = http.client.HTTPResponse(conn)
                response.begin()
                self.assertEqual(response.status, 400)
                response.read()
            self.assertEqual(manager.calls, [])

    def test_methods_and_options_have_no_cors_or_bootstrap_behavior(self):
        manager = Manager()
        with service_for(manager) as service, server_for(service) as server:
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
                status, headers, body = request(server, method=method)
                self.assertEqual(status, 405)
                self.assertFalse(any(key.lower().startswith("access-control") for key in headers))
            self.assertEqual(manager.calls, [])

    def test_pending_http_returns_retry_without_cancelling_and_finished_result_is_cached(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(uid):
            entered.set()
            release.wait(2)
            return result(uid)
        manager = Manager(blocked)
        with service_for(manager) as service, server_for(service) as server:
            try:
                with patch.object(api, "MAX_WAIT_SECONDS", 0.01):
                    status, headers, body = request(server)
                self.assertEqual(status, 503)
                self.assertEqual(body["error"], {"code": "lookup_pending"})
                self.assertEqual(set(body), {"error", "lookupId"})
                self.assertRegex(body["lookupId"], r"\A[0-9a-f]{32}\Z")
                self.assertEqual(headers["Retry-After"], "2")
                self.assertTrue(entered.wait(1))
                release.set()
                self.assertEqual(request(server, "/v1/players/123?lookup=" + body["lookupId"])[0], 200)
                self.assertEqual(request(server)[0], 200)
            finally:
                release.set()
        self.assertEqual(manager.calls, [123])

    def test_polling_pending_failure_returns_same_result_and_never_rotates_again(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_failure(uid):
            entered.set()
            release.wait(2)
            raise RuntimeError("PRIVATE_REFRESH_FAILURE")
        manager = Manager(blocked_failure)
        with service_for(manager) as service, server_for(service) as server:
            try:
                with patch.object(api, "MAX_WAIT_SECONDS", 0.01):
                    status, headers, body = request(server)
                    self.assertEqual(status, 503)
                    self.assertEqual(headers["Retry-After"], "2")
                    lookup_id = body["lookupId"]
                    self.assertTrue(entered.wait(1))
                    path = "/v1/players/123?lookup=" + lookup_id
                    self.assertEqual(request(server, path)[2], body)
                    self.assertEqual(request(server)[2], body)
                release.set()
                for _ in range(4):
                    status, headers, failure = request(server, path)
                    self.assertEqual(status, 502)
                    self.assertEqual(failure, {"error": {"code": "lookup_failed"}})
                    self.assertNotIn("Retry-After", headers)
                    self.assertNotIn("PRIVATE", json.dumps(failure))
                self.assertEqual(manager.calls, [123])
            finally:
                release.set()

    def test_http_polling_success_survives_uid_cache_expiry_and_job_expiry_is_410(self):
        clock, release = [100.0], threading.Event()
        def blocked(uid):
            release.wait(2)
            return result(uid)
        manager = Manager(blocked)
        with service_for(manager, monotonic=lambda: clock[0]) as service, server_for(service) as server:
            try:
                with patch.object(api, "MAX_WAIT_SECONDS", 0.01):
                    body = request(server)[2]
                lookup_id = body["lookupId"]
                path = "/v1/players/123?lookup=" + lookup_id
                for invalid_path in ("/v1/players/124?lookup=" + lookup_id,
                                     "/v1/players/123?lookup=" + "0" * 32):
                    status, _, body = request(server, invalid_path)
                    self.assertEqual(status, 410)
                    self.assertEqual(body, {"error": {"code": "lookup_expired"}})
                release.set()
                status, _, original = request(server, path)
                self.assertEqual(status, 200)
                completed_job(service, lookup_id)
                clock[0] = 131.0
                self.assertEqual(request(server, path)[2], original)
                self.assertNotIn(123, service._cache)
                clock[0] = 400.0
                status, _, body = request(server, path)
                self.assertEqual(status, 410)
                self.assertEqual(body, {"error": {"code": "lookup_expired"}})
                self.assertEqual(manager.calls, [123])
            finally:
                release.set()

    def test_http_lookup_query_is_sole_exact_lowercase_hex_and_never_submits_on_invalid(self):
        manager = Manager()
        value = "a" * 32
        queries = ("", "lookup", "lookup=", "lookup=" + value.upper(),
                   "lookup=" + value + "a", "lookup=" + value[:-1],
                   "lookup=" + value + "&lookup=" + value,
                   "lookup=" + value + "&", "lookup=" + value + "&x=1",
                   "x=1&lookup=" + value, "Lookup=" + value,
                   "%6cookup=" + value, "lookup=%61" + value[1:],
                   "lookup=" + value + "+", "lookup=" + value + "?",
                   "lookup=" + value + "#fragment", "x=PRIVATE_QUERY")
        with service_for(manager) as service, server_for(service) as server:
            for query in queries:
                with self.subTest(query=query):
                    status, _, body = request(server, "/v1/players/123?" + query)
                    self.assertEqual(status, 400)
                    self.assertEqual(body, {"error": {"code": "invalid_lookup"}})
            # Resume endpoints have the same authorization gate as initial GET.
            self.assertEqual(request(server, "/v1/players/123?lookup=" + value, auth=None)[0], 401)
            self.assertEqual(request(server, "/v1/players/123?lookup=" + value)[0], 410)
            self.assertEqual(manager.calls, [])

    def test_http_wait_is_explicitly_capped_at_fifteen_seconds(self):
        with service_for() as service, server_for(service) as server, patch.object(service, "lookup", return_value=api.LookupResponse(503, {"error": {"code": "lookup_pending"}}, 2)) as lookup:
            self.assertEqual(request(server)[0], 503)
            lookup.assert_called_once_with(123, wait_seconds=15)
            lookup.reset_mock()
            lookup_id = "a" * 32
            self.assertEqual(request(server, "/v1/players/123?lookup=" + lookup_id)[0], 503)
            lookup.assert_called_once_with(123, lookup_id=lookup_id, wait_seconds=15)

    def test_disconnect_does_not_stop_manager_completion(self):
        entered, release, persisted = threading.Event(), threading.Event(), threading.Event()
        def blocked(uid):
            entered.set()
            release.wait(2)
            persisted.set()
            return result(uid)
        manager = Manager(blocked)
        with service_for(manager) as service, server_for(service) as server:
            conn = socket.create_connection(server.server_address, timeout=2)
            try:
                conn.sendall(("GET /v1/players/123 HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer " + TOKEN + "\r\n\r\n").encode())
                self.assertTrue(entered.wait(1))
                conn.close()
                release.set()
                self.assertTrue(persisted.wait(1))
                self.assertEqual(request(server)[0], 200)
            finally:
                conn.close()
                release.set()
        self.assertEqual(manager.calls, [123])

    def test_socket_handlers_are_bounded_and_idle_reads_time_out(self):
        with service_for() as service, server_for(service, max_handlers=1, read_timeout=0.1) as server:
            first = socket.create_connection(server.server_address, timeout=1)
            try:
                first.sendall(b"GET /health HTTP/1.1\r\n")
                # Acquiring the only permit proves the first handler started,
                # without inspecting an unbounded private thread list.
                deadline = time.monotonic() + 1
                while server.handler_slots.acquire(blocking=False):
                    server.handler_slots.release()
                    if time.monotonic() >= deadline:
                        self.fail("handler did not start")
                    time.sleep(0.001)
                self.assertEqual(request(server, "/health")[0], 503)
                self.assertEqual(first.recv(1024), b"")
            finally:
                first.close()

    def test_sensitive_exception_access_and_headers_are_not_logged(self):
        def failed(uid):
            raise ValueError("PRIVATE_CREDENTIALS")
        output = io.StringIO()
        with patch("sys.stderr", output), patch("sys.stdout", output), service_for(Manager(failed)) as service, server_for(service) as server:
            status, _, body = request(server)
            self.assertEqual(status, 502)
            self.assertEqual(body, {"error": {"code": "lookup_failed"}})
            raw_request(server, ["Authorization: Bearer PRIVATE_BAD_TOKEN"])
        self.assertEqual(output.getvalue(), "")

    def test_bad_server_configuration_never_opens_socket(self):
        with service_for() as service, patch.object(api._PrivateHTTPServer, "server_bind") as bind:
            for changes in ({"bearer_token": ""}, {"bearer_token": "bad\nsecret"}, {"max_handlers": 0},
                            {"max_handlers": True}, {"read_timeout": 0}, {"port": True}):
                with self.assertRaisesRegex(ValueError, "http_server_inputs_invalid"):
                    api.make_http_server(service, **({"bearer_token": TOKEN} | changes))
            bind.assert_not_called()


if __name__ == "__main__":
    unittest.main()
