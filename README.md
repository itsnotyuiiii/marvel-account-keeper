# Marvel Rivals Account Tracker

Keep track of your Marvel Rivals / Steam accounts in one place: in-game name,
rank, and — if you want — username, email, and password. It runs on your own
PC. Your data stays there. The only time it goes online is when you refresh a
rank or it checks GitHub for an update.

Don't want to store logins? You don't have to. Username, email, and password
are all optional — you can use it purely to track ranks if that's all you want.

![icon](icon.png)

![Marvel Rivals Account Tracker demo](demo.gif)

## Download (Windows)

1. Go to the
   [**Releases page**](https://github.com/itsnotyuiiii/marvel-account-keeper/releases).
2. Download **`MarvelRivalsAccountTracker-windows.exe`** from the newest release.
3. Double-click it. That's it — no install, no setup. The app opens in a window.
4. Done? Close the window. It shuts down with it.

> [!NOTE]
> ### If Windows says "Windows protected your PC"
> That's normal. Windows shows this for any free app that isn't signed with an
> expensive certificate. It's **not** a virus warning, and the app is safe.
>
> Click **More info**, then **Run anyway**. You only do this once.
>
> Want to be sure? The whole source code is right here in this repo for anyone
> to read, and every download comes with a `.sha256` file you can check (see
> [Verify your download](#verify-your-download-optional)).

## Download (Mac & Linux)

Grab `MarvelRivalsAccountTracker-macos` or `MarvelRivalsAccountTracker-linux` from the
[Releases page](https://github.com/itsnotyuiiii/marvel-account-keeper/releases).
Run `chmod +x MarvelRivalsAccountTracker-macos` once in a terminal, then open it. On
macOS you may also need *System Settings → Privacy & Security → Open Anyway*.

## First launch

You set a **master password** the first time you open it. This is the password
that unlocks the app — it locks any account passwords you save (scrypt +
AES-256-GCM encryption) and is never stored anywhere.

Pick one you'll remember. If you forget it, saved passwords can't be recovered.
Everything else (names, ranks, notes) stays readable.

If you only want to track ranks, you still set a master password to open the
app — you just don't have to save any logins behind it.

## Verify your download (optional)

Every release ships a `.sha256` file next to the `.exe`. To confirm your
download wasn't tampered with, run this in PowerShell (it prints `True`):

```powershell
(Get-FileHash MarvelRivalsAccountTracker-windows.exe).Hash -eq `
  (Get-Content MarvelRivalsAccountTracker-windows.exe.sha256).Trim()
```

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
- The only outbound network calls are: rank refreshes (sends a player's
  in-game name / UID to public Marvel Rivals stat sites — `tracker.gg` and
  `marvelrivalsapi.com`) and a version check against `api.github.com`. Your
  vault contents — emails, usernames, passwords — are never sent anywhere.

---

## For developers

**Everything below is optional — you do not need any of this to use the app.**
It only covers running from source or building the executable yourself.

### Running from source

With Python 3.10+ installed:

```sh
pip install -r requirements.txt
python app.py
```

It picks a free port and opens the app window, same as the packaged build
(falling back to your browser if no native window component is available).
On Windows you can also just double-click `run.bat`. Pass `--no-browser` to
run headless (serve only, no window) — handy for development.

### Building it yourself

See [BUILDING.md](BUILDING.md) for producing the executables locally, and
[`.github/workflows/release.yml`](.github/workflows/release.yml) for the CI
that builds all three OS binaries on every `v*` tag.

---

Created by Yui · [github.com/itsnotyuiiii](https://github.com/itsnotyuiiii)
