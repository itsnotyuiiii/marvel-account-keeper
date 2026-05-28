"""Generate a throwaway demo vault full of fake accounts.

    python make_demo_vault.py

Writes ./demo-data/vault.json — a fully valid vault (master password below)
with ten fictional accounts that show off every part of the UI: the MAIN /
PEAKED OAA labels, neon borders, tags, rank tiers across the whole ladder,
Eternity/One-Above-All point scores, and the full range of sync states.

Run the app against it without touching your real vault:

    # PowerShell
    $env:MARVEL_KEEPER_DATA = "$PWD\\demo-data"; python app.py

Nothing here is real — safe to screen-record for a demo video.
"""
from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# Must match app.py.
SCRYPT_N, SCRYPT_R, SCRYPT_P, KEY_LEN = 2**15, 8, 1, 32
VERIFIER_PLAINTEXT = "VAULT_OK::v1"

DEMO_PASSWORD = "demo1234"          # what you type on the lock screen
OUT = Path(__file__).parent / "demo-data" / "vault.json"

NOW = int(time.time())
HOUR, DAY, MIN = 3600, 86400, 60


def _encrypt(key: bytes, plaintext: str) -> dict[str, str]:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return {"nonce": nonce.hex(), "ct": ct.hex()}


def _real_api_key() -> str:
    """Borrow the marvelrivalsapi key from the real vault so the demo can show
    the refresh buttons / live sync. Read-only — the real vault is never
    touched. No key found (e.g. fresh clone) just means no refresh buttons.
    """
    home = Path.home()
    candidates = [
        Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming"),
        home / "Library" / "Application Support",
        Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share"),
    ]
    for base in candidates:
        real = base / "MarvelAccountKeeper" / "vault.json"
        try:
            cfg = json.loads(real.read_text("utf-8")).get("config", {})
            if cfg.get("marvel_rivals_api_key"):
                return cfg["marvel_rivals_api_key"]
        except (OSError, ValueError):
            continue
    return ""


# (ign, username, email, password, current, peak, cur_pts, peak_pts,
#  pinned, neon, border_color, tag, synced_ago, status)
#
# `cur_pts` / `peak_pts` are absolute Marvel Rivals MMR/SR — the same number
# the in-game ranked screen shows. Approximate S1 thresholds:
#   Bronze III ~0, Silver III ~500, Gold III ~1100, Platinum III ~2000,
#   Diamond III ~2900, Grandmaster III ~3800, Celestial III ~4500,
#   Celestial I ~5000, Eternity 5300+, One Above All 6000+.
ACCOUNTS = [
    ("WebSlinger",     "web_slinger88",  "webslinger@example.com",  "Spidey$wing12",
     "Celestial II",   "Celestial I",    4780, 5040, True,  True,  "red",    "Main",     10 * MIN, "ok"),
    ("CosmicStarlord", "cosmic_lord",    "starlord@example.com",    "Z3ro!gravity",
     "Grandmaster I",  "One Above All",  4380, 6210, False, True,  "yellow", "OAA push", 4 * HOUR, "ok"),
    ("StormbornQ",     "storm_q",        "stormborn@example.com",   "Thunder!9rain",
     "Celestial III",  "Celestial I",    4520, 5020, False, True,  "green",  "Smurf",    2 * DAY,  "ok"),
    ("IronCladVet",    "ironclad_v",     "ironclad@example.com",    "R3pulsor!beam",
     "Grandmaster III","Grandmaster I",  3870, 4430, False, False, "",       "",         None,     "private"),
    ("NovaByte",       "nova_byte",      "novabyte@example.com",    "Sup3rnova!x",
     "Diamond I",      "Diamond I",      3540, 3590, False, True,  "cyan",   "",         1 * HOUR, "ok"),
    ("ScarletHex",     "scarlet_hex",    "scarlethex@example.com",  "H3x!crimson7",
     "Platinum II",    "Diamond III",    2310, 2950, False, False, "",       "Alt",      8 * HOUR, "ok"),
    ("GrootlyBudz",    "groot_budz",     "grootly@example.com",     "I.am!Groot33",
     "Gold III",       "Platinum II",    1180, 2280, False, False, "",       "",         20 * MIN, "ok"),
    ("NightProwler",   "night_prowler",  "prowler@example.com",     "Sh4dow!step1",
     "Silver II",      "Gold I",         620,  1720, False, False, "",       "",         None,     None),
    ("BronzeBaron",    "bronze_baron",   "bronzebaron@example.com", "Pl4cement!run",
     "Bronze I",       "Silver II",      280,  640,  False, False, "",       "New",      12 * HOUR, "ok"),
    ("EternalFlux",    "eternal_flux",   "eternalflux@example.com", "Inf1nity!loop",
     "Eternity",       "Eternity",       5520, 5780, False, True,  "orange", "Grinding", 5 * MIN,  "ok"),
    # Real public IGN — left "not synced" so the demo can refresh it live.
    ("HoldThisAcorn",  "hta_login",      "holdthisacorn@example.com", "Acorn$tash44",
     "Celestial III",  "Celestial I",    None, None, False, True,  "cyan",   "Live demo", None,    None),
]

# Real Marvel Rivals UIDs for accounts that should refresh live in the demo.
REAL_UID = {"HoldThisAcorn": "326631126"}


def build_vault() -> dict:
    salt = secrets.token_bytes(16)
    key = Scrypt(salt=salt, length=KEY_LEN, n=SCRYPT_N, r=SCRYPT_R,
                 p=SCRYPT_P).derive(DEMO_PASSWORD.encode("utf-8"))

    accounts = []
    for i, a in enumerate(ACCOUNTS):
        (ign, user, email, pw, cur, peak, cur_pts, peak_pts,
         pinned, neon, border, tag, synced_ago, status) = a
        synced_at = None if synced_ago is None else NOW - synced_ago
        accounts.append({
            "id": uuid.uuid4().hex,
            "created_at": NOW - (i + 3) * DAY,
            "username": user,
            "email": email,
            "in_game_name": ign,
            "peak_rank": peak,
            "current_rank": cur,
            "notes": "",
            "border_color": border,
            "password_enc": _encrypt(key, pw),
            "updated_at": NOW - (i + 1) * HOUR,
            "pinned": pinned,
            "tag": tag,
            "neon": neon,
            "current_points": cur_pts,
            "peak_points": peak_pts,
            "rivals_uid": REAL_UID.get(ign, str(100000000 + i * 7654321)),
            "last_refresh_ts": None if status is None else synced_at,
            "last_refresh_status": status,
            "last_refresh_error": ("Profile is set to private in-game."
                                   if status == "private" else None),
            "rivals_synced_at": synced_at,
            "rivals_update_requested_at": None,
        })

    return {
        "initialized": True,
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R,
                "p": SCRYPT_P, "salt": salt.hex()},
        "verifier": _encrypt(key, VERIFIER_PLAINTEXT),
        "config": {"lockout_minutes": 30,
                   "marvel_rivals_api_key": _real_api_key()},
        "accounts": accounts,
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build_vault(), indent=2), encoding="utf-8")
    print(f"  Demo vault written: {OUT}")
    print(f"  Accounts: {len(ACCOUNTS)}   Master password: {DEMO_PASSWORD}")
    print(f"  Run it:  $env:MARVEL_KEEPER_DATA = \"{OUT.parent}\"; python app.py")


if __name__ == "__main__":
    main()
