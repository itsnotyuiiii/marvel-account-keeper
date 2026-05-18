// Rank metadata for the tracker. Account data is loaded live from the Flask
// backend (GET /api/accounts) — there is no seed data here.

window.RANK_TIERS = [
  "Bronze III", "Bronze II", "Bronze I",
  "Silver III", "Silver II", "Silver I",
  "Gold III", "Gold II", "Gold I",
  "Platinum III", "Platinum II", "Platinum I",
  "Diamond III", "Diamond II", "Diamond I",
  "Grandmaster III", "Grandmaster II", "Grandmaster I",
  "Celestial III", "Celestial II", "Celestial I",
  "Eternity",
  "One Above All",
];

// Tier-family theme. Tuned for a dark background; same hue family across
// aesthetics so the rank reads at a glance regardless of mode.
window.RANK_THEME = {
  Bronze:        { fg: "#c08a5b", glow: "#7a4a1f", accent: "#e0a16a" },
  Silver:        { fg: "#c5cdd9", glow: "#5a6373", accent: "#dbe2ec" },
  Gold:          { fg: "#e8b94a", glow: "#7d5e10", accent: "#f0c863" },
  Platinum:      { fg: "#7fd9c8", glow: "#1f6a5e", accent: "#a3ecdd" },
  Diamond:       { fg: "#7aa6ff", glow: "#1c3a82", accent: "#a4c2ff" },
  Grandmaster:   { fg: "#b067ff", glow: "#4a1e88", accent: "#cf95ff" },
  Celestial:     { fg: "#ff7adb", glow: "#7d1c63", accent: "#ffa3e6" },
  Eternity:      { fg: "#ffce5c", glow: "#7d5a10", accent: "#ffe093" },
  "One Above All": { fg: "#ff5560", glow: "#7d121b", accent: "#ff8a90" },
};

window.RANK_INDEX = Object.fromEntries(window.RANK_TIERS.map((r, i) => [r, i]));

window.tierOf = (rank) => {
  if (!rank) return null;
  if (rank === "One Above All") return "One Above All";
  return rank.split(" ")[0];
};

window.themeFor = (rank) => window.RANK_THEME[window.tierOf(rank)] || null;

// Relative time. Accepts epoch milliseconds OR epoch seconds — the Flask
// backend stores updated_at as integer seconds, so anything that looks like
// a seconds-scale value is scaled up before formatting.
window.fmtRelative = (ts) => {
  if (ts == null) return "—";
  const ms = ts < 1e12 ? ts * 1000 : ts;
  const s = Math.max(1, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};
