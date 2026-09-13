# Local NetEase desktop mode

The active desktop work is on `codex/local-native-stats`. Hosted work is paused
and preserved at `codex/hosted-backend-checkpoint-20260912`, commit `3ea22dd`.
Nothing here requires deploying or running the hosted API server.

## Use Marvel Tracker locally

1. Open `MarvelRivalsAccountTracker-Native.exe` and unlock your existing vault.
2. Expand **NetEase connection** and select **Connect NetEase**. Complete your
   normal sign-in in the official NetEase store window. No purchase is needed.
   The connection currently authenticates the reviewed owner UID `1370598422`;
   other target players do not need to sign in on this computer.
3. Use **Add account** with a numeric PC UID, or refresh an existing UID card.
   Select **PC** or **Unknown**; this client currently reads PC profiles only.
   Add, Refresh and Refresh All use the same local NetEase provider.
4. Open the card details to inspect the available Career, rank and recent-match
   sections. Each section keeps its own visibility/unavailable state.

The game, Steam client, game files, captures and administrator access are not
required for these profile reads. Internet access to the normal first-party
NetEase services is still required. There is no tracker.gg, RivalsData or hosted
backend fallback. IGN-only lookup is not connected yet: supply the player's UID.
This preview does not self-update from the standard Tracker release channel;
install a newer native preview build when one is provided.

Initial sign-in requires the native pywebview/WebView2 window. A browser-only
fallback cannot create the embedded sign-in window. OAuth popup handling depends
on WebView2; successful SDK context collection is not by itself proof of the
game account. Every uncached profile lookup performs the normal owner-bound
authentication checks before publishing player data.

## What the cards mean

- Identity includes the exact requested UID, returned IGN and available level.
- Career totals are **unfiltered**, not totals for the displayed current season.
- Ranks use numeric levels and raw `server_rank_score` values. English rank
  labels, final placement status and a normalized lifetime peak are not inferred
  from those numbers. Existing manual/saved rank fields remain separate.
- Recent history is only the first page, up to **50** matches, not complete
  lifetime history. Per-match rank changes appear only when independently
  verified; missing changes are not fabricated as zero.
- Overview and match-history permissions are independent. A restricted overview
  does not automatically hide an otherwise permitted history section.
- A short cache avoids repeating a successful UID lookup within 30 seconds.
  Older drawer snapshots retain their original read time; opening the drawer
  does not claim a fresh upstream read.

Recent-match rows stay in memory while the connection remains unlocked. They are
not written to the vault or imported into its separate manual match-history
index. Locking or closing clears them. Non-match card snapshots can remain saved
with their original read time; failed refreshes do not silently turn them into
fresh data.

## Session storage and recovery

By default, the new connection is memory-only and ends when the vault locks or
the app closes. Choose **Save the NetEase session encrypted locally for future
launches** before connecting if you want durable renewal/recovery.

Persistent session records use a separate encrypted SQLite file:

```text
%APPDATA%\MarvelAccountKeeper\netease-session.sqlite3
```

`MARVEL_KEEPER_DATA`, when explicitly configured, changes the app's data directory
and therefore this location. Session encryption uses the unlocked vault key;
credentials do not become card fields, frontend responses or release assets.
The app's existing optional Windows DPAPI remember-unlock mechanism is separate
from the choice to persist the NetEase session.

Only one process may own a persisted session at a time. Normal shutdown drains
admitted renewal work before releasing its lock. A saved candidate can be
verified through **Recover saved session** without exchanging an older SDK
refresh token again. An interrupted exchange with no saved candidate is an
uncertain rotation, not proof that a target UID is invalid. The current desktop
UI cannot replace this unrecoverable saved connection yet; it never retries an
old token automatically. A separately approved fresh connection is needed in
that exceptional state. Existing saved session
records are never silently overwritten by another bootstrap.

Memory-only sessions cannot recover after a process crash. The tests cover
synthetic Windows forced-exit/restart behavior, not power loss, live token expiry
or graceful Linux signal handling. Do not force-close an active refresh when a
normal close is possible.

## Isolated Windows packaging

Use the existing build environment with `requirements-dev.txt` and
`requirements-native-client.txt` installed. The script does not install packages,
connect to NetEase, inspect credentials or run the game.

From the repository root, validate the public inputs and inspect the command:

```powershell
& ".\.venv\Scripts\python.exe" -B "scripts\build-native-desktop.py" --dry-run
```

The dry run creates no directories or metadata and never invokes PyInstaller.
When ready to build:

```powershell
& ".\.venv\Scripts\python.exe" -B "scripts\build-native-desktop.py"
```

Each invocation creates fresh UTC timestamp/UUID directories:

```text
dist/native-<timestamp>-<uuid>/MarvelRivalsAccountTracker-Native.exe
build/native-<timestamp>-<uuid>/work/
build/native-<timestamp>-<uuid>/spec/
```

The executable embeds `stats_provider: netease-native`, the main app,
templates/static assets, required Python/WebView libraries and exactly these two
hash-pinned public runtime manifests:

```text
runtime-inputs/3848703.json
runtime-inputs/3848703-social.json
```

Under PyInstaller, `app.RESOURCE_DIR` points into the extraction directory;
the local runtime receives that directory and checks both manifest pins there.
No entire runtime-input directory, game archive, capture, SDK log, research
helper script, local SQLite database, key or browser profile is an added data
asset. Existing legacy-provider Python imports may still be included by normal
dependency analysis; their capture/research assets are not bundled or selected.

The script never invokes `build.py`, deletes `build/` or `dist/`, or replaces a
previous executable. Failed output/work directories remain available for
inspection. The final JSON reports the new executable path, size and SHA-256.
Building alone is not proof of successful interactive desktop sign-in or live
profile acceptance; those require separate checks of the resulting executable.

The packaged build can also be checked without a real vault or login:

```powershell
& ".\.venv\Scripts\python.exe" -B "scripts\check-native-desktop.py" "<full path to the new EXE>"
```

This headless smoke uses a disposable synthetic vault and localhost HTTP only,
checks initialization/status/Add/assets/update-channel behavior, and shuts down
only its fixture-bound child. It never signs in or refreshes NetEase. A headless
instance correctly reports that native sign-in is unavailable.
