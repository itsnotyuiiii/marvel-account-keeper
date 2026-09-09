"""Bounded, explicitly requested own-game session probe; not a stats provider.

No self-elevation, debug privileges, injection, protection changes, dumps, token
output, or credential persistence. Memory collection requires --scan and an
already elevated caller. Optional --send makes exactly one own-Career request
through career_probe; its gateway and account inputs must be provided explicitly.
Python cannot guarantee erasure of immutable copies or prevent OS paging/dumps.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

import career_probe as career


CHUNK_BYTES = 1024 * 1024
MAX_SCAN_BYTES = 2 * 1024 * 1024 * 1024
MAX_SECONDS = 45
MAX_REGIONS = 100000
OVERLAP = 2 * career.MAX_TOKEN_BYTES + 4
# Require a terminator in the bytes already read: a signature ending at a chunk
# boundary must not be accepted as a truncated but syntactically valid token.
ASCII_JWT = re.compile(rb"(?<![A-Za-z0-9_.-])eyJ[A-Za-z0-9_-]{1,1024}\."
                       rb"[A-Za-z0-9_-]{1,30000}\.[A-Za-z0-9_-]{1,2048}(?=[^A-Za-z0-9_.-])")
WIDE_JWT = re.compile(rb"(?<![A-Za-z0-9_.-]\x00)e\x00y\x00J\x00"
                      rb"(?:[A-Za-z0-9_-]\x00){1,1024}\.\x00"
                      rb"(?:[A-Za-z0-9_-]\x00){1,30000}\.\x00"
                      rb"(?:[A-Za-z0-9_-]\x00){1,2048}"
                      rb"(?![A-Za-z0-9_.-]\x00)(?=.{2})", re.DOTALL)


def access_module():
    spec = importlib.util.spec_from_file_location(
        "career_access_local", Path(__file__).parent / "scripts" / "check-career-access.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(repr=False)
class Session:
    token: str = field(repr=False)
    gateway: str

    def __repr__(self):
        return "Session(<redacted>)"


def session_candidate(raw: bytes) -> Session | None:
    """Reject unrelated/expired/non-TLS JWTs; never expose their claims or errors."""
    if len(raw) > career.MAX_TOKEN_BYTES:
        return None
    try:
        token = raw.decode("ascii")
        parts = token.split(".")
        if len(parts) != 3:
            return None
        claims = json.loads(base64.b64decode(parts[1] + "=" * (-len(parts[1]) % 4),
                                           altchars=b"-_", validate=True))
        if not isinstance(claims, dict) or not isinstance(claims.get("websocket"), dict):
            return None
        gateway = claims["websocket"].get("addr")
        career.validate_session(token, gateway)
        host, port = career.gateway_authority(gateway)
        return Session(token, host if port == 443 else f"{host}:{port}")
    except (ValueError, UnicodeError, RecursionError, TypeError):
        return None


class CandidateCollector:
    def __init__(self):
        self.tail = b""
        self.next_address = None
        self.sessions = {}
        self.saturated = False

    def feed(self, address: int, data: bytes):
        prefix = self.tail if address == self.next_address else b""
        merged = prefix + data
        self.next_address = address + len(data)
        self.tail = merged[-OVERLAP:]
        for regex, encoding in ((ASCII_JWT, "ascii"), (WIDE_JWT, "utf-16-le")):
            for match in regex.finditer(merged):
                raw = match.group().decode(encoding).encode("ascii")
                candidate = session_candidate(raw)
                if candidate is None:
                    continue
                digest = hashlib.sha256(raw).digest()
                if digest not in self.sessions:
                    if len(self.sessions) >= 8:
                        self.saturated = True
                        return
                    self.sessions[digest] = candidate

    def clear(self):
        self.tail = b""
        self.sessions.clear()
        self.next_address = None


@dataclass(frozen=True)
class Region:
    base: int
    size: int
    state: int
    protect: int
    kind: int

    def eligible(self):
        # Private, committed, non-executable readable pages only. Never touch
        # guarded/no-access/mapped/image pages or change protection to read them.
        return self.state == 0x1000 and self.kind == 0x20000 and self.protect in (2, 4, 8)


class ProbeStopped(Exception):
    def __init__(self, outcome, win32_error=None):
        super().__init__(outcome)
        self.outcome, self.win32_error = outcome, win32_error


class MemoryInfo(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", w.DWORD), ("PartitionId", w.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", w.DWORD),
                ("Protect", w.DWORD), ("Type", w.DWORD)]


def configure_memory_api(kernel):
    # https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualqueryex
    # https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-readprocessmemory
    kernel.VirtualQueryEx.argtypes = [w.HANDLE, ctypes.c_void_p,
                                     ctypes.POINTER(MemoryInfo), ctypes.c_size_t]
    kernel.VirtualQueryEx.restype = ctypes.c_size_t
    kernel.ReadProcessMemory.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.ReadProcessMemory.restype = w.BOOL


class WindowsGame:
    def __init__(self, pid):
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise ProbeStopped("requires_64_bit_windows_python")
        access = access_module()
        self.api = access.WindowsAccess()
        self.k = self.api.k
        self.handle = None
        if self.api.elevated(os.getpid()) is not True:
            raise ProbeStopped("administrator_terminal_required_no_memory_read")
        configure_memory_api(self.k)
        if self.api.image(pid).casefold() != access.EXPECTED_IMAGE.casefold():
            raise ProbeStopped("wrong_or_unavailable_game_process")
        self.handle = self.k.OpenProcess(0x0410, False, pid)
        if not self.handle:
            raise ProbeStopped("read_handle_denied", ctypes.get_last_error())
        # Check identity on the actual retained handle, not only the PID.
        try:
            name, length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
            if not self.k.QueryFullProcessImageNameW(self.handle, 0, name, ctypes.byref(length)):
                raise ProbeStopped("image_check_failed", ctypes.get_last_error())
            if name.value.casefold() != access.EXPECTED_IMAGE.casefold():
                raise ProbeStopped("wrong_or_unavailable_game_process")
        except BaseException:
            self.close()
            raise

    def region(self, address):
        info = MemoryInfo()
        size = self.k.VirtualQueryEx(self.handle, address, ctypes.byref(info), ctypes.sizeof(info))
        if not size:
            error = ctypes.get_last_error()
            if error == 87:  # Address above the accessible address-space limit.
                return None
            raise ProbeStopped("memory_query_denied_or_failed", error)
        if size != ctypes.sizeof(info):
            raise ProbeStopped("unexpected_memory_info_size")
        return Region(info.BaseAddress or 0, info.RegionSize, info.State, info.Protect, info.Type)

    def read(self, address, size):
        buffer = (ctypes.c_ubyte * size)()
        count = ctypes.c_size_t()
        try:
            if not self.k.ReadProcessMemory(self.handle, address, buffer, size, ctypes.byref(count)):
                # A changing/decommitted region may give PARTIAL_COPY. Skip the
                # region without retrying; actual access denial stops everything.
                error = ctypes.get_last_error()
                if error == 299:
                    return None
                raise ProbeStopped("memory_read_denied_or_failed", error)
            if count.value != size:
                return None
            return bytes(buffer)
        finally:
            ctypes.memset(buffer, 0, size)

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


def scan(game, collector, *, max_bytes=MAX_SCAN_BYTES, seconds=MAX_SECONDS,
         clock=time.monotonic, chunk_bytes=CHUNK_BYTES, max_regions=MAX_REGIONS):
    started, address = clock(), 0
    report = {"outcome": "address_space_scanned", "bytes_requested": 0, "bytes_read": 0,
              "regions_queried": 0, "partial_regions_skipped": 0}
    try:
        while report["regions_queried"] < max_regions:
            if clock() - started >= seconds:
                report["outcome"] = "time_limit_reached"
                break
            region = game.region(address)
            if region is None:
                break
            report["regions_queried"] += 1
            end = region.base + region.size
            if region.size <= 0 or region.base > address or end <= address or end > 2**64:
                raise ProbeStopped("invalid_memory_region")
            if region.eligible():
                offset = address
                while offset < end:
                    if clock() - started >= seconds:
                        report["outcome"] = "time_limit_reached"
                        return report
                    remaining = max_bytes - report["bytes_requested"]
                    if remaining <= 0:
                        report["outcome"] = "byte_limit_reached"
                        return report
                    size = min(chunk_bytes, end - offset, remaining)
                    report["bytes_requested"] += size
                    data = game.read(offset, size)
                    if data is None:
                        report["partial_regions_skipped"] += 1
                        break
                    if len(data) != size:
                        raise ProbeStopped("unexpected_memory_read_size")
                    report["bytes_read"] += len(data)
                    collector.feed(offset, data)
                    del data
                    if collector.saturated:
                        report["outcome"] = "candidate_limit_reached"
                        return report
                    offset += size
            address = end
        else:
            report["outcome"] = "region_limit_reached"
    except ProbeStopped as exc:
        report.update(outcome=exc.outcome, win32_error=exc.win32_error)
        collector.clear()  # Denied/failed reads must never be followed by a send.
    return report


def run_probe(pid, *, send=False, query=None, gateway=None, game_factory=WindowsGame,
              sender=career.probe_career):
    # All send options are validated before any process access.
    if send:
        if query is None or gateway is None:
            raise ValueError("Sending requires an explicit own UID, zone and verified gateway.")
        query.body()
        career.gateway_authority(gateway)
    elif query is not None or gateway is not None:
        raise ValueError("Account/gateway options require --send; no values are inferred.")
    game, collector = None, CandidateCollector()
    report = {"pid": pid, "request_sent": False, "request_attempted": False,
              "tokens_saved": False, "bytes_read": 0,
              "identity_verified": False, "statistics_verified": False}
    try:
        game = game_factory(pid)
        report.update(scan(game, collector))
        game.close()
        game = None
        sessions = list(collector.sessions.values())
        report["candidate_count"] = len(sessions)
        report["gateway_hints"] = sorted({session.gateway for session in sessions})
        report["gateway_verified"] = False
        if send:
            if len(sessions) != 1 or collector.saturated:
                report["request_outcome"] = "requires_one_observed_candidate"
            elif career.gateway_authority(sessions[0].gateway) != career.gateway_authority(gateway):
                report["request_outcome"] = "gateway_mismatch_no_request_sent"
            else:
                # Revalidate expiry immediately before the single request.
                career.validate_session(sessions[0].token, gateway)
                report["request_attempted"] = True
                result = sender(query, sessions[0].token, gateway)
                # A network error may precede or follow transmission; don't assert
                # whether the server received it. Other outcomes contain HTTP status.
                report["request_sent"] = True if result.http_status is not None else None
                report["response"] = result.summary()
                report["response"].update(career.profile_identity_evidence(query, result))
        return report
    except ProbeStopped as exc:
        report.update(outcome=exc.outcome, win32_error=exc.win32_error)
        return report
    finally:
        collector.clear()
        if game is not None:
            game.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--scan", action="store_true", help="Read the specified own game's eligible memory")
    parser.add_argument("--send", action="store_true", help="After scanning, attempt one profile request")
    parser.add_argument("--own-uid")
    parser.add_argument("--zone-id")
    parser.add_argument("--gateway", help="Independently verified first-party hostname[:port]")
    parser.add_argument("--season", type=int)
    args = parser.parse_args(argv)
    if not 0 < args.pid <= 0xFFFFFFFF:
        parser.error("PID must be a positive Windows process ID.")
    try:
        if not args.scan:
            if args.send or any(x is not None for x in (args.own_uid, args.zone_id, args.gateway, args.season)):
                raise ValueError("Sending/account options require --scan and --send.")
            print(json.dumps({"outcome": "preview_only", "memory_read": False,
                              "request_sent": False, "scan_byte_limit": MAX_SCAN_BYTES,
                              "scan_seconds_limit": MAX_SECONDS}, indent=2))
            return 0
        query = None
        if args.send:
            query = career.CareerQuery("profile", args.own_uid, args.zone_id, season=args.season)
        elif any(x is not None for x in (args.own_uid, args.zone_id, args.gateway, args.season)):
            raise ValueError("Account/gateway options require --send.")
        report = run_probe(args.pid, send=args.send, query=query, gateway=args.gateway)
        print(json.dumps(report, indent=2))
        if args.send:
            return 0 if report.get("response", {}).get("usable_profile_identity") is True else 1
        return 0 if report.get("candidate_count") == 1 else 1
    except (ValueError, OSError, RuntimeError):
        # No exception repr/traceback: those can contain local/remote session data.
        print("Probe stopped: invalid input or local/transport failure; no secret details logged.", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("Probe cancelled; no session material saved.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
