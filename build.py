"""Build a single-file executable for Marvel Account Keeper.

    python build.py

Produces dist/MarvelAccountKeeper(.exe) with PyInstaller. The artifact is
native to whichever OS you run this on (Windows .exe, macOS/Linux binary).
See BUILDING.md for the full walkthrough.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
NAME = "MarvelAccountKeeper"


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

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",                              # single self-contained file
        "--windowed",                            # no terminal window on Windows/macOS
        "--name", NAME,
        "--add-data", f"templates{sep}templates",  # Flask templates
        "--add-data", f"static{sep}static",        # JS / CSS / vendored libs
        "--noconfirm",
        "--clean",
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
