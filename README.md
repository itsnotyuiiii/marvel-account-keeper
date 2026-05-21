# Marvel Account Keeper

A small, local desktop app for tracking your Marvel Rivals / Steam accounts —
in-game name, username, email, password, and current / peak rank. It runs
entirely on your machine; nothing is sent anywhere.

![icon](icon.png)

## Download & run

1. Go to the [**Releases**](https://github.com/itsnotyuiiii/marvel-account-keeper/releases)
   page and download the file for your operating system:

   | Your OS | File |
   |---------|------|
   | Windows | `MarvelAccountKeeper-windows.exe` |
   | macOS   | `MarvelAccountKeeper-macos` |
   | Linux   | `MarvelAccountKeeper-linux` |

2. **Double-click it.** Your default browser loads the app automatically (a
   small terminal window also opens — you can ignore it). When you're done,
   just **close the browser**: the app shuts itself down a couple of minutes
   later, leaving no process running. No arguments or setup needed.

   - **macOS / Linux:** the download may need the executable bit first —
     `chmod +x MarvelAccountKeeper-macos` — then run it. On macOS you may also
     need to allow it under *System Settings → Privacy & Security*.
   - **Windows:** SmartScreen may warn about an unsigned app — *More info →
     Run anyway*.

No Python, no install, no internet required — the app is fully self-contained
and works offline.

## First launch

You'll be asked to **set a master password**. It encrypts the password field
of every account (scrypt key derivation + AES-256-GCM) and is **never stored**.
If you forget it, the saved passwords cannot be recovered. Everything else
(IGN, username, email, ranks, notes) is stored in plain text.

## Where your data lives

Your vault is a single `vault.json` file in a per-user data folder:

| OS | Location |
|----|----------|
| Windows | `%APPDATA%\MarvelAccountKeeper\vault.json` |
| macOS   | `~/Library/Application Support/MarvelAccountKeeper/vault.json` |
| Linux   | `~/.local/share/MarvelAccountKeeper/vault.json` |

Timestamped backups are written next to it under `backups/` (and a second
copy under your `Documents/MarvelAccountsBackups/`) on every save. If you ran
an older script-style version, your existing `vault.json` is imported
automatically the first time the new app starts.

## Security notes

- The app binds to `127.0.0.1` (loopback) only — it is not reachable from your
  network.
- The decryption key is held in memory only. The vault auto-locks after a
  configurable idle period (default 30 min — change it under **Options**), on
  quit, or when you click **Lock**.

## Running from source

You don't need the executable — with Python 3.10+ installed:

```sh
pip install -r requirements.txt
python app.py
```

It picks a free port and opens your browser, same as the packaged app. On
Windows you can also just double-click `run.bat`.

## Building it yourself

See [BUILDING.md](BUILDING.md) for producing the executables locally, and
[`.github/workflows/release.yml`](.github/workflows/release.yml) for the CI
that builds all three OS binaries on every `v*` tag.

---

Created by Yui · [github.com/itsnotyuiiii](https://github.com/itsnotyuiiii)
