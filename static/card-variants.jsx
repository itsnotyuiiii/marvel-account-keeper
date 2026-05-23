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

// Display string for a rank with its absolute MMR/SR score. Marvel Rivals'
// ranked system uses absolute points across every tier, so the score reads
// the same for Bronze through One Above All — e.g. "Celestial II · 4897 SR".
function rankDisplay(rank, points) {
  if (!rank) return "—";
  if (points != null && points !== "") return `${rank} · ${points} SR`;
  return rank;
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
  return { "--rk-fg": t && t.fg, "--rk-glow": t && t.glow, color: "var(--rank-ink)" };
}

// Map a stored border_color name -> hex for the tag-pill accent.
// Falls back to a neutral hue if the account has a tag but no color picked.
const TAG_COLORS = {
  red: "#ff5560", orange: "#ff9d2f", yellow: "#ffe14d",
  green: "#4ee07e", cyan: "#5be0ff", magenta: "#ff6ed4",
};
function tagColorFor(acct) {
  return TAG_COLORS[acct.border_color] || "#9aa3b2";
}
function TagPill({ acct, size = "sm" }) {
  if (!acct.tag) return null;
  const c = tagColorFor(acct);
  return (
    <span
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
      {acct.tag}
    </span>
  );
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
// marvelrivalsapi data older than this is shown as "stale" — the rank may no
// longer be current. Server-side recrawls are gated to ~30 min, so anything
// inside a day counts as fresh.
const SYNC_STALE_AFTER_MS = 24 * 3600 * 1000;
const RECRAWL_PENDING_MS = 30 * 60 * 1000;
// A crawl this recent means the data is as live as the 30-min recrawl gate
// allows — shown as "current" (green ✓) rather than just "synced".
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

// Collapse an account's refresh fields into one state token. Drives both the
// SyncChip and the refresh-button badge so they always agree.
//   none → never refreshed       current → ok & crawled within the last 30 min
//   fresh → ok & under a day old  stale → ok but crawl is over a day old
//   private / not_found / bad_key / error / missing_handle → see SYNC_META
function syncState(acct) {
  const st = acct.last_refresh_status;
  if (!st) return "none";
  if (st !== "ok") return st;
  const synced = toMs(acct.rivals_synced_at);
  if (synced == null) return "fresh";
  const age = Date.now() - synced;
  if (age > SYNC_STALE_AFTER_MS) return "stale";
  return age <= SYNC_CURRENT_MS ? "current" : "fresh";
}

// Icon + copy for each non-clean sync state. 'fresh'/'none' render no badge on
// the refresh button — a clean account needs no marker.
const SYNC_META = {
  stale:          { sym: "▲",  cls: "warn",  text: "API rank data is stale — refresh to recrawl" },
  private:        { sym: "🔒", cls: "warn",  text: "Profile private — set rank manually" },
  not_found:      { sym: "?",  cls: "muted", text: "No data for this player on marvelrivalsapi" },
  bad_key:        { sym: "!",  cls: "err",   text: "API key rejected — check Options" },
  missing_handle: { sym: "?",  cls: "muted", text: "No in-game name set — refresh skipped" },
  error:          { sym: "!",  cls: "err",   text: "Last refresh failed — retry later" },
};

// Inline refresh button. Cells/cards/rows all share this so the spinner +
// status-indicator placement stays consistent.
function RefreshBtn({ acct, refreshing, onRefresh, hasApiKey,
                     size = "sm", showLabel = false }) {
  if (!hasApiKey) return null;
  const state = syncState(acct);
  const meta = SYNC_META[state] || null;  // null for none / fresh
  const title = meta ? (acct.last_refresh_error || meta.text) : "Refresh rank from marvelrivalsapi.com";
  return (
    <button
      type="button"
      className={"refresh-btn refresh-btn-" + size
                 + (refreshing ? " is-busy" : "")
                 + (meta ? " has-issue refresh-btn-" + meta.cls : "")}
      data-status={state}
      onClick={(e) => { e.stopPropagation(); onRefresh && onRefresh(acct); }}
      disabled={refreshing}
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

// Inline sync notifier: current / fresh / stale / private / not-found / failed.
// This is the per-account counterpart to the API-wide status in the Options
// panel. When a recrawl is in flight it also shows a live countdown to when
// fresh data should land (and when the next recrawl can be requested).
function SyncChip({ acct, hasApiKey }) {
  const state = syncState(acct);

  const synced = toMs(acct.rivals_synced_at);
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

  let cls, icon, text;
  if (state === "current") {
    cls = "ok";
    icon = "✓";
    text = synced ? `API rank is current — crawled ${fmtRelative(synced)}`
                  : "API rank is current";
  } else if (state === "fresh") {
    cls = "ok";
    icon = "●";
    text = synced ? `API rank synced ${fmtRelative(synced)}` : "API rank synced";
  } else {
    const m = SYNC_META[state] || SYNC_META.error;
    cls = m.cls;
    icon = m.sym;
    text = (state === "stale" && synced)
      ? `API rank from ${fmtRelative(synced)} — may be outdated`
      : m.text;
  }

  const recrawlMins = recrawlPending ? Math.max(1, Math.ceil(recrawlLeft / 60000)) : 0;
  return (
    <div className={"sync-chip sync-chip-" + cls} data-state={state}
         title={acct.last_refresh_error || text}>
      <span className="sync-chip-i" aria-hidden="true">{icon}</span>
      <span className="sync-chip-t">{text}</span>
      {recrawlPending && (
        <span className="sync-chip-pending"
              title={"Recrawl queued — marvelrivalsapi is re-fetching this "
                     + "player's live stats from the game. The crawl usually "
                     + "finishes within a few minutes (30 min at the most); "
                     + "refresh this card again then to pull the updated rank. "
                     + "Another recrawl can't be queued for this player for ~"
                     + recrawlMins + " min."}>
          · recrawl queued · ~{recrawlMins}m
        </span>
      )}
      {recrawlReady && (
        <span className="sync-chip-ready"
              title={"The 30-min recrawl window has elapsed — marvelrivalsapi "
                     + "should have fresh data for this player now. Nothing "
                     + "refreshes on its own; hit the ↻ button to pull the "
                     + "recrawled rank onto the card."}>
          · recrawl done — hit ↻
        </span>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// B. CARDS  (refined trading-card)
// ─────────────────────────────────────────────────────────────────────────────
function CardRefined({ acct, opts, onOpen, onCopy, onPin, onRefresh, refreshing, hasApiKey, activeSteam }) {
  const cur = themeFor(acct.current_rank);
  const peak = themeFor(acct.peak_rank);
  const lab = labelFor(acct);

  return (
    <article
      className={"rcard rcard-" + lab.kind + (acct.neon ? " rcard-neon" : "")}
      style={{
        "--tier-fg":   cur?.fg   || "#9aa3b2",
        "--tier-glow": cur?.glow || "#1a1d24",
        "--label-fg":  lab.color || "var(--muted)",
        "--neon-fg":   acct.neon ? (TAG_COLORS[acct.border_color] || cur?.fg || "#9aa3b2") : "transparent",
      }}
      onClick={() => onOpen(acct)}
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
        {isActiveSteamMatch(acct, activeSteam) && <ActiveSteamBadge activeSteam={activeSteam} />}
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
          <span className="rcard-rval" style={rankInk(cur)}>{rankDisplay(acct.current_rank, acct.current_points)}</span>
        </div>
        <div className="rcard-rank">
          <span className="rcard-rdot" style={{ background: peak?.fg }} />
          <span className="rcard-rlbl">PEAK</span>
          <span className="rcard-rval" style={rankInk(peak)}>{rankDisplay(acct.peak_rank, acct.peak_points)}</span>
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
function TableView({ accounts, opts, onOpen, onCopy, onPin, onRefresh, refreshingIds, hasApiKey, sortLabel, activeSteam }) {
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
                    activeSteam={activeSteam} />
        ))}
      </ol>
    </section>
  );
}

function TableRow({ acct, opts, onOpen, onCopy, onPin, onRefresh, refreshing, hasApiKey, activeSteam }) {
  const cur = themeFor(acct.current_rank);
  const peak = themeFor(acct.peak_rank);
  const lab = labelFor(acct);
  const [open, setOpen] = React.useState(false);

  return (
    <li
      className={"tbl-row" + (open ? " is-open" : "")}
      data-label={lab.kind}
    >
      <div className="tbl-row-main" onClick={() => setOpen((p) => !p)}>
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
          {isActiveSteamMatch(acct, activeSteam) && <ActiveSteamBadge activeSteam={activeSteam} compact />}
          <TagPill acct={acct} />
        </div>

        <div className="tbl-rank-cell">
          <span className="tbl-rdot" style={{ background: cur?.fg }} />
          <span style={rankInk(cur)}>{rankDisplay(acct.current_rank, acct.current_points)}</span>
        </div>

        <div className="tbl-rank-cell">
          <span className="tbl-rdot" style={{ background: peak?.fg }} />
          <span style={rankInk(peak)}>{rankDisplay(acct.peak_rank, acct.peak_points)}</span>
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

function LadderView({ accounts, opts, onOpen, onCopy, onPin, onRefresh, refreshingIds, hasApiKey, activeSteam }) {
  // Group by tier of current_rank
  const groups = React.useMemo(() => {
    const m = new Map();
    for (const t of TIER_ORDER) m.set(t, []);
    m.set("Unranked", []);
    for (const a of accounts) {
      const t = tierOf(a.current_rank) || "Unranked";
      if (!m.has(t)) m.set(t, []);
      m.get(t).push(a);
    }
    return [...m.entries()].filter(([_, list]) => list.length > 0);
  }, [accounts]);

  return (
    <div className="ladder">
      {groups.map(([tier, list]) => (
        <LadderGroup key={tier} tier={tier} list={list}
                     opts={opts} onOpen={onOpen} onCopy={onCopy} onPin={onPin}
                     onRefresh={onRefresh} refreshingIds={refreshingIds} hasApiKey={hasApiKey}
                     activeSteam={activeSteam} />
      ))}
    </div>
  );
}

function LadderGroup({ tier, list, opts, onOpen, onCopy, onPin, onRefresh, refreshingIds, hasApiKey, activeSteam }) {
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
          <LadderCard key={a.id} acct={a}
                      opts={opts} onOpen={onOpen} onCopy={onCopy} onPin={onPin}
                      onRefresh={onRefresh}
                      refreshing={refreshingIds && refreshingIds.has(a.id)}
                      hasApiKey={hasApiKey}
                      activeSteam={activeSteam} />
        ))}
      </div>
    </section>
  );
}

function LadderCard({ acct, opts, onOpen, onPin, onRefresh, refreshing, hasApiKey, activeSteam }) {
  const cur = themeFor(acct.current_rank);
  const peak = themeFor(acct.peak_rank);
  const lab = labelFor(acct);
  const division = (() => {
    if (!acct.current_rank) return "";
    if (acct.current_rank === "Eternity" || acct.current_rank === "One Above All") {
      return acct.current_points ? String(acct.current_points) : "";
    }
    return acct.current_rank.split(" ")[1] || ""; // I / II / III
  })();

  return (
    <article
      className={"lad-card lad-card-" + lab.kind}
      style={{ "--tier-fg": cur?.fg || "var(--muted)" }}
      onClick={() => onOpen(acct)}
    >
      <div className="lad-line">
        <span className={"lad-ign" + (acct.in_game_name ? "" : " lad-ign-empty")}>
          {acct.in_game_name || "No IGN set"}
        </span>
        <div className="lad-line-r">
          {isActiveSteamMatch(acct, activeSteam) && <ActiveSteamBadge activeSteam={activeSteam} compact />}
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
        <span className="lad-peak-lbl">peak</span>
        <span className="lad-peak-val" style={rankInk(peak)}>
          {rankDisplay(acct.peak_rank, acct.peak_points)}
        </span>
      </div>
    </article>
  );
}

Object.assign(window, {
  CardRefined, TableView, LadderView, labelFor,
});
