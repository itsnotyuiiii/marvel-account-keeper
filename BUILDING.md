# Building Marvel Rivals Account Tracker

How to produce the standalone executable yourself — locally, without GitHub
Actions. The CI workflow (`.github/workflows/release.yml`) does exactly these
steps on a clean runner for each OS.

## Prerequisites

- **Python 3.12** (3.10+ should work) on the OS you want to build for.
  PyInstaller is not a cross-compiler — build the Windows `.exe` on Windows,
  the macOS binary on macOS, the Linux binary on Linux.
- A C toolchain is **not** required; all dependencies ship wheels.

## One-time setup

```sh
# from the project root
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

`requirements-dev.txt` pulls in the runtime deps (Flask, cryptography) plus
PyInstaller.

## Build

```sh
python build.py
```

This wraps PyInstaller with the right flags:

- `--onefile` — one self-contained executable
- `--console` — keeps the small terminal window (it shows the local URL)
- `--add-data` — bundles `templates/` and `static/` (HTML, JS, CSS, and the
  vendored React/Babel in `static/vendor/`) inside the binary
- `--icon icon.ico` — Windows only

Output:

| OS      | File                          |
|---------|-------------------------------|
| Windows | `dist/MarvelRivalsAccountTracker.exe` |
| macOS   | `dist/MarvelRivalsAccountTracker`     |
| Linux   | `dist/MarvelRivalsAccountTracker`     |

The build is clean each time — `build.py` deletes `build/`, `dist/` and the
generated `.spec` before running.

## Test the build

Just run the produced file:

```sh
# Windows
dist\MarvelRivalsAccountTracker.exe
# macOS / Linux
./dist/MarvelRivalsAccountTracker
```

It should print a banner with a `http://127.0.0.1:<port>` URL and open your
browser. The vault is read from / written to the per-user data directory
(see the README), **not** the project folder — so building and testing never
touches your development `vault.json`.

## The icon

`icon.ico` is committed and consumed directly by the build. To change the
artwork, edit and re-run `make_icon.py` (needs Pillow: `pip install pillow`),
which regenerates `icon.ico` + `icon.png`.

macOS uses `.icns`, not `.ico`, so the macOS binary currently builds without a
custom icon. To add one: create `icon.icns` and pass `--icon icon.icns` in
`build.py` for `sys.platform == "darwin"`.

## Cutting a release

Releases are produced by CI, not by hand:

```sh
git tag v1.2.3
git push origin v1.2.3
```

Pushing a `v*` tag triggers `.github/workflows/release.yml`, which builds all
three OS binaries and attaches them to a GitHub Release for that tag. You can
also trigger a build (without publishing a release) from the **Actions** tab
via *Run workflow*.
