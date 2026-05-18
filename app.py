"""Marvel Rivals / Steam account tracker.

Single-user local-only app. Stores account records in vault.json, with the
password field encrypted under a key derived from a master password (scrypt
+ AES-256-GCM). The derived key lives only in process memory after unlock.

Run directly (`python app.py`) or as the packaged executable: it picks a free
loopback port, opens the default browser, and serves until Ctrl+C.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import socket
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from flask import Flask, jsonify, render_template, request

APP_NAME = "MarvelAccountKeeper"
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
}


# ---------- locations ----------

def _resource_dir() -> Path:
    """Directory holding bundled templates/ and static/.

    Under a PyInstaller --onefile build this is the temp extraction dir
    (sys._MEIPASS); otherwise it is the folder this file lives in.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent


def _data_dir() -> Path:
    """Per-user writable directory for the vault and its backups.

      Windows : %APPDATA%/MarvelAccountKeeper/
      macOS   : ~/Library/Application Support/MarvelAccountKeeper/
      Linux   : ~/.local/share/MarvelAccountKeeper/
    """
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
    """Rank score for Eternity / One Above All. Blank or non-numeric -> None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    })


@app.route("/api/options", methods=["GET"])
def get_options():
    return jsonify({"lockout_minutes": _lockout_minutes()})


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
    _write_vault(vault)
    return jsonify({"ok": True, "lockout_minutes": cfg.get("lockout_minutes", DEFAULT_LOCKOUT_MINUTES)})


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


# ---------- launcher ----------

def _free_port() -> int:
    """Ask the OS for an unused loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    # Quiet the per-request Werkzeug log lines — the banner is what matters.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    line = "=" * 60
    print(line)
    print("  Marvel Account Keeper")
    print("  " + "-" * 56)
    print(f"  Open in browser : {url}")
    print(f"  Vault file      : {VAULT_PATH}")
    print()
    print("  Your browser should open automatically.")
    print("  Keep this window open while you use the app.")
    print("  Press Ctrl+C to quit.")
    print(line)

    # Open the browser shortly after the server starts listening.
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        app.run(host="127.0.0.1", port=port, debug=False)
    except KeyboardInterrupt:
        pass
    print("\n  Marvel Account Keeper stopped. Your vault is saved.")


if __name__ == "__main__":
    main()
