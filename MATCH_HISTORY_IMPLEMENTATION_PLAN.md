# Safe Match History Implementation Plan

Status: proposed, not yet implemented
Last reviewed: 2026-08-30

## Research result

RivalsData's public FAQ says its pipeline actively tracks match histories for
more than ten thousand players and reconstructs a private player's history from
matches already stored in its database. That confirms the general match-graph
model: a match observed through one profile can provide evidence that another
participant played it.

That finding does **not** authorize this application to copy RivalsData's
service. Its current privacy policy says API logs are used to diagnose issues,
prevent programmatic use, and monitor usage. No public developer API or terms
granting automated access were found. Therefore:

- Do not scrape RivalsData, call its private endpoints, bypass Cloudflare,
  automate a paid feature, or reuse its payloads.
- Do not claim that Tracker's `Private` collector result proves a user's game
  privacy setting. Tracker documents game/API sync problems that can produce
  incorrect privacy results.
- Do not build a searchable graph of unrelated players.

References:

- <https://rivalsdata.com/about>
- <https://rivalsdata.com/privacy>
- <https://feedback.tracker.gg/t/how-to-make-your-marvel-rivals-account-private-public/54772>

## Product boundary

The first implementation is a local, owner-authorized match index for accounts
already saved in this vault. It may infer a match for a second saved account
only when all of the following are true:

1. The user has marked both accounts as owned or authorized.
2. Both accounts have an explicit platform and Marvel Rivals UID.
3. An authorized input contains the complete participant list for a match.
4. A participant UID exactly matches the second saved account.
5. The import preview clearly labels the result as `inferred_owned_account`.

All unrelated participant names and UIDs are used only during in-memory
matching and discarded before the transaction commits. The application stores
only match facts for opted-in vault accounts. This preserves the useful part of
RivalsData's model without reconstructing or publishing strangers' private
histories.

Non-goals for the initial release:

- Looking up arbitrary private players.
- A public or shared player database.
- Background collection from undocumented endpoints.
- Live-game memory, packet, process, or anti-cheat interaction.
- Importing the removed v2.14 provider cache, whose authorization and
  provenance cannot be established.

## Phase 0: data and privacy contract

Estimated effort: 0.5-1 day.

- Add `rivals_platform` (`unknown`, `pc`, `playstation`, `xbox`) and an explicit
  `match_history_authorized` flag to each account.
- Define versioned `mrat.matches.v1` JSON and CSV formats.
- Require an ownership/authorization attestation for every import batch.
- Document that normalized match facts are stored locally in plaintext, like
  the app's current non-password rank metadata. SQLCipher is a separate
  packaging/security decision.
- Establish size limits before code is written: maximum upload bytes, record
  count, nesting depth, string length, and date range.

Exit gate: the schema, retained fields, discarded fields, and authorization
language are approved before any network adapter is considered.

## Phase 1: isolated SQLite match index

Estimated effort: 1.5-2.5 days.

Add `match_index.py` using Python's built-in `sqlite3` and a separate
`match-index.sqlite3` under the application data directory.

Suggested tables:

- `matches`: stable match key/fingerprint, timestamp, platform, season, mode,
  map, result, and duration. No participant identity columns.
- `account_match_facts`: `(account_id, match_key)`, owner hero/stats,
  rank-at-match, and evidence kind (`direct` or `inferred_owned_account`).
- `import_batches`: source kind, source digest, policy/schema version,
  authorization basis, timestamps, and accepted/rejected/duplicate counts.
- `match_provenance`: account-match row to batch, source-record digest, and
  source account ID when the fact was inferred.
- `match_source_state`: per-account/source last attempt, last success, stable
  error code, and retry time.

Database rules:

- One connection per operation; enable foreign keys, WAL, `busy_timeout`, and
  explicit transactions.
- Add a `_match_write_lock`. If an operation needs both locks, always acquire
  the vault lock before the match lock.
- Use sequential transactional migrations with `PRAGMA user_version`.
- Before schema migration or purge, create a consistent backup with
  `Connection.backup()`; never copy only the main database while WAL is live.
- A corrupt or unavailable match index must disable only match features. Vault
  access and rank refresh must continue to work.

Exit gate: migration, backup/restore, concurrency, corruption-isolation, and
storage-full tests pass independently of the vault tests.

## Phase 2: owner-authorized preview and import

Estimated effort: 1.5-3 days.

Add an allowlisted adapter interface in `match_sources.py`. Initial adapters:

1. Manual owner entry.
2. `mrat.matches.v1` JSON.
3. `mrat.matches.v1` CSV.
4. A NetEase privacy-export adapter only after validating a real,
   user-supplied sample.

Do not initially accept ZIP files or user-supplied URLs. A future HTTP adapter
requires published documentation, written access permission, authentication
rules, and rate limits.

Import is two-step:

1. `preview` parses in memory and returns source digest, identity/platform,
   date range, direct/inferred counts, duplicates, and rejection reasons. It
   persists nothing.
2. `commit` receives the same file plus the expected digest, reparses it, and
   inserts normalized rows in one transaction.

For records containing participants, compare normalized `(platform, UID)`
values only against opted-in vault accounts. Persist matches for exact owned
matches, then discard the raw participant collection and payload.

Exit gate: preview/commit equivalence, deterministic deduplication, UID and
platform mismatch rejection, and immediate third-party-data removal are all
covered by tests.

## Phase 3: routes and rank isolation

Estimated effort: 1-2 days.

All routes require an unlocked vault:

- `GET /api/accounts/<id>/matches`
- `GET /api/accounts/<id>/matches/status`
- `POST /api/accounts/<id>/matches/import/preview`
- `POST /api/accounts/<id>/matches/import`
- `DELETE /api/accounts/<id>/matches`

Rank refresh remains independent. Match operations must never modify
`last_refresh_*`, current/peak rank, Tracker cooldowns, or rate-limit state.
Match-source failures live only in `match_source_state`.

Stable HTTP behavior:

- `401`: vault locked only.
- `409`: authorization required/revoked or UID/platform change conflicts with
  existing history.
- `413`: upload/record limit exceeded.
- `422`: unsupported schema, identity mismatch, invalid record, or
  unauthorized row.
- `429`: authorized provider rate limit, with `Retry-After`.
- `503`: match index/provider unavailable.
- `507`: storage full.

Account deletion creates a match-index backup and purges that account's rows.
UID/platform changes require an explicit history purge first. Because JSON and
SQLite cannot share one atomic transaction, add failure-injection tests and a
startup orphan-reconciliation pass.

## Phase 4: UI

Estimated effort: 2-4 days.

- Rename `Refresh stats` to `Refresh ranks`.
- Add a separate Match History section in the account drawer.
- Keep rank and match-source state visually separate; do not overload the
  current rank sync chip.
- Add source selection, local-storage disclosure, ownership checkbox, file
  preview, direct/inferred labels, import summary, filters, and explicit clear
  history confirmation.
- Load match rows only when the history section opens; never add histories to
  the normal `/api/accounts` response.
- Update the frontend helper to leave `Content-Type` unset for `FormData` so
  the browser can supply the multipart boundary.

Exit gate: locked-vault behavior, keyboard/accessibility paths, large-history
rendering, and clear-history confirmation pass a packaged-app smoke test.

## Phase 5: controlled provider adapter

Estimated effort: 2-5 days after access is granted.

This phase is blocked until a provider supplies all of the following:

- Public API documentation and a permitted use statement.
- Credentials that belong to this application/user.
- Rate limits and retry rules.
- Sample responses and a versioning/change policy.
- Permission to retain the specific match and participant fields needed for
  owned-account inference.

RivalsData is not an eligible adapter under its current public information.
If it later grants access, implement it behind the same adapter interface and a
default-off feature flag. Never make it an implicit fallback for rank refresh.

## Verification and release order

Estimated effort: 2-3 days.

Add focused suites for database migrations/backups, import validation,
preview/commit equivalence, deduplication, concurrent access, route lock gates,
identity changes, deletion, corruption, and storage-full behavior. Include
regressions proving:

- A match-index failure never changes a successful rank state.
- Rank refresh works while the match database is unavailable.
- Refresh-all behavior is unchanged.
- No unrelated participant name/UID or raw payload remains after commit.
- A v2.15 vault upgrades without changing credentials or cached ranks.

Release in this order:

1. Internal temporary-data build and fixture migration.
2. Packaged local smoke test with a copied/synthetic vault.
3. Opt-in preview release with file import only.
4. Backup/restore and uninstall/rollback verification.
5. General release after observing no rank/vault regressions.

The first production release should start with an empty match index and require
an explicit authorized import. It should not attempt to recover deleted legacy
provider match data.
