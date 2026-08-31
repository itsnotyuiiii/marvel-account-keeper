// Local, owner-authorized match-history UI.
//
// Match data is deliberately fetched only after this section is expanded. It
// never rides along with the normal account list and never shares rank-refresh
// state. The backend is responsible for normalization, deduplication, and for
// discarding unrelated participant identities before commit.

const MATCH_PLATFORM_OPTIONS = [
  { value: "unknown", label: "Unknown" },
  { value: "pc", label: "PC" },
  { value: "playstation", label: "PlayStation" },
  { value: "xbox", label: "Xbox" },
];
const MATCH_API_PAGE_SIZE = 500;
const MATCH_RENDER_BATCH = 100;

function matchPlatformLabel(value) {
  const found = MATCH_PLATFORM_OPTIONS.find((row) => row.value === value);
  return found ? found.label : (value || "Unknown");
}

function matchHistoryError(error, fallback) {
  if (error?.locked) return "Vault locked.";
  const backendError = error?.data?.error;
  return error?.data?.message
    || (typeof backendError === "string" ? backendError : backendError?.message)
    || (error?.status ? `Request failed (HTTP ${error.status}).` : "")
    || error?.message
    || fallback;
}

function formatMatchDate(value) {
  if (!value) return "Date unavailable";
  const numeric = typeof value === "number"
    ? value
    : (/^\d{9,12}$/.test(String(value)) ? Number(value) : null);
  const date = numeric == null ? new Date(value) : new Date(numeric < 1e12 ? numeric * 1000 : numeric);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  });
}

function formatMatchDuration(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}:${String(secs).padStart(2, "0")}`;
}

function matchRowIdentity(match) {
  if (match?.match_key) return `key:${match.match_key}`;
  if (match?.id) return `id:${match.id}`;
  const fact = match?.account_fact || match?.fact || match || {};
  return "fallback:" + JSON.stringify([
    match?.occurred_at || match?.timestamp || match?.match_time || match?.started_at || "",
    match?.platform || fact.platform || "",
    match?.season || fact.season || "",
    match?.mode || fact.mode || "",
    match?.map_name || match?.map || fact.map_name || fact.map || "",
    fact.evidence_kind || match?.evidence_kind || "direct",
  ]);
}

function appendUniqueMatches(current, incoming) {
  const seen = new Set(current.map(matchRowIdentity));
  const merged = current.slice();
  for (const match of incoming) {
    const key = matchRowIdentity(match);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(match);
  }
  return merged;
}

function emptyManualMatch(platform) {
  return {
    occurred_at: "",
    platform: platform && platform !== "unknown" ? platform : "unknown",
    season: "",
    mode: "",
    map_name: "",
    result: "",
    duration_seconds: "",
    hero_name: "",
    kills: "",
    deaths: "",
    assists: "",
    rank_at_match: "",
  };
}

function normalizedManualMatch(manual) {
  const numberOrUndefined = (value) => {
    if (value === "" || value == null) return undefined;
    const n = Number(value);
    return Number.isFinite(n) ? n : undefined;
  };
  let occurredAt = manual.occurred_at;
  if (occurredAt) {
    const parsed = new Date(occurredAt);
    if (!Number.isNaN(parsed.getTime())) occurredAt = parsed.toISOString();
  }
  return {
    occurred_at: occurredAt,
    platform: manual.platform,
    season: manual.season.trim(),
    mode: manual.mode.trim(),
    map_name: manual.map_name.trim(),
    result: manual.result,
    duration_seconds: numberOrUndefined(manual.duration_seconds),
    hero_name: manual.hero_name.trim(),
    kills: numberOrUndefined(manual.kills),
    deaths: numberOrUndefined(manual.deaths),
    assists: numberOrUndefined(manual.assists),
    rank_at_match: manual.rank_at_match.trim(),
  };
}

function PreviewCounts({ value, title = "Import preview", previewOnly = true, containerRef = null }) {
  if (!value) return null;
  const accounts = Array.isArray(value.accounts) ? value.accounts : [];
  const direct = Number(value.direct_count ?? value.direct_fact_count ?? 0);
  const inferred = Number(value.inferred_count ?? value.inferred_fact_count ?? 0);
  const duplicateFacts = Number(value.duplicate_fact_count ?? value.duplicate_count ?? 0);
  const duplicateMatches = Number(value.duplicate_match_count ?? 0);
  const rejected = Number(value.rejected_count ?? value.rejected_match_count ?? 0);
  const dateFrom = value.date_from || value.started_at_min;
  const dateTo = value.date_to || value.started_at_max;
  const digest = value.digest || value.source_digest;
  return (
    <div ref={containerRef} className="mh-preview" role="status" aria-live="polite"
         tabIndex={containerRef ? -1 : undefined}>
      <div className="mh-preview-head">
        <strong>{title}</strong>
        {value.source_kind && <span>{String(value.source_kind).replaceAll("_", " ")}</span>}
      </div>
      <div className="mh-count-grid">
        <div><b>{direct}</b><span>direct</span></div>
        <div><b>{inferred}</b><span>inferred owned</span></div>
        <div><b>{duplicateFacts}</b><span>duplicate facts</span></div>
        <div><b>{duplicateMatches}</b><span>duplicate input matches</span></div>
        <div><b>{rejected}</b><span>rejected</span></div>
      </div>
      {(dateFrom || dateTo) && (
        <p className="mh-preview-range">
          {formatMatchDate(dateFrom)} — {formatMatchDate(dateTo)}
        </p>
      )}
      {digest && (
        <p className="mh-digest" title={digest}>Source digest {String(digest).slice(0, 12)}…</p>
      )}
      {accounts.length > 0 && (
        <ul className="mh-preview-accounts" aria-label="Affected owned accounts">
          {accounts.map((account, index) => (
            <li key={account.account_id || account.id || index}>
              <span>{account.in_game_name || account.account_name || account.account_id || "Saved account"}</span>
              <small>
                {Number(account.direct_count || 0)} direct · {Number(account.inferred_count || 0)} inferred
              </small>
            </li>
          ))}
        </ul>
      )}
      {previewOnly && <p className="mh-preview-note">Preview only — nothing has been stored yet.</p>}
    </div>
  );
}

function MatchEvidenceLabel({ kind }) {
  const inferred = kind === "inferred_owned_account" || kind === "inferred";
  return (
    <span className={"mh-evidence " + (inferred ? "mh-evidence-inferred" : "mh-evidence-direct")}>
      {inferred ? "Inferred owned account" : "Direct"}
    </span>
  );
}

function MatchRow({ match }) {
  const fact = match.account_fact || match.fact || match;
  const occurredAt = match.occurred_at || match.timestamp || match.match_time || match.started_at;
  const mapName = match.map_name || match.map || fact.map_name || fact.map;
  const hero = fact.hero_name || fact.hero;
  const rank = fact.rank_at_match || fact.rank;
  const duration = formatMatchDuration(match.duration_seconds || match.duration || fact.duration_seconds);
  const evidence = fact.evidence_kind || match.evidence_kind || "direct";
  const kda = [fact.kills, fact.deaths, fact.assists]
    .some((value) => value !== undefined && value !== null && value !== "")
    ? `${fact.kills ?? "–"}/${fact.deaths ?? "–"}/${fact.assists ?? "–"} K/D/A`
    : "";
  const details = [hero, rank, kda, duration].filter(Boolean);
  return (
    <article className="mh-match-row">
      <div className="mh-match-top">
        <time dateTime={occurredAt || undefined}>{formatMatchDate(occurredAt)}</time>
        <MatchEvidenceLabel kind={evidence} />
      </div>
      <div className="mh-match-title">
        <strong>{match.mode || fact.mode || "Match"}</strong>
        {mapName && <span>· {mapName}</span>}
        {(match.result || fact.result) && (
          <span className={`mh-result mh-result-${String(match.result || fact.result).toLowerCase()}`}>
            {match.result || fact.result}
          </span>
        )}
      </div>
      <div className="mh-match-meta">
        <span>{matchPlatformLabel(match.platform || fact.platform)}</span>
        {(match.season || fact.season) && <span>Season {match.season || fact.season}</span>}
        {details.map((detail, index) => <span key={index}>{detail}</span>)}
      </div>
    </article>
  );
}

function ManualMatchForm({ value, onChange, accountPlatform }) {
  const set = (key, next) => onChange({ ...value, [key]: next });
  return (
    <div className="mh-manual-grid">
      <label className="mh-field mh-field-wide">
        <span>Played at</span>
        <input type="datetime-local" value={value.occurred_at}
               onChange={(event) => set("occurred_at", event.target.value)} required />
      </label>
      <label className="mh-field">
        <span>Platform</span>
        <select value={value.platform} onChange={(event) => set("platform", event.target.value)}>
          <option value="unknown" disabled>Choose platform</option>
          {MATCH_PLATFORM_OPTIONS.filter((row) => row.value !== "unknown").map((row) => (
            <option key={row.value} value={row.value}>{row.label}</option>
          ))}
        </select>
        {accountPlatform !== "unknown" && value.platform !== accountPlatform && (
          <small>Must match this account’s saved platform.</small>
        )}
      </label>
      <label className="mh-field">
        <span>Season</span>
        <input value={value.season} onChange={(event) => set("season", event.target.value)}
               placeholder="e.g. 4.5" />
      </label>
      <label className="mh-field">
        <span>Mode</span>
        <input value={value.mode} onChange={(event) => set("mode", event.target.value)}
               placeholder="Competitive" />
      </label>
      <label className="mh-field">
        <span>Map</span>
        <input value={value.map_name} onChange={(event) => set("map_name", event.target.value)} />
      </label>
      <label className="mh-field">
        <span>Result</span>
        <select value={value.result} onChange={(event) => set("result", event.target.value)}>
          <option value="">Unknown</option>
          <option value="win">Win</option>
          <option value="loss">Loss</option>
          <option value="draw">Draw</option>
        </select>
      </label>
      <label className="mh-field">
        <span>Duration (seconds)</span>
        <input type="number" min="0" step="1" value={value.duration_seconds}
               onChange={(event) => set("duration_seconds", event.target.value)} />
      </label>
      <label className="mh-field">
        <span>Hero</span>
        <input value={value.hero_name} onChange={(event) => set("hero_name", event.target.value)} />
      </label>
      <label className="mh-field">
        <span>Rank at match</span>
        <input value={value.rank_at_match}
               onChange={(event) => set("rank_at_match", event.target.value)} />
      </label>
      {["kills", "deaths", "assists"].map((field) => (
        <label className="mh-field mh-field-stat" key={field}>
          <span>{field[0].toUpperCase() + field.slice(1)}</span>
          <input type="number" min="0" step="1" value={value[field]}
                 onChange={(event) => set(field, event.target.value)} />
        </label>
      ))}
    </div>
  );
}

function MatchHistoryPanel({
  account,
  configuredPlatform = "unknown",
  authorized = false,
  configurationDirty = false,
  request,
  showToast,
}) {
  const accountId = account?.id || "";
  const accountUid = String(account?.rivals_uid || "").trim();
  const [expanded, setExpanded] = React.useState(false);
  const [status, setStatus] = React.useState(null);
  const [matches, setMatches] = React.useState([]);
  const [total, setTotal] = React.useState(0);
  const [hasMore, setHasMore] = React.useState(false);
  const [nextOffset, setNextOffset] = React.useState(0);
  const [loading, setLoading] = React.useState(false);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [loadError, setLoadError] = React.useState("");
  const [loadMoreError, setLoadMoreError] = React.useState("");
  const [seasonInput, setSeasonInput] = React.useState("");
  const [seasonFilter, setSeasonFilter] = React.useState("");
  const [platformFilter, setPlatformFilter] = React.useState("");
  const [knownSeasons, setKnownSeasons] = React.useState([]);
  const [displayLimit, setDisplayLimit] = React.useState(MATCH_RENDER_BATCH);
  const [reloadToken, setReloadToken] = React.useState(0);
  const [sourceKind, setSourceKind] = React.useState("file");
  const [file, setFile] = React.useState(null);
  const [manual, setManual] = React.useState(() => emptyManualMatch(configuredPlatform));
  const [attested, setAttested] = React.useState(false);
  const [preview, setPreview] = React.useState(null);
  const [previewSource, setPreviewSource] = React.useState(null);
  const [previewing, setPreviewing] = React.useState(false);
  const [importing, setImporting] = React.useState(false);
  const [operationError, setOperationError] = React.useState("");
  const [importSummary, setImportSummary] = React.useState(null);
  const [confirmingClear, setConfirmingClear] = React.useState(false);
  const [clearing, setClearing] = React.useState(false);
  const fileInputRef = React.useRef(null);
  const historyToggleRef = React.useRef(null);
  const previewSummaryRef = React.useRef(null);
  const importSummaryRef = React.useRef(null);
  const previewButtonRef = React.useRef(null);
  const clearTriggerRef = React.useRef(null);
  const clearKeepRef = React.useRef(null);
  const pagingGenerationRef = React.useRef(0);
  const seasonListId = React.useId();
  const sourceRadioName = `match-source-${React.useId()}`;

  const focusAfterRender = (ref) => {
    window.requestAnimationFrame(() => ref.current?.focus());
  };

  const matchListPath = (offset) => {
    const query = new URLSearchParams();
    query.set("limit", String(MATCH_API_PAGE_SIZE));
    query.set("offset", String(offset));
    if (seasonFilter) query.set("season", seasonFilter);
    if (platformFilter) query.set("platform", platformFilter);
    return `/api/accounts/${encodeURIComponent(accountId)}/matches?${query}`;
  };

  const resetLoadedPages = () => {
    pagingGenerationRef.current += 1;
    setMatches([]);
    setTotal(0);
    setHasMore(false);
    setNextOffset(0);
    setDisplayLimit(MATCH_RENDER_BATCH);
    setLoading(false);
    setLoadingMore(false);
    setLoadError("");
    setLoadMoreError("");
  };

  React.useEffect(() => {
    setExpanded(false);
    setStatus(null);
    resetLoadedPages();
    setLoadError("");
    setSeasonInput("");
    setSeasonFilter("");
    setPlatformFilter("");
    setKnownSeasons([]);
    setSourceKind("file");
    setFile(null);
    setManual(emptyManualMatch(configuredPlatform));
    setAttested(false);
    setPreview(null);
    setPreviewSource(null);
    setOperationError("");
    setImportSummary(null);
    setConfirmingClear(false);
  }, [accountId]); // account switch resets all transient history state

  React.useEffect(() => {
    const timer = window.setTimeout(() => setSeasonFilter(seasonInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [seasonInput]);

  React.useEffect(() => {
    if (manual.platform === "unknown" && configuredPlatform !== "unknown") {
      setManual((current) => ({ ...current, platform: configuredPlatform }));
    }
  }, [configuredPlatform, manual.platform]);

  React.useEffect(() => {
    if (!expanded || !accountId) return undefined;
    let cancelled = false;
    const generation = pagingGenerationRef.current + 1;
    pagingGenerationRef.current = generation;
    setMatches([]);
    setTotal(0);
    setHasMore(false);
    setNextOffset(0);
    setDisplayLimit(MATCH_RENDER_BATCH);
    setLoadingMore(false);
    setLoadMoreError("");
    setLoading(true);
    setLoadError("");
    Promise.all([
      request(`/api/accounts/${encodeURIComponent(accountId)}/matches/status`),
      request(matchListPath(0)),
    ]).then(([statusResponse, listResponse]) => {
      if (cancelled || generation !== pagingGenerationRef.current) return;
      const rows = Array.isArray(listResponse?.matches) ? listResponse.matches : [];
      const pageOffset = Number(listResponse?.offset ?? 0);
      const consumed = rows.length || (listResponse?.has_more ? Number(listResponse?.limit || MATCH_API_PAGE_SIZE) : 0);
      const statusValue = statusResponse?.status && typeof statusResponse.status === "object"
        ? statusResponse.status : statusResponse;
      setStatus(statusValue || null);
      setMatches(appendUniqueMatches([], rows));
      setTotal(Number(listResponse?.total ?? rows.length));
      setHasMore(!!listResponse?.has_more);
      setNextOffset(pageOffset + consumed);
      setKnownSeasons((current) => {
        const found = rows.map((row) => row.season || row.fact?.season)
          .filter((value) => value !== undefined && value !== null && String(value) !== "")
          .map(String);
        return [...new Set([...current, ...found])].sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
      });
    }).catch((error) => {
      if (!cancelled && generation === pagingGenerationRef.current && !error.locked) {
        setLoadError(matchHistoryError(error, "Could not load match history."));
      }
    }).finally(() => {
      if (!cancelled && generation === pagingGenerationRef.current) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [expanded, accountId, seasonFilter, platformFilter, reloadToken, request]);

  const resetPreview = () => {
    setPreview(null);
    setPreviewSource(null);
    setOperationError("");
  };

  React.useEffect(() => {
    if (!configurationDirty || !preview) return;
    setPreview(null);
    setPreviewSource(null);
    setOperationError("Account configuration changed. Save the account and preview the import again.");
  }, [configurationDirty, preview]);

  const updateManual = (next) => {
    setManual(next);
    resetPreview();
  };

  const importBlockedReason = (() => {
    if (!accountId) return "Save this account before adding match history.";
    if (configurationDirty) return "Save the UID, platform, and authorization changes before importing.";
    if (!accountUid) return "Add and save a Marvel Rivals UID before importing.";
    if (!configuredPlatform || configuredPlatform === "unknown") return "Choose and save this account’s platform before importing.";
    if (!authorized) return "Enable and save match-history authorization for this account first.";
    return "";
  })();

  const manualReady = !!manual.occurred_at
    && manual.platform !== "unknown"
    && manual.platform === configuredPlatform;
  const sourceReady = sourceKind === "file" ? !!file : manualReady;

  const onPreview = async () => {
    if (previewing || importBlockedReason || !attested || !sourceReady) return;
    setPreviewing(true);
    setOperationError("");
    setImportSummary(null);
    try {
      let response;
      let snapshot;
      const path = `/api/accounts/${encodeURIComponent(accountId)}/matches/import/preview`;
      if (sourceKind === "file") {
        const body = new FormData();
        body.append("file", file);
        body.append("authorized", "true");
        response = await request(path, { method: "POST", body });
        snapshot = { kind: "file", file };
      } else {
        const value = normalizedManualMatch(manual);
        response = await request(path, {
          method: "POST",
          body: JSON.stringify({ authorized: true, manual: value }),
        });
        snapshot = { kind: "manual", manual: value };
      }
      if (!(response?.preview?.digest || response?.preview?.source_digest)
          || !response?.preview?.scope_digest) {
        throw new Error("Preview did not return its source and scope digests.");
      }
      setPreview(response.preview);
      setPreviewSource(snapshot);
      focusAfterRender(previewSummaryRef);
    } catch (error) {
      if (!error.locked) setOperationError(matchHistoryError(error, "Could not preview this import."));
    } finally {
      setPreviewing(false);
    }
  };

  const onCommit = async () => {
    const expectedDigest = preview?.digest || preview?.source_digest;
    const expectedScopeDigest = preview?.scope_digest;
    if (importing || importBlockedReason || !expectedDigest || !expectedScopeDigest
        || !previewSource || !attested) return;
    setImporting(true);
    setOperationError("");
    try {
      const path = `/api/accounts/${encodeURIComponent(accountId)}/matches/import`;
      let response;
      if (previewSource.kind === "file") {
        const body = new FormData();
        body.append("file", previewSource.file);
        body.append("authorized", "true");
        body.append("expected_digest", expectedDigest);
        body.append("expected_scope_digest", expectedScopeDigest);
        response = await request(path, { method: "POST", body });
      } else {
        response = await request(path, {
          method: "POST",
          body: JSON.stringify({
            authorized: true,
            expected_digest: expectedDigest,
            expected_scope_digest: expectedScopeDigest,
            manual: previewSource.manual,
          }),
        });
      }
      const summary = response?.summary || response?.result || {};
      setImportSummary(typeof summary === "object" ? summary : { message: String(summary) });
      setPreview(null);
      setPreviewSource(null);
      focusAfterRender(importSummaryRef);
      setAttested(false);
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      resetLoadedPages();
      setReloadToken((value) => value + 1);
      const added = Number(summary.inserted_match_count ?? summary.inserted_count
        ?? summary.accepted_count ?? response?.matches?.length ?? 0);
      showToast?.(added ? `Imported ${added} match${added === 1 ? "" : "es"}` : "Match import complete");
    } catch (error) {
      if (!error.locked) setOperationError(matchHistoryError(error, "Could not import match history."));
    } finally {
      setImporting(false);
    }
  };

  const onClear = async () => {
    if (clearing) return;
    setClearing(true);
    setOperationError("");
    try {
      await request(`/api/accounts/${encodeURIComponent(accountId)}/matches`, { method: "DELETE" });
      resetLoadedPages();
      setStatus((current) => current ? { ...current, count: 0, last_error: null } : current);
      setImportSummary(null);
      setConfirmingClear(false);
      focusAfterRender(historyToggleRef);
      setReloadToken((value) => value + 1);
      showToast?.("Match history cleared");
    } catch (error) {
      if (!error.locked) setOperationError(matchHistoryError(error, "Could not clear match history."));
    } finally {
      setClearing(false);
    }
  };

  const locallyFiltered = matches.filter((match) => {
    const season = match.season || match.fact?.season;
    const platform = match.platform || match.fact?.platform;
    return (!seasonFilter || String(season) === seasonFilter)
      && (!platformFilter || platform === platformFilter);
  });
  const visibleMatches = locallyFiltered.slice(0, displayLimit);
  const inferredCount = locallyFiltered.filter((match) => {
    const kind = match.evidence_kind || match.fact?.evidence_kind || match.account_fact?.evidence_kind;
    return kind === "inferred_owned_account" || kind === "inferred";
  }).length;
  const directCount = Math.max(0, locallyFiltered.length - inferredCount);
  const loadedRemaining = Math.max(0, locallyFiltered.length - visibleMatches.length);
  const statusCount = Number(status?.count ?? total ?? 0);
  const statusCountKnown = status !== null || matches.length > 0;
  const statusName = String(status?.status || (statusCount ? "ready" : "empty")).replaceAll("_", " ");

  const onChangePreviewSource = () => {
    resetPreview();
    focusAfterRender(previewButtonRef);
  };

  const onBeginClear = () => {
    setConfirmingClear(true);
    focusAfterRender(clearKeepRef);
  };

  const onCancelClear = () => {
    setConfirmingClear(false);
    focusAfterRender(clearTriggerRef);
  };

  const onShowMore = async () => {
    if (loadedRemaining > 0) {
      setDisplayLimit(Math.min(visibleMatches.length + MATCH_RENDER_BATCH, locallyFiltered.length));
      return;
    }
    if (!hasMore || loadingMore) return;
    const generation = pagingGenerationRef.current;
    const requestedOffset = nextOffset;
    setLoadingMore(true);
    setLoadMoreError("");
    try {
      const response = await request(matchListPath(requestedOffset));
      if (generation !== pagingGenerationRef.current) return;
      const rows = Array.isArray(response?.matches) ? response.matches : [];
      const pageOffset = Number(response?.offset ?? requestedOffset);
      const consumed = rows.length || (response?.has_more ? Number(response?.limit || MATCH_API_PAGE_SIZE) : 0);
      setMatches((current) => appendUniqueMatches(current, rows));
      if (response?.total !== undefined && response?.total !== null) {
        setTotal(Number(response.total));
      }
      setHasMore(!!response?.has_more);
      setNextOffset(pageOffset + consumed);
      setDisplayLimit(visibleMatches.length + MATCH_RENDER_BATCH);
      setKnownSeasons((current) => {
        const found = rows.map((row) => row.season || row.fact?.season)
          .filter((value) => value !== undefined && value !== null && String(value) !== "")
          .map(String);
        return [...new Set([...current, ...found])]
          .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
      });
    } catch (error) {
      if (generation === pagingGenerationRef.current && !error.locked) {
        setLoadMoreError(matchHistoryError(error, "Could not load the next page of match history."));
      }
    } finally {
      if (generation === pagingGenerationRef.current) setLoadingMore(false);
    }
  };

  return (
    <section className="drawer-section mh-section">
      <button ref={historyToggleRef} type="button" className="mh-toggle" aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}>
        <span>
          <b>Match History</b>
          <small>Local owner-authorized index · separate from rank refresh</small>
        </span>
        <span className="mh-toggle-count">{statusCountKnown ? statusCount : "—"}</span>
        <span className="mh-toggle-chevron" aria-hidden="true">{expanded ? "−" : "+"}</span>
      </button>

      {expanded && (
        <div className="mh-panel">
          {!accountId ? (
            <p className="mh-empty">Save this account first, then reopen it to import match history.</p>
          ) : (
            <>
              <div className="mh-status" aria-live="polite">
                <div>
                  <span className="mh-kicker">Match source status</span>
                  <strong>{loading && !status ? "Loading…" : statusName}</strong>
                </div>
                <div><span>Stored</span><b>{statusCount}</b></div>
                <div><span>Last import</span><b>{status?.last_success ? formatMatchDate(status.last_success) : "Never"}</b></div>
              </div>
              {status?.last_error && <p className="mh-inline-error" role="alert">{status.last_error}</p>}
              {loadError && <p className="mh-inline-error" role="alert">{loadError}</p>}

              <div className="mh-filters" aria-label="Match history filters">
                <label>
                  <span>Season</span>
                  <input type="search" list={seasonListId} value={seasonInput}
                         onChange={(event) => setSeasonInput(event.target.value)}
                         placeholder="All seasons" autoComplete="off" />
                  <datalist id={seasonListId}>
                    {knownSeasons.map((season) => <option key={season} value={season} />)}
                  </datalist>
                </label>
                <label>
                  <span>Platform</span>
                  <select value={platformFilter} onChange={(event) => setPlatformFilter(event.target.value)}>
                    <option value="">All platforms</option>
                    {MATCH_PLATFORM_OPTIONS.filter((row) => row.value !== "unknown").map((row) => (
                      <option key={row.value} value={row.value}>{row.label}</option>
                    ))}
                  </select>
                </label>
              </div>

              {!loading && (
                <p className="mh-list-summary">
                  {visibleMatches.length} rendered · {locallyFiltered.length} loaded · {total} stored
                  {" · "}{directCount} direct loaded · {inferredCount} inferred owned loaded
                </p>
              )}
              <div className="mh-history" aria-busy={loading || loadingMore}>
                {loading && <p className="mh-empty">Loading match history…</p>}
                {!loading && !loadError && locallyFiltered.length === 0 && (
                  <p className="mh-empty">{statusCount ? "No matches fit these filters." : "No match history imported yet."}</p>
                )}
                {!loading && visibleMatches.map((match) => (
                  <MatchRow key={matchRowIdentity(match)}
                            match={match} />
                ))}
                {!loading && (loadedRemaining > 0 || hasMore) && (
                  <button type="button" className="mh-more"
                          onClick={onShowMore} disabled={loadingMore}
                          aria-busy={loadingMore}>
                    {loadingMore
                      ? `Loading next ${MATCH_API_PAGE_SIZE}…`
                      : loadedRemaining > 0
                      ? `Show 100 more (${loadedRemaining} already loaded)`
                      : `Show 100 more (load next ${MATCH_API_PAGE_SIZE})`}
                  </button>
                )}
                {loadingMore && (
                  <p className="mh-empty" role="status" aria-live="polite">
                    Loading the next {MATCH_API_PAGE_SIZE} matches…
                  </p>
                )}
                {loadMoreError && (
                  <p className="mh-inline-error" role="alert">
                    {loadMoreError} Use “Show 100 more” to retry.
                  </p>
                )}
              </div>

              <div className="mh-import">
                <div className="mh-subhead">
                  <div><span className="mh-kicker">Authorized import</span><strong>Add match facts</strong></div>
                  <span>{matchPlatformLabel(configuredPlatform)}</span>
                </div>

                <div className="mh-disclosure">
                  <strong>Stored locally in plaintext</strong>
                  <p>
                    Only normalized match facts for authorized accounts are kept. Other participants are
                    used transiently for exact platform + UID matching, then discarded before commit.
                  </p>
                </div>

                {importBlockedReason && <p className="mh-inline-warn">{importBlockedReason}</p>}

                <div className="mh-source-tabs" role="radiogroup" aria-label="Import source">
                  <label className={"mh-source-choice" + (sourceKind === "file" ? " on" : "")}>
                    <input type="radio" name={sourceRadioName} value="file"
                           checked={sourceKind === "file"}
                           onChange={() => { setSourceKind("file"); resetPreview(); }} />
                    <span>JSON / CSV file</span>
                  </label>
                  <label className={"mh-source-choice" + (sourceKind === "manual" ? " on" : "")}>
                    <input type="radio" name={sourceRadioName} value="manual"
                           checked={sourceKind === "manual"}
                           onChange={() => { setSourceKind("manual"); resetPreview(); }} />
                    <span>Manual entry</span>
                  </label>
                </div>

                {sourceKind === "file" ? (
                  <>
                    <label className="mh-file">
                      <span>mrat.matches.v1 JSON or CSV</span>
                      <input ref={fileInputRef} type="file" accept=".json,.csv,application/json,text/csv"
                             onChange={(event) => {
                               setFile(event.target.files?.[0] || null);
                               resetPreview();
                             }} />
                      <small>{file ? `${file.name} · ${Math.ceil(file.size / 1024)} KB` : "Choose a local file. ZIP files and URLs are not accepted."}</small>
                    </label>
                    <p className="mh-source-note">
                      Owned-account inference is available only when a file explicitly marks its
                      participant list complete and contains an exact authorized platform + UID match.
                    </p>
                  </>
                ) : (
                  <>
                    <ManualMatchForm value={manual} onChange={updateManual}
                                     accountPlatform={configuredPlatform} />
                    <p className="mh-source-note">
                      Manual entries are direct facts for this account only; they cannot infer a match
                      for another saved account.
                    </p>
                  </>
                )}

                <label className="mh-attest">
                  <input type="checkbox" checked={attested}
                         onChange={(event) => setAttested(event.target.checked)} />
                  <span>
                    I own or am authorized to manage every saved account represented by this input.
                    <small>This attestation applies to this import batch.</small>
                  </span>
                </label>

                {operationError && <p className="mh-inline-error" role="alert">{operationError}</p>}
                {importSummary && (
                  <PreviewCounts value={importSummary} title="Last import" previewOnly={false}
                                 containerRef={importSummaryRef} />
                )}
                {preview && <PreviewCounts value={preview} containerRef={previewSummaryRef} />}

                <div className="mh-actions">
                  {!preview ? (
                    <button ref={previewButtonRef} type="button" className="drawer-btn-primary" onClick={onPreview}
                            disabled={!!importBlockedReason || !attested || !sourceReady || previewing}>
                      {previewing ? "Previewing…" : "Preview import"}
                    </button>
                  ) : (
                    <>
                      <button type="button" className="drawer-btn-ghost" onClick={onChangePreviewSource}
                              disabled={importing}>Change source</button>
                      <button type="button" className="drawer-btn-primary" onClick={onCommit}
                              disabled={!!importBlockedReason || !attested || importing}>
                        {importing ? "Importing…" : "Confirm import"}
                      </button>
                    </>
                  )}
                </div>
              </div>

              <div className="mh-danger">
                {confirmingClear ? (
                  <div className="mh-clear-confirm" role="group" aria-label="Confirm clear match history">
                    <p>Clear all {statusCount} stored match {statusCount === 1 ? "fact" : "facts"} for this account?</p>
                    <button ref={clearKeepRef} type="button" className="drawer-btn-ghost"
                            onClick={onCancelClear} disabled={clearing}>Keep history</button>
                    <button type="button" className="drawer-btn-del-confirm"
                            onClick={onClear} disabled={clearing}>
                      {clearing ? "Clearing…" : "Yes, clear history"}
                    </button>
                  </div>
                ) : (
                  <button ref={clearTriggerRef} type="button" className="drawer-btn-del"
                          onClick={onBeginClear} disabled={!statusCount}>
                    Clear match history
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

window.MatchHistoryPanel = MatchHistoryPanel;
