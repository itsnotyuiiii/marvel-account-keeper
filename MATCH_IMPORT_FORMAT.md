# Marvel Rivals Match Import Format

Marvel Account Tracker accepts owner-authorized local JSON and CSV files using
the `mrat.matches.v1` schema. Imports use a two-step **Preview import** then
**Confirm import** flow. Preview stores nothing; confirmation must resend the
same input plus both matching source and preview-scope digests.

The importer does not fetch URLs, call a match provider, or accept ZIP/archive
files. Only a file selected from the local computer as JSON or CSV, or a manual
entry made in the app, is accepted.

## Ownership, matching, and privacy

Before importing, the source account must have all of the following saved in
the tracker:

- A 6-11 digit Marvel Rivals UID.
- An explicit platform: `pc`, `playstation`, or `xbox`.
- **Authorize local match history** enabled.

The user must also accept the ownership/authorization attestation for every
import batch. The source file's `(platform, UID)` pair must exactly match that
saved, authorized account. `unknown` is a syntactically valid platform value,
but it cannot authorize a source or participate in owned-account inference.

A second saved account receives an `inferred_owned_account` fact only when:

1. That account has also authorized local match history.
2. Its explicit platform and UID exactly match a participant.
3. The input declares the participant list complete.

Platform values are lowercase and exact; `pc` does not match `playstation` or
`xbox`. A UID match on the wrong platform is not enough. The source account's
fact is labeled `direct`.

Participant UIDs and names are used in memory for matching. The app persists
only normalized match facts for matching authorized accounts plus internal
provenance digests; unrelated participant identities and the raw input payload
are not stored.

Normalized match facts are stored in the separate local
`match-index.sqlite3` database as **plaintext SQLite**, not SQLCipher. Treat
that database and its backups as private local data. Account-password storage
remains separate from the match index.

## JSON format

The file must be UTF-8 JSON containing exactly these root fields:

| Field | Required | Type | Meaning |
|---|---:|---|---|
| `schema` | Yes | string | Must be exactly `mrat.matches.v1`. |
| `source` | Yes | object | Account through which these matches were observed. |
| `matches` | Yes | array | One or more match objects. |

`source` accepts only:

| Field | Required | Type | Rule |
|---|---:|---|---|
| `uid` | Yes | string or integer | 6-11 decimal digits. A quoted string is recommended. |
| `platform` | Yes | string | `pc`, `playstation`, or `xbox` for an importable source. |

Each object in `matches` accepts only:

| Field | Required | Type | Rule |
|---|---:|---|---|
| `match_id` | No | string | Provider/source match identifier. Recommended for stable deduplication. |
| `started_at` | Yes | string | ISO-8601 timestamp with a timezone, such as `2026-08-29T22:30:00Z`. |
| `season` | No | string | Season identifier or label. |
| `mode` | No | string | Match mode. |
| `map` | No | string | Map name. |
| `result` | No | string | Match-wide result. See the result note below. |
| `duration_seconds` | No | integer | From 0 through 86,400. |
| `participants_complete` | Yes | boolean | Must be the JSON boolean `true` for a file import. |
| `participants` | Yes | array | Non-empty complete participant list. |

Each participant accepts only:

| Field | Required | Type | Rule |
|---|---:|---|---|
| `uid` | Yes | string or integer | 6-11 decimal digits. |
| `platform` | Yes | string | `unknown`, `pc`, `playstation`, or `xbox`; use an explicit platform for matching. |
| `name` | No | string | Used transiently and not persisted. |
| `hero` | No | string | Stored only for a matching authorized account. |
| `rank` | No | string | Rank at match; stored only for a matching authorized account. |
| `stats` | No | object | Allowlisted non-negative integer statistics below. |

Allowed `stats` keys are `kills`, `deaths`, `assists`, `damage_dealt`,
`damage_taken`, and `healing_done`. No other statistic name is accepted.

`result` is match-wide in v1 and is copied to every stored fact for that match.
There is no participant-specific result field. If a result would mean something
different for participants on opposing sides, omit `result` rather than storing
an ambiguous value. Each stored owned-account fact keeps that participant's
platform. This permits one shared match to contain authorized accounts from
different platforms while preserving exact `(platform, UID)` matching.

### Valid JSON example

The UIDs below are synthetic. If both example identities are saved and
authorized, preview reports one direct fact and one inferred-owned fact.

```json
{
  "schema": "mrat.matches.v1",
  "source": {
    "uid": "123456789",
    "platform": "pc"
  },
  "matches": [
    {
      "match_id": "example-20260829-001",
      "started_at": "2026-08-29T22:30:00Z",
      "season": "season-4",
      "mode": "competitive",
      "map": "Tokyo 2099",
      "result": "win",
      "duration_seconds": 611,
      "participants_complete": true,
      "participants": [
        {
          "uid": "123456789",
          "platform": "pc",
          "hero": "Storm",
          "rank": "Diamond I",
          "stats": {
            "kills": 17,
            "deaths": 4,
            "assists": 9,
            "damage_dealt": 12345
          }
        },
        {
          "uid": "987654321",
          "platform": "xbox",
          "hero": "Loki",
          "stats": {
            "kills": 8,
            "deaths": 7,
            "assists": 22,
            "healing_done": 8000
          }
        }
      ]
    }
  ]
}
```

The source identity must appear exactly once in every match's participant
array. No `(platform, UID)` participant pair may appear twice in one match.

## CSV format

CSV is UTF-8 with one participant per row. Rows sharing a `match_id` are
grouped into one match. Every row must repeat the same schema and source
identity.

Required headers:

- `schema`
- `source_uid`
- `source_platform`
- `match_id`
- `started_at`
- `participants_complete`
- `participant_uid`
- `participant_platform`

Optional headers:

- `season`, `mode`, `map`, `result`, `duration_seconds`
- `participant_name`, `hero`, `rank`
- `stat_kills`, `stat_deaths`, `stat_assists`
- `stat_damage_dealt`, `stat_damage_taken`, `stat_healing_done`

Headers may be in any order, but duplicate or unknown headers are rejected.
`match_id` cannot be empty in CSV. All rows for one `match_id` must have
identical `started_at`, `season`, `mode`, `map`, `result`,
`duration_seconds`, and `participants_complete` values.

For CSV, `participants_complete` must be the exact lowercase text `true`.
Values such as `TRUE`, `1`, or `yes` do not assert completeness and cause that
match to be rejected.

### Valid CSV example

```csv
schema,source_uid,source_platform,match_id,started_at,season,mode,map,result,duration_seconds,participants_complete,participant_uid,participant_platform,hero,rank,stat_kills,stat_deaths,stat_assists
mrat.matches.v1,123456789,pc,example-20260829-001,2026-08-29T22:30:00Z,season-4,competitive,Tokyo 2099,win,611,true,123456789,pc,Storm,Diamond I,17,4,9
mrat.matches.v1,123456789,pc,example-20260829-001,2026-08-29T22:30:00Z,season-4,competitive,Tokyo 2099,win,611,true,987654321,xbox,Loki,,8,7,22
```

This example is equivalent to the JSON example for matching and evidence
classification. Empty optional CSV cells are treated as absent values.

## Manual entry

Manual entry in the account drawer creates one `direct` fact for the selected
source account. It is always normalized with `participants_complete: false`
and cannot infer a match for another account. A manual entry cannot claim a
complete lobby or provide multiple participants.

The same source requirements still apply: saved UID, explicit matching
platform, account-level match-history authorization, and per-batch ownership
attestation.

## Limits and validation

| Limit | Value |
|---|---:|
| JSON or CSV upload size | 8 MiB maximum |
| JSON matches | 20,000 maximum |
| CSV participant rows | 20,000 maximum |
| CSV grouped matches | 20,000 maximum |
| JSON nesting depth | 12 levels maximum |
| String, CSV value, or CSV header | 512 characters maximum |
| `season`; `result` | 80 characters maximum each |
| `mode`; participant `rank` | 100 characters maximum each |
| `map`; participant `hero` | 120 characters maximum each |
| Earliest timestamp | `2024-12-01T00:00:00Z` |
| Latest timestamp | Seven days after the current import time |
| UID | 6-11 decimal digits |
| Duration | 0-86,400 whole seconds |
| `kills`, `deaths`, `assists` | 0-1,000,000 each |
| Damage/healing statistics | 0-2,000,000,000 each |

Timestamps without a timezone are rejected. Statistic values must be whole,
non-negative integers; booleans and decimal values are not accepted. Unknown
JSON fields, unknown CSV columns, duplicate JSON keys, duplicate CSV headers,
mixed CSV source identities, and unsupported statistics are rejected.

Some invalid match records can appear as rejected rows alongside valid matches
in a partial preview. Review the preview's accepted, rejected, direct,
inferred, duplicate-input-match, and duplicate-owned-fact counts before
confirming. Match counts and owned-account fact counts are reported separately.
Structural problems such as
an invalid root schema or unauthorized source prevent the preview entirely. A
preview containing both valid and rejected matches reports `partial_import`;
one containing only rejected matches reports `rejected` and cannot commit any
facts. Upload, record-count, nesting, and string-limit violations are fatal for
the whole source rather than per-match rejections.

## Deduplication and conflicts

Preview calculates a SHA-256 digest of the exact file bytes. Reformatting a
JSON file or changing CSV line endings changes that source digest, even when
the normalized matches are equivalent. Confirmation must use the unchanged
file shown in preview.

Preview also returns a scope digest covering the exact authorized owned-account
facts and their new/duplicate/conflict dispositions. Confirmation recomputes
that scope while holding the vault write lock. If account authorization,
platform/UID identity, or relevant stored history changed after preview, the
commit is rejected and the app asks for a new preview instead of silently
adding or changing inferred facts.

Match identity is deterministic:

- When `match_id` is present, it defines the match identity within this schema.
- Without `match_id` in JSON, identity is derived from the timestamp, season,
  mode, map, duration, and sorted participant `(platform, UID)` pairs.

Within one preview, repeated match identities must have the same complete
normalized record, including transient participant fields. Identical records
are counted as duplicates; disagreeing records are rejected as a
`conflicting_match` instead of choosing one version.

At commit, persisted match metadata and owned-account facts are compared with
existing rows. Identical facts are duplicates. A reused match key or owned
account fact with different persisted content is a conflict, which aborts the
atomic commit; no partial database write from that commit is retained.

Names and source formatting do not define match identity. Hero, rank, result,
and statistics are normalized content. Names are transient but are included in
the normalized source-record digest, so two records with one identity but
different names conflict within the same preview. Reformatting alone changes
only the exact-file source digest, not the normalized record digest or match
key.
