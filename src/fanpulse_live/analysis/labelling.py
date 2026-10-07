"""Draws the sample a human labels for the Phase 2 evaluation, and writes the labelling page.

Each sampled item is one (stream, moment) window exactly as Jev would see it.
The labeller answers the window questions (mood, main subject, moment) and,
for a few of the window's new comments, the comment questions (subject,
sentiment). Labelling the same windows Jev is asked about means one Jev call
per item answers everything being compared.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from fanpulse_live.engine import calls
from fanpulse_live.gamestate import GameTimeline

# Seconds after a big play at which a "busy" window is taken: where reaction peaks.
BUSY_LAGS = (45, 50, 55, 60, 65, 70, 75)
MIN_NEW_COMMENTS = 2
COMMENTS_PER_WINDOW = 3


def tick_grid(timeline: GameTimeline, update_every_s: int) -> list[datetime]:
    """Update times from first pitch to final out, on whole multiples of the update interval."""
    start = timeline.first_pitch().replace(microsecond=0)
    start -= timedelta(seconds=start.second % update_every_s)
    count = int((timeline.final_out() - start).total_seconds() // update_every_s)
    return [start + timedelta(seconds=update_every_s * i) for i in range(count + 1)]


def _snap(t: datetime, grid_s: int) -> datetime:
    t = t.replace(microsecond=0)
    return t - timedelta(seconds=t.second % grid_s)


def draw_sample(
    timeline: GameTimeline,
    comments: pd.DataFrame,
    parents: dict[str, str],
    subjects: dict[str, str | None],
    game_config: dict,
    *,
    per_stream: int = 8,
    busy_per_stream: int = 4,
    seed: int = 7,
) -> list[dict]:
    """Windows to label: for each stream, some just after big plays and some at random moments."""
    rng = random.Random(seed)
    step = game_config["tick"]["update_every_s"]
    grid = tick_grid(timeline, step)
    big_plays = sorted(timeline.plays, key=lambda p: abs(p.wp_delta), reverse=True)[: busy_per_stream * 2]
    items: list[dict] = []

    for position, stream in enumerate(game_config["streams"]):
        in_stream = comments[(comments["stream"] == stream["id"]) & (comments["thread_type"] == "game")]
        in_stream = in_stream.sort_values("created_utc")

        def try_time(t: datetime, kind: str) -> dict | None:
            call = calls.prepare_call(timeline, t, stream, in_stream, parents, subjects, game_config)
            if call.window.stale or len(call.new_numbers) < MIN_NEW_COMMENTS:
                return None
            chosen = sorted(rng.sample(call.new_numbers, min(COMMENTS_PER_WINDOW, len(call.new_numbers))))
            return {
                "stream": stream["id"],
                "team": stream["team"],
                "time": t.isoformat(),
                "kind": kind,
                "state": call.state,
                "label_comments": chosen,
            }

        picked: list[dict] = []
        # Each stream gets a different half of the biggest plays, so both the plays and the streams vary.
        for play in big_plays[position % 2 :: 2][:busy_per_stream]:
            item = try_time(_snap(play.end + timedelta(seconds=rng.choice(BUSY_LAGS)), step), "after a big play")
            if item:
                picked.append(item)
        taken = {item["time"] for item in picked}
        candidates = rng.sample(grid, len(grid))
        while len(picked) < per_stream and candidates:
            t = candidates.pop()
            if t.isoformat() in taken:
                continue
            item = try_time(t, "random moment")
            if item:
                picked.append(item)
                taken.add(item["time"])
        items.extend(picked)

    rng.shuffle(items)
    for number, item in enumerate(items, start=1):
        item["id"] = f"w{number:02d}"
    return items


def write_labelling_page(items: list[dict], subjects: list[str], directory: Path) -> tuple[Path, Path]:
    """Write ``sample.json`` (what was drawn) and ``label.html`` (the page to label it in)."""
    directory.mkdir(parents=True, exist_ok=True)
    sample_path = directory / "sample.json"
    sample_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    payload = json.dumps({"items": items, "subjects": subjects}, ensure_ascii=False).replace("</", "<\\/")
    page_path = directory / "label.html"
    page_path.write_text(PAGE.replace("__DATA__", payload), encoding="utf-8")
    return sample_path, page_path


def load_labels(path: Path) -> dict[str, dict]:
    """Labels exported from the page, by window id."""
    return json.loads(path.read_text(encoding="utf-8"))["labels"]


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fan Pulse labelling</title>
<style>
  :root {
    --bg: #f9f9f7; --surface: #ffffff; --ink: #0b0b0b; --ink2: #52514e; --muted: #898781;
    --line: #e1e0d9; --accent: #2a78d6; --accent-ink: #ffffff; --mark: #fff4d6; --done: #006300;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink2: #c3c2b7; --muted: #898781;
      --line: #2c2c2a; --accent: #3987e5; --accent-ink: #ffffff; --mark: #3a3214; --done: #0ca30c;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { position: sticky; top: 0; z-index: 2; background: var(--surface); border-bottom: 1px solid var(--line); padding: 10px 16px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  header .count { font-weight: 600; }
  header .spacer { flex: 1; }
  main { max-width: 860px; margin: 0 auto; padding: 16px; }
  button { font: inherit; padding: 6px 12px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); color: var(--ink); cursor: pointer; }
  button:hover { border-color: var(--accent); }
  button.on { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  .card { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin-bottom: 14px; }
  .card h2 { margin: 0 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
  .who { font-size: 17px; font-weight: 600; }
  .ctx { color: var(--ink2); margin: 4px 0; }
  .ctx b { color: var(--ink); font-weight: 600; }
  ul.plain { margin: 4px 0 0; padding-left: 18px; color: var(--ink2); }
  .comment { padding: 5px 8px; border-radius: 6px; }
  .comment .n { color: var(--muted); font-variant-numeric: tabular-nums; margin-right: 6px; }
  .comment .reply { display: block; color: var(--muted); font-size: 13px; margin-left: 34px; }
  .comment.pick { background: var(--mark); margin: 6px 0; padding: 8px 10px; }
  .q { margin: 10px 0 2px; font-weight: 600; }
  .hint { color: var(--muted); font-size: 13px; font-weight: 400; }
  .row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
  input[type=text] { font: inherit; padding: 6px 10px; width: min(360px, 100%); border: 1px solid var(--line); border-radius: 6px; background: var(--surface); color: var(--ink); }
  input.bad { border-color: #d03b3b; }
  .nav { display: flex; gap: 8px; justify-content: space-between; margin: 18px 0 40px; }
  .status { color: var(--muted); font-size: 13px; }
  .status.ok { color: var(--done); }
  details { color: var(--ink2); }
  details summary { cursor: pointer; color: var(--ink); font-weight: 600; }
  details li { margin: 3px 0; }
</style>
</head>
<body>
<header>
  <span class="count" id="count"></span>
  <span class="status" id="status"></span>
  <span class="spacer"></span>
  <button id="prev">Back</button>
  <button id="next" class="primary">Next</button>
  <button id="save">Download labels</button>
</header>
<main>
  <div class="card">
    <details id="help">
      <summary>How to label (read once)</summary>
      <ul>
        <li>Each screen is one fan community at one moment of the game, with the comments posted in the previous seconds. Label what the comments say, not what you know happened later.</li>
        <li><b>Highlighted comments</b>: say who or what the comment is mainly about, and how its author feels about that. Read sarcasm for what it means ("love this for us" from the losing side is negative).</li>
        <li><b>Mood</b>: how these fans feel about how the game is going for <i>their own</i> team, taking all the comments together.</li>
        <li><b>Main subject</b>: who or what most of the comments are about. Pick a player by name, or a team, a manager, the umpire, the broadcast, or "other".</li>
        <li><b>Moment</b>: yes if several comments are reacting to the same specific thing that just happened; no for ordinary scattered chatter.</li>
        <li>If you can't tell, pick the middle option or "other". Don't agonise: your first read is the label.</li>
        <li>Progress is saved in this browser as you go. When you finish, press <b>Download labels</b>.</li>
      </ul>
    </details>
  </div>
  <div id="item"></div>
  <div class="nav">
    <button id="prev2">Back</button>
    <button id="next2" class="primary">Next</button>
  </div>
</main>
<datalist id="subjects"></datalist>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const ITEMS = DATA.items, SUBJECTS = DATA.subjects;
const KEY = 'fanpulse-labels-v1';
const MOOD = [[-2, 'Despair'], [-1, 'Unhappy'], [0, 'Neutral / mixed'], [1, 'Pleased'], [2, 'Elation']];
const SENT = [[-2, 'Very negative'], [-1, 'Negative'], [0, 'Neutral / unclear'], [1, 'Positive'], [2, 'Very positive']];
const YESNO = [[true, 'Yes'], [false, 'No']];
let labels = {};
try { labels = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { labels = {}; }
let index = 0;

document.getElementById('subjects').innerHTML = SUBJECTS.map(s => `<option value="${esc(s)}">`).join('');
if (!Object.keys(labels).length) document.getElementById('help').open = true;

function esc(s) { return String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c])); }
function store() { try { localStorage.setItem(KEY, JSON.stringify(labels)); } catch (e) {} updateStatus(); }
function entry(item) { return labels[item.id] || (labels[item.id] = {comments: {}}); }

function needed(item) {
  const e = labels[item.id] || {comments: {}};
  let missing = 0;
  if (item.team && e.mood === undefined) missing++;
  if (!SUBJECTS.includes(e.target)) missing++;
  if (e.moment === undefined) missing++;
  for (const k of item.label_comments) {
    const c = (e.comments || {})[k] || {};
    if (!SUBJECTS.includes(c.subject)) missing++;
    if (c.sentiment === undefined) missing++;
  }
  return missing;
}

function updateStatus() {
  const done = ITEMS.filter(i => needed(i) === 0).length;
  document.getElementById('count').textContent = `Window ${index + 1} of ${ITEMS.length}`;
  const s = document.getElementById('status');
  s.textContent = `${done} of ${ITEMS.length} complete`;
  s.className = 'status' + (done === ITEMS.length ? ' ok' : '');
}

function buttons(options, current, onPick) {
  const row = document.createElement('div');
  row.className = 'row';
  for (const [value, text] of options) {
    const b = document.createElement('button');
    b.textContent = text;
    if (current === value) b.className = 'on';
    b.onclick = () => { onPick(value); for (const x of row.children) x.className = ''; b.className = 'on'; store(); };
    row.appendChild(b);
  }
  return row;
}

function subjectInput(current, onPick) {
  const input = document.createElement('input');
  input.type = 'text';
  input.setAttribute('list', 'subjects');
  input.placeholder = 'Type a name, a team, umpire, broadcast, other…';
  input.value = current || '';
  input.oninput = () => {
    const ok = SUBJECTS.includes(input.value);
    input.className = input.value && !ok ? 'bad' : '';
    onPick(ok ? input.value : undefined);
    store();
  };
  return input;
}

function question(text, hint) {
  const q = document.createElement('div');
  q.className = 'q';
  q.innerHTML = esc(text) + (hint ? ` <span class="hint">${esc(hint)}</span>` : '');
  return q;
}

function render() {
  const item = ITEMS[index], e = entry(item), st = item.state;
  const root = document.getElementById('item');
  root.innerHTML = '';

  const ctx = document.createElement('div');
  ctx.className = 'card';
  ctx.innerHTML = `<h2>Who and when</h2>
    <div class="who">${esc(st.community)}</div>
    <div class="ctx">${esc(st.game_situation)}</div>
    <div class="ctx"><b>Win probability:</b> ${esc(st.win_probability)}</div>
    <div class="ctx"><b>Recent plays</b><ul class="plain">${st.recent_plays.map(p => `<li>${esc(p)}</li>`).join('')}</ul></div>` +
    (st.players_today.length ? `<div class="ctx"><b>Today</b><ul class="plain">${st.players_today.map(p => `<li>${esc(p)}</li>`).join('')}</ul></div>` : '');
  root.appendChild(ctx);

  const box = document.createElement('div');
  box.className = 'card';
  box.innerHTML = `<h2>Comments (${st.comments.length}), oldest first. Label the highlighted ones.</h2>`;
  st.comments.forEach((text, i) => {
    const k = i + 1, pick = item.label_comments.includes(k);
    const m = text.match(/^\[(\d+)\]\*? (\d+s ago): ([\s\S]*?)(?: ↳ replying to: ([\s\S]*))?$/);
    const div = document.createElement('div');
    div.className = 'comment' + (pick ? ' pick' : '');
    div.innerHTML = `<span class="n">[${k}]</span>${esc(m ? m[3] : text)}` + (m && m[4] ? `<span class="reply">↳ replying to: ${esc(m[4])}</span>` : '');
    if (pick) {
      const c = e.comments[k] || (e.comments[k] = {});
      div.appendChild(question(`Who or what is comment [${k}] mainly about?`));
      div.appendChild(subjectInput(c.subject, v => { c.subject = v; }));
      div.appendChild(question('How does its author feel about that?'));
      div.appendChild(buttons(SENT, c.sentiment, v => { c.sentiment = v; }));
    }
    box.appendChild(div);
  });
  root.appendChild(box);

  const win = document.createElement('div');
  win.className = 'card';
  win.innerHTML = '<h2>The whole window</h2>';
  if (item.team) {
    win.appendChild(question('Mood: how do these fans feel about how the game is going for their team?'));
    win.appendChild(buttons(MOOD, e.mood, v => { e.mood = v; }));
  }
  win.appendChild(question('Main subject: who or what are most of the comments about?'));
  win.appendChild(subjectInput(e.target, v => { e.target = v; }));
  win.appendChild(question('Moment: are they reacting to one specific thing that just happened?'));
  win.appendChild(buttons(YESNO, e.moment, v => { e.moment = v; }));
  root.appendChild(win);

  updateStatus();
  window.scrollTo(0, 0);
}

function go(step) { index = Math.max(0, Math.min(ITEMS.length - 1, index + step)); render(); }
for (const id of ['prev', 'prev2']) document.getElementById(id).onclick = () => go(-1);
for (const id of ['next', 'next2']) document.getElementById(id).onclick = () => go(1);
document.getElementById('save').onclick = () => {
  const blob = new Blob([JSON.stringify({labels: labels}, null, 1)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'labels.json';
  a.click();
};
const firstOpen = ITEMS.findIndex(i => needed(i) > 0);
index = firstOpen < 0 ? 0 : firstOpen;
render();
</script>
</body>
</html>
"""
