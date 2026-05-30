"""RETIRED — the app icon is now hand-designed artwork, not procedurally drawn.

The current icon lives in the repo as `icon.ico` (multi-resolution, consumed by
build.py), `icon.png` (256px), and `icon.svg` (vector source of truth). This
script used to draw a duck mascot and write icon.ico/icon.png; running it now
would CLOBBER the real icon, so it has been gutted to a no-op.

To change the icon: edit `icon.svg`, then export `icon.ico` (16/24/32/48/64/
128/256) and a 256px `icon.png` from it with your vector tool, and replace the
committed files.
"""
from __future__ import annotations

import sys


def main() -> None:
    sys.exit(
        "make_icon.py is retired — the icon is hand-designed (see icon.svg).\n"
        "Running it would overwrite icon.ico/icon.png. Edit icon.svg and "
        "re-export those two files instead."
    )


if __name__ == "__main__":
    main()
