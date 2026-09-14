# Cloud-connected Marvel Tracker desktop

This build keeps the existing desktop vault and account-card interface while
reading player statistics through your AWS-hosted NetEase backend. It does not
use Tracker.gg or RivalsData. The website and desktop share the backend, not the
desktop vault.

## Upgrade an existing desktop installation

1. Close the older Marvel Tracker normally. Keep its executable as a rollback
   option and keep a backup of its data folder.
2. Open `MarvelRivalsAccountTracker-Cloud.exe` under the same Windows user and
   unlock it with the existing vault password. There is no new account import
   when both builds use the same data folder.
3. Confirm your existing cards are present, then refresh one UID card. Add,
   Refresh and Refresh All use the cloud connection in this build.

The default data folder is unchanged:

```text
%APPDATA%\MarvelAccountKeeper
```

An existing `MARVEL_KEEPER_DATA` override must remain the same for the new build.
An empty account list or a request to create a new vault is a reason to check the
data location before initializing anything. Do not copy an empty vault over the
older one. Moving the application executable alone does not move its data.

On first opening an existing vault, the cloud build creates a local pre-upgrade
checkpoint under `backups/pre-cloud-<timestamp>-<uuid>/`: an unchanged vault
copy and, when present, a consistent SQLite backup of the match-history index.
The other original files stay in place. This checkpoint is local, not uploaded.

Saved account passwords, notes, manual ranks and the local match-history index
remain local. The cloud provider sends a player UID and the service access
credential, not the card's Steam username, email, password or notes. Existing
manual/saved rank fields remain separate from current NetEase rank information.
Account passwords are encrypted in the existing vault format; not all vault
metadata is encrypted. Back up the whole data folder, not just the executable.

## Connection and credentials

The backend endpoint is:

```text
https://khvig9ub7b.execute-api.us-east-1.amazonaws.com
```

This private deployment requires a separately provisioned service access
credential. It is stored locally using Windows user-scoped DPAPI, not inside
the executable, Git repository or card data. The packaged application does not
need AWS CLI, AWS access keys, administrator privileges, the game, Steam, a
capture, or another NetEase sign-in. The backend maintains its own approved
NetEase session.

The private connection file is `cloud-connection.dpapi` in the same app data
folder. The vault must be unlocked before the application opens that credential.

A desktop copy on another Windows account or machine cannot reuse the encrypted
connection file automatically; it needs its own provisioning. The operator's
AWS admin credentials are not application runtime credentials. Do not paste
them into account cards or distribute them alongside an executable.

An unconfigured installation can still open the vault and save cards. Refresh
reports `cloud_not_configured` until its service credential is provisioned. A
cloud error does not erase the card or turn an older snapshot into fresh data.
If NetEase returns no profile for a UID, that is not proof the UID is invalid,
banned or private. The UI preserves these distinctions.

The previous native build's encrypted local NetEase session is separate and is
not migrated, uploaded or required by this cloud build. Do not run old and new
desktop builds against the same vault simultaneously.

## Statistics and placements

- Supply a numeric PC UID. IGN-only search is not connected.
- The card fills the returned identity and available Career/rank data. Career
  totals are unfiltered; they are not totals for whichever season is displayed.
- Placement progress uses NetEase's returned placement count for the exact
  current season. Missing data stays unknown; an older season is not substituted.
- A provisional/estimated rank is NetEase's estimate, not a custom hidden-MMR
  prediction or a guarantee of the final rank. Completed placements and unknown
  placement status are labeled separately.
- The current verified data gives numeric native rank levels, not a verified
  English rank-name table. These stay labeled `Rank level N`; raw server score
  is not relabeled as normalized competitive points. No tier name is guessed.
- Recent matches are the returned first page, up to 50, not complete lifetime
  history. Verified match rank changes may be shown; missing changes are not
  invented as zero.

Current live match rows remain in memory while unlocked, separate from the
desktop's manually imported match-history index. Locking or closing clears the
live cache. Saved non-match snapshots retain their original read time.

This preview does not install updates from the older Tracker release channel.
Use an explicitly supplied newer cloud build to update it.

## Isolated Windows packaging

Use the repository's existing Windows Python/build environment. These scripts
do not install dependencies or request any credentials.

Inspect the build plan without creating files:

```powershell
& ".\.venv\Scripts\python.exe" -B "scripts\build-cloud-desktop.py" --dry-run
```

Build a new isolated artifact:

```powershell
& ".\.venv\Scripts\python.exe" -B "scripts\build-cloud-desktop.py"
```

Every invocation gets unique UTC timestamp/UUID directories:

```text
dist/cloud-<timestamp>-<uuid>/MarvelRivalsAccountTracker-Cloud.exe
build/cloud-<timestamp>-<uuid>/work/
build/cloud-<timestamp>-<uuid>/spec/
```

The executable embeds `stats_provider: netease-cloud`, the application,
templates/static assets and required Python/WebView libraries. Only the static
resource directories and generated public build metadata are explicit data
assets. The cloud path needs no game runtime manifests. No connection file,
bearer, vault, database, local session, AWS keys, capture, game files or browser
profile is added as package data. Normal dependency analysis may retain existing
application Python imports; metadata selects the cloud provider at startup.

The builder never calls `build.py`, deletes existing build/output directories,
or replaces a previous executable. Failed build directories remain available.
The final JSON reports the executable path, size and SHA-256.

Check the resulting package without real accounts or a service credential:

```powershell
& ".\.venv\Scripts\python.exe" -B "scripts\check-cloud-desktop.py" "<full path to the new EXE>"
```

The headless smoke uses a disposable synthetic data folder. It checks the
packaged cloud mode, clear unconfigured status, disabled legacy update channel,
Add UID behavior, asset hashes, and preservation of synthetic credentials,
manual ranks and notes through a normal app restart. It reads localhost pages
without executing browser assets and does not sign in or request cloud stats.
It shuts down only its fixture-bound child; it never kills unrelated processes.
Successful closed fixtures are removed, while failed fixtures are retained.

A successful build and synthetic smoke do not by themselves prove live backend
access or visual correctness. Verify a configured UID refresh and inspect the
desktop UI separately before handing off a release.

### Opt-in packaged live acceptance

After provisioning this Windows user's encrypted connection file, explicitly
select it and a UID to test the actual packaged HTTP path with synthetic cards:

```powershell
& ".\.venv\Scripts\python.exe" -B "scripts\check-cloud-desktop.py" "<full path to the new EXE>" --live-config "<full path to cloud-connection.dpapi>" --live-uid 1370598422
```

Both live options are required. The default run remains offline. The live run
first completes all unconfigured and restart checks, then copies only the
bounded DPAPI ciphertext into its isolated fixture and refreshes a synthetic
card through the running executable. The localhost request allows at most
120 seconds. No real vault is opened; the source connection file is unchanged,
and neither the script nor its output decrypts or prints the access credential.
The desktop itself decrypts the fixture copy under the current Windows user.

Acceptance verifies the requested UID, returned IGN, native profile binding,
rank display, read time, and preservation of synthetic private fields and manual
ranks. Output contains only selected public profile facts and check results,
not raw account rows or service responses. Add `--leave-open` to retain the
unlocked synthetic window server for visual inspection; this retains a copy of
the encrypted service credential in its printed fixture folder. Close that
fixture normally and remove only its exact temporary folder after inspection.
