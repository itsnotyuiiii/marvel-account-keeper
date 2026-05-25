"""Marvel Rivals / Steam account tracker.

Single-user local-only app. Stores account records in vault.json, with the
password field encrypted under a key derived from a master password (scrypt
+ AES-256-GCM). The derived key lives only in process memory after unlock.

Run directly (`python app.py`) or as the packaged executable — no arguments
needed. It picks a free loopback port, opens the default browser, and quits
itself ~2 min after the browser is closed, so a double-clicked build leaves
nothing running. Optional flags: `--no-browser` (headless, stays up),
`--port N` (fixed port), `--keep-alive` (open the browser but don't auto-quit).
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import socket
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
from flask import Flask, jsonify, render_template, request

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
APP_VERSION = "1.8.1"
GITHUB_REPO_SLUG = "itsnotyuiiii/marvel-account-keeper"

# Update-check / self-apply settings. The packaged .exe checks the GitHub
# release feed on boot and offers a one-click update when a newer tag is
# available. Running from source (not frozen) hides the banner entirely.
GITHUB_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest"
UPDATE_ASSET_NAME = "MarvelAccountKeeper-windows.exe"
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
    "neon": False,
    "current_points": None,
    "peak_points": None,
    # Stats refresh (marvelrivalsapi.com integration)
    "rivals_uid": None,
    "last_refresh_ts": None,       # epoch — when *we* last hit the API for this account
    "last_refresh_status": None,   # "ok" | "private" | "not_found" | "bad_key" | "error" | "missing_handle"
    "last_refresh_error": None,
    "rivals_synced_at": None,      # epoch — when marvelrivalsapi last crawled this player
    "rivals_update_requested_at": None,  # epoch — when we last asked the API to recrawl
    # Match history (lazy — populated only when the drawer is opened for an account)
    "recent_matches": [],
    "matches_synced_at": None,
    "matches_error": None,
}

# marvelrivalsapi.com integration
RIVALS_API_BASE = "https://marvelrivalsapi.com/api/v1"
RIVALS_API_TIMEOUT_S = 12
RIVALS_UPDATE_TIMEOUT_S = 8  # fire-and-forget recrawl request
REFRESH_ALL_DELAY_S = 2.0  # polite spacing between calls in /refresh-all (30 req/min)
# The /update endpoint locks a player for 30 min and penalizes repeat requests
# (queue position resets, risk of a stuck processing loop) — never re-request
# a recrawl for the same player inside this window.
RIVALS_UPDATE_COOLDOWN_S = 30 * 60


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


def _is_packaged() -> bool:
    """True when running as the PyInstaller-built .exe — the only path where
    self-update is meaningful. Running from source returns False and the
    update endpoints become no-ops."""
    return bool(getattr(sys, "frozen", False))


def _cleanup_post_update() -> None:
    """Delete the .old.exe left behind by a previous successful update.
    Called at app startup so the leftover doesn't accumulate."""
    if not _is_packaged():
        return
    exe = Path(sys.executable)
    old = exe.with_name(exe.stem + ".old" + exe.suffix)
    if old.exists():
        try:
            old.unlink()
        except OSError:
            pass  # likely a permission/lock issue — try again next launch


app = Flask(
    __name__,
    static_folder=str(RESOURCE_DIR / "static"),
    static_url_path="/static",
    template_folder=str(RESOURCE_DIR / "templates"),
)

_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "key": None,            # bytes | None
    "last_activity": 0.0,   # epoch seconds
}

# marvelrivalsapi usage accounting. The limit is dynamic (per the docs) and
# surfaced via X-RateLimit-Limit / -Remaining / -Reset headers — but the API
# serves the literal string "cache" for them on cached hits, so we keep the
# last *numeric* values seen and also count calls locally as a fallback.
# 429 responses carry Retry-After.
_rivals_lock = threading.Lock()
_rivals_usage: dict[str, Any] = {"date": "", "count": 0}
_rivals_rate_limited_until = 0.0  # epoch; > now means we're in a 429 cooldown
_rivals_quota: dict[str, Any] = {"limit": None, "remaining": None,
                                 "reset": None, "at": None}


# ---------- vault file helpers ----------

def _read_vault() -> dict[str, Any]:
    if not VAULT_PATH.exists():
        return {"initialized": False, "accounts": []}
    return json.loads(VAULT_PATH.read_text(encoding="utf-8"))


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


def _write_vault(vault: dict[str, Any]) -> None:
    _backup_current_vault()
    VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = VAULT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(vault, indent=2), encoding="utf-8")
    os.replace(tmp, VAULT_PATH)


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
    the current master password keeps working.
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
        changed = True
    elif "lockout_minutes" not in cfg:
        cfg["lockout_minutes"] = DEFAULT_LOCKOUT_MINUTES
        changed = True
    for acct in vault.get("accounts", []):
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


# ---------- account (de)serialization ----------

def _coerce_points(value: Any) -> int | None:
    """Absolute Marvel Rivals MMR/SR for any tier. Blank or non-numeric -> None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------- marvelrivalsapi.com integration ----------

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

# Marvel Rivals ranked progression — numeric level → tier string.
# Bronze III is level 1, Roman numerals descend within a tier (III lowest,
# I highest). Level 22+ is Eternity (score-gated). One Above All is the
# top-N system and isn't directly derivable from level, so it falls through.
_LEVEL_TO_RANK = {
    1: "Bronze III", 2: "Bronze II", 3: "Bronze I",
    4: "Silver III", 5: "Silver II", 6: "Silver I",
    7: "Gold III",   8: "Gold II",   9: "Gold I",
    10: "Platinum III", 11: "Platinum II", 12: "Platinum I",
    13: "Diamond III",  14: "Diamond II",  15: "Diamond I",
    16: "Grandmaster III", 17: "Grandmaster II", 18: "Grandmaster I",
    19: "Celestial III", 20: "Celestial II", 21: "Celestial I",
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


def _extract_score(value: Any) -> int | None:
    """Pull a points/score number from common API shapes."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        for k in ("score", "points", "rank_score", "rankScore", "value"):
            if k in value and isinstance(value[k], (int, float)):
                return int(value[k])
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _dig(payload: Any, *paths: tuple[str, ...]) -> Any:
    """Walk a list of nested-key paths, returning the first hit (non-None)."""
    for path in paths:
        cur = payload
        ok = True
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if ok and cur is not None and cur != "":
            return cur
    return None


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


def _peak_from_seasons(player: dict[str, Any]) -> tuple[str, int | None]:
    """Walk every ranked season and return (rank_string, peak_rank_score).

    Picks the highest max_level across seasons (tiebreak: max_rank_score).
    Level 22+ is reported as 'Eternity'. The score is the player's absolute
    Marvel Rivals MMR / SR at that peak — used as-is for every tier (the
    in-game ranked system is absolute-score, not 0/100 within a division).
    """
    seasons = ((player or {}).get("info") or {}).get("rank_game_season")
    if not isinstance(seasons, dict):
        return "", None
    best_level = 0
    best_score = 0.0
    for sd in seasons.values():
        if not isinstance(sd, dict):
            continue
        ml = sd.get("max_level") or 0
        ms = sd.get("max_rank_score") or 0.0
        try:
            ml, ms = int(ml), float(ms)
        except (TypeError, ValueError):
            continue
        if ml > best_level or (ml == best_level and ms > best_score):
            best_level, best_score = ml, ms
    if best_level <= 0:
        return "", None
    score = int(best_score) if best_score > 0 else None
    if best_level in _LEVEL_TO_RANK:
        return _LEVEL_TO_RANK[best_level], score
    # Above Celestial I: Eternity (we can't tell OAA from level alone).
    return "Eternity", score


def _current_from_seasons(player: dict[str, Any]) -> tuple[str, int | None]:
    """Current rank + rank_score from the most-recently-updated season.

    The season's `level` maps straight through _LEVEL_TO_RANK. This is the
    dependable current-rank source: player.rank.rank is frequently stale
    ('Invalid level') for players marvelrivalsapi hasn't recrawled lately,
    but the season snapshots survive. Returns ('', None) when there is no
    season history at all.
    """
    seasons = ((player or {}).get("info") or {}).get("rank_game_season")
    if not isinstance(seasons, dict):
        return "", None
    latest = None
    latest_t = -1
    for sd in seasons.values():
        if not isinstance(sd, dict):
            continue
        t = sd.get("update_time") or 0
        try:
            t = int(t)
        except (TypeError, ValueError):
            continue
        if t > latest_t:
            latest_t, latest = t, sd
    if not latest:
        return "", None
    try:
        level = int(latest.get("level") or 0)
    except (TypeError, ValueError):
        level = 0
    try:
        score = int(float(latest.get("rank_score") or 0)) or None
    except (TypeError, ValueError):
        score = None
    if level in _LEVEL_TO_RANK:
        return _LEVEL_TO_RANK[level], score
    if level >= 22:  # above Celestial I — Eternity (can't tell OAA from level)
        return "Eternity", score
    return "", score


def _parse_api_timestamp(value: Any) -> int | None:
    """marvelrivalsapi 'updates' timestamps look like '12/16/2025, 7:20:27 AM'
    (US Eastern). Returns an epoch int, or None when unparseable. A few hours
    of timezone slop is irrelevant for the 'X days ago' display this feeds.
    """
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%m/%d/%Y, %I:%M:%S %p", "%m/%d/%Y, %H:%M:%S", "%m/%d/%Y"):
        try:
            return calendar.timegm(time.strptime(value.strip(), fmt))
        except ValueError:
            continue
    return None


def _parse_rivals_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the bits we care about from a marvelrivalsapi player response.

    Known schema (from a real response, May 2026):
      payload.uid                                    → numeric UID
      payload.player.rank.rank                       → 'Celestial III' etc.
      payload.player.info.rank_game_season[<sid>]    → per-season {level, rank_score, max_level, max_rank_score, update_time}
      payload.isPrivate                              → boolean
    Peak is derived from the highest max_level across all seasons.
    `rank_score` is the player's absolute Marvel Rivals MMR/SR and is
    captured for every tier (the game's ranked system is absolute-score,
    not 0/100 within a division).
    """
    if not isinstance(payload, dict):
        return {}
    player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
    uid = _dig(payload, ("uid",), ("player", "uid"), ("id",))

    # Current rank: the canonical string from player.rank.rank, with legacy
    # fallbacks in case the API ever moves it.
    cur_raw = _dig(
        payload,
        ("player", "rank", "rank"),
        ("player", "rank"),
        ("rank", "current"), ("current_rank",), ("rank",),
    )
    cur_rank = _normalize_rank(cur_raw)

    # The season history is the dependable source. player.rank.rank is often
    # 'Invalid level' (or otherwise unparseable) for players marvelrivalsapi
    # hasn't recrawled recently — when that happens, fall back to the most
    # recently updated season's level, which still maps cleanly to a tier.
    season_cur_rank, season_score = _current_from_seasons(player)
    if not cur_rank:
        cur_rank = season_cur_rank

    out: dict[str, Any] = {}
    if uid:
        out["rivals_uid"] = str(uid)

    # When marvelrivalsapi last crawled this player — drives the freshness
    # ("synced 2h ago" / "stale") indicator. The freshest of the several
    # 'updates' timestamps wins; info_update_time alone lags badly (it can sit
    # months behind a player whose match history updated yesterday). Captured
    # even on the bail path so a not_found row still shows its data age.
    updates = payload.get("updates")
    if isinstance(updates, dict):
        stamps = [_parse_api_timestamp(updates.get(k)) for k in
                  ("info_update_time", "last_history_update", "last_inserted_match")]
        stamps = [s for s in stamps if s is not None]
        if stamps:
            out["rivals_synced_at"] = max(stamps)

    # Nothing usable from player.rank OR the season history — bail with UID
    # only; the caller flags not_found and the user's rank fields stay put.
    if not cur_rank:
        return out

    # Peak: derived from the season history. Trust the level table over any
    # API-supplied "peak" string, since their player.rank is sometimes stale.
    peak_rank, peak_score = _peak_from_seasons(player)

    # Peak floor: a peak can't be lower than the current rank. The season
    # snapshots sometimes lag (or undercount placements), so clamp upward.
    if peak_rank and _RANK_INDEX.get(cur_rank, -1) > _RANK_INDEX.get(peak_rank, -1):
        peak_rank = cur_rank
        peak_score = season_score   # peak == current when we clamp
    elif not peak_rank:
        peak_rank = cur_rank
        peak_score = season_score

    out["current_rank"] = cur_rank
    out["peak_rank"] = peak_rank

    # Absolute MMR score from the latest season → current_points; highest
    # season's max_rank_score → peak_points. Captured for every tier (the
    # in-game ranked system is absolute-score, not 0/100 RR within division).
    if season_score is not None:
        out["current_points"] = season_score
    if peak_score is not None:
        out["peak_points"] = peak_score
    return out


def _is_private_payload(status_code: int, payload: Any) -> bool:
    """Detect 'profile is private' responses by inspecting the body content.

    Status alone is unreliable — marvelrivalsapi returns 403 for both an
    invalid API key AND a private profile, so we always check the message
    text to disambiguate.
    """
    if not isinstance(payload, dict):
        return status_code == 451  # spec-only "unavailable for legal reasons"
    if any(payload.get(k) is True for k in ("private", "is_private", "isPrivate")):
        return True
    msg = (payload.get("message") or payload.get("error") or "").lower()
    return "private" in msg or "hidden" in msg


def _is_bad_key_payload(payload: Any) -> bool:
    """Heuristic: does the upstream message say the API key is the problem?"""
    if not isinstance(payload, dict):
        return False
    msg = (payload.get("message") or payload.get("error") or "").lower()
    return ("api key" in msg) or ("unauthorized" in msg) or ("invalid key" in msg)


def _retry_after_s(headers: Any) -> int | None:
    """Parse a Retry-After header (delta-seconds form) into an int, or None."""
    try:
        v = headers.get("Retry-After") if headers else None
    except Exception:
        return None
    v = str(v).strip() if v else ""
    return int(v) if v.isdigit() else None


def _http_get_json(url: str, headers: dict[str, str]) -> tuple[int, Any, int | None]:
    """GET url → (status, parsed_json_or_None, retry_after_seconds_or_None).

    Records any X-RateLimit-* headers seen as a side effect (see _note_rate_headers).
    """
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=RIVALS_API_TIMEOUT_S) as r:
            body = r.read()
            _note_rate_headers(r.headers)
            retry_after = _retry_after_s(r.headers)
        try:
            return r.status, json.loads(body.decode("utf-8")), retry_after
        except (ValueError, UnicodeDecodeError):
            return r.status, None, retry_after
    except urllib.error.HTTPError as e:
        _note_rate_headers(e.headers)
        retry_after = _retry_after_s(e.headers)
        try:
            return e.code, json.loads(e.read().decode("utf-8")), retry_after
        except Exception:
            return e.code, None, retry_after


def _request_player_update(uid: str, api_key: str) -> None:
    """Best-effort: ask marvelrivalsapi to recrawl this player.

    marvelrivalsapi serves cached data and only recrawls a profile when one
    is explicitly requested; the recrawl completes asynchronously (0-30 min,
    usually 0-5). The endpoint locks a player for 30 min and penalizes repeat
    requests, so callers MUST gate this via _maybe_request_recrawl — never
    call it directly on a hot path. Errors are swallowed: a failed recrawl
    request must never break the refresh that triggered it.
    """
    if not uid or not api_key:
        return
    url = f"{RIVALS_API_BASE}/player/{urllib.parse.quote(str(uid), safe='')}/update"
    headers = {"x-api-key": api_key, "Accept": "application/json",
               "User-Agent": "MarvelAccountKeeper/1.0"}
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers),
            timeout=RIVALS_UPDATE_TIMEOUT_S,
        ) as r:
            _note_api_call()
            _note_rate_headers(r.headers)
    except urllib.error.HTTPError as e:
        _note_api_call()
        _note_rate_headers(e.headers)
        if e.code == 429:
            _note_rate_limited(_retry_after_s(e.headers) or 60)
    except Exception:
        pass


def _queue_player_update(uid: Any, api_key: str) -> None:
    """Spawn _request_player_update on a daemon thread (non-blocking)."""
    if not uid or not api_key:
        return
    threading.Thread(
        target=_request_player_update,
        args=(str(uid), api_key),
        daemon=True,
    ).start()


def _maybe_request_recrawl(acct: dict[str, Any], updates: dict[str, Any],
                           api_key: str) -> None:
    """Queue a marvelrivalsapi recrawl for this account — but only when it is
    actually warranted.

    The /update endpoint locks a player for 30 min and *penalizes* repeat
    requests (queue position resets, risk of a stuck processing loop), so we
    fire at most once per RIVALS_UPDATE_COOLDOWN_S and only when the cached
    data is itself stale. Records rivals_update_requested_at into `updates`
    when it fires so the cooldown persists in the vault.
    """
    if updates.get("last_refresh_status") == "bad_key":
        return
    uid = updates.get("rivals_uid") or acct.get("rivals_uid")
    if not uid or not api_key:
        return
    now = time.time()
    # Respect the per-player lock — another request now would only reset the
    # queue position and delay the recrawl we already asked for.
    last_req = acct.get("rivals_update_requested_at") or 0
    if last_req and now - last_req < RIVALS_UPDATE_COOLDOWN_S:
        return
    # No point recrawling data that is already fresh.
    synced = updates.get("rivals_synced_at") or acct.get("rivals_synced_at") or 0
    if synced and now - synced < RIVALS_UPDATE_COOLDOWN_S:
        return
    _queue_player_update(str(uid), api_key)
    updates["rivals_update_requested_at"] = int(now)


def _marvel_rivals_api_key() -> str:
    try:
        return (_read_vault().get("config", {}) or {}).get("marvel_rivals_api_key") or ""
    except (OSError, json.JSONDecodeError):
        return ""


def _utc_today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _note_api_call(n: int = 1) -> None:
    """Count calls made to marvelrivalsapi, bucketed by UTC day."""
    with _rivals_lock:
        if _rivals_usage["date"] != _utc_today():
            _rivals_usage["date"] = _utc_today()
            _rivals_usage["count"] = 0
        _rivals_usage["count"] += n


def _note_rate_limited(retry_after_s: int) -> None:
    """Record a 429 cooldown so the UI can warn until it clears."""
    global _rivals_rate_limited_until
    with _rivals_lock:
        _rivals_rate_limited_until = time.time() + max(1, int(retry_after_s or 0))


def _parse_rate_headers(headers: Any) -> dict[str, int] | None:
    """Pull numeric X-RateLimit-* values from a response.

    Returns None when the API serves the literal 'cache' for them (a cached
    hit carries no real numbers) so callers keep the last known-good values.
    """
    if not headers:
        return None

    def num(name: str) -> int | None:
        try:
            v = headers.get(name)
        except Exception:
            return None
        v = str(v).strip() if v is not None else ""
        return int(v) if v.lstrip("-").isdigit() else None

    parsed = {k: num(h) for k, h in (
        ("limit", "X-RateLimit-Limit"),
        ("remaining", "X-RateLimit-Remaining"),
        ("reset", "X-RateLimit-Reset"),
    )}
    if all(v is None for v in parsed.values()):
        return None
    return {k: v for k, v in parsed.items() if v is not None}


def _note_rate_headers(headers: Any) -> None:
    """Store the latest numeric X-RateLimit-* values seen (ignores cached hits)."""
    parsed = _parse_rate_headers(headers)
    if not parsed:
        return
    with _rivals_lock:
        _rivals_quota.update(parsed)
        _rivals_quota["at"] = int(time.time())


def _sync_status() -> dict[str, Any]:
    """Snapshot of API usage / rate-limit state for the frontend.

    `quota_*` come straight from the API's X-RateLimit-* headers (the limit is
    dynamic, so there is no fixed daily number); `calls_today` is our own count.
    """
    with _rivals_lock:
        count = _rivals_usage["count"] if _rivals_usage["date"] == _utc_today() else 0
        cooldown = max(0, int(_rivals_rate_limited_until - time.time()))
        quota = dict(_rivals_quota)
    return {
        "calls_today": count,
        "quota_limit": quota.get("limit"),
        "quota_remaining": quota.get("remaining"),
        "quota_reset": quota.get("reset"),
        "quota_seen_at": quota.get("at"),
        "rate_limited": cooldown > 0,
        "rate_limited_for_s": cooldown,
        "has_key": bool(_marvel_rivals_api_key()),
    }


def _persist_api_usage(vault: dict[str, Any]) -> None:
    """Mirror the in-memory call count into the vault config so it survives an
    app restart within the same UTC day."""
    with _rivals_lock:
        snapshot = dict(_rivals_usage)
    vault.setdefault("config", {})["rivals_api_usage"] = snapshot


def _load_api_usage() -> None:
    """Restore today's call count from the vault config at startup."""
    try:
        u = (_read_vault().get("config") or {}).get("rivals_api_usage")
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(u, dict) and u.get("date") == _utc_today():
        with _rivals_lock:
            _rivals_usage["date"] = u["date"]
            try:
                _rivals_usage["count"] = max(0, int(u.get("count") or 0))
            except (TypeError, ValueError):
                _rivals_usage["count"] = 0


def _refresh_account_stats(acct: dict[str, Any], api_key: str) -> dict[str, Any]:
    """Call marvelrivalsapi for one account; return a dict of fields to merge.

    Always sets last_refresh_ts / last_refresh_status / last_refresh_error.
    Sets rank/score/UID fields only on a successful parse.
    """
    now = int(time.time())
    base: dict[str, Any] = {
        "last_refresh_ts": now,
        "last_refresh_status": None,
        "last_refresh_error": None,
    }
    if not api_key:
        return {**base, "last_refresh_status": "bad_key",
                "last_refresh_error": "No Marvel Rivals API key set in Options."}

    # Respect the global rate-limit cooldown set by a prior 429 — without
    # this, spamming refresh during a cooldown burns the cooldown timer.
    cooldown_left = int(_rivals_rate_limited_until - time.time())
    if cooldown_left > 0:
        return {**base, "last_refresh_status": "error",
                "last_refresh_error": f"Rate limit cooldown — wait ~{cooldown_left}s, then retry."}

    # marvelrivalsapi indexes Marvel Rivals player names — look up by the
    # in-game name, never the Steam `username` (a login credential the API
    # has never heard of). Prefer the UID once a past refresh resolved one.
    handle = acct.get("rivals_uid") or acct.get("in_game_name") or ""
    handle = (handle or "").strip()
    if not handle:
        return {**base, "last_refresh_status": "missing_handle",
                "last_refresh_error": "Account has no in-game name to look up."}

    url = f"{RIVALS_API_BASE}/player/{urllib.parse.quote(handle, safe='')}"
    headers = {"x-api-key": api_key, "Accept": "application/json",
               "User-Agent": "MarvelAccountKeeper/1.0"}
    try:
        status, payload, retry_after = _http_get_json(url, headers)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {**base, "last_refresh_status": "error",
                "last_refresh_error": f"Network error: {e}"}
    _note_api_call()

    # 401/403 mean "auth rejected" *unless* the body specifically says "private".
    # marvelrivalsapi uses 403 for both, so the message body is the tiebreaker.
    if status in (401, 403):
        if _is_private_payload(status, payload):
            return {**base, "last_refresh_status": "private",
                    "last_refresh_error": "Profile is set to private in-game."}
        return {**base, "last_refresh_status": "bad_key",
                "last_refresh_error": (
                    (isinstance(payload, dict) and (payload.get("error") or payload.get("message")))
                    or f"API rejected the key (HTTP {status})."
                )}
    if status == 404:
        return {**base, "last_refresh_status": "not_found",
                "last_refresh_error": "Player not found on marvelrivalsapi.com."}
    if status == 429:
        wait = retry_after or 60
        _note_rate_limited(wait)
        return {**base, "last_refresh_status": "error",
                "last_refresh_error": f"Rate limited by marvelrivalsapi — wait ~{wait}s, then retry."}
    if _is_private_payload(status, payload):
        return {**base, "last_refresh_status": "private",
                "last_refresh_error": "Profile is set to private in-game."}
    if _is_bad_key_payload(payload):
        return {**base, "last_refresh_status": "bad_key",
                "last_refresh_error": payload.get("error") or payload.get("message")}
    if status >= 400 or not isinstance(payload, dict):
        return {**base, "last_refresh_status": "error",
                "last_refresh_error": f"Unexpected HTTP {status}."}

    parsed = _parse_rivals_payload(payload)
    if not parsed:
        return {**base, "last_refresh_status": "not_found",
                "last_refresh_error": "Response had no recognizable rank fields."}

    # Sync the in-game name from the API response. The API's name is the
    # canonical IGN — including charmap / superscript characters the user
    # can't type into the form. We adopt it when the field is blank (covers
    # UID-only adds) OR whenever the lookup ran by UID: a UID is the stable
    # identity, so its name is authoritative and a plain-text approximation
    # the user typed should follow it. A name-only lookup never overwrites,
    # since there the typed name is itself the lookup key.
    extra: dict[str, Any] = {}
    api_name = payload.get("name")
    api_name = api_name.strip() if isinstance(api_name, str) else ""
    current_ign = (acct.get("in_game_name") or "").strip()
    by_uid = bool((acct.get("rivals_uid") or "").strip())
    if api_name and (not current_ign or (by_uid and api_name != current_ign)):
        extra["in_game_name"] = api_name

    # Got something, but no rank fields means the upstream knows the player
    # exists yet has stale/missing rank data ('Invalid level' etc.). Keep the
    # UID (useful for future lookups) but flag as not_found so we don't
    # silently leave the user thinking the refresh succeeded.
    if "current_rank" not in parsed:
        return {**base, "last_refresh_status": "not_found",
                "last_refresh_error": "marvelrivalsapi has stale/incomplete rank data for this player.",
                **extra,
                **{k: v for k, v in parsed.items()
                   if k in ("rivals_uid", "rivals_synced_at")}}

    return {**base, "last_refresh_status": "ok", **extra, **parsed}


# ---------- match history ----------
# marvelrivalsapi exposes /player/{uid}/match-history with the last 20 ranked
# matches. We slim each match to the few fields the UI shows and stash them
# on the account so they survive a page reload — much friendlier than a fresh
# API hit on every drawer-open.

MATCH_HISTORY_KEEP = 20

# play_mode_id / game_mode_id labels — derived empirically from real data.
# These are best-effort; the API doesn't publish a key list and we fall back
# to "Match" when an ID is unrecognized.
_GAME_MODE_LABELS = {0: "Quick Match", 1: "Ranked", 2: "Competitive", 3: "Custom", 4: "Tournament"}


def _slim_match(raw: dict[str, Any], player_uid: int | str | None) -> dict[str, Any] | None:
    """Compress one match payload into the row the UI renders."""
    if not isinstance(raw, dict):
        return None
    mp = raw.get("match_player") if isinstance(raw.get("match_player"), dict) else {}
    hero = mp.get("player_hero") if isinstance(mp.get("player_hero"), dict) else {}
    score = mp.get("score_info") if isinstance(mp.get("score_info"), dict) else {}
    iw = mp.get("is_win") if isinstance(mp.get("is_win"), dict) else {}

    try:
        own_uid = int(player_uid) if player_uid is not None else None
    except (TypeError, ValueError):
        own_uid = None

    def _i(v: Any, default: int = 0) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _f(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    return {
        "uid": str(raw.get("match_uid") or ""),
        "ts": _i(raw.get("match_time_stamp")),
        "duration_s": int(_f(raw.get("match_play_duration"))),
        "map_id": _i(raw.get("match_map_id")),
        "map_thumbnail": raw.get("map_thumbnail") or "",
        "hero_name": (hero.get("hero_name") or "").title(),
        "hero_image": hero.get("hero_type") or "",
        "kda": [_i(mp.get("kills")), _i(mp.get("deaths")), _i(mp.get("assists"))],
        "is_win": bool(iw.get("is_win")),
        "is_mvp": own_uid is not None and _i(raw.get("mvp_uid")) == own_uid,
        "is_svp": own_uid is not None and _i(raw.get("svp_uid")) == own_uid,
        "sr_delta": round(_f(score.get("add_score")), 1),
        "sr_after": round(_f(score.get("new_score")), 1),
        "level_after": _i(score.get("new_level")),
        "season": str(raw.get("match_season") or ""),
        "play_mode_id": _i(raw.get("play_mode_id")),
        "game_mode_id": _i(raw.get("game_mode_id")),
        "mode_label": _GAME_MODE_LABELS.get(_i(raw.get("game_mode_id")), "Match"),
        "disconnected": bool(mp.get("disconnected")),
    }


def _fetch_match_history(acct: dict[str, Any], api_key: str) -> dict[str, Any]:
    """Call /player/{uid}/match-history and return updates to merge in."""
    now = int(time.time())
    base: dict[str, Any] = {
        "matches_synced_at": now,
        "matches_error": None,
    }
    if not api_key:
        return {**base, "matches_error": "No Marvel Rivals API key set in Options."}
    uid = (acct.get("rivals_uid") or "").strip()
    if not uid:
        return {**base,
                "matches_error": "Refresh the account's rank first to resolve its UID."}

    url = f"{RIVALS_API_BASE}/player/{urllib.parse.quote(uid, safe='')}/match-history"
    headers = {"x-api-key": api_key, "Accept": "application/json",
               "User-Agent": "MarvelAccountKeeper/1.0"}
    try:
        status, payload, retry_after = _http_get_json(url, headers)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {**base, "matches_error": f"Network error: {e}"}
    _note_api_call()

    if status == 429:
        _note_rate_limited(retry_after or 60)
        return {**base, "matches_error":
                f"Rate limited — wait ~{retry_after or 60}s, then retry."}
    if status in (401, 403):
        if _is_private_payload(status, payload):
            return {**base, "matches_error": "Profile is set to private in-game."}
        return {**base, "matches_error": f"API rejected the key (HTTP {status})."}
    if status == 404:
        return {**base, "matches_error": "No matches on file for this player."}
    if status >= 400 or not isinstance(payload, dict):
        return {**base, "matches_error": f"Unexpected HTTP {status}."}

    raw_matches = payload.get("match_history") or []
    if not isinstance(raw_matches, list):
        return {**base, "matches_error": "Response had no match_history list."}

    slim = [m for m in (_slim_match(r, uid) for r in raw_matches) if m]
    slim.sort(key=lambda m: m["ts"], reverse=True)
    return {**base, "recent_matches": slim[:MATCH_HISTORY_KEEP]}


def _account_from_payload(payload: dict[str, Any], key: bytes, existing: dict | None = None) -> dict[str, Any]:
    now = int(time.time())
    if existing:
        acct = dict(existing)
    else:
        acct = {"id": uuid.uuid4().hex, "created_at": now}
    for field in ("username", "email", "in_game_name", "peak_rank",
                  "current_rank", "notes", "border_color", "tag"):
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
        "has_marvel_rivals_api_key": bool(
            (vault.get("config", {}) or {}).get("marvel_rivals_api_key")
        ),
        "version": APP_VERSION,
        "commit": APP_COMMIT,
        "built_at": APP_BUILT_AT,
        "repo": GITHUB_REPO_SLUG,
    })


@app.route("/api/options", methods=["GET"])
def get_options():
    return jsonify({
        "lockout_minutes": _lockout_minutes(),
        "has_marvel_rivals_api_key": bool(_marvel_rivals_api_key()),
    })


@app.route("/api/rivals/sync-status")
def rivals_sync_status():
    """API usage / rate-limit snapshot. No secrets — safe without unlock."""
    return jsonify(_sync_status())


@app.route("/api/options", methods=["POST"])
def set_options():
    _key, err = _require_key()
    if err:
        return err
    payload = request.json or {}
    vault = _read_vault()
    cfg = vault.setdefault("config", {})
    if "lockout_minutes" in payload:
        try:
            m = int(payload["lockout_minutes"])
        except (TypeError, ValueError):
            return jsonify({"error": "bad_value", "message": "lockout_minutes must be a number."}), 400
        cfg["lockout_minutes"] = max(0, m)
    if "marvel_rivals_api_key" in payload:
        raw = payload.get("marvel_rivals_api_key")
        # Empty / None clears the key; anything else is stored as-is.
        cfg["marvel_rivals_api_key"] = (str(raw).strip() if raw else "")
    _write_vault(vault)
    return jsonify({
        "ok": True,
        "lockout_minutes": cfg.get("lockout_minutes", DEFAULT_LOCKOUT_MINUTES),
        "has_marvel_rivals_api_key": bool(cfg.get("marvel_rivals_api_key")),
    })


@app.route("/api/init", methods=["POST"])
def init_vault():
    vault = _read_vault()
    if vault.get("initialized"):
        return jsonify({"error": "already_initialized"}), 400
    password = (request.json or {}).get("password", "")
    if len(password) < 6:
        return jsonify({"error": "password_too_short", "message": "Master password must be at least 6 characters."}), 400
    salt = secrets.token_bytes(16)
    key = _derive_key(password, salt)
    verifier = _encrypt(key, VERIFIER_PLAINTEXT.decode("utf-8"))
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
    return jsonify({"ok": True})


@app.route("/api/unlock", methods=["POST"])
def unlock():
    vault = _read_vault()
    if not vault.get("initialized"):
        return jsonify({"error": "not_initialized"}), 400
    password = (request.json or {}).get("password", "")
    salt = bytes.fromhex(vault["kdf"]["salt"])
    key = _derive_key(password, salt)
    try:
        plain = _decrypt(key, vault["verifier"])
    except Exception:
        return jsonify({"error": "bad_password"}), 401
    if plain != VERIFIER_PLAINTEXT.decode("utf-8"):
        return jsonify({"error": "bad_password"}), 401
    with _state_lock:
        _state["key"] = key
        _state["last_activity"] = time.time()
    return jsonify({"ok": True})


@app.route("/api/lock", methods=["POST"])
def lock():
    with _state_lock:
        _state["key"] = None
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
    vault = _read_vault()
    acct = _account_from_payload(payload, key)
    vault.setdefault("accounts", []).append(acct)
    _write_vault(vault)
    return jsonify({"ok": True, "id": acct["id"]})


@app.route("/api/accounts/<acct_id>", methods=["PUT"])
def update_account(acct_id: str):
    key, err = _require_key()
    if err:
        return err
    payload = request.json or {}
    vault = _read_vault()
    accounts = vault.get("accounts", [])
    for i, acct in enumerate(accounts):
        if acct["id"] == acct_id:
            accounts[i] = _account_from_payload(payload, key, existing=acct)
            _write_vault(vault)
            return jsonify({"ok": True})
    return jsonify({"error": "not_found"}), 404


@app.route("/api/accounts/reorder", methods=["POST"])
def reorder_accounts():
    _key, err = _require_key()
    if err:
        return err
    payload = request.json or {}
    new_order = payload.get("order") or []
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
    api_key = _marvel_rivals_api_key()
    updates = _refresh_account_stats(target, api_key)
    # Queue a background recrawl when warranted (gated — see _maybe_request_recrawl).
    _maybe_request_recrawl(target, updates, api_key)
    merged = _apply_refresh_updates(vault, acct_id, updates)
    _persist_api_usage(vault)
    _write_vault(vault)
    return jsonify({"ok": True, "account": _public_account(merged, key),
                    "sync": _sync_status()})


@app.route("/api/accounts/refresh-all", methods=["POST"])
def refresh_all_accounts():
    key, err = _require_key()
    if err:
        return err
    api_key = _marvel_rivals_api_key()
    if not api_key:
        return jsonify({"error": "missing_api_key",
                        "message": "Set your Marvel Rivals API key in Options first."}), 400
    vault = _read_vault()
    ids = [a["id"] for a in vault.get("accounts", [])]
    summary = {"ok": 0, "private": 0, "not_found": 0, "bad_key": 0,
               "error": 0, "missing_handle": 0}
    updated_accounts: list[dict[str, Any]] = []
    for i, acct_id in enumerate(ids):
        target = next((a for a in vault.get("accounts", []) if a["id"] == acct_id), None)
        if target is None:
            continue
        updates = _refresh_account_stats(target, api_key)
        # Queue a background recrawl when warranted (gated — see _maybe_request_recrawl).
        _maybe_request_recrawl(target, updates, api_key)
        merged = _apply_refresh_updates(vault, acct_id, updates)
        st = updates.get("last_refresh_status") or "error"
        summary[st] = summary.get(st, 0) + 1
        if merged is not None:
            updated_accounts.append(_public_account(merged, key))
        # Bail early on bad_key — every subsequent call would also fail.
        # Same for a 429: the cooldown applies globally, so plowing through
        # the remaining accounts just stacks up more rate-limited failures.
        if st == "bad_key":
            break
        if st == "error" and _rivals_rate_limited_until > time.time():
            # Mark the rest as skipped so the UI surfaces *why* they didn't run.
            for skipped_id in ids[i + 1:]:
                skipped = next((a for a in vault.get("accounts", []) if a["id"] == skipped_id), None)
                if skipped is None:
                    continue
                cooldown_s = max(1, int(_rivals_rate_limited_until - time.time()))
                merged_skip = _apply_refresh_updates(vault, skipped_id, {
                    "last_refresh_ts": int(time.time()),
                    "last_refresh_status": "error",
                    "last_refresh_error": f"Skipped — rate limit cooldown ~{cooldown_s}s. Retry later.",
                })
                summary["error"] = summary.get("error", 0) + 1
                if merged_skip is not None:
                    updated_accounts.append(_public_account(merged_skip, key))
            break
        if i < len(ids) - 1:
            time.sleep(REFRESH_ALL_DELAY_S)
    _persist_api_usage(vault)
    _write_vault(vault)
    return jsonify({"ok": True, "summary": summary, "accounts": updated_accounts,
                    "sync": _sync_status()})


@app.route("/api/accounts/<acct_id>", methods=["DELETE"])
def delete_account(acct_id: str):
    _key, err = _require_key()
    if err:
        return err
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
_load_api_usage()
_cleanup_post_update()


# ---------- liveness heartbeat ----------
# The frontend POSTs /api/heartbeat every ~30s while a browser tab is open. In
# the default (browser) mode the launcher watches this timestamp and exits
# once every tab has closed — so a double-clicked .exe behaves like an app and
# leaves no orphaned process behind.

_heartbeat_lock = threading.Lock()
_last_heartbeat = 0.0
_heartbeat_seen = False
IDLE_SHUTDOWN_S = 120      # quit this long after the browser's last heartbeat
STARTUP_GRACE_S = 300      # ...or this long if a browser never connects at all


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
    # release workflow. Releases >= v1.7.0 publish this file; older ones
    # (v1.6.0 and below) don't, and we allow the update through with a flag
    # in the response so the UI can mention that verification was skipped.
    checksum_url = _release_checksum_url(release)
    expected = _fetch_expected_checksum(checksum_url) if checksum_url else None
    verified = False
    if expected is not None:
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

    return jsonify({
        "ok": True,
        "installed": latest_tag.lstrip("vV"),
        "verified": verified,
        "message": f"v{latest_tag.lstrip('vV')} downloaded. Close the app and reopen "
                   f"it to run the new version.",
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


@app.route("/api/rivals/lookup-uid/<uid>")
def rivals_lookup_uid(uid: str):
    """Resolve a Marvel Rivals UID to its current in-game name via
    marvelrivalsapi. Used by the drawer to preview which player owns
    each locally-detected UID before claiming it for a vault entry.

    Authenticated (needs the unlocked vault to read the stored API key)."""
    _key, err = _require_key()
    if err:
        return err
    api_key = _marvel_rivals_api_key()
    if not api_key:
        return jsonify({"error": "missing_api_key"}), 400
    uid = re.sub(r"\D", "", uid or "")
    if not uid:
        return jsonify({"error": "bad_uid"}), 400
    cooldown_left = int(_rivals_rate_limited_until - time.time())
    if cooldown_left > 0:
        return jsonify({"error": "rate_limited", "retry_after_s": cooldown_left}), 429

    url = f"{RIVALS_API_BASE}/player/{urllib.parse.quote(uid, safe='')}"
    headers = {"x-api-key": api_key, "Accept": "application/json",
               "User-Agent": f"{APP_NAME}/{APP_VERSION}"}
    try:
        status, payload, retry_after = _http_get_json(url, headers)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return jsonify({"error": "network", "message": str(e)}), 502
    _note_api_call()
    if status == 429:
        _note_rate_limited(retry_after or 60)
        return jsonify({"error": "rate_limited", "retry_after_s": retry_after or 60}), 429
    if status == 404 or not isinstance(payload, dict):
        return jsonify({"error": "not_found"}), 404
    if _is_private_payload(status, payload):
        # Even a private profile usually returns a `name` field — surface it
        # so the user still gets the IGN; just flag the privacy state.
        name = (payload.get("name") or "").strip() if isinstance(payload, dict) else ""
        return jsonify({"uid": uid, "name": name or None, "private": True})
    if status >= 400:
        return jsonify({"error": "upstream", "status": status}), 502
    return jsonify({"uid": uid, "name": (payload.get("name") or "").strip() or None})


@app.route("/api/accounts/<acct_id>/matches", methods=["POST"])
def fetch_match_history(acct_id: str):
    """Pull the last 20 ranked matches for an account and persist them."""
    key, err = _require_key()
    if err:
        return err
    vault = _read_vault()
    target = next((a for a in vault.get("accounts", []) if a["id"] == acct_id), None)
    if target is None:
        return jsonify({"error": "not_found"}), 404
    if not (target.get("rivals_uid") or "").strip():
        return jsonify({"error": "no_uid",
                        "message": "Refresh this account's rank first to resolve its UID."}), 400

    api_key = _marvel_rivals_api_key()
    updates = _fetch_match_history(target, api_key)
    merged = _apply_refresh_updates(vault, acct_id, updates)
    _persist_api_usage(vault)
    _write_vault(vault)
    return jsonify({"ok": True, "account": _public_account(merged, key),
                    "sync": _sync_status()})


@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    global _last_heartbeat, _heartbeat_seen
    with _heartbeat_lock:
        _last_heartbeat = time.time()
        _heartbeat_seen = True
    return jsonify({"ok": True})


# ---------- launcher ----------

def _free_port() -> int:
    """Ask the OS for an unused loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _shutdown(reason: str) -> None:
    """Print a parting line and end the process."""
    print(f"\n  {reason} — Marvel Account Keeper stopped. Your vault is saved.")
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


def main() -> None:
    # Zero arguments is the intended path — a double-clicked .exe just works.
    # The flags below exist only for the rare headless / power-user case.
    parser = argparse.ArgumentParser(
        prog="MarvelAccountKeeper",
        description="Local encrypted Marvel Rivals account vault. "
                    "Just run it with no arguments — the flags are optional.")
    parser.add_argument("--no-browser", action="store_true",
                        help="run headless: don't open a browser, and keep "
                             "running until stopped manually")
    parser.add_argument("--port", type=int, default=0, metavar="N",
                        help="serve on a fixed port instead of a random one")
    parser.add_argument("--keep-alive", action="store_true",
                        help="open the browser but don't quit when it closes")
    args = parser.parse_args()

    # Quiet the per-request Werkzeug log lines — the banner is what matters.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    port = args.port or _free_port()
    url = f"http://127.0.0.1:{port}"
    open_browser = not args.no_browser
    # Default: behave like an app — quit shortly after the browser closes so
    # nothing is left running. Headless or --keep-alive opt out of that.
    auto_stop = open_browser and not args.keep_alive

    line = "=" * 60
    print(line)
    print("  Marvel Account Keeper")
    print("  " + "-" * 56)
    print(f"  Open in browser : {url}")
    print(f"  Vault file      : {VAULT_PATH}")
    print()
    if open_browser:
        print("  Your browser should open automatically.")
    else:
        print("  Open the address above in your browser.")
    if auto_stop:
        print("  Done? Just close the browser — this window closes itself.")
    else:
        print("  Keep this window open while you use the app; Ctrl+C quits.")
    print(line)

    # Open the browser shortly after the server starts listening.
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # In the default app-like mode, quit once the browser is gone.
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
    print("\n  Marvel Account Keeper stopped. Your vault is saved.")


if __name__ == "__main__":
    main()
