"""Test the packaged app with a fresh synthetic vault, not the user's vault.

The --live option makes the panel's three own-account reads. --live-card tests
one-action Add for the session owner; --live-player tests an explicit public UID
or IGN without a per-target login. Card results stay in a synthetic vault.
No capture/token/raw response is copied into the test vault. Scratch stays under
build/ for review; the exact child process is always stopped on completion.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable")
    parser.add_argument("trace")
    parser.add_argument("archive")
    parser.add_argument("--own-uid", required=True)
    live = parser.add_mutually_exclusive_group()
    live.add_argument("--live", action="store_true")
    live.add_argument("--live-card", action="store_true")
    live.add_argument("--live-player", help="One explicitly selected public UID or exact IGN")
    parser.add_argument("--expect-uid", help="Independently known UID for --live-player verification")
    parser.add_argument("--expect-career", action="store_true", help="Require verified detailed PC Career fields, not rank-only success")
    parser.add_argument("--check-refresh", action="store_true", help="After Add, verify normal and streaming bulk refresh across real cooldowns")
    args = parser.parse_args()
    if args.live_player and (not args.expect_uid or not args.expect_uid.isascii() or not args.expect_uid.isdigit()):
        parser.error("--live-player requires --expect-uid with the independently known target UID.")
    if (args.expect_career or args.check_refresh) and not (args.live_card or args.live_player):
        parser.error("Career/refresh assertions require --live-card or --live-player.")
    temp_root = ROOT / "build"
    temp_root.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="career-smoke-", dir=temp_root))
    (scratch / "vault.json").write_text(json.dumps({"initialized": False, "config": {"lockout_minutes": 30}, "accounts": []}), encoding="utf-8")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [args.executable, "--no-browser", "--port", str(port), "--career-capture", args.trace,
               "--career-archive", args.archive, "--career-own-uid", args.own_uid]
    # Path.home() controls the app's secondary Documents backup location. Give
    # ONLY this child a synthetic home so smoke tests cannot prune real backups.
    process = subprocess.Popen(command, env=dict(os.environ, MARVEL_KEEPER_DATA=str(scratch), USERPROFILE=str(scratch)),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    base = "http://127.0.0.1:" + str(port)
    def api(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "Origin": base})
        with urllib.request.urlopen(req, timeout=90) as response:
            return json.load(response)
    report = {"packaged_startup": False, "synthetic_vault_only": True,
              "live_requested": bool(args.live or args.live_card or args.live_player), "credentials_exported": False}
    try:
        for _ in range(50):
            if process.poll() is not None:
                raise ValueError("Packaged process exited before startup")
            try:
                api("/api/status")
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        else:
            raise ValueError("Packaged app startup deadline reached")
        report["packaged_startup"] = True
        if api("/api/status").get("stats_provider") != "netease":
            raise ValueError("The package did not enable NetEase-only mode")
        report["netease_only_mode"] = True
        api("/api/init", {"password": "Synthetic-test-" + secrets.token_hex(16), "remember": False})
        status = api("/api/career/status")
        if status.get("uid") != args.own_uid:
            raise ValueError("Packaged capture/session validation failed")
        report["packaged_capture_decode"] = True
        for name in ("career-live.jsx", "card-variants.jsx", "app.jsx"):
            with urllib.request.urlopen(base + "/static/" + name) as asset:
                if hashlib.sha256(asset.read()).digest() != hashlib.sha256((ROOT / "static" / name).read_bytes()).digest():
                    raise ValueError("Bundled UI is not current")
        report["bundled_ui_matches_source"] = True
        if args.live:
            snapshot = api("/api/career/refresh", {"season": status["season"]})
            report.update(live_own_uid_matches=snapshot.get("uid") == args.own_uid, season=snapshot.get("season"),
                          profile=snapshot.get("profile"), match_count=len(snapshot.get("recent_matches", [])),
                          ranks_received=snapshot.get("ranks") is not None)
        if args.live_card or args.live_player:
            target = args.live_player or args.own_uid
            expected_uid = args.expect_uid if args.live_player else args.own_uid
            numeric = target.isascii() and target.isdigit()
            refreshed = api("/api/accounts", {"in_game_name": "" if numeric else target,
                "rivals_uid": target if numeric else None, "rivals_platform": "unknown",
                "peak_rank": "Bronze III", "peak_points": 3000, "refresh_stats": True})["account"]
            report["game_career_status"] = api("/api/career/status").get("game_career")
            if refreshed.get("last_refresh_status") != "ok" or refreshed.get("last_refresh_source") != "netease":
                raise ValueError("Packaged card refresh did not succeed")
            if refreshed.get("rivals_uid") != expected_uid or not refreshed.get("in_game_name") or refreshed.get("rivals_platform") == "unknown":
                raise ValueError("Packaged player identity was not verified")
            report.update(card_refresh_verified=True, one_action_add_verified=True, target_uid=refreshed["rivals_uid"],
                player_name=refreshed["in_game_name"], platform=refreshed["rivals_platform"],
                current_rank=refreshed["current_rank"], current_points=refreshed["current_points"],
                peak_rank=refreshed["peak_rank"], peak_points=refreshed["peak_points"], rank_metadata=refreshed["netease_rank"],
                profile=refreshed["netease_profile"], profile_note=refreshed.get("netease_profile_note"))
            if args.live_card and not isinstance(refreshed.get("netease_profile"), dict):
                raise ValueError("Own-card Career stats were not returned")
            if args.live_player and not refreshed.get("netease_profile") and not refreshed.get("netease_profile_note"):
                raise ValueError("Missing detailed stats were not explained")
            if args.expect_career:
                profile = refreshed.get("netease_profile")
                if not isinstance(profile, dict) or refreshed.get("netease_profile_note"):
                    raise ValueError("Verified detailed Career stats were required")
                if any(type(profile.get(field)) is not int for field in ("matches", "wins", "kills", "deaths", "assists")):
                    raise ValueError("Detailed Career fields were missing")
                report["detailed_career_verified"] = True
            if args.check_refresh:
                for mode in ("normal", "bulk"):
                    previous = refreshed["netease_profile"]["fetched_at"]
                    # Honor the real per-card/server cooldown and allow the
                    # idle game session to retire before verifying reconnection.
                    print(json.dumps({"verification_phase": mode + "_refresh_cooldown"}), flush=True)
                    time.sleep(31)
                    if mode == "normal":
                        refreshed = api("/api/accounts/" + refreshed["id"] + "/refresh-stats", {})["account"]
                    else:
                        req = urllib.request.Request(base + "/api/accounts/refresh-all/stream", data=b"{}",
                            headers={"Content-Type": "application/json", "Origin": base})
                        with urllib.request.urlopen(req, timeout=90) as response:
                            events = [json.loads(line) for line in response.read(1024 * 1024).splitlines() if line.strip()]
                        terminal = [event for event in events if event.get("type") == "summary"]
                        if len(terminal) != 1 or terminal[0]["summary"]["ok"] != 1:
                            raise ValueError("Streaming bulk refresh failed")
                        refreshed = terminal[0]["accounts"][0]
                    if (refreshed.get("rivals_uid") != expected_uid or refreshed.get("last_refresh_status") != "ok"
                            or refreshed.get("netease_profile_note") or not isinstance(refreshed.get("netease_profile"), dict)
                            or refreshed["netease_profile"].get("fetched_at", 0) <= previous):
                        raise ValueError("Refresh did not return newly verified Career stats")
                    report[mode + "_refresh_verified"] = True
                report.update(profile=refreshed["netease_profile"], rank_metadata=refreshed["netease_rank"],
                    current_rank=refreshed["current_rank"], current_points=refreshed["current_points"],
                    peak_rank=refreshed["peak_rank"], peak_points=refreshed["peak_points"])
        # Only the synthetic verifier/config may have been written here.
        vault = json.loads((scratch / "vault.json").read_text(encoding="utf-8"))
        report["no_accounts_written"] = vault["accounts"] == []
        if args.live_card or args.live_player:
            if len(vault["accounts"]) != 1:
                raise ValueError("Expected exactly one synthetic saved card")
            saved = vault["accounts"][0]
            if (saved.get("current_rank") != report["current_rank"] or saved.get("netease_profile") != report["profile"] or
                    saved.get("netease_rank") != report["rank_metadata"] or
                    saved.get("rivals_uid") != report["target_uid"] or saved.get("in_game_name") != report["player_name"]):
                raise ValueError("Projected card stats were not saved correctly")
            if any(field in json.dumps(saved) for field in ("rank_game_season", "str_payload", "_netease_identity", '"token"')):
                raise ValueError("Unprojected response field was saved")
            report["projected_card_persistence_verified"] = True
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        print(json.dumps({**report, "outcome": "packaged_smoke_failed"}))
        return 1
    finally:
        if process.poll() is None:
            # A one-file Windows build has a bootloader parent and app child.
            # Ask the owned app to exit normally; killing only its parent can
            # leave the child's local server running.
            try:
                api("/api/shutdown", {})
                process.wait(timeout=15)
            except (OSError, urllib.error.URLError, subprocess.TimeoutExpired):
                print(json.dumps({"cleanup_incomplete": True, "test_parent_pid": process.pid, "local_test_port": port}))
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
