"""Check a packaged cloud desktop using a disposable synthetic vault only.

By default no real vault, cloud credentials, AWS CLI, NetEase login, game or
Steam route is used. The unconfigured cloud fixture must fail refresh locally.
Requests are bounded localhost calls; assets are read, never executed. A normal
restart checks that saved credentials, notes and manual ranks remain unchanged.

Explicit --live-config and --live-uid together opt into a real profile refresh
after all offline checks. Only that encrypted DPAPI file is copied to the
synthetic fixture; the real vault is never opened or modified.

Only a unique fixture-bound listener receives shutdown. Processes are never
killed; failed fixtures remain for inspection. Successful temporary fixtures are
removed only after the explicitly launched child has stopped.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("career-live.jsx", "card-variants.jsx", "app.jsx", "styles.css", "data.js")
MAX_RESPONSE = 4 * 1024 * 1024
TEST_UID = "1370598422"
FIXTURE_PREFIX = "cloud-desktop-smoke-"
MARKER_KEY = "cloud_smoke_id"
CLOUD_CONFIG_FILE = "cloud-connection.dpapi"
DPAPI_PREFIX = bytes.fromhex("01000000d08c9ddf0115d1118c7a00c04fc297eb")


class SmokeError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SmokeError("redirect_refused")


class LocalHttp:
    def __init__(self, port):
        self.base = "http://127.0.0.1:" + str(port)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, body=None, *, timeout=3):
        if not path.startswith("/") or path.startswith("//"):
            raise SmokeError("invalid_local_path")
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise SmokeError("invalid_local_timeout")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data,
            headers={"Content-Type": "application/json", "Origin": self.base})
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE or response.geturl() != self.base + path:
                raise SmokeError("invalid_local_response")
            return response.code, raw

    def json(self, path, body=None, *, expected=200, timeout=3):
        code, raw = self.request(path, body, timeout=timeout)
        if code != expected:
            raise SmokeError("unexpected_http_status")
        value = json.loads(raw)
        if type(value) is not dict:
            raise SmokeError("unexpected_json_shape")
        return value


def require(value):
    if not value:
        raise SmokeError("smoke_assertion_failed")


def live_options(config, uid):
    if config is None and uid is None:
        return None
    if (config is None or type(uid) is not str
            or re.fullmatch(r"[1-9][0-9]{5,10}", uid) is None):
        raise SmokeError("live_options_require_config_and_numeric_uid")
    path = Path(config).absolute()
    if (path.suffix.lower() != ".dpapi" or path.is_symlink() or not path.is_file()
            or not len(DPAPI_PREFIX) < path.stat().st_size <= 16384):
        raise SmokeError("live_config_must_be_bounded_dpapi_file")
    return path


def copy_live_config(source, fixture):
    """Copy only bounded ciphertext after baseline checks, without decrypting it."""
    require(not source.is_symlink() and source.is_file())
    with source.open("rb") as stream:
        encrypted = stream.read(16385)
    require(len(DPAPI_PREFIX) < len(encrypted) <= 16384 and encrypted.startswith(DPAPI_PREFIX))
    destination = fixture / CLOUD_CONFIG_FILE
    with destination.open("xb") as stream:
        stream.write(encrypted)
    encrypted = None


def live_summary(card, uid, fields, started):
    """Validate public card facts, then return an explicit, bounded report only."""
    require(type(card) is dict and card.get("rivals_uid") == uid
        and card.get("last_refresh_status") == "ok" and card.get("last_refresh_source") == "netease"
        and card.get("last_refresh_code") is None and card.get("stats_provider") == "netease")
    require(all(card.get(name) == value for name, value in fields.items() if name != "in_game_name"))
    wrapper = card.get("netease_native")
    require(type(wrapper) is dict)
    profile, rank = wrapper.get("profile"), wrapper.get("rank_display")
    require(type(profile) is dict and profile.get("uid") == uid and profile.get("platform") == "pc"
        and profile.get("identity_verified") is True and profile.get("owner_authentication_verified") is True
        and profile.get("game_login_verified") is True and profile.get("social_session_accepted") is True)
    identity = profile.get("identity")
    require(type(identity) is dict and identity.get("uid") == uid and identity.get("platform") == "pc")
    name = identity.get("in_game_name")
    require(type(name) is str and 0 < len(name.strip()) <= 64 and len(name) <= 64
        and not any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in name)
        and card.get("in_game_name") == name)
    fetched = wrapper.get("fetched_at")
    require(type(fetched) is int and started - 300 <= fetched <= time.time() + 300)
    require(type(card.get("last_refresh_ts")) is int and started - 1 <= card["last_refresh_ts"] <= time.time() + 1)
    require(type(rank) is dict and rank.get("uid") == uid
        and rank.get("status") in ("unknown", "estimated", "ranked")
        and rank.get("placements_total") == 10)
    placements = rank.get("placements_completed")
    require(placements is None or type(placements) is int and 0 <= placements <= 10)
    level = rank.get("level")
    require(level is None or type(level) is int and 0 <= level <= 65535)
    result = {"uid": uid, "in_game_name": name, "fetched_at": fetched,
        "rank_level": level, "placement_status": rank["status"],
        "placements_completed": placements, "placements_total": 10}
    for section_name, count_name, row_name, limit in (
            ("ranks", "season_count", "seasons", 100), ("matches", "match_count", "matches", 50)):
        section = profile.get(section_name)
        require(type(section) is dict and section.get("status") in ("available", "unavailable", "restricted", "unknown"))
        count = None
        if section["status"] == "available":
            data = section.get("data")
            require(profile.get(section_name + "_verified") is True and type(data) is dict and data.get("uid") == uid)
            rows, count = data.get(row_name), data.get(count_name)
            require(type(rows) is list and type(count) is int and 0 <= count <= limit and len(rows) == count)
        result[count_name] = count
    career = profile.get("career")
    require(type(career) is dict and career.get("status") in ("available", "unavailable", "restricted", "unknown"))
    result["career_matches"] = None
    if career["status"] == "available":
        data = career.get("data")
        require(profile.get("career_verified") is True and type(data) is dict and data.get("uid") == uid)
        stats = data.get("statistics")
        require(type(stats) is dict and type(stats.get("matches")) is int and 0 <= stats["matches"] <= 10**12)
        result["career_matches"] = stats["matches"]
    return result


def fixture_owned(http, marker):
    try:
        value = http.json("/api/status")
        return (value.get("stats_provider") == "netease" and type(value.get("ui_options")) is dict
                and value["ui_options"].get(MARKER_KEY) == marker)
    except Exception:
        return False


def child_environment(fixture):
    # Strip optional connection/operator environment inputs as well as setting
    # a completely separate data root. The executable receives no bearer.
    environment = {key: value for key, value in os.environ.items()
        if not key.upper().startswith(("NETEASE_", "MARVEL_", "AWS_"))}
    environment.update(MARVEL_KEEPER_DATA=str(fixture), USERPROFILE=str(fixture), HOME=str(fixture),
                       APPDATA=str(fixture / "AppData" / "Roaming"),
                       LOCALAPPDATA=str(fixture / "AppData" / "Local"))
    return environment


def wait_for_fixture(process, http, marker):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        require(process.poll() is None)
        if fixture_owned(http, marker):
            return
        time.sleep(0.2)
    raise SmokeError("startup_deadline")


def stop_fixture(process, http, marker):
    require(fixture_owned(http, marker))
    require(http.json("/api/shutdown", {}).get("ok") is True)
    process.wait(timeout=20)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", help="Explicit path to the newly packaged cloud desktop EXE.")
    parser.add_argument("--leave-open", action="store_true",
        help="Keep only the final headless child and synthetic fixture for separate inspection.")
    parser.add_argument("--live-config", help="Explicit encrypted .dpapi connection file; requires --live-uid.")
    parser.add_argument("--live-uid", help="Numeric PC UID for opt-in real cloud acceptance after offline checks.")
    args = parser.parse_args(argv)
    try:
        live_config = live_options(args.live_config, args.live_uid)
    except SmokeError as error:
        parser.error(str(error))
    executable = Path(args.executable).absolute()
    if os.name != "nt" or executable.is_symlink() or not executable.is_file() or executable.suffix.lower() != ".exe":
        parser.error("Choose a regular packaged cloud desktop EXE on Windows.")

    temp_parent = Path(tempfile.gettempdir()).resolve()
    fixture = Path(tempfile.mkdtemp(prefix=FIXTURE_PREFIX, dir=temp_parent)).resolve()
    marker = secrets.token_hex(32)
    # The seed blocks any adjacent-executable vault migration before launch.
    seed = {"initialized": False, "accounts": [],
            "config": {"lockout_minutes": 30, "ui_options": {MARKER_KEY: marker}}}
    (fixture / "vault.json").write_text(json.dumps(seed), encoding="utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    http = LocalHttp(port)
    process = None
    phase = "launch"
    report = {"outcome": "cloud_desktop_smoke_failed", "executable": str(executable),
        "fixture": str(fixture), "port": port, "parent_pids": [], "headless": True,
        "synthetic_vault_only": True, "checks": {}, "left_open": False,
        "graceful_shutdown": False, "fixture_removed": False, "live_requested": live_config is not None}
    succeeded = False

    def launch():
        child = subprocess.Popen([str(executable), "--no-browser", "--port", str(port)],
            cwd=fixture, env=child_environment(fixture), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        report["parent_pids"].append(child.pid)
        return child

    try:
        process = launch()
        phase = "startup"
        wait_for_fixture(process, http, marker)
        status = http.json("/api/status")
        require(status.get("initialized") is False and status.get("unlocked") is False
                and status.get("account_count") == 0)
        report["checks"]["fixture_bound_cloud_startup"] = True

        phase = "initialize"
        password = "Synthetic-cloud-smoke-" + secrets.token_urlsafe(32)
        initialized = http.json("/api/init", {"password": password, "remember": False})
        require(initialized.get("ok") is True and initialized.get("remembered") is False)
        http.json("/api/options", {"ui_options": {MARKER_KEY: marker}})
        require(fixture_owned(http, marker))
        status = http.json("/api/status")
        require(status.get("unlocked") is True and status.get("initialized") is True)
        report["checks"]["normal_init_unlocked"] = True

        phase = "cloud_status"
        career = http.json("/api/career/status")
        require(career.get("provider") == "cloud" and career.get("configured") is False
                and career.get("connected") is False and career.get("state") == "unconfigured")
        report["checks"]["cloud_unconfigured_status"] = True

        phase = "update_channel"
        update = http.json("/api/check-update")
        require(update.get("channel") == "cloud-preview" and update.get("has_update") is False
                and update.get("packaged") is True and update.get("latest") is None)
        rejected = http.json("/api/apply-update", {}, expected=409)
        require(rejected.get("error") == "cloud_preview_channel")
        report["checks"]["standard_update_disabled"] = True

        phase = "add_uid"
        account_password = "Synthetic-account-only-" + secrets.token_urlsafe(24)
        fields = {"rivals_uid": TEST_UID, "rivals_platform": "pc", "in_game_name": "",
            "username": "synthetic-user", "email": "synthetic@example.invalid", "password": account_password,
            "current_rank": "Gold II", "peak_rank": "Platinum III", "notes": "Synthetic migration check"}
        added = http.json("/api/accounts", {**fields, "refresh_stats": True})
        card = added.get("account")
        require(added.get("ok") is True and type(card) is dict
                and card.get("stats_provider") == "netease"
                and card.get("last_refresh_source") == "netease"
                and card.get("last_refresh_code") == "cloud_not_configured"
                and card.get("last_refresh_status") == "error"
                and all(card.get(name) == value for name, value in fields.items()))
        require(account_password not in (fixture / "vault.json").read_text(encoding="utf-8"))
        require(not (fixture / "netease-session.sqlite3").exists()
                and not (fixture / "remembered_key.bin").exists())
        report["checks"]["unconfigured_add_preserves_card_and_encrypts_password"] = True

        phase = "bundled_assets"
        code, html = http.request("/")
        require(code == 200 and b"career-live.jsx" in html and b"card-variants.jsx" in html)
        for name in ASSETS:
            code, data = http.request("/static/" + name)
            require(code == 200 and hashlib.sha256(data).digest()
                    == hashlib.sha256((ROOT / "static" / name).read_bytes()).digest())
        report["checks"]["bundled_assets_match_source"] = True

        phase = "restart"
        stop_fixture(process, http, marker)
        process = launch()
        wait_for_fixture(process, http, marker)
        status = http.json("/api/status")
        require(status.get("initialized") is True and status.get("unlocked") is False
                and status.get("account_count") == 1)
        unlocked = http.json("/api/unlock", {"password": password, "remember": False})
        password = None
        require(unlocked.get("ok") is True)
        listed = http.json("/api/accounts")
        accounts = listed.get("accounts")
        require(type(accounts) is list and len(accounts) == 1
                and accounts[0].get("id") == card.get("id")
                and all(accounts[0].get(name) == value for name, value in fields.items()))
        report["checks"]["restart_preserves_card_credentials_notes_and_manual_ranks"] = True
        if live_config is not None:
            phase = "live_configuration"
            require(fixture_owned(http, marker))
            copy_live_config(live_config, fixture)
            career = http.json("/api/career/status")
            require(career.get("provider") == "cloud" and career.get("configured") is True)
            phase = "live_refresh"
            live_fields = {**fields, "rivals_uid": args.live_uid, "username": "synthetic-live-user"}
            created = http.json("/api/accounts", {**live_fields, "refresh_stats": False})
            account_id = created.get("id")
            require(created.get("ok") is True and type(account_id) is str and re.fullmatch(r"[0-9a-f]{32}", account_id))
            started = time.time()
            refreshed = http.json("/api/accounts/" + account_id + "/refresh-stats", {}, timeout=120)
            require(refreshed.get("ok") is True)
            report["live"] = live_summary(refreshed.get("account"), args.live_uid, live_fields, started)
            require(account_password not in (fixture / "vault.json").read_text(encoding="utf-8"))
            report["checks"]["live_cloud_refresh_preserves_synthetic_private_fields"] = True
            live_fields = created = refreshed = None
        account_password = fields = card = accounts = listed = None
        succeeded = True
        report["outcome"] = "cloud_desktop_smoke_verified"
    except Exception:
        # Fixed phase only: never emit exception/HTTP bodies, vault passwords,
        # saved synthetic credentials, arbitrary provider values or account rows.
        report["failed_phase"] = phase
    finally:
        if process is not None and process.poll() is None:
            if args.leave_open and fixture_owned(http, marker):
                report["left_open"] = True
            else:
                try:
                    stop_fixture(process, http, marker)
                    report["graceful_shutdown"] = True
                except Exception:
                    report["cleanup_incomplete"] = True
                    report["outcome"] = "cloud_desktop_smoke_cleanup_incomplete"
        stopped = process is None or process.poll() is not None
        if succeeded and stopped and not report.get("cleanup_incomplete"):
            try:
                require(fixture.parent == temp_parent and fixture.name.startswith(FIXTURE_PREFIX)
                        and not fixture.is_symlink())
                shutil.rmtree(fixture)
                report["fixture_removed"] = True
            except Exception:
                report["cleanup_incomplete"] = True
                report["outcome"] = "cloud_desktop_smoke_cleanup_incomplete"
        print(json.dumps(report, indent=2))
    return 0 if succeeded and not report.get("cleanup_incomplete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
