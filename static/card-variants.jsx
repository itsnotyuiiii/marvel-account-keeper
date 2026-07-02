// Three views, sharing one chassis: Cards (refined trading-card),
// Table (data-dense list), Ladder (tier-grouped).
//
// Design rules (per user direction):
//  • Only rank tier colors carry chroma. All chrome is grayscale.
//  • Auto-derived semantic label per account:
//      pinned             → "MAIN ACCOUNT" (★)
//      peak = One Above All → "PEAKED OAA" (◆)
//      otherwise          → "ALT"          (—)
//  • Status dots (●) carry the tier color before CURRENT/PEAK labels.

const { tierOf, themeFor, fmtRelative, RANK_INDEX, RANK_TIERS } = window;

// Display string for a rank with its score. Two modes:
//   • absolute (default): "Celestial II · 4897 SR" — the raw API value.
//   • tier-relative (opts.tierRelative): "Celestial II · 47/100" — progress
//     within the current division. Marvel Rivals divisions step in 100-SR
//     blocks (Bronze III 0–99, Bronze II 100–199, …), so `points % 100`
//     is the live in-tier progress. Eternity and One Above All have no
//     sub-divisions and are open-ended, so we keep the raw value for them.
function rankDisplay(rank, points, opts) {
  if (!rank) return "—";
  if (points == null || points === "") return rank;
  if (opts && opts.tierRelative && rank !== "Eternity" && rank !== "One Above All") {
    const inTier = ((Number(points) % 100) + 100) % 100;
    return `${rank} · ${inTier}/100`;
  }
  // No "SR" suffix — the units are obvious from the context (the row label
  // is CURRENT / PEAK) and dropping the suffix keeps the value on one line in
  // the narrow card column even at the longest ranks ("Grandmaster I · 4704").
  return `${rank} · ${points}`;
}

// True when the vault entry's Steam username matches the currently signed-in
// Steam user on this PC (case-insensitive). Drives the "ACTIVE NOW" badge.
function isActiveSteamMatch(acct, activeSteam) {
  if (!activeSteam || !acct) return false;
  const u = (acct.username || "").trim().toLowerCase();
  const a = (activeSteam.account_name || "").trim().toLowerCase();
  return !!u && u === a;
}

// Inline "ACTIVE NOW" pill. Tiny, low-contrast, sits next to the IGN.
function ActiveSteamBadge({ activeSteam, compact }) {
  if (!activeSteam) return null;
  const title = `Signed in to Steam as ${activeSteam.persona_name || activeSteam.account_name}`;
  return (
    <span className={"active-steam-badge" + (compact ? " active-steam-badge-sm" : "")} title={title}>
      <span className="active-steam-dot" />
      <span className="active-steam-lbl">{compact ? "ACTIVE" : "ACTIVE NOW"}</span>
    </span>
  );
}

// True when this account's Marvel Rivals UID has a local config folder on
// this PC — i.e. someone has actually signed in to that account in the game
// here. Independent of Steam (works for NetEase-launcher logins too).
function isLocalRivalsMatch(acct, localRivalsUids) {
  if (!localRivalsUids || !acct) return false;
  const uid = (acct.rivals_uid || "").toString().trim();
  return !!uid && localRivalsUids.has(uid);
}

// Compact "on this PC" badge — Steam-style icon only, no label. Saves
// horizontal space on the card name row (the IGN + tag + presence all
// have to fit in one line).
function LocalRivalsBadge({ compact }) {
  return (
    <span className={"local-rivals-icon" + (compact ? " local-rivals-icon-sm" : "")}
          title="Signed in to Marvel Rivals on this PC"
          aria-label="on this PC">
      <svg viewBox="0 0 24 24" width={compact ? "12" : "13"} height={compact ? "12" : "13"} aria-hidden="true">
        <path fill="currentColor" d="M12 2a10 10 0 0 0-9.94 9.04l5.4 2.23a2.83 2.83 0 0 1 1.6-.5c.06 0 .12 0 .18.01l2.4-3.48v-.05a3.78 3.78 0 1 1 3.78 3.78h-.09l-3.43 2.45c0 .05.01.1.01.15a2.85 2.85 0 1 1-5.69.04l-3.87-1.6A10 10 0 1 0 12 2zm-3.18 13.16-1.23-.51a2.16 2.16 0 0 0 1.13 1.13 2.15 2.15 0 0 0 2.83-1.16 2.13 2.13 0 0 0-.01-1.64 2.16 2.16 0 0 0-1.16-1.16 2.16 2.16 0 0 0-1.6-.01l1.27.52a1.58 1.58 0 0 1-1.23 2.92zm9.94-5.43a2.52 2.52 0 1 1-2.52-2.52 2.52 2.52 0 0 1 2.52 2.52zm-4.41-.01a1.9 1.9 0 1 0 1.9-1.89 1.9 1.9 0 0 0-1.9 1.9z"/>
      </svg>
    </span>
  );
}

// Pick the single "presence" badge to render for a card. ACTIVE NOW wins
// when both signals fire — a live Steam session implies the account has
// been played here, but the converse isn't true, so the Steam badge is
// strictly more specific. One indicator per card keeps the layout calm.
function PresenceBadge({ acct, activeSteam, localRivalsUids, compact }) {
  if (isActiveSteamMatch(acct, activeSteam)) {
    return <ActiveSteamBadge activeSteam={activeSteam} compact={compact} />;
  }
  if (isLocalRivalsMatch(acct, localRivalsUids)) {
    return <LocalRivalsBadge compact={compact} />;
  }
  return null;
}

// "Not yet set up": no UID linked AND no usable IGN (or the IGN was tried
// and the API didn't recognize it). Drives a greyed-out card style so these
// entries fade into the background until the user finishes linking them.
function isIncomplete(acct) {
  if (!acct) return false;
  const uid = (acct.rivals_uid || "").toString().trim();
  const ign = (acct.in_game_name || "").trim();
  if (uid) return false;            // UID set → considered set up
  if (!ign) return true;            // no UID, no IGN → definitely not set up
  // IGN exists but lookups never resolved a UID — treat the typed name as
  // probably stale / wrong since the UID is the source of truth.
  return acct.last_refresh_status === "not_found"
      || acct.last_refresh_status === "missing_handle";
}

function labelFor(acct) {
  // Colors are theme tokens (see styles.css) so the labels stay legible in
  // both dark and light mode — gold "main", red "oaa", slate-blue "alt".
  if (acct.pinned) return { kind: "main", text: "MAIN ACCOUNT", color: "var(--label-main)" };
  if (acct.peak_rank === "One Above All") return { kind: "oaa", text: "PEAKED OAA", color: "var(--label-oaa)" };
  return { kind: "alt", text: "ALT", color: "var(--label-alt)" };
}

// Inline style for rank-value text. Carries both the bright tier color and its
// dark variant as CSS vars; --rank-ink (set per theme in styles.css) picks the
// legible one — bright on the dark theme, the deep "glow" tone on light.
function rankInk(t) {
  return { "--rk-fg": t && t.fg, "--rk-glow": t && t.glow };
}

// Map a stored color name -> hex for the tag-pill accent.
// Falls back to a neutral hue if the account has a tag but no color picked.
const TAG_COLORS = {
  red: "#ff5560", orange: "#ff9d2f", yellow: "#ffe14d",
  green: "#4ee07e", cyan: "#5be0ff", magenta: "#ff6ed4",
};
// Tag pills can carry their own color (acct.tag_color), independent of the neon
// border (acct.border_color). When no dedicated tag color is set we fall back to
// the border color so existing accounts look unchanged.
function tagColorFor(acct) {
  return TAG_COLORS[acct.tag_color] || TAG_COLORS[acct.border_color] || "#9aa3b2";
}
// A single account's `tag` field holds a comma-separated list — split it into
// individual labels so each renders as its own pill ("boost, items" -> 2 pills).
// Trim blanks and drop empties so trailing/double commas don't make ghost pills.
function splitTags(raw) {
  if (!raw) return [];
  return String(raw).split(",").map((s) => s.trim()).filter(Boolean);
}
function TagPill({ acct, size = "sm" }) {
  const tags = splitTags(acct.tag);
  if (!tags.length) return null;
  const c = tagColorFor(acct);
  return tags.map((tag, i) => (
    <span
      key={tag + "·" + i}
      className={"tag-pill tag-pill-" + size}
      style={{
        "--tag-fg": c,
        color: c,
        borderColor: "color-mix(in oklab, " + c + " 45%, transparent)",
        background: "color-mix(in oklab, " + c + " 12%, transparent)",
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <i className="tag-pill-dot" style={{ background: c }} />
      {tag}
    </span>
  ));
}

function Icon({ kind }) {
  if (kind === "main") {
    return (
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path d="M8 1.5 9.85 6 14.5 6.5 11 9.8 12 14.5 8 12.1 4 14.5 5 9.8 1.5 6.5 6.15 6Z"
          fill="currentColor" />
      </svg>
    );
  }
  if (kind === "oaa") {
    return (
      <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
        <path d="M8 1.5 14.5 8 8 14.5 1.5 8Z" fill="currentColor" />
      </svg>
    );
  }
  return null;
}

function chip({ label, value, field, onCopy, cls = "tk-chip" }) {
  const disabled = !value;
  return (
    <button
      key={field}
      type="button"
      className={cls}
      disabled={disabled}
      onClick={(e) => { e.stopPropagation(); if (value) onCopy(value, field); }}
    >{label}</button>
  );
}

// ── Per-account sync state ───────────────────────────────────────────────────
// Rank doesn't decay on its own — it only changes when the account plays
// ranked games. Freshness is keyed off last_refresh_ts (when WE last pulled),
// not the provider's internal crawl stamp (rivals_synced_at): tracker.gg's
// `lastUpdated` can lag hours behind the live data it serves, so keying off it
// made a just-refreshed account that changed rank still read "synced 13h ago".
// last_refresh_ts is unambiguous — it's when you hit refresh. An old value is
// not "dormant"/"stale" either: the rank we hold is still the rank they hold.
const RECRAWL_PENDING_MS = 30 * 60 * 1000;
// A pull this recent is as live as the 30-min recrawl gate allows — shown as
// "current" (green ✓) rather than just "synced".
const SYNC_CURRENT_MS = 30 * 60 * 1000;

// Account timestamps are epoch seconds; normalize anything seconds-scale to ms.
function toMs(ts) {
  if (ts == null) return null;
  return ts < 1e12 ? ts * 1000 : ts;
}

// Full date/time behind a relative "2h ago" label — used for hover tooltips.
function fmtAbsolute(ts) {
  const ms = toMs(ts);
  if (ms == null) return "";
  return new Date(ms).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  });
}

// Tooltip text for an account's "last edited" timestamp.
function updatedTitle(acct) {
  return acct.updated_at
    ? "Account last updated " + fmtAbsolute(acct.updated_at)
    : "Account not updated yet";
}

// Re-render on an interval so countdowns / relative times stay live. Pass
// active=false to park the timer when there is nothing counting down.
function useMinuteTick(active) {
  const [, force] = React.useState(0);
  React.useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => force((n) => n + 1), 30000);
    return () => clearInterval(id);
  }, [active]);
}

// Faster tick (1s) for short countdowns — the per-account refresh cooldown
// is 20s, so a 30s minute tick wouldn't update the tooltip until after it
// already expired.
function useSecondTick(active) {
  const [, force] = React.useState(0);
  React.useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
}

// Collapse an account's refresh fields into one state token. Drives both the
// SyncChip and the refresh-button badge so they always agree.
//   none → never refreshed   current → ok & pulled within the last 30 min
//   fresh → ok, any older pull (no "dormant"/"stale" — see note above)
//   private / not_found / bad_key / error / missing_handle → see SYNC_META
// There is intentionally no age-based "dormant" state: refresh age is not a
// last-played signal, so an old pull stays a clean green "synced".
function syncState(acct) {
  const st = acct.last_refresh_status;
  if (!st) return "none";
  if (st !== "ok") return st;
  const synced = toMs(acct.last_refresh_ts);
  if (synced == null) return "fresh";
  const age = Date.now() - synced;
  return age <= SYNC_CURRENT_MS ? "current" : "fresh";
}

// DISPLAY-ONLY caveat. tracker.gg reported the profile private on the last
// refresh, yet we're still showing a (cached) marvelrivalsapi rank — flag it so
// live-looking data isn't mistaken for current. Mirrors app.py's `tracker_private`;
// only applies while the sync state is an ok variant (it never overrides a real
// private/error status, which already speaks for itself).
function trackerPrivateCaveat(acct) {
  if (!acct.tracker_private) return false;
  const s = syncState(acct);
  return s === "current" || s === "fresh";
}
const TRACKER_PRIVATE_NOTE = "tracker.gg shows this profile private — rank may be cached";

// DISPLAY-ONLY caveat. tracker served public ranks but the profile's match
// history is private, so even an explicit "X ago" crawl label could be mistaken
// for activity — make it explicit that last-played is unknowable here.
// Mirrors app.py's `tracker_history_private`. Only applies on an ok state.
function historyPrivateCaveat(acct) {
  if (!acct.tracker_history_private) return false;
  const s = syncState(acct);
  return s === "current" || s === "fresh";
}
const HISTORY_PRIVATE_NOTE =
  "Match history is private — ranks shown are the latest tracker.gg has, "
  + "but the account's last-played time can't be determined.";

// Icon + copy for each non-clean sync state. 'current' / 'fresh' / 'none'
// render no badge on the refresh button — a clean (or simply old but
// uncontested) account needs no marker. tracker.gg is the primary data source;
// marvelrivalsapi is the fallback. Copy reflects that.
// Short labels for chip body — the chip is clamped to a single line so it
// can't push the rank rows around. The full long-form message is also passed
// through to the chip's `title` attribute as a native hover tooltip.
const SYNC_META = {
  private:        { sym: "🔒", cls: "warn",  text: "Private profile — set rank manually",
                    long: "Both tracker.gg and marvelrivalsapi reported this profile as private — set rank manually" },
  not_found:      { sym: "?",  cls: "muted", text: "Player not found — check IGN",
                    long: "No data for this player on tracker.gg or marvelrivalsapi — IGN may be wrong" },
  bad_key:        { sym: "!",  cls: "err",   text: "marvelrivalsapi key rejected",
                    long: "marvelrivalsapi key rejected (fallback only) — tracker.gg should still work" },
  missing_handle: { sym: "?",  cls: "muted", text: "No IGN set — refresh skipped",
                    long: "No in-game name set — refresh skipped" },
  error:          { sym: "!",  cls: "err",   text: "Refresh failed — retry shortly",
                    long: "Last refresh failed — retry shortly" },
};

// Short human label for the data source field on each account.
const SOURCE_LABEL = {
  tracker:         "tracker.gg",
  marvelrivalsapi: "marvelrivalsapi.com",
};

// How long (ms) the server enforces between refreshes of the same account.
// Mirrors PER_ACCOUNT_REFRESH_COOLDOWN_S in app.py — keep in sync.
const PER_ACCOUNT_COOLDOWN_MS = 20 * 1000;

// Seconds remaining in the per-account refresh cooldown, or 0 when refresh
// is allowed right now.
function refreshCooldownLeft(acct) {
  const ts = toMs(acct.last_refresh_ts);
  if (ts == null) return 0;
  const left = PER_ACCOUNT_COOLDOWN_MS - (Date.now() - ts);
  return left > 0 ? Math.ceil(left / 1000) : 0;
}

// Inline refresh button. Cells/cards/rows all share this so the spinner +
// status-indicator placement stays consistent. tracker.gg is unauthenticated
// so the button renders even without a marvelrivalsapi key in Options.
function RefreshBtn({ acct, refreshing, onRefresh, hasApiKey,
                     size = "sm", showLabel = false }) {
  const state = syncState(acct);
  let meta = SYNC_META[state] || null;  // null for none / fresh
  // ok-state account that tracker.gg now flags private: badge it amber 🔒 so
  // the cached rank reads as suspect at a glance. Display-only — `state` (and
  // the underlying last_refresh_status) are unchanged.
  if (!meta && trackerPrivateCaveat(acct)) {
    meta = { sym: "🔒", cls: "warn", text: TRACKER_PRIVATE_NOTE };
  }
  const cooldownLeft = refreshCooldownLeft(acct);
  // 1s tick while the per-account cooldown is counting down so the tooltip
  // updates live and the button re-enables the moment it expires.
  useSecondTick(cooldownLeft > 0);

  let title;
  if (cooldownLeft > 0) {
    title = `Just refreshed — wait ${cooldownLeft}s before another pull`;
  } else if (meta) {
    title = acct.last_refresh_error || meta.text;
  } else {
    title = "Refresh rank — tries tracker.gg first, marvelrivalsapi as fallback";
  }
  const blocked = refreshing || cooldownLeft > 0;
  return (
    <button
      type="button"
      className={"refresh-btn refresh-btn-" + size
                 + (refreshing ? " is-busy" : "")
                 + (cooldownLeft > 0 ? " is-cooldown" : "")
                 + (meta ? " has-issue refresh-btn-" + meta.cls : "")}
      data-status={state}
      onClick={(e) => { e.stopPropagation(); if (!blocked) onRefresh && onRefresh(acct); }}
      disabled={blocked}
      aria-label={title}
      title={title}
    >
      <svg viewBox="0 0 16 16" width="13" height="13"
           className={"refresh-btn-svg" + (refreshing ? " is-spinning" : "")}
           aria-hidden="true">
        <path d="M2.5 8a5.5 5.5 0 0 1 9.9-3.3M13.5 8a5.5 5.5 0 0 1-9.9 3.3"
              fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        <path d="M12.4 1.6v3.1H9.3" fill="none" stroke="currentColor"
              strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M3.6 14.4v-3.1h3.1" fill="none" stroke="currentColor"
              strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {meta && <span className="refresh-btn-issue" aria-hidden="true">{meta.sym}</span>}
      {showLabel && <span className="refresh-btn-lbl">{refreshing ? "Refreshing…" : "Refresh"}</span>}
    </button>
  );
}

// Inline sync notifier: current / fresh / private / not-found / failed.
// This is the per-account counterpart to the API-wide status in the Options
// panel. When a recrawl is in flight it also shows a live countdown to when
// fresh data should land (and when the next recrawl can be requested).
function SyncChip({ acct, hasApiKey }) {
  const state = syncState(acct);

  // "Synced Xago" = when WE last pulled (last_refresh_ts), not the provider's
  // internal crawl stamp. tracker.gg's `lastUpdated` (rivals_synced_at) can lag
  // hours behind the live data it serves, so a fresh pull that changed the rank
  // would otherwise still read "synced 13h ago". providerCrawl is kept only for
  // the marvelrivalsapi cached-data note below.
  const synced = toMs(acct.last_refresh_ts);
  const providerCrawl = toMs(acct.rivals_synced_at);
  const reqAt = toMs(acct.rivals_update_requested_at);
  const lastRefreshTs = toMs(acct.last_refresh_ts);
  // marvelrivalsapi locks a player for 30 min after an /update — that same
  // window is both "recrawl in progress" and "can't request another yet".
  const recrawlLeft = reqAt != null ? RECRAWL_PENDING_MS - (Date.now() - reqAt) : -1;
  const recrawlPending = recrawlLeft > 0;
  // Window elapsed and we haven't refreshed since asking for it: the recrawled
  // data is now sitting on the API, but the card still shows the pre-recrawl
  // rank until the next refresh pulls it in. Nothing auto-fetches.
  const recrawlReady = reqAt != null && !recrawlPending
    && (lastRefreshTs == null || lastRefreshTs <= reqAt);
  useMinuteTick(recrawlPending);

  if (state === "none") {
    return hasApiKey ? (
      <div className="sync-chip sync-chip-muted" data-state="none">
        <span className="sync-chip-i" aria-hidden="true">○</span>
        <span className="sync-chip-t">Not synced yet — hit ↻ to pull the live rank</span>
      </div>
    ) : null;
  }

  const srcLabel = SOURCE_LABEL[acct.last_refresh_source] || null;
  const srcSuffix = srcLabel ? ` · ${srcLabel}` : "";
  let cls, icon, text, longText;
  if (state === "current") {
    cls = "ok";
    icon = "✓";
    text = synced ? `Rank current — synced ${fmtRelative(synced)}${srcSuffix}`
                  : `Rank current${srcSuffix}`;
  } else if (state === "fresh") {
    cls = "ok";
    icon = "●";
    text = synced ? `Rank synced ${fmtRelative(synced)}${srcSuffix}`
                  : `Rank synced${srcSuffix}`;
  } else {
    const m = SYNC_META[state] || SYNC_META.error;
    cls = m.cls;
    icon = m.sym;
    text = m.text;
    longText = m.long || m.text;
  }

  // Display-only caveat (see trackerPrivateCaveat): we're on an ok state but
  // tracker.gg says private. Recolor amber + 🔒 and append the note, without
  // touching syncState / last_refresh_status.
  if (trackerPrivateCaveat(acct)) {
    cls = "warn";
    icon = "🔒";
    text = `${text} · ${TRACKER_PRIVATE_NOTE}`;
    longText = providerCrawl
      ? `${TRACKER_PRIVATE_NOTE}. Showing the last marvelrivalsapi crawl from ${fmtRelative(providerCrawl)}.`
      : `${TRACKER_PRIVATE_NOTE}.`;
  }

  // History-private (see historyPrivateCaveat): ranks are valid/current but the
  // crawl age is NOT a last-played signal. Stay green and say plainly that
  // activity is hidden, not stale — never imply last-played from the crawl age.
  if (historyPrivateCaveat(acct)) {
    cls = "ok";
    icon = "🛡";
    text = `Latest ranks · match history private${srcSuffix}`;
    longText = HISTORY_PRIVATE_NOTE;
  }

  const recrawlMins = recrawlPending ? Math.max(1, Math.ceil(recrawlLeft / 60000)) : 0;
  // Recrawl is a marvelrivalsapi-only mechanic. Suppress the verbose
  // "recrawl queued / done" suffixes when:
  //   1. the account has any ok-status sync (current/fresh) — the data is
  //      good, the recrawl is moot, and the chip is just noise.
  //   2. tracker.gg sourced the latest refresh — recrawl wouldn't help
  //      (tracker pulls live every call, no caching layer to thaw).
  const showRecrawl = state !== "current"
                   && state !== "fresh"
                   && acct.last_refresh_source !== "tracker";
  return (
    <div className={"sync-chip sync-chip-" + cls} data-state={state}
         title={acct.last_refresh_error || longText || text}>
      <span className="sync-chip-i" aria-hidden="true">{icon}</span>
      <span className="sync-chip-t">{text}</span>
      {showRecrawl && recrawlPending && (
        <span className="sync-chip-pending"
              title={"Recrawl queued on marvelrivalsapi (fallback only) — "
                     + "their backend is re-fetching this player's live stats. "
                     + "Refresh again in a few minutes to pull the updated rank. "
                     + "Another recrawl can't be queued for ~"
                     + recrawlMins + " min. tracker.gg pulls live data on every "
                     + "refresh, so no recrawl is needed there."}>
          · recrawl queued · ~{recrawlMins}m
        </span>
      )}
      {showRecrawl && recrawlReady && (
        <span className="sync-chip-ready"
              title={"The 30-min marvelrivalsapi recrawl window has elapsed. "
                     + "Hit ↻ to pull the recrawled rank — relevant only if "
                     + "this account falls back to marvelrivalsapi."}>
          · recrawl done — hit ↻
        </span>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// B. CARDS  (refined trading-card)
// ─────────────────────────────────────────────────────────────────────────────
function CardRefined({ acct, opts, onOpen, onCopy, onPin, onRefresh, refreshing, hasApiKey, activeSteam, localRivalsUids }) {
  const cur = themeFor(acct.current_rank);
  const peak = themeFor(acct.peak_rank);
  const lab = labelFor(acct);

  return (
    <article
      className={"rcard rcard-" + lab.kind + (acct.neon ? " rcard-neon" : "")
                 + (isIncomplete(acct) ? " is-incomplete" : "")}
      style={{
        "--tier-fg":   cur?.fg   || "#9aa3b2",
        "--tier-glow": cur?.glow || "#1a1d24",
        "--label-fg":  lab.color || "var(--muted)",
        "--neon-fg":   acct.neon ? (TAG_COLORS[acct.border_color] || cur?.fg || "#9aa3b2") : "transparent",
      }}
      tabIndex={0}
      aria-label={"Open " + (acct.in_game_name || "account")}
      onClick={() => onOpen(acct)}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(acct); }
      }}
    >
      <div className="rcard-wash" aria-hidden="true" />

      <header className="rcard-head">
        <span className="rcard-eyebrow">{lab.text}</span>
        <div className="rcard-head-r">
          <RefreshBtn acct={acct} refreshing={refreshing}
                      onRefresh={onRefresh} hasApiKey={hasApiKey} size="sm" />
          <button
            type="button"
            className={"rcard-mark" + (lab.kind === "alt" ? " rcard-mark-alt" : "")}
            onClick={(e) => { e.stopPropagation(); onPin(acct); }}
            aria-label={acct.pinned ? "Unpin" : (lab.kind === "alt" ? "Pin as main" : lab.text)}
            title={acct.pinned ? "Unpin" : "Pin as main"}
          >
            {lab.kind === "alt"
              ? <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                  <path d="M8 1.5 9.85 6 14.5 6.5 11 9.8 12 14.5 8 12.1 4 14.5 5 9.8 1.5 6.5 6.15 6Z"
                    fill="none" stroke="currentColor" strokeWidth="1.2" />
                </svg>
              : <Icon kind={lab.kind} />}
          </button>
        </div>
      </header>

      <div className="rcard-name-row">
        <h3 className={"rcard-ign" + (acct.in_game_name ? "" : " rcard-ign-empty")}>
          {acct.in_game_name || "No IGN set"}
        </h3>
        <TagPill acct={acct} />
      </div>

      {!opts.hideDetails && (
        <div className="rcard-meta">
          <span className="rcard-meta-line">{acct.username || "—"}</span>
          {acct.email && <span className="rcard-meta-line rcard-trunc">{acct.email}</span>}
        </div>
      )}

      <div className="rcard-ranks">
        <div className="rcard-rank">
          <span className="rcard-rdot" style={{ background: cur?.fg }} />
          <span className="rcard-rlbl">CURRENT</span>
          <span className="rcard-rval" style={rankInk(cur)}
                title={rankDisplay(acct.current_rank, acct.current_points, { tierRelative: opts.srTierRelative })}>
            {rankDisplay(acct.current_rank, acct.current_points, { tierRelative: opts.srTierRelative })}
          </span>
        </div>
        <div className="rcard-rank">
          <span className="rcard-rdot" style={{ background: peak?.fg }} />
          <span className="rcard-rlbl">PEAK</span>
          <span className="rcard-rval" style={rankInk(peak)}
                title={rankDisplay(acct.peak_rank, acct.peak_points, { tierRelative: opts.srTierRelative })}>
            {rankDisplay(acct.peak_rank, acct.peak_points, { tierRelative: opts.srTierRelative })}
          </span>
        </div>
      </div>

      <SyncChip acct={acct} hasApiKey={hasApiKey} />

      {!opts.hideCopy && (
        <footer className="rcard-foot">
          <span className="rcard-time" title={updatedTitle(acct)}>
            {fmtRelative(acct.updated_at)}
          </span>
          <div className="rcard-copy">
            <span className="rcard-copy-lbl">copy</span>
            {chip({ label: "user",  value: acct.username, field: "username", onCopy })}
            {chip({ label: "email", value: acct.email,    field: "email",    onCopy })}
            {chip({ label: "pw",    value: acct.password, field: "password", onCopy })}
          </div>
        </footer>
      )}
    </article>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// C. TABLE  (data-dense list)
// ─────────────────────────────────────────────────────────────────────────────
function TableView({ accounts, opts, onOpen, onCopy, onPin, onRefresh, refreshingIds, hasApiKey, sortLabel, activeSteam, localRivalsUids }) {
  return (
    <section className="tbl">
      <header className="tbl-head">
        <div className="tbl-title">Accounts</div>
        <div className="tbl-meta">{accounts.length} · sorted by {sortLabel}</div>
      </header>
      <div className="tbl-cols">
        <span className="tbl-col tbl-col-pin"></span>
        <span className="tbl-col tbl-col-ign">IGN</span>
        <span className="tbl-col tbl-col-cur">CURRENT</span>
        <span className="tbl-col tbl-col-peak">PEAK</span>
        <span className="tbl-col tbl-col-time">EDITED</span>
        <span className="tbl-col tbl-col-actions"></span>
      </div>
      <ol className="tbl-rows">
        {accounts.map((a) => (
          <TableRow key={a.id} acct={a} opts={opts}
                    onOpen={onOpen} onCopy={onCopy} onPin={onPin}
                    onRefresh={onRefresh}
                    refreshing={refreshingIds && refreshingIds.has(a.id)}
                    hasApiKey={hasApiKey}
                    activeSteam={activeSteam} localRivalsUids={localRivalsUids} />
        ))}
      </ol>
    </section>
  );
}

function TableRow({ acct, opts, onOpen, onCopy, onPin, onRefresh, refreshing, hasApiKey, activeSteam, localRivalsUids }) {
  const cur = themeFor(acct.current_rank);
  const peak = themeFor(acct.peak_rank);
  const lab = labelFor(acct);
  const [open, setOpen] = React.useState(false);

  return (
    <li
      className={"tbl-row" + (open ? " is-open" : "") + (isIncomplete(acct) ? " is-incomplete" : "")}
      data-label={lab.kind}
    >
      <div className="tbl-row-main"
           tabIndex={0}
           aria-label={(open ? "Collapse " : "Expand ") + (acct.in_game_name || "account") + " details"}
           onClick={() => setOpen((p) => !p)}
           onKeyDown={(e) => {
             if (e.target !== e.currentTarget) return;
             if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen((p) => !p); }
           }}>
        <button
          type="button"
          className="tbl-pin"
          onClick={(e) => { e.stopPropagation(); onPin(acct); }}
          aria-label={acct.pinned ? "Unpin" : "Pin as main"}
          style={{ color: lab.color || "var(--muted-2)" }}
        >
          {lab.kind === "alt"
            ? <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                <path d="M8 1.5 9.85 6 14.5 6.5 11 9.8 12 14.5 8 12.1 4 14.5 5 9.8 1.5 6.5 6.15 6Z"
                  fill="none" stroke="currentColor" strokeWidth="1.2" opacity=".35" />
              </svg>
            : <Icon kind={lab.kind} />}
        </button>

        <div className="tbl-ign-cell">
          <span className={"tbl-ign" + (acct.in_game_name ? "" : " tbl-ign-empty")}>
            {acct.in_game_name || "No IGN set"}
          </span>
          {lab.kind === "main" && <span className="tbl-tag">main</span>}
          {lab.kind === "oaa"  && <span className="tbl-tag tbl-tag-oaa">peak oaa</span>}
          <TagPill acct={acct} />
        </div>

        <div className="tbl-rank-cell">
          <span className="tbl-rdot" style={{ background: cur?.fg }} />
          <span style={rankInk(cur)}>{rankDisplay(acct.current_rank, acct.current_points, { tierRelative: opts.srTierRelative })}</span>
        </div>

        <div className="tbl-rank-cell">
          <span className="tbl-rdot" style={{ background: peak?.fg }} />
          <span style={rankInk(peak)}>{rankDisplay(acct.peak_rank, acct.peak_points, { tierRelative: opts.srTierRelative })}</span>
        </div>

        <div className="tbl-time" title={updatedTitle(acct)}>{fmtRelative(acct.updated_at)}</div>

        <div className="tbl-actions">
          <RefreshBtn acct={acct} refreshing={refreshing}
                      onRefresh={onRefresh} hasApiKey={hasApiKey} size="sm" />
          <button
            type="button"
            className="tbl-more"
            onClick={(e) => { e.stopPropagation(); onOpen(acct); }}
            aria-label="Edit"
            title="Edit"
          >···</button>
        </div>
      </div>

      {open && (
        <div className="tbl-row-detail">
          <SyncChip acct={acct} hasApiKey={hasApiKey} />
          {!opts.hideDetails && (
            <div className="tbl-detail-meta">
              <span><i>steam</i> {acct.username || "—"}</span>
              <span><i>email</i> {acct.email || "—"}</span>
              {acct.notes && <span><i>notes</i> {acct.notes}</span>}
            </div>
          )}
          {!opts.hideCopy && (
            <div className="tbl-detail-copy">
              {chip({ label: "Copy username", value: acct.username, field: "username", onCopy, cls: "tk-chip" })}
              {chip({ label: "Copy email",    value: acct.email,    field: "email",    onCopy, cls: "tk-chip" })}
              {chip({ label: "Copy password", value: acct.password, field: "password", onCopy, cls: "tk-chip" })}
              <button className="tk-chip tk-chip-go"
                      onClick={(e) => { e.stopPropagation(); onOpen(acct); }}>Edit →</button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// D. LADDER  (tier-grouped)
// ─────────────────────────────────────────────────────────────────────────────
const TIER_ORDER = [
  "One Above All", "Eternity",
  "Celestial", "Grandmaster", "Diamond", "Platinum", "Gold", "Silver", "Bronze",
];

// Static SR checkpoints. tracker.gg / in-game "SR" is the official RP scale
// (Bronze III = 0 … Celestial I = 2000, +100 per division) shifted by +3000,
// so Grandmaster I lands at 4700. Derived from RANK_TIERS by index — the floor
// to ENTER each division. Eternity/One Above All are point-based (no division
// floor), so they're shown as open-ended.
const SR_BASE = 3000;
const RANK_CHECKPOINTS = RANK_TIERS.map((rank, i) => {
  let sr;
  if (rank === "One Above All") sr = "Top 500";
  else if (rank === "Eternity") sr = (SR_BASE + i * 100) + "+";
  else sr = String(SR_BASE + i * 100);
  return { rank, sr, fg: (themeFor(rank) || {}).fg || "var(--muted)" };
});

function RankCheckpoints() {
  return (
    <details className="lad-ref">
      <summary className="lad-ref-sum">
        <span className="lad-ref-title">Rank checkpoints</span>
        <span className="lad-ref-hint">SR to enter each tier</span>
      </summary>
      <div className="lad-ref-grid">
        {RANK_CHECKPOINTS.map(({ rank, sr, fg }) => (
          <div className="lad-ref-row" key={rank} style={{ "--tier-fg": fg }}>
            <span className="lad-ref-dot" aria-hidden="true" />
            <span className="lad-ref-rank">{rank}</span>
            <span className="lad-ref-sr">{sr}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

function LadderView({ accounts, opts, onOpen, onCopy, onPin, onRefresh, refreshingIds, hasApiKey, activeSteam, localRivalsUids, rankField }) {
  // Group by tier of the chosen rank field (current_rank or peak_rank).
  const field = rankField === "peak_rank" ? "peak_rank" : "current_rank";
  const groups = React.useMemo(() => {
    const m = new Map();
    for (const t of TIER_ORDER) m.set(t, []);
    m.set("Unranked", []);
    for (const a of accounts) {
      const t = tierOf(a[field]) || "Unranked";
      if (!m.has(t)) m.set(t, []);
      m.get(t).push(a);
    }
    return [...m.entries()].filter(([_, list]) => list.length > 0);
  }, [accounts, field]);

  return (
    <div className="ladder">
      <RankCheckpoints />
      {groups.map(([tier, list]) => (
        <LadderGroup key={tier} tier={tier} list={list} rankField={field}
                     opts={opts} onOpen={onOpen} onCopy={onCopy} onPin={onPin}
                     onRefresh={onRefresh} refreshingIds={refreshingIds} hasApiKey={hasApiKey}
                     activeSteam={activeSteam} localRivalsUids={localRivalsUids} />
      ))}
    </div>
  );
}

function LadderGroup({ tier, list, rankField, opts, onOpen, onCopy, onPin, onRefresh, refreshingIds, hasApiKey, activeSteam, localRivalsUids }) {
  const t = themeFor(tier === "One Above All" ? "One Above All" : tier + " I") || { fg: "#9aa3b2" };
  return (
    <section className="ladder-group" style={{ "--tier-fg": t.fg }}>
      <header className="ladder-head">
        <span className="ladder-tier">{tier.toUpperCase()}</span>
        <span className="ladder-rule" aria-hidden="true" />
        <span className="ladder-count">{list.length}</span>
      </header>
      <div className="ladder-grid">
        {list.map((a) => (
          <LadderCard key={a.id} acct={a} rankField={rankField}
                      opts={opts} onOpen={onOpen} onCopy={onCopy} onPin={onPin}
                      onRefresh={onRefresh}
                      refreshing={refreshingIds && refreshingIds.has(a.id)}
                      hasApiKey={hasApiKey}
                      activeSteam={activeSteam} localRivalsUids={localRivalsUids} />
        ))}
      </div>
    </section>
  );
}

function LadderCard({ acct, opts, onOpen, onPin, onRefresh, refreshing, hasApiKey, activeSteam, localRivalsUids, rankField }) {
  // Ladder can be grouped by current or peak rank; the chosen field is the
  // card's primary line, the other becomes the small sub-line.
  const byPeak = rankField === "peak_rank";
  const primaryRank   = byPeak ? acct.peak_rank   : acct.current_rank;
  const primaryPoints = byPeak ? acct.peak_points : acct.current_points;
  const secRank   = byPeak ? acct.current_rank   : acct.peak_rank;
  const secPoints = byPeak ? acct.current_points : acct.peak_points;
  const secLabel  = byPeak ? "now" : "peak";
  const primaryTheme = themeFor(primaryRank);
  const secTheme = themeFor(secRank);
  const lab = labelFor(acct);
  const division = (() => {
    if (!primaryRank) return "";
    if (primaryRank === "Eternity" || primaryRank === "One Above All") {
      return primaryPoints ? String(primaryPoints) : "";
    }
    return primaryRank.split(" ")[1] || ""; // I / II / III
  })();

  return (
    <article
      className={"lad-card lad-card-" + lab.kind + (isIncomplete(acct) ? " is-incomplete" : "")}
      style={{ "--tier-fg": primaryTheme?.fg || "var(--muted)" }}
      tabIndex={0}
      aria-label={"Open " + (acct.in_game_name || "account")}
      onClick={() => onOpen(acct)}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(acct); }
      }}
    >
      <div className="lad-line">
        <span className={"lad-ign" + (acct.in_game_name ? "" : " lad-ign-empty")}>
          {acct.in_game_name || "No IGN set"}
        </span>
        <div className="lad-line-r">
          <TagPill acct={acct} />
          <RefreshBtn acct={acct} refreshing={refreshing}
                      onRefresh={onRefresh} hasApiKey={hasApiKey} size="xs" />
          {lab.kind !== "alt" && (
            <span className="lad-icon" style={{ color: lab.color || "currentColor" }}>
              <Icon kind={lab.kind} />
            </span>
          )}
        </div>
      </div>
      <div className="lad-sub">
        {division && <span className="lad-div">{division}</span>}
        {division && <span className="lad-sep">·</span>}
        <span className="lad-peak-lbl">{secLabel}</span>
        <span className="lad-peak-val" style={rankInk(secTheme)}>
          {rankDisplay(secRank, secPoints, { tierRelative: opts.srTierRelative })}
        </span>
      </div>
    </article>
  );
}

Object.assign(window, {
  CardRefined, TableView, LadderView, labelFor, PresenceBadge,
  splitTags, tagColorFor,
});
