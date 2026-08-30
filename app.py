"""Marvel Rivals / Steam account tracker.

Single-user local-only app. Stores account records in vault.json, with the
password field encrypted under a key derived from a master password (scrypt
+ AES-256-GCM). The derived key lives only in process memory after unlock.

Run directly (`python app.py`) or as the packaged executable — no arguments
needed. It binds a fixed loopback port (falling back to a free one if it's
taken), opens the default browser, and quits
itself ~2 min after the browser is closed, so a double-clicked build leaves
nothing running. Optional flags: `--no-browser` (headless, stays up),
`--port N` (fixed port), `--keep-alive` (open the browser but don't auto-quit).
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

# Packaged as a windowed PyInstaller exe (no console), sys.stdout / sys.stderr
# come up as None on Windows and every print() in this file would raise
# AttributeError. Point them at devnull so the existing chatter becomes a
# silent no-op instead of crashing on launch.
if sys.stdout is None or sys.stderr is None:
    _devnull = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = _devnull
    if sys.stderr is None:
        sys.stderr = _devnull

APP_NAME = "MarvelAccountKeeper"
APP_VERSION = "2.15.0"
WINDOW_TITLE = "Marvel Rivals Account Tracker"  # native window title; also matched for single-instance focus
GITHUB_REPO_SLUG = "itsnotyuiiii/marvel-account-keeper"

# Update-check / self-apply settings. The packaged .exe checks the GitHub
# release feed on boot and offers a one-click update when a newer tag is
# available. Running from source (not frozen) hides the banner entirely.
GITHUB_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest"
UPDATE_ASSET_NAME = "MarvelRivalsAccountTracker-windows.exe"
UPDATE_CHECKSUM_NAME = UPDATE_ASSET_NAME + ".sha256"
UPDATE_MIN_DOWNLOAD_BYTES = 1_000_000  # PyInstaller exes are 10MB+; anything smaller is junk
VERIFIER_PLAINTEXT = b"VAULT_OK::v1"
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LEN = 32
DEFAULT_LOCKOUT_MINUTES = 30  # idle auto-lock; 0 = never lock
BACKUP_KEEP = 20
EXTRA_BACKUP_KEEP = 100

# Per-account fields introduced with the redesigned frontend. The startup
# migration backfills these onto any account created by the old UI.
ACCOUNT_FIELD_DEFAULTS: dict[str, Any] = {
    "pinned": False,
    "tag": "",
    "tag_color": "",               # optional tag-pill color, independent of the neon border.
                                   # Blank → falls back to border_color (back-compat).
    "neon": False,
    "current_points": None,
    "peak_points": None,
    # Stats refresh (public Tracker.gg profile data)
    "rivals_uid": None,
    "last_refresh_ts": None,       # epoch — when we last requested stats for this account
    "last_refresh_status": None,   # "ok" | "not_found" | "error" | "missing_handle"
    # Stable machine-readable reason for non-ok refreshes. Keep the broad
    # status above for compatibility; this lets the UI distinguish an absent
    # player from a recognized-but-unavailable profile and a provider outage.
    "last_refresh_code": None,
    "last_refresh_error": None,
    "last_refresh_source": None,   # currently "tracker" when a refresh succeeds
    "tracker_history_private": False,  # DISPLAY-ONLY: tracker served public ranks but the profile's
                                   # match history is private, so the "last crawled" age is NOT a
                                   # last-played/last-login signal. Suppresses the misleading
                                   # "dormant — Xd ago" framing. Never feeds last_refresh_status.
    "rivals_synced_at": None,      # epoch — upstream profile timestamp when available
}

# Keep refresh-all deliberately slow so a local vault never hammers the
# upstream profile service.
REFRESH_ALL_DELAY_S = 2.0
PER_ACCOUNT_REFRESH_COOLDOWN_S = 20  # min spacing between refreshes for the SAME account

# tracker.gg rank refresh. This is an undocumented website endpoint, so its
# schema may change; failures are surfaced plainly and never fall through to a
# second hidden provider. RivalsData is integrated only as a user-opened profile
# link because it does not publish a developer API and explicitly discourages
# programmatic access.
TRACKER_API_BASE = "https://api.tracker.gg/api/v2/marvel-rivals/standard"
TRACKER_TIMEOUT_S = 15
TRACKER_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36")


# ---------- locations ----------

def _resource_dir() -> Path:
    """Directory holding bundled templates/ and static/.

    Under a PyInstaller --onefile build this is the temp extraction dir
    (sys._MEIPASS); otherwise it is the folder this file lives in.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent


def _load_build_info() -> dict[str, Any]:
    """Read `_build_info.json` written by build.py at CI build time. Carries
    the short commit SHA and build timestamp so the About/footer can show
    which build is running. Returns {} in source runs where the file is
    absent — the UI falls back to a "dev" label."""
    p = _resource_dir() / "_build_info.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


_BUILD_INFO = _load_build_info()
APP_COMMIT = (_BUILD_INFO.get("commit") or "").strip() or "dev"
APP_BUILT_AT = _BUILD_INFO.get("built_at")  # ISO 8601 string or None


def _data_dir() -> Path:
    """Per-user writable directory for the vault and its backups.

      Windows : %APPDATA%/MarvelAccountKeeper/
      macOS   : ~/Library/Application Support/MarvelAccountKeeper/
      Linux   : ~/.local/share/MarvelAccountKeeper/

    Set the MARVEL_KEEPER_DATA env var to override the location entirely —
    handy for a throwaway demo vault, tests, or keeping more than one vault.
    The override is used as-is (no MarvelAccountKeeper subfolder).
    """
    override = os.environ.get("MARVEL_KEEPER_DATA")
    if override:
        d = Path(override).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


RESOURCE_DIR = _resource_dir()
DATA_DIR = _data_dir()
VAULT_PATH = DATA_DIR / "vault.json"
BACKUP_DIR = DATA_DIR / "backups"
EXTRA_BACKUP_DIR = Path.home() / "Documents" / "MarvelAccountsBackups"
# Single-instance guard: an OS-locked file. A second launch fails to take the
# lock, focuses the running window, and exits.
INSTANCE_LOCK_PATH = DATA_DIR / "instance.lock"


# ---------- Steam install detection ----------
# We surface which Steam account is currently logged in so the matching vault
# card can show an "ACTIVE NOW" badge — turning the vault into a live snapshot
# of which account is in use, rather than a passive list.

def _steam_install_dir() -> Path | None:
    """Locate the Steam install directory across Windows / macOS / Linux."""
    if sys.platform == "win32":
        try:
            import winreg  # std-lib, Windows-only
        except ImportError:
            return None
        # SteamPath lives in HKCU; InstallPath in HKLM as a 32/64-bit fallback.
        for hive, path, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, path) as k:
                    val = winreg.QueryValueEx(k, name)[0]
                    if val:
                        return Path(val)
            except OSError:
                continue
        return None
    if sys.platform == "darwin":
        cand = Path.home() / "Library" / "Application Support" / "Steam"
        return cand if cand.exists() else None
    # Linux: ~/.steam/steam is the canonical symlink; ~/.local/share/Steam is
    # the typical install. Try both before giving up.
    for cand in (Path.home() / ".steam" / "steam",
                 Path.home() / ".local" / "share" / "Steam"):
        if cand.exists():
            return cand
    return None


def _parse_vdf(text: str) -> dict[str, Any]:
    """Minimal parser for Valve's text-KV format used in loginusers.vdf.

    Handles only what that file uses: quoted keys, quoted scalar values, and
    nested { ... } blocks. Escapes are limited to \\\\ and \\". Anything more
    exotic isn't worth pulling in a dependency for.
    """
    i, n = 0, len(text)

    def skip_ws() -> None:
        nonlocal i
        while i < n:
            c = text[i]
            if c in " \t\r\n":
                i += 1
            elif c == "/" and i + 1 < n and text[i + 1] == "/":
                while i < n and text[i] != "\n":
                    i += 1
            else:
                return

    def read_quoted() -> str:
        nonlocal i
        assert text[i] == '"'
        i += 1
        out: list[str] = []
        while i < n and text[i] != '"':
            c = text[i]
            if c == "\\" and i + 1 < n:
                nxt = text[i + 1]
                out.append({"\\": "\\", '"': '"', "n": "\n", "t": "\t"}.get(nxt, nxt))
                i += 2
            else:
                out.append(c)
                i += 1
        i += 1  # closing quote
        return "".join(out)

    def read_value() -> Any:
        nonlocal i
        skip_ws()
        if i >= n:
            return ""
        if text[i] == "{":
            i += 1
            obj: dict[str, Any] = {}
            while True:
                skip_ws()
                if i >= n or text[i] == "}":
                    if i < n:
                        i += 1
                    return obj
                if text[i] == '"':
                    key = read_quoted()
                    val = read_value()
                    obj[key] = val
                else:
                    i += 1  # be forgiving — skip stray bytes
        if text[i] == '"':
            return read_quoted()
        return ""

    # The top of loginusers.vdf is a single quoted key followed by a block
    # (`"users" { ... }`). Read it as one pair so the result is a normal dict.
    skip_ws()
    if i < n and text[i] == '"':
        key = read_quoted()
        return {key: read_value()}
    return {}


def _active_steam_account() -> dict[str, Any] | None:
    """Return {steam_id, account_name, persona_name} for the most-recent Steam
    user on this machine, or None if Steam isn't installed / no user has ever
    signed in. Reads config/loginusers.vdf — Steam updates that file on every
    login, so the data is always current.
    """
    steam = _steam_install_dir()
    if not steam:
        return None
    vdf = steam / "config" / "loginusers.vdf"
    if not vdf.exists():
        return None
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        parsed = _parse_vdf(text)
    except Exception:  # nosec — best-effort against arbitrary on-disk content
        return None
    users = parsed.get("users") if isinstance(parsed, dict) else None
    if not isinstance(users, dict):
        return None
    # Pick the user marked MostRecent=1; if multiple (or none) are flagged,
    # fall back to the highest Timestamp.
    most_recent_id: str | None = None
    most_recent_ts = -1
    for steam_id, info in users.items():
        if not isinstance(info, dict):
            continue
        try:
            ts = int(info.get("Timestamp") or 0)
        except (TypeError, ValueError):
            ts = 0
        if str(info.get("MostRecent") or "0") == "1":
            return {
                "steam_id": steam_id,
                "account_name": str(info.get("AccountName") or ""),
                "persona_name": str(info.get("PersonaName") or ""),
                "timestamp": ts,
            }
        if ts > most_recent_ts:
            most_recent_ts, most_recent_id = ts, steam_id
    if most_recent_id:
        info = users[most_recent_id]
        return {
            "steam_id": most_recent_id,
            "account_name": str(info.get("AccountName") or ""),
            "persona_name": str(info.get("PersonaName") or ""),
            "timestamp": most_recent_ts,
        }
    return None


# ---------- Marvel Rivals: local UID detection ----------
# The game writes one folder per signed-in Marvel Rivals account to its local
# config dir (e.g. <Saved>/Saved/Config/326631126/). We use these as the
# authoritative list of accounts that have been played on this PC — better
# than Steam VDF because it captures both Steam and NetEase-launcher logins.

def _marvel_rivals_data_dir() -> Path | None:
    """Locate Marvel Rivals' local config root (parent of the per-UID folders)."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return None
        cand = Path(local) / "Marvel" / "Saved" / "Saved" / "Config"
        return cand if cand.exists() else None
    # The Windows client is the only supported target. Marvel Rivals does not
    # have an official macOS/Linux release at time of writing; if a future
    # port lands here, extend with platform-specific paths.
    return None


def _detected_rivals_uids() -> list[str]:
    """List numeric UID folder names found under the game's local Config dir.

    UIDs are 7-10 digit integers — anything else (e.g. the side-by-side
    'MarvelUserSetting.json' file) is filtered out.
    """
    base = _marvel_rivals_data_dir()
    if not base:
        return []
    out: list[str] = []
    try:
        for child in base.iterdir():
            name = child.name
            if child.is_dir() and name.isdigit() and 6 <= len(name) <= 11:
                out.append(name)
    except OSError:
        return []
    return sorted(out)


# ---------- self-update ----------
# Strategy: the packaged .exe checks the GitHub releases feed for a newer
# version on boot, and applies the update in-place by renaming the running
# exe to <name>.old.exe and writing the new download to the original path.
# Windows allows renaming a running executable (rename is a metadata op,
# not a content modification), so the swap works without a helper process —
# the user just relaunches once and the new binary is what runs.

def _version_tuple(s: str) -> tuple[int, ...]:
    """Lossy semver-ish parse for `1.6.0` / `v1.6.0`. Returns (0,) on garbage."""
    s = (s or "").strip().lstrip("vV")
    parts = []
    for chunk in s.split("."):
        m = re.match(r"\d+", chunk)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts) or (0,)


def _fetch_latest_release() -> dict[str, Any] | None:
    """Hit the GitHub releases feed for the latest tag. Returns None on any
    error — the caller treats that as 'no update available' (silent failure
    is the right default for a background check)."""
    headers = {
        "User-Agent": f"{APP_NAME}/{APP_VERSION}",
        "Accept": "application/vnd.github+json",
    }
    req = urllib.request.Request(GITHUB_RELEASES_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _release_asset_url(release: dict[str, Any] | None) -> str | None:
    """Pull the Windows .exe download URL out of a release payload."""
    if not isinstance(release, dict):
        return None
    for asset in release.get("assets") or []:
        if asset.get("name") == UPDATE_ASSET_NAME:
            return asset.get("browser_download_url")
    return None


def _release_checksum_url(release: dict[str, Any] | None) -> str | None:
    """Pull the URL of the sibling SHA256 file (e.g. `<exe>.sha256`) out of
    a release payload, or None if this release didn't ship one (e.g. <= v1.6.0,
    which predates checksum publishing)."""
    if not isinstance(release, dict):
        return None
    for asset in release.get("assets") or []:
        if asset.get("name") == UPDATE_CHECKSUM_NAME:
            return asset.get("browser_download_url")
    return None


def _fetch_expected_checksum(url: str) -> str | None:
    """Download a tiny checksum file (one line: `<64-hex-chars>`) and return
    the normalized lowercase hex digest. Returns None on any error — the
    caller should treat a missing checksum as "no verification possible"
    and decide whether that's acceptable (it is for releases that predate
    this feature)."""
    headers = {"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", errors="replace").strip()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    # Accept either bare hex or `<hex>  filename` (the sha256sum format).
    token = raw.split()[0] if raw else ""
    token = token.lower()
    if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
        return token
    return None


def _sha256_of_file(path: Path) -> str:
    """Stream-read a file and return its hex SHA256."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_github_download_url(url: str | None) -> bool:
    """True if a URL is an HTTPS GitHub release-download host. The download
    URLs come from the GitHub API response (fetched over TLS), but we re-check
    the host before fetching so a spoofed/MITM'd API body can't redirect the
    download — and therefore the eventual exe swap — to an attacker host."""
    if not url:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (
        host == "github.com" or host.endswith(".github.com")
        or host == "objects.githubusercontent.com"
        or host.endswith(".githubusercontent.com")
    )


def _is_packaged() -> bool:
    """True when running as the PyInstaller-built .exe — the only path where
    self-update is meaningful. Running from source returns False and the
    update endpoints become no-ops."""
    return bool(getattr(sys, "frozen", False))


def _cleanup_post_update() -> None:
    """Delete the .old.exe left behind by a previous successful update.

    Called at app startup. After an auto-restart the just-replaced process is
    usually still exiting, and Windows keeps a running exe's on-disk image
    locked — so a single unlink often loses the race and the .old.exe lingers
    on the user's Desktop. Reap it on a short-lived background thread that
    retries for a few seconds (the prior process exits well within that), so
    startup never blocks on it."""
    if not _is_packaged():
        return
    exe = Path(sys.executable)
    old = exe.with_name(exe.stem + ".old" + exe.suffix)
    if not old.exists():
        return

    def _reap() -> None:
        for _ in range(20):  # ~6s total — prior process unlocks its image fast
            try:
                old.unlink()
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.3)

    threading.Thread(target=_reap, daemon=True).start()


def _scrub_pyinstaller_env() -> dict[str, str]:
    """Copy the environment minus PyInstaller's onefile bootloader markers.

    A onefile child that inherits `_MEIPASS2` (and the 6.x `_PYI_*` set) skips
    its own extraction and reuses the *parent's* `_MEIxxxx` temp dir — which the
    parent deletes on exit, crashing the child with TemplateNotFound. Stripping
    these forces the successor to unpack cleanly. This is the exact bug that got
    auto-restart reverted in 2.8.4 (see the note in apply_update)."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("_MEI", "_PYI"))}


def _spawn_successor() -> bool:
    """Launch a fresh copy of the just-installed exe to take over once this
    process exits. Returns True if spawned. Packaged builds only — from source
    `sys.executable` is the interpreter, not our app, so we never relaunch it.

    The successor is told our PID via `--await-pid` so it waits for us to exit
    (releasing the single-instance lock) before claiming it for itself."""
    if not _is_packaged():
        return False
    exe = Path(sys.executable)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            [str(exe), "--await-pid", str(os.getpid())],
            env=_scrub_pyinstaller_env(),
            cwd=str(exe.parent),
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return True
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    """True if `pid` is still running. Best-effort and cross-platform."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            k = ctypes.windll.kernel32
            h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False  # not found / already reaped → gone
            try:
                code = wintypes.DWORD()
                if k.GetExitCodeProcess(h, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return False
            finally:
                k.CloseHandle(h)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _await_process_exit(pid: int, timeout: float = 10.0) -> None:
    """Block until `pid` is gone (or `timeout` elapses). Used by an
    update-restarted successor so it doesn't race the predecessor for the
    single-instance lock."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.1)


def _run_update_selftest(role: str, sentinel: str) -> int:
    """Regression harness for the in-app update self-restart (hidden flags).

    Reproduces the 2.8.2/2.8.3 failure mode on a *real packaged build* and
    proves the fix. The crash only ever happened when a packaged parent spawned
    a child that inherited the parent's `_MEIPASS2` onefile bootloader marker —
    so the child reused the parent's `_MEIxxxx` extraction dir and lost its
    bundled files the moment the parent exited and deleted that dir.

      role 'spawn'  (parent) — print our extraction dir, spawn a 'verify' child
        with the scrubbed env exactly as _spawn_successor does, then exit so the
        parent's _MEIxxxx dir is torn down.
      role 'verify' (child)  — record our own extraction dir, wait past the
        parent's exit, then confirm our bundled template is STILL readable. If
        we'd reused the parent's dir it would now be gone.

    The driver (tests/update_hop_test.py) asserts CHILD_MEI != PARENT_MEI and
    STAGE2=OK. No-op from source, where there is no extraction dir to clobber.
    """
    if role == "spawn":
        # Builds are --windowed (no console), so signal via the sentinel file,
        # not stdout. Parent line first; the verify child appends to it.
        Path(sentinel).write_text(f"PARENT_MEI={RESOURCE_DIR}\n", encoding="utf-8")
        exe = Path(sys.executable)
        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [str(exe), "--selftest-verify", sentinel],
            env=_scrub_pyinstaller_env(),
            cwd=str(exe.parent),
            close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags,
        )
        return 0

    if role == "restart":
        # Exercise the REAL _spawn_successor() end-to-end (minus the GitHub
        # download): the successor must wait for us to exit, take the lock, load
        # its resources, and reach the UI stage — where the MRAT_RESTART_SENTINEL
        # hook in main() records it. Propagated to the child via the env copy.
        Path(sentinel).write_text(f"PARENT_MEI={RESOURCE_DIR}\n", encoding="utf-8")
        os.environ["MRAT_RESTART_SENTINEL"] = sentinel
        _spawn_successor()
        return 0

    # role == "verify"
    mei = str(RESOURCE_DIR)
    tpl = Path(app.template_folder) / "index.html"
    with open(sentinel, "a", encoding="utf-8") as f:
        f.write(f"CHILD_MEI={mei}\nSTAGE1=alive\n")
    time.sleep(3.0)  # let the parent exit and delete its _MEIxxxx dir
    ok = tpl.is_file()
    if ok:
        try:
            tpl.read_bytes()  # prove it's actually readable, not just listed
        except OSError:
            ok = False
    with open(sentinel, "a", encoding="utf-8") as f:
        f.write(f"STAGE2={'OK' if ok else 'TEMPLATE_GONE'}\n")
    return 0 if ok else 3


app = Flask(
    __name__,
    static_folder=str(RESOURCE_DIR / "static"),
    static_url_path="/static",
    template_folder=str(RESOURCE_DIR / "templates"),
)

# Methods that can't change state — no CSRF protection needed.
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Hostnames the app is ever served from (it binds 127.0.0.1 only).
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _is_loopback_origin(value: str) -> bool:
    """True if an Origin/Referer header points at our own loopback host.

    Port is intentionally ignored: the app binds an ephemeral port, and the
    threat we're blocking is a *remote* site (a different host), not another
    local port. Any non-loopback host — or an unparseable value — fails.
    """
    if not value:
        return False
    try:
        host = urllib.parse.urlsplit(value).hostname
    except ValueError:
        return False
    return (host or "").lower() in _LOOPBACK_HOSTS


@app.before_request
def _block_cross_origin():
    """Reject state-changing requests that didn't originate from the app's
    own page. This closes the localhost-CSRF surface: because the server is
    bound to 127.0.0.1 on a random port with no CORS headers, a malicious web
    page can't *read* responses, but without this guard it could still fire
    blind cross-site POST/PUT/DELETE side effects (delete accounts, force an
    exe self-update, shut the app down). We trust the browser-set Origin /
    Referer headers, which cannot be forged by cross-site JavaScript.

    The Host check below runs on ALL methods, GET/HEAD included. With the
    port now fixed (DEFAULT_PORT) a DNS-rebinding page — an attacker domain
    re-resolving to 127.0.0.1 — would be same-origin for its own hostname
    and could otherwise *read* responses (GET /api/accounts serves decrypted
    passwords while the vault is unlocked). The browser preserves the
    attacker hostname in Host, so rejecting non-loopback Hosts kills
    rebinding outright."""
    try:
        host = urllib.parse.urlsplit("//" + (request.host or "")).hostname or ""
    except ValueError:
        host = ""
    if host.lower() not in _LOOPBACK_HOSTS:
        return jsonify({"error": "forbidden",
                        "message": "Bad Host header."}), 403
    if request.method in _SAFE_METHODS:
        return None
    origin = request.headers.get("Origin")
    # Origin is sent by browsers on every non-safe cross-origin request
    # (fetch, sendBeacon, and cross-site form posts). Prefer it.
    if origin is not None:
        if _is_loopback_origin(origin):
            return None
    # Some same-origin requests omit Origin; fall back to Referer's host.
    elif _is_loopback_origin(request.headers.get("Referer", "")):
        return None
    return jsonify({"error": "forbidden",
                    "message": "Cross-origin request blocked."}), 403


@app.errorhandler(OSError)
def _os_error_handler(e: OSError):
    """Return a structured JSON error for any filesystem failure inside a
    route — primarily _write_vault hitting a Windows lock contention that
    didn't clear within the retry budget. The frontend toast pulls the
    `message` field so the user sees the actual culprit instead of a
    generic 'try again'."""
    app.logger.warning("Filesystem error in route: %s: %s", e.__class__.__name__, e)
    # Keep the class name (helps the user distinguish "file locked" from "disk
    # full") but don't echo the full exception string — it can carry absolute
    # filesystem paths.
    return jsonify({
        "error": "io_error",
        "message": f"Couldn't write to vault.json ({e.__class__.__name__}). "
                   "Close anything that may have it open and try again.",
    }), 500


@app.errorhandler(Exception)
def _generic_error_handler(e: Exception):
    """Last-resort JSON wrapper so unhandled exceptions don't fall through
    to Flask's HTML debug page, which the fetch-based UI can't parse."""
    # Let Werkzeug's HTTPException subclasses (404, 405, etc.) through with
    # their normal status — only wrap genuine 5xx surprises.
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    app.logger.exception("Unhandled exception in route")
    # Don't reflect the exception text (class name + message, sometimes paths
    # or internal detail) to the client — it's logged above for debugging.
    return jsonify({
        "error": "server_error",
        "message": "Something went wrong handling that request.",
    }), 500

_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "key": None,            # bytes | None
    "last_activity": 0.0,   # epoch seconds
}

# Serializes vault read-modify-write across endpoints. The server runs with
# threaded=True, so without this a refresh-all (which mutates account-by-account
# over many seconds) and a concurrent mutation (single-account refresh, create /
# update / delete / reorder / import, options) would race on the whole-file
# write and clobber each other. Every mutating endpoint now does its read +
# modify + write inside this lock (re-reading fresh so it only overwrites with
# its own change); refresh-all takes it per account via _commit_updates. Reads
# (GET /api/accounts) don't need it — _write_vault swaps the file atomically
# (os.replace), so a reader always sees a complete old or new file. Reentrant so
# nested helpers under an already-held lock don't deadlock.
_vault_write_lock = threading.RLock()
# Single-flight guard for refresh-all (shared by the JSON + streaming endpoints)
# so two tabs / a double-submit can't run concurrent sweeps and double-hammer
# tracker.gg. Acquired non-blocking; a second caller gets 409 busy.
_refresh_all_lock = threading.Lock()


# ---------- vault file helpers ----------

def _read_vault() -> dict[str, Any]:
    if not VAULT_PATH.exists():
        return {"initialized": False, "accounts": []}
    try:
        return json.loads(VAULT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        # A corrupt vault (truncated write, disk error, AV mangling the tmp
        # file) must not blow up mid-route with an opaque JSONDecodeError.
        # Re-raise as OSError so the existing OSError-aware callers and the
        # @app.errorhandler(OSError) path surface a clean "vault unreadable".
        raise OSError(f"vault.json is corrupt or unreadable: {e}") from e


def _backup_current_vault() -> None:
    if not VAULT_PATH.exists():
        return
    payload = VAULT_PATH.read_bytes()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for backup_dir, keep in ((BACKUP_DIR, BACKUP_KEEP), (EXTRA_BACKUP_DIR, EXTRA_BACKUP_KEEP)):
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"vault-{stamp}.json"
            i = 0
            while backup.exists():
                i += 1
                backup = backup_dir / f"vault-{stamp}-{i}.json"
            backup.write_bytes(payload)
            for old in sorted(backup_dir.glob("vault-*.json"))[:-keep]:
                try:
                    old.unlink()
                except OSError:
                    pass
        except OSError:
            # never fail the actual save if a backup dir is unreachable
            pass


def _write_vault(vault: dict[str, Any], backup: bool = True) -> None:
    # Rank refreshes and match-history pulls rewrite the vault constantly but
    # never touch credentials, so snapshotting the whole file (to two dirs,
    # one OneDrive-synced) on every one of those is what drives the antivirus
    # lock-contention retries below. Callers that only change metadata pass
    # backup=False; anything touching password_enc keeps the default.
    if backup:
        _backup_current_vault()
    VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = VAULT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(vault, indent=2), encoding="utf-8")
    # os.replace on Windows can transiently fail with PermissionError when an
    # antivirus / backup tool (OneDrive, Defender real-time scan, etc.) has
    # the destination open. Retry a few times with a short backoff before
    # giving up — most lock contention clears in <1s.
    last_err: OSError | None = None
    for attempt in range(5):
        try:
            os.replace(tmp, VAULT_PATH)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.15 * (attempt + 1))
    # Final failure — clean up the orphaned tmp file so the next save isn't
    # blocked by leftover state, then re-raise so the route can surface it.
    try:
        tmp.unlink()
    except OSError:
        pass
    raise last_err  # type: ignore[misc]


# ---------- config / lockout ----------

def _lockout_minutes() -> int:
    """User-configured idle auto-lock window, in minutes (0 = never)."""
    try:
        vault = _read_vault()
    except (OSError, json.JSONDecodeError):
        return DEFAULT_LOCKOUT_MINUTES
    try:
        m = int(vault.get("config", {}).get("lockout_minutes", DEFAULT_LOCKOUT_MINUTES))
    except (TypeError, ValueError):
        m = DEFAULT_LOCKOUT_MINUTES
    return max(0, m)


def _ui_options() -> dict[str, Any]:
    """Client UI preferences (view, density, hide toggles, …) persisted in the
    vault so they survive restarts. localStorage alone can't do that: the app's
    port — and with it the browser origin that scopes localStorage — used to
    change every launch."""
    try:
        ui = (_read_vault().get("config", {}) or {}).get("ui_options")
    except (OSError, json.JSONDecodeError):
        return {}
    return ui if isinstance(ui, dict) else {}


def _idle_timeout_s() -> int | None:
    """Idle timeout in seconds, or None when auto-lock is disabled."""
    m = _lockout_minutes()
    return m * 60 if m > 0 else None


# ---------- migrations ----------

def _migrate_from_app_folder() -> None:
    """First-run upgrade: import a vault.json that lived next to an older
    script-style install into the per-user data directory.

    Copies (never moves) so the original stays untouched as a safety net.
    """
    if VAULT_PATH.exists():
        return
    candidates = [Path(__file__).parent / "vault.json"]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "vault.json")
    for src in candidates:
        try:
            if src.exists() and src.resolve() != VAULT_PATH.resolve():
                shutil.copy2(src, VAULT_PATH)
                print(f"  Imported existing vault from {src}")
                return
        except OSError:
            pass


def _migrate_vault_if_needed() -> None:
    """Backfill new schema fields onto an existing vault without losing data.

    Encrypted material (kdf / verifier / password_enc) is left untouched, so
    the current master password keeps working. Provider credentials and cached
    provider-only records removed in v2.15 are pruned here; _write_vault keeps
    the normal timestamped backup as a rollback path.
    """
    if not VAULT_PATH.exists():
        return
    try:
        vault = _read_vault()
    except (OSError, json.JSONDecodeError):
        return
    changed = False
    cfg = vault.get("config")
    if not isinstance(cfg, dict):
        vault["config"] = {"lockout_minutes": DEFAULT_LOCKOUT_MINUTES}
        cfg = vault["config"]
        changed = True
    elif "lockout_minutes" not in cfg:
        cfg["lockout_minutes"] = DEFAULT_LOCKOUT_MINUTES
        changed = True
    for legacy_key in ("marvel_rivals_api_key", "rivals_api_usage"):
        if legacy_key in cfg:
            del cfg[legacy_key]
            changed = True
    for acct in vault.get("accounts", []):
        for legacy_field in (
            "tracker_private",
            "rivals_update_requested_at",
            "recent_matches",
            "matches_synced_at",
            "matches_error",
        ):
            if legacy_field in acct:
                del acct[legacy_field]
                changed = True
        refresh_error = str(acct.get("last_refresh_error") or "").lower()
        refresh_status = acct.get("last_refresh_status")
        refresh_source = acct.get("last_refresh_source")
        legacy_provider_refresh = (
            refresh_source == "marvelrivalsapi"
            # v2.14 did not attach last_refresh_source on every provider
            # failure, so also recognize its literal name in stored errors.
            or "marvelrivalsapi" in refresh_error
            # All v2.15 Tracker outcomes except missing identity carry an
            # explicit source. A source-less error/not-found result therefore
            # belongs to the retired fallback (including its generic HTTP and
            # network messages, which did not name the provider).
            or (not refresh_source and refresh_status in ("error", "not_found"))
        )
        if legacy_provider_refresh:
            acct["last_refresh_ts"] = None
            acct["last_refresh_status"] = None
            acct["last_refresh_code"] = None
            acct["last_refresh_source"] = None
            acct["last_refresh_error"] = None
            changed = True
        if acct.get("last_refresh_status") in ("bad_key", "private"):
            acct["last_refresh_status"] = None
            acct["last_refresh_source"] = None
            acct["last_refresh_error"] = None
            changed = True
        for field, default in ACCOUNT_FIELD_DEFAULTS.items():
            if field not in acct:
                acct[field] = default
                changed = True
    if changed:
        _write_vault(vault)


# ---------- crypto helpers ----------

def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=KEY_LEN, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return kdf.derive(password.encode("utf-8"))


def _encrypt(key: bytes, plaintext: str) -> dict[str, str]:
    aes = AESGCM(key)
    nonce = secrets.token_bytes(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return {"nonce": nonce.hex(), "ct": ct.hex()}


def _decrypt(key: bytes, blob: dict[str, str]) -> str:
    aes = AESGCM(key)
    return aes.decrypt(bytes.fromhex(blob["nonce"]), bytes.fromhex(blob["ct"]), None).decode("utf-8")


# ---------- "remember me" via Windows DPAPI ------------------------------------
# When the user ticks "remember me" on unlock we stash the scrypt-derived vault
# key in a small file, encrypted with the Windows logged-in user's credentials
# (DPAPI). On next launch, if the file exists and decrypts, we skip the unlock
# screen entirely. The blob can ONLY be decrypted by the same Windows user on
# the same machine — copying the file to another PC/account makes it useless.
#
# Non-Windows platforms: the helpers no-op and remember-me is unavailable.

REMEMBER_FILE = "remembered_key.bin"


def _dpapi_call(func_name: str, in_bytes: bytes, entropy: bytes | None) -> bytes | None:
    """Wrap CryptProtectData / CryptUnprotectData via ctypes. Returns the
    transformed bytes, or None on any failure / non-Windows platform.

    `entropy` is the optional secondary entropy BLOB (pOptionalEntropy): the
    same value must be supplied to protect and unprotect. It binds the blob to
    this app + vault so a process that just sweeps DPAPI blobs (common in
    credential-stealer malware) can't decrypt it by calling CryptUnprotectData
    with no entropy."""
    if sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes as wt

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _mk_blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return _Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    in_blob, _in_buf = _mk_blob(in_bytes)
    ent_ref = None
    if entropy:
        ent_blob, _ent_buf = _mk_blob(entropy)
        ent_ref = ctypes.byref(ent_blob)
    out_blob = _Blob()
    try:
        fn = getattr(ctypes.windll.crypt32, func_name)
    except (AttributeError, OSError):
        return None
    # Signature: BOOL fn(BLOB* in, LPCWSTR descr, BLOB* entropy, void*, void*, DWORD flags, BLOB* out)
    ok = fn(ctypes.byref(in_blob), None, ent_ref, None, None, 0, ctypes.byref(out_blob))
    if not ok:
        return None
    try:
        result = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    except OSError:
        return None
    ctypes.windll.kernel32.LocalFree(out_blob.pbData)
    return result


# App + vault-bound secondary entropy for the remember-me blob. The static tag
# alone defeats blind entropy-less DPAPI sweeps; mixing in the per-vault scrypt
# salt also binds the blob to this specific vault, so a remembered_key.bin can't
# be reused against a different vault even under the same Windows user.
_REMEMBER_ENTROPY_TAG = b"MarvelAccountKeeper::remember-me::v1"


def _remember_entropy() -> bytes:
    """Secondary DPAPI entropy: the app tag plus this vault's scrypt salt
    (best-effort — falls back to just the tag if the salt can't be read)."""
    salt = b""
    try:
        kdf = (_read_vault() or {}).get("kdf") or {}
        salt = bytes.fromhex(kdf.get("salt") or "")
    except (OSError, ValueError, json.JSONDecodeError):
        salt = b""
    return _REMEMBER_ENTROPY_TAG + salt


def _dpapi_protect(data: bytes) -> bytes | None:
    return _dpapi_call("CryptProtectData", data, _remember_entropy())


def _dpapi_unprotect(blob: bytes) -> bytes | None:
    return _dpapi_call("CryptUnprotectData", blob, _remember_entropy())


def remember_supported() -> bool:
    """True when remember-me can actually persist (Windows + DPAPI usable)."""
    return sys.platform == "win32"


def _remember_path() -> Path:
    return _data_dir() / REMEMBER_FILE


def _save_remembered_key(key: bytes) -> bool:
    """Stash the derived vault key via DPAPI. Best-effort — failures are
    silently swallowed since the user can always type the password again."""
    if not remember_supported():
        return False
    blob = _dpapi_protect(key)
    if blob is None:
        return False
    try:
        _remember_path().write_bytes(blob)
        return True
    except OSError:
        return False


def _load_remembered_key() -> bytes | None:
    """Return the DPAPI-protected key, decrypted, or None if missing/corrupt."""
    if not remember_supported():
        return None
    p = _remember_path()
    if not p.exists():
        return None
    try:
        blob = p.read_bytes()
    except OSError:
        return None
    return _dpapi_unprotect(blob)


def _clear_remembered_key() -> None:
    """Wipe the persisted key. Called on explicit lock or password change."""
    try:
        _remember_path().unlink(missing_ok=True)
    except OSError:
        pass


def _try_remembered_unlock(vault: dict[str, Any]) -> bool:
    """Verify a persisted key against the vault. On success, install it as the
    active session key. Called once at startup so a remembered session skips
    the unlock screen entirely."""
    key = _load_remembered_key()
    if key is None or not vault.get("initialized"):
        return False
    verifier = vault.get("verifier")
    if not isinstance(verifier, dict):
        return False
    try:
        plain = _decrypt(key, verifier)
    except Exception:
        # Stale key (probably from a password change) — wipe so we don't keep
        # trying it.
        _clear_remembered_key()
        return False
    if not hmac.compare_digest(plain, VERIFIER_PLAINTEXT.decode("utf-8")):
        _clear_remembered_key()
        return False
    with _state_lock:
        _state["key"] = key
        _state["last_activity"] = time.time()
    return True


# ---------- session ----------

def _current_key() -> bytes | None:
    with _state_lock:
        key = _state["key"]
        if key is None:
            return None
        timeout = _idle_timeout_s()
        if timeout is not None and time.time() - _state["last_activity"] > timeout:
            _state["key"] = None
            return None
        _state["last_activity"] = time.time()
        return key


def _require_key():
    key = _current_key()
    if key is None:
        return None, (jsonify({"error": "locked"}), 401)
    return key, None


def _touch_activity() -> None:
    """Bump the idle-timeout clock without re-validating the key. A long
    refresh-all is a single HTTP request, so _require_key only runs once at the
    start — without this the session could idle-expire mid/post-stream. Called
    as each account commits so an active sweep keeps the session alive."""
    with _state_lock:
        if _state["key"] is not None:
            _state["last_activity"] = time.time()


# ---------- account (de)serialization ----------

def _coerce_points(value: Any) -> int | None:
    """Absolute Marvel Rivals MMR/SR for any tier. Blank or non-numeric -> None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------- Marvel Rivals rank parsing ----------

# In-app rank vocabulary (kept in sync with static/data.js RANK_TIERS).
_RANK_TIERS = {
    "Bronze III", "Bronze II", "Bronze I",
    "Silver III", "Silver II", "Silver I",
    "Gold III", "Gold II", "Gold I",
    "Platinum III", "Platinum II", "Platinum I",
    "Diamond III", "Diamond II", "Diamond I",
    "Grandmaster III", "Grandmaster II", "Grandmaster I",
    "Celestial III", "Celestial II", "Celestial I",
    "Eternity", "One Above All",
}

_TIER_FAMILIES = {
    "bronze": "Bronze", "silver": "Silver", "gold": "Gold",
    "platinum": "Platinum", "diamond": "Diamond",
    "grandmaster": "Grandmaster", "celestial": "Celestial",
    "eternity": "Eternity", "oneaboveall": "One Above All",
    "one above all": "One Above All",
}
_DIVISION_TO_ROMAN = {"1": "I", "2": "II", "3": "III",
                     "i": "I", "ii": "II", "iii": "III"}


def _normalize_rank(value: Any) -> str:
    """Map whatever the API gives us to our canonical tier string ('Diamond III').

    Accepts strings ('Diamond 3', 'diamond_iii', 'DiamondIII'), dicts
    ({level: 'Diamond', division: 3}), or None. Returns '' when the value
    can't be mapped to a known tier.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        tier = value.get("tier") or value.get("level") or value.get("name") or ""
        div = value.get("division") or value.get("sub") or value.get("subTier") or ""
        if tier:
            value = f"{tier} {div}".strip()
        else:
            return ""
    if not isinstance(value, str):
        return ""
    s = value.strip()
    if not s:
        return ""
    # Direct hit on a canonical tier
    if s in _RANK_TIERS:
        return s
    # Decompose into family + division
    low = s.lower().strip()
    if low in _TIER_FAMILIES and _TIER_FAMILIES[low] in {"Eternity", "One Above All"}:
        return _TIER_FAMILIES[low]
    # Pull a family token + a division token
    fam = None
    for token, canonical in _TIER_FAMILIES.items():
        if token in low:
            fam = canonical
            break
    if fam in {"Eternity", "One Above All"} and fam:
        return fam
    if fam is None:
        return ""
    m = re.search(r"\b(iii|ii|i|1|2|3)\b", low)
    if not m:
        # Some APIs return just the tier; pick III as the "entry" division.
        return f"{fam} III"
    div = _DIVISION_TO_ROMAN.get(m.group(1), "")
    if not div:
        return ""
    candidate = f"{fam} {div}"
    return candidate if candidate in _RANK_TIERS else ""


# Ordered list mirroring static/data.js RANK_TIERS for comparing rank strings.
_RANK_ORDER = [
    "Bronze III", "Bronze II", "Bronze I",
    "Silver III", "Silver II", "Silver I",
    "Gold III", "Gold II", "Gold I",
    "Platinum III", "Platinum II", "Platinum I",
    "Diamond III", "Diamond II", "Diamond I",
    "Grandmaster III", "Grandmaster II", "Grandmaster I",
    "Celestial III", "Celestial II", "Celestial I",
    "Eternity", "One Above All",
]
_RANK_INDEX = {r: i for i, r in enumerate(_RANK_ORDER)}


def _parse_api_timestamp(value: Any) -> int | None:
    """Parse an upstream profile timestamp into epoch seconds."""
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%m/%d/%Y, %I:%M:%S %p", "%m/%d/%Y, %H:%M:%S", "%m/%d/%Y",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            return calendar.timegm(time.strptime(value.strip(), fmt))
        except ValueError:
            continue
    return None


def _parse_tracker_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract rank/score/peak from a tracker.gg `/profile/ign/<name>` response.

    Schema (live, May 2026):
      data.platformInfo.platformUserHandle    → IGN (canonical case)
      data.metadata.isPC                      → bool
      data.metadata.level                     → account level
      data.metadata.isPrivateCareerOverview   → bool (rare even for private profiles)
      data.segments[type=overview].stats.ranked        → {value, displayValue, metadata.tierName}
      data.segments[type=overview].stats.peakRanked    → season peak (same tier object shape)
      data.segments[type=overview].stats.lifetimePeakRanked → just value, no tier
      data.segments[type=ranked-peaks].stats.peakTiers.value
          → array of {value, metadata: {tierName, season, ...}} across all seasons
    Lifetime peak tier comes from the max-value entry of the peakTiers array,
    NOT lifetimePeakRanked alone (which carries no tier name).
    """
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    out: dict[str, Any] = {}

    # Pull IGN canonical case from tracker (handles renames + casing).
    pinfo = data.get("platformInfo") or {}
    handle = pinfo.get("platformUserHandle")
    if isinstance(handle, str) and handle.strip():
        out["tracker_handle"] = handle.strip()

    # NetEase UID — tracker carries it in platformInfo. Capturing it here is
    # what lets name-based accounts populate a rivals_uid, which unlocks stable
    # UID-keyed lookups and a direct RivalsData profile link. Guard to a
    # plausible UID shape (digits, 6-11 long
    # — matching _detected_rivals_uids) so a platform slug never lands here.
    uid_raw = pinfo.get("platformUserId") or pinfo.get("platformUserIdentifier")
    uid_digits = re.sub(r"\D", "", str(uid_raw or ""))
    if 6 <= len(uid_digits) <= 11:
        out["rivals_uid"] = uid_digits

    # Synced timestamp — tracker exposes a `lastUpdated` ISO field. tracker
    # zeroes this to the 0001-01-01 / 2000-01-01 sentinels when it has no real
    # crawl time (notably when match history is private), so reject both.
    meta = data.get("metadata") or {}
    last_updated = (meta.get("lastUpdated") or {}).get("value")
    if (isinstance(last_updated, str) and last_updated
            and not last_updated.startswith(("2000-", "0001-"))):
        parsed_ts = _parse_api_timestamp(last_updated)
        if parsed_ts:
            out["rivals_synced_at"] = parsed_ts

    # Match-history privacy. When the profile's battle history is private,
    # tracker still serves ranks (overview public) but the "last updated" age
    # reflects last *public* activity, not last login — so it must NOT be
    # presented as a dormant/last-played signal. DISPLAY-ONLY hint; the rank
    # itself is the latest tracker has and stays authoritative.
    out["tracker_history_private"] = bool(meta.get("isPrivateBattleHistory"))

    # Current rank + SR from overview segment, season-scoped to current season.
    cur_tier = None
    cur_score = None
    season_peak_tier = None
    season_peak_score = None
    for seg in (data.get("segments") or []):
        if seg.get("type") != "overview":
            continue
        stats = seg.get("stats") or {}
        ranked = stats.get("ranked") or {}
        rmeta = ranked.get("metadata") or {}
        if rmeta.get("tierName"):
            cur_tier = _normalize_rank(rmeta["tierName"])
            cur_score = _round_score(ranked.get("value"))
        peak_seg = stats.get("peakRanked") or {}
        pmeta = peak_seg.get("metadata") or {}
        if pmeta.get("tierName"):
            season_peak_tier = _normalize_rank(pmeta["tierName"])
            season_peak_score = _round_score(peak_seg.get("value"))
        break

    # Lifetime peak: scan ranked-peaks segment, pick max-value entry.
    lifetime_peak_tier = None
    lifetime_peak_score = None
    for seg in (data.get("segments") or []):
        if seg.get("type") != "ranked-peaks":
            continue
        peaks = ((seg.get("stats") or {}).get("peakTiers") or {}).get("value")
        if isinstance(peaks, list):
            best = max(peaks, key=lambda e: (e.get("value") or 0) if isinstance(e, dict) else 0,
                       default=None)
            if isinstance(best, dict):
                bmeta = best.get("metadata") or {}
                lifetime_peak_tier = _normalize_rank(bmeta.get("tierName"))
                lifetime_peak_score = _round_score(best.get("value"))
        break

    # Lifetime peak wins over season peak; season peak wins over current.
    peak_tier = lifetime_peak_tier or season_peak_tier or cur_tier
    peak_score = lifetime_peak_score if lifetime_peak_tier else (
        season_peak_score if season_peak_tier else cur_score
    )

    if cur_tier:
        out["current_rank"] = cur_tier
        if cur_score is not None:
            out["current_points"] = cur_score
    if peak_tier:
        # Peak can't be lower than current. Upstream season history can be
        # temporarily sparse, so clamp it to keep the UI internally consistent.
        if cur_tier and _RANK_INDEX.get(cur_tier, -1) > _RANK_INDEX.get(peak_tier, -1):
            peak_tier = cur_tier
            peak_score = cur_score
        out["peak_rank"] = peak_tier
        if peak_score is not None:
            out["peak_points"] = peak_score
    return out


def _tracker_payload_shape_valid(payload: Any) -> bool:
    """Validate only the successful-response containers the parser touches.

    Tracker's endpoint is undocumented. Treat schema drift as an unreadable
    provider response instead of letting a nested list/string abort a whole
    refresh-all stream with an AttributeError.
    """
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    for field in ("platformInfo", "metadata"):
        value = data.get(field)
        if value is not None and not isinstance(value, dict):
            return False
    metadata = data.get("metadata") or {}
    last_updated = metadata.get("lastUpdated")
    if last_updated is not None and not isinstance(last_updated, dict):
        return False
    segments = data.get("segments")
    if segments is None:
        segments = []
    if not isinstance(segments, list):
        return False
    for segment in segments:
        if not isinstance(segment, dict):
            return False
        segment_type = segment.get("type")
        if segment_type not in ("overview", "ranked-peaks"):
            # Ignore unrelated segments exactly as the parser does. Their
            # internal schema may evolve independently of rank data.
            continue
        stats = segment.get("stats")
        if stats is None:
            continue
        if not isinstance(stats, dict):
            return False
        fields = ("ranked", "peakRanked") if segment_type == "overview" \
            else ("peakTiers",)
        for field in fields:
            stat = stats.get(field)
            if stat is not None and not isinstance(stat, dict):
                return False
            if isinstance(stat, dict) and segment_type == "overview":
                stat_meta = stat.get("metadata")
                if stat_meta is not None and not isinstance(stat_meta, dict):
                    return False
            elif isinstance(stat, dict) and segment_type == "ranked-peaks":
                # peakTiers container metadata is not consumed. Validate only
                # its value list and the entry metadata the parser reads.
                peak_values = stat.get("value")
                if peak_values is not None and not isinstance(peak_values, list):
                    return False
                for peak in peak_values or []:
                    if not isinstance(peak, dict):
                        continue
                    peak_meta = peak.get("metadata")
                    if peak_meta is not None and not isinstance(peak_meta, dict):
                        return False
    return True


def _round_score(v: Any) -> int | None:
    """Tracker scores come as float (e.g. 5044.087); rank-score fields in
    the vault are stored as int."""
    if v is None:
        return None
    try:
        number = float(v)
        return int(round(number)) if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


_tracker_scraper = None
_tracker_scraper_lock = threading.Lock()
_tracker_rate_limit_lock = threading.Lock()
_tracker_rate_limited_until = 0.0  # monotonic clock; process-local by design


def _tracker_rate_limit_remaining() -> int:
    """Seconds left in Tracker's provider-wide Retry-After window."""
    with _tracker_rate_limit_lock:
        remaining = _tracker_rate_limited_until - time.monotonic()
    return max(0, math.ceil(remaining))


def _remember_tracker_rate_limit(retry_after: int | None) -> int:
    """Extend the provider-wide guard and return its current remaining time."""
    global _tracker_rate_limited_until
    seconds = max(1, int(retry_after or 60))
    with _tracker_rate_limit_lock:
        _tracker_rate_limited_until = max(
            _tracker_rate_limited_until,
            time.monotonic() + seconds,
        )
    return _tracker_rate_limit_remaining()


def _get_tracker_scraper():
    """Lazy-init a cloudscraper session. Creating one is slow (it primes a
    real Chrome JA3/TLS fingerprint), so we reuse a single instance across
    calls. None on import failure — the caller falls back to plain urllib."""
    global _tracker_scraper
    with _tracker_scraper_lock:
        if _tracker_scraper is not None:
            return _tracker_scraper
        try:
            import cloudscraper
            _tracker_scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "desktop": True},
            )
        except ImportError:
            _tracker_scraper = False  # sentinel so we don't retry every call
        return _tracker_scraper or None


def _fetch_tracker_player(ign: str) -> tuple[int, dict[str, Any] | None, int | None]:
    """Call tracker.gg's player profile endpoint. Returns (status, body, retry_after).

    Uses cloudscraper to bypass Cloudflare's JS bot-challenge — without that,
    tracker.gg returns 403 on every backend hit. Falls back to plain urllib
    if cloudscraper isn't importable (e.g. source-run with deps missing)."""
    url = f"{TRACKER_API_BASE}/profile/ign/{urllib.parse.quote(ign, safe='')}"
    scraper = _get_tracker_scraper()
    if scraper is not None:
        try:
            r = scraper.get(url, timeout=TRACKER_TIMEOUT_S, headers={
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://tracker.gg/",
                "Origin": "https://tracker.gg",
            })
        except Exception:
            return 0, None, None
        try:
            body = r.json() if r.content else None
        except (ValueError, json.JSONDecodeError):
            body = None
        retry = None
        ra = r.headers.get("Retry-After") if hasattr(r, "headers") else None
        try:
            retry = int(ra) if ra else None
        except (TypeError, ValueError):
            pass
        return r.status_code, body, retry

    # Fallback path — plain urllib. Works for some accounts, blocked for most.
    headers = {
        "User-Agent": TRACKER_USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://tracker.gg/",
        "Origin": "https://tracker.gg",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TRACKER_TIMEOUT_S) as r:
            body_raw = r.read()
            try:
                body = json.loads(body_raw.decode("utf-8")) if body_raw else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = None
            return r.status, body, None
    except urllib.error.HTTPError as e:
        retry = _retry_after_s(e.headers) if hasattr(e, "headers") else None
        body_raw = e.read() if hasattr(e, "read") else b""
        try:
            body = json.loads(body_raw.decode("utf-8")) if body_raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        return e.code, body, retry
    except (urllib.error.URLError, TimeoutError, OSError):
        # Keep transport details out of the vault/UI, but return a distinct
        # status sentinel so callers can say this was connectivity rather than
        # incorrectly calling the player missing.
        return 0, None, None


def _retry_after_s(headers: Any) -> int | None:
    """Parse a Retry-After header (delta-seconds form) into an int, or None."""
    try:
        v = headers.get("Retry-After") if headers else None
    except Exception:
        return None
    v = str(v).strip() if v else ""
    return int(v) if v.isdigit() else None


def _tracker_says_private(payload: Any) -> bool:
    """tracker.gg returns HTTP 400 with body.errors[].code == 'CollectorResultStatus::Private'
    for profiles it cannot expose. Treat that as unavailable rather than proof
    of a specific privacy setting; the collector uses the same result for some
    uncrawled and transient cases."""
    if not isinstance(payload, dict):
        return False
    for e in (payload.get("errors") or []):
        if isinstance(e, dict) and "private" in (e.get("code") or "").lower():
            return True
        if isinstance(e, dict) and "private" in (e.get("message") or "").lower():
            return True
    return False


def _try_tracker(acct: dict[str, Any]) -> dict[str, Any]:
    """Fetch one public tracker.gg profile, UID first and then IGN.

    The endpoint resolves a numeric NetEase UID as well as a name. UID-first
    lookup survives renames and special-character/casing issues; a successful
    response can backfill both the canonical IGN and a missing UID. Every path
    returns a user-facing status because there is deliberately no hidden
    fallback provider.
    """
    ign = (acct.get("in_game_name") or "").strip()
    uid = (acct.get("rivals_uid") or "").strip()
    # UID first, IGN fallback. dict.fromkeys dedupes while preserving order in
    # the (unlikely) event the two are equal.
    candidates = list(dict.fromkeys(c for c in (uid, ign) if c))
    if not candidates:
        return {
            "last_refresh_status": "missing_handle",
            "last_refresh_code": "missing_identity",
            "last_refresh_error": "Account has no in-game name or UID to look up.",
        }

    blocked_for = _tracker_rate_limit_remaining()
    if blocked_for:
        return {
            "last_refresh_status": "error",
            "last_refresh_code": "rate_limited",
            "last_refresh_source": "tracker",
            "last_refresh_error": (
                f"Tracker.gg is rate limiting requests. Try again in about "
                f"{blocked_for} seconds."
            ),
            "_retry_after_s": blocked_for,
        }

    saw_private = False
    saw_not_found = False
    saw_empty_profile = False
    # None means no provider/transport failure occurred. Zero is a real
    # sentinel from _fetch_tracker_player for a connection/timeout failure.
    last_failure_status: int | None = None
    for lookup in candidates:
        status, payload, retry_after = _fetch_tracker_player(lookup)
        if status == 429:
            retry_after = _remember_tracker_rate_limit(retry_after)
            return {
                "last_refresh_status": "error",
                "last_refresh_code": "rate_limited",
                "last_refresh_source": "tracker",
                "last_refresh_error": (
                    f"Tracker.gg is rate limiting requests. Try again in "
                    f"about {retry_after} seconds."
                ),
                # Internal control field: callers remove it before any vault
                # write and use it to return/stream an explicit rate-limit.
                "_retry_after_s": retry_after,
            }
        if status == 400 and _tracker_says_private(payload):
            saw_private = True
            continue
        if status == 404:
            saw_not_found = True
            continue
        if status != 200 or not isinstance(payload, dict):
            last_failure_status = status
            continue
        if not _tracker_payload_shape_valid(payload):
            last_failure_status = 200
            continue
        try:
            parsed = _parse_tracker_payload(payload)
        except (AttributeError, TypeError, ValueError):
            # A provider schema change must be a per-account invalid-response
            # result, never an uncaught exception that kills refresh-all.
            last_failure_status = 200
            continue
        # Accept the parse if EITHER current OR peak rank came back. Players who
        # didn't play ranked this season have no current rank but a real peak
        # from prior seasons — still useful. No rank at all → try the next
        # candidate (e.g. UID lookup empty → retry by IGN).
        if "current_rank" not in parsed and "peak_rank" not in parsed:
            saw_empty_profile = True
            continue
        out: dict[str, Any] = {"last_refresh_status": "ok",
                              "last_refresh_source": "tracker"}
        # Adopt tracker's canonical IGN spelling (handles renames / casing) —
        # this is what backfills the IGN on a UID-only account.
        tracker_ign = parsed.pop("tracker_handle", None)
        if tracker_ign and tracker_ign != ign:
            out["in_game_name"] = tracker_ign
        # Guard the auto-backfilled UID before adopting it:
        #   1. Never clobber an already-verified UID — only fill when none.
        #   2. Only accept it when tracker's handle matches the IGN we hold
        #      (case-insensitively). A mismatch means a rename or fuzzy match;
        #      binding the wrong NetEase UID would poison later UID-keyed calls.
        #      (Moot on a UID lookup — tracker returns no platformUserId there —
        #      but kept for the IGN-lookup path.)
        if parsed.get("rivals_uid"):
            handle_matches = bool(tracker_ign) and bool(ign) and \
                tracker_ign.strip().lower() == ign.lower()
            if uid or not handle_matches:
                parsed.pop("rivals_uid", None)
        # Peak monotonicity: never overwrite a higher existing peak with a
        # lower one. tracker sometimes has incomplete season history while a
        # prior refresh captured a true higher peak.
        new_peak = parsed.get("peak_rank")
        if new_peak:
            old_peak = acct.get("peak_rank")
            old_score = acct.get("peak_points") or 0
            new_score = parsed.get("peak_points") or 0
            if old_peak and _RANK_INDEX.get(old_peak, -1) > _RANK_INDEX.get(new_peak, -1):
                parsed.pop("peak_rank", None)
                parsed.pop("peak_points", None)
            elif old_peak == new_peak and old_score > new_score:
                parsed.pop("peak_points", None)
        out.update(parsed)
        return out

    if saw_private:
        return {
            "last_refresh_status": "error",
            "last_refresh_code": "profile_unavailable",
            "last_refresh_source": "tracker",
            "last_refresh_error": (
                "Tracker.gg recognized this player, but its collector cannot "
                "expose the profile. It may have private career data, may not "
                "be indexed yet, or may be affected by a Tracker/game API sync "
                "issue. Any saved rank was kept."
            ),
        }
    if last_failure_status is not None:
        if last_failure_status == 0:
            code = "network_error"
            message = (
                "Could not connect to Tracker.gg. Check the connection and try "
                "again; any saved rank was kept."
            )
        elif last_failure_status == 200:
            code = "invalid_response"
            message = (
                "Tracker.gg returned an unreadable profile response. Try again "
                "later; any saved rank was kept."
            )
        elif last_failure_status in (401, 403):
            code = "provider_blocked"
            message = (
                f"Tracker.gg refused the profile request (HTTP "
                f"{last_failure_status}). Try again later; any saved rank was kept."
            )
        elif last_failure_status >= 500:
            code = "provider_unavailable"
            message = (
                f"Tracker.gg is temporarily unavailable (HTTP "
                f"{last_failure_status}). Try again later; any saved rank was kept."
            )
        else:
            code = "provider_error"
            message = (
                f"Tracker.gg returned an unexpected response (HTTP "
                f"{last_failure_status}). Try again later; any saved rank was kept."
            )
        return {
            "last_refresh_status": "error",
            "last_refresh_code": code,
            "last_refresh_source": "tracker",
            "last_refresh_error": message,
        }
    if saw_empty_profile:
        return {"last_refresh_status": "not_found",
                "last_refresh_code": "no_ranked_data",
                "last_refresh_source": "tracker",
                "last_refresh_error": "Tracker.gg found the profile but returned "
                                      "no ranked data. Any saved rank was kept."}
    if saw_not_found:
        return {"last_refresh_status": "not_found",
                "last_refresh_code": "player_not_found",
                "last_refresh_source": "tracker",
                "last_refresh_error": "Tracker.gg couldn't find that player. "
                                      "Check the saved UID or in-game name."}
    # Defensive fallback: all candidates should have produced one of the
    # outcomes above, but retain a clear code if Tracker changes its schema.
    return {"last_refresh_status": "error",
            "last_refresh_code": "provider_error",
            "last_refresh_source": "tracker",
            "last_refresh_error": "Tracker.gg did not return a usable profile. "
                                  "Try again later; any saved rank was kept."}


def _refresh_account_stats(acct: dict[str, Any]) -> dict[str, Any]:
    """Refresh one account's public rank data without credentials."""
    now = int(time.time())
    base: dict[str, Any] = {
        "last_refresh_ts": now,
        "last_refresh_status": None,
        "last_refresh_code": None,
        "last_refresh_error": None,
        "last_refresh_source": None,
        # Reset every refresh; set True only when tracker serves ranks but flags
        # the profile's match history private (see _parse_tracker_payload).
        "tracker_history_private": False,
    }
    return {**base, **_try_tracker(acct)}


def _account_from_payload(payload: dict[str, Any], key: bytes, existing: dict | None = None) -> dict[str, Any]:
    now = int(time.time())
    if existing:
        acct = dict(existing)
    else:
        acct = {"id": uuid.uuid4().hex, "created_at": now}
    for field in ("username", "email", "in_game_name", "peak_rank",
                  "current_rank", "notes", "border_color", "tag", "tag_color"):
        if field in payload:
            acct[field] = (payload.get(field) or "").strip()
    for field in ("pinned", "neon"):
        if field in payload:
            acct[field] = bool(payload.get(field))
    for field in ("current_points", "peak_points"):
        if field in payload:
            acct[field] = _coerce_points(payload.get(field))
    # Marvel Rivals UID — optional manual override for stats lookup. UIDs are
    # numeric, so we keep only digits (cleans pasted whitespace); blank clears
    # it back to None and the lookup falls back to the in-game name.
    if "rivals_uid" in payload:
        uid_digits = re.sub(r"\D", "", str(payload.get("rivals_uid") or ""))
        acct["rivals_uid"] = uid_digits or None
    if "password" in payload:
        pw = payload.get("password") or ""
        acct["password_enc"] = _encrypt(key, pw) if pw else None
    # Guarantee a new account carries the full schema.
    for field, default in ACCOUNT_FIELD_DEFAULTS.items():
        acct.setdefault(field, default)
    acct["updated_at"] = now
    return acct


# ---------- routes ----------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    vault = _read_vault()
    timeout = _idle_timeout_s()
    with _state_lock:
        unlocked = _state["key"] is not None
        if unlocked and timeout is not None and (
            time.time() - _state["last_activity"] > timeout
        ):
            _state["key"] = None
            unlocked = False
        if unlocked and timeout is not None:
            elapsed = time.time() - _state["last_activity"]
            lock_in_s = max(0, int(timeout - elapsed))
        else:
            lock_in_s = 0
    return jsonify({
        "initialized": vault.get("initialized", False),
        "unlocked": unlocked,
        "account_count": len(vault.get("accounts", [])),
        "lock_in_s": lock_in_s,
        "lockout_minutes": _lockout_minutes(),
        "idle_timeout_s": timeout or 0,
        "version": APP_VERSION,
        "commit": APP_COMMIT,
        "built_at": APP_BUILT_AT,
        "repo": GITHUB_REPO_SLUG,
        "remember_supported": remember_supported(),
        "has_remembered_session": _remember_path().exists() if remember_supported() else False,
        "ui_options": (vault.get("config", {}) or {}).get("ui_options") or {},
    })


@app.route("/api/options", methods=["GET"])
def get_options():
    return jsonify({
        "lockout_minutes": _lockout_minutes(),
        "ui_options": _ui_options(),
    })


@app.route("/api/options", methods=["POST"])
def set_options():
    _key, err = _require_key()
    if err:
        return err
    payload = request.json or {}
    if "lockout_minutes" in payload:
        try:
            m = int(payload["lockout_minutes"])
        except (TypeError, ValueError):
            return jsonify({"error": "bad_value", "message": "lockout_minutes must be a number."}), 400
    if "ui_options" in payload:
        ui = payload.get("ui_options")
        # Small flat blob of scalar prefs — reject anything that could bloat
        # the vault or smuggle structure the frontend never sends.
        valid = isinstance(ui, dict) and len(ui) <= 32 and all(
            isinstance(k, str) and len(k) <= 64
            and (v is None or isinstance(v, (str, int, float, bool)))
            and not (isinstance(v, str) and len(v) > 256)
            for k, v in ui.items()
        )
        if not valid:
            return jsonify({"error": "bad_value",
                            "message": "ui_options must be a small object of scalar values."}), 400
    with _vault_write_lock:  # RMW under the shared lock so a concurrent refresh-all isn't clobbered
        vault = _read_vault()
        cfg = vault.setdefault("config", {})
        if "lockout_minutes" in payload:
            cfg["lockout_minutes"] = max(0, m)
        if "ui_options" in payload:
            cfg["ui_options"] = ui
        # UI-preference-only writes happen on every toggle — skip the backup
        # snapshot for those (see _write_vault); lockout changes keep it.
        _write_vault(vault, backup=any(k != "ui_options" for k in payload))
        return jsonify({
            "ok": True,
            "lockout_minutes": cfg.get("lockout_minutes", DEFAULT_LOCKOUT_MINUTES),
        })


@app.route("/api/init", methods=["POST"])
def init_vault():
    body = request.json or {}
    password = body.get("password", "")
    remember = bool(body.get("remember"))
    if len(password) < 12:
        return jsonify({"error": "password_too_short", "message": "Master password must be at least 12 characters."}), 400
    salt = secrets.token_bytes(16)
    key = _derive_key(password, salt)
    verifier = _encrypt(key, VERIFIER_PLAINTEXT.decode("utf-8"))
    with _vault_write_lock:  # atomic check-then-create so two inits can't race
        if _read_vault().get("initialized"):
            return jsonify({"error": "already_initialized"}), 400
        new_vault = {
            "initialized": True,
            "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "salt": salt.hex()},
            "verifier": verifier,
            "config": {"lockout_minutes": DEFAULT_LOCKOUT_MINUTES},
            "accounts": [],
        }
        _write_vault(new_vault)
    with _state_lock:
        _state["key"] = key
        _state["last_activity"] = time.time()
    if remember and remember_supported():
        _save_remembered_key(key)
    return jsonify({"ok": True, "remembered": remember and remember_supported()})


@app.route("/api/unlock", methods=["POST"])
def unlock():
    vault = _read_vault()
    if not vault.get("initialized"):
        return jsonify({"error": "not_initialized"}), 400
    body = request.json or {}
    password = body.get("password", "")
    remember = bool(body.get("remember"))
    salt = bytes.fromhex(vault["kdf"]["salt"])
    key = _derive_key(password, salt)
    try:
        plain = _decrypt(key, vault["verifier"])
    except Exception:
        return jsonify({"error": "bad_password"}), 401
    if not hmac.compare_digest(plain, VERIFIER_PLAINTEXT.decode("utf-8")):
        return jsonify({"error": "bad_password"}), 401
    with _state_lock:
        _state["key"] = key
        _state["last_activity"] = time.time()
    # Persist via DPAPI if requested; otherwise wipe any prior remember-blob
    # so a "do not remember" tick effectively forgets the last "remember" tick.
    if remember and remember_supported():
        _save_remembered_key(key)
    else:
        _clear_remembered_key()
    return jsonify({"ok": True, "remembered": remember and remember_supported()})


@app.route("/api/lock", methods=["POST"])
def lock():
    """Explicit lock — wipes both the in-memory key and any persisted
    remember-me blob, so the next boot lands on the unlock screen."""
    with _state_lock:
        _state["key"] = None
    _clear_remembered_key()
    return jsonify({"ok": True})


@app.route("/api/accounts", methods=["GET"])
def list_accounts():
    key, err = _require_key()
    if err:
        return err
    vault = _read_vault()
    out = []
    for acct in vault.get("accounts", []):
        item = {k: v for k, v in acct.items() if k != "password_enc"}
        # Forward-fill new fields for any record the migration somehow missed.
        for field, default in ACCOUNT_FIELD_DEFAULTS.items():
            item.setdefault(field, default)
        try:
            item["password"] = _decrypt(key, acct["password_enc"]) if acct.get("password_enc") else ""
        except Exception:
            item["password"] = ""
            item["password_error"] = True
        out.append(item)
    return jsonify({"accounts": out})


@app.route("/api/accounts", methods=["POST"])
def create_account():
    key, err = _require_key()
    if err:
        return err
    payload = request.json or {}
    acct = _account_from_payload(payload, key)
    with _vault_write_lock:  # RMW under the shared lock so a concurrent refresh-all isn't clobbered
        vault = _read_vault()
        vault.setdefault("accounts", []).append(acct)
        _write_vault(vault)
    return jsonify({"ok": True, "id": acct["id"]})


@app.route("/api/accounts/<acct_id>", methods=["PUT"])
def update_account(acct_id: str):
    key, err = _require_key()
    if err:
        return err
    payload = request.json or {}
    with _vault_write_lock:  # RMW under the shared lock so a concurrent refresh-all isn't clobbered
        vault = _read_vault()
        accounts = vault.get("accounts", [])
        for i, acct in enumerate(accounts):
            if acct["id"] == acct_id:
                accounts[i] = _account_from_payload(payload, key, existing=acct)
                _write_vault(vault)
                return jsonify({"ok": True})
    return jsonify({"error": "not_found"}), 404


@app.route("/api/accounts/import-detected", methods=["POST"])
def import_detected_accounts():
    """Scaffold a vault entry for every Marvel Rivals UID detected on this PC
    that isn't already linked to an account. Skeleton entries carry only the
    UID — IGN, rank, and credentials are blank until the user fills them in
    or hits refresh (which will fetch the IGN from tracker.gg)."""
    key, err = _require_key()
    if err:
        return err
    detected = _detected_rivals_uids()
    with _vault_write_lock:  # RMW under the shared lock so a concurrent refresh-all isn't clobbered
        vault = _read_vault()
        accounts = vault.setdefault("accounts", [])
        claimed = {(a.get("rivals_uid") or "").strip() for a in accounts}
        created = 0
        for uid in detected:
            if uid in claimed:
                continue
            new = _account_from_payload({
                "rivals_uid": uid,
                "in_game_name": "",
                "username": "",
                "email": "",
                "password": "",
                "current_rank": "",
                "peak_rank": "",
            }, key)
            accounts.append(new)
            created += 1
        if created:
            _write_vault(vault)
    return jsonify({"ok": True, "created": created})


@app.route("/api/accounts/reorder", methods=["POST"])
def reorder_accounts():
    _key, err = _require_key()
    if err:
        return err
    payload = request.json or {}
    new_order = payload.get("order") or []
    with _vault_write_lock:  # RMW under the shared lock so a concurrent refresh-all isn't clobbered
        vault = _read_vault()
        by_id = {a["id"]: a for a in vault.get("accounts", [])}
        if set(new_order) != set(by_id.keys()):
            return jsonify({"error": "order_mismatch"}), 400
        vault["accounts"] = [by_id[i] for i in new_order]
        _write_vault(vault)
    return jsonify({"ok": True})


def _apply_refresh_updates(vault: dict[str, Any], acct_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Merge refresh-result fields onto an account by id; return the merged record."""
    for acct in vault.get("accounts", []):
        if acct["id"] == acct_id:
            acct.update(updates)
            acct["updated_at"] = int(time.time())
            return acct
    return None


def _commit_updates(acct_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Atomically merge `updates` onto one account: under the vault-write lock,
    re-read the vault fresh, apply only this account's updates, and write.
    Returns the merged record (or None if the account is gone).

    Re-reading fresh (rather than writing a snapshot captured seconds earlier) is
    what makes a long refresh-all safe against a concurrent single-account
    refresh — each writer only ever overwrites its own account's fields."""
    with _vault_write_lock:
        vault = _read_vault()
        merged = _apply_refresh_updates(vault, acct_id, updates)
        _write_vault(vault, backup=False)  # metadata-only; no credential change
        return merged


def _public_account(acct: dict[str, Any], key: bytes) -> dict[str, Any]:
    """Strip password_enc and inject decrypted password (matches /api/accounts shape)."""
    item = {k: v for k, v in acct.items() if k != "password_enc"}
    for field, default in ACCOUNT_FIELD_DEFAULTS.items():
        item.setdefault(field, default)
    try:
        item["password"] = _decrypt(key, acct["password_enc"]) if acct.get("password_enc") else ""
    except Exception:
        item["password"] = ""
        item["password_error"] = True
    return item


@app.route("/api/accounts/<acct_id>/refresh-stats", methods=["POST"])
def refresh_account_stats(acct_id: str):
    key, err = _require_key()
    if err:
        return err
    vault = _read_vault()
    target = next((a for a in vault.get("accounts", []) if a["id"] == acct_id), None)
    if target is None:
        return jsonify({"error": "not_found"}), 404
    # Per-account spam guard. Block repeated upstream requests inside the
    # cooldown even if the button is double-clicked or multiple tabs are open.
    last_ts = int(target.get("last_refresh_ts") or 0)
    since = int(time.time()) - last_ts
    if last_ts and since < PER_ACCOUNT_REFRESH_COOLDOWN_S:
        wait = PER_ACCOUNT_REFRESH_COOLDOWN_S - since
        return jsonify({"error": "cooldown", "retry_after_s": wait,
                        "message": f"Just refreshed — wait {wait}s before another pull."}), 429
    updates = _refresh_account_stats(target)
    retry_after = updates.pop("_retry_after_s", None)
    if retry_after:
        response = jsonify({
            "error": "rate_limited",
            "retry_after_s": retry_after,
            "message": updates.get("last_refresh_error"),
        })
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response
    merged = _commit_updates(acct_id, updates)  # atomic RMW under the vault lock
    if merged is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"ok": True, "account": _public_account(merged, key)})


def _refresh_all_steps(vault: dict[str, Any], key: bytes):
    """Refresh every account in series, yielding a progress dict per account and
    a terminal summary dict. Shared by the plain-JSON and NDJSON-streaming
    refresh-all endpoints so the loop logic lives in one place.

    Progress events: {"type":"progress","done":k,"total":n,"status":st,"account":pub|None}
    Terminal event:  {"type":"summary","summary":{...},"accounts":[...]}

    `vault` is only a read snapshot of which accounts to refresh; each result is
    persisted via _commit_updates (fresh read-modify-write under the vault lock),
    so a concurrent single-account refresh can't be clobbered and an already-
    committed account survives a client disconnect mid-stream.
    """
    ids = [a["id"] for a in vault.get("accounts", [])]
    targets = {a["id"]: a for a in vault.get("accounts", [])}
    total = len(ids)
    summary = {"ok": 0, "not_found": 0, "error": 0,
               "missing_handle": 0, "rate_limited": 0, "skipped": 0,
               "by_code": {}}
    updated_accounts: list[dict[str, Any]] = []
    now_ts = int(time.time())
    done = 0

    for i, acct_id in enumerate(ids):
        target = targets.get(acct_id)
        if target is None:
            done += 1
            summary["skipped"] += 1
            yield {"type": "progress", "done": done, "total": total,
                   "status": "skipped", "account": None}
            continue
        # Skip accounts that were refreshed within the per-account cooldown.
        # Avoids hammering tracker.gg when the user just did refresh-all.
        last_ts = int(target.get("last_refresh_ts") or 0)
        if last_ts and now_ts - last_ts < PER_ACCOUNT_REFRESH_COOLDOWN_S:
            # Existing rank stays as-is; just advance the progress counter.
            done += 1
            summary["skipped"] += 1
            yield {"type": "progress", "done": done, "total": total,
                   "status": "cooldown", "account": None}
            continue
        updates = _refresh_account_stats(target)
        retry_after = updates.pop("_retry_after_s", None)
        if retry_after:
            # Stop the sweep immediately. Retrying another identifier or
            # account would only extend the upstream rate limit.
            done += 1
            summary["rate_limited"] += 1
            summary["by_code"]["rate_limited"] = \
                summary["by_code"].get("rate_limited", 0) + 1
            summary["retry_after_s"] = retry_after
            yield {"type": "progress", "done": done, "total": total,
                   "status": "rate_limited", "account": None,
                   "retry_after_s": retry_after}
            remaining = total - done
            if remaining:
                summary["skipped"] += remaining
                done = total
                yield {"type": "progress", "done": done, "total": total,
                       "status": "skipped", "account": None}
            break
        merged = _commit_updates(acct_id, updates)  # atomic RMW under the vault lock
        _touch_activity()  # keep the session alive across a long sweep
        st = updates.get("last_refresh_status") or "error"
        summary[st] = summary.get(st, 0) + 1
        reason = updates.get("last_refresh_code")
        if reason and st != "ok":
            summary["by_code"][reason] = summary["by_code"].get(reason, 0) + 1
        pub = _public_account(merged, key) if merged is not None else None
        if pub is not None:
            updated_accounts.append(pub)
        done += 1
        yield {"type": "progress", "done": done, "total": total,
               "status": st, "code": reason, "account": pub}
        if i < len(ids) - 1:
            time.sleep(REFRESH_ALL_DELAY_S)
    _touch_activity()
    yield {"type": "summary", "summary": summary,
           "accounts": updated_accounts}


def _refresh_all_busy():
    # Built per-call: jsonify needs an app context, so it can't be a module const.
    return jsonify({"error": "busy",
                    "message": "A refresh-all is already running."}), 409


@app.route("/api/accounts/refresh-all", methods=["POST"])
def refresh_all_accounts():
    key, err = _require_key()
    if err:
        return err
    if not _refresh_all_lock.acquire(blocking=False):
        return _refresh_all_busy()
    try:
        vault = _read_vault()
        summary: dict[str, Any] = {}
        accounts: list[dict[str, Any]] = []
        for ev in _refresh_all_steps(vault, key):
            if ev.get("type") == "summary":
                summary = ev["summary"]
                accounts = ev["accounts"]
        return jsonify({"ok": True, "summary": summary, "accounts": accounts})
    finally:
        _refresh_all_lock.release()


@app.route("/api/accounts/refresh-all/stream", methods=["POST"])
def refresh_all_accounts_stream():
    """Streaming refresh-all: emits one NDJSON line per account so the client
    can show a real progress percentage and update cards live as each lands."""
    key, err = _require_key()
    if err:
        return err
    # Single-flight: a second concurrent sweep would double-hammer tracker.gg.
    # Hold the lock for the life of the stream and release when the generator
    # is exhausted (or the client disconnects and Flask closes it).
    if not _refresh_all_lock.acquire(blocking=False):
        return _refresh_all_busy()

    def gen():
        # Read inside the generator (not before building the Response) so a
        # corrupt-vault OSError can't escape between acquire() and the
        # try/finally and strand the lock held → permanent 409 until restart.
        try:
            vault = _read_vault()
            for ev in _refresh_all_steps(vault, key):
                yield json.dumps(ev) + "\n"
        finally:
            _refresh_all_lock.release()

    return Response(
        stream_with_context(gen()),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/accounts/<acct_id>", methods=["DELETE"])
def delete_account(acct_id: str):
    _key, err = _require_key()
    if err:
        return err
    with _vault_write_lock:  # RMW under the shared lock so a concurrent refresh-all isn't clobbered
        vault = _read_vault()
        before = len(vault.get("accounts", []))
        vault["accounts"] = [a for a in vault.get("accounts", []) if a["id"] != acct_id]
        if len(vault["accounts"]) == before:
            return jsonify({"error": "not_found"}), 404
        _write_vault(vault)
    return jsonify({"ok": True})


# Run migrations as soon as the module is imported, so they happen regardless
# of how the app is launched. Both are idempotent and snapshot the vault into
# backups/ before any write.
_migrate_from_app_folder()
_migrate_vault_if_needed()
_cleanup_post_update()
# Attempt to install a remembered key from a previous "remember me" unlock.
# Silent — if DPAPI is unavailable or the blob is stale, the app just starts
# on the unlock screen like it always did.
try:
    _try_remembered_unlock(_read_vault())
except Exception:
    pass


# ---------- liveness heartbeat ----------
# The frontend POSTs /api/heartbeat every ~30s while a browser tab is open. In
# the default (browser) mode the launcher watches this timestamp and exits
# once every tab has closed — so a double-clicked .exe behaves like an app and
# leaves no orphaned process behind.

_heartbeat_lock = threading.Lock()
_last_heartbeat = 0.0
_heartbeat_seen = False
# Quit this long after the browser's last heartbeat. Heartbeats fire every
# 10 s while the tab is in the foreground, but background tabs get throttled
# (Chrome/Brave coalesce to 1/min). 20 s was way too aggressive — the process
# would die out from under a user who tabbed away to look up a username, and
# the next refresh would dangle on a dead backend looking like a wrong
# password. 5 minutes survives normal alt-tabbing and tab-throttling without
# leaving an orphaned process running forever after the browser truly closes.
IDLE_SHUTDOWN_S = 300
STARTUP_GRACE_S = 180      # ...or this long if a browser never connects at all


@app.route("/api/steam-active")
def steam_active():
    """Which Steam account is currently logged in on this PC? Used by the
    frontend to badge the matching vault card with an 'ACTIVE NOW' indicator.

    Unauthenticated on purpose: this exposes only what Steam itself broadcasts
    locally (which the user can also see by opening Steam) and is consulted
    from the lock screen too, where no vault key is yet held.
    """
    info = _active_steam_account()
    return jsonify({"active": info})


@app.route("/api/check-update")
def check_update():
    """Compare the running version against the latest GitHub release.
    Always returns 200 — a transient network failure shows as
    `{has_update: false}` so the UI stays quiet."""
    release = _fetch_latest_release()
    if not release:
        return jsonify({
            "current": APP_VERSION,
            "latest": None,
            "has_update": False,
            "checked_at": int(time.time()),
            "packaged": _is_packaged(),
        })
    latest = (release.get("tag_name") or "").strip()
    asset = _release_asset_url(release)
    has_update = (
        _version_tuple(latest) > _version_tuple(APP_VERSION)
        and bool(asset)
        and _is_packaged()      # source runs never auto-update
    )
    notes = (release.get("body") or "").strip()
    return jsonify({
        "current": APP_VERSION,
        "latest": latest.lstrip("vV"),
        "tag_name": latest,
        "has_update": has_update,
        "download_url": asset,
        "release_url": release.get("html_url"),
        "release_notes": notes[:2000],
        "checked_at": int(time.time()),
        "packaged": _is_packaged(),
    })


@app.route("/api/apply-update", methods=["POST"])
def apply_update():
    """Download the newest .exe and swap it into place over the running one.
    The user must relaunch the app to actually run the new binary.

    Implementation: rename the running exe to <name>.old.exe (Windows allows
    this even while the process is running — rename is a metadata op), then
    write the download to the original path. On next launch, the new exe
    deletes the .old leftover via `_cleanup_post_update`."""
    # Require an unlocked vault: swapping the running executable is a sensitive
    # action, so don't let an unauthenticated local caller trigger it. (The
    # cross-origin guard already blocks remote pages; this adds local authz.)
    _, err = _require_key()
    if err:
        return err
    if not _is_packaged():
        return jsonify({"error": "not_packaged",
                        "message": "Self-update only works from the packaged .exe. "
                                   "From source, `git pull` instead."}), 400

    release = _fetch_latest_release()
    if not release:
        return jsonify({"error": "fetch_failed",
                        "message": "Could not reach the GitHub releases API."}), 502

    latest_tag = (release.get("tag_name") or "").strip()
    asset_url = _release_asset_url(release)
    if not asset_url:
        return jsonify({"error": "no_asset",
                        "message": f"Release {latest_tag} has no {UPDATE_ASSET_NAME} asset."}), 502
    if not _is_github_download_url(asset_url):
        return jsonify({"error": "bad_asset_host",
                        "message": "Update download URL isn't a GitHub host — aborted."}), 502
    if _version_tuple(latest_tag) <= _version_tuple(APP_VERSION):
        return jsonify({"error": "not_newer",
                        "message": f"Already on the latest version (v{APP_VERSION})."}), 400

    exe = Path(sys.executable)
    parent = exe.parent
    new_path = exe.with_name(exe.stem + ".new" + exe.suffix)
    old_path = exe.with_name(exe.stem + ".old" + exe.suffix)

    # 1. Sanity-check writability before we start, so we don't half-finish.
    if not os.access(parent, os.W_OK):
        return jsonify({"error": "dir_not_writable",
                        "message": "Move the .exe to a folder you can write to "
                                   "(e.g. Desktop or Documents) and try again."}), 403

    # 2. Wipe stale .new from a prior attempt so the download lands cleanly.
    if new_path.exists():
        try:
            new_path.unlink()
        except OSError as e:
            return jsonify({"error": "stale_cleanup_failed",
                            "message": str(e)}), 500

    # 3. Download.
    headers = {"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
    req = urllib.request.Request(asset_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as r, open(new_path, "wb") as out:
            shutil.copyfileobj(r, out)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        new_path.unlink(missing_ok=True)
        return jsonify({"error": "download_failed",
                        "message": f"Could not download the update: {e}"}), 502

    if new_path.stat().st_size < UPDATE_MIN_DOWNLOAD_BYTES:
        new_path.unlink(missing_ok=True)
        return jsonify({"error": "bad_download",
                        "message": "Download was suspiciously small — aborted."}), 502

    # 3b. Verify the SHA256 against the sibling .sha256 published by the
    # release workflow. This is mandatory: if the checksum asset is missing,
    # on a non-GitHub host, or can't be fetched, we refuse to install rather
    # than swap in an unverified binary (closes a strip/downgrade attack where
    # a release without a checksum would otherwise be installed unchecked).
    # Every release since v1.7.0 publishes it; the current line is well past
    # that, so a missing checksum now signals tampering, not an old release.
    checksum_url = _release_checksum_url(release)
    if not _is_github_download_url(checksum_url):
        new_path.unlink(missing_ok=True)
        return jsonify({"error": "no_checksum",
                        "message": "Release is missing its SHA256 checksum — "
                                   "refusing to install an unverified update."}), 502
    expected = _fetch_expected_checksum(checksum_url)
    if expected is None:
        new_path.unlink(missing_ok=True)
        return jsonify({"error": "checksum_unavailable",
                        "message": "Couldn't fetch the update's SHA256 checksum — "
                                   "refusing to install an unverified update."}), 502
    actual = _sha256_of_file(new_path)
    if actual.lower() != expected:
        new_path.unlink(missing_ok=True)
        return jsonify({
            "error": "checksum_mismatch",
            "message": f"Downloaded file failed SHA256 verification. "
                       f"Expected {expected[:12]}…, got {actual[:12]}…. "
                       f"Update aborted — your current install is untouched.",
            "expected": expected,
            "actual": actual,
        }), 502
    verified = True

    # 4. Wipe any older .old from a prior update so the rename has room.
    if old_path.exists():
        try:
            old_path.unlink()
        except OSError:
            pass

    # 5. Swap: move the running exe out of the way, move the new one in.
    try:
        exe.rename(old_path)
    except OSError as e:
        new_path.unlink(missing_ok=True)
        return jsonify({"error": "rename_failed",
                        "message": f"Could not rename running .exe: {e}"}), 500
    try:
        new_path.rename(exe)
    except OSError as e:
        # Try to restore the original so we don't leave the user with no .exe.
        try:
            old_path.rename(exe)
        except OSError:
            pass
        return jsonify({"error": "install_failed",
                        "message": f"Could not install the update: {e}"}), 500

    # Auto-restart. This was tried in 2.8.2/2.8.3 and REVERTED in 2.8.4 because
    # the spawned onefile child inherited the parent's `_MEIPASS2`, reused the
    # parent's extraction dir, and crashed with TemplateNotFound. The fix is in
    # _spawn_successor: it strips the `_MEI*`/`_PYI*` bootloader env so the
    # child unpacks its own dir, and passes our PID so the child waits for us to
    # release the single-instance lock before taking over. If the spawn fails
    # for any reason we fall back to the proven "reopen it yourself" behavior.
    ver = latest_tag.lstrip("vV")
    relaunching = _spawn_successor()
    if relaunching:
        # End this process once the HTTP response has flushed, so the successor
        # (already waiting on our PID) can grab the lock and open its window.
        threading.Timer(0.8, lambda: os._exit(0)).start()
        msg = f"v{ver} installed — restarting…"
    else:
        msg = (f"v{ver} installed. Close the app and reopen it to run the new "
               f"version.")
    return jsonify({
        "ok": True,
        "installed": ver,
        "verified": verified,
        "restarting": relaunching,
        "message": msg,
    })


@app.route("/api/rivals/local-uids")
def rivals_local_uids():
    """Every Marvel Rivals UID with a local config folder on this PC.

    The game creates one folder per account that signs in, regardless of
    launcher (Steam, NetEase native). Vault cards whose rivals_uid is in
    this list get an 'ON THIS PC' badge; UIDs not in the vault are
    candidates to quick-add later.

    Unauthenticated for the same reason as /api/steam-active.
    """
    return jsonify({"uids": _detected_rivals_uids()})


@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    global _last_heartbeat, _heartbeat_seen
    with _heartbeat_lock:
        _last_heartbeat = time.time()
        _heartbeat_seen = True
    return jsonify({"ok": True})


@app.route("/api/shutdown", methods=["POST"])
def shutdown_now():
    """Browser told us it's closing — exit immediately instead of waiting
    out the idle-shutdown timer. The frontend's beforeunload hook fires
    this via navigator.sendBeacon so it survives tab closure."""
    threading.Timer(0.1, lambda: _shutdown("Browser closed")).start()
    return jsonify({"ok": True})


# ---------- launcher ----------

def _free_port() -> int:
    """Ask the OS for an unused loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Fixed preferred port so the browser origin — which scopes localStorage
# (UI options cache, drawer drafts) — stays stable across launches. Arbitrary
# high number to dodge common services; anything squatting on it just pushes
# us back to an OS-assigned port for that run.
DEFAULT_PORT = 27455


def _default_port() -> int:
    """DEFAULT_PORT when it's free, else an OS-assigned fallback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", DEFAULT_PORT))
            return DEFAULT_PORT
        except OSError:
            return _free_port()


def _shutdown(reason: str) -> None:
    """Print a parting line and end the process."""
    print(f"\n  {reason} — Marvel Rivals Account Tracker stopped. Your vault is saved.")
    os._exit(0)


def _idle_watch() -> None:
    """Quit once the browser is gone — the no-CLI "close it and you're done"
    lifecycle for a double-clicked build.

    Runs only in the default browser mode. Exits when no heartbeat has arrived
    for IDLE_SHUTDOWN_S after one was seen (every tab closed), or when none
    arrives within STARTUP_GRACE_S (the browser never opened — so the process
    can't get stuck running invisibly).
    """
    start = time.time()
    while True:
        time.sleep(15)
        with _heartbeat_lock:
            seen, last = _heartbeat_seen, _last_heartbeat
        now = time.time()
        if seen and now - last > IDLE_SHUTDOWN_S:
            _shutdown("Browser closed")
        elif not seen and now - start > STARTUP_GRACE_S:
            _shutdown("Browser never opened")


def _wait_until_serving(port: int, timeout: float = 10.0) -> bool:
    """Block until the Flask server on `port` accepts a connection, so the
    native window doesn't load before the server is listening."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            try:
                s.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.05)
    return False


def _serve_background(port: int) -> None:
    """Run Flask on a daemon thread so the main thread is free for the native
    window's UI loop. threaded=True lets the page's asset + API requests be
    served concurrently while the window loads."""
    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False,
                               use_reloader=False, threaded=True),
        daemon=True,
    ).start()


def _native_backend_available() -> bool:
    """True if the OS has a webview backend pywebview can actually host in.

    On Windows that means the Evergreen WebView2 Runtime. We locate the
    runtime *folder* (not just a registry flag) and pin pywebview to it via
    WEBVIEW2_BROWSER_EXECUTABLE_FOLDER. This is deliberate: the WebView2Loader
    bundled with pywebview can fail its own auto-detection even when the
    runtime is installed and current — it throws "Package dependency criteria
    could not be resolved" and opens a blank window. Pointing it straight at
    the runtime binary sidesteps that loader entirely. If no runtime folder is
    found we return False so the caller falls back to the browser instead of
    showing a blank frame. Non-Windows platforms return True and let
    _run_native_window's try/except handle a missing backend at start()."""
    if sys.platform != "win32":
        return True
    # Respect an explicit override if the user already set one.
    env = os.environ.get("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER")
    if env and Path(env, "msedgewebview2.exe").exists():
        return True
    bases = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             "Microsoft", "EdgeWebView", "Application"),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"),
             "Microsoft", "EdgeWebView", "Application"),
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")),
             "Microsoft", "EdgeWebView", "Application"),
    ]
    candidates = []
    for base in bases:
        try:
            for child in base.iterdir():
                if (child / "msedgewebview2.exe").exists():
                    candidates.append(child)
        except OSError:
            continue
    if not candidates:
        return False
    # Highest version folder wins (Evergreen keeps the newest installed).
    best = max(candidates, key=lambda p: _version_tuple(p.name))
    os.environ["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"] = str(best)
    return True


def _run_native_window(url: str, port: int) -> bool:
    """Open the app in a native OS webview window instead of the browser.

    Returns True if the native window ran (and the process should exit when
    it closes), or False if pywebview isn't available so the caller can fall
    back to the browser. The native window's close *is* the app lifecycle —
    no heartbeat/idle-watch needed; closing it ends the process.
    """
    try:
        import webview  # optional dependency; absent in headless/source setups
    except ImportError:
        return False  # Flask not started yet — caller runs the browser path.

    if not _native_backend_available():
        # No WebView2 runtime (or equivalent) — opening a window would just
        # show a blank frame. Bail before starting Flask so the caller's
        # browser path runs cleanly.
        print("  No native webview runtime found — opening in your browser.")
        return False

    # From here on we own the process lifecycle. Flask runs on a daemon thread
    # so we never start app.run() twice (which would fail with the port held).
    _serve_background(port)
    _wait_until_serving(port)

    try:
        webview.create_window(
            WINDOW_TITLE, url,
            width=1240, height=860, min_size=(900, 640),
        )
        webview.start()  # blocks until the window is closed
    except Exception as e:  # no webview backend (e.g. Linux w/o WebKitGTK)
        print(f"  Native window unavailable ({e}) — opening in your browser.")
        # The server is already up on the daemon thread; just point a browser
        # at it and fall back to the heartbeat/idle lifecycle.
        webbrowser.open(url)
        _idle_watch()  # blocks forever; _shutdown() ends the process
        return True
    _shutdown("Window closed")
    return True


_INSTANCE_LOCK = None  # held file object; kept alive for the whole process


def _acquire_single_instance(lock_path: Path):
    """Take an exclusive OS lock so only one packaged instance runs per data
    dir. Returns the held file object on success, or None if another instance
    already holds it. The OS releases the lock when the process exits — even on
    a crash — so there is no stale-lock file to clean up."""
    f = open(lock_path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _focus_existing_window() -> None:
    """Best-effort: bring the already-running instance's native window to the
    foreground (Windows only). No-op elsewhere or if the window isn't found —
    the second launch simply exits either way."""
    if os.name != "nt":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE — un-minimize if needed
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def main() -> None:
    # Zero arguments is the intended path — a double-clicked .exe just works.
    # The flags below exist only for the rare headless / power-user case.
    parser = argparse.ArgumentParser(
        prog="MarvelRivalsAccountTracker",
        description="Local encrypted Marvel Rivals account vault. "
                    "Just run it with no arguments — the flags are optional.")
    parser.add_argument("--no-browser", action="store_true",
                        help="run headless: don't open a browser, and keep "
                             "running until stopped manually")
    parser.add_argument("--port", type=int, default=0, metavar="N",
                        help="serve on a fixed port instead of a random one")
    parser.add_argument("--keep-alive", action="store_true",
                        help="open the browser but don't quit when it closes")
    parser.add_argument("--await-pid", type=int, default=0, metavar="PID",
                        help=argparse.SUPPRESS)  # internal: set on self-restart
    parser.add_argument("--selftest-spawn", metavar="FILE", default=None,
                        help=argparse.SUPPRESS)  # internal: update-hop test
    parser.add_argument("--selftest-verify", metavar="FILE", default=None,
                        help=argparse.SUPPRESS)  # internal: update-hop test
    parser.add_argument("--selftest-restart", metavar="FILE", default=None,
                        help=argparse.SUPPRESS)  # internal: update-hop test
    args = parser.parse_args()

    # Hidden update-restart regression harness (see _run_update_selftest).
    if args.selftest_spawn:
        sys.exit(_run_update_selftest("spawn", args.selftest_spawn))
    if args.selftest_verify:
        sys.exit(_run_update_selftest("verify", args.selftest_verify))
    if args.selftest_restart:
        sys.exit(_run_update_selftest("restart", args.selftest_restart))

    # Self-restart after an in-app update: wait for the predecessor to exit so
    # we don't fight it for the single-instance lock, then proceed normally.
    if args.await_pid:
        _await_process_exit(args.await_pid)

    # Single-instance guard (packaged builds only — running from source stays
    # unrestricted for dev/tests). The lock is scoped to the data dir, so a
    # separate MARVEL_KEEPER_DATA vault is allowed its own instance.
    global _INSTANCE_LOCK
    if _is_packaged():
        _INSTANCE_LOCK = _acquire_single_instance(INSTANCE_LOCK_PATH)
        if _INSTANCE_LOCK is None and args.await_pid:
            # Successor of an update restart: the predecessor may still be
            # releasing the lock even though its PID is gone. Retry briefly.
            for _ in range(40):  # ~6s
                time.sleep(0.15)
                _INSTANCE_LOCK = _acquire_single_instance(INSTANCE_LOCK_PATH)
                if _INSTANCE_LOCK is not None:
                    break
        if _INSTANCE_LOCK is None:
            # Already running — surface the existing window instead of starting
            # a second copy (which previously fell through to the browser path).
            print("  Marvel Rivals Account Tracker is already running.")
            _focus_existing_window()
            sys.exit(0)

    # Quiet the per-request Werkzeug log lines — the banner is what matters.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    port = args.port or _default_port()
    url = f"http://127.0.0.1:{port}"
    open_browser = not args.no_browser
    # Default: behave like an app — quit shortly after the browser closes so
    # nothing is left running. Headless or --keep-alive opt out of that.
    auto_stop = open_browser and not args.keep_alive

    line = "=" * 60
    print(line)
    print("  Marvel Rivals Account Tracker")
    print("  " + "-" * 56)
    print(f"  Address    : {url}")
    print(f"  Vault file : {VAULT_PATH}")
    print()
    if not open_browser:
        print("  Open the address above in your browser.")
        print("  Keep this window open while you use the app; Ctrl+C quits.")
    elif auto_stop:
        # Default mode — a native window or the browser opens automatically,
        # and closing it quits the app. The native path prints its own line.
        print("  Opening Marvel Rivals Account Tracker… close it when you're done.")
    else:
        print("  Your browser will open; this window stays up (Ctrl+C quits).")
    print(line)

    # Test hook: a packaged successor launched by the update-restart self-test
    # signals it fully initialized (lock held, resources resolved) by touching
    # this file. Inert unless the env var is set. See tests/update_hop_test.py.
    _restart_sentinel = os.environ.get("MRAT_RESTART_SENTINEL")
    if _restart_sentinel:
        try:
            with open(_restart_sentinel, "a", encoding="utf-8") as f:
                f.write(f"UP={os.getpid()} MEI={RESOURCE_DIR}\n")
        except OSError:
            pass

    # Default mode: try a native OS window first. It owns the process
    # lifecycle (close the window = quit) and, if it runs, never returns here.
    # Falls through to the browser path only when pywebview isn't installed.
    if auto_stop and _run_native_window(url, port):
        return

    # Browser path: open the default browser shortly after the server starts
    # listening, and (in app mode) quit once the browser is gone.
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    if auto_stop:
        threading.Thread(target=_idle_watch, daemon=True).start()

    try:
        app.run(host="127.0.0.1", port=port, debug=False)
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"\n  Could not start on port {port}: {e}")
        print("  Try a different --port, or omit it to pick one automatically.")
        return
    print("\n  Marvel Rivals Account Tracker stopped. Your vault is saved.")


if __name__ == "__main__":
    main()
