# NetEase one-step player stats preview

Normal Add and Refresh now resolve a UID or exact IGN and fill the verified
player name, UID, platform, current rank, highest returned peak and available
ranked-season match/win counts and public PC Career matches/wins/kills/deaths/assists directly from
NetEase. Target players do not need to have logged in on this PC or already have
a card. The app uses shared game-issued sessions from the selected local capture
and the user's latest normal SDK login on this PC.
This is not an anonymous API and does not establish how other sites collect data.

Detailed Career totals for other public PC players are now connected through
the normal game Career RPC. Missing/restricted stats are not shown as zero.
Console detailed Career totals remain unverified; console identity/rank lookup
is independent. The optional recent-match explorer still shows the session
owner only; this build does not import other players' match history.

## Try the prepared desktop copy

1. Close an older Account Tracker window if one is open.
2. Open the **Marvel Keeper - NetEase Career** desktop shortcut, not the bare
   executable. The shortcut supplies the local capture and installed-game paths.
3. Unlock your existing vault normally.
4. Click **New account**, enter a UID or exact in-game name, and click **Create**.
   Leave Platform on **Auto-detect** unless you want a specific platform. Lookup
   happens as part of Create; no extra linking or refresh step is needed. Steam
   credentials and local-match-history authorization are not needed for lookup.
5. Use the card's refresh button or **Refresh rank & stats** for later updates.
   Editing its UID/IGN/platform also triggers lookup as part of **Save changes**.
6. The **NetEase connection** panel is optional. It shows the shared connection
   and an own-Career explorer. Its season selector does not change card rank season.

The shared session owner is UID **1370598422 / PC**. This package is **NetEase-only**:
card and bulk refresh never call Tracker.gg or RivalsData, even if the session
is unavailable or the bare executable is opened without its shortcut. Other
players use this same connection, not separate local logins. Restricted Career
overviews keep saved ranks and show a restriction message. If an IGN has no
unique exact match, enter its UID; the app never guesses a similar player.
The legacy Tracker code remains available to ordinary source/legacy launches,
but `cloudscraper` is excluded from this package. Optional user-opened profile
links and the separate GitHub update checker are not stats dependencies.

Verified other-player lookup on September 8: **KovaaksKid2008 / 1822797559 / PC**,
season19 **One Above All / 5545**, highest returned peak **5601** (season5).
The namecard contains a season19 ranked record of **27 matches / 21 wins**.
A separate verified game Career reply returns **27 matches / 21 wins /
526 kills / 118 deaths / 679 assists**. These are separate sources even where
the counts coincide.
The game capture independently confirms the target's namecard reads. These are
point-in-time results, not promises of future rank or session availability.

Live September 7 result for the session owner: internal season **19** returns rank level14,
**Diamond II / 4350 points**, explicitly **estimated, placements 0/10**. This
is not a completed placement rank. The returned peak is level21,
**Celestial I / 5016 points**, from internal season17. Current-season aggregate
counts are zero. Old season17's rank is not substituted for today's estimate.
Higher saved lifetime peaks are preserved. Rank names use the build-specific
ordered division mapping; independent user confirmation of these labels is pending.

Choose **game season ID 17** for the verified nonempty profile. This is the game's
internal ID, not its displayed season number (17 corresponds to the observed
S8.5 screen). Verified live totals on September 7: 47 matches, 20 wins, 661 kills,
293 deaths, 828 assists, plus 50 match-history entries. ID19 currently returns
zero aggregate totals. History can span earlier seasons.

## Session and file requirements

- Normal refresh does not need administrator permission or another capture.
- Live verification was performed with the game closed. Simultaneous use while
  playing has not been verified; try this build with Marvel Rivals closed first.
- Session credentials are kept in application memory, not sent to the UI or saved in the
  vault/build. The captured identity/rank token's expiration claim is approximately seven days after
  capture, but NetEase can invalidate it sooner. Do not assume it lasts forever.
- Keep the existing capture in its original local diagnostics folder and keep
  the matching game installation available. No capture or token is included in
  the desktop folder. Do not upload the capture to troubleshoot an error.
- Detailed PC Career reads use the latest successful normal SDK login already
  recorded by the game locally. This prototype accepts a log transaction at most
  36 hours old, and NetEase must still accept it. If absent/too old/rejected,
  log into your own game account once, exit the game, and refresh a card after
  the 30-second retry cooldown. Target players never need to log in on this PC.
- Consecutive detailed reads share one authenticated game connection. It closes
  after 15 seconds idle or 32 requests and is recreated on demand with the
  latest local SDK input. No background packet recording or admin access is used.
- If the captured identity/rank session expires, a fresh approved game-start
  capture is still needed. This preview does not automatically renew either
  credential source or start the game. If the installed game
  build changes, its decoder must be revalidated; simply retrying a UID will not
  fix that. Renewal is app-level, not a separate login for each target card.
- The panel stores only a process-local snapshot. Card refresh persists selected
  aggregate numbers and rank/placement metadata as plaintext account metadata,
  like the existing saved ranks. Raw replies and tokens are never saved there.
  No recent matches are automatically imported into the persistent match index.
- Current rank is scoped to the default season observed at game login; capture
  freshness and session renewal still matter at a season rollover. An unavailable
  profile or platform never triggers a third-party fallback request.

## Source and build

Install `requirements-dev.txt` and `requirements-research.txt`, then run
`python scripts/build-career.py`. It creates a new build/output folder without
deleting existing builds. The packaged app accepts these three flags together:

```text
--career-capture "<approved local career.etl>"
--career-archive "<matching installed script.uep>"
--career-own-uid "<your confirmed UID>"
```

`python scripts/smoke-career-build.py <exe> <trace> <archive> --own-uid <uid> --live-card`
verifies one-action Add for the session owner (identity/profile/rank reads), with
persistence in a separate synthetic vault. `--live-player <UID-or-IGN>
--expect-uid <independently-known-UID>` instead verifies one-action public-player
lookup. `--live` tests the panel's profile/rank/summary reads without saving a card.
These are opt-in; test scratch stays under `build/`. Without a live flag, no
live NetEase requests are made. Captures remain outside builds and Git.
