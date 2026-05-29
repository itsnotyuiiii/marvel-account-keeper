# Marvel Account Keeper

A small, local desktop app for tracking your Marvel Rivals / Steam accounts —
in-game name, username, email, password, and current / peak rank. Everything
lives on your own machine. The only time it touches the internet is when *you*
refresh a rank (a player lookup on public Marvel Rivals stat sites) or when it
checks GitHub for a newer version — your accounts and passwords are never
uploaded anywhere.

![icon](icon.png)

![Marvel Account Keeper demo](demo.gif)

## Get started (Windows — this is almost everyone)

There's nothing to "install" — Marvel Account Keeper is a single file you run.

1. **Download the app.** On the
   [**Releases page**](https://github.com/itsnotyuiiii/marvel-account-keeper/releases),
   open the newest release and grab **`MarvelAccountKeeper-windows.exe`** under
   **Assets**. That's the file 99% of people want.
2. **Put it somewhere handy** — Desktop, or a folder like
   `Documents\Marvel Account Keeper`. It runs fine from anywhere.
3. **Double-click it.** The app opens in its own window — no terminal, no
   installer, no setup screen. (On the rare PC missing the built-in window
   component, it opens in your web browser instead.)
4. **When you're done, close the window** and the app quits with it — nothing
   left running. (If it opened in your browser, close the tab; it shuts down a
   couple of minutes later.)

> [!NOTE]
> ### "Windows protected your PC"?
> The first launch may show a blue SmartScreen box. **This is expected and the
> app is safe to run** — Windows shows it for any program that isn't signed
> with a paid certificate, which independent free apps like this one don't buy.
> It is *not* a virus warning.
>
> Click **More info → Run anyway**. You only do this once.
>
> Why you can trust it: the entire source code is public in this repo, so
> anyone can read exactly what it does. It keeps your vault on your own PC and
> only reaches the internet to refresh ranks or check for updates. If you like,
> you can verify your download is untampered — every release ships a
> `.sha256` file next to the `.exe`; compare it with
> `Get-FileHash MarvelAccountKeeper-windows.exe` in PowerShell.

## Mac & Linux

Download `MarvelAccountKeeper-macos` or `MarvelAccountKeeper-linux` from the
[Releases page](https://github.com/itsnotyuiiii/marvel-account-keeper/releases)
instead. Make it runnable once in a terminal —
`chmod +x MarvelAccountKeeper-macos` — then double-click or run it. On macOS
you may also need *System Settings → Privacy & Security → Open Anyway*.

## First launch — set your password

The first time it opens, the app asks you to **create a master password**. It
locks the account passwords you save (strong encryption — scrypt key
derivation + AES-256-GCM) and is **never written down anywhere**.

Pick something you'll remember: if you forget it, the saved passwords can't be
recovered. Everything else (in-game name, email, ranks, notes) is stored
normally and stays readable.

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
