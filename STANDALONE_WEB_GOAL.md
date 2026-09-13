# Standalone NetEase profile goal

Activated September 11, 2026 at the user's explicit request. The active Codex
goal is the execution tracker; this file preserves the technical acceptance
criteria across machines. It is not a claim that the goal has been completed.

## Required outcome

A UID lookup on the hosted site returns the player's available NetEase identity,
Career totals, season ranks and verified recent matches without the user's
laptop, installed game, SDK logs or packet captures at runtime. Per-match rank
changes must use verified match/player data, not differences between unrelated
rank snapshots. No third-party statistics provider is used.

1. Obtain a legitimate owner-authorized server-side session and renew it through
   the normal platform/UniSDK flow. A browser OAuth session is not assumed to be
   accepted game authentication and must never be relabelled as a gas token.
2. Supply reviewed, versioned public runtime configuration without game files.
   Public configuration portability does not itself provide authentication.
   Review each supported update explicitly; do not simply replace hashes, guess
   version strings, or claim old pins describe a new build. Build 3848703 inputs
   were independently reviewed on September 12, not assumed from the old pins.
3. Obtain/renew the separate SocialMatrix session without capture files. Verify
   the owner identity and request binding before using a returned token.
4. Run the authenticated native client on an appropriate server and expose only
   the existing validated HTTPS profile contract to Sites. The Sites Worker
   cannot itself run the native raw-TCP connection. Deployment cost/provider
   choices need approval before acquiring paid infrastructure.
5. Live-verify multiple requested UIDs and individual section visibility. Test
   refresh, session expiry/renewal, unavailable/restricted responses, source
   identity and server restart without falling back to local files.
6. Populate the existing Overview, Rank history and Matches tabs with the
   verified responses. Recent first-page matches are not lifetime history;
   unknown fields remain null, not zero. Additional hero metrics/pagination
   require separate response evidence.

## Punishments investigation and conditional tab

Investigate authoritative NetEase data for exact-UID sanctions, including
cheating and communication restrictions. A normal read-only lookup for another
player must be established before any such records are attached to their UID.
Do not submit reports or acknowledge/clear warnings to probe for records.

If a supported authorized source is verified, add a Punishments tab with the
exact source, exact UID binding, sanction type/reason only as returned, event
times and expiry when known, and explicit coverage and reversal/appeal limits.
Do not infer that an old announcement proves a ban is still in effect.

Masked announcement UIDs, matching nicknames, user reports, and the shared login
owner's restriction state are not proof about a queried UID. Missing/unavailable
data must never become "clean account" or "no punishments". If no suitable source
is verified, document that limit rather than creating an apparently live feed.

## Current evidence — September 12, 2026

- Latest follow-up supersedes the token-index/initial-delivery wording below:
  reviewed RPC1538836248 delivered the normal SocialMatrix token after exact
  owner gateway verification. Owner/zone/time/route claims passed, using only
  packaged public inputs, not captures or installed files at runtime. The
  passive reader received850846 bytes/19 records and sent no application bytes.
  Engine eid and account uid are separate; an earlier incorrect equality gate
  was fixed from current source. Claim checks are not service acceptance.
- Fresh Python SocialMatrix setup and packaged public parameter/index loading
  are LIVE VERIFIED separately from the historical captured native template.
  The explicit --verify-social-session helper wires a single callback through
  the verified owner chain. Hello, exact RPC/aid echo and streamid passed; seven
  modules including usable svc-user were advertised. One connection sent14
  handshake+711 application bytes, received509, then closed. Source-backed
  parser corrections resolved a prior local shape rejection. No player-query
  integration, saved token or autonomous renewal is implied by setup.
- Direct OAuth renewal returned an HTTP rejection before AUTH (original status
  unknown); browser comparison returned TypeError without a usable candidate.
  A credential-free diagnostic returned400/code1000/invalid access with no CORS
  sharing header. None proves the UID/token invalid. Normal SDK login can mask
  refresh failure with interactive fallback. Autonomous renewal remains open.
  All raw credentials remain transient; no browser-storage update was made.
- The previous update blocker is resolved. Steam installed/target build 25249397
  is complete; native version/changelist is 1.1.3848703 / 3848703, not the Steam
  build ID. The active goal remains incomplete.
- The current archive/image/SDK/config and all five typed LOGIN descriptors
  were independently reviewed. Relevant LOGIN schemas and Career RPC IDs match
  the implementation. Public transport key, parameter and official server-list
  URL are unchanged. See API_RESEARCH.md for current provenance and offsets.
- A separate 774-byte public-only 3848703 runtime artifact is independently pinned
  at SHA256 `0111accea3a87a8ed6f45190f41cf5e517b400944c6342bdf689afa34bbb1d81`.
  Old 3825464 defaults/artifact remain unchanged; research explicitly selects
  `--runtime-version 3848703`. Loading the package needs no installed game.
- ONE current-build AUTH-only test accepted the owner's existing official-store
  SDK session and verified expected existing role UID 1370598422. Result:
  own_existing_role_verified; prepare/auth_service/own_role true; one connection,
  286 handshake + 625 application bytes sent and 2399 received. It closed without
  gateway entry, completed game login, Career query, refresh or saved credentials.
  The first helper expired before reading a payload; the fresh successful helper
  is terminal too. Refresh remained in the browser, not in a saved server session.
- A separate explicitly selected gateway follow-up now returned
  own_gateway_login_verified: prepare/auth_service/own_role/game_login true.
  The LOGIN leg used one connection, 286 handshake + 625 application bytes sent,
  2397 received. The gateway leg used one connection, 286 handshake + 71
  application bytes sent, 39295 received. Its LOGIN result matched UID 1370598422,
  login_key presence was verified without export, and reply_relogin was 0.
  Four HOTFIX data records contained 37886 content bytes and were never executed.
  Both sockets/helper closed. No after_login callback, Career/SocialMatrix/
  punishment query, refresh or credential saving occurred. Career remains
  unverified in this flow; AUTH and gateway success are not full stats access.
- Current-version acceptance supersedes the older 1911-only evidence below for
  this specific reviewed envelope. It does not prove independent renewal, all
  envelope variants or a standalone full-stat service. The final focused set is
  50 combined web-SDK tests (0.097s) and 40 runtime tests (0.129s), including 15
  new gateway and three helper tests. The full suite after all current source
  edits passed 572 tests in 69.995s, with existing non-failing synthetic warnings,
  injected vault-write failure output and ResourceWarnings.
- Required next work remains: independently obtainable and renewable
  owner-authorized server authentication; separate SocialMatrix session;
  live exact-UID/visibility-bound Career, season ranks,
  matches and verified per-match rank changes; approved native-capable hosting;
  and refresh/expiry/restart integration verification without local-file fallback.
  Source S_REFRESH_SOCIAL_TOKEN identifies the next passive initial-token path,
  but its numeric RPC index is unresolved and no live SocialMatrix token has
  yet been received or verified. Fresh route/hello/setup support is still needed;
  do not guess the RPC index or repeat gateway login before the collector is ready.
- The private website still has three prepared views and only a live identity
  source. No production provider/site switch, game launch, memory/log
  inspection, capture or Wi-Fi change occurred in this follow-up. Public source
  inspection was static preparation; the live helper used only packaged inputs.
  A read-only process-name check found no running Marvel Rivals game before the
  gateway experiment; process memory was not read.
- Punishments remain an investigation, not a verified feed. The previously
  found current-restriction getter is not history or an established read-only
  other-player source. No punishment query or report was sent, and unavailable
  data must not imply no punishments. See PUNISHMENT_RESEARCH.md.

## Earlier evidence — September 11, 2026

The update/current-version/authentication limits in this historical record are
superseded only to the extent stated in the September 12 checkpoint above.

- Private website has the three profile views but only a live identity source.
- September 11 07:16–07:17 Eastern recovery check verified both direct and hosted
  IGN/level lookups for1370598422 and1822797559. Earlier store0500 errors recovered;
  maintenance causation remains unproven. See the website's ignored diagnostic
  report, not the older outage wording in earlier checkpoints.
- Other-player aggregate Career was previously verified using the normal game
  transport. First-page other-player match-summary helpers are synthetic-tested,
  not live-verified or connected to the website.
- Server-side authentication/renewal and the additional live data remain open.
- Portable public runtime manifest parsing and server-list projection are now
  implemented without installed-game or network reads. A reviewed public-only
  774-byte build3825464 artifact is packaged with an independent digest and LF
  preservation;35 runtime/export/artifact tests pass, including a real-artifact
  load with desktop sources disabled. This is not current-patch validation or
  a live provider switch. See runtime-inputs/README.md.
  Final complete Python verification passed517 tests in67.094s.
- One credential-free live check using only that packaged artifact and the
  verified official server list received a decrypted LOGIN prepare record.
  Normal transport works without installed files; no account/version acceptance
  or profile access was tested, and no live provider was enabled.
- Current public official SDK scripts expose OAuth refresh and sdk_token
  history-login flows. Neither is proven to yield accepted game authentication.
  Native gen_auth_info directly requires metadata absent from browser SAUTH;
  the inspected paths do not establish a normal browser-to-game conversion.
  Owner Steam-linked Account Login now verifies1370598422 / ゆいᶻᶻᶻ. Actual
  issued SDK values are platformpc and SDK1.0.0. One research-only minimal AUTH
  request preserved that contract and omitted absent native fields; the game
  service rejected it with code1911 after successful prepare. No gateway entry
  or stats query followed. Tokens were not printed or saved, refresh stayed in
  the browser, and the production provider/site remain unchanged. This is not
  proof of an invalid UID or a general incompatibility of browser-issued tokens.
  Pinned errcode definitions map1911 to BATTLE_DS_VERSION_NOT_FOUND; the minimal
  envelope omitted public version fields. Their necessity and current-version
  acceptance remain unverified. Full Python suite:538 tests passed in116.645s.
  A separately selected variant adding only reviewed client_version/engine_ver
  also returned1911. Those fields alone do not resolve the version condition;
  current build versus missing platform inputs remains unresolved.26 focused
  tests pass after this variant; the full538 result preceded five added tests.
  A third source-verified comparison also added outer os_namepc and still1911;
  no further AUTH was sent. Steam now records a pending20.7GB update (installed
  Steam build25098044, target25249397); installed native changelist remains3825464.
  Obtain/review current official public distribution inputs next. Steam buildID
  must not be used as native changelist, and the update is not a proven fix yet.
  Final full verification after all research variants:546 tests passed in101.007s.
- A native UID-keyed current restriction getter was found. It checks login/chat/
  rank categories, not an established event history or adjudicated cause. See
  PUNISHMENT_RESEARCH.md. No reports or punishment queries have been submitted.

The goal stays active until the required integration is genuinely delivered or
a persistent external blocker meets the goal system's blocked-state criteria.
