# Live API research checkpoints

## September 11, 2026: official web login and renewal contract

### Owner sign-in and one AUTH-only experiment

The owner completed normal Steam-linked Account Login. Selecting the official
store character returned HTTP200/application0000 from
`/gameclub/marvelrivals/11001/login-role`; both `roleid` and `show_roleid` matched
1370598422, with IGN ゆいᶻᶻᶻ and level32. The internal account ID is distinct
from the game UID. No raw account IDs or credentials were exported to notes.

Important correction to the earlier platform inference: this actual login's
game-specific SDK object has `platform="pc"`, `sdk_version="1.0.0"`,
`gameid="x20naxx2gb"`, `login_channel="netease_global"`, and
`app_channel="netease.allysdk_global"`. Those are issued values, not a web-to-PC
relabel. Its other fields are sdkuid, sessionid, deviceid, udid, client_login_sn,
and refresh_token. gas_token, ip and aim_info are absent. Only shape, constants
and presence metadata were inspected; token values were not printed or saved.

The concrete wrapper constructor is at module offset148974 (SDK_VERSION1.0.0);
`__createSauthJson` at158247 copies the login result's platform. Mpay's
`formatPlatform` at427447 maps p to pc; normal method selection at568900 uses
the method's allowed platform. Thus generic web defaults do not establish the
actual returned platform. The wrapper's SDK instance is private, not a verified
window.sdk/window.unisdk global. Normal refresh behavior is source-verified
below, but successful independent renewal is still NOT live-verified.

`web_sdk_auth_probe.py` and `scripts/probe-web-sdk-login.py` implement a separate,
research-only AUTH experiment. The SDK JSON is preserved without refresh_token;
seven directly corresponding SDK envelope values are copied. Unknown native
fields are omitted, not fabricated. The existing native build_auth validator
is unchanged. Encoding an absent field is supported by the serializer; this
does not prove that the server considers that field optional.

The first one-use localhost handoff expired before receiving credentials; no
game AUTH was sent. Read-only browser permission checks reported granted, and
a later dummy-only preflight/POST succeeded without changing permissions,
network settings or code. The initial timeout's cause was not established.

The subsequent in-memory handoff succeeded. It loaded only the hash-pinned
packaged public manifest, fetched its exact official server-list URL once,
and made one AUTH-only connection. The terminal report was:

```json
{"outcome":"server_rejected_authentication","connections_attempted":1,
 "handshake_bytes_sent":286,"application_bytes_sent":593,"bytes_received":128,
 "prepare_verified":true,"credentials_sent":true,"server_error_code":1911,
 "auth_service_accepted":false,"own_role_verified":false,
 "game_login_verified":false,"career_verified":false,"secrets_saved":false}
```

No gateway entry, role creation, retry, Career query, punishment query or
refresh request followed. Refresh tokens stayed in the browser. The helper
closed and network observation stopped. This is a real rejection of this
minimal envelope, not evidence of an invalid UID or proof that all browser-issued
tokens are incompatible. The hosted site and production provider are unchanged.

The pinned archive's share/errcode.py entry875 maps1911 to
`BATTLE_DS_VERSION_NOT_FOUND` (LOAD_CONST243 at976, STORE_NAME243 at978).
This is distinct from253 `LOGIN_ERR_AUTH_FAIL`. The label points to a version
condition, but does not by itself identify which omitted field is required or
whether the SDK session was validated. No version strings should be guessed.

All21 new synthetic AUTH/handoff tests passed. The full Python suite passed
538 tests in116.645s, exit0. Existing injected-error warnings, temporary-file
ResourceWarnings and the already-running message were non-failing outputs.

### Evidence-backed versioned variant

Further pinned source inspection identified the normal version contract:
entry7800 gen_auth_info line223 assigns `client_version` from
`utils.get_build_change_list()` at472-510 and `engine_ver` from the same value
at776-802, inserted through key tuple40 at902-904. `client_full_version` defaults
to an empty string at512-516; only Development/Test builds replace it. No full
version string was invented. Entry4891 get_build_change_list line1373 derives
the fourth build-version component. s_login_auth line959 explicitly compares1911
at272-302, then invokes show_check_changlist_panel at304-416.

A separately selected `--with-reviewed-version` variant adds ONLY the genuine
packaged provenance value3825464 as client_version and engine_ver. The pure
builder rejects any other supplied version before I/O; it leaves the SDK JSON
unchanged. This is an experiment with a reviewed public version, not a claim
that this build is current after the patch.

One versioned AUTH-only attempt sent286 handshake+618 application bytes and
received128 bytes. Prepare verified, but it returned the SAME1911. No gateway
entry, profile query or further unchanged retry followed, and the helper exited.
Thus those two fields alone do not resolve version selection. Stale public
version versus other absent platform/envelope inputs remains unresolved; neither
attempt establishes a verdict on token validity. The26 focused synthetic
AUTH/handoff tests passed after adding the explicit variant (full538-suite result
above predates these five additional tests).

### Reviewed outer PC selector and pending distributor update

The native source separately sets outer `os_name` from
`get_sdk_prop_str("SDC_LOG_OS_NAME")` (entry7800,360-382). The hash-pinned native
SDK property getter maps that property to literal pc: string RVA0x4b07a8,
matching branch0x1fff9-0x20016, pc stringRVA0x4b07b8, return0x206cd-0x206d9.
This is separate from `platform` inside SDK JSON. The explicit
`--with-reviewed-pc-envelope` variant therefore adds only that reviewed pc
selector to the versioned envelope, without altering credentials or inventing
other metadata. Unsupported os_name values fail before AUTH dispatch.

The third and final AUTH comparison sent286 handshake+625 application bytes,
received128, and again returned1911 after verified prepare. No role was verified,
no gateway entered, and all three helper sessions closed. Stop AUTH testing
with this build rather than trying more unsupported values. The code now also
tracks whether handoff body reading began: an expired handoff after a rejected
or partial body reports credentials_read:null, not a false claim of no read.

Read-only inspection then found a concrete pending update in Steam's public
appmanifest2767030 metadata: installed buildid25098044, TargetBuildID25249397,
BytesToDownload20656586254, BytesDownloaded0. The installed game's version.json
still reports version1.1.3825464/changelist3825464. Its recorded install update
time is September5 00:04:37UTC. Steam's build IDs are NOT the native game
changelist; do not substitute25249397 into AUTH. No download, update, game
launch or Steam credential read was performed. A known downloading directory
for that app was absent, so there is no verified staged version.json to inspect.

The other three reviewed public game configs expose no current-version manifest
URL; DefaultGame has HTTP chunk installation disabled and its directory/version
values empty. An official Steam UpToDateCheck documentation lookup exposed a
public method but its attempted web retrieval did not yield version data; it
does not establish a replacement native changelist. Do not invent one from
patch dates or unrelated version numbers. Next: obtain the current official
distribution update, review its actual public game changelist/material/schema,
then revalidate compatibility before any further AUTH test. The pending update
supports a stale-build hypothesis but does not prove1911's full server cause.

Final verification after all variants and the expiry-report correction:
29 focused AUTH/handoff tests passed;18 existing LOGIN regressions passed;
the complete Python suite passed546 tests in101.007s, exit0. Independent bounded
correctness review found the expiry-report issue, which was fixed and retested,
and no additional issues in the final deltas. No production app or site change.

### Earlier static research and remaining contract limits

Read-only retrieval of the current official store HTML and its public scripts
still identifies `module.894e7ca6.js` and Mpay H5 SDK v1.4.2. This research did
not read browser credentials, submit authentication, or contact the game. The
official store tab was in User ID Login mode, not an authenticated account
session. Account Login was opened; the owner was asked to complete normal
sign-in. A successful UID/IGN lookup is not evidence of an SDK account session.

The wrapper's `__createSauthJson` produces `gameid`, `login_channel`,
`app_channel`, `platform`, `sdkuid` from `userId`, `sessionid` from `accessToken`,
`deviceid`, `sdk_version`, `udid`, and `client_login_sn`. It does not add
`gas_token`, `ip`, or `aim_info`. Browser platform and channel must not be
relabelled as native PC values to satisfy the existing game validator.

Two distinct normal SDK paths are now documented:

1. Wrapper `__getAccessToken` posts `game_id`, `user_id`, and `refresh_token`
   to `/api/users/login/v2/oauth/refresh_token`. On code 0, the returned
   `access_token` replaces browser SAUTH `sessionid`. Failure clears the SDK's
   saved state and opens normal interactive login; it is not a retry loop.
2. Mpay `loginWithHistory` posts `user_id`, `token` (its stored refresh token),
   `cp`, and `platform` to `/api/users/login/v2/sdk_token`. Steam and passport
   methods add `scene: "login"`. Success maps `response.user.login_token` to
   `accessToken`, then wrapper `sessionid`, and `response.user.token` to the
   renewal token. This is an additional legitimate exchange lead, NOT proof
   that its output is accepted by the game LOGIN service.

Mpay requests also include normal `game_id`, `cp`, `cv`, `gv`, `ci`,
`app_channel`, `lang`, `device_id`, `debug_mode`, and `package_name` metadata.
The store has SDK defaults `cp="w"`, `cv="3.1.5"`, but the concrete login result
above independently returned pc. Refresh-token possession, browser success, and a
similarly named `login_token` are not sufficient compatibility evidence.

Store account login uses GET `/gameclub/marvelrivals/login-account`, with
browser SAUTH query fields and ordinary request metadata. The inspected
success handler consumes account identity (`aid`, `username`, `universal_info`)
and account metadata, not a replacement native auth bundle. None of the 13
current store scripts contained the literal `uni_sauth`; this bounded negative
finding does not rule out an internal server exchange.

Primary sources and offsets in the inspected minified versions:

- [Official store](https://pay.neteasegames.com/marvelrivals/topup).
- [Store SDK wrapper](https://pay.neteasegames.com/static/js/module.894e7ca6.js):
  constructor 158237, OAuth refresh 155527, history adapter 147658.
- [Mpay H5 SDK v1.4.2](https://sdk-os.mpsdk.easebar.com/gzip_static/h5/v1.4.2/js/mpay-h5-sdk-oversea.umd.js):
  `loginWithHistory` 809728–810752.
- [Store main bundle](https://pay.neteasegames.com/static/js/main.cfbf45bf.js):
  account request/handler 1104, 25855–32200, 166211–168200.

Remaining acceptance test: establish why the real minimal AUTH envelope above
was rejected, and whether an evidenced compatible request can authenticate the
expected existing game role. Do not manufacture missing native fields or assume browser OAuth is a
gas token. Server-side acquisition, renewal, SocialMatrix access, current
patch compatibility, and hosted full-stat integration remain unverified.

Pinned native-path follow-up (reviewed archive hash in the next section,
entries 4653 and 7800 only):
`userLogin` calls `unisdk.ntLogin`; `on_login_channel_sdk_finish` copies
`getPropStr("SAUTH_JSON")` at offsets 1464–1516. The subsequent nonempty-SAUTH
branch of `gen_auth_info` directly indexes `ip` (1380–1398) and `aim_info`
(1534–1552). It does not use optional `.get` calls for these fields. The actual
browser object therefore cannot pass through this normal native constructor
unchanged. No intermediate browser conversion was found in these Python
methods. This is NOT proof that the server rejects all web-issued tokens.
`gas_token` is forwarded within the whole JSON rather than indexed separately
here, so this method alone does not prove its server-side necessity.

`finalize_sdk_bundle` is only a successful native response finalizer. It does
not perform the `uni_sauth` exchange, obtain platform credentials, supply the
missing metadata, or prove that browser SAUTH can initiate that transaction.
After owner sign-in, first identify the actual token/account/game/channel
contract; then establish an observed compatible exchange or a separately
evidenced browser-compatible game request. Merely passing a web token through
the finalizer would not establish compatibility. No universal need for native
platform credentials is inferred from this bounded inspection.

The final bounded native exchange check verified the existing DLL hash and
traced export `__NtSdkMgr_ntLogin` at RVA `0xdcd0` through the SDK singleton to
the base login routine at `0x34ab0`. That routine manages normal state including
`NT_UNISDK_SAUTH_STATUS` and `NT_UNISDK_SAUTH_ERROR_JSON`; it did not establish
an HTTP interface accepting browser SAUTH or a reusable independent renewal
contract. Steam imports confirm the ordinary client/callback lifecycle, not a
server-only renewal path. [Valve's authentication documentation](https://partner.steamgames.com/doc/features/auth)
separates browser OpenID identity from client-generated authentication tickets;
one must not be substituted for the other.

During that earlier static continuation, Account Login still showed Log in.
The successful owner sign-in and AUTH-only result at the top of this section
supersede that prerequisite. Repeating an unchanged rejected request, the public
SDK fetches or the transport handshake will not resolve token audience or renewal.

## September 11, 2026: static hero, match-history and rank-change contracts

Evidence boundary: the September 8 other-player PC read below live-verified
only five projected aggregate totals: matches, wins, kills, deaths and assists.
The raw Career object had 41 fields, but its complete key list was not saved.
The additional fields and operations here are STATIC evidence, not verified
other-player responses, a public HTTP API, or a working hosted integration.
Do not synthesize a 41-field schema or silently zero unavailable metrics.

Offline inspection used the existing hash-pinned `script.uep` decoder; archive
SHA256 `4dbe9d3246a05d4a402373cc11c03a938692db9bc16aaaf939ddff4e8682d22e`
passed. No game process, network request, credentials, raw capture or recovered
code execution was used. No extracted game code/assets were saved. Source
lines below refer to embedded game scripts, not this Markdown file.

### Other-player operations and history scope

Entry1833, `Scripts/mos_client/MosClient_Career.py`:

- `c_get_history_matches` (line414) constructs `player_uid`, `zone_id`, `page:0`
  and `page_size:50`; the other-player branch selects `query_match_summary`
  via `c_query_match_summary_other_player` (line660).
- Single/multiple match-detail callers (lines432/451) use `player_uid`,
  `zone_id`, `match_uids`; the other-player operation is `uid_query_match`
  (line664), with `is_multiple_match` in its extra context.
- Hero callers (lines360/391) select `query_hero_career` for other players
  (line676). One-hero input has `player_uid`, `zone_id`, `hero_id`; all-hero
  input also has `hero_id:0` and the game's converted `hero_id_list`. The
  normal mode/season/platform filters use `filter`, `game_mode_id`,
  `play_mode_id`, `match_season`, `device_type` with the existing omission rules.
- These operations go through `c_query_career` (line656) and native
  `Career.C_QUERY_CAREER`, not the own-player SocialMatrix `query_my_*` route.
  The live-verified standalone reader supports `query_player_career`. New
  first-page `query_match_summary` helpers are implemented and synthetic-tested
  in `game_career_probe.py`, but are not enabled in the CLI/provider or live-tested.
- Summary handler (line893) reads `data.match_info`, including `match_uid`,
  `match_map_id`, `game_mode_id`, `play_mode_id`, `match_player`, `mvp_uid`,
  `dynamic_fields`, `epic_moments`, `replay_version`; it sorts by
  `match_time_stamp`. Detail handler (line992) reads `data.matches`, each with
  `match_uid` and `match_players`. Hero handlers (lines838/862) read
  `data.careers` or `data.career` respectively.

Only the normal first-page request (0,50) is established here. No other-player
history/hero request was sent; successful later-page pagination, completeness,
and additional response identity binding remain unverified. Existing app
match projection is still own-account/page0 only and drops match IDs/details.

### Independent visibility settings

Entry5465, `Scripts/share/const/const_career.py`, defines overview
`CareerOverviewIsVisibleToOther`, hero `CareerHeroDataIsVisibleToOther`, history
`BattleHistoryIsVisibleToOther`, and timeline `TimelineIsVisibleToOther`.
Its permission-list builder (line58) suffixes overview/heroes for a positive
login OS, but keeps history/timeline unsuffixed. Entry1684,
`PyWidget_CareerPersonalRecord.check_permission` (line287), checks the history
permission without an OS argument; entry1683's hero permission check (line240)
does pass the selected OS. Therefore public overview is NOT sufficient for
history/heroes, and `BattleHistoryIsVisibleToOther_1` is not the normal history
setting. Preserve the existing public/restricted/unknown interpretation and
server denial handling; do not implement relationship or privileged bypasses.

### Additional hero metrics and per-match rank changes

Entry1683, `PyWidget_CareerHeroDataPanel.update_high_light_data` (line526) and
`update_detail_data` (line569), consume these HERO Career fields:

- `total_play_time`: seconds. Entry4617, `ui/utils_ui/career_utils.py`,
  `format_time` (line86) divides by60/3600; per-ten-minute helpers
  (lines471/483) use `stat / total_play_time * 600`.
- `total_match_count`, `total_match_win_count`: hero matches/wins. The latter
  differs from the live-verified aggregate `total_win_count`.
- `total_hero_damage`, `total_hero_heal`, `total_damage_taken`: numerically
  displayed totals; additional server numeric types/scaling are not live-tested.
- `k`, `d`, `a`, `last_kill`, `solo_kill`, `head_kill`: distinct counters.
- Maximum fields include `session_max_K`, `session_max_A`,
  `session_max_last_kill`, `session_max_solo_kill`, `session_max_head_kill`,
  `session_max_hero_damage`, `session_max_heal`, `session_max_damage_token`
  (the last spelling is literal). These are not proven keys of every aggregate
  Career response. No tests currently validate new hero-field projections.

Entry1707, `PyWidget_CareerPersonalRecord_BaseItem.refresh_rank_dan_level`
(line488), reads the selected player's `dynamic_fields.new_level`, `level`,
`new_score`, `add_score`, and `rankscore_info.fixed_battle_count`. It displays
rank changes only in ranked mode with `new_level > 0`; its displayed delta is
`floor(new_score) - floor(new_score - add_score)`, NOT `floor(add_score)`.
Placement counts affect the qualifying label. This is a concrete static lead
for match-linked rank history, separate from the already implemented namecard
per-season current/peak summaries. Other-player availability and strict
match/player response validation must be verified before labeling it working.

## September 8: full other-player Career reply verified

Normal NetEase AUTH + gateway login followed by query_player_career now
returns verified statistics for UID1822797559, KovaaksKid2008, PC, season19.
No local game process or target-account login was needed. The decoder skips
only structurally validated lifecycle messages (65534/111,113,114,120) and
unrelated avatar RPCs; it does not execute initdata or remote hotfixes. A
sequence-reset or unknown command stops the read. The default RPC salt works
for the installed build/current session; no client-ready send was required.

Live reply: 41 Career fields; 19 records, 825198 bytes. Exact method, nonce,
request context, target UID and season validation succeeded. A subsequent
verified read projected 27 matches, 21 wins, 526 kills, 118 deaths, 679 assists.
Only these five count fields are intended for cards. Other fields remain
unmapped and raw replies are not saved. This verifies availability, not a
public/official API contract or perpetual SDK-session validity.

Integration is implemented in CapturedCareerProvider with a shared app-level
game session, after the existing identity/platform/privacy checks. The
previous SocialMatrix query_player_career service500 is not used as fallback;
this is the normal game Career RPC. Keep own Career and rank reads independent.
No Tracker/RivalsData calls are involved. This checkpoint supersedes the
startup-blocked statements below. Source one-action UID and IGN Add both passed
live, using the same shared game gateway. The packaged UID Add and copied
Desktop IGN Add also passed live with stats and projected-only persistence in
separate synthetic vaults. Normal/bulk follow-up verification is recorded in
CODEX_HANDOFF.md.

Packaging correction: inspect-sdk-log.py is bundled as data and its PKCS7 import
was not discovered by PyInstaller. The first executable could read identity/
ranks but failed at own_sdk_login with dependency_missing. Explicitly bundling
cryptography.hazmat.primitives.padding resolved it; the final executable's
full-Career smoke test passes. Fixed-stage diagnostics distinguish this from
an expired/rejected session or invalid target. No raw exception text is exposed.

## September 8 latest: corrected login accepted, gateway UID verified

The post-SDK correction below is now validated by the actual NetEase server:
C_LOGIN_AUTH1349bytes is accepted, S_LOGIN_AUTH returns the unique initialized
own role UID1370598422. New normal C_LOGIN_ENTER71bytes uses the returned
gateway, auth_hostid/auth_sn, empty device_id/basic_info, and that UID. No
game process was present. The helper does not create a role, send
C_LOGIN_RELOGIN, launch/inject the game, or change Wi-Fi settings.

Gateway sequence now verified:

1. Fresh28-byte suite2 opening and286-byte client ACK.
2. Nine main4/sub102 S_PUSH_CLIENT_HOTFIX messages precede the login result.
   Entry5049 schema: split1, path8, content10, signature10. Total content139188
   bytes. Bounds16 messages and64KiB each; contents/signatures are not executed,
   applied, saved or considered signature-verified.
3. main1/sub103 S_LOGIN_RES reports errcode0 and info.uid1370598422 with a
   nonempty login_key. Gateway bytes received through this point142167.
   No key or auth binding was printed or saved. Socket closes after the check.

Important source correction: entry7800 s_login_res only LOGS
msg.get('relogin',False). A value1 after C_LOGIN_ENTER describes server state;
it is not an alternate credential path or failure condition. The helper accepts
0/1/absent as this normal handler does, but still requires successful auth,
successful S_LOGIN_RES, unique initialized own role and exact returned UID.
It never sends C_LOGIN_RELOGIN. The first gateway check stopped at the hotfix;
the second at the initially overstrict relogin check; the third verified normal
login after those specific receiver corrections. No automatic retries.

### Career request implemented; response still blocked by startup framing

`game_career_probe.py` loads the hash-pinned default RPC indices statically
from entry5719; both DEFAULT_RECV_RPC_SALT and DEFAULT_SEND_RPC_SALT use const3.
It calculates Career.C_QUERY_CAREER / Career.S_QUERY_CAREER using the previously
documented ASCII-MD5-word formula, not binary digest words. No recovered game
code or startup hotfix is executed. Runtime salt overrides remain unverified.

Entry1833 c_get_player_career_data builds player_uid + current zone_id, omits
game/play mode when0, adds nonzero match_season, maps PC to device_type pc.
sort_hero is omitted at default count0. Other-player route is
query_player_career; c_query_career sets ex_info.is_career=True. The new probe
also includes career_req and a fresh nonce, then MessagePacks the three
positional arguments in the known main3/sub1 avatar envelope. It sends one
224-byte request for UID1822797559 (KovaaksKid2008), PC, season19. This user
does not need to be locally logged in; only the own shared session is used.

Eleven synthetic Career tests validate wire construction, target/season/nonce/
method binding, no own-player substitution, server-error preservation, and
preview/no-network behavior. Thirteen gateway tests validate normal enter,
startup hotfix bounds, UID/error/relogin semantics, closed sockets and secret
redaction. Eighteen LOGIN and14 SDK input tests also pass.

Latest live result: own gateway login verified; Career verification FALSE.
First attempt stopped at record_framing. Receiver now allows an explicitly
requested4MiB Career record bound with <=64KiB cipher updates; ordinary LOGIN
keeps64KiB limits. Second attempt reads one16-byte main65534/sub114 record,
S_ENTITY_DESTROY (entry5053), then stops as unsupported startup lifecycle.
No decoded target Career data or availability conclusion yet. Next work is
bounded normal lifecycle/wrapped-RPC reception, not more credential guessing.

Relevant entry5053 mapping:111 S_MSG_ACK,112 S_MSG_RESET,113 S_ENTITY_CREATE,
114 S_ENTITY_DESTROY,115/116 migration,117 forwarding,118 S_MSG_OPT_STRUCT,
119 S_CALL_CLIENT_METHOD,120 S_SYNC_CLIENT_PROP. The reliable wrapper and
entity startup shapes still need handling before calling the full API usable.

Regression note: the402-test run before gateway additions had one unrelated
failure, unchanged match_index_test.test_verified_backups_are_pruned_to_configured_retention.
The isolated20-test match-index suite reproduced it. A frozen-clock synthetic
check confirms a filename collision-order bug: three distinct paths are made,
but retention keeps first/third and removes second because the numeric suffix
sorts before the unsuffixed name. No production match-index changes were made.
Final full source suite **426 tests passed in66.669s**; this passing rerun does
not resolve the separately reproduced retention flake. git diff --check and
new-file whitespace checks pass; all command sessions have completed. Existing Desktop
rank/identity build and real vault remain unchanged; full Career/UI integration
and automatic session renewal are still required for the complete goal.

Further entry5053 schema metadata for the next receiver step: S_ENTITY_CREATE113
has eid:int64, classname:string, initdata:blob; S_ENTITY_DESTROY114 has eid:int64
(its handler name is misspelled S_ENTITY_DESTORY). S_MSG_ACK111 has ack:uint32;
S_MSG_RESET112 has reset:uint32. S_MSG_OPT_STRUCT118 has seq:uint32 and is
described as the additional segment of each packet, not necessarily a standalone
wrapper. S_CALL_CLIENT_METHOD119 has func:string; S_SYNC_CLIENT_PROP120 has
entity_id:int64 and sync_msg:blob. Remote initialization data must not be executed.

## September 8 follow-up: actual production LOGIN and SDK response mapping

New research-only `game_config_probe.py` and `game_login_probe.py` close the
configuration/schema gap, but do not yet establish game login or full Career
access. They are not application providers and are not in the Desktop build.
No installed game files, real vault, or Desktop artifact was changed.

### Installed configuration, integrity and endpoint

Installed pakchunk0-Windows.pak is192,038,286bytes with an encrypted v11 index
and221-byte footer (magic0x5a6f12e1). Main index84,832bytes, SHA1
`1e0489547103431956b8771c188bc7ce05db206f`;4704 entries,415 directories.
The verified algorithm reverses each four-byte word of the public distribution
key AND the AES input/output. Plain AES or data-only reversal is incorrect.
The helper keeps only DefaultEngine.ini, DefaultGame.ini, WindowsEngine.ini,
WindowsGame.ini, validates index/data hashes and metadata, and bounds reads.
Nothing is extracted to disk. Optional research dependencies blake3 and pyooz
were added to requirements-research.txt, not runtime requirements.

Public algorithm references actually inspected:
[repak-rivals PAK reader](https://raw.githubusercontent.com/natimerry/repak-rivals/master/repak/src/pak.rs),
[entry layout](https://raw.githubusercontent.com/natimerry/repak-rivals/master/repak/src/entry.rs),
[data decoding](https://raw.githubusercontent.com/natimerry/repak-rivals/master/repak/src/data.rs),
[NetEase AES implementation](https://raw.githubusercontent.com/FabianFG/CUE4Parse/master/CUE4Parse/GameTypes/NetEase/MAR/Encryption/Aes/NetEaseAes.cs).
These were treated as leads and checked against this installed file's hashes.

WindowsGame.ini is355 uncompressed bytes at offset21606425 (53-byte header),
plaintext SHA256
`0609b5302a6cc11e5398a2d58eecd99bc0913d90bc8a46c5717d9ab4046080fc`.
Its /Script/Marvel.MarvelGameInstance ShippingDefaultServerURL selects
EN https://x20na.update.easebar.com/server_pc_prod_serverB (CN is the distinct
x20.update.netease.com host). Normal HTTPS returned200 with this54-byte list:

```text
prod 34.8.137.117 10001 13001 1 1788426300 10001-10010
```

Entry6268 interprets columns as name/IP/port/host_num/status/start_time/range.
Choose ONE advertised port; do not scan them. host_num13001 is not the gateway
port. The previous observed gateway34.60.237.47:10203 is not the initial LOGIN
service. Native get_cur_buildversion0x4bcf0e0 ->0xdab3e0 ->RVA0xa195c68 returns
1.1.3825464.3825464; entry4891 takes split component3, so client_version3825464.
Entry8201/5459 EPlatformType.PC=0, distinct from ELoginOS.PC=1.

### Normal LOGIN protocol and rejected pre-SDK input

Entry5050 schemas are implemented declaratively: C_LOGIN_AUTH main1/sub1,
S_LOGIN_PREPARE main1/sub101, S_LOGIN_AUTH main1/sub102 and nested zone records.
Extended field IDs16+ use BIG-endian16-bit tags, not little-endian; integer
values and lengths remain little-endian. Structs have byte lengths; arrays
have total byte lengths and individually framed dictionaries, not item counts.
Native references0x5485340/0x5485ba4,0x5484cd0,0x5481480/0x5483b60.

One fresh normal suite2 connection to the advertised login service received
28-byte opening plus26-byte prepare (19-byte body), after286-byte ACK.
All five prepare fields were decoded: empty version_list, empty args,
is_test_branch0, fresh timestamp, timezone0. The helper refuses unexpected
args/version requirements rather than executing them or inventing answers.

Two subsequent one-attempt connections each sent1803 application bytes and
received358 bytes total (opening28+prepare26+auth304). Both returned game
code253; the second redacted SDK diagnostic was code502/subcode0. No automatic
retry, game role enter/create/init, or Career request. Source entry875 labels
253 LOGIN_ERR_AUTH_FAIL. SDK code502 is not established as HTTP502 or an expiry
diagnostic. These attempts used the earlier HTTP uni_sauth bundle, not final
post-SDK SAUTH_JSON. Do not repeat that unchanged request.

### Correcting the actual post-SDK SAUTH_JSON

Hash-pinned NtUniSdkBase.dll SHA256
`d134ce2e46c1b4fb97b569db9fa3f3b9052309a0d9e96f9a3adf39e08b498163`:
0x2b600..0x2cd13 processes successful SDK response, stores UNISDK_LOGIN_JSON,
then calls0x416c0 at0x2c02a. 0x417a6 calls base64 decoder0x11f770; the result is
JSON. No native code was executed. Entry7800 later reads SDK getPropStr
SAUTH_JSON and gen_auth_info forwards it with corresponding identity overrides.

Verified normal updates in0x416c0:

- Response sdkuid -> UID property, then SAUTH_JSON.sdkuid via0x4195b/0x42d50.
  The setter adds or replaces the named JSON string property.
- Optional sdk_token -> GAS_TOKEN and fill-empty SAUTH_JSON.gas_token via
  0x41a71/0x42740. Existing nonempty gas_token is preserved. The own observed
  response has NO sdk_token; do not replace gas_token with OAuth credentials.
- oauth2.access_token/refresh_token -> UNISDK_JF_ACCESS_TOKEN/REFRESH_TOKEN
  at0x41d1d/0x41d43. oauth2 is a dictionary, not a scalar. Final use of these
  properties remains under static review; do not assume they belong in sauth.
- extra.channel_raw_info is parsed from a JSON STRING at0x41fb2. token is the
  initial session; nonempty login_token is preferred at0x42208. SESSION and
  SAUTH_JSON.sessionid are replaced at0x42295/0x422de. The parallel0x42334 setter
  updates SAUTH_STR. Function0x42a40 also concerns SAUTH_STR, not JSON.

The own SDK HTTP200 success response at2026-09-08 04:19:30 contains these fields
and both replacement identity/session differ from the original input. Inner
and outer SDKuid/aid agree; chl_sdk_json matches gameid/login_channel/udid and
response identity; outer data.app_channel matches the input. The response
appears BEFORE the standalone telemetry copy of the earlier request, so the
new loader groups the exact timestamp and rejects ambiguous/missing pairs.
These are local consistency checks only. Signatures and live validity are not
verified. An expected initialized existing role UID1370598422 and successful
server error code remain mandatory before any gateway connection.

Fourteen new synthetic SDK-input tests pass alongside18 LOGIN tests. Corrected
own auth data reconstructs locally to1342-byte body, with sdkuid consistent
between the envelope and sauth_json. No corrected-input live result yet at this
checkpoint. Full regression run is pending. The Wi-Fi investigation caused a
temporary network pause, now cleared by the user reporting that they fixed it;
no Wi-Fi settings were changed by these helpers or diagnosis.

## September 8 follow-up: live game transport, login still separate

New research-only `game_transport_probe.py` implements the installed suite2
key exchange and continuous directional AES-256-CTR streams. It is not imported
by the application or included in the existing Desktop build. It previews by
default; `--handshake` sends only the normal key exchange, and `--echo` adds
one native heartbeat. Neither mode loads account credentials, logs in, requests
Career data, reads a process/capture, writes files or retries. Output contains
structural metadata only. No real vault or game files were modified.

Verification:13 new synthetic transport tests pass; full source discovery
passes **358 tests in170.033s**. Coverage includes fresh RSA randoms, directional
CTR state across fragments, native ACK/echo layout, mismatched/all-zero echoes,
public-key pin/assignment shape, bounds, preview/no-network behavior and
single-attempt socket cleanup. git diff --check reports no whitespace errors.
These checks do not validate account login or Career availability.

### New live evidence and its limits

Three bounded connections to the previously game-observed34.60.237.47:10203:

1. Handshake only:28-byte hello received,286-byte acknowledgement sent, then
   timeout with no additional bytes. Not proof of key-exchange acceptance alone.
2. Handshake + one12-byte encrypted C_SYS_GAME_ECHO: a complete20-byte encrypted
   response decoded to main65534/sub103 (S_SYS_GAME_ECHO),13-byte body.
3. A focused repeat to inspect the nonmatching echo fields confirmed all three
   returned heartbeat values are zero. Field order was3,2,1. The second and
   third runs each received48 total bytes (28 opening +20 response).

The independent encrypted transport can now receive a structurally valid,
expected game-protocol reply. The requested nonzero timestamp did NOT echo:
`plaintext_record_verified=true`, `echo_verified=false`. No account login or
stats were sent/verified, and a zero reply must not be promoted to timestamp
correlation or Career success. All sockets closed. No further repeated echo
is currently useful. Entry5053 gives heartbeat main65534/sub1 request with
timestamp:int32, response sub103 with timestamp:int32,svr_ts:int32,svr_ms:int16.
The live zero-body fixture is public protocol metadata, not account data.

### Configured public key, parameter and exact acknowledgement

Installed archive/native hashes remain those below. Static archive entry3145,
`Scripts/core/bootstrap.py`, assigns True to USE_PUB_KEY at offsets96/98 and
const7 to MOS_PUB_KEY at100/102 (custom opcodes204/140). Const7 is a complete
PEM RSA1024 public key, not bare base64. SPKI-DER SHA256:
`d37c1ab339cfd920f261b8457f9e5dadac6c076684ba93c51987d2299464401c`.
The normal bootstrap passes it to mosclient.set_rsa_pub_key. The loader parses
only this hash-gated module/data; it does not execute the recovered function.

Native global manager constructor0x546dca0 assigns18 bytes from RVA0xb41fb78
to manager+0x300 at0x546df98..0x546dfac. Parameter SHA256:
`e0f8b1c2e1e906a1d4cf345c554852a844cc8bc1411c0b7ba2385885cd4a9c49`.
Connection creation0x5486f80 copies it to child-manager+0x268;0x5475d00 passes
it through0x547df20 to connection+0x1c8. It is not empty and is not the17-byte
SocialMatrix parameter. The only located direct caller of setter0x549b390 is
the Python set_secure_suite binding; the earlier name scan found no Python
call to it. This does not prove no dynamic/native override can ever occur.

The normal suite2 branch XOR-folds all18 bytes over the16-byte server challenge,
then SHA1s the16-byte result (0x547b0a0..0x547b123). Native0x5487860 serializes
uint32 total size followed by three uint16 length-prefixed fields: proof20,
RSA-encrypted first client random128, RSA-encrypted second client random128.
Total286 bytes, without suite/compression bytes in this direction. Public-key
encrypt helper0x5478a70 uses EVP_PKEY encryption with no padding override;
OpenSSL's [RSA implementation](https://github.com/openssl/openssl/blob/OpenSSL_1_1_1-stable/crypto/rsa/rsa_pmeth.c)
initializes the corresponding default to RSA_PKCS1_PADDING. Each32-byte AES key
is one new16-byte client random followed by the server challenge; IV is the
client random. Receive uses the first, send the second. Randoms are fresh for
this independent connection, not recovered from any game session or private key.

### Normal login route and current remaining inputs

Entry7799 `Scripts/client/client_logic.py` owns separate login/gate client
objects. Entry7800 `login_mgr.py` sends C_LOGIN_AUTH through the login client,
then checks S_LOGIN_AUTH.err_code before using auth_hostid/auth_sn and the
returned zone_list gate_ip/gate_port/role_uid. C_LOGIN_ENTER goes to that
returned gateway with auth_hostid,auth_sn,device_id="",basic_info="",uid.
Bind the existing expected own UID1370598422 and reject failed auth, missing or
uninitialized roles. Never create/init a role, use another role, suppress server
errors or substitute relogin/developer/attestation bypass paths.

Entry7800 gen_auth_info uses real SDK properties, then its nonempty sauth_json
overrides sdkuid,ip,sdk_version,device_id/unisdk_deviceid (deviceid),app_channel,
channel (login_channel),udid,ip_info (aim_info). It supplies build change list as
client_version/engine_ver; shipping PC blanks info.local_host_name. Source
entry5050 contains C_LOGIN_AUTH and nested S_LOGIN_AUTH/S_LOGIN_RES schemas.
These credentialed messages are NOT implemented/sent in this new helper yet.

The exact initial production login endpoint remains unlocated. Entry6243
PyLoginPanelV3.refresh_server_list performs a normal GET to entry7805's
get_shipping_server_list_url, whose source is package_data.ShippingDefaultServerURL
or the game-instance property. JSON-valued URLs select EN for overseas. Entry6268
login_server_data_builder parses the returned server list. Its separate developer
zone-id portal route is not established as the shipping route and was not used.
Do not assume the previously observed gameplay gateway is the initial login server.

Read-only checks found native LoadPackageConfig0x68abb30 reads local meta.data;
the32-byte distribution config decoded to JSON with only is_oversea, no URL.
GetCustomPackageConfig0x68a1fc0 optionally reads Saved/Config/CustomPackageSetting.ini;
no such file was found. Saved/current_connect_info is simply base64 JSON from
entry4891 record_connect_ip, currently tag=exit and no addresses. Installed
version.json says Shipping,version1.1.3825464,changelist3825464. Relevant DNS
cache query returned no entries. Web searches for these exact configuration
names produced no useful primary-source endpoint; no third-party claim was adopted.

The authorized own SDK log has rotated: current97,880-byte NtUniSdk.log has124
valid September8 records, two SDK JWTs with future expiry CLAIMS but no complete
sauth bundle. Scoped NtUniSdk.log.1 is5,244,227bytes/8,711 valid records and does
contain a latest complete SDK bundle stamped2026-09-08 04:19:30 in the log,
with gameid=x20naxx2gb and all sauth-based override fields above. Inspection
remained transient/redacted; no SDK HTTP request, export, credential use,
signature verification, fresh game login or role binding was performed.

Next: resolve the actual shipping login server from the installed configuration
or an ordinary observed login; implement/test the exact normal auth/reply codec
and verified UID binding before any authenticated Career call. No further user
restart/capture is currently requested. Desktop rank-card delivery remains
unchanged; detailed other-player Career and session renewal remain unfinished.

## Prior September 8 checkpoint: offline avatar-RPC contract, not live Career access

The Desktop one-step lookup build below is unchanged. New `nex_rpc_probe.py`
is an offline research decoder only. Thirteen synthetic fixtures pass, including
four new opening-parser cases. Final full source discovery passes
**345 tests in122.841s**; git diff --check has no whitespace errors. The separate receive-only opening
check below made one TCP connection, but no application bytes or credentials
were sent. No fresh recording, game process/memory access, recovered-code
execution, raw-response output, or real vault writes were performed.

### Live opening check changes the next step

One bounded connection to the previously observed game gateway
**34.60.237.47:10203** succeeded and returned a complete28-byte hello: uint32
length28, secure_suite2, compression_flags0, then three length-prefixed fields
with lengths16/0/0. This happened without launching the game, sending any
application bytes, credentials, login, stats query or key-exchange response.
The socket was closed and the challenge discarded; only framing/mode metadata
was printed. Endpoint attribution comes from the earlier game-owned socket,
not a claim of cryptographic server authentication from this opening alone.

The matching native handshake branch starts at0x547acde. It requires configured
RSA public-key state at manager+0x288 (missing-key path0x547ad1b); generates two
16-byte client randoms via0x9839ee0 at0x547add0/0x547adfd; builds each directional
key from a client random plus the server challenge at0x547ae7a/0x547aed2; and
encrypts the client randoms through0x5478a70 before sending the opening reply.
That helper calls the EVP public-key encryption initialization/operation paths
at0x9839b20/0x98399d0. No game routine was executed.

Suite2 context vtable0xb41f738 uses initialization0x547e3c0. It passes the
32-byte key and its first16 bytes as the IV to the cipher selected by0x9837920.
Both acceleration variants point to descriptors0xd463220/0xd463280 with
`nid906, block_size1, key_len32, iv_len16`; OpenSSL's
[object definitions](https://github.com/openssl/openssl/blob/OpenSSL_1_1_1-stable/include/openssl/obj_mac.h)
identify906 as AES-256-CTR. Do not substitute SocialMatrix's suite1/RC4 decoder.
The native Python binding `set_rsa_pub_key` is at0x549ca90, forwarding through
0x549b1b0. Public-key PEM markers at0xb421fd8/0xd4cb728 did NOT parse as complete
usable keys in the bounded checks; neither is a verified configured key.

**Important correction:** a passive capture of the start of this suite2 game
connection is not sufficient by itself to recover the freshly generated client
key material protected by public-key encryption. The old "just capture the
handshake" plan was not established for this separate protocol. Do not request
another restart on that premise. A legitimate independent client can create
its own key material, but the actual installed public-key configuration, normal
handshake acknowledgement and own-game authentication still need implementation
and verification. No key exchange or game login has yet been completed by our
program. The new `decode_game_hello` only parses the observed mode and explicitly
does not authenticate, decrypt another connection, or provide stats access.

Next useful work is the normal independent game connection/login path: inspect
entry7800 login_mgr C_LOGIN_AUTH/C_LOGIN_ENTER and the existing authorized
game-specific SDK log source described below. A SocialMatrix JWT is not proven
valid for game login; SDK credential freshness/ownership and returned role UID
must be checked, and a new login must not silently replace an active game.
No further game interaction was requested; the user was told another restart
is not needed right now.

### Request and response route

The same hash-gated `script.uep` establishes this route:

- Entry1833 `MosClient_Career.c_query_career` sets `ex_info.is_career=True`,
  then calls `PlayerAvatar.Server["Career"].C_QUERY_CAREER(url,data,ex_info)`.
  Its other-player profile wrapper selects `query_player_career`.
- Entry5312 `mos_server_proxy` forms `Career.C_QUERY_CAREER`, calculates a
  send RPC index and uses `Efun.dump_msgpack(args)` for positional arguments.
- Entry8739 `net_avatar.C_CALL_RPC_METHOD` creates `{rpc_index,args}` and
  calls entry4891 `utils.send_tcp_msg_to_gate`; that forwards to native
  `gameinstance.client_logic.send_gate_msg`. No recovered function was invoked.
- Entry5051 `avatar_pto` defines main3/sub1 for outgoing calls and main3/sub101
  for incoming calls. The schema is field1 `rpc_index:uint32` and field2
  `args:blob`; these are not the SocialMatrix JSON envelopes.
- Entry5308 `CareerComponent.S_QUERY_CAREER` emits five reply arguments:
  `(url,ex_info,err_code,err_msg,data)`. Entry1817
  `s_query_other_player_career` checks `ex_info.is_career`, reads target UID
  from `ex_info.career_req.player_uid`, rejects its own-player case, selects
  that target's Career object and dispatches the operation. Entry1833's profile
  handler rejects nonzero errors and consumes `data.career`, `sort_hero`, and
  `hero_careers`; `career.player_uid` is a separate returned identity field.

The reply context and returned UID must both match the intended query before
any future stats integration. Platform, season, privacy, method mapping and
same-connection request/reply provenance also remain required. Neither a decoded
RPC index nor a matching UID alone proves a fresh successful profile lookup.

Entry5719's `calculate_rpc_index` hashes `(name+salt).encode('utf-8')` with MD5,
then operates on ASCII hex characters (not raw digest bytes): four big-endian
32-bit groups from the first16 characters, with each first byte masked127,
multiplied together; result `((product & 0x7fffffff00000000) >> 32) ^
(product & 0x7fffffff)`. Default send/receive salts remain in RAM, not printed
or saved. A metadata-only full archive scan decoded9717/9726 entries (one
bootstrap skipped, eight undecodable); only entry5719 referenced those salt
names. This does not rule out native/runtime overrides or establish live IDs.

### Plaintext wire encoding

Native image SHA256:
`54154e879731f3f6fad32371bff78d62629c3d87ad5443dc9466871312a76714`.
All locations are build-specific RVAs in read-only PE data, never process VAs.

- `ProtoUtil.pack_nex_for_send` binding0x545e360 writes uint32 total length,
  uint16 main command, uint8 subcommand; 0x545e56b applies the low28-bit length.
  `pick_nex_cmd_index` at0x545e85d reads `(u16_at4 << 8) | byte6`.
- Descriptor parser0x5485340 maps schema uint32 to wire3 and blob to wire7;
  0x5485388 encodes small field tags as `(wire_type << 5) | field_id`.
  Therefore the avatar fields use **0x61** and **0xe2** respectively.
- Integer writer0x5484880 writes little-endian uint32. Blob writer0x5481a95
  writes a little-endian uint32 byte count followed by the blob bytes.
  Top-level map writer0x5483b60 writes tagged fields without a field count;
  the field order is not assumed fixed.
- Existing transport parser0x5479fe0 uses a seven-byte ordinary header;
  flag8 instead uses nine bytes and excludes the indicated trailing extension.
  Other flag meanings and live compression/encryption state remain unverified.

`nex_rpc_probe.py` requires a complete already-plaintext stream, supports only
these validated flags/fields, and rejects gaps, incomplete records, duplicate
fields/map keys, unknown tags, unsupported MessagePack extensions and excessive
sizes. It does not scan arbitrary prefixes, infer method IDs, save arguments,
decode an encrypted game connection or send/replay requests. Fixtures include
a hand-authored18-byte wire record, reversed field order, incoming/outgoing
direction checks, extensions and malformed inputs. **No captured game RPC has
yet been decoded with it. It is not imported or shipped by the app.**

### Remaining capture gap

The normal startup lookup's decoded SocialMatrix `hero_info` is cosmetic data;
`hero_proficiency_infos` provides proficiency levels/points, not aggregate
Career kills/deaths/assists. These are not a substitute full-stats endpoint.

Re-auditing the older successful own-session recording
`career-netsh-a7910f664b944d5ba0e4d88f63c51209` found161 complete in-scope packets:
134 on35.236.255.68:12101 and27 on8.221.212.39:21308. Both have TCP handshakes
and contiguous streams, but neither is the main game-gateway connection.
There is no missing game setup to recover from that trace. Newer lookup-pass
limitations remain as recorded below; no missing bytes were filled in.

Local `netsh trace show capturefilterhelp` lists address/protocol/custom-offset
filters but not TCP.Port or per-process packet filters. Microsoft's
[Netsh tracing guidance](https://learn.microsoft.com/en-us/windows/win32/ndf/using-netsh-to-manage-traces)
documents using this local help and address restrictions. Do not assume a port
filter exists from generic documentation or broaden to all computer traffic.
No dumpcap/tshark executable or Npcap driver/service was found in checked
standard locations. A further recording would need a demonstrated way to solve
the suite2 key-state gap above as well as gateway/full-packet coverage; another
identical fixed-IP run is not the next action.
Netsh currently reports no active trace; no game is running and no additional
user launch/restart action has been requested at this checkpoint.

## Outcome

**September 8: one-step public UID/IGN cards delivered, full Career still open.**
The same legitimate shared session supports normal other-player identity and
rank reads without per-target local login. Live packaged UID-only and IGN-only
Add both resolve **KovaaksKid2008 / 1822797559 / PC**, current internal-season19
**One Above All5545**, highest returned peak**5601 (season5)**. The default
card workflow performs lookup during Create/Save; no second linking step.
The user's earlier UID10826896958 is not this target and was not substituted.

Installed SvcUser entry3010 defines CmdGetUser7/reply8 and CmdSearchUser15/reply16.
Entry3012 GetUser sends `{aid: "11001_<UID>"}`; SearchUser sends
`{name, cursor:0, limit:10, host:11001}` (entry7609 confirms accurate-search
arguments). Use the fresh advertised svc-user module (observed main_cmd4), outer
data_cmd0 and body.cmd, not a guessed hardcoded route. Identity is independently
validated: exact zone/UID echo for GetUser, or one unique exact-name result for
SearchUser. Raw social/presence/third-party account fields are discarded in RAM.

Target ranks use the already-confirmed `xyq-namecard/GetData` template with
selected zone_UID and `fields:"rank_game_season"`. The rank-only reply does not
echo UID: fresh correlated RPC and verified selected identity bind it. The
normal game's full namecard reply has a BARE aid string"1822797559"; do not
mistake that for SvcUser's zone_UID format or invent a required echo.

New available stats: target season19 row contains **battle_count27/win_count21**,
also present in its individual rank_game_1001019.rank_game record. The independently
verified own season17 rank row47/20 agrees with the separate Career profile47/20.
Provider projects these as **ranked-season matches/wins**, not full Career totals
or K/D/A. Counts are filtered by platform, normal-ranked mode and exact season;
missing/invalid counts remain unavailable, not zero. Old-season records cannot
replace current counts. Final EXE live tests verified27/21 survive card persistence.

Platform/privacy: config_server.login_os is string"1" for the target. Installed
const_career entry5465 maps OPEN0,SELF1,FRIEND2,TEAM3,CLAN4; entry4617 resolves
platform-suffixed overview permission before legacy/default0. Target PC overview
is0; its other platform settings differ. Only public selected-platform overviews
are queried; restricted/unknown settings return identity plus an explicit note.

Full other-player Career is still separate: entry1833 sends query_player_career
through PlayerAvatar.Server["Career"].C_QUERY_CAREER. The one earlier capability
test through SocialMatrix returned service500 and was not repeated. The normal
game does not establish that this operation is supported by xyq-career; no
query_my_* request is redirected to another UID as a workaround.

User-performed normal lookup capture90s (diagnostics player-career-331bbce8fc40411c87894282cc8aad79)
contains game34.60.237.47:10203 and SocialMatrix35.236.255.68:12101. The outgoing
game run was contiguous2324bytes, but the incoming run had39760 missing bytes in
two truncated packets and no handshake. NDIS event1002 was metadata, not a
continuation payload. Microsoft's [ETL converter source](https://raw.githubusercontent.com/microsoft/etl2pcapng/main/src/main.c)
confirms captured/original lengths must remain distinct; no missing bytes were
invented or overlapping runs joined. No game RPC was decoded from this pass.

The second startup pass (player-career-1341e57ffb4d40d0bed16e5a7bf06a6b) used the
same two peer IPs, nominal180s. It stopped after~166s when the new gamePID8284
exited; native recorder stop succeeded. It captured126 complete in-scope packets,
ONLY the SocialMatrix peer, including full handshake and contiguous streams.
Existing decoder recovered35 inbound/34 outbound records and the normal target's
namecard, achievements, item_list, hero_info and hero_proficiency_level reads.
No target xyq-career call was seen. The game gateway was not captured; it may
have changed, but filtered connection metadata cannot prove that. Do not claim
a successful full-game startup capture or silently widen the filters next time.

Both recorders are stopped; no further game interaction is currently requested.
`capture-player-career.ps1` has preview/fingerprint, exact process/peer checks,
90s normal /180s startup modes, and bounded named-session cleanup; 28 mocked
OS cases pass across Windows PowerShell and PowerShell7. The offline inspector's
explicit truncated-prefix diagnostic leaves the production strict decoder intact.

Final verification: **332 tests passed**, all3 JSX transforms passed; synthetic
browser checks verified UID/IGN Create, edit refresh, restricted labels and
ranked-record layout. Both final packaged UID and Desktop-copy IGN live Add
tests passed with separate synthetic vaults. No real vault changes. Desktop
artifact/hash/shortcut and remaining session renewal limitation are in the
latest CODEX_HANDOFF.md and CAREER_PREVIEW.md. The full user goal remains active.

### Earlier September 7 checkpoints (superseded where noted above)

**September 7 evening: direct rank reads and NetEase-only card refresh work.**
The remaining namecard lead is now tested with the same legitimate own session:
`xyq-namecard / GetData`, POST body `{host: <captured zone>, tag: "player",
id: "<zone>_<own UID>", fields: "rank_game_season"}`. The template comes from
the successful captured own-player achievement query (out19, correlated reply),
and the rank field/decoder comes from installed entry1833 `s_request_rank_data`.
Only that one field and the confirmed own ID can be requested; no other-player
or leaderboard lookup is used. Each read has fresh authenticated RPC correlation.
GetData does not echo a UID: identity evidence is request/session binding plus
the separate verified own-profile read, not a falsely claimed echoed rank UID.

The response is JSON-in-JSON: `data.rank_game_season` maps rank keys to JSON
strings. Installed entry5471 `get_rank_key` is OS*1000000 + type*1000 + season;
entry5459 maps PC=1, PS=2, Xbox=4 and normal rank type is1. The provider projects
PC/normal only. Entry4612 `get_highest_rank_info_of_login_os` compares `max_level`
and `max_rank_score`; `season_max_level` is a distinct reward-related field and
must not be substituted. Rank labels use the existing ordered division model,
one-based levels1..23, not guessed from points. Independent user confirmation
of the displayed labels was requested and remains pending at this checkpoint.

Own UID1370598422 live result: key1001019 / rank_game_id19, level14,
rank_score4350, fixed_finished0, fixed_battle_count0, update_time1787093892.
This displays **estimated Diamond II / 4350, placements0/10**, not a completed
placement rank. Key1001017 has level17/rank_score4659.659451053589,
max_level21/max_rank_score5016.412060008755, battle_count47, win_count20,
fixed_finished1. The 47/20 and latest score independently agree with Career
profile and own-match summary. Highest returned PC peak is **Celestial I /5016**
in season17. Current season19 aggregate is allzero and is kept separate from
season17 totals. Returned update times are not silently replaced with fetch time.

The game's [official placement explanation](https://www.marvelrivals.com/20251114/41525_1270590.html)
distinguishes the estimate shown during the initial ten matches from the eventual
placed rank. This corroborates the fixed_finished/count interpretation. No
third-party player profile was fetched to supply these ranks. A public RivalsData
homepage research fetch was unavailable; it was not retried or bypassed.

Implementation: `career_provider.project_ranks`, `refresh_account`, selected
socket kind `ranks`, `_try_netease`, identity-bound commits, preserved lifetime
peaks, explicit placement/season labels and independent match-summary failures.
Card refresh uses the default season observed at login; historical panel season
selection never overrides it. Recent duplicates share only a successful recent
same-season snapshot. Source tests cover failed reads, unknown levels, platform
and UID mismatch, malformed nested data, vault locks during a read, and no
Tracker fallback. No match-index imports or credential handling were changed.

The new Career package sets `stats_provider: netease` in build metadata and
excludes cloudscraper. Even the bare EXE with no connected session cannot fall
back to Tracker. Legacy source launches without Career flags still retain the
old provider. Normal game was not running during the successful direct rank/card
probes; no admin, new capture, memory access or game restart was needed.

304 tests pass in47.980s; all three JSX bundles transform. One earlier full run
hit an existing fast backup-retention test failure; the isolated test and two
subsequent full runs passed. No match-index code/test was changed to hide it.
UI inspection caught and fixed an overlong estimated label and an existing narrow
table selector that hid current rank instead of peak. The synthetic card, table,
ladder and panel show the separate current/historical seasons and placements.
Packaged/desktop delivery verification is recorded in the latest CODEX_HANDOFF.

**Delivered September 7: working own-session NetEase Career desktop preview.**
`career_provider.py` and the app's new Career panel now perform independently
authenticated socket reads for the selected own-account season and 50 recent
matches. Live internal-season17 totals: 47 matches, 20 wins, 661 kills,
293 deaths, 828 assists. Computer Use confirmed the game's displayed Rocket
74 KOs / 314 assists match its decoded season17 reply. UI uses internal season
IDs explicitly and preserves the separate Tracker rank-refresh behavior.

284 tests pass; synthetic UI rendered correctly; the packaged EXE passed a
live two-read test with an isolated synthetic vault. An identical 24,010,585-byte
copy and configured shortcut are on the user's Desktop. No capture/token/game
archive is bundled or copied there. Session claims expire about seven days
after login, subject to earlier invalidation; automatic renewal and direct
rank integration are not implemented. See CAREER_PREVIEW.md and the latest
CODEX_HANDOFF checkpoint for exact delivery/test details.

**September 7 breakthrough: standalone own-account Career socket query verified.**
The approved Netsh run `career-netsh-a7910f664b944d5ba0e4d88f63c51209` captured
startup for gamePID24284. All161 packet headers match the two-IP scope. The
12101 connection's opening validates against hash-gated installed NetSecurity
code; continuous RC4 decodes71 complete records. JSON/uni_lz4 envelopes then
base64(JSON) requests and base64(gzip(JSON)) replies yield14 successful Career
request/reply pairs for own UID1370598422. Profile zone11001/PC/season19 has zero
aggregate stats; match-summary returns50 matches, first own-player K/D/A36/6/7
from season17. Different seasons must remain distinct.

`career_socket_probe.py --send` then independently authenticated a new connection
with the captured game-issued session and sent one own-profile query. NetEase
accepted it and returned the correct own UID; totals matched the game's captured
response. No tokens/keys/plaintext were exported, no process memory or code
injection, and no game/vault changes. This establishes a working authenticated
socket request, NOT anonymous UID access or how Tracker/RivalsData obtain theirs.
One separate verified-TLS HTTPS test at the session's websocket gateway returned
403; that HTTP transport is not verified. Do not repeat it or weaken TLS checks.

New helpers: `scripts/inspect-netsh-career.py`, `socialmatrix_capture_probe.py`,
`career_socket_probe.py`; optional dependency lz44.4.5. LZ4 framing was confirmed
by exact length validation and JSON parsing of every compressed envelope, using
the [python-lz4 block API](https://python-lz4.readthedocs.io/en/stable/lz4.block.html).
RC4 implementation matches [RFC6229 vectors](https://www.rfc-editor.org/rfc/rfc6229).
The hash function in installed entry43 is34*h+signed_char, not standard DJB2.
The top CODEX_HANDOFF checkpoint contains exact capture/protocol evidence and
continuation steps. Goal active; UI integration/desktop build still pending.

Historical checkpoints below are superseded where noted:

September 6, 04:29: goal BLOCKED on manual launch coordination. Computer Use
opened Steam and then the normal Marvel Rivals launcher (PID 26160), but a
screenshot-backed Launch click did not start the game; cause is unknown. A
second same-scope Netsh experiment (04:26:14.451-04:27:44.624) stopped cleanly
with no game in any of 89 observations and no NDIS packet events in its ETL.
The launcher remains at yellow LAUNCH; no recorder is running. Resume only on
explicit user readiness for a manual click, not an automatic goal continuation.
Current handoff has exact run evidence. Full suite remains 244 passed; no API
verification/integration, credential changes or new desktop build.

September 6, 04:21 update: the first approved fixed-two-IP Netsh run completed
04:18:25.752-04:19:56.629 and stopped its own named session successfully. The
game never appeared in any of 90 process observations. The 5 MB ETL contains
filter rules and Windows trace/system metadata, but ZERO NDIS packet events;
no Career/handshake evidence or live filter-enforcement proof was obtained.
Do not automatically repeat. Wait for explicit readiness at the final Launch
button; game closure alone did not establish that readiness. Full suite now
244 passed. See the top CODEX_HANDOFF checkpoint for run details and the
non-elevated session-visibility caveat.

September 6, 04:16 update: the user approved a 90-second local Netsh capture
of IPv4 TCP at only 35.236.255.68 and 8.221.212.39 (all ports at those IPs).
The new preview-first `capture-career-netsh.ps1` is prepared; 11 mocked cases
pass in each installed PowerShell engine. No Netsh recording has started:
the user must first close the game and remain at the launcher. Signal READY
only after the recorder reports successful startup, then they press Launch.
See the current CODEX_HANDOFF checkpoint for exact scope/fingerprint. PktMon
recording remains held after the demonstrated scope discrepancy. Read-only CIM
metadata additionally found 34.60.237.47:10202; this is outside the approved
Netsh scope and has not been added or identified as Career traffic.

General-player NetEase stats refresh is **not implemented or verified**. There
are working first-party match-detail routes, but the tested ordinary competitive
match is absent from those routes. Tracker and RivalsData serve the supplied
profile to a normal browser while rejecting the tested standalone HTTP clients.
Their browser APIs do not reveal their server-to-NetEase requests.

Latest offline follow-up: `tls_midstream_probe.py` now passes independent RFC
8448 application-record/key-derivation vectors without hello messages, plus
synthetic tests for its bounded TLS 1.3 workflow. It emits only metadata and
fixed markers, not decrypted contents. Full suite: **208 passed**. No September 5
capture had appeared at the 17:22 local metadata check, so no game traffic or
Career response has been decrypted. See "Bounded TLS 1.3 midstream verifier".

Latest live diagnostic result (2026-09-05, after the user's administrator launch):
the diagnostic run produced a valid local key log. Game PID 16172 is running and
the user reports the lobby/Career screen. A bounded format check found 32 session
identifiers with all four TLS 1.3 handshake/application labels and six
`CLIENT_RANDOM` entries; no secret or random value was output. This is not yet
a correlation to a game connection, a decrypted Career request or working API
access. A narrow, current-game port-443 capture is prepared for the user to run,
but no new capture has started. Full suite: **192 tests passed**.
See "Live diagnostic key log and next capture" below.

Latest authorized diagnostic result (2026-09-05): the user explicitly approved
the temporary local-only TLS launch and the normal game session exited. The
first attempt exposed a PowerShell Core ACL compatibility bug; this is fixed
and tested in both installed PowerShell engines, with all **191 tests passing**.
The second attempt reached process creation but Windows rejected it because
the installed launcher requires elevation. No diagnostic launcher or key file
was created. The worker remains non-elevated; no privilege settings changed.
The already-approved helper must now be run from Administrator PowerShell.
See "Authorized launch attempts and PowerShell compatibility fix" below.

Earlier session update (2026-09-05, 16:22 local): the user manually launched the
game, without the prepared diagnostic helper. Read-only process/TCP metadata
confirmed game PID 4772 and associated its remote addresses with cached names
`x20na.gph.easebar.com`, `x20na.update.easebar.com`, `whoami.nie.easebar.com`
(port 443) and `s.q.easebar.com` (port 21308). DNS-cache associations do not
prove TLS hostnames, service function or Career routing. No diagnostic launch,
key file, packet capture, memory read or authenticated request occurred. The
game was left untouched. At that earlier snapshot the diagnostic approval was
still unanswered; the subsequent explicit approval and attempts are above.

Latest follow-up (2026-09-05): the installed executable contains libcurl's
`SSLKEYLOGFILE` implementation **and the callback registration path**. A tested,
opt-in normal-launch helper is prepared and now explicitly approved, but live
activation has not succeeded. This may support inspection of own-game
HTTPS without process-memory access. A key file alone will not prove Career
decryption; fresh scoped traffic and a matching handshake are still required.
An offline selected-flow/hello/key-log correlation checker is now prepared;
its old-capture negative control does not contain the required initial TLS
records. It does not decrypt or prove game ownership. See "Built-in TLS
diagnostic candidate" and "Offline capture/key-log correlation preparation"
below. All **191 tests pass**; direct first-party stats retrieval and production
integration remain unverified. Approval is recorded; Windows elevation for the
normal installed launcher is the current execution blocker.

Earlier offline follow-up (2026-09-05): the separate game-specific UniSDK log
has been decoded with a reproducible, redacted local helper. All 2,758 records
validate as padded UTF-8; the selected date has platform-login credentials and
an HTTP-200 SDK authentication record, but **no Career-shaped token**. Static
code traces SDK login through the game login protocol to the later SocialMatrix
token delivery. These are separate stages; a working SDK login is not Career
access. See "Game login chain and separate SDK log" below. That checkpoint's
full suite passed **172 tests**.
No direct first-party player response or production provider is verified.

Latest public-site follow-up (2026-09-05): RivalsData's normal "Update Matches" action
returned 13 matches marked `source: "live"` from `/player/matches`, distinct
from its `/player/matches/cached` loading route. This supports a separate
live-fetch path beyond private-history reconstruction, but does not establish
the upstream NetEase request or independently verify data freshness. See
"Public-site refresh paths traced" below. No first-party player integration
has been completed; that checkpoint's full test suite passed all 159 tests.

The current code adds `provider_probe.py`, a standalone diagnostic client, and
clarifies the app's HTTP 401/403 message. It does not replace rank refresh, alter
the match index, read credentials, or introduce a browser-based fallback.

## Current observations

Tests were made from this Windows checkout on 2026-09-04. They are observations,
not guarantees about future access or the scope of every NetEase API.

| Request | Result | What it establishes |
| --- | --- | --- |
| NetEase replay `10361658731`, zone `12001` | HTTP 200, application code 200, one match | The public tournament route works |
| NetEase internal match `6687808_1749775929_1310002_12001_11` | HTTP 200, one match | The `match_uids` request construction also works |
| Same internal-match route for `5513353_1788233115_1272012_11001_12` | HTTP 200, `matches: null` | This route did not return the known ordinary match |
| Tracker profile `KovaaksKid2008`, app's actual cloudscraper transport | HTTP 403, no JSON | The app is blocked before rank parsing |
| Same Tracker profile in a normal browser | API HTTP 200; populated rank and match UI | The profile exists; browser and app access differ |
| RivalsData player `1822797559`, ordinary Python/PowerShell POST | HTTP 403 | A bare program client cannot currently use this route here |
| RivalsData search/profile in a normal browser | API HTTP 200; search resolves `KovaaksKid2008` to `1822797559` | Identifies the site's player lookup and profile UID |

The earlier numeric input `10826896958` must not be assumed to be a player UID
or replay code without clarification. These are different identifiers even
though both can be decimal strings. No saved vault identity was changed.

## First-party calls verified against public site code

Base: `https://interact32-h.webapp.easebar.com/x20namwss202505/`

```text
GET x20mwss202505_get_history/?match_uids=<five-part-internal-match-id>
GET x20mwss202505_get_history_by_replay_id/?zone_id=12001&replay_id=<replay-code>
GET x20mwss202505_zh_get_history_by_replay_id/?zone_id=12001&replay_id=<replay-code>
```

The [official esports JavaScript](https://www.marvelrivalsesports.com/pc/gw/20250714124101/js/index_4a4ded09.js)
uses these calls for tournament schedules, hardcodes zone `12001` for replay
lookups, and falls back to the `zh` route when the first replay lookup is empty.
The [public control match page](https://www.marvelrivalsesports.com/?matchId=10361658731&overallId=a2025061203)
provides the positive control. The diagnostic defaults to the observed zone;
changing it is an experiment, not a verified PC/platform mapping.

The response envelope is `code`, `success`, `msg`, `data.matches`. Match records
include `match_uid`, `match_time_stamp`, `match_map_id`, `match_winner_side`,
`game_mode_id`, `match_season`, `play_mode_id`, `zone_id`, `replay_id`,
`match_play_duration`, `dynamic_fields`, and `match_players`.

An HTTP 200 response with `matches: null` is an empty result from this endpoint.
It is not proof of an invalid UID, an invalid replay, or universally inaccessible
ordinary matches. Comparing positive and negative controls on the **same**
internal-match route rules out a completely broken query construction.

## Initial observed website APIs — not NetEase endpoints (2026-09-04)

The following requests were observed while visiting the user-supplied
[Tracker profile](https://tracker.gg/marvel-rivals/profile/ign/KovaaksKid2008/overview?season=19)
and searching for that name on [RivalsData](https://rivalsdata.com/player/1822797559).
No login or manual challenge-solving was used. Only normal navigation/search
was performed; no refresh action, live-game feature, or paid feature was invoked.

```text
GET https://api.tracker.gg/api/v2/marvel-rivals/standard/profile/ign/KovaaksKid2008?

POST https://api.rivalsdata.com/player
{"uid":1822797559}

POST https://api.rivalsdata.com/player/heroes
{"uid":1822797559,"season":19}

POST https://api.rivalsdata.com/player/matches/cached
{"uid":1822797559,"cursor":null,"season":19}
```

RivalsData search calls `https://api.rivalsdata.com/players/search`; its request
body was not captured in this checkpoint. Other automatically requested profile
routes included `/player/teammates` and `/player/crosshairs`, but their data is
not needed for owned-account rank refresh and was not retained.

Tracker's successful payload uses `data.platformInfo`, `data.metadata`, and
`data.segments`, matching the app's existing parser design. For this profile,
`platformUserId` was null and `platformUserIdentifier` was the IGN, not a UID.
Do not assume every successful Tracker response can backfill a player UID.

These are observed internal website interfaces, not documented integration
contracts. HTTP 403 prevents simply wiring RivalsData into requests/urllib as a
reliable fallback. Browser-assisted sync would be a separate, explicitly labeled
implementation choice, not a discovery of direct NetEase access.

## Other leads and limitations

- [NetEase Rewind event](https://www.marvelrivals.com/h5/20251128/bgen/): earlier
  research found base `https://s2.easebar.com/4701275b27582859/api/`, game-login
  exchange routes, `game/anniversary/getReport` using an access token, and
  `getReportByDataKey` using an opaque share key. Earlier testing returned event
  ended (`-8`). This is not an established UID-to-current-stats service; it was
  not revalidated as live during this checkpoint.
- [Official Hero Hot List](https://www.marvelrivals.com/heroes_data/): aggregate
  hero/rank data does not supply individual player history.
- [MRT broadcaster source](https://github.com/smohd-rosli-tech/MRT/blob/main/Server/routes/api.js)
  references authenticated competition and replay services. The existence of
  those routes does not provide an authorized token or demonstrate an anonymous
  general-player lookup. No token was acquired or guessed.
- [MarvelRivalsAPI documentation](https://docs.marvelrivalsapi.com/) describes a
  separate third-party API requiring `x-api-key`, not a NetEase public API. It
  must not be silently reintroduced as direct NetEase access.

## Repeat the checks

Use Python 3.10+ from the repository directory. The probe uses only the standard
library, imports no application code, and does not touch the vault. Each command
performs one request and prints a summary, never a raw player response.

```powershell
.\.venv\Scripts\python.exe provider_probe.py --control
.\.venv\Scripts\python.exe provider_probe.py --match-id 6687808_1749775929_1310002_12001_11
.\.venv\Scripts\python.exe provider_probe.py --match-id 5513353_1788233115_1272012_11001_12
.\.venv\Scripts\python.exe provider_probe.py --tracker-ign KovaaksKid2008
.\.venv\Scripts\python.exe provider_probe.py --rivalsdata-uid 1822797559
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*_test.py" -v
```

Exit 0 means match records or a JSON object was received; `json_received` alone
does **not** mean a valid profile or usable stats. Exit 1 means an empty result,
an access/transport problem, or an unexpected response. Exit 2 means invalid CLI
arguments. Use `--replay-code` only for a replay code, not a player UID.

Verification: all 88 source tests pass, including 12 diagnostic tests. A local
ignored `.venv` was created with the existing `requirements.txt` dependencies.
No executable was rebuilt or published.

## Next integration gate

### Own-client capture preparation

The user authorized a narrowly scoped own-account capture and logged into the
game. The game process and established TCP connections are visible, but this
Codex session is not elevated: `pktmon status` reports access denied. At that
preparation checkpoint no packet capture had started; the user-run result is
recorded below.

`scripts/capture-career.ps1` is prepared for the user to run in Administrator
PowerShell. Its syntax was checked and its non-mutating `-DryRun` successfully
found the game connections. The elevated path subsequently completed after the
empty-filter guard fix described below.
It requires no existing Packet Monitor session or filters, filters by each
current game connection's remote IP and both TCP ports, captures for 30 seconds,
then stops and removes its temporary filters. It stores ETL/PCAPNG locally under
`%LOCALAPPDATA%\MarvelAccountKeeper\diagnostics\career-<unique-id>` outside Git.
Do not close the terminal mid-capture; if automatic stop fails, use `pktmon stop`
in that Administrator window. No certificates, proxy settings, or game files
are modified. The capture remains local and must not be pasted/uploaded.

This initial capture excludes UDP and newly opened TCP connections; an empty
result is not proof that Career sends no traffic. Encryption can prevent API
paths and bodies from being visible. The connection-filter approach follows
[Microsoft's Packet Monitor guidance](https://learn.microsoft.com/en-us/windows-server/networking/technologies/pktmon/pktmon-syntax).

### First own-client capture result

The first attempt stopped before modifying filters because Windows returned
`Packet Filters:` followed by `None`. The guard now accepts that complete empty
response, with 10 offline PowerShell checks covering empty and nonempty output.
The subsequent user-run capture completed on 2026-09-04 around 20:52 local time.
ETL and PCAPNG exist in the local diagnostics directory; neither is in Git.

The capture has 35 packets and 25.396 seconds between its first and last packet.
Although its PCAPNG interface declares Ethernet, the frames contain a 24-byte
wireless header plus an LLC/SNAP IPv4 header. The helper recognizes the observed
SNAP signature rather than searching arbitrary offsets inside payloads.

- `35.236.255.68:12101`: 5,240 unique outbound TCP payload bytes and 20,965 inbound
  bytes after deduplicating overlapping captured segments. The largest burst
  occurred near the end of the capture. This is a candidate connection for the
  Career exchange, not proof of a particular request or server role.
- `34.30.128.220:10202`: 268 unique inbound payload bytes in two captured runs.
- No run starts as recognized plaintext HTTP, a JSON document, or a standard TLS
  record header. No complete zlib stream was found in the tested first 64 offsets
  of each run, and no tested career/player/match field markers were found.
- These limited checks do **not** establish encryption, rule out compression,
  identify a binary protocol, or recover a usable API URL/schema. Newly opened
  connections and UDP were outside the capture filters.

`scripts/summarize-career-capture.py` prints metadata only and does not decrypt,
replay, or upload traffic. It uses `dpkt` installed only in the local `.venv` as
an optional research dependency (not added to production requirements). Six
synthetic tests cover framing, overlap, protocol hints, and non-disclosure.
Raw payloads and token values were not printed or copied into reports.

### Client networking component identified (read-only continuation)

The user confirmed opening Career near the end of the capture, which aligns
with the port-12101 burst. This is timing evidence, not runtime attribution of
individual packets to a function.

Read-only examination of the installed 64-bit executable found a Python method
table referenced by a module definition named **`mosclient`**. Unlike a lone
string match, the table groups networking and serialization functions together;
its function pointers resolve to executable sections. The inspector does not
load the game binary, attach a debugger, execute its functions, or read process
memory. No game files, certificates, or proxy settings were changed.

Build identification:

- Executable size: 295,913,472 bytes.
- SHA-256: `54154e879731f3f6fad32371bff78d62629c3d87ad5443dc9466871312a76714`.
- Method table RVA: `0x0f08b0b0`. Offsets below apply only to this build.

| Binding | Wrapper RVA | Observed argument-format string / check |
| --- | --- | --- |
| `new_tcp_connect` | `0x0549bac0` | `sHi` |
| `send` | `0x0549bb50` | `KO`; checks a bytes object, obtains its buffer and size |
| `set_secure_suite` | `0x0549c450` | `is`; stores a suite value and string |
| `dump_msgpack` | `0x0549c4e0` | Serialization binding; not invoked |
| `load_msgpack` | `0x0549c6b0` | Deserialization binding; not invoked |
| `set_tcp_compress_type` | `0x0549c9a0` | Integer argument; diagnostic describes directional flags |
| `set_rsa_pub_key` | `0x0549ca90` | Public-key configuration binding; no key values extracted |

Other methods include initialization/shutdown, polling, callback registration,
connection closing, ticking, logging controls, and heartbeat controls. The
compression diagnostic identifies `1` as send, `2` as receive, and `3` as both;
these are **directions, not compression algorithms**. The send wrapper calls
backend RVA `0x0549b150`, which forwards to `0x05487620`. The security-suite
wrapper calls `0x0549b390`; its existence does not reveal the active cipher or
provide a session key.

Reproduce the metadata inspection (optional research dependencies only):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-research.txt
.\.venv\Scripts\python.exe scripts/inspect-client-network.py "C:\Program Files (x86)\Steam\steamapps\common\MarvelRivals\MarvelGame\Marvel\Binaries\Win64\Marvel-Win64-Shipping.exe"
```

Do **not** install the PyPI package named `mosclient`: the
[published package](https://pypi.org/project/mosclient/) is an unrelated Meituan
cloud SDK. The game module has not been recovered as a standalone usable SDK.

Follow-up offline capture checks:

- No complete two-or-more-record length-prefix layout matched any captured run
  for the tested 2/3/4-byte length fields, endian choices, offsets 0-8, and header
  sizes up to 16. Different layouts or incomplete boundaries remain possible.
- No MessagePack container stream consuming the full remaining run was found
  after skipping 0-32 prefix bytes. The parser rejects scalar-only coincidences
  as evidence and never prints decoded keys or values. See the
  [MessagePack implementation documentation](https://github.com/msgpack/msgpack-python)
  for the streaming parser used by this optional diagnostic.
- Existing client log keyword checks did not expose readable career, RPC,
  protocol, or serialization information. A narrow string search of the main
  asset archive did not expose career/network Python source paths.

**Integration remains incomplete.** The concrete next code path is the calling
layer that constructs Career messages for `mosclient.send`, plus the transport
framing and session setup. We have not mapped `mosclient` to the captured socket
at runtime or recovered a Career RPC name/schema. No packet replay, game-memory
inspection, login/session-key extraction, or live instrumentation was performed.
The updated analyzer has nine synthetic tests, including framing hypotheses and
MessagePack non-disclosure checks.

### Transport framing and compression checkpoint (read-only, 2026-09-04)

Further static analysis of the **same SHA-256 build above** resolves more than
the Python method names. All addresses here are RVAs, not runtime addresses.
This is not a claim that the candidate captured socket uses this component.

The send chain is `0x549bb50 -> 0x549b150 -> 0x5487620 -> 0x5478620`.
The last function queues a callback at `0x5487710`, which looks up a connection
and calls its virtual slot `+0x18`. A client TCP vtable at `0xb41fa40` has
`0x548c800` in that slot. Its data-send path ultimately uses the virtual `+0x90`
implementation `0x5477f50`: optionally compress, optionally transform through
the configured send context when state byte `+0xb8 == 2`, then call the base
TCP sender `0x5478210`. The send wrapper itself accepts already-constructed
bytes; the Career request construction remains unlocated.

Static log descriptors identify source files under
`OnlineSubsystemMos/ClientSdk/common/src/net/`, specifically
`client_tcp_connection.cpp`, `tcp_connection.cpp`, and `tcp_connection_mgr.cpp`.
Useful verified anchors for continuing without dumping the entire executable:

| Function / meaning | Code RVA | Log descriptor RVA |
| --- | --- | --- |
| Secure hello parsing / handshake handler | `0x547a4d0` | `0xb420618` |
| Missing RSA public-key handshake configuration | within `0x547a4d0`, reference `0x547ad1b` | `0xb420748` |
| Unsupported peer secure suite | within `0x547a4d0`, reference `0x547b345` | `0xb4207e8` |
| Client send compression error | `0x5477f50`, reference `0x5478051` | `0xb4208f0` |
| TCP compression adapter | `0x54767c0` | `0xb421398` |
| Zstandard streaming compression implementation | `0x5476900` | `0xb421bf0` |

Each descriptor starts with a pointer to a UTF-16 diagnostic format, followed
by a pointer to an ASCII source path and a source-line integer. Find references
to the **descriptor**, not just to the source-path string. The last diagnostic
explicitly names `ZstdStreamCompressor::Compress`, from `util/compress.cpp`.
The compression factory at `0x547c1a0` installs vtable `0xb41f7f8`; its compress
slot points to `0x5476900`. It uses persistent stream contexts and 512-byte input
chunks. This establishes Zstandard for this component, not the active capture's
compression setting. Earlier zlib checks did not cover this algorithm.

The receive path `0x5479dd0` handles an initial secure hello, conditionally
transforms incoming bytes through its receive context, conditionally decompresses,
then calls the plaintext message parser `0x5479fe0`. The handshake handler has
suite-dependent branches and configures compression direction flags. Neither
the active suite nor session material was read. The order of these operations
means an application header need not be visible on the wire.

The plaintext message parser establishes this build-specific layout:

- Bytes 0-3: little-endian word; low 28 bits are the **total record size**,
  upper four bits are flags. See `0x547a09c`, `0x547a0a1`, and `0x547a0d8`.
- Ordinary header: seven bytes. The remaining three bytes are combined as
  `(little_endian_u16_at_4 << 8) + byte_at_6`; their application meaning is
  not yet established. They must not be labeled as a Career RPC identifier.
- With flag bit `8`, the header becomes nine bytes. Bytes 7-8 give a
  little-endian trailing-extension size; the main body excludes that trailer.
- The ordinary-body limit checked here is `0x04000000` bytes. The new offline
  diagnostic rejects malformed extension lengths and requires at least two
  complete records consuming the full suffix, with at most 32 prefix bytes
  skipped. It does not attempt to reproduce permissive malformed-input behavior.

The generic length-prefix search did not account for a masked 28-bit field.
The new `mos_packet_hypotheses` check now does. **It found no complete candidate
in any of the four captured runs.** No standard Zstandard frame magic appeared
in the first 64 bytes of those runs either. A mid-session streaming capture can
lack a compression header or earlier decoder state, so these negative results
do not disprove this protocol. No decryption or decoded payload extraction was
attempted, and no request was replayed.

Static narrow identifier searches also found `CareerInfoController.cpp`,
`ue_PyUCareerInfoController`, `ue_PyUCareerWorldSubsystem`, and Career data-type
names. These are next calling-layer leads, **not** verified network request names.
A file-name inventory did not find loose `.py`, `.pyc`, or `.zip` files in the
game's `MarvelGame` tree. No asset archive was unpacked or decrypted.

Reproduction notes: use optional `pefile` and `capstone` to read the executable
without loading it. Read PE exception directory index 3 as raw `<III>` runtime
function entries to locate code ranges; parsing all unwind structures is
unnecessary. Leaf functions and compiler-split functions may not be covered by
a single entry, so inspect explicit branch targets rather than treating an
entry end as the full function end. This checkpoint was manually traced;
`inspect-client-network.py` still reports only Python binding metadata.

Verification: **103 source tests pass**, including 15 synthetic capture-tool
tests. The added tests cover masked lengths, extended records, truncation,
invalid sizes, bounded prefix search, and metadata-only reporting. Re-running
the actual capture confirmed the new negative results above. No production
provider, packaged build, runtime instrumentation, or credential change was made.

Next: trace the Career controller's Python/native calling layer and establish
where the seven-byte application envelope is constructed. Session-start
capture or live instrumentation is not automatically authorized by this
checkpoint; request the user's involvement/explicit approval if needed. Keep
the active goal incomplete until an owned-profile response is reproduced and
integrated, not merely until more transport details are recovered.

### Actual Career call sites recovered from packaged Python (2026-09-04)

**This supersedes the previous next action.** The native Career controller lead
resolved mainly to in-match damage/healing/kill event collection. The actual
own-profile call sites were instead found in packaged Python and lead to
**XYQ / SocialMatrix**. Do not assume the earlier `mosclient` connection or
framing is the transport used by these own-Career requests.

Installed container: `MarvelGame/Marvel/Content/Marvel/NonAssetsToCopy/script.uep`,
49,328,266 bytes, SHA-256
`4dbe9d3246a05d4a402373cc11c03a938692db9bc16aaaf939ddff4e8682d22e`.
The `MCPK` header has 12-byte directory records and 16-byte file records;
there are 9,726 file entries. Second index start: 15732; payload start: 171348.
File offsets are payload-relative. The fourth file-record integer is NOT the
decoded marshal byte length. Static evidence: header parser RVA `0x4bb6fc0`,
lookup `0x4bbdcc0`, read `0x4bc04e0`, bootstrap loader `0x4c0d660`.

Bootstrap entry 4703 uses a four-byte `MCPK` XOR mask and zlib; it parses as
`redirect.py`. Its loader uses the legacy rotor algorithm, zlib, a prefix
transform/reversal, then marshal parsing. Distribution-file rotor parameters
are derived in memory from the archive bootstrap, never hardcoded, printed or
persisted. This is unrelated to account/session credentials. The implementation
uses the permissively licensed [CPython rotor source](https://github.com/python/cpython/blob/v2.3.7/Modules/rotormodule.c),
with attribution and permission notice retained in the research script.

The engine contains Python 3.11.10 version strings. The
[xdis cross-version parser](https://github.com/rocky/python-xdis) reads code-object
metadata, but this build REMAPS OPCODE NUMBERS. Stock Python disassembly is
incorrect and must not be treated as source evidence. The small bootstrap
expression/transform was manually checked; no recovered code was executed.

New optional tool: `scripts/inspect-script-container.py`, hash-gated to this
archive, bounded decompression, no network/process calls, no extracted-file
writes, and no constant/key output. A full scan parsed **9,717 entries**, skipped
the bootstrap and reported eight failures: 989, 1093, 1115, 3037, 3619, 6454,
8478, 9430. Parser diagnostics are suppressed; do not claim complete recovery.

Important zero-based archive entries:

| Entry | Filename / role |
| --- | --- |
| 1817 | `Scripts/mos_client/MosClient_CareerManager.py` |
| 1833 | `Scripts/mos_client/MosClient_Career.py` -- own-Career request construction |
| 5308 | `Scripts/component/career_component.py` -- stubs including `S_QUERY_CAREER` |
| 4863 | `Scripts/sdk_utils.py`: `get_xyq_client(gi)` accesses `gi.PlayerAvatar.social_handler.xyq_client` |
| 3476 | `Scripts/UniSDK/SocialMatrix/XyqService/XyqServiceClient.py` |
| 698 / 700 | SocialMatrix `SocialMatrixClientBase.py` / `SocialMatrixSocketClientBase.py` |
| 7940 / 9419 | SocialMatrix HTTP implementation / C++ wrapper |
| 38 / 40 / 43 | SocialMatrix Python socket implementation / TCP connection / `NetSecurity.py` |
| 5238 | `Scripts/network/nex_prot.py` -- separate NEX envelope helpers |
| 5048-5055 | `Scripts/proto/` -- RPC/login/avatar/system schemas and registration |
| 5719-5722 | `Scripts/core/rpc/` -- RPC index/decorator/registration |

Entry 1833's `request_xyq_career(self, api, content, callback)` uses the XYQ
client's `call` and service name `xyq-career`. Recovered caller constants:

| Caller | Operation | Input fields/constants |
| --- | --- | --- |
| `c_get_player_career_data` | `query_my_player_career` | `player_uid`, `zone_id`, `filter`, `game_mode_id`, `play_mode_id`, `match_season`, `device_type`, `sort_hero` |
| `c_get_history_matches` | `query_my_match_summary` | `player_uid`, `zone_id`, `page` (0), `page_size` (50) |
| `c_get_history_match_info_by_uid` | `uid_query_my_match` | `player_uid`, `zone_id`, `match_uids` |

These are service-operation names, NOT verified public HTTP URLs. Exact nesting,
defaults, runtime values, serialization and authentication remain to be traced.
Response-handler field names include `career`, `sort_hero`, `hero_careers`;
summary fields include `match_info`, `match_uid`, `match_map_id`, `game_mode_id`,
`play_mode_id`, `mvp_uid`, `dynamic_fields`, `match_player`, `epic_moments`,
`replay_version`; detail handlers reference `matches`, `match_players`.
None has yet been compared with a live own-profile response.

Rank/overview methods use service `xyq-namecard`, operation `GetData`, fields
`host`, `tag`, `id` (overview adds `fields`). Other-player queries use a different
path through `gi.PlayerAvatar.Server.C_QUERY_CAREER`; do not substitute that
workflow for the owned-profile goal.

The XYQ wrapper exposes `http_request(service, headers, uri, method, querystring,
post_body, host, protocol, version, cb, body_use_json)`. SocialMatrix base classes
have `process_token(token_dict)`, endpoints, payload settings, optional RC4 and
signature settings. Their existence does not prove runtime values. No account
token was obtained/reused. The previously deferred SocialMatrix lead is now
directly relevant. `get_xyq_client`'s concrete `social_handler` class and inherited
`call` implementation still need resolution.

Reproduce (optional dependencies in `requirements-research.txt`):

```powershell
.\.venv\Scripts\python.exe scripts/inspect-script-container.py "C:\Program Files (x86)\Steam\steamapps\common\MarvelRivals\MarvelGame\Marvel\Content\Marvel\NonAssetsToCopy\script.uep" --start 1833 --count 1 --details
```

Six synthetic tests cover archive bounds/build gating, bounded zlib decoding,
prefix reversal, rotor restart and parser non-disclosure. **109 source tests
pass.** Production stats handling is unchanged, with no verified live NetEase
provider or new packaged executable.

Next: follow `social_handler.xyq_client.call`, legitimate session setup and
serialization; validate the opcode mapping for exact Career request dictionaries.
Then reproduce an owned-profile response before integration. No runtime
instrumentation, process-memory inspection or session-key extraction has occurred;
those still require explicit user approval. The goal remains incomplete.

### XYQ HTTP gateway and session requirement established statically

The concrete game wrapper is archive entry **103**,
`Scripts/subclassing/PyMarvelSocialClientHandle.py`, class `XyqClient`.
`reset_sdk_handlers` constructs it and stores it as `xyq_client`.
Its `call(self, service, api, payload, headers, callback)` does the following:

1. Initializes missing headers/payload as dictionaries, sets `Content-Type` to
   `application/json` and `Accept-Encoding` to `gzip`.
2. Encodes JSON as UTF-8 and base64 for the generic SDK wrapper.
3. Calls `client.http_request(service, headers, api, 'POST', None, body,
   None, None, None, callback)`.

The base64 at this layer must NOT be mistaken for the direct HTTP request body.
Entry 3476's `http_request` detects an HTTP transport and base64-decodes the
body before forwarding it. The HTTP transport (entry 7940) builds:

```text
POST https://<token-provided host>/xyq_service/xyq-career/query_my_player_career
Authorization: <the original SocialMatrix token, as-is>
Content-Type: application/json
Accept-Encoding: gzip

<ordinary JSON request body>
```

The token's transport settings choose HTTPS versus HTTP in the game code. For
our eventual credentialed test, do not send a token over plaintext HTTP or to
an unverified host. No hostname has been recovered from a live session yet.
This is a VERIFIED STATIC SDK ROUTE, not a verified usable endpoint for this
account. The game may use its socket transport; whether this account's token
also works through the HTTP gateway remains a live-verification question.

`SocialMatrixClientBase.setup(token, cb)` at entry 698 calls
`Helper.parse_jwt_base64_payload(token)`, then `process_token(payload_info)`.
The latter reads `aid`, `websocket.addr`, `websocket.tls`, `config`, `appid`, and
`clientlog`. The HTTP transport assigns the ORIGINAL token directly to the
`Authorization` header; there is no observed `Bearer ` prefix in this path.
Decoding claims is not signature verification or proof of account ownership.

Entry 103's `on_refresh_token(self, token)` passes the game-supplied token to
`social_client.setup`. Successful setup proceeds to `user_client.login`.
Token failures request `Chat.C_REFRESH_SOCIAL_TOKEN` or
`Chat.C_REFRESH_SOCIAL_MULTI_TOKEN` through `player_avatar.Server`.
Therefore a numeric UID alone is not the missing authentication credential.
No token was extracted, forged, printed, persisted or sent during this research.

Exact request construction now traced from entry 1833:

- Base profile body: `{player_uid: own_uid, zone_id: current_zone}`.
- If `game_mode_id` is truthy, use `filter=bson_filter` when that is truthy;
  otherwise set `game_mode_id` and include `play_mode_id` unless it is `None`.
- Include `match_season` only when `season_id` is truthy. `device_type` comes
  from the game's platform conversion of the selected/fallback platform.
  Subsequent static tracing of entry 5459 verified the PC value is `pc`;
  see the platform/rank correction below.
- When `sort_hero_num > 0`, add `sort_hero` with `hero_id_list`, `limit`,
  `query_career: true`, and `order: {by: 'total_play_time'}`. The IDs go through
  the game's multi-role hero conversion.
- Summary body: `{player_uid: own_uid, zone_id: current_zone, page: 0,
  page_size: 50}`; operation `query_my_match_summary`.
- Single match-detail body: `{player_uid: own_uid, zone_id: current_zone,
  match_uids: [match_uid]}`; operation `uid_query_my_match`.
- Each method compares the requested player's UID to `own_player_state.uid`
  before choosing the own-player XYQ path; other-player calls use another path.

The game wrapper decodes the SDK response body from base64, optionally inflates
gzip, parses JSON, handles outer HTTP status separately, then recognizes either
`code == 200` or `error_code == 0` and forwards `data` to the Career handler.
A future direct HTTP probe must process the HTTP body normally, not duplicate
SDK-only base64 wrapping. Response schema and identity still need live checking.

Manual opcode interpretation used coherent code-object names/constants and
cache-layout/call patterns across these small functions. Relevant mappings:
173 LOAD_FAST, 175 STORE_FAST, 204 LOAD_CONST, 114 LOAD_GLOBAL, 248 LOAD_ATTR,
94 LOAD_METHOD, 142 PRECALL, 149 CALL, 77 RETURN_VALUE, 121 BUILD_MAP,
226 BUILD_CONST_KEY_MAP, 234 BUILD_LIST, 82 STORE_SUBSCR, 243 COMPARE_OP,
191 POP_JUMP_FORWARD_IF_FALSE, 221 POP_JUMP_FORWARD_IF_TRUE,
220 POP_JUMP_FORWARD_IF_NONE, 233 KW_NAMES, 103 LOAD_DEREF, 236 STORE_DEREF,
161 STORE_ATTR. This is a partial build-specific mapping, not a general
decompiler or permission to execute recovered code.

Additional entry locations: 3458 `ModuleBase.py`, 3460
`SocialMatrixMultitonMgr.py`, 3462 `SocialMatrixClient.py`. The generic ModuleBase
does not implement the game's `call`; entry 103 does. The inspector's new
`--symbol` option searches code-name references without exposing constants.

**Live verification now requires a legitimate, current own-account SocialMatrix
token and its first-party gateway host.** Ask the user before locating/using
that session material. Start with a narrowly scoped check of the game's existing
local logs if authorized; do not assume approval for debugger attachment,
process-memory reads, hooks, game modifications or broad credential searches.
No new provider should be connected to rank refresh before this test succeeds.

### Authorized existing-log check (2026-09-04)

The user explicitly approved checking existing local Marvel Rivals logs and
using a discovered own-session token for a read-only stats lookup. This does
not authorize process attachment, memory reads, hooks or game modifications.

Checked the one file in `%LOCALAPPDATA%\Marvel\Saved\Logs`
(`2026.09.04_20.45.10-5560.log`, 112,106 bytes), plus the installed game's
`Log\log_2026_09_04_20.txt` (21,245 bytes) and
`Log\profile_20260904.txt` (156 bytes). No SocialMatrix/XYQ/session markers
or JWT-shaped tokens were found. The main game log is not ordinary readable
text: it contains 467 NUL bytes, and UTF-8, UTF-16LE, UTF-16BE and Latin-1
decoding all yielded zero relevant markers/token candidates. This does NOT
establish whether its contents are compressed, encrypted or otherwise encoded,
nor prove that a session token is absent from the opaque data.

Only counts and file metadata were output. No token was recovered, printed,
saved or transmitted, and no authenticated lookup was attempted. The existing
logs therefore do not currently provide the credential needed for live API
verification. Next read-only lead: identify the main log's format/reader from
the installed client before considering any live instrumentation.

### Main log format traced statically (2026-09-04)

The previous check was real progress, not an authorization wait: the authorized
existing-log search found no readable session token. Further read-only analysis
now establishes why a plain-text search is insufficient.

The installed executable's RVA `0xa1d1ed0` contains the file's 13-byte
`logversion000` signature. Constructor `0xf19b40` writes that header; the
flush/write path is `0xf26dc0` -> `0xf24c40` -> `0xf24460`.
The file has exactly nine records after its header, each framed as:

```text
uint32_le payload_size | payload[payload_size] | uint32_le same_payload_size
```

All record boundaries/trailing sizes validate and cover all 112,106 bytes.
Payload sizes: 14115, 15238, 19103, 18480, 11464, 10744, 13181, 9616, 80.
These lengths are diagnostic metadata, not decoded account content.

Writer `0xf24460` attempts compression (`0xed03f0`) and then applies a
Crypto++ cipher initialized by `0xf18d90` using 16-byte key/IV parameters.
Cipher RTTI identifies Rijndael encryption and CTR_ModePolicy. Constructor
`0xf19b40` initializes those parameters through helper `0x104af0fd`, with
on-disk inputs at `0xa1d1f48` and `0xa1d1f60`; the helper follows obfuscated
control flow and a runtime-initialized global. No parameter values were output
or persisted. This is static own-log format analysis, not game-code execution.

A narrowly bounded control used those on-disk inputs directly as AES-CTR
parameters and checked each output for complete valid zlib/gzip/raw-deflate
streams (4 MiB expansion cap). **None of the nine records validated.** This
does not establish the true parameters or a working decoder. No plaintext,
session token or authenticated response was recovered. Do not label the log
successfully decrypted, or conclude tokens are absent from its opaque payloads.

An earlier native lead, `LogCompressionArchive.cpp`, supports gzip/zstd and
`-LogCompressionType`, but is not sufficient to decode this outer encrypted
log format. Do not propose disabling compression as a proven repair.

Also checked only the three existing files in installed `ccmini/logs`
(20260818_185926, 20260821_234115, 20260822_202237): no SocialMatrix/XYQ markers
or JWT-shaped candidates. No database/cache credential search was performed.

There is still no verified first-party provider. Existing logs have not supplied
a usable session credential, and the current offline decoder lead has not
resolved the runtime-transformed parameters. Read-only access to live process
session data would be a separate, more invasive scope requiring explicit user
approval. It is not covered by the approval to check logs. Do not inject code,
modify game files, disable/bypass anti-cheat, or assume OS access is available.

### PC platform and rank-path correction (2026-09-04)

While awaiting explicit approval for live memory inspection (the automated goal
continuation is not approval), further static tracing resolved two relevant
request details without accessing session data.

Archive entry **5459**, `Scripts/share/const/const_common.py`, function
`get_platform_str_of_sdk(login_os)`, compares the argument with `ELoginOS.PC`,
`PS` and `XBOX`, returning respectively `pc`, `ps` and `xbox`. Its final fallback
is also `pc`. Therefore the recovered own-Career PC request uses
`device_type: "pc"`; numeric enum values must not be substituted for that field.

**Correction to the earlier rank/namecard lead:** entry 1833's
`c_request_rank_data` FIRST checks requested UID == own-player UID. On that
branch it calls `_refresh_own_rank_game_season`, emits the UI event and returns
without an XYQ request. The local refresh reads `rank_utils.get_opened_season_ranks`.
Only the OTHER-player branch calls `xyq-namecard` / `GetData` with:

```text
{host: current_zone_id, tag: "player", id: "<current_zone_id>_<requested_uid>"}
```

The host field here is the zone identifier, NOT the gateway hostname. The
response handler parses `data.rank_game_season` as JSON, then JSON-decodes each
season value. This static schema is not a demonstrated live response or an
authorized substitute for own-account verification. Do not claim this method
establishes the own-rank refresh API.

`c_request_overview_data` uses the same namecard request identifiers plus
`fields: "completed_achievement_num,gallery_num"`; these are overview counts,
not ranking statistics. Additional remapped opcodes corroborated by these
small functions: 231 FORMAT_VALUE, 183 BUILD_STRING.

No authenticated requests, memory reads, game modifications or provider changes
were made. Session availability and ordinary own-account live results remain
the gating evidence.

### Live-session authorization and launch blocker (2026-09-05)

The user explicitly approved the proposed narrowly scoped, read-only check of
the running game's memory for their own SocialMatrix token, saying they were
going to sleep and granting permission to proceed. Do not ask again for the
same read-only session check. Continue to keep credentials out of chat/files;
stop on denied access, and do not inject code, change game files, elevate via a
workaround, or disable/bypass protections. Ordinary own-account read-only API
verification remains authorized if a legitimate usable token is obtained.

Current live check: no `Marvel-Win64-Shipping` process was running. Steam was
running. The Computer Use skill's installed-app inventory returned Marvel
Rivals as `steam://rungameid/2767030`. Launching that app opened the existing
`MarvelRivals_Launcher.exe` (PID 12464, observed 03:49 local), but did not start
the game. Two screenshot-grounded clicks of its visible Launch button, with
fresh observations between them, left the same launcher visible and no game
process. No authentication dialog was automated.

A read-only Windows metadata diagnostic found this research process is not
elevated; querying the launcher's Windows process-token metadata failed with
Win32 error 5 (access denied). This is NOT a SocialMatrix credential and no
game-session memory was read. The denied metadata query is consistent with a
permission boundary but does not prove the cause of the ignored UI clicks.
Do not claim anti-cheat was specifically responsible.

The immediate dependency is a running, logged-in game. The installed launcher
has been left open for the user to click Launch and reach the lobby when awake.
There is still no recovered session token, authenticated first-party lookup,
new provider or updated desktop build. This is a new external-state blocker,
not a need for further authorization of the already approved read-only check.

### Game running; authorized memory access denied (2026-09-05)

After the user said "It's open", confirmed Marvel-Win64-Shipping PID 17132,
started 11:44:34 local. A single native Windows `OpenProcess` call requesting
only `PROCESS_QUERY_INFORMATION | PROCESS_VM_READ` (0x0410) returned NULL with
Win32 error 5, access denied. The test would have verified the executable path
before any further work if a handle had been granted. No handle was granted,
no `ReadProcessMemory` was called, no token was obtained or transmitted, and no
authenticated stats request occurred. Do not retry with elevated privileges,
injection or protection changes under the guise of the read-only check; its
explicit boundary was to stop on denied access. The source of the denial
(Windows privileges versus game protection) is not established.

The current blocker is access to the running game's session, not permission
from the user and not the game's launch state. The goal is still incomplete.

### Standalone Career request probe implemented (2026-09-05)

New `career_probe.py` implements the statically recovered own-player profile,
summary and single-match request paths. It is separate from `app.py` and does
not acquire credentials, inspect processes, access the vault or persist bodies.
The CLI defaults to preview-only; `--send` requires an independently verified
gateway and an interactive hidden prompt. Do not ask for a token in chat.

Sending uses one HTTPS JSON POST with the original token as Authorization and
normal bounded gzip handling (not SDK-layer base64). It refuses redirects and
does not retry. Routing checks require the explicit gateway to match the token's
`websocket.addr` hint and `websocket.tls` to be true; optional expiry/not-before
claims are checked. These checks DO NOT verify a JWT signature or ownership.
Only subdomains of easebar.com/netease.com are currently accepted; this is a
conservative preparation constraint, not evidence for a particular live host.
Other gateway forms require actual first-party evidence before changing it.

Responses distinguish denied/rate-limited HTTP, redirects, bad encoding/JSON,
application errors and unexpected data shapes. `json_received` explicitly does
not mean the player's identity or statistics are verified. Raw parsed `data` is
available only to an explicit in-process caller and excluded from repr/CLI
summaries. CLI summaries emit only fixed diagnostic fields, never remote text.

Synthetic request preview (no network, no real account identifiers):

```powershell
.\.venv\Scripts\python.exe career_probe.py profile --own-uid 123456789 --zone-id 11001 --season 19
```

Eighteen synthetic tests in `tests/career_probe_test.py` verify request schemas,
credential routing, TLS, non-disclosing errors, bounded responses/decompression,
redirect refusal, no retries, preview defaults and hidden-prompt requirements.
All **128 source tests passed** on 2026-09-05; the existing injected-failure
warnings/temporary-file ResourceWarnings remain. No live gateway or token was
used. The app still uses its existing provider, with no new packaged build.

Web searches for the exact log signature and a Marvel Rivals/NetEase log decoder
did not yield a verified decoder; unrelated music decryptors are not evidence.
The normal desktop tool could activate and show the running game's lobby, but
an observed avatar click did not open Career. Do not infer why from that alone,
or conflate this input behavior with the separately observed OpenProcess denial.
The desired UID/name/profile comparison is not complete.

### Privilege mismatch verified; administrator diagnostic prepared (2026-09-05)

After the user asked for continued unaffected work, six existing game/launcher
configuration files were checked for SocialMatrix/XYQ/session markers and
JWT-shaped strings: GameUserSettings.ini, Hubble.ini, MachinePSOConfig.ini,
the general and per-account MarvelUserSetting.json, and launcher_config.xml.
All counts were zero. No browser password/cookie databases, crash dumps or
unrelated account stores were opened. The current per-account settings folder
is named `1370598422`, matching the UID visually read from the current lobby
HUD. This is not the previously supplied `10826896958` nor the earlier website
lookup `1822797559`; do not silently substitute one identity for another.
The current account's name/Career screen and statistics are still unverified.

Read-only OS metadata established a concrete privilege mismatch: game PID
17132 is elevated; the research process is not. WMI GetOwner and whoami agree
on the Windows account. These checks requested only limited process metadata
and Windows access-token metadata (not the game's SocialMatrix token). The
GetProcessInformation protection-level value was 0xfffffffe for both processes;
do not treat this observation as ruling out third-party game protections.
Reference: https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocessinformation

New `scripts/check-career-access.py` provides a reviewable diagnostic. Default
mode queries metadata only and was verified against the running game. With
`--check-read-access` it refuses to request VM_READ unless the caller is already
elevated and the exact installed image path matches. An elevated user can run
it manually to check whether a read handle is available. It never calls
ReadProcessMemory, collects credentials, elevates itself, changes permissions,
enables debug privilege, injects, or bypasses protections. A granted handle is
closed after rechecking the image path. This administrator check has NOT been
run; do not claim elevation is a verified remedy or retry elevation silently.

Current-process command proposed for the user in an administrator PowerShell:

```powershell
& "C:\CodexRepos\marvel-account-keeper\.venv\Scripts\python.exe" "C:\CodexRepos\marvel-account-keeper\scripts\check-career-access.py" --pid 17132 --check-read-access
```

PID must be refreshed if the game restarts. Five synthetic tests verify default
metadata-only behavior, rejecting non-elevated/unknown callers, exact-image
targeting, one-shot access checks and denial handling. All **133 source tests
passed**. No actual memory-read access was requested this turn, and no live
stats response or production provider is verified. Next: inspect the user's
administrator diagnostic outcome, then decide the narrowest actual session
acquisition step. Do not ask again for the already-granted own-session consent.

### Administrator access confirmed; bounded helper prepared (2026-09-05)

The user supplied the output from running `check-career-access.py` in an
administrator PowerShell against PID 17132: caller/game elevated, expected image
true, `read_handle_available`, error 0, memory_read false. This establishes that
elevation resolved the handle-open denial in that test; actual game-memory
reading and session acquisition have NOT yet been demonstrated. The local
Codex app, worker and command shell still report non-elevated. Do not ask again
for the already-granted own-game session-check approval, or claim the agent can
execute in the user's separate administrator terminal.

`career_session_probe.py`, launched by `scripts/probe-career-session.py`, now
connects the missing acquisition stage to the existing standalone HTTP probe:

- No flags other than `--pid`: preview only, no process access or network.
- `--scan`: already-elevated caller required; exact installed image checked
  before opening and on the retained read handle. No self-elevation, debug
  privilege changes, injection, writes to the game, or protection changes.
- Only private, committed, non-executable readable pages are considered. Reads
  are at most 1 MiB, capped at 2 GiB total requested bytes, 100,000 queried
  regions and 45 seconds checked between operations. The time bound is
  cooperative, not a guarantee against a single slow OS call. Guarded/mapped/
  image/no-access pages are skipped. Partial-copy regions are skipped without
  retry; access denial/other read errors stop and discard collected candidates.
- ASCII and UTF-16LE JWT-shaped strings are filtered for SocialMatrix `aid`,
  TLS gateway claims, permitted first-party domain syntax and time validity.
  Unrelated tokens/claims are not printed. At most eight distinct candidates
  are held; ambiguity or a saturated limit prevents a send. Candidate routing
  hints are NOT independently verified gateways or proof of current identity.
- Without `--send`, no request is made. The printed summary contains counts,
  scan limits/outcome and validated gateway hints, not token values or raw
  memory. No game/response/session data is written to disk by this helper.
  Python cannot guarantee erasing every immutable copy or prevent OS paging or
  system-configured dumps; do not claim cryptographic in-memory zeroization.
- Optional `--send` requires explicit `--own-uid`, `--zone-id`, `--gateway`
  (and optionally `--season`) before scanning. Exactly one observed candidate
  must match the independently verified gateway. It attempts one profile
  request through `career_probe`, no retries/redirects, with redacted response
  classification only. Network failure reports transmission as unknown.
  It does NOT infer UID/zone from `aid` or mark identity/statistics verified.

Run this first in the user's existing Administrator PowerShell, with the same
game session open. Refresh the PID if the game has restarted:

```powershell
& "C:\CodexRepos\marvel-account-keeper\.venv\Scripts\python.exe" "C:\CodexRepos\marvel-account-keeper\scripts\probe-career-session.py" --pid 17132 --scan
```

Only paste the helper's diagnostic JSON, never credentials or memory dumps.
This first run sends nothing and intentionally discards candidates on exit;
the optional request mode reacquires them when gateway/UID/zone evidence is
ready. No candidate in a bounded scan is NOT proof that the game lacks a token.

Verification: 21 focused tests, including every ASCII/wide token split point,
no artificial joining over unread gaps, deduplication/ambiguity, limits, native
48-byte MEMORY_BASIC_INFORMATION layout, read denial, handle cleanup, explicit
send inputs and output redaction. A native binding smoke test reads only a
known synthetic buffer allocated inside the test process, not the game.
Full discovery: **154 tests passed**; expected injected-failure warnings and
existing temporary-file ResourceWarnings remain. Default CLI preview verified.
Actual elevated scanning, live Career response, gateway eligibility, current
account/zone and stats comparison all remain unverified. No production changes
or new packaged build were made in this helper checkpoint.

Native API references:
[VirtualQueryEx](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualqueryex),
[ReadProcessMemory](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-readprocessmemory),
[MEMORY_BASIC_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-memory_basic_information).

### Zone source and response identity check traced (2026-09-05)

While the administrator scan result is pending, further hash-gated static
analysis of the same archive establishes these paths (no game code executed):

- Entry 1858, `Scripts/mos_client/MosClient_GameState.py`, `s_login_auth` reads
  `msg['zone_list']`; when nonempty it assigns
  `self.zone_id = zone_list[0]['zone_id']`. Confirmed by LOAD_FAST/LOAD_CONST,
  BINARY_SUBSCR (remapped opcode 23) and STORE_ATTR (161) at offsets 6-54.
  Constant index 2 is zero. Error handling assigns zero, not a PC default.
  The Career request's `self.mc_game_state.zone_id` therefore comes from the
  login reply, not an established universal PC mapping. Neither 11001 nor
  12001 may be silently selected for the current account.
- Entry 1833, `s_get_player_career_data`, assigns `data.get('career')` to the
  player Career state and reads that object's `player_uid` (offsets 268-300).
  The expected identity field is therefore **data.career.player_uid**, not a
  top-level UID, an IGN, or a token's unverified `aid` claim.
- Entry 1703, `PyWidget_CareerOverviewPanel.py`, consumes that Career state.
  Entry 4617, `ui/utils_ui/career_utils.py`, retrieves common stat fields using
  `career_data.get(key, 0)` and badge data using `badge_ids` records' `id/count`.
  Keys/calculations/display formats come from `MarvelCareerOverviewDataTable`,
  including `DataSourceKey`, source type, division configuration and rounding.
  This does not yet establish names, units or season semantics for the live
  response; do not invent a statistics mapping from these generic helpers.

Added `profile_identity_evidence` to the standalone probe. A profile send now
reports whether the nested player UID matches the explicitly requested UID;
missing/malformed/different IDs do not return CLI success. It does not print
an unexpected UID, modify saved accounts, or assert that a matching request UID
proves the logged-in account or verified statistics. HTTP/JSON reception remains
a separate outcome. The memory helper includes this check for optional sends;
the already-proposed `--scan` command and its no-network behavior are unchanged.

Five new synthetic tests cover numeric/string matches, mismatches, absent and
malformed IDs, unrelated nesting, unsuccessful envelopes, and CLI rejection.
Focused probe suites: 44 tests pass. Full discovery: **159 tests pass**.
Game PID 17132 remains running; the scan-helper result is still required before
gateway/session verification. No live credential or Career response is held by
the agent, and no production provider is integrated.

### Elevated memory read denied: stop this acquisition route (2026-09-05)

The user supplied the actual Administrator PowerShell output from
`probe-career-session.py --pid 17132 --scan`. Outcome:

```json
{
  "outcome": "memory_read_denied_or_failed",
  "win32_error": 5,
  "regions_queried": 9,
  "bytes_requested": 4096,
  "bytes_read": 0,
  "partial_regions_skipped": 0,
  "candidate_count": 0,
  "request_attempted": false,
  "request_sent": false,
  "tokens_saved": false
}
```

This supersedes the pending-scan notes above. The earlier administrator test
only proved that OpenProcess returned a handle. The first actual read failed
despite that success. The helper's access-denial branch stopped collection,
discarded any candidates and closed its handle; no retry was made. Zero
candidates with zero bytes read says nothing about whether a session token
exists in memory. It does not establish that the UID is invalid, that the
API rejected authentication, or which Windows/game protection caused denial.

Honor the agreed stop condition: no further memory scans, privilege changes,
injection, game modification or protection bypasses. More repetitions of the
administrator approval are not the next step. Existing captures/log checks
and static request/schema work remain useful but have not supplied a current
usable session. Direct integration still requires a legitimate non-memory
session source or a different verified first-party endpoint, followed by
own-identity/zone/statistics verification. Neither alternative is verified.
This checkpoint changes documentation only; no live API request was sent by
the agent and no new production code was connected.

### Non-memory website-login lead checked (2026-09-05)

After stopping the denied memory route, fetched only public first-party HTML
and JavaScript (no login, cookies, account access or game-memory reads):

- [Official homepage](https://www.marvelrivals.com/) and its
  [header script](https://www.marvelrivals.com/pc/gw/20241128194803/js/global_include/24v2/header_0a4f69c6.js).
- [Official account page](https://www.marvelrivals.com/m/account/) and its
  [account script](https://www.marvelrivals.com/m/zt/20241011170429/js/index_5688134a.js).
- [Shared platform-login script](https://comm.res.easebar.com/load/authLogin.js).

The homepage's website login uses base
`https://interact3-h.webapp.easebar.com/x20drop2410/`, platform-specific
`steam/login/`, `epic/login/`, `xbox/login/`, `ps5/login/`, `custom/login/` and
`view_userinfo/`. The response's `weixin_token` and `user_id` are stored by the
website under its `x20na_hwb_` cookie prefix. These are code field names only;
no user token was retrieved. There is no demonstrated exchange from this
website token to the game's SocialMatrix session.

The account-page code also references `x20drop2410_get_server_list/` (response
`hostnum`), followed by `query_characters/` with website user_id/token/hostid.
Character records used by the UI include `hostnum`, `nick`, and `data_key`.
This could inform website character selection, but `hostnum` is not yet
verified as the game's current `zone_id`, and it is NOT a Career-stats route.
The inspected account script has no Career/XYQ/SocialMatrix/rank references.

Important concrete side effects: the page calls `bind_character/` when no
current character is bound, and contains Twitch binding/unbinding and batch
reward-redemption flows. Do not tell the user to log into this account page
as a harmless stats diagnostic or automate it without evaluating those writes.
No such endpoints were invoked in this check. Searches did not establish a
first-party companion stats API or public SocialMatrix login exchange; this
is a bounded negative result, not proof no such service exists.

[Official cross-progression Q&A](https://www.marvelrivals.com/m/announcements/20250924/40955_1261246.html)
also distinguishes platform accounts from playable characters, keeps Career
rank/battle data separate by device, and notes that Discord/Twitch web bindings
do not automatically follow character changes. This reinforces the need to
verify the actual in-game character/device, not substitute a website account.
No API implementation or account-state changes were made from this lead.

### Public-site refresh paths traced (2026-09-05)

Fresh browser observations used the already user-supplied public profile name,
[KovaaksKid2008 on RivalsData](https://rivalsdata.com/player/1822797559).
No website login was required. The site's UID 1822797559 is not verified as the
currently logged-in game character; neither it nor any third-party profile
data was written to the vault. Performed one normal player refresh and one
"Update Matches" action, without repeated polling or additional pagination.

| Browser action | Request | Observed result |
| --- | --- | --- |
| Open profile | POST `https://api.rivalsdata.com/player`, `{"uid":1822797559}` | HTTP 200, profile object |
| Initial history load | POST `https://api.rivalsdata.com/player/matches/cached`, `{"uid":1822797559,"cursor":null,"season":19}` | HTTP 200 |
| Refresh player data | POST `/player` with the same UID body, then POST `/player/matches/cached` | Both HTTP 200 |
| Update Matches | POST `https://api.rivalsdata.com/player/matches`, `{"uid":1822797559,"cursor":null,"season":19}` | HTTP 200, `source: "live"`, 13 matches |

The update response's top-level keys are `matches`, `next_cursor`, and `source`.
The profile response uses a normalized object, including `uid`, `name`,
`rank_game_season`, `level`, `login_os`, `match_history_is_visible`, `cached_at`
and `last_seen`, rather than a first-party `code/data` envelope. Its returned UID
matches the requested website profile. This is not verification of the user's
current game identity, of rank semantics, or of the upstream authentication.
Only field names, counts and outcomes are retained here, not raw responses,
session headers/cookies or other players' match data.

Inspected public frontend resources already loaded by the browser:

- [Main bundle](https://rivalsdata.com/assets/index-B3aUt5V6.js).
- [Player page bundle](https://rivalsdata.com/assets/player._playerId-DJfuWHes.js).
- [Refresh cooldown hook](https://rivalsdata.com/assets/use-refresh-cooldown-DKyfnim9.js).

The main bundle constructs `/player/matches` plus `/cached` when its cached
option is set. Both forms send numeric `uid`, nullable `cursor`, and `season`;
cached requests can additionally include mode/hero/teammate filters. The player
query tries `/player` with the UID first, then retries with `prefer_cache: 1`
on a non-`not_found` error. Its per-request timeout is 7,500 ms with automatic
transport retries disabled; the refresh hook defaults to a 15-second cooldown.
The inspected stats calls all target `api.rivalsdata.com`. They do not expose
a NetEase gateway, a SocialMatrix token exchange, or an upstream request body.
This is a bounded frontend observation, not proof no other code path exists.

A standard Python GET of the main bundle returned HTTP 403 in this environment;
the browser had already loaded it successfully, so that loaded public copy was
read through browser developer tools. No challenge bypass, credential copying,
fingerprint impersonation or standalone-HTTP success is implied. Previous
standalone profile requests also returned 403; a browser success is not a
verified reusable application integration.

The expanded [RivalsData FAQ](https://rivalsdata.com/about) separately explains
private-history reconstruction from a stored collection of tracked matches.
The separate update route and its `live` label support the user's distinction
between reconstruction and active retrieval. However, `source` is a label
provided by RivalsData: this observation alone cannot establish what its server
called, whether it consulted another cache/provider, or how fresh each record
is. No private-profile or Live Game feature was tested.

Tracker primary-source checks:

- [API availability, December 2024](https://feedback.tracker.gg/t/api-availability/51665)
  and [Marvel Rivals API, March 2025](https://feedback.tracker.gg/t/marvel-rivals-api/55690)
  state that Tracker does not provide a Marvel Rivals developer API. This does
  not mean Tracker itself lacks an upstream API.
- [Social settings support, April 2026](https://feedback.tracker.gg/t/marvel-rivals-social-settings/66090)
  attributes a privacy-display issue to the API level rather than Tracker's
  own implementation. That supports the existence of an upstream interface,
  but discloses neither its host/authentication nor any partnership status.

A public implementation lead was checked and ruled out for this purpose:
[Korthaus/RivalsData](https://gitlab.com/Korthaus/RivalsData) and
[Korthaus/rivalsdata-api](https://gitlab.com/Korthaus/rivalsdata-api).
Their READMEs describe a learning/data-analysis project using Liquipedia hero
and balance-change data, and a Laravel API serving those records. They are not
evidence of rivalsdata.com's live-player backend. No third-party source code
was executed or installed. Searches for the recovered XYQ operation names did
not identify a public upstream implementation during this pass; that negative
search result is not proof that one cannot be found.

Current position: the game's own-Career request construction is traced, and a
live-labeled third-party refresh has been observed. The missing milestone is
a reproducible first-party ordinary-player response with legitimate, repeatable
authentication. Browser network inspection cannot reveal server-to-server
traffic. Next useful evidence would be a first-party web/SDK login exchange
that actually yields Career access, or a published server implementation with
an applicable upstream request. Another tournament-only JSON result, repeated
game-memory denial, or another copy of a third-party browser route would not
satisfy that milestone.

Verification this checkpoint: all **159 tests passed** in full unittest
discovery. Existing injected-error warnings and temporary-file ResourceWarnings
remain. Only research/handoff documentation changed during this pass: no new
production provider, packaged build, game-memory read, login or account-state
change was made.

## Game login chain and separate SDK log (2026-09-05)

This is new offline evidence, not a successful Career request. The existing
SDK log can be decoded without process memory. It exposes the platform-login
stage but did not contain the later SocialMatrix token needed by the recovered
Career operations. No new login, SDK API request, game-memory read, account
binding or production-provider change was made during this pass.

### Distinct authentication stages

Using the same hash-verified `script.uep` and metadata-only parser described
above, the following custom-bytecode paths were traced without executing or
importing recovered game code:

1. Entry 4653, `Scripts/UniSDK/UnisdkChannel/UnisdkBase.py`: `userLogin` calls
   native `unisdk.ntLogin` when initialized.
2. Entry 7800, `Scripts/client/login_mgr.py`: successful
   `on_login_channel_sdk_finish` reads `channel_sdk.getPropStr("SAUTH_JSON")`
   at bytecode offsets 1464–1516. `gen_auth_info` includes the original string
   under `sauth_json` and copies selected platform/device fields. The PC
   `send_login_auth` path sends that object with `C_LOGIN_AUTH` through the
   game connection; this is not a demonstrated standalone HTTPS stats login.
3. The successful `s_login_auth` reply supplies `auth_sn`, `auth_hostid` and
   `zone_list` entries containing `role_uid`, `zone_id`, `gate_ip`, `gate_port`.
   `send_login_enter` sends `C_LOGIN_ENTER` with `auth_hostid`, `auth_sn`, empty
   device/basic info and the first zone's `role_uid`. The wire framing and
   complete standalone login are not implemented or verified.
4. Entry 3743, `Scripts/entity/player_avatar.py`:
   `S_REFRESH_SOCIAL_TOKEN(self, token)` receives a server argument, stores it
   as `game_instance.SocialMatrixToken` at offsets 140–154, forwards it to the
   social handler at 306–346, and obtains `websocket.addr` from decoded claims
   at 514–608. This is the credential/gateway used by the Career HTTP wrapper.
   Decoding JWT claims without signature verification is not proof of validity.

An exact name/constant search covering 9,717 parsed script entries found the
SocialMatrix usages above plus Career events, replay authorization and social
configuration. No ordinary browser token-handoff method was found in that
bounded search; eight entries did not parse and native code is a separate
surface, so this is not proof no such path exists. Developer/editor token
helpers are not an established normal login flow and were not invoked.

`NtUniSdkSteam.dll` contains paths/log labels for Steam authentication tickets
and imports the Steam SDK. Static references alone do not identify which ticket
variant is used in the current runtime, its arguments, or a complete callable
SDK ABI. No separate Steam/NetEase game session was started. A second login
could affect the user's active session and needs a concrete, reviewed proposal
before a live experiment; it is not the next automatic diagnostic.

### Scoped source and decoder

Existing source:

```text
C:\Users\kylep\AppData\Local\UniSDK\Log\3109201CA1FD65B3A6AC32FAB1ABB35D\NtUniSdk.log
```

Observed size: 2,240,322 bytes; 2,758 records; last write September 5 at
13:59:45 local. The directory is exactly uppercase MD5 of UTF-8 game ID
`x20naxx2gb`, independently found in installed script entry 5449. The native
logger uses `JF_GAMEID` plus the same uppercase hash construction. This maps
the log to this game; unrelated application logs were not examined.

Read-only native evidence, all under the installed `Marvel\Binaries\Win64`:

| Distribution file | SHA-256 | Relevant evidence |
| --- | --- | --- |
| `NtUniSdkBase.dll` (6,317,560 bytes) | `d134ce2e46c1b4fb97b569db9fa3f3b9052309a0d9e96f9a3adf39e08b498163` | Constructor RVA `0x1dc20` copies 32 bytes from RVA `0x4b0100` into encrypted-logger configuration |
| `NtUniSdkRoostX.dll` (11,584,504 bytes) | `af00dfb698c54f56eb8310efe746f0a95173b400f5fac28df4cc63fd6594d217` | `spdlog_create_encrypted_logger` export forwards to RVA `0x2c1c50`; rotating encrypted sink |
| `NtUniSdkSteam.dll` (3,967,480 bytes) | `e888fa845eb3f67fe17aa9c48dc48a3c72700b3931ad51d55e7c495d4911aa4b` | Native Steam login/ticket references, not a Career token export |

The log parameter is derived in memory from the hash-verified installed DLL,
not hardcoded or recorded here. It is a distribution logger parameter, not a
user token. Every line is strict Base64, then AES-256-ECB ciphertext with
PKCS#7 padding. All 2,758 records decrypted to valid padding, strict UTF-8 and
timestamped text. An initial first-eight/last-eight control gave 16/16 valid
records in ECB mode; CBC with a zero IV gave no valid padding/UTF-8 records.
This validates the format empirically; unauthenticated encryption does not
establish cryptographic authenticity of a log record.

This is **not** the main-game `logversion000` / AES-CTR format documented above.
That logger remains undecoded. No game DLL was loaded/executed, logging flag
changed, encrypted file modified, or log upload invoked.

A bounded main-logger re-check confirms that `0xf24460` creates a fresh cipher
for each record, on both the compressed and uncompressed branches. Thus a
counter carried across records is not supported by this writer path. The
parameter helper first reads/dereferences a runtime pointer at `0x104e47c8`;
that location is zero in the installed file, so its on-disk contents cannot
substitute for initialized runtime state. A six-block trace finds a pass-through
branch and a further transformed branch, but does not resolve which applies or
the transformation. No helper was executed and no working main-log decoder
resulted. Do not repeat the previous raw-parameter control as if it were new
evidence, or infer that SDK-log decoding also solved this format.

### Redacted results for September 5

Reproducible helper:

```powershell
# Default: metadata only, no decoding.
& ".\.venv\Scripts\python.exe" ".\scripts\inspect-sdk-log.py"

# Explicit transient decoding; outputs only fixed labels, counts and flags.
& ".\.venv\Scripts\python.exe" ".\scripts\inspect-sdk-log.py" --inspect --date 2026-09-05
```

Observed helper results:

- 2,758 decoded/timestamped records, zero invalid records; 859 on the selected
  date. Fixed-label counts are per record, not occurrence counts.
- Selected records: `UNISDK_LOGIN_JSON` 20, `sdkuid` 10, `uni_sauth` 5,
  `gas_token` 1, `client_login_sn` 1. `SocialMatrix`, literal `SAUTH_JSON`,
  `websocket`, and `xyq-career` each occur in zero selected records.
- Eight JWT occurrences deduplicate to two parsed JWTs. Both have SDK-shaped
  `exp`, `gameid`, `iat`, `sub` claim keys. Neither has the observed Career
  `aid` plus `websocket.addr` shape. Both expiry claims were in the future at
  inspection, but signatures, server acceptance and account ownership were
  not verified. No claim values or tokens are retained here.
- A SAUTH-shaped bundle includes `sdkuid`, `gas_token`, `sessionid`, `gameid`
  and device/channel fields. A login result includes `access_token`,
  `refresh_token`, `oauth2` and `chl_sdk_json`. These structures are distinct
  from the SocialMatrix JWT and are **not** inputs to the Career probe.
- Four selected records contain the exact observed URL
  `https://mgbjp-x20na.unisdk.easebar.com/x20naxx2gb/sdk/uni_sauth`.
  One telemetry object, timestamped 12:15:30, records `status_code: 200`,
  `res_code: 200`, `rawcode: 0` for it. The HTTP method is not independently
  established by these retained fields. These are the normal game's existing
  log records; no request was sent by this research pass.

The helper has fixed game/DLL paths and hash-gated RVA extraction, a 6 MiB
log bound, 64 KiB encoded-record bound, and 10,000-record bound. It offers no
plaintext output path, token export, network request, process access or game
code execution. Decoded credentials are inspected transiently in Python memory;
do not describe that as no credential access. They are not printed, persisted
or transmitted. Tests use only synthetic strings, encryption parameters and
temporary fixtures, never installed game files or real credentials.

Verification: **13 new synthetic tests pass; 172 tests pass** in full unittest
discovery. Metadata-only and explicit inspection were both run locally. Existing
injected-failure warnings and temporary-file ResourceWarnings remain. The app
still uses its existing provider; no packaged build was created for this change.

Remaining gap: acquire the **later own-game SocialMatrix session** through a
repeatable normal/non-memory source, or establish another first-party Career
authentication mechanism. Then obtain an ordinary own-player response, verify
current character/zone and displayed statistics, and integrate the provider.
The newly inspectable SDK login alone does not satisfy that milestone. The
denied process-memory path stays stopped; further administrator retries do not
follow from this finding.

## Built-in TLS diagnostic candidate (2026-09-05)

The useful next live experiment is now concrete, but **has not been activated**.
The installed game has libcurl code for local TLS key logging. This is distinct
from decoding the main-game log or acquiring game memory. It could reveal the
actual own-Career HTTPS request when combined with a suitable packet capture;
that capability has not yet been demonstrated in this game session.

Primary reference: [libcurl TLS key logging](https://everything.curl.dev/usingcurl/tls/sslkeylogfile.html)
describes the environment-variable facility and its backend dependence. The
[upstream implementation](https://raw.githubusercontent.com/curl/curl/curl-7_88_1/lib/vtls/keylog.c)
shows environment lookup, append-mode file opening and key-line writing. This
is a structural comparison, not a claim that the installed game uses exactly
that upstream release or that its active Career transport has been verified.

### Evidence in the installed executable

Same 295,913,472-byte game executable, SHA-256
`54154e879731f3f6fad32371bff78d62629c3d87ad5443dc9466871312a76714`:

| Evidence | Build-specific RVA |
| --- | --- |
| `SSLKEYLOGFILE` string | `0xd4d9ec8` |
| Key-file open routine: reads that setting, opens in append-text mode, configures buffering | `0x9970e00–0x9970e8e` |
| Backend initialization calls the open routine | `0x99464a0` |
| Key-log enabled check before callback registration | `0x994744c–0x9947453` |
| Registration supplies the callback to the SSL-context setter | `0x9947458–0x994745f` |
| Setter stores the callback at context offset `0x388` | `0x97e4690` |
| Callback forwards its text argument to the key-line writer | `0x9949cb0–0x9949cb3` -> `0x9970e90` |

The writer checks for an open file/nonempty line, bounds the line to 254 bytes,
adds a newline if needed and writes it to the configured file. Together these
references establish compiled, wired support, not merely an unused string.
They do not establish environment inheritance through the normal launcher,
runtime execution, which process produced a future key line, or whether Career
uses that TLS connection. No native game code or callback was executed here.

### Prepared opt-in launch helper

```powershell
# Safe default: metadata only; this was run and returned can_launch: true.
& "C:\CodexRepos\marvel-account-keeper\scripts\launch-career-tls.ps1"

# NOT RUN. Requires the user's explicit approval of this live diagnostic.
& "C:\CodexRepos\marvel-account-keeper\scripts\launch-career-tls.ps1" -Launch
```

The opt-in path verifies the exact game build and the normal launcher hash
`cb56ae37b02f4cb888d61220aab847e7c340b235234752407251b0c6b26028f3`, requires
Steam running and no game/launcher already running, then launches only
`MarvelRivals_Launcher.exe` in its normal installed working directory. It does
not forcibly close anything, start an alternate game executable, add special
game arguments, change Steam settings or perform an SDK login of its own.

Only that new launcher's process environment receives `SSLKEYLOGFILE`, pointing
to a unique `session.keys` under the user's local Marvel Account Keeper
diagnostics directory. The parent/user/machine environment is unchanged; an
existing process-level setting causes a stop. Before launching, the new
directory is protected for the current Windows user and SYSTEM, with inheritable
permissions for any generated key file. There is no token/header/record dump,
capture start, proxy change, certificate change, driver installation, game-file
modification, memory read or self-elevation. The normal game launch may still
have its own UI or operating-system requirements; this helper does not bypass
them or guarantee a launch.

The diagnostic process tree may write TLS secrets until it exits. Quit that
game/launcher normally after the experiment; subsequent normal launches do not
inherit this setting from the helper. The local key file must not be pasted or
uploaded. It is intentionally retained locally for authorized analysis, not
automatically deleted. A created key file could initially contain launcher
traffic only, so file existence is **not** proof the game/Career path worked.

Verification completed before asking for live approval:

- Game/launcher absent and Steam present in the September 5, approximately
  14:44 local process-name check. No process was closed. This is a snapshot,
  not a condition to assume for a later run.
- Helper default returned `metadata_only`, `can_launch: true`, no started
  launcher/capture, no changed TLS setting and no created key file.
- Two new unittest methods validate PowerShell syntax, seven launch-decision
  cases, the child-only environment and the real private ACL on a new empty
  synthetic temporary directory. The initial ACL test failed because this
  worker could not autoload the PowerShell Security module; switching just the
  helper to the Windows .NET ACL API passed. No system policy/module changed.
- Full suite: **174 tests pass**; usual injected-error and temporary-file
  ResourceWarnings remain. No live diagnostic launch or stats request ran.

### Capture requirement and approval gate

The first proposed experiment is one normal launch with the built-in TLS
diagnostic enabled, to establish that it works here. Explicit user approval is
required before `-Launch`; the automatic goal continuation is not that approval.
No additional administrator privilege is requested by this helper.

End-to-end decryption still needs a **fresh, own-game-scoped** packet capture
covering the appropriate TLS handshake/records and matching key-log entries.
The previous capture is from an older session and cannot be treated as decoded
using newly generated secrets. Its filter design selects already-established
5-tuples and excludes newly opened connections; blindly reusing it does not
establish that an initial Career HTTPS handshake will be captured. A narrow
DNS-cache metadata check found no matching cached Marvel/social/XYQ NetEase
hostnames at this point; no gateway is inferred from that absence. Do not
substitute a broad all-application port-443 capture. The fresh capture workflow
must be prepared and reviewed for actual game-scoped coverage before activation.

After any successful decryption, keep the original token local, verify the
requested own UID/zone and the returned identity, compare visible in-game
statistics, then integrate the verified provider. Built-in key-log support is
a new evidence-backed route to that milestone, not its completion.

### Other bounded checks during this pass

Entry 3743's `S_REFRESH_SOCIAL_TOKEN` formats the received token into an
`AvtLogger.info` call at bytecode offsets 50–128. `AvtLogger` comes from
`ue_log.get_logger`; entry 4916 maps `INFO` to `LOG` and uses the native logger.
This supports checking the game logging path, but does not establish the
current category's runtime verbosity or actual record presence. The encrypted
main-game log remains undecoded; no repeated raw-key control was performed.

Read-only metadata inspection of the installed `client_diagnose.exe`,
`CapturePro.exe` and `upload_profile.exe` found PyInstaller archives. Their
selected diagnostic modules collect/copy/ZIP logs or gzip/upload profiling
files, not a discovered main-log decoder. In particular,
`collector.log_collector.collect` uses copy/ZIP helpers; none were executed.
The [PyInstaller archive reader](https://raw.githubusercontent.com/pyinstaller/pyinstaller/develop/PyInstaller/archive/readers.py)
supplied the archive-layout reference. Only bounded in-memory decompression and
code-name inspection were done, with no recovered module import/execution or
diagnostic upload. No decrypt/cipher module was found in these three archive
module-name inventories; this is a bounded negative result, not proof no reader
exists elsewhere. Exact web searches did not yield a usable `logversion000`
decoder or a reproducible SocialMatrix authentication implementation.

### Restart completed after capture; unexpected filter scope (September 6, 04:04)

The user clarified no restart had happened in the earlier window. A second
same-reviewed-scope run, `career-handshake-dc9ddd76e78748bf915131e7c2d3a328`,
started at 03:49:04.271 and stopped at 03:50:34.949 local. READY/GO was announced
immediately after start. Child PID 13716 exited and the transcript in
`career-command-c3c02fabfc8d4eb793cd63b8aca38001` confirms stop, removal of all
temporary filters and successful conversion. ETL 552,093 bytes, PCAPNG 561,316,
report 87,662; 73 packets, zero drops/parse errors/lost ETL events.

The game disappeared at 03:49:32.255 (122 of 163 observations had no game).
The launcher started at 03:49:43.781; game PID **19880** started at
**03:51:55.763**, about 81 seconds AFTER recording ended. Thus the requested
restart did happen, but no new game connection opening was recorded. Eight FIN,
three RST and zero SYN snapshots were present. The latest endpoint query at
04:04 found 35.236.255.68:12101 and 8.221.212.39:21308; port 10200 was not
present. The old .163 peer and PID 16172 must not be assumed current.

More importantly, this file does NOT respect the expected three-peer scope.
Only 21 snapshots match the reviewed endpoints. The remaining **52** are inbound
TCP/443 snapshots at four unrelated remote addresses. They begin at 03:49:08,
before the game exits or filter cleanup occurs. ETL metadata labels these with
filter IDs 4/7 even though the start console shows six peer filters; there are
no ID-100 definitions in this ETL, so a filter-name mapping is not established.
Do not infer a particular driver, protection mechanism, cache bug, or responsible
application from this alone. The installed command resolved to the normal
System32 PktMon.exe, not a workspace executable.

Payload heuristics ran once before the unexpected scope was recognized. No
unrelated raw payload/secret was printed or uploaded; further inspection is now
restricted to headers and ETL metadata. No key log was read or decryption/replay
attempted. Do not feed this file into unrestricted payload/credential scans.
Microsoft documents conjunctive conditions within each filter and matching at
least one filter for capture; that does not explain the observed discrepancy.
See [PktMon filter behavior](https://learn.microsoft.com/en-us/windows-server/networking/technologies/pktmon/pktmon-syntax#capture-filters).

Live `capture-career-handshake.ps1 -Record` is held at entry until scope
enforcement is understood/verified. Its newly prepared `-PriorCaptureReport`
mode can preview the same recently reviewed peers while a user is at the
launcher: <=1 MiB local report, <=30 minutes old, matching old fingerprint and
scope fields, exact recomputation, current local interface, restart flag and no
running game required. Schema 2 does not label historical PID/connections as
current. Six synthetic cases pass; a real preview correctly refused once game
PID 19880 had started. This mode has not recorded live and is also covered by
the live hold. Full suite before the hold: 242 passed; final full discovery
including the actual live-recording hold: **243 passed** at 04:07. All capture
mutation lifecycle checks are mocked, not proof that real packet filters work.

Read-only fallback checks found no installed Wireshark/dumpcap/Npcap. Built-in
`netsh trace` is available; `show status` reports no current trace. Local
`show capturefilterhelp`, `start help` and `stop help` establish IPv4-address
list / protocol filters, physical-adapter capture, bounded output, named sessions
and named-session stop. There is no normal TCP-port filter in that help. Its
CustomIp fixed-offset filter is not a verified bidirectional TCP-port substitute.
An alternative 90-second physical-adapter capture with `Ethernet.Type=IPv4`,
`Protocol=TCP`, and only the current game-server IPv4 addresses would therefore
include all TCP ports at those IPs. That changed scope is NOT yet approved or
run. No broad InternetClient scenario, provider extras, persistent trace,
installer or report package is needed for the proposed packet-only test.
No ordinary-player API response, reproducible authentication, integrated provider
or desktop build was produced. The game can remain open pending the next choice.

### Approved fixed-peer capture, no new connection opening (September 6, 03:46)

The user explicitly approved the proposed 90-second fixed-peer experiment.
The 03:42 preview matched the prior reviewed PID 16172, local IP and three
remote endpoints, duration/restart mode and SHA-256 scope fingerprint exactly.
The agent launched `capture-career-handshake.ps1 -Record -AllowGameRestart`
via the already verified normal one-time RunAs workflow, preserving its admin,
empty-filter and idle-session guards. No UI consent was automated and no
persistent administrator helper or security setting was changed.

Local diagnostics directory: `career-handshake-d06d92e16f1f4537b0b496d140a99335`.
Report interval 2026-09-06 03:42:32.573-03:44:03.393 America/New_York.
ETL 45,307 bytes; PCAPNG 33,144; schema-1 report 344,547. Launch transcript:
`career-command-bd5ae3cbf1ca4649af44a3ab4d7946cf/capture-console.txt`.
Capture child PID 20832 exited; transcript verifies stop, removal of all six
temporary filters, successful PCAP conversion and no lost ETL events.
Report cleanup/metadata failure flags are false. Non-elevated status monitoring
must not interpret a missing logger-name match alone as proof of no capture;
check query success and the actual launched process/transcript lifecycle.

The report's 113 `packet_event_count` includes ID 160 and ID 170. Independent
ETL enumeration found 111 ID-160 events and two ID-170 drop events. Native
conversion and independent PCAP parsing count **111 formatted packets**, with
zero parse skips/errors. Those drop events are not a claim that ETL events were
lost. Six ID-100 filter definitions are present in this capture.

All 139 endpoint observations kept game PID 16172; there were no absent-game
samples, replacement PIDs or metadata errors. The same original tuples remained:
local 64949 to 34.56.114.218:10200, local 64973 to 35.236.255.68:12101,
local 64819 to 8.221.212.163:21308. Twelve outside peers were observed in OS
metadata only; no new peer filters were added. At 03:46 the game's start time
still read September 5 17:02:17.8436427, so no application restart occurred.

| Remote port | PCAP snapshots | Unique inbound bytes / runs | Unique outbound bytes / runs |
| --- | ---: | ---: | ---: |
| 10200 | 31 | 1,281 / 2 | 44 / 2 |
| 12101 | 45 | 93 / 1 | 0 / 0 |
| 21308 | 35 | 0 / 0 | 1,458 / 1 |

Zero SYN, FIN or RST snapshots were present. Streams have no overlapping-data
conflicts but remain opaque; none matches the opening-message, MOS framing,
MessagePack, known Career marker or bounded compression checks. No decryption
or key-log retry was attempted. This capture does not establish a new connection
opening, server identity, active cipher, legitimate Career token or own-profile
response. It is not production API verification.

The agent announced READY during the recording and asked for a normal restart
on the SAME account, followed by own Career/Statistics/History. The process and
tuple evidence does not show that restart. Whether a title-screen reconnect or
tab action was attempted is unknown; the user was asked about timing, not asked
again for the already completed capture's permission. Next coordinate a fresh
recording window with the user's normal reconnect, re-previewing the same scope;
do not silently widen it, force-close the game or repeat a recording based only
on an observation timeout. No source change this turn; last suite **236 passed**.
The stored goal status remains blocked from the earlier approval gate, but
permission for this completed experiment was granted. The API objective remains
incomplete and needs actual connection/authentication/statistics evidence.

### Exact socket opening layout and RPC nesting (September 6, 03:39)

No live-capture approval was received. This continuation used only the existing
hash-gated installed archive and previously approved local capture files. No
recovered game code was executed/imported and no game-memory/network access ran.

Entry 43 (`NetSecurity.py`) defines little-endian `<I`, `<H`, `<B` and `<%ds>`
formats. `SecureSessionHello.MIN_LEN` is 12 and its fixed header is `<IBB>`:

```text
server: total_size:u32, suite:u8, compression:u8,
        nonce_length:u16, nonce:bytes,
        server_key_data_length:u16, server_key_data:bytes,
        client_key_data_length:u16, client_key_data:bytes
client: total_size:u32,
        signature_length:u16, signature:bytes,
        server_key_data_length:u16, server_key_data:bytes,
        client_key_data_length:u16, client_key_data:bytes
```

All integers are little-endian; total size includes the prefix and length
fields. `SecureSessionAck.MIN_LEN` is 10. The game's hello parser reads the
fields in that order; its serializer calculates the exact combined length.
Entry 40's suite constants are 0 (none), 1 (Magic RC4), 2 (ECDH/ChaCha20).
The Python implementation's `recv` consumes this server opening before calling
the decrypted-data handler. `send` constructs ordinary records as
`<IHB>`: total size `len(data)+7`, main command `(_cmd >> 8) & 65535`, subcommand
`_cmd & 255`, followed by data. This specific send path does not apply the MOS
28-bit masking/extension assumptions. The native runtime branch/suite is still
unverified; static format similarity is not wire identification.

`scripts/summarize-career-capture.py` now reports
`socialmatrix_opening_hypotheses` for each contiguous captured run. It checks
only offset zero, requires the complete declared message and exact consumption
by three bounded length-prefixed fields, allows trailing stream data without
decoding it, and suppresses candidates if reassembly contains conflicting
overlap. It never joins gaps, reads keys, extracts field bytes, or labels a
layout match as protocol/authentication verification. A run beginning at a
matching message does NOT establish that TCP startup was captured. Rejecting
unsupported suite values is intentionally limited to the inspected Python
transport, not a claim about other native suites.

Actual offline results, all without parse skips:

| Existing diagnostics capture | Contiguous runs | Unique payload bytes | Opening layout candidates |
| --- | ---: | ---: | ---: |
| career-e00bec92df1148a9a485ad2d5fa2c218 | 4 | 4,327 | 0 |
| career-80be4c3009584ad9a5434d838d736f4f | 6 | 3,006 | 0 |
| career-f3b10f75ebc243f595006cbf75e29034 | 6 | 11,932 | 0 |
| career-96e5877cc37f44ae8d26a0ecaeeae20c | 2 | 3,377 | 0 |

These are bounded structural negatives, not proof that SocialMatrix is absent.
Four new synthetic tests cover both message layouts, empty fields, truncation,
invalid total/field lengths, unknown suites, internal padding, offset rejection,
trailing stream bytes, overlap, gaps, conflicts and non-disclosure. Nineteen
capture-parser tests pass; full source discovery: **236 passed**.

Further static nesting from entry 3476, `http_request`, offsets 504-632:
constructs `{service, headers, uri, method, querystring, post_body, host,
protocol, version}` and dispatches `call_rpc_method(module_cmd + 2, 0, data, cb)`.
Whether `post_body` stays base64 depends on HTTP mode, selected payload type and
`body_use_json`; do not apply the direct-HTTP decoding rule blindly to sockets.
Entry 3005, `call_rpc_method.do_func`, serializes that dictionary into
`body.payload`, optionally base64-wrapped according to the selected payload
type, then serializes the whole RPC envelope for the socket stub.
Its `header` contains `rpcid`, `aid`, connection-state-dependent `streamid` and
original `token`, and optional `metaData` entries for retransmission acknowledgements
(`retran-ack`) and configured payload signatures. No live credential or resolved
Career command number was obtained. The remaining runtime/authentication gap
has not changed: capture the connection opening and verify an own-profile reply
before integrating any provider. The prepared wider-scope capture still needs
the explicit approval already requested from the user.

### Socket transport correction and handshake-first preparation (September 6, 03:34)

Further read-only inspection used the same installed `script.uep`, SHA-256
`4dbe9d3246a05d4a402373cc11c03a938692db9bc16aaaf939ddff4e8682d22e`.
Only the existing bounded archive/custom-bytecode parser was used; recovered
game code was not executed or imported.

#### What the client selects

The word `http_request` in XyqServiceClient is not proof of HTTP/TLS transport:

- Entry 3462, `Scripts/UniSDK/SocialMatrix/SocialMatrixClient.py`:
  `__init__` sets `is_simple_mode` from whether
  `SocialMatrixConst.SocialMatrixNetWrapper is None` (offsets 98-126).
  `socialmatrix_client_base` creates `SocialMatrixHttp` in that mode
  (offsets 16-98), otherwise `SocialMatrixSocketProxy` (240-308).
- Entry 3455, `SocialMatrixConst.py`, `init`: selects the native `socialmatrix`
  implementation when available, or the NeoX/Python socket implementations;
  it assigns the corresponding socket proxy. Entry 3459 calls this initializer,
  and entry 5857 initializes SocialMatrix during the ordinary UniSDK startup.
- Entry 103, `PyMarvelSocialClientHandle.py`, initializes UniSDK/SocialMatrix,
  sets `const_social.SDK_SECURE_PARAM`, and installs the UE HTTP hook. Merely
  having that hook does not prove this request used it.
- Entry 3476, `XyqServiceClient.py`, dispatches through `call_rpc_method`;
  its special handling for an HTTP base is only one branch.
- Entry 700, `SocialMatrixSocketClientBase.py`, reads socket endpoint and
  security configuration from the SocialMatrix token. A legitimate current
  game-issued token is still required; no new authentication bypass or token
  acquisition route was found.

These are installed-code branches, not runtime observations. The SDK has a
simple-mode setter and the active branch is not verified. The previously
recovered HTTPS gateway remains an alternate implementation, not proof that
the captured game's Career requests used HTTPS. This materially weakens the
assumption behind repeatedly capturing only port 443.

#### Socket framing / missing first exchange

Entries 36 (`SocialMatrixNetPythonSocket/Conf.py`), 40 (`TCPConnection.py`) and
43 (`NetSecurity.py`) describe a four-byte little-endian record length and a
seven-byte `<IHB` envelope, with socket encryption outside the plaintext parser.
The connection handles SecureSessionHello and sends SecureSessionAck before
normal decryption/framing. Named alternatives include no encryption, Magic RC4,
and ECDH/ChaCha20. The active suite is NOT established by their presence.
The hello carries suite/compression and session-specific fields; the installed
game passes a separate distributed parameter. Its value, session keys, and
credential values were not output. Entries 3003/3005 connect the wrapper and
socket RPC setup. Entry 5448 contains the distributed parameter, not a live
SocialMatrix login token.

Additional offline single-frame checks on the existing non-443 reassembled runs
also found no complete frame under the tested 32-byte prefix / masked-length
hypotheses. This does not identify the transport or cipher. No non-TLS stream
was decrypted, and no new memory access or live instrumentation was attempted.

#### TLS flight is not a reason to expand TLS 1.3 guessing

For the 03:07 capture's only payload flow, the client sent a one-byte CCS,
then record type 22 with 68 bytes, then type 23 with 2,884 bytes; the server's
type-23 record has 404 bytes. The type-22 body does not parse as complete
plaintext handshake messages. Those three protected-record lengths fit the
HYPOTHESIS of a 16-byte explicit IV, block-aligned ciphertext and a 20-byte
encrypt-then-MAC trailer. This is not negotiated cipher/version verification.

TLS 1.3 encrypted records use outer content type 23; the legacy 0x0303 field
does not establish TLS 1.3. TLS 1.2 key expansion needs both hello randoms,
which this capture does not supply. See [RFC 8446 section 5.2](https://www.rfc-editor.org/rfc/rfc8446#section-5.2),
[RFC 5246 section 6.3](https://www.rfc-editor.org/rfc/rfc5246#section-6.3), and
[RFC 7366 section 3](https://www.rfc-editor.org/rfc/rfc7366#section-3).
No TLS 1.2 decoder or additional TLS 1.3 key/counter search was implemented.

At 03:11 the local DNS cache associated 8.221.191.202 with
`acsdk-x20na.gameyw.easebar.com`. That is a cache association only, not SNI,
authenticated peer identity, proof of service purpose, or a Career request.
The entry was not returned by the 03:33 cache query. No captured game record
has been authenticated/decrypted.

#### Prepared next capture, not yet authorized/run

`scripts/capture-career-handshake.ps1` defaults to a read-only preview. It
selects only existing game connections to ports 10200/12101/21308, deduplicates
exact local/remote IP plus remote-port peers (maximum six), and pins those peers
for at most 90 seconds. It intentionally omits the local ephemeral TCP port so
the first bytes of replacement connections can be recorded. Consequently, it
can capture another local process contacting the same exact peers. This scope
difference requires explicit approval before `-Record`; a fingerprint is not
consent. New remote peers are never added, even when endpoint metadata changes.

Optional `-AllowGameRestart` keeps the same filters across a user-controlled
game restart; it does not launch, close, switch accounts or log in. Without the
flag, a game exit/PID change ends recording. Multiple game processes stop it in
either mode. Replacement PIDs are metadata only, never packet ownership proof.
The report explicitly leaves handshake, Career access and packet ownership
verification false. TLS keys from an earlier process must not be assumed to
match a replacement process/session.

Both real PowerShell previews at 03:33 found PID 16172, local 192.168.4.25 and
peers 34.56.114.218:10200, 35.236.255.68:12101, 8.221.212.163:21308.
The 90-second restart-enabled fingerprint was
`7a61f47cad03691ae652863231b4ee4c08f5af0c52ed9bce3c83f2daf789166e`.
It covers initial PID, duration, restart flag and sorted peers; re-preview and
review any changed scope before launch. No recording, filters, capture files,
game restart or network request was performed by these previews.

Record mode requires admin, idle Packet Monitor, empty filters and matching
reviewed scope. Native help and [Microsoft's filter documentation](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/pktmon-filter)
confirm that this CLI removes all filters, not a named one. Cleanup therefore
checks an unchanged saved filter-list snapshot after capture stops (or before
any start attempt); uncertain start/stop and changed/unreadable lists retain
filters with a warning. It never blindly clears another observed filter list.
At 03:33 no PktMon session was listed. Reading its driver filters was denied to
the non-elevated worker; filter emptiness is not currently verified and the
elevated helper must check it rather than assuming the prior cleanup still holds.

Twelve new synthetic lifecycle tests pass in both PowerShell 5.1 and 7.6.5;
full source discovery: **232 passed**. Tests mock all capture mutations, files,
OS metadata and waiting; a fixture string-precedence error was corrected before
the passing run. No production provider, authenticated own-profile response,
credential/vault change or desktop build was added. Next: obtain the fixed-peer
capture approval, start it via the already verified normal RunAs command, then
have the user reconnect normally and open own Career only after READY. The
previous activity-timing question, if answered, is not this scope approval.

### Agent-started captures and first TLS payload (September 6, 03:09)

The user's request to enter the commands was fulfilled via normal one-time
`Start-Process -Verb RunAs -WindowStyle Hidden`, not terminal UI automation.
The exact existing `-TlsOnly -AllComponents -FollowConnections
-ExpectedGameProcessId 16172` helper ran with its guards unchanged. Any UAC
consent remains the user's action. Local Start-Transcript output records only
the helper's console output; no persistent administrator broker/settings were
installed. The worker itself is still non-elevated.

The first run, diagnostics `career-d5f26d26f744415ca79b154b0e53b52b`, completed
03:04:21.553-03:04:52.002 with zero packet events and a 188-byte empty PCAPNG,
independently checked. Two selected HTTPS tuples remained present in all 48
samples, with no new eligible connections or capture/cleanup errors. User UI
activity during that window is not verified.

The second run, `career-96e5877cc37f44ae8d26a0ecaeeae20c`, completed
03:07:06.702-03:07:37.102. ETL 29,663 bytes; PCAPNG 12,296; schema-4 report
95,348. Six new HTTPS connections were added without exhausting the limit.
ETL/report and independent dpkt parsing count **56 snapshots**, all on port
443, without parse errors. The sole payload-bearing tuple is game-selected
192.168.4.25:61192 <-> 8.221.191.202:443. Its 13 snapshots reassemble into
2,968 client bytes and 409 server bytes, with no gaps or conflicting overlap.

The client stream contains complete TLS records of content types 20/22/23,
with body lengths 1/68/2884; the server stream contains one type-23 record of
404 bytes. All legacy header versions are 0x0303. Initial-hello correlation
reports `initial_hello_missing`; no negotiated version/cipher or peer identity
is established. The TLS-1.3-only verifier reached the two-million-attempt
bound without authenticating a record. The approved local key log parsed 393
complete TLS 1.3 application pairs and 1,578 recognized lines, with no invalid,
duplicate or conflicting entries. No key/credential/plaintext was output or
uploaded. Next inspect this record flight before considering more TLS-1.3
counter guesses; header version 0x0303 alone is not version negotiation.

Game-window inspection confirmed UID 1370598422, Career Overview, S3.0 (The
Abyss Awakens), "No Season Data". Two automated Statistics clicks failed to
produce a visible change and were not repeated further. A manual tab change
was requested while recording, but the captured TLS flow is not proven to
correspond to Career activity. Both captures stopped and removed their filters
without reported failures. No TLS record or Career response has been decrypted,
no production provider was integrated, and no source code changed this turn.

### First inbound capture and user-confirmed own UID (September 6, 03:02)

The user confirms **1370598422** was their displayed in-game UID and that the
capture activity opened **their own Career**. This resolves the own-versus-
searched-profile ambiguity for the reported activity, but is not verification
of a server response or a reason to change the saved imported account.

The new `career-f3b10f75ebc243f595006cbf75e29034` capture has a 46,861-byte ETL,
61,412-byte PCAPNG and 64,737-byte schema-3 report. Both ETL and independent PCAP
parsing find 79 snapshots, 70 outbound and nine inbound. All nine inbound
snapshots are from the game-selected port-12101 connection (local 64973), with
5,680 unique server bytes after deduplication. Its outbound runs are gapped,
1,124 and 617 bytes. Port 10200 has 4,464 unique outbound bytes in one run.
The payloads remain unidentified: no TLS records, known Career field markers,
bounded zlib/zstd candidate, whole MessagePack container or MOS plaintext
framing hypothesis was recognized. No decryption or API request was attempted.
There were no captured port-443 packets.

`-AllComponents -FollowConnections` was actually used. Twelve connections were
added successfully, proving late filter additions work in this environment.
Ten were short-lived 8880/8881 flows; their purpose is not established. The
16-connection/32-filter limit was reached after about 16 seconds, so the capture
stopped early with `scope_refresh_failure: connection_filter_limit_reached`.
The new HTTPS tuple local 58725 -> 35.186.233.65:443 was added; local 58726 to
the same remote endpoint could not be added. That IP's prior replay-service
DNS association is not an authenticated peer identity or verified Career host.

The ETL decodes only packet events and anonymous control/schema events, with
no ID-100 filter-definition records. All inbound snapshots have filter ID 7;
its name/orientation cannot be mapped from available decoded metadata. Thus
the changed capture contains replies, but it does not prove explicit reverse
filters fixed the previous missing-reply problem.

Next scoped comparison uses `-TlsOnly -AllComponents -FollowConnections` so
the 8880/8881 burst cannot exhaust the HTTPS filter budget. A live dry run first
failed because the game then had no established HTTPS connection. The helper
now accepts zero initial eligible connections only in follow mode and waits
up to 30 seconds before any capture/file/filter mutation. It still selects
only the same game's established tuples, rechecks PktMon idle/empty filters
after waiting, and never starts with zero filters. A timeout/process change
leaves capture settings untouched. Once CAPTURE READY appears, the user must
repeat the Career/tab action: detecting a connection after it opens can miss
its handshake and initial request. Report schema 4 adds the initial-wait flag.

Eleven scope/count/lifecycle methods pass in both Windows PowerShell and Core,
including a 20-connection non-HTTPS burst, a first eligible connection, wait
timeout, process replacement and an intervening existing trace. Ten filter
guard checks pass. Full source suite: **220 passed** at 03:02. Later real dry runs found one eligible HTTPS connection,
three excluded remote game connections, and made no changes.

```powershell
& "C:\CodexRepos\marvel-account-keeper\scripts\capture-career.ps1" -TlsOnly -AllComponents -FollowConnections -ExpectedGameProcessId 16172
```

This next recording has not yet run. There is no verified current-session
Career response, usable/repeatable Career authentication, live TLS decryption
or new production provider/build. Account bindings and vault data are unchanged.

### All-component comparison and explicit-direction/follow preparation (September 6)

The next capture arrived at 02:42 local in diagnostics
`career-80be4c3009584ad9a5434d838d736f4f`. Its schema-2 report confirms `all`
components. The ETL is 41,970 bytes and PCAPNG 11,892 bytes; both independently
count **15 snapshots**, all outbound. Components 11, 27, 28, 29, 61 and 77 occur,
so the component change was effective but did not solve missing replies.
There were three port-10200 snapshots (24 unique bytes in two runs), 11 on
12101 (2,703 unique bytes in three runs), and one on 21308 (279 bytes). No
HTTPS snapshots or live Career response was obtained. No decryption attempt
was made on these gapped/unidentified game-protocol streams.

The outgoing TCP acknowledgements advanced by 6,375 bytes for the port-12101
connection and 40 for port 10200, without backward/large deltas. This is evidence
of incoming TCP data absent from the packet recording, not proof of its Career
content. Windows `tracerpt` reports zero lost events, 695 processed events and
two buffers. It warned that some events lack matching schemas. Only its local
summary was retained; raw XML output was directed to NUL. A metadata-only
`capture-analysis.json` preserves the analysis separately from the originals.

Two more late HTTPS connections to the previously DNS-associated replay service
35.186.233.65, local ports 62285/62286, again fell outside the fixed filters.
This recurring coverage gap is established independently of the directional
capture issue. The installed `netsh trace show capturefilterhelp` has no TCP
port filter; no broader netsh capture or driver installation was performed.

Two scoped changes are now prepared in the capture helper:

- Explicit forward and reverse address/port filters for every selected game
  connection. [CLI documentation](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/pktmon-filter-add)
  describes unordered pairs, whereas the [underlying Win32 API](https://learn.microsoft.com/en-us/windows/win32/pktmon/packetmonitor/nf-packetmonitor-packetmonitoraddcaptureconstraint)
  describes source/destination fields. This discrepancy motivates the experiment;
  it does **not** prove the CLI currently mishandles direction. If pairs are
  unordered, the second filter is simply redundant.
- Opt-in `-FollowConnections` observes established TCP connections belonging to
  the same game PID every 500 ms and adds exact forward/reverse filters for new
  tuples. Maximum 16 connections/32 filters. Filter failure, the cap or a changed
  process stops capture with existing data preserved. No empty-filter interval,
  broad-filter fallback, other-process selection or elevation is introduced.
  The API documents adding constraints before/after activation; the CLI path
  is not yet live-verified. Fast connections/first records can still be missed.

Report schema 3 records explicit reverse filters, follow mode, late additions,
and scope-refresh errors, and does not claim complete new-connection coverage.
Seven scope/count/lifecycle test methods pass in both PowerShell engines,
covering forward/reverse filters, fixed scope, a late connection, failed partial
pair cleanup, filter-cap stop, and process-change stop. Ten filter guard cases
pass; full suite **216 passed**. A real dry run selected four game connections
and eight direction-explicit filters, without starting a recording.

Next user-run experiment, not yet executed:

```powershell
& "C:\CodexRepos\marvel-account-keeper\scripts\capture-career.ps1" -AllComponents -FollowConnections -ExpectedGameProcessId 16172
```

The same diagnostic game remains open. No game-memory work, game modification,
proxy/certificate change, adapter setting change, raw plaintext export, vault
edit, verified own-player lookup or production provider integration occurred.

### September 6 capture: outbound packets present, replies absent

The user's next run created diagnostics `career-e00bec92df1148a9a485ad2d5fa2c218`
at 02:32:12.395-02:32:42.793 local. Its 43,462-byte ETL and 8,100-byte PCAPNG
each contain **18 packet snapshots**, despite the original report incorrectly
claiming zero. That original report remains intact; a local `capture-analysis.json`
records the independently checked result.

The reporting defect reproduced directly: `Get-WinEvent -FilterHashtable`
with the ETL path, PktMon provider and IDs 160/170 returned NoMatchingEventsFound;
unfiltered `Get-WinEvent -Path ... -Oldest` exposed 18 ID-160 events. The helper
now enumerates the bounded ETL and filters decoded provider/ID properties. A
verified zero requires observed PktMon metadata and a completed read; empty,
foreign-only or partial/failed reads are not treated as evidence of zero.
No event message or payload property is formatted. Actual count 18 passed in
both PowerShell engines, and the earlier genuinely empty ETL still counts zero.

All saved packets are outbound on Wi-Fi component 77; no replies were saved:

| Remote port | Saved packets | Unique outbound bytes | Contiguous runs |
| --- | ---: | ---: | ---: |
| 12101 | 14 | 3,881 | 1 |
| 10200 | 2 | 51 | 2 |
| 21308 | 2 | 395 | 1 |

PktMon adapter counters include receive packets despite the lack of saved
receive records (component 77: 21 receive/84 transmit; component 11: 24/69).
The exact capture-layer cause is unproven. Port 12101 and 21308 checks returned
`no_complete_tls13_candidate_records`, zero attempts, no key-log read. Other
bounded checks found no Career markers, MessagePack or zlib candidate. Ambiguous
port-21308 length-prefix hypotheses with 107/164/124-byte records do not identify
the payload or a Career request.

The four initial tuples stayed present in every metadata sample. Two new HTTPS
connections to 35.186.233.65 (local ports 56827/56828) appeared in the last sample,
outside the fixed filters. DNS cache associated this destination with
`iowa-uscentral-replayclient-x20na.easebar.com`. That association is not SNI/peer
verification and is not evidence of a Career gateway. No HTTPS packet was saved.
The key file was 303,806 bytes at 02:33, with last write 02:32:57; no key values
or plaintext have been output and no session has been correlated with traffic.

An opt-in `-AllComponents` now selects `pktmon start --comp all` while preserving
the same exact game TCP endpoint filters. This is a capture-point experiment,
not a proven fix. [Microsoft documents component selection](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/pktmon-start)
separately from endpoint filters. Capturing across components can duplicate
packets; [Microsoft's MsQuic guide](https://github.com/microsoft/msquic/blob/main/docs/TroubleshootingGuide.md)
notes that conversion can later select one component. No broad all-app filter,
dynamic new-connection capture or self-elevation was added. Report schema 2
records the component selection. All **212 tests pass**, including updated
counter failure cases and exact-filter/all-component lifecycle mocks in both
PowerShell engines. A real dry run found four remote game TCP connections.

Next user-run experiment, not executed by the agent:

```powershell
& "C:\CodexRepos\marvel-account-keeper\scripts\capture-career.ps1" -AllComponents -ExpectedGameProcessId 16172
```

After CAPTURE READY, reopen own Career, change a statistics/history/season
filter near the beginning and repeat once before the 30-second stop. The
diagnostic game remains open. Full capture coverage, legitimate repeatable
Career authentication, a verified own-profile response and provider integration
remain unresolved. The account vault and production provider were not changed.

### New capture received: zero packets, not an authentication failure (2026-09-05)

The user completed the approved `-TlsOnly -ExpectedGameProcessId 16172` capture.
Its files under local diagnostics `career-67699424dc5049be847b0d5dc5a6c91b`
were inspected without outputting payloads or keys:

- ETL: 25,021 bytes, 22:15:53.535-22:16:23.653 local capture interval.
- PCAPNG: 188 bytes, Ethernet link type, **zero packets** using dpkt.
- ETL independently contains zero packet events (PktMon IDs 160/170), and all
  107 flow-counter events total zero matching packets. No decryption was run.
- Its single recorded filter was TCP 192.168.4.25:57203 to 8.221.56.176:443.
  At a later metadata check the HTTPS local port was 65279. This establishes
  turnover by the later check, not its exact timing within the capture.
- The same game also had established connections on ports 12101, 10200 and
  21308, all excluded by `-TlsOnly`. Their Career role remains unverified.

The selected two-IP/two-port filter syntax is supported by the installed help
and [Microsoft's filter documentation](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/pktmon-filter-add).
An empty ETL cannot distinguish inactivity/caching, missed new connections,
non-443 transport, or an NIC capture-layer issue. It says nothing about the
validity of the user's UID or session credentials. PCAP conversion did not
discard packet events; none were present in the original ETL.

`scripts/capture-career.ps1` now counts only packet events for its outcome,
distinguishes an unreadable ETL from a verified empty capture, and writes a
local endpoint/count `capture-report.json`. It samples the selected game's
established remote TCP endpoints every five seconds, reporting selected tuples
that disappear and observed tuples outside the fixed filters. No payload,
token or key is included in the report. Filters are not changed during capture;
new/short-lived connections can still be missed. Sampling is not proof of
per-packet process ownership. No broad all-app capture or self-elevation was added.

Three scope/count/lifecycle test methods pass in both installed PowerShell
engines, including a fully mocked zero-packet capture with a replaced HTTPS
connection, exact filters on port 443 AND 12101, and stop-before-remove cleanup.
The ten existing filter-list cases also pass. Full suite: **210 tests passed**.
The new counter function returned verified zero on the actual ETL. An actual
default dry run at 22:23 found five remote game TCP connections, excluding none.

The next user-run comparison omits the HTTPS-only restriction:

```powershell
& "C:\CodexRepos\marvel-account-keeper\scripts\capture-career.ps1" -ExpectedGameProcessId 16172
```

After CAPTURE READY, reopen own Career and switch statistics/history and the
season filter if available. Keep the diagnostic game and key file local/open.
This comparison has not yet run; non-443 inclusion is not a demonstrated fix.
The key file grew from 31,278 bytes at 22:17 to 182,036 at 22:23:51 (last write
22:23:33). New key-log entries have not yet been paired with captured traffic.
No production provider, vault, account binding or desktop package changed.

### Bounded TLS 1.3 midstream verifier (2026-09-05)

The new standalone `tls_midstream_probe.py` implements an offline check using
the already-authorized local key file and an explicitly selected captured TCP
flow. This does not capture traffic, connect to a server, read game memory,
export credentials/keys, save plaintext or change the production provider.
Default CLI mode is metadata-only; inspection requires explicit file paths and
both endpoint IPs/ports. TCP reassembly reuses the existing gap/conflict/reuse
checks in `tls_capture_probe.load_selected_flow`.

Protocol basis: [RFC 8446 sections 5.2, 5.3 and 7](https://www.rfc-editor.org/rfc/rfc8446)
define traffic-key expansion, record-header authentication and nonce construction.
The checker uses the logged generation-zero application secrets with AES-128-GCM,
AES-256-GCM or ChaCha20-Poly1305, validates each authentication tag, and checks
inner content types/padding. The authenticated-encryption library rejects wrong
keys, nonces, associated data or changed ciphertext with an
[InvalidTag result](https://cryptography.io/en/latest/hazmat/primitives/aead/).

Counter/record-boundary recovery is explicitly bounded: at most 1,024 key-log
session groups, eight plausible record anchors within the first 16,645 bytes,
starting counters 0–4095, two million decryption attempts, and a 20-second
budget checked every 256 attempts. Every candidate key is tried at each counter
in ascending order, rather than exhausting all counters for an older unrelated
key first. All keys are checked at the first successful counter before returning;
the per-direction summary reports the actual `starting_sequence_search_ceiling`.
Alternative matches requiring higher counters are not ruled out. Existing
input-file/flow byte bounds still
apply; decrypted application data is capped at 2 MiB per direction and each
chain at 512 records. The first authenticated anchor is not skipped to hide a
later failure. All subsequent complete records must authenticate at consecutive
counters; incomplete leading/trailing capture bytes stay explicitly unexamined.

Successful CLI verification requires a unique matching session/cipher across
both directions. Its result is `authenticated_tls13_record_prefixes`, not full
TLS-connection or Career verification. Process ownership, peer/handshake identity,
application protocol/schema and Career access remain false. Fixed Career-operation
markers may be counted inside authenticated application bytes, but do not prove
that those bytes form a successful request or response. Private decoded objects
disable data representations, and the JSON output contains no plaintext, keys,
session-random values, header values or account claims.

Eighteen test methods include independent [RFC 8448 section 3](https://www.rfc-editor.org/rfc/rfc8448)
client/server application secrets, expanded keys/IVs and complete encrypted
application records. The actual published client record at sequence 0 and server
record at sequence 1 each decode to the published 50-byte payload without hello
records. Additional synthetic tests cover all three supported ciphers, padded
records, nonce byte order, partial captures, wrong keys/counters, tampered tags,
missing/duplicated records, cross-session direction mismatches, duplicate aliases,
conflicting key-log lines, malformed inner records, bounds and output redaction.
At 22:25 local, the live key file reproduced `keylog_session_limit_exceeded`
against the original 128-session cap: its 878 valid recognized lines contained
218 paired TLS 1.3 application sessions, no invalid/conflicting lines. The new
bound/search order supports a synthetic 218-pair log with the RFC vector last
using only 1,308 attempts at the default counter limit. Another test preserves
older keys and authenticates counters 257/258. The live file then parsed 219
pairs at 22:26, with no plaintext/key output or record-decryption claim.
Full suite: **212 tests passed**, with the existing injected-error/ResourceWarning
messages. These tests do not establish live game API access.

TLS 1.2, CCM, early data, key updates, full-handshake validation, TCP gaps and
HTTP/2 header decompression remain unsupported. No new game capture existed at
the 17:22 local file-metadata check. The current input needed is the user-run
30-second selected-game capture described below, not an additional test against
the known old opaque capture or a broader all-app capture.

### Live diagnostic key log and next capture (2026-09-05)

The user reports having run the approved elevated helper and being logged in
at the lobby/Career screen. Read-only metadata at 17:07 local confirmed game
PID 16172, started at 17:02:17, and bootstrap PID 16712. The diagnostic directory
`career-tls-3096395a0b7a49eebc6940baca48c3a1` contains `session.keys`, created
17:02:12, initially 26,958 bytes and later 31,278 bytes. The previous two
failed-attempt directories remain empty. File ACL inspection found exactly two
FullControl rules, for the current Windows user and SYSTEM only.

Using the bounded local `tls_capture_probe.read_keylog` parser, one format-check
snapshot produced:

- 134 recognized lines; zero invalid, duplicate or conflicting entries;
- 32 ignored lines (their contents were not emitted);
- 32 distinct session identifiers with all four TLS 1.3 handshake/application
  traffic-secret labels;
- six `CLIENT_RANDOM` entries, which alone do not identify a negotiated version.

Only counts and fixed labels were output. The parser retained digests/lengths,
not returned secret values. No keys/randoms, decrypted body, token or account
claim was printed, exported or transmitted. File growth after game startup is
useful evidence of the diagnostic run, not independent proof of the writing
process or the Career service. No connection has yet been correlated/decrypted.

The game-owned TCP metadata snapshot contained four port-443 connections and
three other remote ports (12101, 10200 and 21308); only gaming-sdk.com-related names
matched the current DNS cache. These associations do not establish a Career
gateway. A later dry run found two established port-443 connections. The changing
set is precisely why an old connection list must not be reused blindly.

The capture helper now accepts `-TlsOnly` and `-ExpectedGameProcessId`. It refuses
a changed game process before querying connections or modifying anything, then
selects only current established game TCP connections with remote port 443.
Temporary filters match both IP addresses and both ports, using the installed
Packet Monitor's documented two-address/two-port syntax. This is not a broad
all-application HTTPS capture. Default existing behavior is retained without
`-TlsOnly`. The two flags and loopback exclusion pass a synthetic, mutation-blocked
dry-run test in both installed PowerShell engines; the existing ten filter-list
guard cases also pass. Full source suite: **192 passed**.

Proposed user-run capture, not executed by the agent:

```powershell
& "C:\CodexRepos\marvel-account-keeper\scripts\capture-career.ps1" -TlsOnly -ExpectedGameProcessId 16172
```

After `CAPTURE READY`, leave the user's own Career page and reopen it, then
switch one statistics/history tab. The capture stops after 30 seconds. Keep
the diagnostic game open and all capture/key files local. This only selects
already-established connections; new connections are excluded, and neither
Career traffic nor a TLS handshake is guaranteed. No game socket will be closed
or reconnect forced to manufacture a handshake.

The original hello-correlation helper deliberately cannot accept a mid-session
stream without hello records. TLS 1.3 traffic keys and record nonces are specified
in [RFC 8446's record-protection/key-schedule sections](https://www.rfc-editor.org/rfc/rfc8446).
They motivate checking whether an authenticated, bounded midstream decryption
can use this session's logged application traffic secrets without another
relaunch. That is an inference to investigate, not an implemented/decrypted
result. TLS 1.2, missing records, cipher/sequence state and higher-level framing
remain possible limits. Never substitute a key file's existence for a verified
first-party request/response.

### Authorized launch attempts and PowerShell compatibility fix (2026-09-05)

The user explicitly answered "yes" to the proposed one-session local TLS
diagnostic launch. This supersedes earlier unanswered-approval checkpoints;
do not ask for that same approval again. The normal game session exited while
the agent waited on its observed processes. No forced close was performed.

The first actual `-Launch` attempt failed before process creation: PowerShell
Core 7.6.5 does not expose `[IO.Directory]::SetAccessControl`. The previous
tests had exercised only Windows PowerShell's .NET Framework implementation.
The helper now selects the modern
[FileSystemAclExtensions API](https://learn.microsoft.com/en-us/dotnet/api/system.io.filesystemaclextensions.setaccesscontrol)
when available and retains the Framework API otherwise. Both test methods now
run against Windows PowerShell and the bundled PowerShell Core; each verifies
syntax/decisions/child-only environment or the actual ACL of an empty synthetic
directory. Both engines passed and full source discovery passed **191 tests**.

The second attempt successfully created its private directory, but the normal
`Process.Start` call failed with "The requested operation requires elevation."
The worker is non-elevated. No diagnostic launcher started, no key file was
created, no capture ran and no parent/user/machine environment changed. No
compatibility/privilege settings, game files, certificates or protections were
modified, and no self-elevation was attempted. Two empty diagnostic directories
remain from the attempts; directory presence must not be treated as key logging.

The concrete next action is to run the already-approved helper from the user's
Administrator PowerShell, then use the new launcher's normal Launch button:

```powershell
& "C:\CodexRepos\marvel-account-keeper\scripts\launch-career-tls.ps1" -Launch
```

Keep any generated session keys local. This first live test still needs to
establish that the actual game produces usable diagnostics; fresh, scoped
capture and Career request/response verification are later unresolved steps.

### Offline capture/key-log correlation preparation (2026-09-05)

While the live-diagnostic approval is unanswered, `tls_capture_probe.py`
provides a bounded offline check for a caller-selected TCP connection. It is
separate from the production provider and defaults to file-existence metadata
only. `--inspect` requires explicit client/server IPs and ports; optional
`--keylog` is read locally only after both initial hello messages parse.

The checker reassembles only that selected 4-tuple, rejects observed connection
reuse, gaps, unsupported sequence wrap and conflicting TCP overlaps, and
parses initial TLS 1.2/1.3 ClientHello/ServerHello messages without searching
arbitrary packet offsets. It handles hello fragmentation across TLS records.
HelloRetryRequest is explicitly unsupported. It does not validate the entire
handshake, TLS authentication, the remaining stream or application payloads.

Key-log correlation uses the selected ClientHello random and required label/
length combinations. Duplicate lines do not add evidence, contradictory lines
cannot later be repaired by a duplicate, and unrelated launcher/old-session
identifiers do not match. The parser retains secret digests/lengths rather than
returning secrets, with fixed non-secret diagnostics and explicit byte/packet
limits. See [Wireshark's TLS documentation](https://wiki.wireshark.org/TLS)
for the key-log/capture relationship; a key file is not a substitute for the
matching connection's captured handshake and records.

The strongest possible helper result is
`matching_material_not_yet_decrypted`. It always leaves
`process_ownership_verified`, `tls_decryption_verified` and
`career_access_verified` false. The selected flow's game ownership needs
independent evidence; successfully parsing two hello messages does not prove
that Career used the connection. No decryption/export, network requests,
process access or capture/launch operation is implemented in this checker.

Verification:

- Seventeen synthetic test methods cover TLS versions/ciphers, key labels and
  lengths, missing/duplicate/conflicting entries, unrelated session identifiers,
  fragmented/malformed/truncated input, resource bounds, selected-flow filtering,
  TCP conflicts/gaps/reuse, metadata-only defaults and redacted capture errors.
- The existing September 4 `career.pcapng` selected connection was confirmed
  from metadata as client port 58953 to `35.236.255.68:12101`, with 23 packets.
  Running the checker without a key log returned `not_initial_tls_records`;
  both hello-presence flags and all verification flags were false. This is an
  expected negative control, not proof the game has no HTTPS traffic elsewhere.
  No packet body or credential was output.
- Read-only `pktmon filter add help` confirms that this installed Packet Monitor
  supports address/port/protocol filters but no PID/process packet filter.
  Only help was requested; no filter or capture was changed. The original
  established-connection filter can miss new handshakes, and a new properly
  scoped capture workflow is still needed. Broad all-app HTTPS is not an
  approved replacement.
- Full source discovery: **191 tests passed**. Existing injected-error logging
  and temporary-file ResourceWarnings remain. No provider/vault/account binding
  or packaged build changed in this preparation.

The proposed normal launch with child-only TLS diagnostics is still awaiting
the user's explicit approval. No live logging, fresh capture, session-key file
or first-party authenticated player response has been produced by these steps.

### Remaining integration evidence

Obtain a reproducible request/response for an **ordinary owned player** from a
first-party service: method, URL, non-secret input schema, required authentication
mechanism, and response schema. Public site JavaScript or a narrowly scoped,
user-authorized examination of an own-account game lookup could establish this.
Do not substitute another tournament success for that evidence, or ask the user
to paste credentials/session tokens into chat.

Once available, implement the actual player provider with explicit identity and
platform handling, preserved rank-refresh failure semantics, and synthetic schema
fixtures. Match ingestion should reuse existing opt-in preview/commit code only
after owner identity, timestamps, result perspective, and statistic semantics
are understood. Rank and match-history state remain separate.
