// Run from any working directory: node --test tests/native_desktop_ui_test.cjs
// Synthetic-only JSX checks using the app's bundled Babel and Node builtins.
// No browser, server, credentials, network requests or generated files.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '..');
const Babel = require(path.join(root, 'static/vendor/babel.min.js'));
const source = (name) => fs.readFileSync(path.join(root, 'static', name), 'utf8');
const compiled = new Map();
for (const name of ['card-variants.jsx', 'career-live.jsx', 'app.jsx']) {
  compiled.set(name, Babel.transform(source(name), { presets: ['react'], filename: name, ast: true }));
}

const element = (type, props, ...children) => ({ type, props: { ...props, children } });
function renderedText(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (Array.isArray(node)) return node.map(renderedText).join('');
  if (typeof node === 'object') {
    return renderedText(typeof node.type === 'function' ? node.type(node.props) : node.props.children);
  }
  return String(node);
}
function nodes(node, predicate) {
  if (node == null || typeof node !== 'object') return [];
  if (Array.isArray(node)) return node.flatMap((child) => nodes(child, predicate));
  return [...(predicate(node) ? [node] : []), ...nodes(node.props.children, predicate)];
}
function button(tree, label) {
  return nodes(tree, (node) => node.type === 'button' && renderedText(node) === label)[0];
}

const React = {
  createElement: element,
  useState(value) { return [typeof value === 'function' ? value() : value, () => {}]; },
  useEffect() {}, useMemo(fn) { return fn(); }, useRef(value) { return { current: value }; },
};
const cards = { window: {}, React, TAG_COLORS: {}, Date, Number, String, Object, Array, Map, Set, Math };
vm.createContext(cards);
vm.runInContext(source('data.js'), cards, { filename: 'data.js' });
vm.runInContext(compiled.get('card-variants.jsx').code, cards, { filename: 'card-variants.jsx' });
const sortNode = compiled.get('app.jsx').ast.program.body.find(
  (node) => node.type === 'FunctionDeclaration' && node.id.name === 'sortAccounts');
assert.ok(sortNode, 'actual app sortAccounts declaration must be tested');
const sorting = { RANK_INDEX: cards.window.RANK_INDEX };
vm.createContext(sorting);
vm.runInContext(source('app.jsx').slice(sortNode.start, sortNode.end), sorting);

function fixture() {
  const uid = '1370598422';
  return {
    id: 'fixture', rivals_uid: uid, in_game_name: 'FixtureIGN',
    current_rank: 'One Above All', current_points: 9999,
    peak_rank: 'Celestial I', peak_points: 5000,
    stats_provider: 'netease', last_refresh_source: 'netease', last_refresh_status: 'ok',
    last_refresh_ts: Math.floor(Date.now() / 1000), netease_rank: { qualification: 'estimated' },
    netease_native: { fetched_at: 1700000000, matches_memory_only: true, profile: {
      uid, platform: 'pc', identity_verified: true, owner_authentication_verified: true,
      game_login_verified: true, social_session_accepted: true,
      identity: { uid, platform: 'pc', level: 32 },
      current_season_verified: true, current_season: 20,
      current_rank: { season: 20, level: 14, points: 4350, points_basis: 'server_rank_score' },
      career: { status: 'available', data: { request_scope: 'unfiltered', statistics: {
        matches: 273, wins: 149, kills: 4165, deaths: 1609, assists: 3145,
      } } },
      ranks: { status: 'available', data: { season_count: 1, seasons: [
        { season: 20, level: 14, points: 4350, peak_level: 17, peak_points: 4600,
          record: null, updated_at: null },
      ] } },
      matches: { status: 'available', data: { matches: [
        { uid, match_uid: 'synthetic-match', played_at: 1700000000, season: 20, mode_id: 2,
          result: 'win', kills: 10, deaths: 2, assists: 4, duration_seconds: 120,
          rank_change_verified: true,
          rank_change: { points_before: 4330, points_after: 4350, points_delta: 20 } },
      ] } },
    } },
  };
}
const profileOf = (account) => account.netease_native.profile;
function ranked(id, level, points) {
  const account = fixture();
  account.id = id;
  Object.assign(profileOf(account).current_rank, { level, points });
  return account;
}
const sortedIds = (accounts, mode = 'current_desc') =>
  Array.from(sorting.sortAccounts(accounts, mode), (account) => account.id);

test('all three modified JSX modules parse with the bundled production Babel', () => {
  assert.equal(compiled.size, 3);
  for (const result of compiled.values()) assert.ok(result.code.length > 0);
  assert.match(source('app.jsx'), /<window\.NativeProfileDetails account=\{acct\}/);
});

test('native current uses numeric level/raw points while saved peak and legacy scale stay separate', () => {
  const account = fixture();
  assert.equal(cards.accountRankDisplay(account, 'current', { tierRelative: true }), 'Level 14 · 4350 raw');
  assert.equal(cards.accountRankDisplay(account, 'peak', { tierRelative: true }), 'Celestial I · 5000');
  delete account.netease_native;
  account.current_rank = 'Diamond II'; account.current_points = 4350;
  assert.equal(cards.accountRankDisplay(account, 'current', { tierRelative: true }), 'Diamond II · 50/100');
});

test('read-only details show unfiltered totals, season rows and first-page raw match changes', () => {
  const account = fixture();
  profileOf(account).private_unknown = 'DO_NOT_RENDER_SYNTHETIC';
  const text = renderedText(cards.window.NativeProfileDetails({ account }));
  for (const expected of ['Account level 32', 'Career totals · unfiltered', 'Matches273',
    'Season ranks', 'Recent matches', '4330 → 4350 (+20)', 'first 50', 'imported match index']) {
    assert.ok(text.includes(expected), expected);
  }
  assert.ok(!text.includes('Stats season'));
  assert.ok(!text.includes('DO_NOT_RENDER_SYNTHETIC'));
});

test('Career, ranks and matches are independently restricted without hiding other available sections', () => {
  for (const section of ['career', 'ranks', 'matches']) {
    const account = fixture();
    profileOf(account)[section] = { status: 'restricted', data: null };
    const text = renderedText(cards.window.NativeProfileDetails({ account }));
    assert.ok(text.includes("Restricted by this player's visibility settings"));
    if (section !== 'career') assert.ok(text.includes('Matches273'));
    if (section !== 'ranks') assert.ok(text.includes('4600'));
    if (section !== 'matches') assert.ok(text.includes('4330 → 4350 (+20)'));
  }
});

test('native rank unavailable and UID mismatch never restore saved current rank', () => {
  const account = fixture();
  profileOf(account).current_rank = null;
  assert.equal(cards.accountRankDisplay(account, 'current'), 'Unavailable');
  profileOf(account).uid = '1234567890';
  assert.equal(cards.accountRankDisplay(account, 'current'), 'Unavailable');
  assert.match(renderedText(cards.window.NativeProfileDetails({ account })), /unavailable for this UID/);
});

test('Cards, Table and both Ladder views suppress stale current labels and estimated metadata', () => {
  const account = fixture();
  const props = { acct: account, accounts: [account], refreshingIds: new Set(),
    opts: { hideCopy: true, hideDetails: true, srTierRelative: true } };
  for (const [component, rankField] of [
    [cards.window.CardRefined], [cards.window.TableView],
    [cards.window.LadderView, 'current'], [cards.window.LadderView, 'peak'],
  ]) {
    const text = renderedText(component({ ...props, rankField }));
    assert.ok(text.includes('Level 14 · 4350 raw'));
    assert.match(text, /saved peak/i);
    for (const stale of ['9999', '50/100', 'One Above All', 'EST.']) assert.ok(!text.includes(stale), stale);
  }
});

test('native current sort uses exact level first, raw numeric points second, without input mutation', () => {
  const accounts = [ranked('low', 9, 9999), ranked('raw-low', 14, 4350),
    ranked('raw-high', 14, 4400), ranked('level-high', 15, 1)];
  const before = JSON.stringify(accounts);
  assert.deepEqual(sortedIds(accounts), ['level-high', 'raw-high', 'raw-low', 'low']);
  assert.equal(JSON.stringify(accounts), before);
});

test('native current sort rejects wrong binding, false verification and wrong season without manual fallback', () => {
  const good = ranked('good', 0, 0);
  const mutations = [
    (p) => { p.uid = '1234567890'; },
    (p) => { p.identity_verified = false; },
    (p) => { p.owner_authentication_verified = false; },
    (p) => { p.game_login_verified = false; },
    (p) => { p.current_season_verified = false; },
    (p) => { p.current_rank.season = 19; },
    (p) => { p.current_rank.level = '99'; },
    (p) => { p.current_rank.level = -1; },
    (p) => { p.current_rank = null; },
  ];
  for (const mutate of mutations) {
    const bad = ranked('unavailable', 999, 99999);
    mutate(profileOf(bad));
    assert.deepEqual(sortedIds([bad, good]), ['good', 'unavailable']);
  }
});

test('missing/nonfinite/string raw scores sort below numeric scores without coercion or modulo', () => {
  for (const value of [null, undefined, NaN, Infinity, -Infinity, '99999']) {
    assert.deepEqual(sortedIds([ranked('missing', 14, value), ranked('numeric', 14, -1)]), ['numeric', 'missing']);
  }
  assert.deepEqual(sortedIds([ranked('4350', 14, 4350), ranked('4301', 14, 4301)]), ['4350', '4301']);
  assert.deepEqual(sortedIds([ranked('first', 14, null), ranked('second', 14, null)]), ['first', 'second']);
});

test('mixed source grouping avoids equating native levels to English tiers; legacy order remains intact', () => {
  const missing = ranked('native-unavailable', 99, 99999);
  profileOf(missing).current_rank = null;
  const legacyHigh = { id: 'legacy-high', current_rank: 'One Above All' };
  const legacyLow = { id: 'legacy-low', current_rank: 'Bronze III' };
  assert.deepEqual(sortedIds([legacyLow, missing, legacyHigh, ranked('native', 0, 0)]),
    ['native', 'legacy-high', 'legacy-low', 'native-unavailable']);
  assert.deepEqual(sortedIds([legacyLow, legacyHigh]), ['legacy-high', 'legacy-low']);
});

test('pinned sort retains pin priority then native level/score; saved peak sorting is unchanged', () => {
  const pinned = ranked('pinned', 0, 0); pinned.pinned = true;
  const high = ranked('native-high', 15, 4400); high.peak_rank = 'Bronze III';
  const low = ranked('native-low', 14, 4350); low.peak_rank = 'One Above All';
  assert.deepEqual(sortedIds([low, high, pinned], 'pinned'), ['pinned', 'native-high', 'native-low']);
  assert.deepEqual(sortedIds([high, low], 'peak_desc'), ['native-low', 'native-high']);
});

function connectionHarness(initial, options = {}) {
  const states = [], calls = [], timers = new Map();
  let cursor = 0, effects = [], status = { ...initial }, timerId = 0;
  const react = {
    createElement: element,
    useState(value) {
      const index = cursor++;
      if (!(index in states)) states[index] = typeof value === 'function' ? value() : value;
      return [states[index], (next) => { states[index] = typeof next === 'function' ? next(states[index]) : next; }];
    },
    useRef(value) {
      const index = cursor++;
      if (!(index in states)) states[index] = { current: value };
      return states[index];
    },
    useEffect(fn) { effects.push(fn); },
  };
  const context = { React: react, window: {
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  } };
  vm.createContext(context);
  vm.runInContext(compiled.get('career-live.jsx').code, context);
  async function request(route, requestOptions) {
    calls.push({ route, options: requestOptions });
    if (options.reject) throw new Error('SYNTHETIC_PRIVATE_ERROR');
    return { ...status, connection_in_progress: false };
  }
  return {
    calls, timers, effects: () => effects, status: () => status,
    render() {
      cursor = 0; effects = [];
      return context.NativeNetEaseConnection({ request, status, setStatus(next) { status = next; } });
    },
    async tick() {
      const [id, timer] = timers.entries().next().value;
      timers.delete(id);
      await timer.fn();
    },
  };
}
const disconnected = { provider: 'native', configured: true, connected: false, can_connect: true,
  connection_in_progress: false, state: 'unconfigured', persistence: 'none' };

test('Connect defaults to memory only, POSTs once, then checks status', async () => {
  const harness = connectionHarness(disconnected);
  const tree = harness.render();
  assert.equal(harness.calls.length, 0);
  assert.equal(nodes(tree, (node) => node.type === 'input')[0].props.checked, false);
  await button(tree, 'Connect NetEase').props.onClick();
  assert.deepEqual(harness.calls.map((call) => call.route), ['/api/netease/connect', '/api/career/status']);
  assert.equal(harness.calls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(harness.calls[0].options.body), { persist: false });
});

test('encrypted-local persistence requires explicit checkbox opt-in', async () => {
  const harness = connectionHarness(disconnected);
  nodes(harness.render(), (node) => node.type === 'input')[0].props.onChange({ target: { checked: true } });
  await button(harness.render(), 'Connect NetEase').props.onClick();
  assert.deepEqual(JSON.parse(harness.calls[0].options.body), { persist: true });
});

test('only recoverable states offer recovery and never POST Connect as a fallback', async () => {
  for (const state of ['candidate_pending', 'candidate_verifying', 'candidate_recovery_required']) {
    const harness = connectionHarness({ ...disconnected, can_connect: false, state });
    await button(harness.render(), 'Recover saved session').props.onClick();
    assert.deepEqual(harness.calls.map((call) => call.route), ['/api/netease/recover', '/api/career/status']);
    assert.deepEqual(JSON.parse(harness.calls[0].options.body), {});
  }
  for (const state of ['ready', 'rotation_unknown', 'unconfigured']) {
    assert.equal(button(connectionHarness({ ...disconnected, state }).render(), 'Recover saved session'), undefined);
  }
});

test('connecting state disables actions and polls only status with a cancellable two-second timer', async () => {
  const harness = connectionHarness({ ...disconnected, state: 'candidate_pending', connection_in_progress: true });
  const tree = harness.render();
  assert.equal(button(tree, 'Recover saved session').props.disabled, true);
  assert.equal(button(tree, 'Complete NetEase login…').props.disabled, true);
  const cleanup = harness.effects().map((effect) => effect()).filter(Boolean);
  assert.equal(harness.timers.size, 1);
  assert.equal(harness.timers.values().next().value.delay, 2000);
  await harness.tick();
  assert.deepEqual(harness.calls.map((call) => call.route), ['/api/career/status']);
  assert.equal(harness.calls[0].options, undefined);
  assert.equal(harness.status().connection_in_progress, false);
  cleanup.forEach((fn) => fn());
  assert.equal(harness.timers.size, 0);
});

test('idle connection has no poll and persistence copy states the actual lifetime', () => {
  for (const [persistence, phrase] of [['memory', 'until the vault locks or the app closes'],
    ['encrypted_local', 'Session saved encrypted on this computer']]) {
    const harness = connectionHarness({ ...disconnected, persistence });
    const text = renderedText(harness.render());
    assert.ok(text.includes(phrase));
    assert.ok(text.includes('No game capture or administrator access is needed'));
    assert.ok(text.includes('In-game-name search is not supported'));
    harness.effects().forEach((effect) => effect());
    assert.equal(harness.timers.size, 0);
    assert.equal(harness.calls.length, 0);
  }
});

test('connection errors show fixed fallback copy without reflecting exception text or retrying', async () => {
  const harness = connectionHarness(disconnected, { reject: true });
  await button(harness.render(), 'Connect NetEase').props.onClick();
  const text = renderedText(harness.render());
  assert.ok(text.includes('NetEase connection could not complete'));
  assert.ok(!text.includes('SYNTHETIC_PRIVATE_ERROR'));
  assert.deepEqual(harness.calls.map((call) => call.route), ['/api/netease/connect']);
});
