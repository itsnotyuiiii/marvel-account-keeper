// Own-account NetEase snapshot. No session token reaches this component.
function NativeNetEaseConnection({ request, status, setStatus }) {
  const [open, setOpen] = React.useState(!status.connected);
  const [persist, setPersist] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const active = React.useRef(true);
  React.useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const connecting = status.connection_in_progress === true;
  React.useEffect(() => {
    if (!connecting) return;
    let current = true;
    let timer;
    const poll = async () => {
      try {
        const next = await request("/api/career/status");
        if (current) { setStatus(next); setError(""); }
      } catch (e) {
        if (current && !e.locked) setError(e.data?.message || "Connection status is unavailable. You can check again.");
      } finally {
        if (current) timer = window.setTimeout(poll, 2000);
      }
    };
    timer = window.setTimeout(poll, 2000);
    return () => { current = false; window.clearTimeout(timer); };
  }, [connecting, request, setStatus]);
  const pending = ["candidate_pending", "candidate_verifying", "candidate_recovery_required"].includes(status.state);
  async function action(path) {
    if (busy || connecting) return;
    setBusy(true); setError("");
    try {
      if (path) await request(path, { method: "POST", body: JSON.stringify(path === "/api/netease/connect" ? { persist } : {}) });
      const next = await request("/api/career/status");
      if (active.current) setStatus(next);
    } catch (e) {
      if (active.current && !e.locked) setError(e.data?.message || "NetEase connection could not complete. Saved accounts were not changed.");
    } finally { if (active.current) setBusy(false); }
  }
  return <section className="career-live" aria-label="Native NetEase connection">
    <div className="career-live-bar"><button type="button" className="career-live-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      <b>NetEase connection</b><span>{connecting ? "Login in progress" : status.connected ? "Login context configured" : "Connect once for PC UID lookups"}</span>
      <span aria-hidden="true">{open ? "−" : "+"}</span>
    </button></div>
    {open && <div className="career-live-content">
      <p className="career-live-note">Connect through NetEase's normal SDK login. No game capture or administrator access is needed. Then add a PC UID or refresh a saved card to load its available profile. In-game-name search is not supported by this connection yet.</p>
      <p className="career-live-note">One authorized owner connection serves other public PC profiles; those players do not need to log in on this computer. Each profile's visibility settings still apply.</p>
      {status.message && <p role="status" className="career-live-note">{status.message}</p>}
      <p className="career-live-note">{status.persistence === "encrypted_local"
        ? "Session saved encrypted on this computer. The encryption key is managed locally; profile refresh verifies the owner again."
        : status.persistence === "memory" ? "Session held in memory only until the vault locks or the app closes. No session was saved for the next launch."
        : "By default, the session stays in memory only until the vault locks or the app closes."}
        {status.connected && " Configured login context does not by itself prove a fresh owner authentication; the next profile read checks it."}</p>
      {status.can_connect && !status.connected && <label className="drawer-pin-row">
        <input type="checkbox" checked={persist} disabled={busy || connecting || !status.can_connect} onChange={(event) => setPersist(event.target.checked)} />
        Save the NetEase session encrypted locally for future launches (optional)
      </label>}
      <div className="career-live-actions">
        {status.can_connect && <button type="button" className="btn primary" disabled={busy || connecting} onClick={() => action("/api/netease/connect")}>
          {connecting ? "Complete NetEase login…" : "Connect NetEase"}
        </button>}
        {pending && <button type="button" className="btn primary" disabled={busy || connecting} onClick={() => action("/api/netease/recover")}>Recover saved session</button>}
        <button type="button" className="btn" disabled={busy || connecting} onClick={() => action(null)}>{busy ? "Checking…" : "Check connection"}</button>
      </div>
      {connecting && <p role="status" className="career-live-note">Finish the normal NetEase sign-in in the opened login window. This panel checks the connection automatically.</p>}
      {!status.can_connect && !status.connected && !connecting && !status.message && <p className="career-live-note">A new connection cannot be started from this view. Use the native Marvel Tracker window, or recover the existing session when offered.</p>}
      {error && <p role="alert" className="career-live-error">{error}</p>}
    </div>}
  </section>;
}

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
  if (status.provider === "native") return <NativeNetEaseConnection request={request} status={status} setStatus={setStatus} />;
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
