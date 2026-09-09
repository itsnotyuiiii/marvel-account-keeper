"""Synthetic data only, including one known self-buffer; never read game memory."""
import contextlib
import ctypes
import io
import json
import os
import unittest
from unittest.mock import Mock, patch

import career_probe as career
import career_session_probe as p
from tests.career_probe_test import GATEWAY, token_for


class FakeGame:
    def __init__(self, data, *, read_error=None, protect=4):
        self.data, self.read_error, self.protect = data, read_error, protect
        self.reads = []
        self.closed = 0

    def region(self, address):
        if address >= len(self.data):
            return None
        return p.Region(0, len(self.data), 0x1000, self.protect, 0x20000)

    def read(self, address, size):
        self.reads.append((address, size))
        if self.read_error:
            raise self.read_error
        return self.data[address:address + size]

    def close(self):
        self.closed += 1


class CareerSessionProbeTest(unittest.TestCase):
    def candidate(self):
        return token_for().encode()

    def query(self):
        return career.CareerQuery("profile", 123456789, 11001)

    def test_candidates_require_socialmatrix_tls_first_party_and_valid_time(self):
        valid = self.candidate()
        self.assertIsNotNone(p.session_candidate(valid))
        for updates in ({"aid": None}, {"websocket": {}}, {"exp": 1},
                        {"websocket": {"addr": "other.example", "tls": True}},
                        {"websocket": {"addr": GATEWAY, "tls": False}}):
            self.assertIsNone(p.session_candidate(token_for(**updates).encode()))
        for value in (b"x.y.z", b"x" * (career.MAX_TOKEN_BYTES + 1), b"\xff", b"a.b"):
            self.assertIsNone(p.session_candidate(value))
        self.assertNotIn(valid.decode(), repr(p.session_candidate(valid)))

    def test_ascii_token_across_every_chunk_boundary_without_truncated_acceptance(self):
        token = self.candidate()
        for split in range(1, len(token)):
            collector = p.CandidateCollector()
            collector.feed(0, b"\0" + token[:split])
            self.assertEqual(len(collector.sessions), 0)
            collector.feed(split + 1, token[split:] + b"\0")
            self.assertEqual([s.token for s in collector.sessions.values()], [token.decode()])

    def test_wide_token_across_every_chunk_boundary_and_both_alignments(self):
        token = self.candidate().decode()
        wide = token.encode("utf-16-le")
        for alignment in (b"\0\0", b"x\0\0"):
            for split in range(1, len(wide)):
                collector = p.CandidateCollector()
                collector.feed(0, alignment + wide[:split])
                self.assertFalse(collector.sessions)
                collector.feed(len(alignment) + split, wide[split:] + b"\0\0")
                self.assertEqual([s.token for s in collector.sessions.values()], [token])

    def test_gap_is_not_joined_and_duplicate_encodings_are_deduplicated(self):
        token = self.candidate()
        collector = p.CandidateCollector()
        collector.feed(0, b"\0" + token[:30])
        collector.feed(10000, token[30:] + b"\0")
        self.assertFalse(collector.sessions)
        collector.feed(20000, b"\0" + token + b"\0" + token.decode().encode("utf-16-le") + b"\0\0")
        self.assertEqual(len(collector.sessions), 1)
        collector.clear()
        self.assertFalse(collector.sessions)
        self.assertEqual(collector.tail, b"")

    def test_candidate_count_is_bounded(self):
        collector = p.CandidateCollector()
        data = b"\0".join(token_for(aid=f"synthetic-{i}").encode() for i in range(20)) + b"\0"
        collector.feed(0, data)
        self.assertTrue(collector.saturated)
        self.assertEqual(len(collector.sessions), 8)

    def test_only_private_committed_non_executable_unguarded_pages_are_eligible(self):
        for protection in (2, 4, 8):
            self.assertTrue(p.Region(0, 10, 0x1000, protection, 0x20000).eligible())
        for protection in (0, 1, 0x104, 0x20, 0x40, 0x80, 0x204):
            self.assertFalse(p.Region(0, 10, 0x1000, protection, 0x20000).eligible())
        self.assertFalse(p.Region(0, 10, 0x2000, 4, 0x20000).eligible())
        self.assertFalse(p.Region(0, 10, 0x1000, 4, 0x1000000).eligible())
        game = FakeGame(b"x" * 50, protect=0x104)
        p.scan(game, p.CandidateCollector())
        self.assertEqual(game.reads, [])

    def test_scan_byte_time_region_limits_and_no_overread(self):
        game = FakeGame(b"x" * 100)
        report = p.scan(game, p.CandidateCollector(), max_bytes=9, chunk_bytes=4)
        self.assertEqual(report["outcome"], "byte_limit_reached")
        self.assertEqual(game.reads, [(0, 4), (4, 4), (8, 1)])
        self.assertEqual(report["bytes_read"], 9)
        game = FakeGame(b"x" * 100)
        report = p.scan(game, p.CandidateCollector(), clock=Mock(side_effect=[0, 50]))
        self.assertEqual(report["outcome"], "time_limit_reached")
        self.assertFalse(game.reads)
        report = p.scan(game, p.CandidateCollector(), max_regions=0)
        self.assertEqual(report["outcome"], "region_limit_reached")

    def test_denied_read_stops_without_retry_or_send_and_closes_handle(self):
        game = FakeGame(b"x", read_error=p.ProbeStopped("memory_read_denied_or_failed", 5))
        sender = Mock()
        report = p.run_probe(123, send=True, query=self.query(), gateway=GATEWAY,
                             game_factory=Mock(return_value=game), sender=sender)
        self.assertEqual(report["outcome"], "memory_read_denied_or_failed")
        self.assertEqual(report["win32_error"], 5)
        self.assertEqual(len(game.reads), 1)
        self.assertEqual(game.closed, 1)
        sender.assert_not_called()

    def test_partial_region_is_skipped_without_retry(self):
        game = FakeGame(b"x" * 10)
        game.read = Mock(return_value=None)
        report = p.scan(game, p.CandidateCollector())
        self.assertEqual(report["partial_regions_skipped"], 1)
        self.assertEqual(report["bytes_read"], 0)
        game.read.assert_called_once()

    def test_error_discards_previously_collected_candidates(self):
        collector = p.CandidateCollector()
        collector.feed(0, self.candidate() + b"\0")
        game = FakeGame(b"x", read_error=p.ProbeStopped("memory_read_denied_or_failed", 5))
        p.scan(game, collector)
        self.assertFalse(collector.sessions)

    def test_scan_only_summary_contains_neither_token_nor_account_claims(self):
        token = token_for(aid="private-synthetic-account")
        game = FakeGame(token.encode() + b"\0")
        sender = Mock()
        report = p.run_probe(123, game_factory=Mock(return_value=game), sender=sender)
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["gateway_hints"], [GATEWAY])
        self.assertFalse(report["request_sent"])
        self.assertFalse(report["identity_verified"])
        self.assertNotIn(token, json.dumps(report))
        self.assertNotIn("private-synthetic-account", json.dumps(report))
        self.assertEqual(game.closed, 1)
        sender.assert_not_called()

    def test_one_send_reuses_original_token_and_only_returns_redacted_response(self):
        token = self.candidate()
        game = FakeGame(token + b"\0")
        sender = Mock(return_value=career.CareerProbeResult("json_received", 200, ("career",),
                                                           {"career": {"secret": token.decode()}}))
        query = self.query()
        report = p.run_probe(123, send=True, query=query, gateway=GATEWAY,
                             game_factory=Mock(return_value=game), sender=sender)
        sender.assert_called_once_with(query, token.decode(), GATEWAY)
        self.assertTrue(report["request_sent"])
        self.assertFalse(report["statistics_verified"])
        self.assertNotIn(token.decode(), json.dumps(report))
        self.assertEqual(game.closed, 1)

    def test_no_send_when_missing_multiple_or_mismatched_candidates(self):
        for data, gateway in ((b"none", GATEWAY),
                              (self.candidate() + b"\0" + token_for(aid="second").encode() + b"\0", GATEWAY),
                              (self.candidate() + b"\0", "different.easebar.com")):
            sender = Mock()
            report = p.run_probe(123, send=True, query=self.query(), gateway=gateway,
                                 game_factory=Mock(return_value=FakeGame(data)), sender=sender)
            sender.assert_not_called()
            self.assertFalse(report["request_sent"])

    def test_input_validation_precedes_process_access(self):
        factory = Mock()
        for kwargs in ({"send": True}, {"send": True, "query": self.query(), "gateway": "evil.test"},
                       {"send": True, "query": career.CareerQuery("profile", None, 11001), "gateway": GATEWAY},
                       {"gateway": GATEWAY}):
            with self.assertRaises(ValueError):
                p.run_probe(123, game_factory=factory, **kwargs)
        factory.assert_not_called()

    def test_default_cli_has_no_memory_access_or_network(self):
        out = io.StringIO()
        with patch.object(p, "run_probe") as run, contextlib.redirect_stdout(out):
            self.assertEqual(p.main(["--pid", "123"]), 0)
        run.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())["outcome"], "preview_only")

    def test_cli_exception_is_redacted(self):
        err = io.StringIO()
        with patch.object(p, "run_probe", side_effect=OSError(self.candidate().decode())), contextlib.redirect_stderr(err):
            self.assertEqual(p.main(["--pid", "123", "--scan"]), 2)
        self.assertNotIn(self.candidate().decode(), err.getvalue())

    def test_failed_request_is_not_cli_success_just_because_a_candidate_exists(self):
        report = {"candidate_count": 1, "response": {"outcome": "access_blocked"}}
        with patch.object(p, "run_probe", return_value=report), contextlib.redirect_stdout(io.StringIO()):
            result = p.main(["--pid", "123", "--scan", "--send", "--own-uid", "123456789",
                             "--zone-id", "11001", "--gateway", GATEWAY])
        self.assertEqual(result, 1)

    def test_network_failure_does_not_claim_transmission_did_or_did_not_happen(self):
        sender = Mock(return_value=career.CareerProbeResult("network_error"))
        report = p.run_probe(123, send=True, query=self.query(), gateway=GATEWAY,
                             game_factory=Mock(return_value=FakeGame(self.candidate() + b"\0")), sender=sender)
        self.assertTrue(report["request_attempted"])
        self.assertIsNone(report["request_sent"])
        sender.assert_called_once()

    def test_non_admin_and_wrong_process_never_open_read_handle(self):
        access = Mock()
        access.EXPECTED_IMAGE = "C:\\synthetic\\game.exe"
        api = access.WindowsAccess.return_value
        for elevated, image in ((False, access.EXPECTED_IMAGE), (None, access.EXPECTED_IMAGE), (True, "wrong.exe")):
            api.reset_mock()
            api.elevated.return_value = elevated
            api.image.return_value = image
            with patch.object(p, "access_module", return_value=access), self.assertRaises(p.ProbeStopped):
                p.WindowsGame(123)
            api.k.OpenProcess.assert_not_called()

    def test_actual_handle_image_is_rechecked_and_mismatch_closed(self):
        access = Mock()
        access.EXPECTED_IMAGE = "C:\\synthetic\\game.exe"
        api = access.WindowsAccess.return_value
        api.elevated.return_value = True
        api.image.return_value = access.EXPECTED_IMAGE
        api.k.OpenProcess.return_value = 999
        def wrong_image(handle, flags, name, length):
            name.value = "C:\\synthetic\\other.exe"
            return True
        api.k.QueryFullProcessImageNameW.side_effect = wrong_image
        with patch.object(p, "access_module", return_value=access), self.assertRaises(p.ProbeStopped):
            p.WindowsGame(123)
        api.k.OpenProcess.assert_called_once_with(0x0410, False, 123)
        api.k.CloseHandle.assert_called_once_with(999)
        api.k.ReadProcessMemory.assert_not_called()

    @unittest.skipUnless(os.name == "nt" and ctypes.sizeof(ctypes.c_void_p) == 8, "Windows ABI check")
    def test_native_bindings_read_only_a_known_buffer_in_this_test_process(self):
        # Deliberately not WindowsGame(pid): production construction targets only
        # the installed game. This adapter fixture opens this test process and
        # reads exactly the synthetic buffer we allocate, never enumerating it.
        game = object.__new__(p.WindowsGame)
        game.k = p.access_module().WindowsAccess().k
        p.configure_memory_api(game.k)
        game.handle = game.k.OpenProcess(0x0410, False, os.getpid())
        self.assertTrue(game.handle)
        buffer = ctypes.create_string_buffer(b"synthetic-self-buffer")
        try:
            address = ctypes.addressof(buffer)
            region = game.region(address)
            self.assertLessEqual(region.base, address)
            self.assertGreaterEqual(region.base + region.size, address + len(buffer))
            self.assertEqual(game.read(address, len(buffer)), buffer.raw)
            self.assertEqual(ctypes.sizeof(p.MemoryInfo), 48)
            self.assertEqual(p.MemoryInfo.RegionSize.offset, 24)
        finally:
            game.close()
        self.assertIsNone(game.handle)


if __name__ == "__main__":
    unittest.main()
