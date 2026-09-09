"""Redacted, offline inspection of this installed Marvel Rivals SDK log.

Default: file metadata only. --inspect decodes the existing game-specific log
in transient Python memory, which can contain account credentials. Only fixed
labels, counts and structural flags are returned; never plaintext, token values,
claim values, user identifiers or the native log-encryption parameter. This is
not a token exporter or a provider. No network, process access or game DLL calls.
The separate main-game log has a different format and is not supported here.
"""

import argparse
import base64
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


DLL_PATH = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\MarvelRivals"
    r"\MarvelGame\Marvel\Binaries\Win64\NtUniSdkBase.dll"
)
DLL_SHA256 = "d134ce2e46c1b4fb97b569db9fa3f3b9052309a0d9e96f9a3adf39e08b498163"
LOG_PARAMETER_RVA = 0x4B0100
GAME_ID = "x20naxx2gb"
SDK_AUTH_URL = "https://mgbjp-x20na.unisdk.easebar.com/x20naxx2gb/sdk/uni_sauth"
MAX_DLL_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 6 * 1024 * 1024
MAX_ENCODED_RECORD_BYTES = 64 * 1024
MAX_RECORDS = 10000
LABELS = (
    "UNISDK_LOGIN_JSON", "sdkuid", "uni_sauth", "gas_token", "client_login_sn",
    "SocialMatrix", "SAUTH_JSON", "websocket", "xyq-career",
)
TIMESTAMP = re.compile(r"(20\d{2})[-./](\d{2})[-./](\d{2})[ T]\d{2}:\d{2}:\d{2}")
JWT = re.compile(r"(?<![A-Za-z0-9_-])(eyJ[A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)(?![A-Za-z0-9_-])")


class InspectionError(ValueError):
    """Fixed error codes only: never include source records in errors."""


def default_log_path():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise InspectionError("local_app_data_unavailable")
    # Directory mapping verified against the native logger and installed config.
    game_hash = hashlib.md5(GAME_ID.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    return Path(local_app_data) / "UniSDK" / "Log" / game_hash / "NtUniSdk.log"


def load_log_parameter(path=DLL_PATH):
    """Read a fixed RVA only after validating the complete installed DLL hash."""
    with path.open("rb") as source:
        binary = source.read(MAX_DLL_BYTES + 1)
    if len(binary) > MAX_DLL_BYTES or hashlib.sha256(binary).hexdigest() != DLL_SHA256:
        raise InspectionError("unsupported_sdk_build")
    import pefile  # Optional research dependency, never loads/executes the DLL.

    pe = pefile.PE(data=binary, fast_load=True)
    try:
        parameter = pe.get_data(LOG_PARAMETER_RVA, 32)
    finally:
        pe.close()
    if len(parameter) != 32:
        raise InspectionError("log_parameter_unavailable")
    return parameter


def decode_record(encoded, parameter):
    encoded = encoded.rstrip(b"\r\n")
    if not encoded or len(encoded) > MAX_ENCODED_RECORD_BYTES:
        raise InspectionError("invalid_record_size")
    try:
        ciphertext = base64.b64decode(encoded, validate=True)
        if not ciphertext or len(ciphertext) % 16:
            raise ValueError
        decryptor = Cipher(algorithms.AES(parameter), modes.ECB()).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        return plaintext.decode("utf-8", errors="strict")
    except (ValueError, UnicodeError):
        raise InspectionError("invalid_encrypted_record") from None


def first_json_object(line):
    decoder = json.JSONDecoder()
    # Bounded attempts for unstructured log prefixes; no record text is returned
    # by the public report. Nested data is not recursively dumped or traversed.
    for start in [m.start() for m in re.finditer(r"\{", line)][:64]:
        try:
            value, _ = decoder.raw_decode(line, start)
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def summarize_stream(source, parameter, selected_day, *, now=None):
    """Inspect a bounded stream, returning only allowlisted non-secret results."""
    now = time.time() if now is None else now
    report = {
        "outcome": "inspected", "selected_date": selected_day.isoformat(),
        "encoded_bytes_read": 0, "records": 0, "decoded_records": 0,
        "invalid_records": 0, "timestamped_records": 0, "selected_date_records": 0,
        "label_record_counts": dict.fromkeys(LABELS, 0),
        "sdk_auth_url_records": 0, "sdk_auth_http_200_records": 0,
        "sdk_login_bundle_observed": False, "sdk_oauth_shape_observed": False,
        "jwt_occurrences": 0, "distinct_jwts": 0, "sdk_shaped_jwts": 0,
        "career_shaped_jwts": 0, "future_exp_claim_jwts": 0,
        "jwt_signatures_verified": False, "identity_verified": False,
        "career_access_verified": False, "network_requests_sent": False,
        "plaintext_saved": False, "token_values_output": False,
    }
    seen = set()
    while True:
        encoded = source.readline(MAX_ENCODED_RECORD_BYTES + 3)
        if not encoded:
            break
        report["encoded_bytes_read"] += len(encoded)
        report["records"] += 1
        if report["encoded_bytes_read"] > MAX_LOG_BYTES or report["records"] > MAX_RECORDS:
            raise InspectionError("log_limit_exceeded")
        if len(encoded.rstrip(b"\r\n")) > MAX_ENCODED_RECORD_BYTES:
            raise InspectionError("record_limit_exceeded")
        try:
            line = decode_record(encoded, parameter)
        except InspectionError:
            report["invalid_records"] += 1
            continue
        report["decoded_records"] += 1
        stamp = TIMESTAMP.search(line[:80])
        if not stamp:
            continue
        try:
            record_day = date(*(int(part) for part in stamp.group(1, 2, 3)))
        except ValueError:
            continue
        report["timestamped_records"] += 1
        if record_day != selected_day:
            continue
        report["selected_date_records"] += 1
        for label in LABELS:
            report["label_record_counts"][label] += int(label in line)
        report["sdk_auth_url_records"] += int(SDK_AUTH_URL in line)
        obj = first_json_object(line)
        if {"sdkuid", "gas_token", "sessionid", "gameid"} <= obj.keys():
            report["sdk_login_bundle_observed"] = True
        if {"access_token", "refresh_token", "oauth2", "chl_sdk_json"} <= obj.keys():
            report["sdk_oauth_shape_observed"] = True
        raw_info = obj.get("raw_info")
        if isinstance(raw_info, dict) and isinstance(raw_info.get("url"), str):
            if raw_info["url"].split("?", 1)[0] == SDK_AUTH_URL and raw_info.get("status_code") == 200:
                report["sdk_auth_http_200_records"] += 1
        for match in list(JWT.finditer(line))[:64]:
            report["jwt_occurrences"] += 1
            fingerprint = hashlib.sha256(match.group(0).encode("ascii")).digest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            try:
                payload = match.group(2)
                claims = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True))
            except (ValueError, RecursionError):
                continue
            if not isinstance(claims, dict):
                continue
            report["distinct_jwts"] += 1
            report["sdk_shaped_jwts"] += int({"exp", "gameid", "iat", "sub"} <= claims.keys())
            websocket = claims.get("websocket")
            report["career_shaped_jwts"] += int(
                "aid" in claims and isinstance(websocket, dict)
                and isinstance(websocket.get("addr"), str) and bool(websocket["addr"])
            )
            exp = claims.get("exp")
            if type(exp) in (int, float) and -1e15 < exp < 1e15 and math.isfinite(exp) and exp > now:
                report["future_exp_claim_jwts"] += 1
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="Decode the existing scoped SDK log transiently; print redacted counts only.")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Select log date (YYYY-MM-DD; default local today).")
    args = parser.parse_args(argv)
    try:
        log_path = default_log_path()
        if not args.inspect:
            report = {"outcome": "metadata_only", "sdk_log_exists": log_path.is_file(),
                      "sdk_dll_exists": DLL_PATH.is_file(), "plaintext_inspected": False,
                      "network_requests_sent": False, "plaintext_saved": False}
        else:
            parameter = load_log_parameter()
            with log_path.open("rb") as source:
                report = summarize_stream(source, parameter, args.date)
            report["plaintext_inspected"] = True
        print(json.dumps(report, indent=2))
        return 0
    except InspectionError as exc:
        print(json.dumps({"outcome": str(exc), "network_requests_sent": False, "plaintext_saved": False}))
    except (OSError, ImportError):
        print(json.dumps({"outcome": "local_input_or_dependency_unavailable", "network_requests_sent": False, "plaintext_saved": False}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
