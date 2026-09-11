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
   Revalidate the supported build after the September 11 update; do not simply
   replace hashes, guess version strings, or claim old pins describe the update.
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

## Current evidence

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
  implemented without installed-game or network reads. Only synthetic inputs
  have been tested; no production manifest or current-patch validation exists.
- A native UID-keyed current restriction getter was found. It checks login/chat/
  rank categories, not an established event history or adjudicated cause. See
  PUNISHMENT_RESEARCH.md. No reports or punishment queries have been submitted.

The goal stays active until the required integration is genuinely delivered or
a persistent external blocker meets the goal system's blocked-state criteria.
