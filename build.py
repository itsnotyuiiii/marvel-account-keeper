"""Build a single-file executable for Marvel Rivals Account Tracker.

    python build.py

Produces dist/MarvelRivalsAccountTracker(.exe) with PyInstaller. The artifact is
native to whichever OS you run this on (Windows .exe, macOS/Linux binary).
See BUILDING.md for the full walkthrough.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
NAME = "MarvelRivalsAccountTracker"
BUILD_INFO_PATH = ROOT / "_build_info.json"


def _git_short_sha() -> str:
    """Read the current commit SHA so the build can stamp it into the binary.
    CI checks out the tagged commit before building, so this matches the
    release tag. Falls back to an env var (GITHUB_SHA) or "unknown" when
    `git` isn't available."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT, stderr=subprocess.DEVNULL,
        ).decode("utf-8").strip()
        if out:
            return out
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    env_sha = (os.environ.get("GITHUB_SHA") or "").strip()
    return env_sha[:12] if env_sha else "unknown"


def _write_build_info() -> Path:
    """Drop a tiny JSON file into the source tree that PyInstaller will
    bundle next to the code. app.py reads it at startup to display the
    running version + commit in the footer."""
    info = {
        "commit": _git_short_sha(),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    BUILD_INFO_PATH.write_text(json.dumps(info, indent=2), encoding="utf-8")
    return BUILD_INFO_PATH


def main() -> None:
    sep = ";" if os.name == "nt" else ":"   # PyInstaller --add-data separator
    icon = ROOT / "icon.ico"

    # Clean prior output so a stale binary can't masquerade as a fresh build.
    for d in ("build", "dist"):
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)
    spec = ROOT / f"{NAME}.spec"
    if spec.exists():
        spec.unlink()

    _write_build_info()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",                              # single self-contained file
        "--windowed",                            # no terminal window on Windows/macOS
        "--name", NAME,
        "--add-data", f"templates{sep}templates",  # Flask templates
        "--add-data", f"static{sep}static",        # JS / CSS / vendored libs
        "--add-data", f"_build_info.json{sep}.",   # commit SHA + build timestamp
        # pywebview (native window) + its data/backends. PyInstaller's stock
        # hooks miss some pieces, so pull the whole package in.
        "--collect-all", "webview",
        "--noconfirm",
        "--clean",
    ]
    # The Windows native backend (EdgeChromium) loads through pythonnet/clr,
    # which needs its managed runtime + metadata bundled explicitly. Harmless
    # to omit elsewhere (mac/Linux use Cocoa/GTK backends instead).
    if sys.platform == "win32":
        cmd += [
            "--collect-all", "clr_loader",
            "--collect-all", "pythonnet",
            "--copy-metadata", "pythonnet",
        ]
    # .ico is a Windows resource format; macOS would need an .icns. Only wire
    # the icon up where it applies so the build doesn't fail elsewhere.
    if icon.exists() and sys.platform == "win32":
        cmd += ["--icon", str(icon)]
    cmd.append(str(ROOT / "app.py"))

    print("running:", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)

    out = ROOT / "dist" / (NAME + (".exe" if os.name == "nt" else ""))
    if not out.exists():
        sys.exit(f"build finished but {out} is missing")
    print(f"\n  Built: {out}  ({out.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
