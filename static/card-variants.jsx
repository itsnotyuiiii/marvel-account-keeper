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

// Display string for a rank, including the point score for Eternity / OAA.
// `Eternity (4280)` reads much better than just `Eternity` when the user
// went to the trouble of recording the number.
function rankDisplay(rank, points) {
  if (!rank) return "—";
  if ((rank === "Eternity" || rank === "One Above All") && points != null && points !== "") {
    return `${rank} · ${points}`;
  }
  return rank;
}

function labelFor(acct) {
  if (acct.pinned) return { kind: "main", text: "MAIN ACCOUNT", color: "#e8b94a" };
  if (acct.peak_rank === "One Above All") return { kind: "oaa", text: "PEAKED OAA", color: "#ff5560" };
  return { kind: "alt", text: "ALT", color: null };
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

// Account timestamps are epoch seconds; normalize anything seconds-scale to ms.
function toMs(ts) {
  if (ts == null) return null;
  return ts < 1e12 ? ts * 1000 : ts;
}

// Collapse an account's refresh fields into one state token. Drives both the
// SyncChip and the refresh-button badge so they always agree.
//   none → never refreshed      fresh → ok & recently crawled
//   stale → ok but crawl is old   private / not_found / bad_key / error / missing_handle
function syncState(acct) {
  const st = acct.last_refresh_status;
  if (!st) return "none";
  if (st !== "ok") return st;
  const synced = toMs(acct.rivals_synced_at);
  if (synced == null) return "fresh";
  return (Date.now() - synced) > SYNC_STALE_AFTER_MS ? "stale" : "fresh";
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

// Inline sync notifier: fresh / stale / private / not-found / failed. This is
// the per-account counterpart to the API-wide status in the Options panel.
function SyncChip({ acct, hasApiKey }) {
  const state = syncState(acct);
  if (state === "none") {
    return hasApiKey ? (
      <div className="sync-chip sync-chip-muted">
        <span className="sync-chip-i" aria-hidden="true">○</span>
        <span className="sync-chip-t">Not synced yet — hit ↻ to pull the live rank</span>
      </div>
    ) : null;
  }

  const synced = toMs(acct.rivals_synced_at);
  const reqAt = toMs(acct.rivals_update_requested_at);
  const recrawlPending = reqAt != null && (Date.now() - reqAt) < RECRAWL_PENDING_MS;

  let cls, icon, text;
  if (state === "fresh") {
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
  return (
    <div className={"sync-chip sync-chip-" + cls}
         title={acct.last_refresh_error || text}>
      <span className="sync-chip-i" aria-hidden="true">{icon}</span>
      <span className="sync-chip-t">{text}</span>
      {recrawlPending && <span className="sync-chip-pending">· recrawl queued</span>}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// B. CARDS  (refined trading-card)
// ─────────────────────────────────────────────────────────────────────────────
function CardRefined({ acct, opts, onOpen, onCopy, onPin, onRefresh, refreshing, hasApiKey }) {
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
          <span className="rcard-rval" style={{ color: cur?.fg }}>{rankDisplay(acct.current_rank, acct.current_points)}</span>
        </div>
        <div className="rcard-rank">
          <span className="rcard-rdot" style={{ background: peak?.fg }} />
          <span className="rcard-rlbl">PEAK</span>
          <span className="rcard-rval" style={{ color: peak?.fg }}>{rankDisplay(acct.peak_rank, acct.peak_points)}</span>
        </div>
      </div>

      <SyncChip acct={acct} hasApiKey={hasApiKey} />

      {!opts.hideCopy && (
        <footer className="rcard-foot">
          <span className="rcard-time">{fmtRelative(acct.updated_at)}</span>
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
function TableView({ accounts, opts, onOpen, onCopy, onPin, onRefresh, refreshingIds, hasApiKey, sortLabel }) {
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
                    hasApiKey={hasApiKey} />
        ))}
      </ol>
    </section>
  );
}

function TableRow({ acct, opts, onOpen, onCopy, onPin, onRefresh, refreshing, hasApiKey }) {
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
          <TagPill acct={acct} />
        </div>

        <div className="tbl-rank-cell">
          <span className="tbl-rdot" style={{ background: cur?.fg }} />
          <span style={{ color: cur?.fg }}>{rankDisplay(acct.current_rank, acct.current_points)}</span>
        </div>

        <div className="tbl-rank-cell">
          <span className="tbl-rdot" style={{ background: peak?.fg }} />
          <span style={{ color: peak?.fg }}>{rankDisplay(acct.peak_rank, acct.peak_points)}</span>
        </div>

        <div className="tbl-time">{fmtRelative(acct.updated_at)}</div>

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

function LadderView({ accounts, opts, onOpen, onCopy, onPin, onRefresh, refreshingIds, hasApiKey }) {
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
                     onRefresh={onRefresh} refreshingIds={refreshingIds} hasApiKey={hasApiKey} />
      ))}
    </div>
  );
}

function LadderGroup({ tier, list, opts, onOpen, onCopy, onPin, onRefresh, refreshingIds, hasApiKey }) {
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
                      hasApiKey={hasApiKey} />
        ))}
      </div>
    </section>
  );
}

function LadderCard({ acct, opts, onOpen, onPin, onRefresh, refreshing, hasApiKey }) {
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
        <span className="lad-peak-val" style={{ color: peak?.fg || "var(--muted)" }}>
          {rankDisplay(acct.peak_rank, acct.peak_points)}
        </span>
      </div>
    </article>
  );
}

Object.assign(window, {
  CardRefined, TableView, LadderView, labelFor,
});
