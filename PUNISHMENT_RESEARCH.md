# UID-linked punishments: evidence and integration boundary

September 11, 2026. The user requested research toward a UID-associated
Punishments tab. No punishment record was retrieved, no report was submitted,
and no website punishment feed is enabled by this work.

## Finding

The normal game contains a read-only per-target CURRENT restriction getter.
It uses the same namecard service family as season ranks, with a different
tag. This is a concrete operation, not a guessed URL. Its accessibility with
our authorized session and actual response schema have not been live-verified.
It is not an established punishment-history API.

Offline source: installed script archive with SHA256
`4dbe9d3246a05d4a402373cc11c03a938692db9bc16aaaf939ddff4e8682d22e`.
The existing hash-validated offline inspector read selected structural values;
no recovered code was executed, credentials inspected, or game process used.
Embedded source lines below are not line numbers in this repository.

## Observed current-status read

Archive entry4863, `Scripts/sdk_utils.py`, function `get_player_punish_info`
(embedded line267):

- Service `xyq-namecard`, operation `GetData`.
- Body keys: `host` = current zone ID, `tag` = `punishment`, and `id` =
  the target player's ordinary `zone_UID` aid with `_login` appended.
- On application success code0, it interprets `data.time` and `data.expire`
  as integers. `expire` is a DURATION in seconds, not an expiry timestamp.
- Its current-state check is `expire > 0` and `time + expire > server_time`.
  A current login restriction is returned immediately.
- Otherwise the selected report category is mapped through a game data table
  by entry4515 `report_utils.get_report_lv1_ban_type` (line56). A nonempty,
  non-login mapped value triggers the same read with that suffix instead.
- Entry5476 `const_report.BanType` (line72) defines literal category strings
  `login`, `chat`, `rank`. These are restriction categories, not proven causes.

There is no list/page/date-range operation in this traced function. The login
first/early-return behavior also means it is not a complete inventory of all
simultaneous restrictions. Do not invent an HTTP route from the operation name
or assume permission for the ranks tag implies permission for punishments.

## Identity, cause and completeness caveats

Entry4515 `report_utils.show_report_success_panel` (line149) consumes `uid`,
`name`, `time`, `expire`; its seven-day threshold is604800 seconds.

The getter's secondary callback replaces `name` with the already known target
name. If no data exists, it synthesizes a UID/name object for a generic report
acknowledgement. Those injected fields are NOT independent server identity
evidence. A reader must correlate the raw reply to the exact service/request
and check a UID if the server actually supplies one, before such client changes.

The examined consumer does not establish an actual infraction/reason field.
Never label a login restriction as cheating, or a chat restriction as voice
rather than text, from the category alone. The category chosen by a reporting
player is an allegation/input, not an adjudicated reason returned by NetEase.

An empty/missing/expired record, service error, or unsupported session cannot
establish "never punished", "clean history", or a complete historical record.
An older public announcement likewise cannot prove a restriction remains active
or whether an appeal/reversal later changed it.

## Where the game uses it

Only normal UI caller located in this bounded search:

1. ReportConfirm submits the selected player report: entry5816 line224 via
   entry1864 `MosClient_Report` line135 (`C_GAME_REPORT`).
2. A report-success event invokes its result panel (entry5816 line66).
3. That panel calls the getter (entry5816 line331).
4. The returned status may inform an already-under-punishment acknowledgement.

The getter itself performs only namecard reads. No independent public Career
tab for this data was found. Any server-side report prerequisite remains
unknown. DO NOT file a report or acknowledge/clear warnings as a lookup test.
The presence of a callable client helper does not establish arbitrary public
access to private moderation records.

## Own-account state is separate

Entry1864 receives `clientForbidInfo`, `clientForbidExt`, `lastForbidSeqs` and
`warning_seqs` from the own avatar WebGM component (entry5356). The corresponding
punishment-panel UID uses the own player/own login-zone list (entry823 line195).
These are not evidence of another UID's history. Write operations including
`C_RECEIVED_REPORT` and `C_CLEAR_WARNING` are not lookup operations.

The inspected SvcUser User schema (entry3011) and UserClient (entry3012) have
normal social/identity fields and opaque extra dictionaries, but no explicit
sanction-history field/getter was established. That bounded result is not proof
that no other source exists.

## Official public web sources checked

- [May25, 2026 penalty announcement](https://www.marvelrivals.com/announcements/20260525/40955_1301465.html):
  publishes masked UIDs/names, not reliable exact-UID associations. Do not
  reconstruct identities or attach records through partial-ID/name matching.
- [August2024 account bans notice](https://www.marvelrivals.com/update/20240815/40955_1171770.html):
  also masks game IDs/names; it is an announcement, not a live per-player feed.
- [July31, 2025 community management announcement](https://www.marvelrivals.com/announcements/20250731/40955_1251113.html):
  confirms voice/text moderation mechanisms, but supplies no per-UID history.
- [Official release FAQ](https://www.marvelrivals.com/news/20241205/40185_1198415.html), Q25:
  documents reporting and own-account appeals through support, not public history.

## Next bounded verification and tab contract

First establish the authorized, independently renewable server session required
by the main goal. Then verify this read with an explicitly authorized test account
and exact request correlation; do not use real reports to unlock it. Determine
actual access restrictions and response fields before expanding to another UID.

If a supported authorized source is confirmed, the tab should label Current
restrictions separately from History, keep unknown causes unknown, show checked
time and source, and preserve missing/expired/unavailable distinctions. History
remains unavailable unless a separate authoritative historical source is found.
The user has been asked for a link/screenshot of the punishment-history feature
they saw, which may identify that missing source or clarify the intended feature.
