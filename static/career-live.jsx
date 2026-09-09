// Own-account NetEase snapshot. No session token reaches this component.
window.NetEaseCareerPanel = function NetEaseCareerPanel({ request }) {
  const [status, setStatus] = React.useState(null);
  const [snapshot, setSnapshot] = React.useState(null);
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [season, setSeason] = React.useState(null);
  React.useEffect(() => {
    let active = true;
    request("/api/career/status").then((value) => {
      if (active) { setStatus(value); setSnapshot(value.snapshot || null); setSeason(value.snapshot?.season ?? value.season ?? null); if (value.message) setError(value.message); }
    }).catch((e) => {
      if (active && !e.locked) { setStatus({ configured: true }); setError(e.data?.message || "Career status is unavailable."); }
    });
    return () => { active = false; };
  }, [request]);
  if (!status?.configured) return null;
  async function refresh() {
    setBusy(true); setError("");
    try { setSnapshot(await request("/api/career/refresh", { method: "POST", body: JSON.stringify({ season }) })); }
    catch (e) { if (!e.locked) setError(e.data?.message || "The Career read failed. Your saved accounts were not changed."); }
    finally { setBusy(false); }
  }
  const date = (value) => value ? new Date(value * 1000).toLocaleString() : "Unknown";
  return <section className="career-live" aria-label="Live NetEase Career">
    <div className="career-live-bar">
      <button type="button" className="career-live-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <b>NetEase connection</b><span>Shared card lookup · optional own-Career explorer</span>
        <span aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
    </div>
    {open && <div className="career-live-content">
      <p className="career-live-note">Use the normal Add account button, enter a UID or exact IGN, and save. The shared NetEase connection supports public PC Career stats for other players without per-player local login or extra linking. This optional explorer shows only the connection’s own Career and recent matches ({status.uid || "not connected"}).</p>
      <div className="career-live-actions">
        <p>NetEase-only mode. Normal card and bulk refresh load identity, available ranks and public PC Career stats. Private or unavailable stats are labeled separately. This own-player explorer never changes cards.</p>
        {status.seasons?.length > 0 && <label>Game season ID <select value={season ?? ""} disabled={busy} onChange={(e) => setSeason(Number(e.target.value))}>
          {status.seasons.map((value) => <option value={value} key={value}>{value}</option>)}
        </select></label>}
        <button type="button" className="btn primary" disabled={busy || !status.uid} onClick={refresh}>
          {busy ? "Reading NetEase…" : "Refresh from NetEase"}
        </button>
      </div>
      <p className="career-live-note">Session expiry: {date(status.expires_at)}. Refresh does not need administrator access. Reconnecting an expired session requires a fresh approved capture.</p>
      {error && <p role="alert" className="career-live-error">{error}</p>}
      {!snapshot && !error && <p className="career-live-note">Click Refresh to load the linked account’s Career and up to 50 recent matches.</p>}
      {snapshot && <>
        {snapshot.ranks && <div className="career-rank-summary">
          <h3>Rank · connected season {snapshot.ranks.current_season}</h3>
          {snapshot.ranks.current ? <p>
            {snapshot.ranks.current.qualification === "estimated" ? "Estimated: " : "Current: "}
            <b>{snapshot.ranks.current.rank || "Unranked"} · {snapshot.ranks.current.points}</b>
            {snapshot.ranks.current.qualification === "estimated" && ` · Placements ${snapshot.ranks.current.placements_completed ?? "?"}/10`}
          </p> : <p>Current rank unavailable. Saved current rank is kept.</p>}
          {snapshot.ranks.peak && <p>Returned career peak: <b>{snapshot.ranks.peak.peak_rank} · {snapshot.ranks.peak.peak_points}</b> · season {snapshot.ranks.peak.season}</p>}
          <p className="career-live-note">Rank stays on the connected current season even when you explore an older stats season. Estimates are not final placement ranks.</p>
        </div>}
        {snapshot.rank_error && <p role="status" className="career-live-note">{snapshot.rank_error}</p>}
        <h3>Game season ID {snapshot.season ?? "unspecified"} · {String(snapshot.platform).toUpperCase()} · all modes</h3>
        <div className="career-live-stats">
          {[["Matches", "matches"], ["Wins", "wins"], ["Kills", "kills"], ["Deaths", "deaths"], ["Assists", "assists"]].map(([label, key]) =>
            <div key={key}><span>{label}</span><strong>{snapshot.profile[key].toLocaleString()}</strong></div>)}
        </div>
        <p className="career-live-note">The API uses internal season IDs. These totals are season-specific; zeros do not mean the account has never played. Read {date(snapshot.fetched_at)}.</p>
        <h3>Recent matches · {snapshot.recent_matches.length} returned</h3>
        {snapshot.match_error && <p role="status" className="career-live-note">{snapshot.match_error}</p>}
        <p className="career-live-note">History may include earlier seasons. Only the linked player’s statistics are shown; nothing is imported into saved match history automatically.</p>
        <div className="career-live-table-wrap"><table className="career-live-table">
          <thead><tr><th>Played</th><th>Season</th><th>Mode ID</th><th>Result</th><th>K / D / A</th><th>Duration</th></tr></thead>
          <tbody>{snapshot.recent_matches.map((match, index) => <tr key={`${match.played_at}-${index}`}>
            <td>{date(match.played_at)}</td><td>{match.season}</td><td>{match.mode_id}</td>
            <td>{match.result}</td><td>{match.kills} / {match.deaths} / {match.assists}</td>
            <td>{Math.floor(match.duration_seconds / 60)}:{String(match.duration_seconds % 60).padStart(2, "0")}</td>
          </tr>)}</tbody>
        </table></div>
      </>}
    </div>}
  </section>;
};
