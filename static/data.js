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

// Tier-family theme. Palette matches the in-game / tracker.gg rank colors:
//   Bronze brown · Silver cool-grey · Gold yellow · Platinum cyan ·
//   Diamond saturated blue · Grandmaster purple · Celestial ORANGE-amber ·
//   Eternity magenta · One Above All red.
// (Celestial used to be styled pink; that was wrong — the in-game gradient
// is orange/amber. Eternity used to be gold; in-game it's pink/magenta.)
window.RANK_THEME = {
  Bronze:        { fg: "#c08a5b", glow: "#7a4a1f", accent: "#e0a16a" },
  Silver:        { fg: "#a8b6c2", glow: "#5a6f7d", accent: "#cad6e0" },
  Gold:          { fg: "#ffc83d", glow: "#8a5e10", accent: "#ffd870" },
  Platinum:      { fg: "#58e1e8", glow: "#1c6a70", accent: "#9ff0f5" },
  Diamond:       { fg: "#1680ff", glow: "#0a3c80", accent: "#62a8ff" },
  Grandmaster:   { fg: "#9e4bff", glow: "#4a1e88", accent: "#c590ff" },
  Celestial:     { fg: "#fe5a1d", glow: "#8a2d05", accent: "#ff8a5b" },
  Eternity:      { fg: "#eb46ff", glow: "#7a1888", accent: "#f48aff" },
  "One Above All": { fg: "#ff3344", glow: "#7d121b", accent: "#ff7a85" },
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
