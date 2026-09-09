"""Check Windows access rights to the installed game; NEVER read process memory.

Run manually in an administrator terminal with --pid and --check-read-access to
test whether normal elevation resolves OpenProcess denial. This tool does not
elevate itself, enable debug privileges, inspect credentials, inject code, change
permissions, or disable protections. It only opens/closes handles and queries
metadata. Without the switch, it requests limited metadata rights only.
"""

import argparse
import ctypes
from ctypes import wintypes as w
import json
import os


EXPECTED_IMAGE = r"C:\Program Files (x86)\Steam\steamapps\common\MarvelRivals\MarvelGame\Marvel\Binaries\Win64\Marvel-Win64-Shipping.exe"


def access_report(api, pid, *, check_read_access=False):
    report = {"pid": pid, "memory_read": False, "credentials_collected": False,
              "read_access_attempted": False}
    report["caller_elevated"] = api.elevated(os.getpid())
    report["expected_game_image"] = api.image(pid).casefold() == EXPECTED_IMAGE.casefold()
    if not report["expected_game_image"]:
        report["outcome"] = "wrong_or_unavailable_game_process"
        return report
    report["game_elevated"] = api.elevated(pid)
    if not check_read_access:
        report["outcome"] = "metadata_only"
    elif report["caller_elevated"] is not True:
        report["outcome"] = "administrator_terminal_required_no_read_access_attempted"
    else:
        report["read_access_attempted"] = True
        allowed, error = api.can_open_for_read(pid)
        report["outcome"] = "read_handle_available" if allowed else "read_handle_denied"
        report["win32_error"] = error
    return report


class WindowsAccess:
    def __init__(self):
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        self.k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.k.OpenProcess.restype = w.HANDLE
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.k.CloseHandle.restype = w.BOOL
        self.k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = w.BOOL
        self.a.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        self.a.OpenProcessToken.restype = w.BOOL
        self.a.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)]
        self.a.GetTokenInformation.restype = w.BOOL

    def image(self, pid):
        handle = self.k.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = w.DWORD(len(buffer))
            if self.k.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                return buffer.value
            return ""
        finally:
            self.k.CloseHandle(handle)

    def elevated(self, pid):
        handle = self.k.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        token = w.HANDLE()
        try:
            if not self.a.OpenProcessToken(handle, 8, ctypes.byref(token)):  # TOKEN_QUERY, Windows metadata only
                return None
            elevation, length = w.DWORD(), w.DWORD()
            if self.a.GetTokenInformation(token, 20, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(length)):
                return bool(elevation.value)
            return None
        finally:
            if token:
                self.k.CloseHandle(token)
            self.k.CloseHandle(handle)

    def can_open_for_read(self, pid):
        handle = self.k.OpenProcess(0x0410, False, pid)  # QUERY_INFORMATION | VM_READ
        if not handle:
            return False, ctypes.get_last_error()
        try:
            # Re-check the image on the actual handle, guarding against PID reuse.
            buffer = ctypes.create_unicode_buffer(32768)
            length = w.DWORD(len(buffer))
            if not self.k.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                return False, ctypes.get_last_error()
            if buffer.value.casefold() != EXPECTED_IMAGE.casefold():
                return False, None
            return True, 0
        finally:
            self.k.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--check-read-access", action="store_true")
    args = parser.parse_args()
    if os.name != "nt" or not 0 < args.pid <= 0xFFFFFFFF:
        parser.error("Requires Windows and a positive Windows process ID.")
    report = access_report(WindowsAccess(), args.pid, check_read_access=args.check_read_access)
    print(json.dumps(report, indent=2))
    return 0 if report["outcome"] in ("metadata_only", "read_handle_available") else 1


if __name__ == "__main__":
    raise SystemExit(main())
