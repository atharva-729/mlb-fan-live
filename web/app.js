'use strict';

/* Fan Pulse Live dashboard.
 *
 * Everything shown is precomputed and looked up by time. One clock drives the
 * page: the dashboard's own (play, pause, scrub) or, once video anchors exist,
 * the YouTube player's. The page never knows which model produced the
 * readings; that is whichever source file is loaded.
 */

// ---------------------------------------------------------------- helpers

function clamp(value, low, high) { return Math.min(high, Math.max(low, value)); }

/** Index of the first element greater than value, in a sorted array. */
function upperBound(sorted, value) {
  let low = 0, high = sorted.length;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (sorted[mid] <= value) low = mid + 1; else high = mid;
  }
  return low;
}

/** Carry the last reading across updates flagged in `carry`; leave other gaps empty. */
function forwardFill(values, carry) {
  const out = new Array(values.length);
  let last = null;
  for (let i = 0; i < values.length; i++) {
    if (values[i] !== null && values[i] !== undefined) last = values[i];
    out[i] = values[i] !== null && values[i] !== undefined ? values[i] : (carry[i] ? last : null);
  }
  return out;
}

/** Exponential moving average that restarts after a gap. */
function ema(values, alpha) {
  const out = new Array(values.length);
  let level = null;
  for (let i = 0; i < values.length; i++) {
    if (values[i] === null) { out[i] = null; level = null; continue; }
    level = level === null ? values[i] : level + alpha * (values[i] - level);
    out[i] = Math.round(level * 1000) / 1000;
  }
  return out;
}

function hexToRgb(hex) {
  const n = parseInt(hex.replace('#', ''), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function mixColor(a, b, share) {
  const x = hexToRgb(a), y = hexToRgb(b);
  const c = x.map((v, i) => Math.round(v + (y[i] - v) * share));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

/** Diverging color for a -1..+1 value: negative pole, neutral gray, positive pole. */
function sentimentColor(value, palette) {
  if (value === null || value === undefined) return palette.mid;
  const v = clamp(value, -1, 1);
  return mixColor(palette.mid, v < 0 ? palette.neg : palette.pos, Math.abs(v));
}

function signed(value, digits = 2) {
  if (value === null || value === undefined) return '–';
  const text = Math.abs(value).toFixed(digits);
  return (value > 0 ? '+' : value < 0 ? '−' : '') + text;
}

/** Video seconds to wall-clock seconds. Each anchor starts a stretch that plays in real time. */
function videoToWall(anchors, videoSeconds) {
  let anchor = anchors[0];
  for (const a of anchors) if (a.video <= videoSeconds) anchor = a;
  return anchor.wall + (videoSeconds - anchor.video);
}

/** Wall-clock seconds to video seconds; a time cut from the video maps to the next stretch's start. */
function wallToVideo(anchors, wallSeconds) {
  let k = 0;
  for (let i = 0; i < anchors.length; i++) if (anchors[i].wall <= wallSeconds) k = i;
  const video = anchors[k].video + (wallSeconds - anchors[k].wall);
  const next = anchors[k + 1];
  return next ? Math.min(video, next.video) : video;
}

/** Wall-clock seconds as an Eastern-time string Plotly shows as written (the game was in October, UTC-4). */
function etStamp(seconds) {
  return new Date((seconds - 4 * 3600) * 1000).toISOString().slice(0, 19).replace('T', ' ');
}

function etClock(seconds) {
  const d = new Date((seconds - 4 * 3600) * 1000);
  const h = d.getUTCHours(), m = d.getUTCMinutes(), s = d.getUTCSeconds();
  const pad = n => String(n).padStart(2, '0');
  return `${((h + 11) % 12) + 1}:${pad(m)}:${pad(s)} ${h < 12 ? 'AM' : 'PM'} ET`;
}

function ordinal(n) {
  const tail = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({1: 'st', 2: 'nd', 3: 'rd'}[n % 10] || 'th');
  return n + tail;
}

function halfLabel(inning, half) { return `${half === 'top' ? 'Top' : 'Bot'} ${ordinal(inning)}`; }

function streamLabel(id) {
  const [sub, flair] = id.split(':');
  if (!flair) return sub;
  return `${sub} · ${{'LAD-flair': 'Dodgers flair', 'TOR-flair': 'Blue Jays flair', neutral: 'neutral'}[flair] || flair}`;
}

/** Count and mean sentiment per subject among tagged comments in [lo, hi), for one stream or all (-1). */
function subjectsInRange(comments, tags, subjects, lo, hi, streamIndex) {
  const stats = new Map();
  for (let i = lo; i < hi; i++) {
    if (streamIndex >= 0 && comments.s[i] !== streamIndex) continue;
    const k = tags.subject[i];
    if (k < 0 || subjects[k] === 'other') continue;
    let entry = stats.get(k);
    if (!entry) { entry = {name: subjects[k], count: 0, sum: 0, scored: 0}; stats.set(k, entry); }
    entry.count++;
    if (tags.sentiment[i] !== null) { entry.sum += tags.sentiment[i]; entry.scored++; }
  }
  return [...stats.values()]
    .map(e => ({name: e.name, count: e.count, sentiment: e.scored ? e.sum / e.scored : null}))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

if (typeof module !== 'undefined') {
  module.exports = {upperBound, forwardFill, ema, sentimentColor, signed, videoToWall, wallToVideo, etStamp, etClock,
    halfLabel, streamLabel, subjectsInRange, mixColor};
}

// ------------------------------------------------------------------ state

const RECENT_SECONDS = 120;
const COMMENTS_SHOWN = 40;
const SUBJECTS_SHOWN = 6;
// Share of each new reading in the smoothed mood line. At one reading per 5 seconds,
// 0.05 averages over roughly the last two minutes; 0.3 is close to the raw readings.
const MOOD_SMOOTH = 0.05, MOOD_RAW = 0.3;

const S = {
  manifest: null, game: null, comments: null, src: null, sourceId: null,
  now: 0, playing: false, speed: 16, tab: -1, showFuture: false, smooth: true, selected: null,
  derived: null, player: null, synced: false,
  drawn: {tick: -1, comment: -1, moments: -1, chartAt: 0, key: ''},
};

const $ = id => document.getElementById(id);

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function palette() {
  return {
    surface: css('--surface'), ink: css('--ink'), ink2: css('--ink-2'), muted: css('--muted'),
    grid: css('--grid'), axis: css('--axis'), pos: css('--pos'), neg: css('--neg'), mid: css('--neutral-mid'),
    series: [1, 2, 3, 4, 5].map(i => css(`--series-${i}`)),
  };
}

function esc(text) {
  return String(text).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}

function tickIndex(t) {
  const m = S.manifest;
  return clamp(Math.floor((t - m.tickStart) / m.tickStep), 0, m.nTicks - 1);
}

function tickTime(i) { return S.manifest.tickStart + i * S.manifest.tickStep; }

// ------------------------------------------------------------------- load

async function getJson(name) {
  const response = await fetch(`data/${name}`, {cache: 'no-cache'});
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
  return response.json();
}

async function init() {
  try {
    S.manifest = await getJson('manifest.json');
    [S.game, S.comments] = await Promise.all([getJson('game.json'), getJson('comments.json')]);
  } catch (error) {
    const box = $('error');
    box.hidden = false;
    box.textContent = `Could not load the dashboard data (${error.message}). Run "fanpulse-live build", then "fanpulse-live serve".`;
    return;
  }
  const m = S.manifest;
  $('game-label').textContent = `${m.label}: ${m.away.name} at ${m.home.name}`;
  S.now = m.firstPitch - 30;

  const select = $('source');
  select.innerHTML = m.sources.map(s => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('');
  select.onchange = () => loadSource(select.value);

  const scrub = $('scrub');
  scrub.min = m.tickStart; scrub.max = tickTime(m.nTicks - 1); scrub.step = 1;
  scrub.oninput = () => seek(Number(scrub.value));
  $('play').onclick = () => setPlaying(!S.playing);
  $('speed').onchange = e => { S.speed = Number(e.target.value); };
  $('show-future').onchange = e => { S.showFuture = e.target.checked; invalidate(); };
  $('smooth').onchange = e => { S.smooth = e.target.checked; deriveMood(); };
  $('prev-moment').onclick = () => jumpMoment(-1);
  $('next-moment').onclick = () => jumpMoment(1);
  buildTabs();
  buildLegend();
  setupVideo();
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { buildLegend(); buildTabs(); invalidate(); });
  }
  window.addEventListener('resize', () => { if (window.Plotly && $('pulse').data) Plotly.Plots.resize($('pulse')); });

  await loadSource(m.sources[0].id);
  let last = performance.now();
  const frame = stamp => {
    const elapsed = (stamp - last) / 1000;
    last = stamp;
    advance(elapsed);
    render();
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

async function loadSource(id) {
  S.src = await getJson(`${id}.json`);
  S.sourceId = id;
  $('source').value = id;
  const info = S.manifest.sources.find(s => s.id === id);
  $('coverage').textContent = info.coverage >= 0.995
    ? 'covers the whole game'
    : `covers ${Math.round(info.coverage * 100)}% of the game so far; the rest is still to run`;

  S.selected = null;
  deriveMood();
}

function deriveMood() {
  const mood = {};
  for (const stream of S.manifest.streams) {
    const series = S.src.ticks[stream.id];
    // A stale update keeps the last reading; one the model has not answered yet stays empty.
    mood[stream.id] = ema(forwardFill(series.mood, series.stale), S.smooth ? MOOD_SMOOTH : MOOD_RAW);
  }
  S.derived = {mood};
  invalidate();
}

function invalidate() { S.drawn = {tick: -1, comment: -1, moments: -1, chartAt: 0, key: ''}; }

// ------------------------------------------------------------------ clock

function advance(elapsedSeconds) {
  const m = S.manifest, end = tickTime(m.nTicks - 1);
  if (S.synced && S.player && S.player.getCurrentTime) {
    const state = S.player.getPlayerState ? S.player.getPlayerState() : -1;
    if (state === 1 || state === 3) S.now = clamp(videoToWall(m.anchors, S.player.getCurrentTime()), m.tickStart, end);
    S.playing = state === 1;
  } else if (S.playing) {
    S.now = Math.min(end, S.now + elapsedSeconds * S.speed);
    if (S.now >= end) S.playing = false;
  }
  $('play').textContent = S.playing ? 'Pause' : 'Play';
}

function setPlaying(playing) {
  if (S.synced && S.player) {
    if (playing) S.player.playVideo(); else S.player.pauseVideo();
    return;
  }
  const end = tickTime(S.manifest.nTicks - 1);
  if (playing && S.now >= end) S.now = S.manifest.firstPitch - 30;
  S.playing = playing;
}

function seek(t) {
  const m = S.manifest;
  S.now = clamp(t, m.tickStart, tickTime(m.nTicks - 1));
  if (S.synced && S.player && S.player.seekTo) S.player.seekTo(wallToVideo(m.anchors, S.now), true);
}

function jumpMoment(direction) {
  const starts = S.src.moments.map(x => x.start);
  if (!starts.length) return;
  const target = direction > 0
    ? starts.find(t => t - 15 > S.now + 1)
    : [...starts].reverse().find(t => t - 15 < S.now - 5);
  if (target !== undefined) seek(target - 15);
}

// ------------------------------------------------------------------ video

function setupVideo() {
  const m = S.manifest;
  S.synced = m.anchors.length > 0;
  $('video-note').textContent = S.synced
    ? `Video synced from ${m.anchors.length} half-inning anchors: the dashboard follows the player.`
    : 'The video is not synced to the dashboard yet, so it plays on its own.';
  if (!S.synced) $('video-note').insertAdjacentHTML('beforeend', ' <a href="anchors.html">Sync it</a>.');
  $('speed').disabled = S.synced;
  window.onYouTubeIframeAPIReady = () => {
    S.player = new YT.Player('player', {
      videoId: m.youtubeVideoId,
      playerVars: {playsinline: 1, rel: 0},
      events: {
        onError: () => { $('video-note').textContent = 'YouTube would not play this video here. The dashboard clock still works on its own.'; S.synced = false; $('speed').disabled = false; },
      },
    });
  };
  const script = document.createElement('script');
  script.src = 'https://www.youtube.com/iframe_api';
  script.onerror = () => { $('video-note').textContent = 'Could not reach YouTube. The dashboard clock still works on its own.'; };
  document.head.appendChild(script);
}

// ----------------------------------------------------------------- render

function render() {
  if (!S.src) return;
  const i = tickIndex(S.now);
  $('clock').textContent = etClock(S.now);
  if (document.activeElement !== $('scrub')) $('scrub').value = Math.round(S.now);

  const key = `${S.sourceId}|${S.tab}|${S.showFuture}`;
  const changed = key !== S.drawn.key;
  if (changed || i !== S.drawn.tick) {
    renderScoreboard(i);
    renderSubjects();
    const stamp = performance.now();
    if (changed || stamp - S.drawn.chartAt > 200) { renderChart(i); S.drawn.chartAt = stamp; S.drawn.tick = i; }
  }
  const newest = upperBound(S.comments.t, S.now);
  if (changed || newest !== S.drawn.comment) { renderComments(newest); S.drawn.comment = newest; }
  const visible = S.showFuture ? S.src.moments.length : S.src.moments.filter(x => x.start <= S.now).length;
  if (changed || visible !== S.drawn.moments) { renderMoments(); S.drawn.moments = visible; }
  S.drawn.key = key;
}

function renderScoreboard(i) {
  const st = S.game.state, m = S.manifest, p = palette();
  const home = st.home[i], away = st.away[i], status = st.status[i];
  let inning = halfLabel(st.inning[i], st.half[i]);
  if (status === 'pregame') inning = 'Pregame';
  if (status === 'final') inning = 'Final';
  if (status === 'between_innings') inning = `End ${halfLabel(st.inning[i], st.half[i])}`;
  const live = status === 'in_play' || status === 'between_plays';
  const runners = st.runners[i];
  const outs = [0, 1, 2].map(k => `<span class="dot${live && k < st.outs[i] ? ' on' : ''}"></span>`).join('');
  const wp = st.wp[i];
  // The two teams keep the colors of their home communities, in the stream order.
  const awayColor = p.series[0], homeColor = p.series[1];
  $('scoreboard').innerHTML = `
    <div class="teams">
      <div class="team-row${away > home ? ' leading' : ''}"><span class="name">${esc(m.away.name)}</span><span class="runs">${away}</span></div>
      <div class="team-row${home > away ? ' leading' : ''}"><span class="name">${esc(m.home.name)}</span><span class="runs">${home}</span></div>
    </div>
    <div class="situation">
      <span class="inning">${esc(inning)}</span>
      <span class="outs">${outs}<span>${live ? `${st.outs[i]} out` : ''}</span></span>
      <span class="muted">${status === 'in_play' ? `Count ${st.balls[i]}-${st.strikes[i]}` : '&nbsp;'}</span>
    </div>
    <div class="diamond" role="img" aria-label="Runners on base: ${['first', 'second', 'third'].filter((_, k) => runners[k] === '1').join(', ') || 'none'}">
      <span class="base b1${live && runners[0] === '1' ? ' on' : ''}"></span>
      <span class="base b2${live && runners[1] === '1' ? ' on' : ''}"></span>
      <span class="base b3${live && runners[2] === '1' ? ' on' : ''}"></span>
    </div>
    <div class="matchup">
      <span>At bat: <b>${esc(st.batter[i] || '–')}</b></span>
      <span>Pitching: <b>${esc(st.pitcher[i] || '–')}</b></span>
    </div>
    <div class="wp" title="Win probability">
      <div class="wp-labels"><span>${esc(m.away.name)} ${Math.round((1 - wp) * 100)}%</span><span>win probability</span><span>${esc(m.home.name)} ${Math.round(wp * 100)}%</span></div>
      <div class="wp-bar"><span style="width:${(1 - wp) * 100}%;background:${awayColor}"></span><span style="width:${wp * 100}%;background:${homeColor}"></span></div>
    </div>`;
}

function moodStreams() { return S.manifest.streams.map((s, k) => ({...s, k})).filter(s => s.team !== null); }

function buildLegend() {
  const p = palette();
  const keys = moodStreams().map(s => `<span class="key"><span class="swatch" style="background:${p.series[s.k]}"></span>${esc(streamLabel(s.id))} mood</span>`);
  keys.unshift(`<span class="key"><span class="swatch" style="background:${p.ink2}"></span>${esc(S.manifest.home.name)} win probability</span>`);
  keys.push('<span class="key">▼ moment</span>');
  $('legend').innerHTML = keys.join('');
}

function inningTicks() {
  const st = S.game.state, vals = [], text = [];
  let last = 0;
  for (let i = 0; i < st.inning.length; i++) {
    if (st.status[i] !== 'pregame' && st.inning[i] !== last) { last = st.inning[i]; vals.push(etStamp(tickTime(i))); text.push(ordinal(last)); }
  }
  return {vals, text};
}

function renderChart(i) {
  if (!window.Plotly) { $('pulse').textContent = 'The chart library could not be loaded (no internet?).'; return; }
  const p = palette(), m = S.manifest, st = S.game.state;
  const upto = S.showFuture ? m.nTicks : i + 1;
  const x = [];
  for (let k = 0; k < upto; k++) x.push(etStamp(tickTime(k)));

  const traces = [{
    x, y: st.wp.slice(0, upto), mode: 'lines', line: {color: p.ink2, width: 2, shape: 'hv'},
    name: `${m.home.name} win probability`, hovertemplate: '%{y:.0%}<extra>' + m.home.name + ' win prob.</extra>',
  }];
  for (const s of moodStreams()) {
    traces.push({
      x, y: S.derived.mood[s.id].slice(0, upto), mode: 'lines', connectgaps: false,
      line: {color: p.series[s.k], width: 2}, yaxis: 'y2', name: streamLabel(s.id),
      hovertemplate: '%{y:+.2f}<extra>' + streamLabel(s.id) + '</extra>',
    });
  }
  const shown = S.src.moments.filter(mo => S.showFuture || mo.start <= S.now);
  traces.push({
    x: shown.map(mo => etStamp(mo.start)), y: shown.map(() => 1.08), yaxis: 'y2', mode: 'markers',
    marker: {symbol: 'triangle-down', size: 9, color: p.ink2}, name: 'Moment',
    text: shown.map(mo => mo.play || 'Volume spike'), hovertemplate: '%{text}<extra>Moment</extra>',
  });

  const innings = inningTicks();
  const axis = {gridcolor: p.grid, linecolor: p.axis, zeroline: false, tickfont: {color: p.muted, size: 11}, title: {font: {color: p.muted, size: 11}}};
  const layout = {
    paper_bgcolor: p.surface, plot_bgcolor: p.surface, showlegend: false, hovermode: 'x unified',
    font: {family: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: p.ink2, size: 12},
    margin: {l: 52, r: 12, t: 6, b: 34},
    hoverlabel: {bgcolor: p.surface, bordercolor: p.axis, font: {color: p.ink}},
    xaxis: {...axis, showgrid: false, range: [etStamp(m.tickStart), etStamp(tickTime(m.nTicks - 1))], tickvals: innings.vals, ticktext: innings.text,
      hoverformat: '%-I:%M:%S %p ET', title: {text: 'inning', font: {color: p.muted, size: 11}}, anchor: 'y2'},
    yaxis: {...axis, domain: [0.72, 1], range: [0, 1], tickformat: '.0%', tickvals: [0, 0.5, 1], title: {text: 'win prob.', font: {color: p.muted, size: 11}}},
    yaxis2: {...axis, domain: [0, 0.64], range: [-1.05, 1.18], tickvals: [-1, -0.5, 0, 0.5, 1], title: {text: 'mood (−1 to +1)', font: {color: p.muted, size: 11}}},
    shapes: [
      {type: 'line', xref: 'x', yref: 'paper', x0: etStamp(S.now), x1: etStamp(S.now), y0: 0, y1: 1, line: {color: p.ink, width: 1}},
      {type: 'line', xref: 'paper', yref: 'y2', x0: 0, x1: 1, y0: 0, y1: 0, line: {color: p.axis, width: 1}},
    ],
  };
  Plotly.react($('pulse'), traces, layout, {displayModeBar: false, responsive: true});
}

function buildTabs() {
  const p = palette();
  const tabs = [{k: -1, label: 'All'}].concat(S.manifest.streams.map((s, k) => ({k, label: streamLabel(s.id)})));
  $('tabs').innerHTML = tabs.map(t =>
    `<button role="tab" data-k="${t.k}" aria-selected="${t.k === S.tab}">${t.k >= 0 ? `<span class="dot" style="background:${p.series[t.k]}"></span>` : ''}${esc(t.label)}</button>`).join('');
  for (const button of $('tabs').children) {
    button.onclick = () => { S.tab = Number(button.dataset.k); S.selected = null; buildTabs(); };
  }
}

function renderComments(newest) {
  const c = S.comments, tags = S.src.tags, p = palette(), rows = [];
  for (let i = newest - 1; i >= 0 && rows.length < COMMENTS_SHOWN; i--) {
    if (S.tab >= 0 && c.s[i] !== S.tab) continue;
    const chips = [`<span class="chip"><span class="dot" style="background:${p.series[c.s[i]]}"></span>${esc(streamLabel(S.manifest.streams[c.s[i]].id))}</span>`];
    if (tags.tagged[i]) {
      if (tags.subject[i] >= 0) chips.push(`<span class="chip">about <b>${esc(S.src.subjects[tags.subject[i]])}</b></span>`);
      if (tags.sentiment[i] !== null) chips.push(`<span class="chip"><span class="dot" style="background:${sentimentColor(tags.sentiment[i], p)}"></span>sentiment ${signed(tags.sentiment[i], 1)}</span>`);
    }
    rows.push(`<div class="comment"><span class="when">${etClock(c.t[i]).replace(' ET', '')}</span><span class="text">${esc(c.x[i])}</span><span class="meta">${chips.join('')}</span></div>`);
  }
  $('comments').innerHTML = rows.join('') || '<div class="empty">No comments yet at this point in the game.</div>';
}

function currentLine(name) {
  const player = S.game.players[name];
  if (!player) return null;
  let line = null;
  for (const [t, text] of player.lines) if (t <= S.now) line = text;
  return {team: player.team, line, season: player.season};
}

function renderSubjects() {
  const c = S.comments, p = palette();
  const hi = upperBound(c.t, S.now), lo = upperBound(c.t, S.now - RECENT_SECONDS);
  const all = subjectsInRange(c, S.src.tags, S.src.subjects, lo, hi, S.tab);
  const top = all.slice(0, SUBJECTS_SHOWN);
  const most = top.length ? top[0].count : 1;
  const chosen = S.selected || (top.find(s => S.game.players[s.name]) || {}).name || null;

  $('subjects').innerHTML = top.map(s => `
    <button class="subject" data-name="${esc(s.name)}" aria-pressed="${s.name === chosen}">
      <span class="name">${esc(s.name)}</span>
      <span class="track"><span class="bar" style="width:${Math.max(4, s.count / most * 100)}%;background:${sentimentColor(s.sentiment, p)}"></span></span>
      <span class="num">${s.count} · ${signed(s.sentiment, 1)}</span>
    </button>`).join('') || '<div class="empty">Nobody in particular right now.</div>';
  for (const button of $('subjects').querySelectorAll('.subject')) {
    button.onclick = () => { S.selected = button.dataset.name; S.drawn.tick = -1; };
  }

  const card = $('player-card');
  const info = chosen && currentLine(chosen);
  if (!info) { card.innerHTML = ''; return; }
  const recent = all.find(s => s.name === chosen);
  const team = info.team === S.manifest.home.abbr ? S.manifest.home.name : S.manifest.away.name;
  card.innerHTML = `
    <span class="who">${esc(chosen)} <span class="muted">${esc(team)}</span></span>
    <span class="line">Fans, last 2 minutes: <b>${recent ? signed(recent.sentiment, 2) : '–'}</b> ${recent ? `across ${recent.count} comment${recent.count === 1 ? '' : 's'}` : '(no recent comments)'}</span>
    <span class="line">Today: <b>${esc(info.line || 'nothing yet')}</b></span>
    ${info.season ? `<span class="line">2025 season: <b>${esc(info.season)}</b></span>` : ''}`;
}

function fanbaseMood(fanbase, i) {
  const values = S.manifest.streams.filter(s => s.fanbase === fanbase && s.team !== null)
    .map(s => S.derived.mood[s.id][i]).filter(v => v !== null && v !== undefined);
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

function renderMoments() {
  const p = palette();
  const fanbases = [...new Set(S.manifest.streams.map(s => s.fanbase))];
  const dotFor = fanbase => p.series[S.manifest.streams.findIndex(s => s.fanbase === fanbase)];
  const shown = S.src.moments.filter(mo => S.showFuture || mo.start <= S.now).reverse();
  $('moments').innerHTML = shown.map(mo => {
    const i = tickIndex(mo.peak);
    const fans = fanbases.map(fanbase => {
      const line = mo.lines && mo.lines[fanbase];
      const mood = fanbaseMood(fanbase, i);
      if (!line && mood === null) return '';
      return `<span class="fan"><span class="dot" style="background:${dotFor(fanbase)}"></span><span><b>${esc(fanbase)}:</b> ${line ? esc(line) : `mood ${signed(mood)}`}</span></span>`;
    }).join('');
    const swing = mo.wpDelta === null || mo.wpDelta === undefined ? ''
      : ` · ${esc(S.manifest.home.name)} win prob. ${signed(mo.wpDelta * 100, 0)} pts`;
    return `<button class="moment" data-start="${mo.start}">
      <span class="head"><span>${mo.inning ? esc(halfLabel(mo.inning, mo.half)) + ' · ' : ''}${etClock(mo.start)}</span><span>${mo.peakVolume} comments in 5s</span></span>
      <span class="play">${esc(mo.play || 'Comment volume spiked across several communities')}</span>
      <span class="muted">${mo.streams.length} communities reacting${swing}</span>
      ${fans}
    </button>`;
  }).join('') || '<div class="empty">No moments yet. They appear here as the game goes on.</div>';
  for (const button of $('moments').querySelectorAll('.moment')) {
    button.onclick = () => seek(Number(button.dataset.start) - 15);
  }
}

if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', init);
