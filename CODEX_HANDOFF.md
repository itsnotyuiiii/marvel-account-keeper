# Codex Continuation Handoff

This file intentionally travels with the continuation branch so the work can
be resumed from another computer without relying on local Codex session data.

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
- The branch was cut before the v2.15.1 release commit. Reconcile it with
  origin/main before any release, preserving APP_VERSION 2.16.0 for this
  development candidate unless the release plan changes explicitly.

First verification command:
python -m unittest tests.match_index_test tests.match_import_test tests.match_routes_test tests.stats_provider_test -v

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
