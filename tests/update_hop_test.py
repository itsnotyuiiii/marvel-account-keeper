#!/usr/bin/env python3
"""Update-hop regression test for the in-app self-restart.

Reproduces, on a REAL packaged build, the 2.8.2/2.8.3 crash that got
auto-restart reverted in 2.8.4, and proves the fix in app.py.

  test_extraction_isolation
      A packaged parent spawns a successor (the way apply_update does). The
      successor must unpack its OWN _MEIxxxx dir and keep its bundled template
      readable AFTER the parent exits and tears its dir down. The original bug:
      the child inherited `_MEIPASS2`, reused the parent's extraction dir, and
      crashed with TemplateNotFound the moment the parent cleaned up.

  test_restart_end_to_end
      Drives the actual `_spawn_successor()` path end-to-end (minus the GitHub
      download): the successor waits for the parent to exit, takes the
      single-instance lock, loads its resources, and reaches the UI stage —
      with its own extraction dir.

Usage:
    python tests/update_hop_test.py [path-to-exe]
Defaults to dist/MarvelRivalsAccountTracker-windows.exe. Must be run against a
packaged build (the bug only exists for a PyInstaller onefile exe).
"""
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def find_exe() -> Path:
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if not p.exists():
            sys.exit(f"exe not found: {p}")
        return p
    root = Path(__file__).resolve().parent.parent
    for name in ("MarvelRivalsAccountTracker-windows.exe",
                 "MarvelRivalsAccountTracker.exe",
                 "MarvelRivalsAccountTracker"):
        cand = root / "dist" / name
        if cand.exists():
            return cand
    sys.exit("no built exe in dist/ — run `python build.py` first, or pass a path")


EXE = find_exe()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _wait_for(p: Path, needle: str, timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = _read(p)
        if needle in data:
            return data
        time.sleep(0.25)
    return _read(p)


def _kill(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # taskkill can return before Windows has fully torn down the
            # process and released its instance.lock handle. Waiting on the
            # process handle keeps TemporaryDirectory cleanup deterministic.
            import ctypes
            from ctypes import wintypes
            synchronize = 0x00100000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = (
                wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
            )
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = (
                wintypes.HANDLE, wintypes.DWORD,
            )
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(synchronize, False, pid)
            if handle:
                try:
                    kernel32.WaitForSingleObject(handle, 10_000)
                finally:
                    kernel32.CloseHandle(handle)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def test_extraction_isolation() -> None:
    with tempfile.TemporaryDirectory() as td:
        sentinel = Path(td) / "sentinel.txt"
        r = subprocess.run([str(EXE), "--selftest-spawn", str(sentinel)], timeout=30)
        assert r.returncode == 0, f"parent exited {r.returncode}"
        data = _wait_for(sentinel, "STAGE2=", timeout=25)
        pm = re.search(r"PARENT_MEI=(.+)", data)
        cm = re.search(r"CHILD_MEI=(.+)", data)
        s2 = re.search(r"STAGE2=(\w+)", data)
        assert pm, f"no PARENT_MEI; sentinel=\n{data}"
        assert cm, f"successor never started; sentinel=\n{data}"
        assert s2, f"successor never finished STAGE2; sentinel=\n{data}"
        parent_mei, child_mei = pm.group(1).strip(), cm.group(1).strip()
        assert child_mei != parent_mei, \
            f"successor REUSED parent extraction dir (the old bug): {child_mei}"
        assert s2.group(1) == "OK", \
            f"successor lost its template after parent exit: STAGE2={s2.group(1)}"
        print("  PASS extraction-isolation")
        print(f"       parent _MEI: {parent_mei}")
        print(f"       child  _MEI: {child_mei}")
        print("       template survived parent teardown: STAGE2=OK")


def test_restart_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as td:
        sentinel = Path(td) / "restart.txt"
        env = {**os.environ, "MARVEL_KEEPER_DATA": td}
        r = subprocess.run([str(EXE), "--selftest-restart", str(sentinel)],
                           env=env, timeout=30)
        assert r.returncode == 0, f"parent exited {r.returncode}"
        data = _wait_for(sentinel, "UP=", timeout=30)
        pm = re.search(r"PARENT_MEI=(.+)", data)
        up = re.search(r"UP=(\d+) MEI=(.+)", data)
        assert up, f"successor never reached the UI stage; sentinel=\n{data}"
        succ_pid = int(up.group(1))
        succ_mei = up.group(2).strip()
        try:
            assert pm, f"no PARENT_MEI; sentinel=\n{data}"
            assert succ_mei != pm.group(1).strip(), \
                f"successor reused parent dir: {succ_mei}"
            print("  PASS restart-end-to-end")
            print(f"       successor pid {succ_pid} took the lock + loaded resources")
            print(f"       successor _MEI: {succ_mei}")
        finally:
            _kill(succ_pid)


def main() -> int:
    print(f"exe under test: {EXE}")
    failures = 0
    for t in (test_extraction_isolation, test_restart_end_to_end):
        try:
            t()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001 — report any harness failure
            failures += 1
            print(f"  ERROR {t.__name__}: {e!r}")
    print()
    print("RESULT:", "ALL PASS" if failures == 0 else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
