# Codex Continuation Handoff

This file intentionally travels with the continuation branch so the work can
be resumed from another computer without relying on local Codex session data.

## Active goal and recovery update — September 11, 2026

The user explicitly activated a goal for the standalone NetEase-only hosted
profile integration and added investigation of UID-linked punishment history
(cheating, voice/text restrictions, etc.). See STANDALONE_WEB_GOAL.md for the
acceptance criteria. Do not claim that the web stats connection is finished.

The one-time recovery check completed at07:16–07:17 Eastern: direct NetEase
HTTP200/application0000 and hosted HTTP200 for UID1370598422 (ゆいᶻᶻᶻ, level32)
and1822797559 (KovaaksKid2008, level63). This supersedes the store0500 outage and
pending-recheck wording below. No request-path fix was needed; maintenance's
causal role remains unknown. Do not restart the completed one-time monitor.

Punishments must be tied to an exact UID by an authoritative, authorized source.
Masked official ban-announcement UIDs and own-account restriction fields are
not a verified other-player punishment feed. Static research found a concrete
xyq-namecard/GetData read using tagpunishment and zone_UID_login/chat/rank record
IDs, called by normal report feedback. It is CURRENT restriction status, not
history or verified cause; no live lookup or report was sent. See
PUNISHMENT_RESEARCH.md for request/response caveats and exact source evidence.

portable_game_runtime.py now parses an explicit, independently hash-pinned public
runtime manifest into the existing configuration/method types, and projects
supplied server-list bytes. Runtime loading reads only the explicit manifest;
there are no installed-file reads or network calls. The follow-up packaged
runtime-inputs/3825464.json (774 bytes, SHA256
63d536706d9cb055770129ca413950861d2553cb6c977bdee4f113fa66ccbc28) from the reviewed
public build pins. Its independent digest is in portable_game_runtime.py and
.gitattributes preserves LF. See runtime-inputs/README.md. Source config/version
and key/parameter/method provenance passed; current-patch compatibility,
authentication and renewal remain unverified. The app/site was NOT switched to
this artifact. All35 runtime/export/artifact tests pass. The prior123 game
regressions also passed before this follow-up. Final full follow-up verification:
517 tests passed in67.094s, including the two real-artifact tests. The independent
bounded correctness review found no issues. Existing synthetic error logs and
temporary-file ResourceWarnings were observed; unrelated code was not changed.

A bounded live follow-up loaded ONLY the packaged manifest, fetched its exact
official HTTPS server list once without redirects/cookies/environment auth,
validated it and connected to its first advertised port once. Normal transport
sent286 handshake bytes and received54 bytes, including a decrypted main1/sub101
LOGIN prepare record with a19-byte body. No credentials, application message,
heartbeat, gateway enter or Career query were sent. The socket closed. This
verifies portable transport, not account/version acceptance or stats access;
no installed files were consulted and no port scan/retry was performed.

scripts/export-public-runtime.py requires four explicit distribution paths,
emits only a deterministic public candidate to stdout plus digest metadata to
stderr, and never reads credentials, launches the game or contacts a server.
The first real export exposed an overly small version-file bound: the public
version.json contains a108231-byte asset inventory. A256KiB bound and synthetic
large-inventory regression corrected this; the inventory is never exported.

Owner sign-in is NO LONGER pending: normal Steam-linked store login and character
selection verified1370598422 / ゆいᶻᶻᶻ / level32. Important correction: the actual
issued SDK object is platformpc, SDK1.0.0, not an assumed web/1.0.8 bundle. It
has sessionid and refresh_token but lacks gas_token/ip/aim_info. See the opening
API_RESEARCH.md section for observed fields and corrected source offsets.

The separate research-only web_sdk_auth_probe.py and one-use loopback helper
scripts/probe-web-sdk-login.py made one minimal AUTH attempt preserving that
SDK contract and omitting unknown native fields. The existing native builder is
unchanged. After a first handoff expired WITHOUT credentials or game AUTH, a
dummy-only handoff passed; the fresh real handoff then completed. No browser
permission, firewall, Wi-Fi or network setting was changed. Initial timeout
cause remains unknown. Both helper processes are terminal, not pending jobs.

Live result: prepare verified; one connection,286 handshake+593 application
bytes sent,128 received; AUTH rejected with server_error_code1911. Credentials
were sent only to the normal advertised NetEase LOGIN service, never printed
or saved. Refresh tokens stayed in the browser. No gateway enter, role creation,
Career query, punishment query or automatic retry followed. Do not call this an
invalid UID, successful game login, or proof that every browser token fails.
The official store remains signed in; do not ask for the same login again.

Normal oauth/refresh_token and sdk_token paths are source-verified, not proven
independent game authentication/renewal. Missing native fields must not be
invented. Next work requires explaining1911 and an evidence-backed change before
another AUTH attempt. Pinned errcode entry875 maps1911 to
BATTLE_DS_VERSION_NOT_FOUND, not253 LOGIN_ERR_AUTH_FAIL; which absent version
field caused it and token acceptance remain unproven. The full Python suite
passed538 tests in116.645s, including21 new synthetic AUTH/handoff tests.
Existing non-failing injected-error/ResourceWarning outputs remain unchanged.
The standalone integration and full goal remain incomplete.

Follow-up: pinned gen_auth_info maps client_version and engine_ver to the public
build changelist. An explicit --with-reviewed-version variant adds only those
two fields using packaged provenance3825464, not an invented current version.
One changed-envelope AUTH test also returned1911 (286 handshake+618 application
bytes sent,128 received). It closed without gateway/profile queries. Then pinned
native SDK source independently established outer os_namepc; the separately
selected --with-reviewed-pc-envelope variant adds only that field to the versioned
envelope. Its final comparison also returned1911 (625 application bytes sent).
There were THREE real AUTH attempts, each separately evidence-selected, not an
automatic retry loop. The first failed handoff sent none. All helpers are closed.
Do not retry these envelopes unchanged or infer a credential/UID failure.

Concrete next prerequisite: Steam appmanifest2767030 records installed
buildid25098044 versus TargetBuildID25249397 and a20656586254-byte download with
0 bytes downloaded. The installed version.json is still1.1.3825464/changelist
3825464, last updatedSeptember5 00:04:37UTC. Steam buildIDs are NOT the game
changelist. No staged version file or known public updater-manifest URL was
found. Obtain the official update and independently review its public version,
transport/method/schema inputs before another AUTH test; do not just repin
hashes or substitute the Steam targetID. No update/download/launch was started.
This supports the stale-build hypothesis but does not prove the complete cause
of1911. The official web login step is complete; no repeat sign-in is requested.

Final verification:29 focused AUTH/handoff tests,18 existing LOGIN regressions,
and full546 tests in101.007s passed. The independent correctness review's one
expiry-report defect was fixed: if body reading began but handoff validation
failed, expiration reports credentials_read:null rather than false. The final
deltas had no further review findings. No production app/site or desktop build
changed. All test/helper processes are terminal; there is nothing left to poll.

Next goal continuation: Steam update still showed0 bytes downloaded. A bounded
official Loading Bay alternative was exhausted: Windows download metadata is
launcher1.5.26.11, not the native game build. Public recommendation/list verifies
Marvel app_id31; the ordinary game_package/config?app_id=31 response contains
branding/channel/news metadata but no game version/manifest/download URL. Exact
primary links are recorded in API_RESEARCH.md. No binaries, installations,
account requests or AUTH retries were used. The pending official game update
remains the next prerequisite; don't repeat these closed metadata leads.

No website source/deployment, Desktop executable, Wi-Fi, game process, reports
or punishment queries were changed by this goal research/preparation work.

## September 11, 2026: hosted profile views and transport preparation

Current user request: NetEase-only UID profile website with available player
stats, matches and rank history; no laptop/game/capture-file runtime dependency
and no third-party stats provider. This goal is NOT complete. Nothing here
claims a working independent full-stat backend or complete lifetime history.

Website checkout: C:\CodexRepos\marvel-stats-web, separate Sites source repository.
Private owner-only site: https://yui-rivals-stats.kbj311.chatgpt.site . Published
version3 from source30d34b613f012c1c4374882a24af955bcea2dc9b on September11.
Overview, Rank history and Matches tabs accept validated sections; unavailable,
restricted and unknown are distinct from available-empty or zero. Season rank
summaries are separate from an unimplemented per-match rank timeline. Match
summaries bind every row to the requested UID, maximum50, scope recent with
explicit unknown completeness. Synthetic fixtures never enter production.
29 web contract/service/projector tests, TypeScript check and production build
passed. Deployed source has NO new live full-stat source; default is still the
anonymous official-store IGN/level lookup.

That store lookup currently returns HTTP200/application0500 for previously
working UIDs, also reproduced in its own normal browser UI with user-approved
terms and UID1370598422. It does NOT establish invalid UID. The error preceded
the announced September11 09:00UTC maintenance window; causation is unproven.
Ignored website outputs/netease-lookup-diagnostic-2026-09-11.md records evidence.
User approved a one-time September11 07:15 America/New_York recovery comparison,
scheduled as heartbeat recheck-netease-after-maintenance in this task. Do not
duplicate that schedule or change Wi-Fi/network settings.

Root source improvements (not a new Desktop executable):

- GameCareerClient now accepts four explicit zero-argument providers for public
  configuration, Career RPC methods, owner credentials and advertised server.
  With all supplied, archive may be omitted and no desktop loaders run. Partial
  injection retains desktop defaults. Failure never silently falls back. This
  is an injection seam, NOT credential acquisition/renewal.
- game_career_probe adds make_match_summary_request, verify_match_summary_args
  and read_one_match_summary for the observed native query_match_summary call,
  target UID/zone, first page0/size50 only. Whole request+nonce/method+every row's
  target UID must match. Shared reader keeps15s/128-record/4MiB bounds. Separate
  global BattleHistoryIsVisibleToOther permission is a caller prerequisite;
  public overview alone is insufficient. No CLI/provider live path was enabled.
- 123 game regression tests pass, including27 shared-client and30 Career/probe
  tests. New match-summary behavior is synthetic-tested, NOT live-verified.
  Final complete Python suite:482 tests passed in69.094s. Existing synthetic
  injected-error logs and temporary-file ResourceWarnings were observed; no
  unrelated cleanup or match-index changes were made.
- API_RESEARCH.md now records static hero, other-player match/detail and rank
  delta contracts with pinned archive source evidence. Only five aggregate
  Career fields were previously live-projected; do not reconstruct the unsaved
  41-field list or conflate hero total_match_win_count with total_win_count.

Remaining: a verified independent platform/UniSDK login and renewal flow,
SocialMatrix acquisition/renewal without captures, versioned portable public
configuration, authenticated server hosting (Sites Worker cannot run the native
TCP client), and live verification of history/hero fields with correct privacy.
Normal store browser login has not been established as game authentication;
OAuth access tokens must not be substituted for gas_token. No game/account
credentials were read, exported or deployed during this interim implementation.

## Earlier delivered checkpoint: one-step NetEase stats and Desktop verified

The requested one-step workflow is implemented and delivered for verified
available data. UID or exact IGN Add, normal card Refresh, and streaming bulk
Refresh now populate identity/current rank/highest returned peak plus public
PC Career matches, wins, kills, deaths and assists directly from NetEase.
No Tracker.gg/RivalsData stats fallback, target-local login or extra card-link
step is used. Privacy and exact UID/season/request binding remain enforced.

Final executable: dist/career-20260908-212539-503b3c78/
MarvelRivalsAccountTracker-Career.exe, 24486982 bytes, SHA256
65a7e913fbe61ff06738e6c2cc6f0ccece34e3c2a417f8617507464302f2f736.
Identical Desktop copy: C:\Users\kylep\OneDrive\Desktop\
Marvel Keeper - Full NetEase Stats 2026-09-08\MarvelRivalsAccountTracker-Career.exe.
Existing Desktop shortcut Marvel Keeper - NetEase Career.lnk now points there;
its capture/archive/own-UID arguments were preserved and read back. Prior
folders and a copy of the old shortcut are retained. READ ME.md and BUILD INFO.md
in the new Desktop folder explain use, verification and limitations.

Live source UID+IGN Add shared the same verified gateway. Packaged UID Add,
Desktop IGN Add, and Desktop normal + streaming bulk Refresh all succeeded
for KovaaksKid2008/1822797559/PC: current5545/One Above All, peak5601, season19
27 matches,21 wins,526 kills,118 deaths,679 assists. Follow-up reads honored
31-second cooldowns, reopened the idle connection, and produced newer fetch
timestamps. Projected persistence was verified in isolated synthetic vaults;
the smoke child's synthetic USERPROFILE also isolates secondary backups.
All owned test processes shut down. No real account/vault or real backup was
changed, and no game process, admin access or new capture was required.

GameCareerClient owns one serial daemon gateway worker:15-second idle closure,
32 requests/connection,8 pending maximum,60-second caller bound and30-second
failure cooldown across targets. Latest own SDK inputs are loaded on demand;
only projected counts leave the reader. Missing package component, old/missing
own login, unsupported build and rejected session have distinct fixed errors.
Packaging required inspect-sdk-log.py as data plus an explicit hidden import
of cryptography.hazmat.primitives.padding; the first rank-only executable failure
was diagnosed at own_sdk_login/dependency_missing and fixed, then reverified.

Verification: full suite462 passed in59.221s before final packaging diagnostics;
final shared-client21 passed, final Career-focused183 passed in58.668s, all
three JSX transforms passed, git diff --check passed. Earlier459 run reproduced
the PRE-EXISTING match-index backup-retention flake described below. It is not
fixed by later passing runs; match_index.py was not changed.

Remaining explicit limitations: not anonymous; shared own SDK login must be
locally available and within the prototype's36-hour freshness bound, and
NetEase must accept it. Re-log into own game account once if needed, then exit
the game and retry after cooldown. Captured identity/rank token renewal still
requires an approved fresh capture when expired; neither credential source
renews automatically. Installed image/config/SDK paths and hashes target this
PC/build. Console detailed Career and other-player recent-match import remain
unverified/unimplemented; unavailable fields are labeled rather than zeroed.
Current season follows the capture default; season-rollover renewal matters.
Live use was verified with game closed, not simultaneous play. These are
app-level dependencies, not per-target logins. See CAREER_PREVIEW.md.

## Earlier checkpoint: other-player Career verified; integration in progress

Supersedes the startup blocker below. After handling known lifecycle envelopes
main65534/sub111,113,114,120, a normal authenticated game connection returned
the requested OTHER player's Career: UID1822797559 (KovaaksKid2008), PC,
season19. No game process was running or target account logged in locally.
The Career response matched method, nonce, entire request context, UID and
season; 41 fields, 19 received records (7 lifecycle, 11 unrelated own-avatar
RPCs, 1 requested reply), 825198 received bytes. No extra ready/ACK send,
hotfix execution or runtime salt override was needed. Unknown commands and
sequence reset still fail closed. No new capture or memory access is needed.

A second verified live read projected only the card fields: 27 matches,
21 wins, 526 kills, 118 deaths, 679 assists. Raw responses and credentials
were not saved. The CLI output remains redacted; an optional local
on_verified callback receives data only after complete reply binding.

App integration is underway: one app-owned game reader, existing verified
identity/privacy gate, normal Add/card/bulk paths and existing card display.
The goal remains ACTIVE until shared-session tests, integrated live check,
and a newly packaged Desktop build are verified. The current Desktop build
is still the earlier identity/rank milestone. Shared SDK login renewal and
capture-based identity/rank-session renewal remain explicit dependencies;
target players do not need to be logged in or present locally.

The user fixed the Wi-Fi issue and cleared the pause. No Wi-Fi settings,
profiles, drivers, network resets or captures were changed in this resumed
work. Source updates only so far; no real vault or Desktop changes yet.

## Earlier checkpoint: own game login works; Career startup pending

Normal NetEase authentication AND gateway login are now live-verified using
the corrected post-SDK input described below. The full goal is still ACTIVE.
No game process was running during these checks. No Desktop/provider changes,
capture, process access, game-file changes, role creation, remote-code
execution, C_LOGIN_RELOGIN, credential exports, or real-vault writes occurred.

- Corrected LOGIN request1349bytes (1342body),286-byte ACK; S_LOGIN_AUTH
  successful with unique initialized own role UID1370598422. Receive4398-4401
  bytes on recent checks (including prepare/opening). No OAuth token had to be
  inserted into gas_token. Prior253/SDK502 failures were the stale input shape.
- New `--enter` mode uses ONLY this successful reply's gateway/auth_hostid/
  auth_sn and UID. C_LOGIN_ENTER main1/sub3 is71bytes, device_id/basic_info empty
  as the normal game does. Fresh gateway suite2 handshake,286-byte ACK.
- The gateway first sends nine S_PUSH_CLIENT_HOTFIX records (main4/sub102),
  139188 total content bytes. Their entry5049 envelopes are bounded/decoded;
  contents/signatures are NOT executed, applied, exported or considered verified.
  Then S_LOGIN_RES (main1/sub103) returns errcode0, an actual login_key, and
  info.uid exactly1370598422. Gateway receives142167bytes through that result.
- S_LOGIN_RES.relogin=1 is merely a status indicator here. Static entry7800
  s_login_res reads msg.get('relogin',False) ONLY for logs, not as an auth gate.
  Accept0/1/missing, but require normal successful auth/enter and exact returned
  UID. Never substitute C_LOGIN_RELOGIN or weaken identity/error checks.

New `game_career_probe.py` (research only, not bundled) now sends one224-byte
Career.C_QUERY_CAREER for verified other-player UID1822797559/PC/season19 after
normal own login. Uses entry1833 query_player_career, current returned zone_id,
device_type pc, match_season19; adds nonce and requested context. Any returned
Career must match method, context, nonce, requested UID/season and success code.
Default RPC salt is statically loaded from hash-pinned entry5719 (no values
printed). Default send/receive salt both const3; runtime hotfix changes are
NOT yet assessed. No Career result has been verified or put into cards.

Latest live Career attempt stopped after the first post-login startup record:
main65534/sub114,16bytes, S_ENTITY_DESTROY per entry5053. This is an unhandled
normal engine lifecycle record, not a server rejection of the public UID.
Next: handle the bounded startup lifecycle/envelope sequence without executing
remote code, then consume and validate the actual Career RPC. Do not repeatedly
change credentials or request another packet capture. Earlier first attempt
stopped at record_framing; receive_record now allows an explicit4MiB Career
record limit with <=64KiB cipher chunks, preserving64KiB LOGIN defaults and
reporting unknown flags/size instead of guessing them.

Focused coverage:18 LOGIN +14 SDK-input +13 gateway +11 Career tests pass;
12 config and13 transport tests are separate. Full-suite run before gateway
additions:402 tests, one failure in UNCHANGED match_index_test backup retention;
isolated match-index suite reproduced it (19/20 pass). Final full source suite:
**426 tests passed in66.669s**. The earlier retention failure is a real flake:
a synthetic frozen-clock test produces three distinct backup paths but retains
the first/third and prunes the second. Unsuffixed and numeric collision suffixes
sort incorrectly in unchanged MatchIndex._prune_backups. No unrelated production
backup code was edited; a passing rerun does not resolve this separate issue.
git diff --check and explicit new-file whitespace checks pass (existing CRLF
notices only). All verification command sessions are complete; no live-network
helper remains running.

Entry5053 next-step schemas, read statically: S_ENTITY_CREATE113 body
[(1,eid,12),(2,classname,8),(3,initdata,10)]; S_ENTITY_DESTROY114 body[(1,eid,12)]
(handler misspelled S_ENTITY_DESTORY in the installed descriptor). S_MSG_ACK111
body[(1,ack,6)], S_MSG_RESET112 body[(1,reset,6)]. S_MSG_OPT_STRUCT118 contains
seq:uint32 and is described as each packet's additional message segment;
do not assume it is a standalone wrapped RPC. S_CALL_CLIENT_METHOD119 func:str;
S_SYNC_CLIENT_PROP120 entity_id:int64 + sync_msg:blob. Never execute initdata.

## Earlier checkpoint: production LOGIN and corrected SDK inputs (2026-09-08)

This supersedes the unresolved-login-input statements in the older checkpoint
below. The goal remains ACTIVE; full other-player Career and session renewal
are not implemented. The existing one-step Desktop rank/identity build is
unchanged. New research helpers are NOT imported by the app or bundled.

`game_config_probe.py` now reads four allowlisted installed PAK configuration
files, validating encrypted index/data integrity without extracting files or
executing game code. WindowsGame.ini supplies the actual EN production list:
https://x20na.update.easebar.com/server_pc_prod_serverB . The verified response
advertised34.8.137.117, ports10001-10010, host_number13001. The fourth server-list
column is NOT a gateway port. Native build1.1.3825464.3825464 supplies client
version3825464; PC platform_type is0. See API_RESEARCH.md for hashes and sources.

`game_login_probe.py` implements the normal entry5050 LOGIN schemas and bounded
suite2 records. One prepare-only connection received a valid production
S_LOGIN_PREPARE: no extra args/version requirements. Two subsequent single
authentication attempts using the PRE-SDK bundle were rejected with game
code253 (LOGIN_ERR_AUTH_FAIL); the latter exposed only redacted SDK code502,
subcode0. This does NOT establish HTTP502, invalid target UID, expired tokens,
or successful game login. All sockets closed; no automatic retries. No role
enter/create/init, Career request, game launch, process-memory access, or
attestation bypass occurred.

The rejected input was incomplete: the normal SDK HTTP200 success response
contains base64 UNISDK_LOGIN_JSON. Static inspection of the hash-pinned
NtUniSdkBase.dll establishes updates AFTER that response:

- Function0x416c0 base64-decodes the SDK response. At0x4195b it replaces
  SAUTH_JSON.sdkuid with the response identity through setter0x42d50.
- extra.channel_raw_info is a JSON STRING, parsed at0x41fb2. token is the
  default; nonempty login_token takes precedence at0x42208. At0x422de this
  replaces SAUTH_JSON.sessionid. Both replacement values differ from the
  pre-SDK bundle in the observed own login.
- Optional sdk_token fills an EMPTY gas_token via0x41a71/0x42740. The observed
  response has no sdk_token, so gas_token stays empty. Do NOT substitute the
  OAuth access token. oauth2 is a DICTIONARY, not a boolean. Its access/refresh
  tokens must never be printed. 0x42a40 updates SAUTH_STR, not SAUTH_JSON.

The loader now pairs one unambiguous same-timestamp bundle/response, since the
log writes its response BEFORE a telemetry copy of the request. It also checks
game/channel/device and internally consistent response SDKuid/aid. A newer
incomplete or ambiguous transaction fails rather than selecting an older
account. These are consistency checks, not token signature/server acceptance.
Source timestamp remains2026-09-08 04:19:30; corrected auth body1342bytes.
Expected own role UID1370598422 must still be checked against S_LOGIN_AUTH.
Fourteen new synthetic SDK-input tests plus18 LOGIN tests pass. Full-suite
verification and a changed-input live check are pending at this checkpoint.

The user reported Wi-Fi drops; all game network tests were paused during a
read-only Windows diagnosis. No Wi-Fi settings were changed. Windows reported
user-requested disconnects20:36/20:39 and subsequent successful authentication;
available48h WLAN log had no connection/association/security-failure events.
No causal link to this task was established. The user then said they fixed the
Wi-Fi issue, clearing that pause. Do not claim a Wi-Fi repair by this project.

IMPORTANT incident: an earlier inline diagnostic accidentally printed the
user's OAuth access/refresh tokens into this task's tool output by treating
oauth2 as a scalar. The user was informed. No values were put in source/docs
and no revocation/rotation was performed. Do not repeat those values or share
that output; print only explicitly typed, allowlisted codes/boolean metadata.

## Current research checkpoint: live suite2 transport (2026-09-08)

The goal is still ACTIVE. The Desktop build below is unchanged. New
`game_transport_probe.py` completes a normal independent suite2 key exchange
using public installed distribution configuration and fresh client randoms.
It is research-only, NOT imported by the app or bundled. No credentials, login,
Career requests, game-process access, recording, recovered-code execution,
real-vault changes or Desktop changes occurred in this continuation.

Verification: **13 new transport tests pass** (synthetic RSA keypair, directional
stream state, exact ACK/heartbeat layout, zero/mismatched echo rejection,
preview/no-network behavior, bounds and single-attempt cleanup). Final full
source discovery: **358 tests passed in170.033s**. git diff --check has no
whitespace errors; existing LF/CRLF notices remain. No full Career success is
implied by these tests. No command sessions remain running after verification.

Live evidence: one handshake-only connection sent286 bytes and timed out after
receiving only the28-byte opening. A second connection sent the handshake plus
one encrypted12-byte native heartbeat and decrypted a complete20-byte reply:
main65534/sub103,13-byte body. A third, focused heartbeat check resolved the
remaining field mismatch: the reply's timestamp, server timestamp and
milliseconds are ALL ZERO. This is a readable encrypted game-protocol response,
NOT a verified timestamp echo, account authentication or Career result.
`plaintext_record_verified=true`, `echo_verified=false`, `login_verified=false`,
`career_verified=false`. Do not weaken echo correlation to call it verified.
All three sockets closed. No automatic retry or extra commands on any socket.
The previously observed peer remains34.60.237.47:10203; it is not established
as the normal initial LOGIN server. No further identical heartbeat is needed.

Configuration is now found and validated, rather than a missing key:

- Archive entry3145 `Scripts/core/bootstrap.py` assigns `USE_PUB_KEY=True`
  and `MOS_PUB_KEY` from const7 at customized bytecode offsets96..103.
  The value is **PEM**, not bare base64. RSA1024; SPKI-DER SHA256
  `d37c1ab339cfd920f261b8457f9e5dadac6c076684ba93c51987d2299464401c`.
  Loader validates the full archive hash, module/assignment shape and key pin.
- Native manager constructor0x546dca0 assigns the18-byte parameter from
  RVA0xb41fb78 at0x546df91..0x546dfac. Its SHA256 is
  `e0f8b1c2e1e906a1d4cf345c554852a844cc8bc1411c0b7ba2385885cd4a9c49`.
  It propagates global+0x300 -> child+0x268 -> connection+0x1c8.
  This is not SocialMatrix's parameter; no parameter/key bytes were printed.
- Native0x5487860 confirms ACK uint32 total size, then three uint16-prefixed
  fields: SHA1 proof20, RSA-wrapped receive random128, send random128.
  Proof folds ALL18 parameter bytes over the16-byte server challenge, then
  SHA1. RSA uses PKCS1-v1.5. Each AES-256-CTR key is client random || challenge,
  with that client random as IV, separate continuous directional contexts.
  No private key or another connection's client randoms are recovered.

Remaining work is NORMAL login, not another passive handshake recording:
entry7799 `client_logic.py` has separate login and gateway clients. Entry7800
`login_mgr.py` sends C_LOGIN_AUTH with SDK `sauth_json`/metadata to LOGIN, then
uses successful S_LOGIN_AUTH's auth_hostid/auth_sn and returned zone_list
gate_ip/gate_port/role_uid for C_LOGIN_ENTER. Require expected existing role
UID1370598422 and normal successful auth; do NOT create/init a role or use
relogin/developer/attestation bypass paths. No credentialed request has been
sent by this helper. Source schema entry5050 is available but not implemented.

The shipping login endpoint is the next unresolved input. Entry6243
`PyLoginPanelV3.refresh_server_list` GETs entry7805
`get_shipping_server_list_url`; selects the returned production server with
entry6268 `login_server_data_builder`. The URL comes from package_data's
ShippingDefaultServerURL or the game-instance property, with EN selected for
overseas. Do not substitute an arbitrary gateway/port or the developer portal.
New metadata scan for these symbols completed9717 modules/8 undecodable/one
bootstrap skip; only6243 and7805 matched. Native package-data loader0x68abb30
reads meta.data (32bytes), whose decoded public JSON contains only is_oversea;
it does not supply a server URL. Optional CustomPackageSetting.ini wasn't
found. `Saved/current_connect_info` is base64 JSON, now tag=exit and empty IPs,
not an encrypted record to crack. Installed version.json: Shipping,1.1.3825464,
changelist3825464. No useful current x20/rivals DNS cache entry was returned.

Own SDK source refreshed read-only: current NtUniSdk.log is97,880bytes/124
records, two SDK-shaped JWTs with future exp CLAIMS, no complete sauth bundle.
Scoped sibling NtUniSdk.log.1 is5,244,227bytes/8,711 valid records; its latest
complete sdkuid/gas_token/sessionid/gameid bundle is2026-09-08 04:19:30 (log
timestamp), gameid matches x20naxx2gb. Fields cover gen_auth_info's sauth-based
identity/device/channel overrides. Nothing was exported or used on the network;
signatures, server acceptance and the game-role UID remain unverified. Do not
mistake the current file lacking gas_token for absence from the rotated source.

No game process was present at the last process check. The user has no pending
action; no new restart/capture was requested. This goal turn made substantive
implementation/live-protocol progress, not a repeated no-progress blocker.
See API_RESEARCH.md for the remaining schema details and historical evidence.

## Prior checkpoint: separate Career RPC (2026-09-08)

The one-step Desktop delivery below is unchanged. The full user goal remains
ACTIVE: detailed other-player Career stats and automatic session renewal are
not implemented. This continuation made offline protocol progress, not a new
successful live Career read. Do not call the new decoder live-validated.

New research-only `nex_rpc_probe.py` decodes complete, already-plaintext NEX
records and the avatar RPC envelope. It is NOT imported by the app or included
in the delivered build. It performs no network, recording, game-process access,
recovered-code execution, credential acquisition, or file output. Its thirteen
synthetic tests pass, including four new server-opening cases. Final full source
discovery passes **345 tests in122.841s**; git diff --check has no whitespace
errors (only existing LF/CRLF notices).
No real vault or Desktop artifact was changed during this continuation.

IMPORTANT correction to the next step: a single receive-only TCP connection to
the previously observed game peer34.60.237.47:10203 returned a complete28-byte
server hello: suite2, compression0, field lengths16/0/0. Zero application bytes
or credentials were sent; no login, stats request or key-exchange reply; socket
closed. Native suite2 uses newly generated client randoms encrypted to the
configured RSA public key, then AES-256-CTR contexts. Therefore a passive
restart capture alone is NOT a demonstrated route to readable game Career RPCs.
Do not ask the user for another identical recording or try RC4 on suite2.
`decode_game_hello` recognizes only the observed opening and does not implement
the key exchange. Next: prepare a normal independent game connection using the
installed public-key configuration and the authorized own SDK login source.
Authentication, current credential validity and UID binding remain unverified;
do not replay game login blindly or assume a SocialMatrix JWT is a game token.
The user was told no further game restart is needed right now.

Static call chain, same hash-gated installed archive/native executable:
entry1833 `PlayerAvatar.Server["Career"].C_QUERY_CAREER(url,data,ex_info)`;
entry5312 hashes `Career.C_QUERY_CAREER` and MessagePack-encodes positional args;
entry8739 wraps `{rpc_index,args}`; entry4891 forwards to native
`gameinstance.client_logic.send_gate_msg`. Avatar proto entry5051 gives
main3/sub1 outgoing and main3/sub101 incoming. The plaintext header is seven
bytes, with a masked28-bit total length; flag8 adds a two-byte trailer length.
Native schema encoding uses tag0x61 + LEuint32 RPC index and tag0xe2 + LEuint32
blob length + MessagePack args. See API_RESEARCH.md for exact native locations.

Reply binding is now traced: entry5308 `S_QUERY_CAREER` emits
`(url,ex_info,err_code,err_msg,data)`. Entry1817
`s_query_other_player_career` requires `ex_info.is_career`, selects the target
from `ex_info.career_req.player_uid`, rejects the own-player route here, and
dispatches `query_player_career` to that target's handler. Entry1833 reads
`data.career` (including `player_uid`), `sort_hero`, and `hero_careers` after
checking the error code. The future integration must validate both the request
context and returned Career UID, plus method, platform/season and privacy.
RPC index alone is not identity/correlation proof. Default RPC salts were not
printed; a static hash is not yet a verified live method identifier.

The startup lookup capture's hero_info/proficiency fields contain cosmetics
and proficiency, not the missing aggregate K/D/A. The older successful
`career-netsh-a7910f664b944d5ba0e4d88f63c51209` was re-audited: all161 packets
are complete/in scope, but its only flows are SocialMatrix12101 and SDK21308,
not the main game gateway. It cannot supply the missing game handshake.

No recorder or game is running at the latest check; Netsh reports no current
trace. Only the receive-only opening check above contacted a game endpoint.
No new capture, launch, game modification, authenticated request or stats call.
Do not repeat the old fixed-IP startup recording blindly: one lookup pass
lacked the handshake and the restart pass omitted the game gateway. A next
recording would also need a demonstrated solution to the suite2 key-state gap.
Local
`netsh trace show capturefilterhelp` does not advertise TCP.Port/process filters;
do not invent them or silently remove the IP restriction. No installed
dumpcap/tshark/Npcap was found in the checked standard paths/services.

## Delivered milestone: one-step UID/IGN cards (2026-09-08)

The user expanded the goal: restore normal Add/Refresh by UID or IGN, with no
per-target local login or extra linking step, NetEase only, including available
identity/rank/Career data. The Codex goal is ACTIVE and not achieved. The last
goal turns made progress (working requests, source changes, tests and delivery);
there is not a repeated no-progress blocker audit. The new one-step flow IS
shipped. Full other-player Career and automatic session renewal are NOT done.

The existing `Marvel Keeper - NetEase Career.lnk` Desktop shortcut now points to
`C:\Users\kylep\OneDrive\Desktop\Marvel Keeper - NetEase Player Lookup 2026-09-08\`
containing `MarvelRivalsAccountTracker-Career.exe` and `READ ME.md`.
EXE size **23,207,228 bytes**, SHA256
`21a72363b1a4042bf7eb6b5b8ea90029d9161263f7e06ae6ec5006cb9994e16b`.
Identical source build:
`dist/career-20260908-054804-d363de73/MarvelRivalsAccountTracker-Career.exe`.
Metadata: `local-netease-player-lookup`, NetEase-only,
`built_at=2026-09-08T09:48:04Z`. Source/copy hashes, shortcut target and working
directory match. Original capture/archive/own-UID arguments are preserved;
no admin setting was added. Old Desktop folders remain untouched; prior shortcut
backup is `build/career-iq4gcv15/previous-desktop-shortcut.lnk`.

Normal New account > UID or exact IGN > Create resolves/fills identity, platform,
current rank, highest returned peak and available ranked-season match/win counts.
Refresh and bulk refresh use the same shared session. Editing UID/IGN/platform
also refreshes as part of Save. No per-target local login or extra linking step.
See CAREER_PREVIEW.md for the user guide. No real vault was changed by Codex.

Verified live with the existing approved own session, without game/admin/new
capture: `svc-user` CmdSearchUser15 resolved KovaaksKid2008 to UID1822797559;
CmdGetUser7 returned the same exact player identity; `xyq-namecard/GetData`
rank_game_season returned that target's season19 One Above All5545, current
season peak5570, highest returned peak5601 (season5), ranked record27 matches /
21 wins. These values differ from
the session owner's ranks. Target1822797559 came from the user's previously
supplied profile/IGN research, not the unrelated earlier UID10826896958.
GetUser echoes the exact zone_UID aid; GetData rank replies do not echo UID,
so rank identity remains selected-request/fresh-RPC binding, not an invented echo.

Static evidence: entry3010 SvcUser DataCmd GetUser7/reply8, SearchUser15/reply16;
entry3012 _get_svc_user sends {aid: zone_UID}, search_user sends
{name,cursor,limit,host}. Entry7609 accurate_search_player uses cursor0/limit10;
entry4606 uid_2_aid/aid_2_uid establish zone_UID formatting. The accepted
session advertises svc-user main_cmd4; use the fresh advertised module, not a
guessed fixed route. Normal SvcUser calls use outer(main_cmd,0) and body.cmd.
Returned target config_server login_os is string"1" and PC overview visibility
is explicitly0. Entry5465 enum OPEN0, SELF1, FRIEND2, TEAM3, CLAN4; entry4617
get_permission uses platform-suffixed CareerOverviewIsVisibleToOther key,
then legacy key, then0. No relationship/GM bypass is implemented.

Full other-player Career stats remain unverified. Entry1833 sends operation
query_player_career through the separate game C_QUERY_CAREER RPC. One bounded
test of that operation over working SocialMatrix returned service500; it was
not retried or replaced with a query_my_* request targeting another player.
The public player lookup work must not be declared completion of full Career.

Implemented: career_socket_probe.py now has explicit player_ranks,
player_profile capability-test, player_search and player_user operations;
the CLI previews unless --send. socialmatrix_capture_probe.ServiceReplyError
exposes only numeric status/layer. 22 socket tests pass. career_provider.py
adds public identity projection, platform/visibility selection, exact IGN
matching, per-identity bounded30s card cache and shared-session card reads.
app.py/JSX adds one-action auto-refresh on Add, canonical name/UID/platform
fill, identity-race binding and separate unavailable-detailed-stats labels.
The old Add connected PC card controls are removed from the optional explorer.
Invalid IGN is rejected before session/network loading. Platform-specific privacy
restrictions prevent further rank/Career reads; ambiguous names never guess.
Optional battle_count/win_count are projected to rank.record matches/wins for
the selected normal-ranked platform/season only. Missing/invalid/inconsistent
counts are omitted, never zero-filled; full Career totals remain separate.

Verification: **332 tests passed in113.710s**, all three JSX Babel transforms
passed, and git diff --check found no whitespace errors. Synthetic browser
checks covered one-action UID and IGN Create, identity edit+refresh, restricted
profiles, cards/table/ladder. Checks caught/fixed a false Unranked label for
unavailable ranks. The additional ranked-record line is readable in card and
expanded table layouts. The final source EXE passed live UID-only Add and the
identical Desktop EXE passed live IGN-only Add, both verifying expected UID
1822797559, PC, rank5545, peak5601, record27/21 and persisted whitelisted metadata
in SEPARATE synthetic vaults. Missing detailed Career data is explicitly labeled.
Both packaged helpers shut down; no real account/vault was opened or modified.

### Research captures from the user's normal lookup (both stopped)

Two exact-two-peer captures are retained outside Git in local diagnostics:

- `player-career-331bbce8fc40411c87894282cc8aad79`:90s midstream lookup,
  08:10:18–08:11:48 UTC, normal start/stop success. GamePID21752 used
  34.60.237.47:10203 and SocialMatrix35.236.255.68:12101. Outgoing game bytes
  were contiguous, but the game reply had two truncated packet prefixes and
  no connection handshake. Do not claim it was decoded or fill its gaps.
- `player-career-1341e57ffb4d40d0bed16e5a7bf06a6b`:launcher-start pass,
  08:18:19–08:21:06 UTC. GamePID8284 exited before the180s deadline; the helper
  reported that failure and stopped its own recorder successfully. All126 packet
  events were complete and within the two-IP scope, but only SocialMatrix was
  present. Full handshake decoded35 inbound/34 outbound records, including
  normal target GetData namecard/ranks, hero_info and item/proficiency reads.
  No target full-Career call or game-gateway connection to34.60.237.47 was
  captured. A changed gateway is possible but NOT proven; other peers were not
  retained. No automatic third capture or new user action is pending.

`scripts/capture-player-career.ps1` defaults to a preview, fingerprints scope,
and only records with explicit -Record plus the reviewed fingerprint. Normal
mode90s requires the exact game/current peers; -FromLauncher180s requires game
closed at start. Fixed physical IPv4/TCP peer filters, max32MB, no persistence,
stop only its named session. 28 mocked OS fixture cases pass across both
PowerShell engines. Never treat a preview's capture_scope_verified=false as
an offline packet audit. Do not broaden scope or restart recording automatically.

`inspect-netsh-career.py --player-lookup --inspect-truncated` is an explicit
offline-only diagnostic: full recognized IPv4/TCP headers and the NDIS truncated
flag are required to inspect actual payload prefixes. Production decode remains
strict and default old-peer scope is unchanged. Missing bytes are counted, never
padded, and disjoint stream runs are never joined.

Remaining full Career lead is the separate game RPC: entry1833
PlayerAvatar.Server["Career"].C_QUERY_CAREER; installed proto entries5048/5050/
5051/5053–5055 and transport5238. Inspect statically if useful, but do not execute
recovered code or resume process-memory/anti-cheat bypass work. A further capture
would need a concrete plan accounting for the game gateway at startup, not a
blind repeat of the same missing-handshake/missing-peer recordings.

Both recorders, packaged test processes and synthetic UI server/tab are stopped;
port55032 has no listener and no Marvel game process was running at last check.
Normal packaged lookups succeeded with the older approved session while the game
was closed; no admin was needed. Shared session renewal/rollover remains manual.

All raw
SvcUser/social/config/third-party account fields stay in RAM and are discarded;
only whitelisted identity, visibility and rank/stat projections reach the app.
Do not print/store session material or raw third-party-account fields.

## Previous delivery: NetEase-only own-rank/card build (2026-09-07 evening)

This checkpoint supersedes the older Career-only delivery and blocked/active
goal notes below. The user asked to continue toward direct NetEase card rank
and stats refresh. That path is implemented, tested live, and delivered. There
is no active Codex goal at this checkpoint. Do not restart capture, the game,
or the previously denied process-memory route merely to resume this work.

The existing desktop shortcut `Marvel Keeper - NetEase Career.lnk` now points
to the NEW folder:
`C:\Users\kylep\OneDrive\Desktop\Marvel Keeper - NetEase Ranks 2026-09-07\`
containing `MarvelRivalsAccountTracker-Career.exe` and `READ ME.md`.
EXE size **23,194,681 bytes**, SHA256
`b7ac2d910d04345b847dbdb006c164a8f955a5003c1d07c5cc7f5d41289c196a`.
Identical source build:
`dist/career-20260907-190509-1ecdbc4a/MarvelRivalsAccountTracker-Career.exe`.
Build metadata is `commit=local-netease-ranks`, `stats_provider=netease`,
`built_at=2026-09-07T23:05:09Z`. The previous Career-only desktop folder remains
untouched. Source/copy hashes and shortcut target, working directory, and
preserved capture/archive/own-UID arguments were checked. No token, capture,
game archive, or existing vault was copied into the new desktop folder.

Normal card refresh and bulk **Refresh rank & stats** now use the connected
own-account NetEase session, with NO Tracker/RivalsData fallback. The package
also remains NetEase-only if opened without shortcut arguments: it reports
missing setup rather than calling Tracker. `cloudscraper` is excluded from the
bundle; legacy Tracker source is retained for ordinary legacy/source launches.
Optional user-opened profile links and GitHub update checks are separate.

The working rank read is SocialMatrix `xyq-namecard / GetData`, POST body:
`{host: <own zone>, tag: "player", id: "<zone>_<own UID>", fields: "rank_game_season"}`.
It uses the same approved session as Career. The own-player GetData template
was observed in a successful captured request/reply; the specific rank field
comes from installed game entry1833 `s_request_rank_data`. GetData does NOT
echo UID: identity evidence is the own-ID request, fresh correlated RPC reply,
and independently UID-verified Career profile, not an invented response UID.
See API_RESEARCH.md for schema evidence and installed-code references.

Live UID1370598422 / PC result: capture-default internal season19 returns
level14 / **Diamond II**, **4350 points**, `fixed_finished=0`, placements0/10.
It is explicitly ESTIMATED, not a completed placement rank. Its upstream rank
update timestamp is1787093892, not today's fetch time. Highest returned PC peak
is season17 level21 / **Celestial I**, floor5016 points. Rank names use the
ordered one-based division mapping; independent user/in-game confirmation of
those two labels remains pending. Season19 aggregate totals are zero. The
separately verified season17 profile remains47 matches/20 wins/661 kills/
293 deaths/828 assists; changing panel season does not substitute historical
rank for the card's capture-default season.

Only matching UID1370598422 / PC cards can refresh with this session. A
metadata-only real-vault check found zero such cards before delivery. Use the
shortcut, unlock normally, expand **NetEase Career**, click **Add connected PC
card**, then refresh that card. This creates a local UID/PC card with blank
credentials and leaves other accounts alone. An existing unknown-platform
card can use **Use PC for this card**. No real-vault changes were made by Codex.

Card refresh saves selected aggregate/rank/placement metadata, not raw replies
or tokens. Higher saved lifetime peaks survive; missing/unverified rank fields
do not erase saved ranks. Tests cover UID/platform edits during requests,
vault locking, partial rank/history failure, exact-season cache reuse, and
unconnected-account errors. Panel history remains a process-local snapshot;
it is not imported into the persistent match index.

Verification: **304 source tests passed** (47.980s), all three JSX Babel
transforms passed, and synthetic browser checks covered cards/table/ladder and
the panel. Those visual checks caught and fixed a narrow-table current/peak
column issue and estimated-rank wrapping. One prior full-suite run exposed an
existing retention-test flake; isolated and subsequent full runs passed, with
no match-index implementation changes. The final EXE passed `--live-card`
smoke testing: own profile/rank reads and persisted projected card values in a
SEPARATE synthetic vault. A second smoke test of the desktop copy verified
startup, NetEase-only mode, capture decoding, and bundled JSX/source hashes
without another network request or account write. Archive checks confirmed no
cloudscraper module, ETL capture, or game archive in the bundle.

Owned packaged test processes and the synthetic UI server/tab are closed;
port55031 has no listener. The game was not running during these successful
live reads. No recorder was started. Normal refresh needed no administrator
permission. Synthetic test scratch and older builds were retained.

Remaining work: session renewal/connection setup, separate sessions for other
own accounts, season-rollover handling, independent rank-label confirmation,
and optional richer history persistence. The current game-issued session can
expire or be invalidated; automatic renewal is NOT implemented. Continue to
preserve saved values and report missing-session errors, not silently fall
back to third-party stats. CAREER_PREVIEW.md is the current user guide.

## Historical API research checkpoints (starting 2026-09-04)

### Delivered: tested NetEase Career desktop preview (2026-09-07)

The own-account API research/integration milestone is achieved. Independent
socket authentication and Career reads now work, have been verified against
game data and a visible game screen, and are integrated into a tested desktop
preview. This is NOT an anonymous UID lookup, a fixed Tracker collector, a
NetEase rank provider, or an automatically renewed login session.

Delivered shortcut:
`C:\Users\kylep\OneDrive\Desktop\Marvel Keeper - NetEase Career.lnk`

Delivered app and instructions:
`C:\Users\kylep\OneDrive\Desktop\Marvel Keeper - NetEase Career 2026-09-07\`
contains `MarvelRivalsAccountTracker-Career.exe` and `READ ME.md`.
EXE size 24,010,585 bytes; SHA256
`fd64582d244b4f233fbf56bfdaf1edb740033c6cb7d695ff8bf184829650cada`.
Original identical build:
`dist/career-20260907-132137-1c84c4a2/MarvelRivalsAccountTracker-Career.exe`.
Source/copy hashes and shortcut target/arguments were checked. No capture,
token, game archive or existing account data was copied into the desktop folder.

Use the shortcut (it supplies the three local Career flags), unlock the vault,
expand NetEase Career below the toolbar, and click Refresh from NetEase. The
panel is bound to own UID 1370598422 / PC. `career_provider.py` keeps session
and projected stats in process memory. It changes no saved account/rank/UID or
match-index records. Existing normal rank-refresh buttons still use Tracker.
There is a 30-second refresh guard, no retries, explicit stage errors, and
session expiry checks. UI cannot supply another UID, host, token or local path.

`CapturedCareerProvider.refresh()` opens bounded normal socket requests for
profile and page-0 match summary. Only seasons observed in own captured Career
requests are selectable (19,17 here); the last observed season 17 is default.
Both were tested live. Season 17 profile: 47 matches, 20 wins, 661 kills,
293 deaths, 828 assists; history returns 50 own-player rows. Only selected
numeric fields reach the UI, never other participant identities or session
material. Season 19 profile has zeros; it is not all-time inactivity.

Computer Use visual verification: the current game screen shows UID1370598422,
S8.5 Competitive, Rocket Raccoon with 74 KOs and 314 assists. Those exactly
match captured own `query_my_hero_career` replies for internal season17,
hero1023/game_mode2/play_mode0. Hero `total_match_count` is weighted float
10.101926385710513 (UI displays10); do NOT treat it as the integer aggregate
profile count or infer rank. The app uses integer profile totals, not those
weighted hero fields. Game PID24284 and its original socket local57360 remained
open at the post-test metadata check.

Verification: **284 source tests passed** (55.143s), Babel transforms both
modified JSX files, git diff --check clean apart from normal CRLF notices.
Synthetic UI fixture visually rendered counts and 50-row table. A packaged
live smoke test independently decoded the capture and retrieved the same
season17/50-match result with a separate synthetic vault and zero account
writes. A second smoke test launched the copied desktop EXE and verified the
bundled UI matches source and capture/session decoding works there too.

Build helper `scripts/build-career.py` uses fresh build/dist folders, bundles
only three offline helper scripts and optional analysis libraries, and never
deletes prior builds. Initial relative-data-path packaging error was fixed to
absolute source paths. `scripts/smoke-career-build.py` now shuts down its owned
app through `/api/shutdown`: killing only a PyInstaller parent left a child
alive on the first run. That child was explicitly shut down; corrected helper
cleanup verified, no packaged test processes remain. The separate UI-fixture
server/tab were also closed. No live recorder is running. Test scratch contains
only synthetic vault data under build/ and was retained, not deleted.

See CAREER_PREVIEW.md for session limitations and build/run instructions.
Future work, if requested: user-friendly session renewal/connection setup,
other own-account sessions, richer stats/history persistence, and separately
verified direct current/peak rank fields. None of those is silently enabled.

### BREAKTHROUGH: own-account Career socket lookup works (2026-09-07)

The user resumed with **ready**. The goal is ACTIVE, not blocked or complete.
Manual launch during the approved 90-second Netsh recording succeeded. Game
PID 24284 started 12:52:50.778 local; launcher PID 26160 was already open.
The game remains open at the last check. Do NOT restart or capture again just
because earlier checkpoints below were blocked. No recorder is running.

Successful run: `career-netsh-a7910f664b944d5ba0e4d88f63c51209` beneath
`C:\Users\kylep\AppData\Local\MarvelAccountKeeper\diagnostics`.
Start 16:52:38.1885246Z, stop 16:54:09.1895022Z; child PID23568 exited;
both native exit codes0 and transcript positively confirms named-session stop.
ETL13,107,200 bytes. All161 NDIS packets independently parse as IPv4 TCP at
the two approved IPs, no out-of-scope/unsupported packets. Packet scope is now
verified. Provider process IDs do not prove packet ownership, but observed
TCP tuples match game PID24284 metadata. One outbound LSO frame has IPv4 total
length0; the offline reader honestly marks its length as captured, not wire.

`scripts/inspect-netsh-career.py` is the offline ETL scope gate. At
35.236.255.68:12101, local192.168.4.39:57360, both TCP handshakes and one complete
stream in each direction exist, no conflicting overlaps. The other approved
peer8.221.212.39:21308 has a different, still-unidentified protocol; ignore it.

`socialmatrix_capture_probe.py` now decodes the real SocialMatrix connection:

- Archive SHA256 remains4dbe9d3246a05d4a402373cc11c03a938692db9bc16aaaf939ddff4e8682d22e.
  Parameter from hash-gated entry5448 remains IN MEMORY ONLY, never copied to source.
- Server opening24 bytes, suite1/compression0, nonce/scKey/csKey lengths4/4/4;
  client acknowledgment14 bytes, signature/extra lengths4/0/0.
- Client signature exactly matches installed code: cyclic XOR, then
  h=5381; h=34*h+SIGNED_CHAR(byte), finally uint32 little endian. Not standard DJB2.
- Directional keys are four-byte cyclic XOR folds of the respective hello key
  data with the distributed parameter. Standard continuous RC4, no dropped bytes.
- 36 inbound and35 outbound records, all `<IHB>` lengths valid; total plaintext
 154438in/18882out. No plaintext/key/token file was written or printed.
- First record each way is JSON negotiation: payload_type1, uni_lz4.
  Later records are byte0 + JSON, or byte1 + uint32LE uncompressed length + LZ4
  block. Decoded all20 compressed envelopes using optional lz4==4.4.5.
- Outbound `body.payload` is base64(JSON); inbound `body.str_payload` is JSON.
  Career request `post_body` is base64(JSON); reply `response_body` is
  base64(gzip(JSON)). Pair using exact rpcid IN MEMORY, not order guesses.
- All14 captured xyq-career requests matched own UID1370598422 and successful
  service200/application200 responses. They include query_my_player_career,
  query_my_match_summary, uid_query_my_match, query_my_hero_career,
  replay_query_my_match and sort_my_hero. Do not replay sorting or unrelated
  social/namecard operations merely because they are present in the capture.
- Own profile response UID matches1370598422, zone11001, PC, selected season19;
  that season's aggregate is allzero. Summary page0/page_size50 contains50
  matches. First match is season17, own player36kills/6deaths/7assists,
  timestamp1782187019, duration817.709435sec. Do NOT treat season19 zeros as
  all-time inactivity or overwrite saved ranks from these fields.

Actual standalone request verification:

1. Captured JWT's aid matches its socket setup and contains own UID; unexpired
   (about7days lifetime). TCP route35.236.255.68:12101/rc4:true/tls:false.
   Separate TLS websocket route is sm-x20-1y41wdfk-prod-ws.nie.easebar.com:12104.
2. TLS hostname/certificate preflight succeeded at that hostname, DNS34.48.87.69,
   TLS1.3. One HTTPS Career POST returned403; HTTP route is NOT working. No
   HTTP retry/alternate-route guessing or authorization bypass was attempted.
3. New `career_socket_probe.py --send` successfully opens exactly the captured
   TCP peer, normal secure opening + hello + token setup, then ONE captured
   own-profile query (wire1:2, body.cmd0). Server accepted session and replied
   with matching own UID and equal captured/live profile totals. Connection
   closes in finally. No retries/logout/process access or token persistence.
   Setup is wire1:3/body.cmd0/payload empty, version1.5.9. Fresh rpcids are used;
   server supplies a fresh streamid. Runtime request includes that streamid,
   not the old captured one. RC4 counters remain continuous per direction.

The socket probe defaults to preview; its arguments are trace path, installed
archive path, --own-uid1370598422 and optional --send. Trace is never uploaded.
8 synthetic socket tests +11 offline decoder tests passed; new9 NDIS tests
passed earlier. Full source suite running at this checkpoint, previous244.
Tests never use the real capture/archive/network; RC4 verified against RFC6229.

NEXT: finish synthetic/full checks, integrate the verified own-session provider
into the application with clear season/session-expiry labels, keep credentials
in memory or existing approved vault handling, and build/test a desktop copy.
No new UI/provider integration or desktop build yet at this checkpoint. Existing
Tracker rank refresh cannot be represented as repaired by this Career probe.
Need explicit observed rank fields before mapping current/peak rank.

### Goal blocked on a coordinated manual Launch click (2026-09-06, 04:29)

The goal is now **BLOCKED, not complete**. Across three consecutive goal turns
the needed coordinated launch has remained unavailable: recorder preparation,
an empty timed recording, then an attempted agent-controlled launch. Safe UI
preparation is now complete, but there is still no game process or handshake.
No further automatic capture/restart attempts should run until the user replies
that they are ready at the yellow Launch button. This is a launch/readiness
blocker, not missing approval for the already-reviewed two-IP scope.

Computer Use successfully brought up Steam's Marvel Rivals library page and
clicked Steam PLAY, creating **MarvelRivals_Launcher PID 26160** at 04:24:50.914.
The launcher is now open at its yellow LAUNCH button. A fresh screenshot-backed
click on that button while recording had no visible effect; subsequent process
checks found no game. Cause is UNPROVEN: do not assert anti-cheat, Windows
permissions, bad UID or a credential problem, and do not bypass input barriers.
No login/security dialog was automated. The Steam launch helper initially
reported no target window, but refreshing list_windows found the real Steam
CEF-owned window; that recovery did NOT require launching Steam again.

Second same-scope Netsh run:
`career-netsh-f9d42a8768c441baa1551758034e5921`, child PID 26612 (now exited),
session `MAKCareerf9d42a8768c441ba`. Started 04:26:14.451, stopped 04:27:44.624.
Both native exit codes 0 and transcript confirms successful named-session stop.
ETL 4,194,304 bytes; report 27,350; transcript 1,771. All 89 process observations
had no game, no metadata errors. Independent ETL metadata check again finds no
NDIS packet events: one count event, six filter rules, 1,098 other Windows trace
metadata events. Files stayed local; no packet decoding/replay or saved-account
changes occurred. Both Netsh experiments are stopped; do not leave the user
thinking a recorder is armed or running.

Next user interaction: when they explicitly say ready, start the recorder from
the existing launcher stage, confirm READY, then ask them to click yellow Launch
ONCE and open own Career on the same account. Do not ask them to close/restart
again while they are already at the launcher. Refresh process/window state first;
UI handles are ephemeral. The user did not respond to the last timed-click cue.
Source/test state is unchanged from the previous **244 tests passed** checkpoint.
There is still no verified Career session/response, provider or new desktop build.

### First Netsh recording completed without a game launch (2026-09-06, 04:21)

The approved two-IP experiment DID run. Read-only polling observed PID 19880
exit and Steam remain running; the agent started the helper through normal
RunAs (no execution-policy or persistent privilege changes). Child PID 4892
reported READY, and the agent told the user to press Launch. No game process
appeared during the 90-second window or at the 04:20:50 check. Do not assume
the user reached the final launcher button merely because the old game closed.
For another attempt, wait for an explicit launcher-ready reply before starting
the timer. No automatic repeat or additional capture is running.

Run `career-netsh-da4ceb3e9a9e4ce9a109131768f736b5`, named session
`MAKCareerda4ceb3e9a9e4ce9`, started 04:18:25.752 and stopped 04:19:56.629.
The child exited, transcript confirms successful named-session stop, both
native exit codes are 0. ETL 5,242,880 bytes; report 27,634; transcript 1,770.
All 90 metadata observations had no game; no query errors. Files remain local
under the normal diagnostics root. No login/UID/vault changes occurred.

Independent Get-WinEvent inspection found ZERO NDIS packet events (1001/1003).
There are one filter-count event, six filter-rule events, and 1,084 Windows
system/process/trace metadata events. Rule-value bytes match the two approved
IPv4 addresses, TCP protocol 6, Ethernet IPv4 and 65,535-byte cap. Empty packet
output is not proof of filter enforcement, a handshake or Career access.
System metadata exists despite no scenario/provider extras and perfMerge=no;
only event descriptors and filter configuration were inspected, not unrelated
event contents. No packet conversion/decryption was needed or attempted.

Important visibility caveat: non-elevated `logman query -ets` and
`netsh trace show status sessionname=...` did NOT expose this elevated named
capture while the child was live. Do not declare such a child stopped or
restart merely from those negative queries. Use its process, transcript and
event/report evidence; the final stop is positively confirmed here.

Full suite now **244 tests passed** (56.964 seconds). The new Netsh fixture
executes 11 cases in each PowerShell engine. The existing PktMon hold remains.
The actual API objective remains incomplete; next needed input is the user's
current launch/loading screen and a coordinated launch, not another UID.

### Netsh scope approved; waiting at launcher (2026-09-06, 04:16)

The user replied **yes** to the alternative 90-second local Netsh capture of
all TCP ports at exactly **35.236.255.68** and **8.221.212.39**. This is approval
for that experiment, not arbitrary added peers. No Netsh recording has started.
The agent asked the user to close Marvel Rivals and leave Steam/the launcher
open WITHOUT pressing Launch, so startup is inside the recording window.
The latest process check still showed PID 19880. Wait for the launcher-stage
action; do not start a timer while they are still closing the game.

Prepared `scripts/capture-career-netsh.ps1`: default nonmutating fixed-scope
preview; explicit fingerprint required for Record; no game may run at start;
administrator check with no self-elevation; unique named session; physical
IPv4/TCP only, explicit Ethernet.Type plus address filters, all TCP ports,
65,535-byte per-packet cap, 32 MB single file, 90 seconds, no scenario/provider
extras, persistence, report package, installation, game launch or login changes.
Cleanup stops ONLY its unique named session, including uncertain startup.
Report/transcript stay in a unique local diagnostics folder. Scope, handshake
and Career verification remain false pending independent inspection.

Current fingerprint:
`41e2fcd065420f225b6bcbb3980232a9229af2d23acdd16245d87824bf05a7fe`.
Run once with `-Record -ReviewedScopeFingerprint <fingerprint> -RunId <32hex>`
through the already-used normal RunAs workflow AFTER the game is closed. The
known transcript path is `diagnostics/career-netsh-<RunId>/capture-console.txt`.
Tell the user READY only when that transcript says NETSH CAPTURE READY, then
they press Launch and open their own Career on the same account. Do not rely
on old TLS keys for a new normally launched process.

New test covers 11 mocked lifecycle/guard cases in BOTH PowerShell 5.1 and
7.6.5 (22 fixture executions), all passed. Native packet capture itself has
not been tested. Previous full suite remains 243 passed; no new full run yet.
The PktMon live-recording hold remains intact; do not bypass it.

Read-only CIM TCP metadata also discovered **34.60.237.47:10202**, missed by
the old hardcoded 10200 candidate-port filter. This is a possible protocol
lead, not proven Career traffic. It is OUTSIDE the approved two-IP Netsh scope
and was deliberately not added. Current own diagnostic UID remains 1370598422;
no saved account edits, verified Career response, integration or desktop build.

### Current capture hold / restart timing (2026-09-06, 04:04)

**Do not start another PktMon recording: the newest capture contains traffic
outside its reviewed peer filters.** Live `capture-career-handshake.ps1 -Record`
is temporarily disabled at entry; previews remain available. This is a real
scope-enforcement discrepancy, not a verified Windows/driver diagnosis and not
a reason to inspect unrelated payloads or increase capture scope silently.

The user clarified they had not restarted during the first 90-second run and
asked when to do so. The agent started another same-scope recording and said
GO immediately after READY. Capture `career-handshake-dc9ddd76e78748bf915131e7c2d3a328`
ran 03:49:04.271-03:50:34.949. Child PID 13716 exited; transcript in
`career-command-c3c02fabfc8d4eb793cd63b8aca38001/capture-console.txt` verifies stop,
filter removal and conversion with no ETL events lost. ETL 552,093 bytes;
PCAPNG 561,316; report 87,662. Native/independent packet count: 73, no drop
events or parse errors. Of 163 observations, 122 had no game, beginning
03:49:32.255. No replacement game appeared before recording ended.

The user DID close/relaunch: launcher PID 26276 started 03:49:43.781; replacement
game PID **19880** started **03:51:55.763**, after the recording. They do not
need to repeat that step now. Do not reuse old PID 16172 as a current process.
At 04:04 current game metadata showed only 35.236.255.68:12101 (local 60416)
and **8.221.212.39:21308** (local 56469), not the older .163 peer. No 10200
connection was returned. UID/login-screen state after restart is not independently
verified; no saved account identity was changed.

Header-only findings: 21 snapshots belong to the original three peers; **52
incoming port-443 snapshots belong to four other remote IPs**, already present
four seconds after capture start (not merely after cleanup). No SYN, eight FIN,
three RST snapshots. ETL packet events carry filter IDs 4 and 7 for the unexpected
traffic, but there are no ID-100 definitions to resolve those IDs; the console
showed only six configured peer filters. Cause remains unproven. Unrelated
payload inspection stopped when this was discovered; no files/keys were uploaded.
Keep these files local and do not feed them through whole-file payload/decryption
checks. Earlier scope conclusions must not be generalized to this recording.

Prepared `-PriorCaptureReport` support to move the timer to the launcher stage:
only a <=1 MiB local diagnostics report, <=30 minutes old, matching the explicit
previous fingerprint/PID/duration/restart flag and recomputed exact peer scope;
local IP must still exist and no game may currently run. Schema 2 truthfully
records historical peer source, no current initial connections, and observed
replacement PIDs only. This mode has NOT run live. Its real preview correctly
refused because the replacement game had already started. Six new mocked cases
passed in both PowerShell versions; full suite before the recording hold was
242 passed. Final full suite including the live-recording hold: **243 passed**
at 04:07. Mocked lifecycle tests bypass the hold only with all OS mutations
mocked; that is not validation of real PktMon scope enforcement.

Read-only alternative preparation: no installed Wireshark/dumpcap/Npcap found.
Native `netsh trace` is available and reports no active trace. Its local help
supports named sessions, physical-adapter capture, IPv4-address lists,
Protocol=TCP, bounded files and named-session stop, but exposes no normal TCP
port filter. An alternative 90-second capture limited to the CURRENT game-server
IPs would cover all TCP ports at those IPs: obtain explicit approval for that
different scope before starting. Use no scenario/provider extras, no persistence,
no report package or installer, a unique local output and finally/stop on its
own named session. Do not substitute the stale three-IP scope or claim this
alternative has been tested live. No stats/API verification or integration exists.

### Latest approved recording (2026-09-06, 03:46)

The user replied **yes** to the 90-second local fixed-peer capture scope. The
permission blocker below is superseded for that experiment. A fresh preview
matched the exact prior fingerprint/endpoints/PID/restart flag, and the agent
successfully ran `capture-career-handshake.ps1 -Record` through normal one-time
RunAs. No manual command paste or persistent admin setting was needed. The
stored goal tool still reports `blocked`; do not confuse that scheduler status
with missing permission for the completed experiment or API completion.

Capture: `career-handshake-d06d92e16f1f4537b0b496d140a99335`.
Started 03:42:32.573, stopped 03:44:03.393 local. ETL 45,307 bytes; PCAPNG
33,144 bytes; schema-1 report 344,547 bytes. Transcript lives in
`career-command-bd5ae3cbf1ca4649af44a3ab4d7946cf/capture-console.txt`.
Elevated child PID 20832 has exited. Native stop reported no ETL events lost,
all temporary filters were removed, and conversion succeeded. The report counts
113 packet/drop events: independent ETL grouping finds 111 ID-160 events and
two ID-170 events; the PCAP has 111 packets. Do not describe the two drop events
as trace loss, or conflate 113 events with 111 formatted packets.

All 139 metadata samples kept game PID 16172, no absent/replacement process,
no metadata errors, and the same three original TCP tuples. Twelve outside
peers were observed as metadata only; no new filters were added. Independent
PCAP parsing found zero SYN/FIN/RST snapshots and no parse errors. Payload:
10200 inbound 1,281 bytes in two runs / outbound 44 in two;
12101 inbound 93 in one / no outbound; 21308 outbound 1,458 in one / no inbound.
All are opaque with no hello layout, MOS/MessagePack match, Career marker or
tested compression match. No overlap conflicts. This is new bidirectional
traffic, not a connection opening, usable token or verified Career response.

The agent told the user READY while recording, then to restart normally on
the SAME account and open own Career/Statistics/History. The game did not
restart during the capture; a later check still shows its September 5 17:02
start time. Whether the user attempted a title-screen reconnect/tab change is
not known. An asynchronous timing question is pending. Do not blame the UID,
automatically retry the same window, force-close the game, or assume the
requested UI action happened. Coordinate the next restart/recording with the
user, retaining the reviewed scope and rechecking current endpoints first.

No source changed in this recording turn; latest suite remains **236 passed**.
No packet decryption, own-profile API verification, production integration,
vault edit or desktop build. The original objective remains incomplete.

### Current goal state: blocked on capture approval (2026-09-06, 03:41)

The same pending fixed-peer-capture approval has persisted across three goal
turns. Offline preparation and checks are complete for this next experiment;
no meaningful live/authentication verification can proceed without that user
decision. The goal is now marked BLOCKED, not complete. At 03:41, a successful
session query listed no PktMon capture; the newest report/PCAP still belong to
the 03:07 run (`career-96e5877cc37f44ae8d26a0ecaeeae20c`, PCAP 12,296 bytes).
No new capture was started. Resume on explicit approval of the 90-second local
three-peer scope, re-preview the current endpoints, then use the already
verified normal RunAs workflow. Do not treat an automatic continuation as consent.

### Latest offline protocol checkpoint (2026-09-06, 03:39)

The requested 90-second fixed-peer recording has NOT been approved or started.
Do not interpret an automatic goal continuation as that approval. The previous
turn made real progress (capture helper and transport correction); this turn
completed further authorized offline protocol analysis. Live verification is
still missing, and the goal remains active/incomplete.

Archive entry 43 now establishes the candidate opening layout exactly:
server `<I total_size><B suite><B compression>` followed by three `<H length>`
byte fields (nonce, server/client key data), minimum 12 bytes. Client ack has
`<I total_size>` then three analogous fields (signature, server/client key
data), minimum 10 bytes. Suite constants in entry 40 are 0/1/2 for
none/Magic-RC4/ECDH-ChaCha20. These are static Python transport definitions, not
proof of the running game's implementation or selected suite.

Added `socialmatrix_opening_hypotheses` to the existing offline capture
summarizer. It checks only a complete leading message in a contiguous run,
requires all three variable fields to consume its declared size, rejects
unknown suites, never scans offsets or joins gaps, and suppresses candidates
on conflicting TCP overlaps. It emits lengths only, not field contents, and
leaves protocol verification false. Four existing captures (e00bec92, 80be4c30,
f3b10f75, 96e5877c) yielded ZERO candidates with no parse skips. This bounded
negative result does not prove which protocol or encryption the flows used.

Static RPC nesting is also clearer: XyqServiceClient builds the nine-field
service/headers/uri/method/querystring/post_body/host/protocol/version dictionary,
then dispatches `module_cmd + 2`, data command 0. The socket layer wraps selected
serialization in `body.payload`; `header` carries `rpcid`, `aid`, and
connection-state-dependent `streamid`/original `token`, plus optional metadata.
This does not acquire a token or verify a live command number. See API_RESEARCH.

Verification: **236 source tests passed** (19 capture-parser tests including
four new cases). No live recording, network request, raw-key output, vault edit,
provider integration or new desktop build. The only pending user action remains
approval of the capture scope described immediately below; don't repeat the
same HTTPS-only experiment or ask for manual command pasting.

### Latest protocol/preparation checkpoint (2026-09-06, 03:34)

**Still incomplete: no verified own-Career response, repeatable Career session,
production provider, vault change or new desktop build.** The user-confirmed
diagnostic character remains UID 1370598422. Do not replace saved identities.

Static tracing corrected a transport assumption: the method named
`XyqServiceClient.http_request` does not establish HTTP on the wire. Archive
entry 3462 selects `SocialMatrixHttp` only in simple mode; otherwise it selects
`SocialMatrixSocketProxy`. Entry 3455 initializes native/NeoX/Python socket
wrappers, and entry 103 passes `SDK_SECURE_PARAM` into this client. Entries
36/40/43 describe socket framing and a separate SecureSessionHello/Ack with
multiple possible encryption suites. The active runtime branch/suite is NOT
verified. An HTTPS-only capture can therefore miss this candidate transport.
See API_RESEARCH.md for exact entries and limits; do not execute recovered code.

The 03:07 TLS flow's record flight is consistent with an older TLS/CBC hypothesis,
not proof of TLS 1.3. A 03:11 DNS-cache observation associated its IP with
`acsdk-x20na.gameyw.easebar.com`, not a verified Career identity. The cache no
longer returned that entry at 03:33. No additional TLS guessing/decryption was
attempted; do not raise the TLS-1.3-only search budget blindly.

Prepared `scripts/capture-career-handshake.ps1`, **preview-only by default and
NOT yet run as a live recording**. It previews only observed game peers on
ports 10200/12101/21308 and omits the local ephemeral TCP port so replacement
connections can be captured from their beginning. This may include another
local process contacting those exact same peers: explicit approval for this
scope is still needed. It never adds new remote peers, launches/closes the game,
changes login, or uses the denied memory path. Optional `-AllowGameRestart`
retains the same fixed filters during a normal user-controlled game restart;
replacement PIDs supply metadata only, not proof of packet ownership.

Actual previews in both PowerShell 5.1 and 7.6.5 at 03:33 matched:

- Initial game PID 16172; duration 90 seconds; AllowGameRestart true.
- Local IP 192.168.4.25; peers 34.56.114.218:10200,
  35.236.255.68:12101, 8.221.212.163:21308.
- Scope fingerprint `7a61f47cad03691ae652863231b4ee4c08f5af0c52ed9bce3c83f2daf789166e`.

The fingerprint pins the reviewed PID/duration/restart mode/endpoints; it is
NOT user consent. Re-preview before running; do not silently approve a changed
scope. Recording requires an administrator process, an idle PktMon session and
empty filters. This installed CLI removes ALL filters only, so cleanup requires
the filter-list snapshot to remain unchanged and capture to be stopped (or never
attempted). Uncertain start/stop or changed/unreadable filters are retained with
a warning, not blindly cleared. No persistent administrator helper is installed.

Verification: **232 source tests passed**, including 12 new mocked lifecycle
tests in both PowerShell engines. Real previews made no capture/settings/files.
The full suite used synthetic data; it is not API verification. At 03:33 the
session list showed no PktMon session; this non-elevated worker cannot read the
driver's filter list, so current filter emptiness is not independently verified.
The elevated recording guard will check it. Tracked diff has no whitespace errors.

Next user interaction: ask approval for the described 90-second fixed-peer
capture. After approval, use the already verified normal one-time RunAs launch
with a local transcript; do not ask the user to paste commands. Only after
HANDSHAKE CAPTURE READY should they normally reconnect/restart, use the SAME
account, and open own Career / Statistics / History. The script stops itself.
Do not start this new mode before explicit scope approval or automate UAC/login.

### Latest live-session checkpoint (2026-09-06, 03:09)

The user asked the agent to enter the capture commands. This now WORKS through
a normal one-time Windows administrator launch: the worker remains non-elevated,
but `Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -PassThru`
successfully starts the exact existing capture helper. The user handles any UAC
prompt; no automated consent, security-setting change, persistent elevated
service/task or terminal UI typing was used. Use `-NoProfile -NonInteractive`;
the command wraps the existing script in a local Start-Transcript/finally
Stop-Transcript and is Unicode-base64 encoded only for reliable argument quoting.
Do not claim that the agent cannot start a capture or insist on manual command
pasting. The helper's own administrator/process/idle/filter guards remain intact.

First agent-started capture: `career-d5f26d26f744415ca79b154b0e53b52b`,
03:04:21.553-03:04:52.002 local. Schema 4, TLS-only/all-components/follow enabled,
two initial HTTPS tuples, no late additions or scope errors. ETL 25,192 bytes,
PCAPNG 188 bytes, report 115,527 bytes. Independently verified ZERO packets;
the selected tuples remained sampled throughout. It stopped/cleaned up normally
and exited zero. Its launch transcript is in
`career-command-2eb2d9fa222542528d078b51b98cf8e5`. User activity during this first
window is not independently confirmed; an asynchronous timing question is pending.

Second agent-started capture: `career-96e5877cc37f44ae8d26a0ecaeeae20c`,
03:07:06.702-03:07:37.102 local. ETL 29,663 bytes, PCAPNG 12,296 bytes, report
95,348 bytes. **56 port-443 snapshots**, independently parsed without errors.
Six new game HTTPS connections were added; no cap, refresh, process or cleanup
failure. Most snapshots contain no TCP payload. The only payload-bearing tuple
is 192.168.4.25:61192 <-> 8.221.191.202:443: 13 snapshots, contiguous 2,968
client bytes and 409 server bytes, no overlap conflict. This is the first
current-session captured TLS payload, NOT a verified Career request.

TLS record metadata: client has complete record types 20/22/23 with respective
body lengths 1/68/2884; server has one complete type-23 body of 404 bytes.
All legacy record versions are 0x0303. No ClientHello/ServerHello was recovered;
negotiated TLS version/cipher and peer identity are not verified. The existing
TLS-1.3-only midstream verifier reached its two-million-attempt bound without
authenticating records. It read the approved local key log (393 complete TLS
1.3 application pairs, 1,578 recognized lines, no invalid/duplicate/conflicting
entries); no keys, plaintext or tokens were output or uploaded. Do not blindly
raise the search bound: investigate the observed record flight/protocol first.
No game TLS record has yet been decrypted. Launch transcript:
`career-command-151e510456294d4abfc47c88e201b170`.

Computer Use could select/activate and view the existing game window. Screenshot
confirmed UID 1370598422 on Career Overview, season S3.0 (The Abyss Awakens),
"No Season Data". The first occluded screenshot showed Codex; only after normal
game-window activation was the actual Career page visible. Two observed
Statistics-tab clicks did NOT change the tab/cursor, and automation was stopped.
Do not claim an automated tab/season change succeeded or that this capture is
proven to correspond to one. The user was asked to switch tabs manually while
recording. The cause of ignored input is not established; no input/protection
bypass was attempted. Game remains open. Both recordings have stopped.

No source implementation changed in this turn; prior **220 passing tests**
remain the latest suite. Only research/handoff notes and local capture/console
artifacts were added. No production API integration, vault edit, authenticated
own-Career response, repeatable Career session or new desktop build exists.
The goal is active/incomplete; new TLS evidence supersedes a missing-capture
block. No further user command is needed for offline inspection of this data.

### Latest live-session checkpoint (2026-09-06, 03:02)

The previous missing-capture block is superseded by an actual new capture.
The goal remains INCOMPLETE. The user explicitly confirms that **1370598422**
was the UID displayed while opening **their own Career**, not a searched
player. Use that as the user-confirmed current diagnostic character. It has
not yet been verified against an authenticated API response. Do not overwrite
the earlier imported UID 10826896958 or substitute the website UID 1822797559.

New diagnostics directory: `career-f3b10f75ebc243f595006cbf75e29034`.
Observation interval: 02:52:41.920-02:52:57.990 local. ETL 46,861 bytes,
PCAPNG 61,412 bytes, schema-3 report 64,737 bytes. Both ETL and independent
PCAP parsing count **79 snapshots: 70 outbound and nine inbound**. This is the
first current-session recording containing server payload. The nine inbound
snapshots are duplicate views of 5,680 unique bytes from port 12101, not nine
verified API replies. Outbound 12101 has 1,741 unique bytes in gapped runs of
1,124/617; outbound 10200 has one 4,464-byte run. No port-443 packet was saved.
These streams have no recognized Career fields, TLS record framing, bounded
zlib/zstd candidate, complete MessagePack container or MOS framing match. No
decryption was attempted on them, and they are not a verified Career response.

Live follow mode successfully added 12 game-owned connections, but ten were
short-lived port-8880/8881 connections. Their purpose is not proven. With four
initial connections, this filled the 16-connection/32-filter cap and stopped
the recording early. A new HTTPS connection to 35.186.233.65, local port 58725,
was added; the next, local 58726, hit the cap. Previous DNS-cache evidence
associates this remote IP with a replay service, not a verified Career host.
The decoded ETL has no ID-100 filter-definition records; incoming snapshots
carry filter ID 7, but its name/orientation cannot be established from those
records. Do not claim that explicit reverse filters caused the first replies.

Next experiment narrows existing follow mode to game-owned remote port 443,
excluding the 8880/8881 burst from the connection budget. An actual dry run
initially found zero eligible connections and reproduced a start-guard error.
The helper now permits zero initial connections ONLY with `-FollowConnections`:
after its existing administrator check it waits up to 30 seconds for an eligible
connection of the SAME game PID, without creating files, filters or a trace.
It then rechecks that PktMon is idle and its filter list empty, adds exact
forward/reverse filters and starts the normal 30-second capture. Timeout or a
changed game stops without mutations. `-DryRun` never waits. It instructs the
user to repeat the Career/tab action after CAPTURE READY because the triggering
request may already have passed. Polling still cannot guarantee full coverage.
Schema 4 adds `waited_for_initial_connection`; no broad capture fallback exists.

Verification: full source suite **220 passed** at 03:02; `git diff --check`
has no whitespace errors. Eleven capture-scope/count/lifecycle methods pass in both
PowerShell engines, including initial wait, timeout, process change, another
trace becoming active, and a 20-connection non-HTTPS burst excluded from TLS
follow mode. Ten existing filter guard cases pass. Subsequent actual dry runs
in both engines found one game-owned HTTPS connection and excluded three others.
No live capture was started by the agent. At 03:02, PktMon is idle and the
latest PCAPNG remains the 61,412-byte file from 02:52:58. No game relaunch is needed.

Next command in the user's existing Administrator PowerShell:
`& "C:\CodexRepos\marvel-account-keeper\scripts\capture-career.ps1" -TlsOnly -AllComponents -FollowConnections -ExpectedGameProcessId 16172`
If WAITING appears, open own Career and switch tabs to produce a connection.
After CAPTURE READY, repeat the own-Career/statistics/season action, then repeat
once more within the 30-second recording. Keep the diagnostic game open and
return the actual result/error; do not upload captures or session keys.

No current-session TLS record has been decrypted. A legitimate repeatable Career
session, own-account statistics response, production NetEase integration and a
new desktop build remain unverified/unimplemented. Existing vault/account
bindings and production provider are unchanged. The denied memory route stays
stopped. Approval for the existing TLS diagnostic launch is already granted.

### Latest live-session checkpoint (2026-09-06, 02:50)

GOAL BLOCKED, NOT COMPLETE at 02:51:38 local. The same missing usable capture
has persisted for three consecutive turns of this resumed run, including the
all-component result analysis/preparation and two follow-up checks. The latest
PCAPNG is still the 11,892-byte file from 02:42:36; no new capture directory or
pktmon process exists, and `logman query PktMon -ets` reports "Data Collector
Set was not found". Later directory mtimes are from the agent's analysis files,
not an active recording. No capture job is available to wait on. The updated
explicit-reverse/follow helper is prepared and tested (216 passed), but needs
the user's actual run or command error. Do not re-ask for TLS launch approval,
invent a new capture, or mark the stats provider complete. Resume on new input.

An all-component capture arrived during the preceding resumed goal turn:
diagnostics `career-80be4c3009584ad9a5434d838d736f4f`, interval
02:42:05-02:42:36 local. The old 02:40 blocked/missing-input status below is
superseded. Files: ETL 41,970 bytes, PCAPNG 11,892 bytes, schema-2 report 17,354
bytes. Both PCAP parsing and ETL/report correctly count **15 snapshots**.
All are OUTBOUND, across components 11/27/28/29/61/77. Thus `-AllComponents`
really was applied but did NOT recover replies. Port 10200: three snapshots,
24 unique bytes in two runs; port 12101: 11 snapshots, 2,703 unique bytes in
three runs (681/995/1027); port 21308: one snapshot, 279 bytes. No HTTPS packet.
No decryption was attempted on the incomplete, opaque game-protocol streams.

Additional evidence: outbound TCP ACK progression acknowledges 6,375 new bytes
on port 12101 and 40 on port 10200, with no reverse/wrapped deltas. Server bytes
were received by the TCP stack but are absent from the recorded packet data;
this does not identify their application content. `tracerpt` metadata summary
reports zero lost trace events (695 processed, two buffers); raw XML dump was
sent to NUL, not saved. Some schema mismatches were warned; no relaxed raw dump
was requested. `etl-summary.txt` and metadata-only `capture-analysis.json` are
beside the preserved original capture/report. Netsh trace is idle; no Npcap/NPF
service/driver was found. No network adapter/security setting was changed.

The same initial-only limitation recurred: two new connections to the prior
replay-service IP 35.186.233.65 (local 62285/62286) were visible near the end but
not selected. All four initial connections remained in the sampled metadata.
Netsh's installed capture-filter help does not provide TCP port filters, so
no less-scoped alternative netsh capture was started.

NEW PREPARATION: the helper now explicitly creates forward AND reversed exact
IP/port filters. CLI help describes unordered pairs, but Microsoft's Win32
PacketMonitorAddCaptureConstraint API documents source/destination fields.
This is a documentation mismatch and a testable hypothesis, NOT a proven CLI
direction-filter bug. Adding both orientations cannot broaden the intended
four-tuple/reverse scope. At most 16 connections / 32 filters are permitted.

Optional `-FollowConnections` samples the SAME game PID's established remote
TCP connections every 500 ms and adds forward/reverse filters for newly seen
eligible tuples. It never removes all filters during capture, selects another
PID, or falls back to a broad IP/port/all-app capture. It stops and preserves
the trace if a new filter fails, 16 connections would be exceeded, or the game
changes. It can still miss short-lived connections and their first packets/
handshake. Report schema 3 records follow mode, late additions and errors;
new-connection capture is unknown, not claimed successful. CLI support for late
filter additions is not yet live-verified; the underlying API documents adding
constraints before/after activation. No new live capture was started by the agent.

Verification: seven scope/count/lifecycle methods run in both PowerShell engines,
including exact reverse pairs, no-follow behavior, one late connection, partial
pair failure cleanup, connection/filter cap and process-change stop. Ten existing
filter-list guard cases pass. Full suite: **216 passed**. A real dry run found
four game TCP connections, eight explicit-direction filters, no excluded flows.

Next user-run command in existing Administrator PowerShell (no relaunch needed):
`& "C:\CodexRepos\marvel-account-keeper\scripts\capture-career.ps1" -AllComponents -FollowConnections -ExpectedGameProcessId 16172`
After CAPTURE READY, reopen OWN Career and change history/statistics/season
filters, repeating once during the 30-second window. Inspect the actual result
before proposing another retry. Game PID 16172 remains the diagnostic session;
the final `logman query PktMon -ets` found no active recording. No verified own
Career response, usable/repeatable Career authentication, provider integration
or new desktop build exists. Vault/account binding/production provider unchanged.

### Latest live-session checkpoint (2026-09-06)

GOAL BLOCKED, NOT COMPLETE as of 02:40:27 local: the required capture containing
current-session replies/TLS remains unavailable across three consecutive
resumed goal turns. The user-triggered turn analyzed the outbound-only capture
and fixed/prepared the helper; the next turn made no progress because no new
recording existed. This turn rechecked: `logman query PktMon -ets` reports "Data
Collector Set was not found", no pktmon process exists, and the latest PCAPNG
is still the 8,100-byte file written at 02:32:43. No all-component capture has
started or completed. The directory's later mtime is from the agent's local
`capture-analysis.json`, NOT a new recording. Game PID 16172 remains open.
Preparation/testing are complete; no active capture job can be waited on.
Resume on the user's all-component capture result or actual command error.
Do not ask for renewed TLS launch approval or claim API/provider completion.

The user previously said "done" and supplied the genuinely new capture below.
It supersedes September 5's empty-capture evidence, but is outbound-only.
Directory: local diagnostics `career-e00bec92df1148a9a485ad2d5fa2c218`.
Capture interval: September 6 02:32:12.395-02:32:42.793 local. Files: ETL
43,462 bytes; PCAPNG 8,100 bytes; original schema-1 report 16,442 bytes.

IMPORTANT REPORTING BUG FIXED: the original report says zero packets, but
independent PCAP parsing AND unfiltered ETL enumeration both prove **18**.
`Get-WinEvent -FilterHashtable` with provider/IDs returns NoMatchingEventsFound
on this actual ETL even though `-Path` enumeration exposes 18 PktMon ID-160
events. The counter now enumerates the bounded local ETL and filters decoded
provider/ID metadata. NoMatch/unrecognized/partial-read errors are not treated
as verified zero. Tested actual new ETL count = 18 and actual earlier empty
ETL count = 0; actual new ETL also verified in Windows PowerShell. The original
report is preserved; `capture-analysis.json` beside it records the correction.

All 18 saved packets are OUTBOUND from Wi-Fi component 77 (direction tag 4):
14 on local 64973 -> 35.236.255.68:12101 (3,881 unique bytes, one run), two on
64949 -> 34.56.114.218:10200 (51 bytes, two gapped runs), and two on 64819 ->
8.221.212.163:21308 (395 bytes, one run). No port-443 packet or incoming packet
was saved. Adapter counters nevertheless include receive-direction packets
(component 77: 21 receive/84 transmit; component 11: 24 receive/69 transmit).
This is evidence of missing recorded traffic, not a proven driver/root cause.
Port 12101 and 21308 runs have no complete TLS 1.3 candidate records; verifier
performed zero attempts and did not read keys. No Career field markers, valid
MessagePack or bounded zlib candidate was found; port 21308 has ambiguous
length-prefix hypotheses for lengths 107/164/124, NOT identified Career data.

Selected initial tuples were the three above plus local 52119 -> 8.221.57.187:443.
The original four remained in all metadata samples. Two NEW HTTPS connections
(local 56827 and 56828 -> 35.186.233.65:443) appeared in the final sample and
were excluded by the initial-only filters. DNS cache associated that IP with
`iowa-uscentral-replayclient-x20na.easebar.com`; this is not verified TLS SNI,
an authenticated peer or a Career gateway. Those replay connections later
closed. Do not assume that the missed HTTPS traffic was Career stats.

Prepared opt-in `-AllComponents` for the SAME exact game-owned endpoint filters,
to test capture before/after the wireless NIC rather than at NICs only. It does
not add new connections, widen endpoint filters, alter the game, self-elevate,
change TLS/proxies/certificates, or start a capture by itself. Report schema 2
records selected components. Full mocked lifecycle verifies `--comp all` and
unchanged exact filters in both engines. Full suite: **212 passed**; actual
dry run found four remote game TCP connections and zero exclusions.

Next user-run command in the same Administrator PowerShell:
`& "C:\CodexRepos\marvel-account-keeper\scripts\capture-career.ps1" -AllComponents -ExpectedGameProcessId 16172`
After CAPTURE READY, reopen OWN Career, change a statistics/history/season
filter near the start, and repeat once before the 30-second stop. Game PID
16172 is still the diagnostic session. This experiment has NOT run; do not
claim it fixes missing replies/new connection coverage or demonstrates API
access. New flows still require matching filters and may be missed.

Key-file metadata at 02:33: 303,806 bytes, last write 02:32:57. No key values,
raw payloads or credentials were output/uploaded. No own-account stats response,
verified session/token for Career, production provider or new desktop build.

### Latest live-session checkpoint (2026-09-05)

22:27 BLOCKED CHECKPOINT: the same required nonempty current-session capture
has remained unavailable across three consecutive goal turns, including the
user-triggered zero-packet analysis and the decoder-limit repair. Preparation
and relevant tests are complete (212 passed); another offline fixture does
not establish live Career access. A fresh read-only check at 22:27:27 confirmed
that the latest PCAPNG is still the same 188-byte file from 22:16, no new capture
directory exists, and `logman query PktMon -ets` reports "Data Collector Set was
not found". Game PID 16172 remains open; no pktmon process was found. There is
no active capture job to wait on. Goal marked BLOCKED, not complete, pending
the user-run command below or its actual error output. Resume on that input;
do not repeat the already-granted TLS approval or invent a running capture.

22:26 FOLLOW-UP: no new capture or active PktMon trace exists yet; the missing
nonempty current-session capture remains the required input. The preceding
turn made progress by diagnosing the empty ETL and repairing its reporting.
This follow-up also fixed a concrete decoder incompatibility: the live key log
had 218 valid TLS 1.3 application pairs and reproduced
`keylog_session_limit_exceeded` against the old 128-session cap.
`tls_midstream_probe.py` now accepts up to 1,024 groups within the unchanged
1 MiB input bound. It tries every key at each ascending starting counter,
checking all keys at the first matching counter before returning. Summaries
report that actual counter-search ceiling; higher-counter alternative matches
are not ruled out. The existing 0-4095, 20-second and two-million-attempt bounds
and record-chain validation remain. Old keys are NOT discarded to retain only
recent sessions. Synthetic 218-pair/public-RFC-vector and older-key/high-counter
regressions pass; the live file now parses 219 pairs without outputting secrets.
Full suite: **212 passed**. This is decoder preparation, not live TLS/Career
decryption or API integration. No new capture was started by the agent.

CURRENT GOAL STATUS: blocked, not complete, pending the nonempty comparison
capture. The user previously said "done" and supplied the following new but
empty capture, superseding the old missing-input block further below. It
ran at 22:15:53-22:16:23 local on September 5 in diagnostics directory
`career-67699424dc5049be847b0d5dc5a6c91b`. Its ETL is 25,021 bytes; PCAPNG is
188 bytes with ZERO packets (independently checked using dpkt and ETL events).
All 107 component flow-counter events report zero matching packets; no
PktMon packet events (IDs 160/170) exist. Conversion did not discard usable
packets. Do NOT run decryption or describe this as a UID/authentication failure.

The ETL records exactly one filter: TCP 192.168.4.25:57203 <-> 8.221.56.176:443.
The command's `-TlsOnly` excluded the game's other established connections on
ports 12101, 10200 and 21308. By 22:17 the HTTPS local port was 65279, so the
selected connection changed sometime between selection and that later check;
whether this happened DURING capture is unknown. The data does not distinguish
an idle selected connection, a new/missed connection, cached Career UI, another
transport, or a capture-layer issue. NIC selection was unchanged. No first-party
Career request/response or live TLS decryption has been verified.

Updated capture helper now reports initially excluded connections, samples
game-owned TCP endpoint metadata every five seconds during the same bounded
capture, and writes local `capture-report.json` with selected/missed/changed
connections and the verified ETL packet-event count. Empty, unreadable and
nonempty captures are distinguished; packet content is never formatted into
the report. Filters are still fixed exact tuples: no dynamic/all-app filter,
new-connection coverage, or automatic elevation was added. Default capture
includes the game's existing non-443 TCP connections. Stop/cleanup behavior
and the empty-result path pass a fully mocked lifecycle in both PowerShell
engines; actual new ETL count returns verified zero. Full suite: **210 passed**.

Next action (same Administrator PowerShell, keep diagnostic game open):
`& "C:\CodexRepos\marvel-account-keeper\scripts\capture-career.ps1" -ExpectedGameProcessId 16172`
Do NOT add `-TlsOnly` for this comparison. After CAPTURE READY, open own Career,
switch statistics/history and change the season filter if available. Current
default dry run at 22:23 found five established remote game TCP connections.
The agent has not started another capture; the original trace is stopped.
New connections remain excluded and are reported only if a metadata sample
catches them. A successful capture is not a successful API integration.

At 22:17 the approved key file was still 31,278 bytes, last changed 17:07.
By 22:23:51 it had grown to 182,036 bytes with last write 22:23:33. This is
fresh file-growth evidence only; the new entries have not yet been correlated
with traffic. Do not claim that logging stopped or that this proves Career
TLS. Game PID 16172 remains the diagnostic session. TLS approval is granted;
do not ask for the same launch approval again. Keep raw captures/keys local.

OFFLINE TLS 1.3 DECRYPTION PREPARED: `tls_midstream_probe.py` now authenticates
bounded record prefixes using the authorized key log's paired generation-zero
application secrets. It derives AES-128/256-GCM or ChaCha20-Poly1305 keys/IVs,
tries bounded starting counters, and requires both directions to match the
same key-log pair/cipher. Every complete subsequent record must authenticate;
gaps, changed tags, conflicting/ambiguous keys and malformed tails cannot be
silently skipped. Partial leading/trailing bytes are explicitly reported.
Only counts/fixed markers are emitted; private decoded objects have no data
repr. No capture, request, token export or plaintext output is implemented.

Sixteen tests include independent RFC 8448 key/IV and complete client/server
application-record vectors (50 bytes each, counters 0 and 1, no hellos), plus
synthetic cipher, padding, tamper, ambiguity, limit and redaction cases.
Full suite: **208 passed**. This is verified offline record-decryption code,
NOT a verified game/Career response, process ownership or TLS peer identity.
TLS 1.2, key updates, HTTP/2 decompression and complete connection validation
are not implemented. The earlier hello-only checker stays unchanged.

At the 17:22 local metadata check, no September 5 capture file/directory had
appeared; only the old September 4 capture existed. No live capture/job is
assumed from the user's intent. The next required user action remains the
already-presented 30-second, current-game TLS capture command below. Do not
replace that missing input with another run against the old capture.

LIVE DIAGNOSTIC CHECKPOINT: the user ran the approved Administrator PowerShell
launch and reports being logged in at the lobby/Career screen. At 17:07 local,
game PID 16172 (start 17:02:17) and bootstrap PID 16712 were running. The new
private diagnostics directory is
`C:\Users\kylep\AppData\Local\MarvelAccountKeeper\diagnostics\career-tls-3096395a0b7a49eebc6940baca48c3a1`.
Its `session.keys` was created at 17:02:12 and grew from 26,958 to 31,278 bytes
between metadata/format checks. A bounded local parse found 134 recognized
lines, zero invalid/conflicting/duplicate lines, 32 ignored lines, 32 session
identifiers with all four TLS 1.3 handshake/application labels, and six
`CLIENT_RANDOM` entries. Those six entries alone do not prove a negotiated
TLS version. No key/random value, plaintext response or credential was output.
The key file's ACL has only the current user and SYSTEM with FullControl.

This verifies that the diagnostic launch produced valid key-log material, NOT
which game connection or service each entry belongs to. No captured connection
has been decrypted, and Career/API access is still unverified. Do not ask again
for a diagnostic relaunch or more administrator access to run that same launch.
Keep this game session open for the proposed next capture.

Prepared capture refinement: `scripts/capture-career.ps1 -TlsOnly
-ExpectedGameProcessId 16172` restricts the existing 30-second capture to that
game's currently established port-443 connections, matching BOTH IPs and both
ports. A dry run found two such connections and made no changes. New connections
remain excluded; no handshake/Career coverage is guaranteed. The user must
explicitly run the capture command in Administrator PowerShell; the agent has
not started it. The strict hello-correlation checker cannot use a mid-session
capture without hellos. The separate TLS 1.3 midstream verifier is now implemented
and validated on public/synthetic fixtures as described above, not on game data.
The synthetic scope test passes in both installed PowerShell engines; that
capture-refinement checkpoint's full suite had 192 tests.

AUTHORIZATION UPDATE: the user explicitly answered "yes" to the temporary,
local-only TLS diagnostic launch. This supersedes the historical unanswered
approval gates below. Do not ask for the same approval again. The approved
action is one launch through `scripts/launch-career-tls.ps1 -Launch`, followed
by local verification of whether the diagnostic produces usable material.
It does not authorize broad all-app capture, game-memory work, persistent
settings changes or forced process termination. At the first post-approval
check, normal game PID 4772/bootstrap PID 11620 remained open; no diagnostic
directory existed. The user has been asked to quit the game normally, and
the helper must recheck all launch prerequisites before activation.

POST-APPROVAL RESULT: the normal session exited. Two actual `-Launch` attempts
did NOT start a diagnostic launcher. First, the worker's PowerShell Core 7.6.5
lacked `[IO.Directory]::SetAccessControl`; the prior tests covered only Windows
PowerShell. Fixed the helper to use `FileSystemAclExtensions` on modern .NET
and retain the Framework fallback. Both syntax/environment and real empty-folder
ACL tests now run in both installed engines; full suite still **191 passed**.
The second attempt passed directory setup but Windows rejected `Process.Start`:
"The requested operation requires elevation." The worker is non-elevated.
No self-elevation, privilege-setting change or forced close was attempted.

The next action is the SAME already-approved helper, run from the user's
Administrator PowerShell:
`& "C:\CodexRepos\marvel-account-keeper\scripts\launch-career-tls.ps1" -Launch`
Then use Launch in that new launcher and reach the lobby. Do not ask again for
TLS-diagnostic approval. A final metadata check found no game/launcher process,
no key files and no parent `SSLKEYLOGFILE` setting. Two empty directories from
the failed attempts remain: `career-tls-42e36b8e12ac44a48dde25695ce6de4a`
(ACL setup failed) and `career-tls-aac25332dd0c4fd6a9e20a6fffb0d544` (private ACL
verified). Their existence is NOT evidence of active diagnostics. No fresh
capture or first-party stats response exists. Earlier blocked/approval-pending
descriptions below are historical and superseded by this result.

Prior normal-launch checkpoint: the previously blocked goal resumed when the user said
they would launch the game. This was a normal manual launch, not approval of
the still-unanswered TLS diagnostic request. At 16:22:38 local, game PID 4772
(started 16:22:05) and bootstrap PID 11620 were running; the launcher had exited.
Read-only TCP ownership/DNS-cache metadata associated three port-443 connections
with `x20na.gph.easebar.com`, `x20na.update.easebar.com` and
`whoami.nie.easebar.com`, plus port 21308 with `s.q.easebar.com`. These are
snapshot/cache associations, not verified TLS hostnames or a Career gateway.
No diagnostic directory, key file, capture, memory read or request was created.

Goal status is blocked again, not complete: the same explicit-approval gate
remained unanswered across three consecutive turns of this resumed run. A
fresh read-only check confirmed the same normal game session (PID 4772) and
no TLS diagnostic directory. There is no diagnostic job to wait on. Explicit
approval to relaunch with temporary local-only TLS diagnostics is still
required. The launch helper's
metadata-only check correctly refused while the normal game/launcher was
running. Do not close the user's game, change it in place or treat automatic
continuation as approval. The user was asked to close it normally and approve
the diagnostic relaunch. Recheck prerequisites after their actual response.

Latest offline preparation: `tls_capture_probe.py` now checks an explicitly
selected TCP flow for initial ClientHello/ServerHello messages and matching
TLS 1.2/1.3 key-log identifiers. Default mode is metadata-only. It never
captures, launches, decrypts, sends requests or outputs keys/packet bodies.
Even a positive result leaves process ownership, TLS decryption and Career
access unverified. Seventeen synthetic test methods cover matching/missing/
conflicting material, malformed input, bounded parsing, unrelated flows, TCP
gaps and connection reuse. Full suite: **191 passed**.

Negative control against the existing capture's 23 selected port-12101 packets
returned `not_initial_tls_records`, with no hello or matching material found.
No key file was supplied or read. This does not establish the game's current
TLS transport or rule out HTTPS on other connections. Read-only installed
Packet Monitor help confirms it has no PID/process packet filter; the old
existing-connection filter cannot guarantee coverage of a new TLS handshake.
Fresh, correctly scoped capture setup remains unresolved. Do not replace it
with broad all-application HTTPS capture.

The approval question below is still unanswered. No `-Launch`, live TLS
logging, new capture or authenticated request has occurred. The new checker
is offline preparation, not a working stats provider. See API_RESEARCH.md's
"Offline capture/key-log correlation preparation" section for limits/results.

NEW NEXT STEP — explicit approval needed before activation: this installed game
has libcurl's `SSLKEYLOGFILE` implementation and TLS callback wiring. Static
trace: environment/file opening `0x9970e00`, backend init call `0x99464a0`,
enabled check `0x994744c`, callback registration `0x9947458–0x994745f`, callback
`0x9949cb0` -> key-line writer `0x9970e90`. This is a built-in diagnostic
candidate, NOT verified live logging or Career decryption. It avoids the denied
game-memory route and requires no binary/certificate/proxy modification.

Prepared `scripts/launch-career-tls.ps1`: default metadata-only; `-Launch` is
explicitly opt-in and must wait for the user's approval of this live diagnostic.
It launches only the normal installed launcher with a child-only TLS key-file
setting, a new current-user/SYSTEM-only local diagnostics directory, and exact
game/launcher build checks. It will not close processes, change Steam options,
set a user/machine environment variable, self-elevate, or start a packet capture.
Keys can be written until the diagnostic launcher/game exits. They stay local;
do not print/upload them or confuse launcher-only TLS traffic with Career.
The current game and launcher were absent at the September 5, 14:44 local
process check; Steam was running. Recheck before any authorized launch.
Default preview succeeded without creating a key file or launching anything.
Two new tests cover seven decisions, child-only environment, syntax and private
ACLs on an empty synthetic directory. Full suite: **174 passed**. The initial
ACL test exposed unavailable PowerShell Security-module autoloading; using the
Windows .NET directory ACL API fixed the helper and passed the actual ACL test.

A launch-only key file is not sufficient for the goal. Any decryption test
also needs a fresh, appropriately scoped own-game TLS capture with handshake
data and matching secrets; the old mid-session capture/new-connection filters
do not guarantee this. Capture setup is not yet implemented for this workflow.
Do not enable broad all-app HTTPS capture or claim the old capture can be
decoded with new-session keys. No live diagnostic launch has been run. See
API_RESEARCH.md's "Built-in TLS diagnostic candidate" section and approval gate.

Latest offline follow-up: decoded the separate, existing game-specific UniSDK
log, not the encrypted main-game log. All 2,758 records decode as strict Base64,
AES-256-ECB, PKCS#7 and UTF-8; 859 records are dated September 5. The directory
is the uppercase MD5 of installed global game ID `x20naxx2gb`. A new scoped
`scripts/inspect-sdk-log.py` reproduces counts only; default is metadata-only,
and `--inspect --date 2026-09-05` explicitly inspects plaintext transiently.
It derives the logger parameter from a hash-verified installed SDK DLL; no
parameter value, plaintext record, credential, claim value or raw response is
printed or persisted. No DLL is executed and no network/process call is made.
The selected log records contain eight JWT occurrences, two distinct SDK-shaped
JWTs, zero Career-shaped JWTs, and one HTTP-200 authentication telemetry record
for `https://mgbjp-x20na.unisdk.easebar.com/x20naxx2gb/sdk/uni_sauth`.
JWT signatures, current character identity and Career access are NOT verified.
These are platform-login credentials, not a demonstrated Career session.

Static login-chain evidence: entry 4653 calls native `unisdk.ntLogin`; entry
7800 obtains `SAUTH_JSON`, sends it in game-protocol `C_LOGIN_AUTH`, then uses
the reply's gate/auth fields for `C_LOGIN_ENTER`. Later, entry 3743 receives
`S_REFRESH_SOCIAL_TOKEN(token)`, stores `SocialMatrixToken` and gets the Career
gateway from its `websocket.addr` claim. SDK login and Career authentication
are distinct stages; do not send an SDK JWT to a guessed Career gateway.
No ordinary web-token export was found in the bounded script-name search.
The main-game encrypted logger remains a separate unresolved offline lead.
The denied process-memory route remains STOPPED. No separate SDK/game login
was initiated; that could affect the running session and is not yet prepared.
Thirteen new synthetic log tests pass; full suite: **172 passed**. No provider,
vault, account binding or packaged build changed. See API_RESEARCH.md's
"Game login chain and separate SDK log" section for reproducible evidence.

Latest public-site follow-up: observed one normal player refresh and one
"Update Matches" action on RivalsData for the user-supplied public IGN's page,
UID 1822797559 / season 19. Loading uses POST `/player/matches/cached`; the
update uses POST `/player/matches` with the same UID/cursor/season body. The
update returned HTTP 200, 13 matches and `source: "live"`. This is RivalsData's
source label, not independent verification of a NetEase request or freshness.
Its profile route POST `/player` also returned 200. The frontend tries this
route normally and falls back to `prefer_cache: 1` on certain failures.
Do not describe RivalsData as reconstruction-only. Its FAQ describes private
history reconstruction separately; the inspected frontend exposes only its
own backend, not the server-to-NetEase authentication or endpoint.
Tracker support likewise refers to an upstream API while declining to offer
a Marvel Rivals developer API. No direct first-party player response, production
NetEase integration, or new build is verified. A similarly named public GitLab
RivalsDataAPI project is a hero/balance-data project, not a verified source for
rivalsdata.com. Full suite re-run: 159 passed. See API_RESEARCH.md's public-site
refresh section for exact observations, sources and the next evidence required.
No new game-memory attempt, login, account binding, raw-response persistence,
or vault change was made. The website UID is still not verified as the currently
logged-in game character; do not replace any saved identity with it.

Non-memory follow-up: inspected public official website/account-page login code.
It uses `interact3-h.webapp.easebar.com/x20drop2410/` website authentication and
character/Twitch-drop routes, not a verified SocialMatrix/Career login exchange.
The account page can automatically bind a character and has reward-redemption
flows; no login or state-changing endpoint was invoked. Do not ask the user to
log in there solely on the assumption it supplies a Career token. See the newest
API_RESEARCH section for sources, exact routes and limitations.

Latest process-memory result: The user ran the session helper in Administrator PowerShell
for PID 17132. Its first ReadProcessMemory request (4096 bytes, after nine
region queries) failed with Win32 error 5: `memory_read_denied_or_failed`.
Bytes read: zero; candidates: zero; no network request or token storage.
Opening the handle succeeded in the earlier diagnostic, but actual reading
did not. Do not claim elevation solved memory access, blame a specific
protection mechanism without evidence, or conclude the token is absent.
The helper stopped as designed. STOP this memory route: no repeat scans,
debug-privilege changes, injection, driver methods or protection bypasses.
The blocker is now unavailable authorized session acquisition, not a missing
administrator approval or pending first scan. A usable non-memory first-party
authentication route or different verified endpoint is still needed; neither
has been demonstrated. The project is not complete or integrated.

Latest static follow-up: entry 1858 takes `zone_id` from the first `zone_list`
entry in the login reply; do not assume a fixed PC zone. Entry 1833 reads
`data.career.player_uid`. Standalone profile sends now compare that nested UID
with the explicit request and reject missing/malformed/different IDs as CLI
success, without printing another account's ID or claiming full verification.
Overview stat keys/formatting are driven by `MarvelCareerOverviewDataTable`;
live schema and stat semantics remain unverified. Full suite: 159 tests pass.
The user-run scan subsequently failed as recorded above. Do not silently
substitute a different UID/zone or retry the denied memory route.

Earlier handle-only test: the user ran the administrator diagnostic for PID 17132 and supplied
`caller_elevated: true`, `read_handle_available`, Win32 error 0. It opened a
read-capable handle only; no memory was read. Our app/worker/shell remain
non-elevated even after the attempted app relaunch. The earlier denial below is
historical, not the current result from the user's administrator terminal.

Prepared `scripts/probe-career-session.py` / `career_session_probe.py` for that
terminal. Default is preview-only; `--scan` explicitly reads a bounded subset
of the exact installed game's private, committed, readable non-executable pages.
It recognizes ASCII/UTF-16LE SocialMatrix-shaped JWTs, keeps candidates only in
memory, and prints counts/gateway hints, never token/account-claim values.
The scan subsequently attempted a read but obtained zero bytes. Optional `--send` requires explicit own UID,
zone and independently verified gateway; no identity/zone is inferred and no
production provider is wired. Current identity and gateway remain unverified.
The helper never self-elevates, injects, changes protections, or retries denied
reads. It closes handles; byte/time/region/candidate limits are explicit.
Twenty-one tests cover synthetic data, including native API bindings reading
one known buffer in the test's own process only. Full suite: 154 tests pass.
Next user command and limitations are in API_RESEARCH.md's bounded-helper section.

Historical checkpoint before the successful user-run administrator check:

NEW: Limited OS metadata now verifies the game is elevated and the research
process is not; both have the same Windows owner. This is a concrete privilege
mismatch, not proof anti-cheat caused the access denial. Prepared
`scripts/check-career-access.py`: default metadata-only; optional user-run
administrator check opens/closes a read handle but NEVER reads memory. It has
not been run elevated. Do not silently elevate/retry the denied memory path.
All 133 source tests pass. Six scoped config files had no readable token.
Current lobby/config identity appears to be 1370598422, distinct from both
earlier supplied/website UIDs; name/stat comparison remains unresolved.
See API_RESEARCH.md's privilege-mismatch section for the exact proposed command.

Latest local implementation: `career_probe.py` now prepares/sends the recovered
own-Career operations independently of the app. Default mode is preview-only;
live sending needs a verified gateway and legitimate token. It does not acquire
credentials or read memory. Eighteen new offline tests pass; 128 source tests
pass in total. No live NetEase response or production provider is verified.
See API_RESEARCH.md's standalone-probe section and limitations.

UPDATE: The user subsequently opened the game. Confirmed
Marvel-Win64-Shipping PID 17132, started 11:44:34 local. The authorized read-only
access attempt `OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ)`
returned NULL / Win32 error 5 (access denied). No memory was read and no session
token was recovered. Stop this access path as promised; no escalation, injection
or protection changes were attempted. The current blocker is denied process
access, NOT a missing game process or missing user approval. It is not known
whether Windows privileges or game protection caused the denial.

The user HAS now explicitly approved the proposed read-only own-game session
memory check and subsequent own-account stats lookup. Do not re-ask for that
permission. No game process was running when checked. Normal Steam launch
opened MarvelRivals_Launcher.exe, but two observed Launch clicks did not start
the game. A Windows process-token metadata query against the launcher returned
access denied (5); the research process is non-elevated. Cause is not proven.
No memory read or authenticated stats request occurred. The launcher is left
open; the user needs to start the game and reach the lobby when awake. Do not
work around denied access or modify game protections. See API_RESEARCH.md's
latest section. Historical approval gates below are superseded only for this
explicitly approved read-only session check, not arbitrary instrumentation.

### Prior research

The user's newer request expands the original local-import scope below: research
live NetEase stats calls and integrate a working provider. Read `API_RESEARCH.md`
first for the observed routes, reproducible controls, and unresolved requirements.
The historical Phase 5 block is not a reason to stop that research.

Latest follow-up: the user completed an own-Career capture and confirmed the
lookup near its end. Offline analysis narrowed the candidate connection to TCP
port 12101 but did not recover readable requests. Read-only PE analysis identified
the client's `mosclient` Python binding table, including TCP send, MessagePack,
compression, and security-suite methods. `API_RESEARCH.md` records the executable
hash, build-specific code locations, limitations, and next calling-layer target.
Further read-only tracing identified a seven-byte plaintext message envelope,
masked 28-bit record lengths, optional trailing extensions, and a Zstandard
streaming compression component. The updated offline checks do not match the
existing mid-session capture. A subsequent offline script-container reader
located the actual own-Career calls: service `xyq-career`, operations
`query_my_player_career`, `query_my_match_summary`, and `uid_query_my_match`.
These use XYQ/SocialMatrix, so the previously identified mosclient socket must
not be assumed to carry them. See the latest API_RESEARCH checkpoint for archive
entries and input/response field names. Exact request nesting, authentication,
transport and live own-account responses remain unverified. Latest static trace
resolved the game's XyqClient wrapper and HTTP gateway route
`/xyq_service/xyq-career/<operation>` with JSON and the game-issued SocialMatrix
JWT in Authorization (as-is). Host/TLS settings come from that token. Exact
request construction is recorded in API_RESEARCH.md; live testing now needs
explicit approval to locate/use the user's own current session token. No such
session material had been accessed at that checkpoint. The user subsequently
approved an existing-log check: three local game/launcher logs contained no
readable SocialMatrix token. The main game log appears opaque, not ordinary
text; its format is not yet established. No token was recovered or transmitted,
and no authenticated lookup was made. See API_RESEARCH.md for scope/results.
Further static tracing verified the main log's `logversion000` header and nine
length-delimited records covering the complete file. Its writer uses compression
and AES-CTR; runtime-transformed parameters remain unresolved and no record was
successfully decrypted. Three extra ccmini logs also lacked readable session
markers. No token is available yet. See the newest API_RESEARCH checkpoint.
Do not assume permission for game hooks or process-memory inspection.
Additional static verification: Career's PC `device_type` is the string `pc`
(archive entry 5459). The rank method's own-player branch uses existing local
rank state; its namecard request is on the other-player branch, not a verified
own-rank endpoint. API_RESEARCH.md records the correction and exact fields.
The research tools are separate from production dependencies; 110 source tests
now pass. No new stats provider, packet replay, or live instrumentation exists.

- General-player NetEase access remains unverified; no new production provider
  is connected to rank refresh or the match index.
- `provider_probe.py` tests observed routes without vault access or raw-response
  persistence. Positive tournament controls work; the tested ordinary match is
  absent from that endpoint.
- Tracker/RivalsData browser requests succeed for the supplied profile, while
  standalone HTTP requests return 403. The app now makes clear that a blocked
  request does not establish an invalid UID.
- 110 source tests pass, including probe, capture and script-container tests. Use `.venv\Scripts\python.exe`
  in this checkout; the system `python` alias is not the configured test runtime.
- No browser-assisted fallback has been implemented. No new executable has been
  built or published. Existing credential handling and opt-in storage are intact.

## Paste this into Codex

```text
Continue the Marvel Account Tracker safe match-history implementation in this
repository.

Repository: https://github.com/itsnotyuiiii/marvel-account-keeper.git
Continuation branch: codex/wip-safe-match-history
Branch base: 23937e2
Current origin/main at handoff: 92de1cc (v2.15.1)

Before changing code:
1. Confirm the checkout is on codex/wip-safe-match-history and inspect git
   status, branch tracking, and the complete diff against origin/main.
2. Read MATCH_HISTORY_IMPLEMENTATION_PLAN.md, MATCH_IMPORT_FORMAT.md, README.md,
   SECURITY.md, match_index.py, and match_sources.py.
3. Treat the existing dist executable as smoke-test evidence only. Do not
   publish it: it was built before this continuation commit and its embedded
   build metadata does not identify the committed implementation.

Current state:
- Phases 0 through 4 of the owner-authorized local match-history design are
  implemented. Phase 5 is intentionally blocked.
- Match data is local, opt-in, and limited to accounts saved and authorized in
  the user's vault. Participant identities for unrelated players must not be
  persisted.
- RivalsData must not be scraped. Do not call private endpoints, bypass
  Cloudflare or payment controls, copy provider payloads, build a searchable
  database of unrelated players, or add live-game/process/packet collection.
- Rank refresh and match-history state must remain isolated. A match-index
  failure must not change rank-refresh state.
- The branch now includes the v2.15.1 release commit from origin/main and keeps
  APP_VERSION 2.16.0 for this development candidate. Do not publish until a
  fresh packaged build passes the release checks below.

Latest continuation checkpoint (verified 2026-08-31):
- The continuation branch is reconciled with origin/main at `92de1cc` while
  preserving APP_VERSION 2.16.0.
- The required focused suite and full `*_test.py` discovery both pass: 76 tests.
- Preview and commit now share control-character validation.
- Persisted match, provenance, and exact-source tokens are keyed with the
  unlocked vault key before entering plaintext SQLite.
- Stale rank-refresh results cannot overwrite a newer nonblank Marvel Rivals
  UID, and destructive startup reconciliation runs only after the packaged
  single-instance wait/lock.
- Match-result styling uses an explicit CSS-class allowlist. Match-index backup
  retention is bounded to the newest 10 files, and the clear-history flow
  discloses the retained local recovery backup.
- `tests/update_hop_test.py` resolves its packaged executable lazily, so normal
  unittest discovery no longer mistakes the discovery argument for an EXE.

First verification command:
python -m unittest tests.match_index_test tests.match_import_test tests.match_routes_test tests.stats_provider_test -v

Broader source-test discovery:
python -m unittest discover -s tests -p "*_test.py" -v

Then review the implementation and tests for any regression or missing safety
gate. If preparing a release, rebuild from the reconciled committed tree, run
the packaged update/restart-hop checks and an isolated synthetic-vault smoke
test, verify the artifact's embedded commit/version metadata, and only then
propose publication. Do not merge, tag, or publish without explicit approval.
```

## Checkout on another PC

```powershell
git clone https://github.com/itsnotyuiiii/marvel-account-keeper.git
cd marvel-account-keeper
git switch --track origin/codex/wip-safe-match-history
```

Open the cloned folder as a Codex project, open this file, and paste the prompt
above into a new Codex task.
