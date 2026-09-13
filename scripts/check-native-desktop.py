"""Headless packaged-desktop HTTP smoke with an isolated synthetic vault.

Explicit EXE only; no app import, GUI automation, sign-in, game, Steam route,
SDK request or external HTTP. --no-browser avoids GUI font/Steam polling. The
native-window login callback is therefore expected to be unavailable here.
--leave-open retains this headless child and fixture for separate inspection;
opening a browser later is outside this localhost-only smoke's checks.

The default requests normal /api/shutdown only after a unique fixture marker
binds the listener. It never kills a process. Cleanup cannot remove the fixture
while the launched process is still alive; failures retain it for inspection.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
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


class SmokeError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SmokeError("redirect_refused")


class LocalHttp:
    def __init__(self, port):
        self.base = "http://127.0.0.1:" + str(port)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, body=None):
        if not path.startswith("/") or path.startswith("//"):
            raise SmokeError("invalid_local_path")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data,
            headers={"Content-Type": "application/json", "Origin": self.base})
        try:
            response = self.opener.open(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE or response.geturl() != self.base + path:
                raise SmokeError("invalid_local_response")
            return response.code, raw

    def json(self, path, body=None, *, expected=200):
        code, raw = self.request(path, body)
        if code != expected:
            raise SmokeError("unexpected_http_status")
        value = json.loads(raw)
        if type(value) is not dict:
            raise SmokeError("unexpected_json_shape")
        return value


def require(value):
    if not value:
        raise SmokeError("smoke_assertion_failed")


def fixture_owned(http, marker):
    try:
        value = http.json("/api/status")
        return (value.get("stats_provider") == "netease" and type(value.get("ui_options")) is dict
                and value["ui_options"].get("native_smoke_id") == marker)
    except Exception:
        return False


def child_environment(fixture):
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("NETEASE_")}
    # Path.home controls the secondary Documents backup directory. APPDATA/
    # LOCALAPPDATA are also isolated; never use the operator's vault/SDK paths.
    environment.update(MARVEL_KEEPER_DATA=str(fixture), USERPROFILE=str(fixture), HOME=str(fixture),
                       APPDATA=str(fixture / "AppData" / "Roaming"),
                       LOCALAPPDATA=str(fixture / "AppData" / "Local"))
    return environment


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", help="Explicit path to the newly packaged native desktop EXE.")
    parser.add_argument("--leave-open", action="store_true", help="Keep only this headless process and synthetic fixture for separate inspection.")
    args = parser.parse_args(argv)
    executable = Path(args.executable).absolute()
    if os.name != "nt" or executable.is_symlink() or not executable.is_file() or executable.suffix.lower() != ".exe":
        parser.error("Choose a regular packaged native desktop EXE on Windows.")

    temp_parent = Path(tempfile.gettempdir()).resolve()
    fixture = Path(tempfile.mkdtemp(prefix="native-desktop-smoke-", dir=temp_parent)).resolve()
    marker = secrets.token_hex(32)
    # Existence blocks migration from a vault beside the executable BEFORE the
    # app starts. This marker also binds our localhost mutations to this fixture.
    seed = {"initialized": False, "accounts": [],
            "config": {"lockout_minutes": 30, "ui_options": {"native_smoke_id": marker}}}
    (fixture / "vault.json").write_text(json.dumps(seed), encoding="utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    http = LocalHttp(port)
    process = None
    phase = "launch"
    report = {"outcome": "native_desktop_smoke_failed", "executable": str(executable),
        "fixture": str(fixture), "port": port, "parent_pid": None,
        "headless": True, "synthetic_vault_only": True, "checks": {},
        "left_open": False, "graceful_shutdown": False, "fixture_removed": False}
    succeeded = False
    try:
        process = subprocess.Popen([str(executable), "--no-browser", "--port", str(port)],
            cwd=fixture, env=child_environment(fixture), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        report["parent_pid"] = process.pid
        phase = "startup"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            require(process.poll() is None)
            if fixture_owned(http, marker):
                break
            time.sleep(0.2)
        else:
            raise SmokeError("startup_deadline")
        status = http.json("/api/status")
        require(status.get("initialized") is False and status.get("unlocked") is False
                and status.get("account_count") == 0)
        report["checks"]["fixture_bound_native_startup"] = True

        phase = "initialize"
        password = "Synthetic-native-smoke-" + secrets.token_urlsafe(32)
        initialized = http.json("/api/init", {"password": password, "remember": False})
        password = None  # Never printed, returned, saved plaintext or remembered.
        require(initialized.get("ok") is True and initialized.get("remembered") is False)
        # Init deliberately replaces the seed config; reinstall the harmless
        # marker through the normal options route for later cleanup binding.
        http.json("/api/options", {"ui_options": {"native_smoke_id": marker}})
        require(fixture_owned(http, marker))
        status = http.json("/api/status")
        require(status.get("unlocked") is True and status.get("initialized") is True)
        report["checks"]["normal_init_unlocked"] = True

        phase = "native_status"
        career = http.json("/api/career/status")
        require(career.get("provider") == "native" and career.get("configured") is True
                and career.get("can_connect") is False and career.get("connected") is False
                and career.get("state") == "unconfigured")
        report["checks"]["native_unconfigured_headless_status"] = True

        phase = "update_channel"
        update = http.json("/api/check-update")
        require(update.get("channel") == "native-preview" and update.get("has_update") is False
                and update.get("packaged") is True and update.get("latest") is None)
        # Known native-preview route rejects before the release-fetch/update path.
        rejected = http.json("/api/apply-update", {}, expected=409)
        require(rejected.get("error") == "native_preview_channel")
        report["checks"]["standard_update_disabled"] = True

        phase = "add_uid"
        added = http.json("/api/accounts", {"rivals_uid": TEST_UID, "rivals_platform": "pc",
            "in_game_name": "", "username": "", "email": "", "password": "", "refresh_stats": True})
        card = added.get("account")
        require(added.get("ok") is True and type(card) is dict
                and card.get("rivals_uid") == TEST_UID and card.get("stats_provider") == "netease"
                and card.get("last_refresh_source") == "netease"
                and card.get("last_refresh_code") == "career_not_configured"
                and card.get("last_refresh_status") == "error" and card.get("password") == "")
        listed = http.json("/api/accounts")
        require(type(listed.get("accounts")) is list and len(listed["accounts"]) == 1
                and listed["accounts"][0].get("id") == card.get("id"))
        require(not (fixture / "netease-session.sqlite3").exists()
                and not (fixture / "remembered_key.bin").exists())
        report["checks"]["unconfigured_add_saves_card_without_session"] = True

        phase = "bundled_assets"
        code, html = http.request("/")
        require(code == 200 and b"career-live.jsx" in html and b"card-variants.jsx" in html)
        # Read bytes only: this does not execute HTML/JS or request its fonts.
        for name in ASSETS:
            code, data = http.request("/static/" + name)
            require(code == 200 and hashlib.sha256(data).digest()
                    == hashlib.sha256((ROOT / "static" / name).read_bytes()).digest())
        report["checks"]["bundled_assets_match_source"] = True
        succeeded = True
        report["outcome"] = "native_desktop_smoke_verified"
    except Exception:
        # Never emit exception/HTTP bodies, fixture passwords, account rows or
        # arbitrary service values. Phase is one of the fixed literals above.
        report["failed_phase"] = phase
    finally:
        if process is not None and process.poll() is None:
            if args.leave_open and fixture_owned(http, marker):
                report["left_open"] = True
            else:
                try:
                    require(fixture_owned(http, marker))
                    require(http.json("/api/shutdown", {}).get("ok") is True)
                    process.wait(timeout=20)
                    report["graceful_shutdown"] = True
                except Exception:
                    report["cleanup_incomplete"] = True
                    report["outcome"] = "native_desktop_smoke_cleanup_incomplete"
        # No broad paths or cleanup while our one-file bootloader is alive.
        # Failed smoke fixtures stay available; they contain synthetic data only.
        stopped = process is None or process.poll() is not None
        if succeeded and stopped and not report.get("cleanup_incomplete"):
            try:
                require(fixture.parent == temp_parent and fixture.name.startswith("native-desktop-smoke-")
                        and not fixture.is_symlink())
                shutil.rmtree(fixture)
                report["fixture_removed"] = True
            except Exception:
                report["cleanup_incomplete"] = True
                report["outcome"] = "native_desktop_smoke_cleanup_incomplete"
        print(json.dumps(report, indent=2))
    return 0 if succeeded and not report.get("cleanup_incomplete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
