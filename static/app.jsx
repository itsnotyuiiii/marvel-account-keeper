// Main app — shell, toolbar, header, drawer, lock screen, toast.
// Three views: Cards / Table / Ladder. Same grayscale chassis; rank tier
// is the only chroma in the UI.
//
// This file is wired to the Flask backend (app.py). All account state lives
// server-side in the encrypted vault; this component is a thin client:
//
//   • Initial load   → GET  /api/status  then  GET /api/accounts   (item 4)
//   • Save           → POST /api/accounts  /  PUT /api/accounts/:id (item 1)
//   • Delete         → DELETE /api/accounts/:id                    (item 2)
//   • Pin            → PUT  /api/accounts/:id { pinned }           (item 3)
//   • Options        → localStorage + POST /api/options            (items 4b, 5)
//   • Lock countdown → mirrors server last_activity; auto-lock is
//                      server-driven via lockout_minutes           (item 5)
//   • Drawer drafts  → localStorage auto-save, password excluded    (item 6)
//   • tag / neon / *_points fields are part of the account schema  (item 7)

const {
  RANK_TIERS, RANK_INDEX, RANK_THEME, tierOf, themeFor, fmtRelative,
  CardRefined, TableView, LadderView, labelFor,
  useTweaks, TweaksPanel, TweakSection, TweakRadio, TweakToggle, TweakColor, TweakSelect
} = window;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "view": "cards",
  "theme": "dark",
  "densityCards": "regular",
  "densityTable": "regular",
  "densityLadder": "regular",
  "hideDetails": false,
  "hideCopy": false,
  "infoRailOpen": true,
  "lockoutMinutes": 30
} /*EDITMODE-END*/;

// User-facing options persisted to localStorage so they survive refresh /
// tab close. lockoutMinutes is *also* persisted server-side (POST /api/options)
// because the server owns the real auto-lock timeout; the server value is
// authoritative and is synced back into this store on every boot/unlock.
const OPTIONS_KEY = "marvel-tracker-options";
const OPTION_KEYS = ["view", "theme", "densityCards", "densityTable", "densityLadder",
  "hideDetails", "hideCopy", "infoRailOpen", "lockoutMinutes"];
// Density is stored per view, so each layout keeps its own compact/comfy choice.
const DENSITY_KEY = { cards: "densityCards", table: "densityTable", ladder: "densityLadder" };
const DENSITY_VIEW_LABEL = { cards: "Cards", table: "Table", ladder: "Ladder" };
function loadStoredOptions() {
  try {
    const raw = localStorage.getItem(OPTIONS_KEY);
    const stored = raw ? JSON.parse(raw) : {};
    // Migrate the legacy single `density` value to the per-view keys.
    if (typeof stored.density === "string") {
      for (const k of ["densityCards", "densityTable", "densityLadder"]) {
        if (stored[k] === undefined) stored[k] = stored.density;
      }
      delete stored.density;
    }
    return stored;
  } catch { return {}; }
}
function saveStoredOptions(values) {
  try {
    const subset = {};
    for (const k of OPTION_KEYS) if (values[k] !== undefined) subset[k] = values[k];
    localStorage.setItem(OPTIONS_KEY, JSON.stringify(subset));
  } catch { /* quota / private-mode — silently no-op */ }
}

const LOCKOUT_OPTIONS = [
  { value: 0,   label: "Never (stay unlocked)" },
  { value: 5,   label: "5 minutes" },
  { value: 15,  label: "15 minutes" },
  { value: 30,  label: "30 minutes" },
  { value: 60,  label: "1 hour" },
  { value: 240, label: "4 hours" },
];

const SORT_OPTIONS = [
{ value: "pinned", label: "Pinned first" },
{ value: "current_desc", label: "Current rank ↓" },
{ value: "peak_desc", label: "Peak rank ↓" },
{ value: "ign", label: "IGN A→Z" },
{ value: "recent", label: "Recently edited" }];


function sortAccounts(items, mode) {
  const arr = items.slice();
  const idx = (r) => r && RANK_INDEX[r] != null ? RANK_INDEX[r] : -1;
  const byName = (a, b) =>
  (a.in_game_name || a.username || "").localeCompare(
    b.in_game_name || b.username || "", undefined, { sensitivity: "base" }
  );
  switch (mode) {
    case "ign":arr.sort(byName);break;
    case "current_desc":arr.sort((a, b) => idx(b.current_rank) - idx(a.current_rank));break;
    case "peak_desc":arr.sort((a, b) => idx(b.peak_rank) - idx(a.peak_rank));break;
    case "recent":arr.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));break;
    case "pinned":
    default:
      arr.sort((a, b) =>
      (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0) ||
      idx(b.current_rank) - idx(a.current_rank) ||
      idx(b.peak_rank) - idx(a.peak_rank));
      break;
  }
  return arr;
}

function filterAccounts(items, q) {
  q = q.trim().toLowerCase();
  if (!q) return items;
  return items.filter((a) =>
  [a.in_game_name, a.username, a.email, a.notes, a.current_rank, a.peak_rank].
  some((v) => (v || "").toLowerCase().includes(q))
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// API helper
// ─────────────────────────────────────────────────────────────────────────────
// Single fetch wrapper for authenticated endpoints. A 401 means the server
// auto-locked (or someone hit /api/lock); callers get a tagged error and the
// app drops back to the lock screen. `onLocked` / `onActivity` are supplied
// by App so this stays a plain function.
async function apiCall(path, opts, onLocked, onActivity) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 401) {
    onLocked && onLocked();
    const e = new Error("locked");
    e.locked = true;
    throw e;
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const e = new Error((data && data.error) || "request_failed");
    e.data = data;
    e.status = res.status;
    throw e;
  }
  // Every authenticated request resets the server's last_activity timer —
  // mirror that on the client so the countdown stays in sync. (item 5)
  onActivity && onActivity();
  return data;
}

// ─────────────────────────────────────────────────────────────────────────────
// Drawer draft auto-save  (item 6)
// ─────────────────────────────────────────────────────────────────────────────
// Form fields are mirrored to localStorage on every keystroke so an Alt+F4,
// a tab close, an Esc, or a server auto-lock never loses in-progress edits.
// The draft is restored when the drawer reopens for the same account.
const DRAFT_PREFIX = "marvel-tracker-draft:";
// Password is intentionally excluded — never store decrypted secrets in localStorage.
const DRAFT_FIELDS = [
  "in_game_name", "rivals_uid", "username", "email", "current_rank", "peak_rank",
  "current_points", "peak_points", "notes", "tag", "border_color",
  "pinned", "neon",
];
function draftKey(acct) {
  return DRAFT_PREFIX + ((acct && acct.id) || "__new");
}
function saveDraft(acct, form) {
  const key = draftKey(acct);
  const d = {};
  let any = false;
  for (const f of DRAFT_FIELDS) {
    d[f] = form[f];
    if (form[f] !== "" && form[f] != null && form[f] !== false) any = true;
  }
  d.savedAt = Date.now();
  try {
    if (any) localStorage.setItem(key, JSON.stringify(d));
    else localStorage.removeItem(key);
  } catch { /* quota / private-mode — silently no-op */ }
}
function loadDraft(acct) {
  try {
    const raw = localStorage.getItem(draftKey(acct));
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}
function clearDraft(acct) {
  try { localStorage.removeItem(draftKey(acct)); } catch { /* no-op */ }
}

// ─────────────────────────────────────────────────────────────────────────────
// HEADER
// ─────────────────────────────────────────────────────────────────────────────
// Play a real quack sample. Falls back to silence if the file can't load.
let __quackAudio = null;
function quack() {
  try {
    if (!__quackAudio) {
      __quackAudio = new Audio("/static/assets/quack.mp3");
      __quackAudio.preload = "auto";
      __quackAudio.volume = 0.85;
    }
    __quackAudio.currentTime = 0;
    const p = __quackAudio.play();
    if (p && p.catch) p.catch(() => {});
  } catch (e) { /* silently no-op */ }
}

function DuckMark({ onQuack }) {
  const [bobbing, setBobbing] = React.useState(false);
  const trigger = () => {
    setBobbing(true);
    onQuack?.();
    clearTimeout(DuckMark._t);
    DuckMark._t = setTimeout(() => setBobbing(false), 600);
  };
  return (
    <button
      type="button"
      className={"app-duck" + (bobbing ? " is-quacking" : "")}
      onClick={trigger}
      aria-label="Quack"
      title="Quack!"
    >
      <svg viewBox="0 0 40 36" width="32" height="28" aria-hidden="true">
        {/* head — mallard green */}
        <circle cx="18" cy="17" r="13" fill="#3fbf4a" stroke="#0e2c12" strokeWidth="1.4" />
        {/* iridescent sheen */}
        <path d="M8 16a13 13 0 0 1 20-6" fill="none" stroke="#6fe57c" strokeWidth="1.6" strokeLinecap="round" opacity=".85" />
        <path d="M10 21a13 13 0 0 0 16 6" fill="none" stroke="#2a8a36" strokeWidth="1.4" strokeLinecap="round" opacity=".7" />
        {/* bill */}
        <path d="M27 16c5 0 9 1.5 9 3.6S32 23.2 27 23.2c-2.7 0-4-1-4-3.6s1.3-3.6 4-3.6Z"
              fill="#ff9b2f" stroke="#1a1500" strokeWidth="1.4" strokeLinejoin="round" />
        <path d="M24 19.6h11" stroke="#1a1500" strokeWidth="1" opacity=".55" />
        {/* eye */}
        <circle cx="20" cy="14" r="2.2" fill="#0e2c12" />
        <circle cx="20.7" cy="13.4" r="0.7" fill="#fff" />
        {/* tuft */}
        <path d="M14 5.5c0-2 1-3 2-3s1 1 2 1 1.5-1 2.5-1 1.5 1.2 1.5 2.5"
              fill="none" stroke="#0e2c12" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
      <span className="app-duck-quack" aria-hidden="true">quack!</span>
    </button>
  );
}

// Sun / moon button — flips the app between dark and light. Shows the icon of
// the mode you'd switch *to*.
function ThemeToggle({ theme, onToggle }) {
  const light = theme === "light";
  return (
    <button className="app-btn-icon" onClick={onToggle}
            aria-label={light ? "Switch to dark mode" : "Switch to light mode"}
            title={light ? "Switch to dark mode" : "Switch to light mode"}>
      {light ? (
        <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
          <path d="M13.6 9.4A5.6 5.6 0 0 1 6.6 2.4 5.6 5.6 0 1 0 13.6 9.4Z"
                fill="currentColor" />
        </svg>
      ) : (
        <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
          <circle cx="8" cy="8" r="3.1" fill="currentColor" />
          <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <path d="M8 1v1.8M8 13.2V15M1 8h1.8M13.2 8H15M3.05 3.05l1.27 1.27M11.68 11.68l1.27 1.27M12.95 3.05l-1.27 1.27M4.32 11.68l-1.27 1.27" />
          </g>
        </svg>
      )}
    </button>
  );
}

function Header({ count, lockIn, lockoutMinutes, onLock, onSettings, theme, onToggleTheme }) {
  return (
    <header className="app-head">
      <div className="app-head-l">
        <DuckMark onQuack={quack} />
        <div className="app-title-wrap">
          <span className="app-eyebrow">MARVEL · ACCOUNT KEEPER</span>
          <span className="app-aesth">by Yui</span>
        </div>
      </div>
      <div className="app-head-r">
        <span className="app-stat"><b>{count}</b> {count === 1 ? "account" : "accounts"}</span>
        {lockoutMinutes > 0 ? (
          <span className="app-stat app-stat-warn">
            <span className="app-dot-pulse" /> locks in {lockIn}
          </span>
        ) : (
          <span className="app-stat app-stat-quiet" title="Auto-lock is disabled in options">
            <span className="app-dot-quiet" /> auto-lock off
          </span>
        )}
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        <button className="app-btn-icon" onClick={onSettings} aria-label="Options" title="Options">
          <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
            <path fill="currentColor" d="M9.405 1.05c-.413-1.4-2.397-1.4-2.81 0l-.1.34a1.464 1.464 0 0 1-2.105.872l-.31-.17c-1.283-.698-2.686.705-1.987 1.987l.169.311c.446.82.023 1.841-.872 2.105l-.34.1c-1.4.413-1.4 2.397 0 2.81l.34.1a1.464 1.464 0 0 1 .872 2.105l-.17.31c-.698 1.283.705 2.686 1.987 1.987l.311-.169a1.464 1.464 0 0 1 2.105.872l.1.34c.413 1.4 2.397 1.4 2.81 0l.1-.34a1.464 1.464 0 0 1 2.105-.872l.31.17c1.283.698 2.686-.705 1.987-1.987l-.169-.311a1.464 1.464 0 0 1 .872-2.105l.34-.1c1.4-.413 1.4-2.397 0-2.81l-.34-.1a1.464 1.464 0 0 1-.872-2.105l.17-.31c.698-1.283-.705-2.686-1.987-1.987l-.311.169a1.464 1.464 0 0 1-2.105-.872l-.1-.34zM8 10.5a2.5 2.5 0 1 1 0-5 2.5 2.5 0 0 1 0 5z" />
          </svg>
        </button>
        <button className="app-btn-ghost" onClick={onLock}>Lock</button>
      </div>
    </header>);

}

// ─────────────────────────────────────────────────────────────────────────────
// TOOLBAR
// ─────────────────────────────────────────────────────────────────────────────
const Toolbar = React.forwardRef(function Toolbar(
{ query, onQuery, sort, onSort, view, onView, onNew,
  onRefreshAll, refreshingAll, hasApiKey, accountsCount }, searchRef)
{
  return (
    <div className="app-toolbar">
      <div className="app-search">
        <svg viewBox="0 0 16 16" width="14" height="14" className="app-search-i" aria-hidden="true">
          <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <path d="M10.5 10.5 14 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
        <input
          ref={searchRef}
          type="search"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Search IGN, username, email, notes…"
          aria-label="Search accounts" />

        {query &&
        <button className="app-search-x" onClick={() => onQuery("")} aria-label="Clear">×</button>
        }
      </div>

      {/* View switcher: segmented control */}
      <div className="app-viewseg" role="radiogroup" aria-label="View">
        {["cards", "table", "ladder"].map((v) =>
        <button key={v} type="button" role="radio" aria-checked={view === v}
        className={"app-viewseg-btn" + (view === v ? " on" : "")}
        onClick={() => onView(v)}>
            <ViewIcon view={v} />
            <span>{v[0].toUpperCase() + v.slice(1)}</span>
          </button>
        )}
      </div>

      {view !== "ladder" &&
      <select className="app-select" value={sort} onChange={(e) => onSort(e.target.value)}>
          {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      }

      {hasApiKey && accountsCount > 0 && (
        <button
          className={"app-btn-ghost app-btn-refresh-all" + (refreshingAll ? " is-busy" : "")}
          onClick={onRefreshAll}
          disabled={refreshingAll}
          title="Pull current ranks from marvelrivalsapi.com for every account">
          <RefreshIcon spinning={refreshingAll} />
          <span>{refreshingAll ? "Refreshing…" : "Refresh stats"}</span>
        </button>
      )}

      <button className="app-btn-primary" onClick={onNew}>
        <span className="app-btn-plus">+</span> New account
      </button>
    </div>);

});

function RefreshIcon({ spinning }) {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13"
         className={"app-refresh-svg" + (spinning ? " is-spinning" : "")}
         aria-hidden="true">
      <path d="M2.5 8a5.5 5.5 0 0 1 9.9-3.3M13.5 8a5.5 5.5 0 0 1-9.9 3.3"
            fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M12.4 1.6v3.1H9.3" fill="none" stroke="currentColor"
            strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3.6 14.4v-3.1h3.1" fill="none" stroke="currentColor"
            strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ViewIcon({ view }) {
  if (view === "cards") {
    return (
      <svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true">
        <rect x="1" y="1" width="5" height="5" rx="1" fill="currentColor" />
        <rect x="8" y="1" width="5" height="5" rx="1" fill="currentColor" />
        <rect x="1" y="8" width="5" height="5" rx="1" fill="currentColor" />
        <rect x="8" y="8" width="5" height="5" rx="1" fill="currentColor" />
      </svg>);

  }
  if (view === "table") {
    return (
      <svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true">
        <rect x="1" y="2" width="12" height="2" fill="currentColor" />
        <rect x="1" y="6" width="12" height="2" fill="currentColor" opacity=".7" />
        <rect x="1" y="10" width="12" height="2" fill="currentColor" opacity=".5" />
      </svg>);

  }
  // ladder
  return (
    <svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true">
      <rect x="1" y="1" width="6" height="2" fill="currentColor" />
      <rect x="1" y="6" width="10" height="2" fill="currentColor" opacity=".7" />
      <rect x="1" y="11" width="8" height="2" fill="currentColor" opacity=".5" />
    </svg>);

}

// ─────────────────────────────────────────────────────────────────────────────
// DRAWER
// ─────────────────────────────────────────────────────────────────────────────
function baseFormFor(acct) {
  return {
    in_game_name: acct?.in_game_name || "",
    rivals_uid: acct?.rivals_uid || "",
    username: acct?.username || "",
    email: acct?.email || "",
    password: acct?.password || "",
    current_rank: acct?.current_rank || "",
    peak_rank: acct?.peak_rank || "",
    current_points: acct?.current_points ?? "",
    peak_points: acct?.peak_points ?? "",
    notes: acct?.notes || "",
    tag: acct?.tag || "",
    border_color: acct?.border_color || "",
    pinned: !!acct?.pinned,
    neon: !!acct?.neon,
  };
}

function Drawer({ open, acct, view, onClose, onSave, onDelete }) {
  const isNew = !acct?.id;
  const [confirmingDelete, setConfirmingDelete] = React.useState(false);
  const [form, setForm] = React.useState(() => baseFormFor(null));
  const [showPw, setShowPw] = React.useState(false);
  // Timestamp of a restored draft, or null. Drives the banner.
  const [draftAt, setDraftAt] = React.useState(null);
  const [saving, setSaving] = React.useState(false);

  // (Re)hydrate the form whenever the drawer opens. A saved draft for this
  // account is overlaid on top of the persisted values. (item 6)
  React.useEffect(() => {
    if (!open) return;
    const base = baseFormFor(acct);
    const draft = loadDraft(acct);
    if (draft) {
      for (const f of DRAFT_FIELDS) {
        if (draft[f] !== undefined) base[f] = draft[f];
      }
      setDraftAt(draft.savedAt || Date.now());
    } else {
      setDraftAt(null);
    }
    setForm(base);
    setShowPw(false);
    setConfirmingDelete(false);
  }, [open, acct]);

  // Every user edit writes the draft. The open-time hydration above does NOT
  // go through `set`, so opening an account never creates a phantom draft.
  const set = (k, v) => setForm((p) => {
    const next = { ...p, [k]: v };
    saveDraft(acct, next);
    return next;
  });

  const discardDraft = () => {
    clearDraft(acct);
    setDraftAt(null);
    setForm(baseFormFor(acct));
  };

  // Explicit Cancel discards the draft. Other close paths (×, double-click
  // backdrop, Esc, lock, Alt+F4) preserve it on purpose.
  const handleCancel = () => {
    clearDraft(acct);
    onClose();
  };

  const handleSave = async () => {
    if (saving) return;
    setSaving(true);
    const ok = await onSave({ ...acct, ...form });
    setSaving(false);
    if (ok !== false) clearDraft(acct);
  };

  const handleDelete = async () => {
    setConfirmingDelete(false);
    const ok = await onDelete(acct);
    if (ok !== false) clearDraft(acct);
  };

  return (
    <>
      <div className={"drawer-backdrop" + (open ? " open" : "")}
           onDoubleClick={onClose}
           title="Double-click to close" />
      <aside className={"drawer" + (open ? " open" : "")} aria-hidden={!open}>
        <header className="drawer-head">
          <div>
            <div className="drawer-eyebrow">{isNew ? "NEW ACCOUNT" : "EDIT ACCOUNT"}</div>
            <h2 className="drawer-title">{isNew ? "Add an account" : form.in_game_name || "Untitled"}</h2>
          </div>
          <button className="drawer-x" onClick={onClose} aria-label="Close">×</button>
        </header>

        <div className="drawer-body">
          {draftAt && (
            <div className="drawer-draft-banner">
              <span>
                Restored unsaved changes from {fmtRelative(draftAt)}.
                {isNew && " Re-enter the password from 1Password if needed."}
              </span>
              <button type="button" onClick={discardDraft}>Discard</button>
            </div>
          )}

          <section className="drawer-section">
            <div className="drawer-section-lbl">Identity</div>
            <Field label="In-game name" value={form.in_game_name}
            onChange={(v) => set("in_game_name", v)}
            placeholder="e.g. Yuiiii — optional if a UID is set" />
            <Field label="Marvel Rivals UID" value={form.rivals_uid}
            onChange={(v) => set("rivals_uid", v)}
            placeholder="recommended — numeric player ID" />
            <p className="dr-field-note">
              <strong>A UID is the preferred way to link an account.</strong> It's
              the most reliable lookup — it works for private or renamed accounts,
              and it pulls the exact in-game name straight from the API, including
              superscript / special characters that are awkward to type by hand.
              Set a UID and the in-game name fills itself in and stays in sync on
              every refresh; leave it blank and lookups fall back to the typed
              in-game name.
            </p>
          </section>

          <section className="drawer-section">
            <div className="drawer-section-lbl">Credentials</div>
            <Field label="Steam username" value={form.username}
            onChange={(v) => set("username", v)} />
            <Field label="Email" value={form.email}
            onChange={(v) => set("email", v)} type="email" />
            <div className="drawer-pw">
              <Field
                label="Password"
                value={form.password}
                onChange={(v) => set("password", v)}
                type={showPw ? "text" : "password"} />

              <button className="drawer-pw-toggle" type="button"
              onClick={() => setShowPw((p) => !p)}>{showPw ? "hide" : "show"}</button>
            </div>
          </section>

          <section className="drawer-section">
            <div className="drawer-section-lbl">Rank</div>
            <div className="drawer-rank-grid">
              <div className="dr-rank-pair">
                <RankPicker label="Current rank" value={form.current_rank}
                onChange={(v) => set("current_rank", v)} />
                {rankNeedsPoints(form.current_rank) && (
                  <PointsField
                    label={`${form.current_rank} score`}
                    value={form.current_points}
                    onChange={(v) => set("current_points", v)} />
                )}
              </div>
              <div className="dr-rank-pair">
                <RankPicker label="Peak rank" value={form.peak_rank}
                onChange={(v) => set("peak_rank", v)} />
                {rankNeedsPoints(form.peak_rank) && (
                  <PointsField
                    label={`${form.peak_rank} score`}
                    value={form.peak_points}
                    onChange={(v) => set("peak_points", v)} />
                )}
              </div>
            </div>
          </section>

          <section className="drawer-section">
            <div className="drawer-section-lbl">Tag</div>
            <Field
              label="Tag label (optional)"
              value={form.tag}
              onChange={(v) => set("tag", v)}
              placeholder="e.g. main steam, smurf, stream account" />
            <div className="drawer-sw-row">
              <span className="dr-field-lbl">Tag color</span>
              <div className="drawer-swatches">
                {["", "red", "orange", "yellow", "green", "cyan", "magenta"].map((c) =>
                <button key={c || "none"} type="button"
                className={"drawer-sw" + (form.border_color === c ? " on" : "") + (c ? "" : " none")}
                data-color={c}
                aria-label={c || "none"}
                onClick={() => set("border_color", c)} />
                )}
              </div>
            </div>
            {form.tag && (
              <div className="drawer-tag-preview">
                <span className="dr-field-lbl">Preview</span>
                <span
                  className="tag-pill tag-pill-sm"
                  style={{
                    color: ({red:"#ff5560",orange:"#ff9d2f",yellow:"#ffe14d",green:"#4ee07e",cyan:"#5be0ff",magenta:"#ff6ed4"})[form.border_color] || "#9aa3b2",
                    borderColor: "currentColor",
                    background: "transparent",
                  }}>
                  <i className="tag-pill-dot" style={{ background: "currentColor" }} />
                  {form.tag}
                </span>
              </div>
            )}
            <label className="drawer-pin-row">
              <span>Pin as main account</span>
              <input type="checkbox" checked={form.pinned}
              onChange={(e) => set("pinned", e.target.checked)} />
            </label>
            {view === "cards" && (
              <label className="drawer-pin-row">
                <span>
                  Neon border on card
                  <em className="drawer-hint">Makes this account stand out in Cards view</em>
                </span>
                <input type="checkbox" checked={form.neon}
                onChange={(e) => set("neon", e.target.checked)} />
              </label>
            )}
            <Field label="Notes" value={form.notes}
            onChange={(v) => set("notes", v)} multiline
            placeholder="e.g. 1Password entry, alt purpose, region…" />
          </section>
        </div>

        <footer className="drawer-foot">
          {!isNew && (
            confirmingDelete ? (
              <div className="drawer-confirm">
                <span className="drawer-confirm-lbl">Delete this account?</span>
                <button className="drawer-btn-ghost" onClick={() => setConfirmingDelete(false)}>Keep</button>
                <button className="drawer-btn-del-confirm" onClick={handleDelete}>
                  Yes, delete
                </button>
              </div>
            ) : (
              <button className="drawer-btn-del" onClick={() => setConfirmingDelete(true)}>
                Delete account
              </button>
            )
          )}
          {!confirmingDelete && (
            <div className="drawer-foot-r">
              <button className="drawer-btn-ghost" onClick={handleCancel}>Cancel</button>
              <button className="drawer-btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? "Saving…" : isNew ? "Create" : "Save changes"}
              </button>
            </div>
          )}
        </footer>
      </aside>
    </>);

}

function Field({ label, value, onChange, type = "text", placeholder = "", multiline = false }) {
  const id = React.useId();
  return (
    <label className="dr-field" htmlFor={id}>
      <span className="dr-field-lbl">{label}</span>
      {multiline ?
      <textarea id={id} value={value} rows={2}
      placeholder={placeholder} onChange={(e) => onChange(e.target.value)} /> :

      <input id={id} type={type} value={value}
      placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      }
    </label>);

}

// Eternity & One Above All in Rivals are point-based, not division-based.
// When either is selected, surface an optional score field next to it.
function rankNeedsPoints(rank) {
  return rank === "Eternity" || rank === "One Above All";
}

function PointsField({ label, value, onChange }) {
  const id = React.useId();
  return (
    <label className="dr-field dr-points" htmlFor={id}>
      <span className="dr-field-lbl">{label} <em className="dr-optional">optional</em></span>
      <input
        id={id}
        type="number"
        inputMode="numeric"
        min="0"
        step="1"
        value={value}
        placeholder="e.g. 4200"
        onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function RankPicker({ label, value, onChange }) {
  const t = themeFor(value);
  const id = React.useId();
  return (
    <label className="dr-field dr-rank" htmlFor={id}>
      <span className="dr-field-lbl">{label}</span>
      <div className="dr-rank-row">
        <span className="dr-rank-dot"
        style={{ background: t?.fg || "transparent",
          borderColor: t?.fg || "var(--border-2)" }} />
        <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">—</option>
          {RANK_TIERS.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>
    </label>);

}

// ─────────────────────────────────────────────────────────────────────────────
// LOCK SCREEN  — init (set master password) and unlock both run through here.
// ─────────────────────────────────────────────────────────────────────────────
function LockScreen({ mode, accountCount, onSubmit }) {
  const isInit = mode === "init";
  const [pw, setPw] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [err, setErr] = React.useState("");
  const [shake, setShake] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(() => { setTimeout(() => ref.current?.focus(), 200); }, []);

  const fail = (m) => {
    setErr(m);
    setShake(true);
    setTimeout(() => setShake(false), 420);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (busy || !pw.length) return;
    setErr("");
    if (isInit) {
      if (pw.length < 6) { fail("Master password must be at least 6 characters."); return; }
      if (pw !== confirm) { fail("Passwords don't match."); return; }
    }
    setBusy(true);
    let error = null;
    try { error = await onSubmit(pw); }
    catch { error = "Something went wrong. Try again."; }
    setBusy(false);
    if (error) fail(error);
  };

  return (
    <div className="lock-screen">
      <div className="lock-bg" aria-hidden="true" />
      <div className="lock-grid" aria-hidden="true" />

      <main className="lock-main">
        <div className="lock-eyebrow">RIVALS · ACCOUNT VAULT</div>
        <h1 className="lock-title">
          {isInit ? (
            <>
              <span className="lock-title-row">Set your</span>
              <span className="lock-title-row lock-title-row-2">master key.</span>
            </>
          ) : (
            <>
              <span className="lock-title-row">All your accounts.</span>
              <span className="lock-title-row lock-title-row-2">One key.</span>
            </>
          )}
        </h1>
        <p className="lock-sub">
          {isInit
            ? "Pick a master password. It encrypts every saved password with scrypt + AES-GCM and is never stored — if you forget it, those passwords are unrecoverable."
            : "Encrypted at rest with scrypt + AES-GCM. The master key never leaves this machine."}
        </p>

        <form className={"lock-form" + (shake ? " shake" : "")} onSubmit={submit}>
          <div className="lock-field">
            <span className="lock-field-lbl">MASTER PASSWORD</span>
            <input
              ref={ref}
              type="password"
              autoComplete={isInit ? "new-password" : "current-password"}
              value={pw}
              onChange={(e) => { setPw(e.target.value); setErr(""); }}
              placeholder="• • • • • • • •"
              aria-label="Master password" />
          </div>
          {isInit && (
            <div className="lock-field">
              <span className="lock-field-lbl">CONFIRM PASSWORD</span>
              <input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => { setConfirm(e.target.value); setErr(""); }}
                placeholder="• • • • • • • •"
                aria-label="Confirm master password" />
            </div>
          )}
          <button type="submit" className="lock-btn" disabled={busy || !pw.length}>
            {busy ? (isInit ? "Creating…" : "Unlocking…") : isInit ? "Create vault" : "Unlock"}
            <span className="lock-btn-arrow" aria-hidden="true">→</span>
          </button>
        </form>
        {err && <div className="lock-err">{err}</div>}

        <footer className="lock-foot">
          <span className="lock-foot-i">●</span> Vault file: <code>marvel-accounts/vault.json</code>
          {!isInit && accountCount > 0 && (
            <>
              <span className="lock-foot-sep">·</span>
              {accountCount} {accountCount === 1 ? "account" : "accounts"} stored
            </>
          )}
        </footer>
      </main>
    </div>);

}

// ─────────────────────────────────────────────────────────────────────────────
// Marvel Rivals API key control (rendered inside the Options panel)
// ─────────────────────────────────────────────────────────────────────────────
function ApiKeyControl({ hasKey, onSave }) {
  // The actual key is never returned by the server — only the "set / not set"
  // boolean. Editing means typing a new key into the input. Empty save clears.
  const [editing, setEditing] = React.useState(false);
  const [value, setValue] = React.useState("");
  const [show, setShow] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  const handleSave = async () => {
    if (busy) return;
    setBusy(true);
    const ok = await onSave(value);
    setBusy(false);
    if (ok) {
      setEditing(false);
      setValue("");
      setShow(false);
    }
  };

  const handleClear = async () => {
    if (busy) return;
    setBusy(true);
    await onSave("");
    setBusy(false);
    setEditing(false);
    setValue("");
    setShow(false);
  };

  return (
    <div className="api-key-ctrl">
      <div className="api-key-status">
        <span className={"api-key-dot" + (hasKey ? " on" : "")} aria-hidden="true" />
        <span className="api-key-status-lbl">
          {hasKey ? "API key set" : "No API key yet"}
        </span>
        {!editing && (
          <button type="button" className="api-key-edit"
                  onClick={() => setEditing(true)}>
            {hasKey ? "Change" : "Add"}
          </button>
        )}
      </div>

      {editing && (
        <>
          <div className="api-key-input-row">
            <input
              className="twk-field api-key-input"
              type={show ? "text" : "password"}
              placeholder="paste x-api-key from marvelrivalsapi.com"
              value={value}
              autoFocus
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleSave(); }} />
            <button type="button" className="api-key-toggle"
                    onClick={() => setShow((s) => !s)}>
              {show ? "hide" : "show"}
            </button>
          </div>
          <div className="api-key-actions">
            <button type="button" className="api-key-btn ghost"
                    onClick={() => { setEditing(false); setValue(""); setShow(false); }}>
              Cancel
            </button>
            {hasKey && (
              <button type="button" className="api-key-btn danger"
                      onClick={handleClear} disabled={busy}>
                Clear
              </button>
            )}
            <button type="button" className="api-key-btn primary"
                    onClick={handleSave} disabled={busy || !value.trim()}>
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      )}

      <p className="api-key-hint">
        Get a free key at{" "}
        <a href="https://marvelrivalsapi.com/dashboard/settings"
           target="_blank" rel="noopener noreferrer">marvelrivalsapi.com</a>{" "}
        — used to refresh current / peak rank automatically. Stays on this machine.
      </p>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Marvel Rivals API sync status (rendered inside the Options panel)
// ─────────────────────────────────────────────────────────────────────────────
// marvelrivalsapi enforces a *dynamic* rate limit surfaced via X-RateLimit-*
// headers — there is no fixed daily number. `calls_today` is our own local
// count; `quota_*` are the live header values (null until an uncached call,
// since cached hits report the literal string "cache").
function SyncStatus({ sync, hasKey }) {
  if (!hasKey) return null;
  if (!sync) {
    return <p className="sync-status-empty">API usage appears after your first refresh.</p>;
  }
  const {
    calls_today, quota_limit, quota_remaining, quota_reset,
    rate_limited, rate_limited_for_s,
  } = sync;

  const fmtSecs = (s) => (s >= 60 ? `${Math.round(s / 60)}m` : `${Math.max(1, s | 0)}s`);
  const fmtUntil = (ts) => {
    if (ts == null) return "—";
    const ms = ts < 1e12 ? ts * 1000 : ts;
    const s = Math.round((ms - Date.now()) / 1000);
    return s <= 0 ? "now" : fmtSecs(s);
  };

  return (
    <div className="sync-status">
      {rate_limited && (
        <div className="sync-status-alert">
          <span aria-hidden="true">⚠</span>
          Rate limited by marvelrivalsapi — retry in {fmtSecs(rate_limited_for_s)}
        </div>
      )}
      <div className="sync-status-grid">
        <div className="sync-status-cell">
          <span className="sync-status-k">Calls today</span>
          <span className="sync-status-v">{calls_today ?? 0}</span>
        </div>
        <div className="sync-status-cell">
          <span className="sync-status-k">Window left</span>
          <span className="sync-status-v">
            {quota_limit != null ? `${quota_remaining ?? "—"} / ${quota_limit}` : "—"}
          </span>
        </div>
        <div className="sync-status-cell">
          <span className="sync-status-k">Window resets</span>
          <span className="sync-status-v">
            {quota_reset != null ? `in ${fmtUntil(quota_reset)}` : "—"}
          </span>
        </div>
      </div>
      <p className="sync-status-note">
        Dynamic rate limit — it adapts to your usage. Cached lookups don't
        report live numbers, so the window figures fill in after an uncached call.
      </p>
      <p className="sync-status-note">
        Each player can only be recrawled once every 30 min. Refreshing more
        often just re-reads the cached rank — it won't pull newer data.
      </p>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Sync status legend — explains every per-account chip / refresh-badge state.
// Lives in the Options panel so the icons on the cards are self-documenting.
// ─────────────────────────────────────────────────────────────────────────────
const SYNC_LEGEND = [
  { icon: "✓", tone: "ok",    name: "Current",   desc: "Rank crawled by the API within the last 30 min — as live as it gets." },
  { icon: "●", tone: "ok",    name: "Synced",    desc: "Rank synced and under a day old." },
  { icon: "▲", tone: "warn",  name: "Stale",     desc: "API data is over a day old — refresh to queue a recrawl." },
  { icon: "🔒", tone: "warn",  name: "Private",   desc: "Profile is private in-game — set the rank manually." },
  { icon: "?", tone: "muted", name: "Not found", desc: "No data for this player on marvelrivalsapi.com." },
  { icon: "?", tone: "muted", name: "No handle", desc: "Account has no in-game name or UID to look up." },
  { icon: "!", tone: "err",   name: "Key error", desc: "API key was rejected — check the key above." },
  { icon: "!", tone: "err",   name: "Failed",    desc: "Last refresh hit an error — retry in a bit." },
  { icon: "○", tone: "muted", name: "Not synced", desc: "Never refreshed from the API yet." },
];

// Collapsible left-side rail carrying the status key. Sits beside the account
// grid so the chip icons on the cards are documented in view, not buried in
// Options. Collapsed state persists via the `infoRailOpen` tweak.
function InfoRail({ open, onToggle }) {
  return (
    <aside className={"info-rail" + (open ? "" : " is-closed")}>
      <button type="button" className="info-rail-tab" onClick={onToggle}
              aria-label={open ? "Collapse status key" : "Show status key"}
              title={open ? "Collapse" : "Status key"}>
        {open ? "‹" : "ⓘ"}
      </button>
      {open && (
        <div className="info-rail-body">
          <div className="info-rail-title">Status key</div>
          <ul className="sync-legend-list">
            {SYNC_LEGEND.map((row) => (
              <li className="sync-legend-row" key={row.name}>
                <span className={"sync-legend-i sync-legend-i-" + row.tone}
                      aria-hidden="true">{row.icon}</span>
                <span className="sync-legend-name">{row.name}</span>
                <span className="sync-legend-desc">{row.desc}</span>
              </li>
            ))}
          </ul>
          <p className="sync-legend-foot">
            <strong>Recrawl queued</strong> — refreshing a stale account asks
            marvelrivalsapi to re-fetch that player's stats from the game. While
            it runs, the card shows a “recrawl queued” note with a live
            countdown (“~12m” = roughly 12 minutes left). When that 30-min
            window is up the note switches to “recrawl done — hit ↻”: nothing
            auto-refreshes, so click ↻ then to pull the new rank in. That window
            is also the soonest another recrawl can be queued for the player.
          </p>
        </div>
      )}
    </aside>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// MAIN APP
// ─────────────────────────────────────────────────────────────────────────────
function App() {
  const [t, setTweakRaw] = useTweaks({ ...TWEAK_DEFAULTS, ...loadStoredOptions() });
  // Wrap setTweak so every runtime change also writes to localStorage.
  const tRef = React.useRef(t);
  tRef.current = t;
  const setTweak = React.useCallback((keyOrEdits, val) => {
    const edits = typeof keyOrEdits === "object" && keyOrEdits !== null
      ? keyOrEdits : { [keyOrEdits]: val };
    setTweakRaw(edits);
    saveStoredOptions({ ...tRef.current, ...edits });
  }, [setTweakRaw]);

  // phase: loading → (init | unlock) → ready
  const [phase, setPhase] = React.useState("loading");
  const [accounts, setAccounts] = React.useState([]);
  const [accountCount, setAccountCount] = React.useState(0);
  const [query, setQuery] = React.useState("");
  const [sort, setSort] = React.useState("pinned");
  const [drawer, setDrawer] = React.useState({ open: false, acct: null });
  const [toast, setToast] = React.useState(null);
  const [lockIn, setLockIn] = React.useState(30 * 60);
  // Whether a Marvel Rivals API key is configured server-side. The key itself
  // is never returned by the API — only this boolean. The actual key lives in
  // vault.json's config and is sent to the server (POST /api/options) when set.
  const [hasApiKey, setHasApiKey] = React.useState(false);
  // marvelrivalsapi usage / rate-limit snapshot. Seeded from
  // GET /api/rivals/sync-status and refreshed from every refresh response.
  const [syncStatus, setSyncStatus] = React.useState(null);
  // Set of account ids currently refreshing, for spinner state. Plus a single
  // flag for the "Refresh all" action.
  const [refreshing, setRefreshing] = React.useState(() => new Set());
  const [refreshingAll, setRefreshingAll] = React.useState(false);
  const searchRef = React.useRef(null);
  const ready = phase === "ready";

  const showToast = (msg) => {
    setToast(msg);
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => setToast(null), 1600);
  };

  // Reset the client countdown — mirrors the server resetting last_activity
  // on every authenticated request. (item 5)
  const noteActivity = React.useCallback(() => {
    const m = tRef.current.lockoutMinutes;
    if (m > 0) setLockIn(m * 60);
  }, []);

  const onLocked = React.useCallback(() => {
    setPhase("unlock");
    setDrawer({ open: false, acct: null });
  }, []);

  // Authenticated request helper, bound to this component's lock/activity hooks.
  const api = React.useCallback(
    (path, opts) => apiCall(path, opts, onLocked, noteActivity),
    [onLocked, noteActivity]
  );

  const loadAccounts = React.useCallback(async () => {
    const data = await api("/api/accounts");
    const list = data.accounts || [];
    setAccounts(list);
    setAccountCount(list.length);
    return list;
  }, [api]);

  // Pull the server-side lockout setting into the client store. Server is
  // authoritative; this never POSTs back. (items 4b, 5)
  const syncLockout = React.useCallback((mins) => {
    if (mins == null) return;
    setTweak("lockoutMinutes", mins);
    setLockIn(mins > 0 ? mins * 60 : 0);
  }, [setTweak]);

  // Pull the API usage / rate-limit snapshot. Unauthenticated, fire-and-forget.
  const refreshSyncStatus = React.useCallback(() => {
    fetch("/api/rivals/sync-status")
      .then((r) => r.json())
      .then(setSyncStatus)
      .catch(() => { /* transient — the next refresh response carries it */ });
  }, []);

  // ── boot ──────────────────────────────────────────────────────────────────
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await fetch("/api/status").then((r) => r.json());
        if (cancelled) return;
        setAccountCount(s.account_count || 0);
        syncLockout(s.lockout_minutes);
        setHasApiKey(!!s.has_marvel_rivals_api_key);
        refreshSyncStatus();
        if (!s.initialized) { setPhase("init"); return; }
        if (!s.unlocked) { setPhase("unlock"); return; }
        setLockIn(s.lockout_minutes > 0 ? s.lock_in_s : 0);
        await loadAccounts();
        if (!cancelled) setPhase("ready");
      } catch {
        if (!cancelled) setPhase("unlock");
      }
    })();
    return () => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // After a successful init/unlock, resync state and enter the app.
  const enterReady = React.useCallback(async () => {
    try {
      const s = await fetch("/api/status").then((r) => r.json());
      syncLockout(s.lockout_minutes);
      setLockIn(s.lockout_minutes > 0 ? s.lock_in_s : 0);
      setHasApiKey(!!s.has_marvel_rivals_api_key);
    } catch { /* fall through with whatever we have */ }
    refreshSyncStatus();
    await loadAccounts();
    setPhase("ready");
  }, [loadAccounts, syncLockout, refreshSyncStatus]);

  const handleUnlock = async (password) => {
    const res = await fetch("/api/unlock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      return d.message || (d.error === "bad_password"
        ? "Wrong password. Try again."
        : "Couldn't unlock the vault.");
    }
    await enterReady();
    return null;
  };

  const handleInit = async (password) => {
    const res = await fetch("/api/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      return d.message || "Couldn't create the vault.";
    }
    await enterReady();
    return null;
  };

  const onLock = async () => {
    try { await fetch("/api/lock", { method: "POST" }); } catch { /* no-op */ }
    setPhase("unlock");
    setDrawer({ open: false, acct: null });
  };

  // ── liveness heartbeat ────────────────────────────────────────────────────
  // Ping the server every 30s so a launch with --auto-stop can tell when every
  // browser tab has closed and shut itself down. Harmless otherwise — the
  // server just records the timestamp. Runs in every phase (incl. the lock
  // screen), so the app counts as "open" even while locked.
  React.useEffect(() => {
    const ping = () => fetch("/api/heartbeat", { method: "POST" }).catch(() => {});
    ping();
    const id = setInterval(ping, 30000);
    return () => clearInterval(id);
  }, []);

  // ── document data attributes ──────────────────────────────────────────────
  React.useEffect(() => {
    document.documentElement.dataset.view = t.view;
    document.documentElement.dataset.theme = t.theme || "dark";
    // data-density always carries the *active* view's density — only one view
    // renders at a time, so the table/ladder/card density CSS can key off this
    // single attribute without also matching on the view.
    document.documentElement.dataset.density = t[DENSITY_KEY[t.view]] || "regular";
  }, [t.view, t.theme, t.densityCards, t.densityTable, t.densityLadder]);

  // ── lock countdown ────────────────────────────────────────────────────────
  React.useEffect(() => {
    if (!ready || t.lockoutMinutes === 0) return undefined;
    const id = setInterval(() => setLockIn((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(id);
  }, [ready, t.lockoutMinutes]);

  // When the countdown hits zero, confirm with the server before locking.
  React.useEffect(() => {
    if (!ready || t.lockoutMinutes === 0 || lockIn !== 0) return;
    fetch("/api/status").then((r) => r.json()).then((s) => {
      if (!s.unlocked) onLocked();
      else setLockIn(s.lockout_minutes > 0 ? s.lock_in_s : 0);
    }).catch(() => onLocked());
  }, [lockIn, ready, t.lockoutMinutes, onLocked]);

  // Periodic resync so the client countdown can't drift from the server, and
  // an external lock (server restart, /api/lock elsewhere) is picked up.
  React.useEffect(() => {
    if (!ready) return undefined;
    const id = setInterval(async () => {
      try {
        const s = await fetch("/api/status").then((r) => r.json());
        if (!s.unlocked) onLocked();
        else if (s.lockout_minutes > 0) setLockIn(s.lock_in_s);
      } catch { /* transient — try again next tick */ }
    }, 60000);
    return () => clearInterval(id);
  }, [ready, onLocked]);

  // Esc closes the drawer (preserving any draft).
  React.useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape" && drawer.open) setDrawer({ open: false, acct: null });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer.open]);

  // ── CRUD ──────────────────────────────────────────────────────────────────
  const onCopy = (text, field) => {
    if (!text) return;
    try { navigator.clipboard?.writeText(text); } catch { /* no-op */ }
    showToast(`Copied ${field}`);
  };

  const onOpen = (acct) => setDrawer({ open: true, acct });
  const onNew = () => setDrawer({ open: true, acct: { id: "", current_rank: "", peak_rank: "" } });

  // Save → POST /api/accounts (new) or PUT /api/accounts/:id (existing). (item 1)
  const onSave = async (next) => {
    const payload = {
      in_game_name: next.in_game_name || "",
      rivals_uid: next.rivals_uid || "",
      username: next.username || "",
      email: next.email || "",
      password: next.password || "",
      current_rank: next.current_rank || "",
      peak_rank: next.peak_rank || "",
      notes: next.notes || "",
      tag: next.tag || "",
      border_color: next.border_color || "",
      pinned: !!next.pinned,
      neon: !!next.neon,
      current_points: next.current_points,
      peak_points: next.peak_points,
    };
    try {
      if (next.id) {
        await api(`/api/accounts/${next.id}`, { method: "PUT", body: JSON.stringify(payload) });
      } else {
        await api("/api/accounts", { method: "POST", body: JSON.stringify(payload) });
      }
      await loadAccounts();
      setDrawer({ open: false, acct: null });
      showToast(next.id ? "Saved" : "Account created");
      return true;
    } catch (e) {
      if (!e.locked) showToast("Save failed — try again");
      return false;
    }
  };

  // Delete → DELETE /api/accounts/:id. (item 2)
  const onDelete = async (acct) => {
    if (!acct?.id) { setDrawer({ open: false, acct: null }); return true; }
    try {
      await api(`/api/accounts/${acct.id}`, { method: "DELETE" });
      await loadAccounts();
      setDrawer({ open: false, acct: null });
      showToast("Deleted");
      return true;
    } catch (e) {
      if (!e.locked) showToast("Delete failed — try again");
      return false;
    }
  };

  // Pin toggle → PUT /api/accounts/:id { pinned }. (item 3)
  const onPin = async (acct) => {
    if (!acct?.id) return;
    // Optimistic flip so the card responds instantly.
    setAccounts((arr) => arr.map((a) => a.id === acct.id ? { ...a, pinned: !a.pinned } : a));
    try {
      await api(`/api/accounts/${acct.id}`, {
        method: "PUT",
        body: JSON.stringify({ pinned: !acct.pinned }),
      });
      await loadAccounts();
    } catch (e) {
      if (!e.locked) {
        showToast("Couldn't update pin");
        loadAccounts().catch(() => {});
      }
    }
  };

  // Persist the auto-lock window to the server, then reset the countdown. (item 5)
  const changeLockout = async (mins) => {
    setTweak("lockoutMinutes", mins);
    setLockIn(mins > 0 ? mins * 60 : 0);
    try {
      await api("/api/options", {
        method: "POST",
        body: JSON.stringify({ lockout_minutes: mins }),
      });
    } catch (e) {
      if (!e.locked) showToast("Couldn't save that option");
    }
  };

  // Save the marvelrivalsapi.com key. Empty string clears it.
  const changeApiKey = async (rawKey) => {
    const key = (rawKey || "").trim();
    try {
      const res = await api("/api/options", {
        method: "POST",
        body: JSON.stringify({ marvel_rivals_api_key: key }),
      });
      setHasApiKey(!!res?.has_marvel_rivals_api_key);
      showToast(key ? "API key saved" : "API key cleared");
      return true;
    } catch (e) {
      if (!e.locked) showToast("Couldn't save API key");
      return false;
    }
  };

  // Apply a refreshed account record (from the API) over its row in the
  // accounts state, so we don't have to refetch the whole list.
  const mergeRefreshed = React.useCallback((updated) => {
    if (!updated || !updated.id) return;
    setAccounts((arr) => arr.map((a) => a.id === updated.id ? { ...a, ...updated } : a));
  }, []);

  const REFRESH_LABEL = {
    ok: "Refreshed",
    private: "Profile is private",
    not_found: "Player not found",
    bad_key: "API key rejected — check Options",
    missing_handle: "No in-game name set on this account",
    error: "Refresh failed",
  };

  // Refresh one account's stats from marvelrivalsapi.com.
  const onRefresh = async (acct) => {
    if (!acct?.id) return;
    if (!hasApiKey) {
      showToast("Set your Marvel Rivals API key in Options first");
      return;
    }
    setRefreshing((s) => { const n = new Set(s); n.add(acct.id); return n; });
    try {
      const res = await api(`/api/accounts/${acct.id}/refresh-stats`, { method: "POST" });
      mergeRefreshed(res?.account);
      if (res?.sync) setSyncStatus(res.sync);
      const st = res?.account?.last_refresh_status || "error";
      showToast(REFRESH_LABEL[st] || "Done");
    } catch (e) {
      if (!e.locked) showToast("Refresh failed — try again");
    } finally {
      setRefreshing((s) => { const n = new Set(s); n.delete(acct.id); return n; });
    }
  };

  // Refresh every account in series (server adds a polite delay between calls).
  const onRefreshAll = async () => {
    if (!hasApiKey) {
      showToast("Set your Marvel Rivals API key in Options first");
      return;
    }
    if (refreshingAll) return;
    setRefreshingAll(true);
    try {
      const res = await api("/api/accounts/refresh-all", { method: "POST" });
      for (const a of (res?.accounts || [])) mergeRefreshed(a);
      if (res?.sync) setSyncStatus(res.sync);
      const sum = res?.summary || {};
      const ok = sum.ok || 0;
      const issues = (sum.private || 0) + (sum.not_found || 0)
                   + (sum.error || 0) + (sum.bad_key || 0) + (sum.missing_handle || 0);
      showToast(issues
        ? `Refreshed ${ok} · ${issues} skipped`
        : `Refreshed ${ok} ${ok === 1 ? "account" : "accounts"}`);
    } catch (e) {
      if (!e.locked) showToast("Refresh-all failed — try again");
    } finally {
      setRefreshingAll(false);
    }
  };

  // ── derived ───────────────────────────────────────────────────────────────
  const effectiveSort = t.view === "ladder" ? "current_desc" : sort;
  const visible = React.useMemo(
    () => filterAccounts(sortAccounts(accounts, effectiveSort), query),
    [accounts, effectiveSort, query]
  );

  const lockInLabel = (() => {
    const m = Math.floor(lockIn / 60), s = lockIn % 60;
    return m >= 1 ? `${m}m` : `${s}s`;
  })();

  const sortLabel = (SORT_OPTIONS.find((o) => o.value === sort) || SORT_OPTIONS[0]).label.toLowerCase();
  const opts = { hideDetails: t.hideDetails, hideCopy: t.hideCopy };

  // ── render ────────────────────────────────────────────────────────────────
  if (phase === "loading") {
    return (
      <div className="lock-screen">
        <div className="lock-bg" aria-hidden="true" />
        <main className="lock-main">
          <div className="lock-eyebrow">RIVALS · ACCOUNT VAULT</div>
          <p className="lock-sub">Loading…</p>
        </main>
      </div>
    );
  }

  if (phase === "init" || phase === "unlock") {
    return (
      <LockScreen
        mode={phase}
        accountCount={accountCount}
        onSubmit={phase === "init" ? handleInit : handleUnlock} />
    );
  }

  return (
    <>
      <div className="app-shell">
        <Header
          count={accounts.length}
          lockIn={lockInLabel}
          lockoutMinutes={t.lockoutMinutes}
          onLock={onLock}
          theme={t.theme}
          onToggleTheme={() => setTweak("theme", t.theme === "light" ? "dark" : "light")}
          onSettings={() => window.postMessage({ type: "__activate_edit_mode" }, "*")} />


        <main className="app-main">
          <Toolbar
            ref={searchRef}
            query={query} onQuery={setQuery}
            sort={sort} onSort={setSort}
            view={t.view} onView={(v) => setTweak("view", v)}
            onNew={onNew}
            onRefreshAll={onRefreshAll}
            refreshingAll={refreshingAll}
            hasApiKey={hasApiKey}
            accountsCount={accounts.length} />


          <div className="app-body">
            <InfoRail open={t.infoRailOpen}
                      onToggle={() => setTweak("infoRailOpen", !t.infoRailOpen)} />
            <div className="app-content">
              {visible.length === 0 ?
              <div className="app-empty">
                  {accounts.length === 0 ?
                <><b>No accounts yet.</b> Add one with “New account”.</> :
                <><b>No matches.</b> Try a different search.</>}
                </div> :
              t.view === "table" ?
              <TableView accounts={visible} opts={opts}
              onOpen={onOpen} onCopy={onCopy} onPin={onPin}
              onRefresh={onRefresh} refreshingIds={refreshing} hasApiKey={hasApiKey}
              sortLabel={sortLabel} /> :
              t.view === "ladder" ?
              <LadderView accounts={visible} opts={opts}
              onOpen={onOpen} onCopy={onCopy} onPin={onPin}
              onRefresh={onRefresh} refreshingIds={refreshing} hasApiKey={hasApiKey} /> :

              <div className="app-grid">
                  {visible.map((a) =>
                <CardRefined key={a.id} acct={a} opts={opts}
                onOpen={onOpen} onCopy={onCopy} onPin={onPin}
                onRefresh={onRefresh} refreshing={refreshing.has(a.id)} hasApiKey={hasApiKey} />
                )}
                </div>
              }
            </div>
          </div>
        </main>

        {toast && <div className="app-toast">{toast}</div>}

        <footer className="app-foot">
          <span>Created by Yui</span>
          <span className="app-foot-sep">·</span>
          <a className="app-foot-link"
             href="https://github.com/itsnotyuiiii"
             target="_blank"
             rel="noopener noreferrer">
            <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
              <path fill="currentColor" d="M8 0a8 8 0 0 0-2.53 15.59c.4.07.55-.17.55-.38v-1.34c-2.22.48-2.69-1.07-2.69-1.07-.36-.92-.89-1.17-.89-1.17-.73-.5.05-.49.05-.49.81.06 1.23.83 1.23.83.72 1.22 1.87.87 2.33.66.07-.52.28-.87.5-1.07-1.77-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.13 0 0 .67-.21 2.2.82A7.6 7.6 0 0 1 8 3.82c.68 0 1.36.09 2 .27 1.52-1.03 2.2-.82 2.2-.82.44 1.11.16 1.93.08 2.13.51.56.82 1.28.82 2.15 0 3.07-1.87 3.74-3.65 3.94.29.25.54.73.54 1.48v2.2c0 .21.15.46.55.38A8 8 0 0 0 8 0Z"/>
            </svg>
            github.com/itsnotyuiiii
          </a>
        </footer>

        <Drawer
          open={drawer.open}
          acct={drawer.acct}
          view={t.view}
          onClose={() => setDrawer({ open: false, acct: null })}
          onSave={onSave}
          onDelete={onDelete} />

      </div>

      <TweaksPanel title="Options">
        <TweakSection label="Layout">
          <TweakRadio
            label="View"
            value={t.view}
            options={[
            { value: "cards", label: "Cards" },
            { value: "table", label: "Table" },
            { value: "ladder", label: "Ladder" }]
            }
            onChange={(v) => setTweak("view", v)} />

          <TweakRadio
            label={"Density · " + DENSITY_VIEW_LABEL[t.view]}
            value={t[DENSITY_KEY[t.view]]}
            options={["compact", "regular", "comfy"]}
            onChange={(v) => setTweak(DENSITY_KEY[t.view], v)} />

        </TweakSection>

        <TweakSection label="Cards">
          <TweakToggle label="Hide username & email" value={t.hideDetails}
          onChange={(v) => setTweak("hideDetails", v)} />
          <TweakToggle label="Hide copy buttons" value={t.hideCopy}
          onChange={(v) => setTweak("hideCopy", v)} />
        </TweakSection>

        <TweakSection label="Security">
          <TweakSelect
            label="Auto-lock after"
            value={t.lockoutMinutes}
            options={LOCKOUT_OPTIONS}
            onChange={(v) => changeLockout(Number(v))} />
        </TweakSection>

        <TweakSection label="Marvel Rivals stats">
          <ApiKeyControl hasKey={hasApiKey} onSave={changeApiKey} />
          <SyncStatus sync={syncStatus} hasKey={hasApiKey} />
          <p className="sync-status-note">
            Status-icon key moved to the info rail on the left of the accounts list.
          </p>
        </TweakSection>
      </TweaksPanel>
    </>);

}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
