# Fan Pulse Live ⚾⚡

**A real-time audience-reaction engine for live sports.**
Every 5 seconds, it looks at what fans in each community said over the last ~20 seconds, asks a decision model (Jev) a fixed set of questions about it, and updates a live dashboard synced to the game. That dashboard shows how each fanbase feels, what they're reacting to, who they're talking about, and which moments matter.

**Demo:** the 2025 World Series Game 1 (Dodgers 4 @ Blue Jays 11, Oct 24 2025), replayed "as if live" next to MLB's official full-game YouTube video.
**Proof that it's real time:** the same engine run live on a 2026 postseason game (Phase 8).

> This is a separate project from `fan-pulse` (the batch version). It **reuses** that project's ingest code (MLB Stats API, Arctic Shift, cleaning) and its RoBERTa sentiment as a baseline. Everything else is new: 5-second updates over a sliding window, Jev, multiple subreddits, the replay engine, and the live dashboard.

---

## Scope (current)

- ✅ Text only: Reddit comments + MLB play-by-play + win probability
- ✅ Jev (TypeSafe decision model, via OpenRouter) as the only per-tick model
- ✅ One game: WS 2025 Game 1, three subreddits (r/baseball, r/Dodgers, r/Torontobluejays)
- ✅ Video is **embedded** from MLB's official YouTube playlist, never downloaded
- ⏸ Clef / video frame analysis: on hold (pending a licensing decision)
- ⏸ TikTok / Instagram / X / YouTube comments: later, as extra sources behind the same interface

---

## Status and how to run (8 Oct 2026, evening)

| Phase | State |
|---|---|
| 0 Setup, 1 Data | Done. 52,872 cleaned comments across the six threads; reaction lag measured. |
| 2 Jev client, questions, evaluation | Done against 40 hand-labelled windows and 114 comments (`reports/phase2_eval.md`). Question wording not tuned yet. |
| 3 Every tick through Jev | Done: 12,175 readings, 40,354 comments tagged, about $1.20 of Jev calls in 14 minutes. |
| 4 Moments and summaries | Done: 12 moments, each with a one-line summary per fanbase. |
| 5 Fanbase report | Done on the baseline (`reports/fanbase_report.html`). |
| 6 Video sync | **Working but late: the dashboard runs at least 30 seconds behind the video.** Must be fixed before going further; see `PROGRESS_LOG.md`. 20 sync points in `config/anchors.csv`, 16 marked by hand and 4 estimated. |
| 7 Replay dashboard | Done, on Jev's readings, with the baseline selectable for comparison. |
| 8 Live mode | Not started. |

**The baseline.** Alongside Jev there is a free local stand-in: RoBERTa sentiment per comment, name matching for who a comment is about, and comment-volume spikes for moments. It reads tone only, with no game context, so it scores "LET'S FUCKING GO" as negative and cannot follow "he" or "this guy". The dashboard's "Readings from" menu switches between it and Jev, which makes the difference easy to see.

**What a Jev call costs.** To keep a full game near $1.20, each call shows Jev at most 30 of the window's comments and asks it to tag at most 10 new ones per update (sampled evenly across a burst, about 89% of all comments). Subject questions offer only the players in the last fifteen minutes of action or named in the comments, plus "another Dodgers/Blue Jays player". The window's main subject is derived from the comment tags rather than asked. Both caps are in `config/game.yaml`.

**See the dashboard**

```
C:\Users\91821\.venvs\mlb-fan-live\Scripts\fanpulse-live.exe serve
```

then open http://localhost:8000. It has its own clock: Play, a speed menu, a scrubber, and buttons that jump between moments.

**Re-run Jev** (everything already paid for is cached, so this costs nothing unless the questions or caps change):

```
fanpulse-live ticks        # every 5-second update through Jev
fanpulse-live build        # moments, summaries, dashboard data; Jev becomes the default source
fanpulse-live evaluate     # refresh the accuracy tables
```

**Publish it.** `.github/workflows/pages.yml` deploys the `web/` folder to GitHub Pages on every push. One-time setup: make the repository public, then Settings → Pages → Source: "GitHub Actions".

**Sync the video.** The game video cuts every break, between half-innings and at mid-inning pitching changes, so it needs one anchor after each: the video time, in seconds, of the first pitch after the break. Open `anchors.html` on the dashboard site, mark them with one button press each, save the downloaded file as `config/anchors.csv` and run `fanpulse-live build`. Codes are `T1`/`B6` for half-innings and `B6P1` for the first mid-inning pitching change of the bottom of the 6th. The build warns about anchors that cannot be right.

**All commands:** `pull`, `volume`, `lag`, `state`, `label-sheet`, `evaluate`, `ticks`, `baseline-scores`, `build`, `fanbase-report`, `serve`. Run any with `--help`.

**Where the comments came from.** Arctic Shift times out when asked for a whole game thread, so `pull` reads each game thread out of its subreddit in 5-minute windows. PullPush refuses bulk pulls. `pull --dump` can read a local Reddit dump file instead.

---

## Data sources (verified Oct 2026)

### Game
- **MLB Stats API** (`https://statsapi.mlb.com`), free, no key.
  - gamePk: look up via `/api/v1/schedule?sportId=1&date=2025-10-24&gameType=W`. Don't hardcode a guessed ID.
  - `/api/v1.1/game/{gamePk}/feed/live`: plays with `about.startTime`/`endTime`, plus `playEvents[]` with per-pitch `startTime`
  - `/api/v1/game/{gamePk}/winProbability`: inspect the response first to confirm field names
- First pitch: about 8:00 PM ET Oct 24 = **about 00:00 UTC Oct 25, 2025**. Use the feed's actual times.

### Reddit (via Arctic Shift: `https://arctic-shift.photon-reddit.com`)

| Subreddit | Thread | ID | Comments | Notes |
|---|---|---|---|---|
| r/baseball | Game Thread (BaseballBot) | `1ofc2dw` | 18,963 | Neutral; **mixed fanbases**, so split by `author_flair_text` |
| r/Dodgers | Game Chat (DodgerBot) | `1ofc23n` | 16,446 | Home crowd for LAD |
| r/Torontobluejays | Game Thread (BlueJaysBaseball) | `1of9auu` | 14,720 | Opens about 3h before first pitch; tag pre-game comments |
| r/baseball | Postgame | `1ofhelr` | 934 | |
| r/Dodgers | Postgame (DodgerBot) | `1ofheap` | 758 | |
| r/Torontobluejays | Postgame | `1ofhdzh` | 945 | |

In-game total: **about 50k comments**, or roughly 20 comments every 5 seconds across the three subreddits (about 70 per 20-second window). r/mlb was checked and left out: its Game 1 thread had only 1,946 comments, under one comment per 5 seconds, too thin for a stream. Postgame and side threads (like "Andy Pages needs to be benched," r/Dodgers, 373 comments) are for the fanbase analysis in Phase 5, not the live ticks.

**Arctic Shift usage:**
- Comments: `/api/comments/search?link_id=<id>&sort=asc&limit=100&after=<last_created_utc>`. Page forward until empty. Fallback: `/api/comments/tree?link_id=t3_<id>&limit=9999`.
- On HTTP 429, sleep for the `X-RateLimit-Reset` seconds and retry. Keep it to 1–2 requests per second.
- `title=` filtering returns 422 on busy subreddits. Use `author=<bot>` plus a date window, or use the IDs above.
- Some network environments block the domain. If requests fail, test from a different network before debugging the code.
- Cache every raw response under `data/raw/` and never re-fetch something already cached.

### Video
- MLB's official full-game video for WS 2025 Game 1: `https://youtu.be/6BrK9Ep5M6o`. Put the video ID `6BrK9Ep5M6o` in `config/game.yaml`.
- Embedded only, via the **YouTube IFrame Player API**.
- **Rules (YouTube API RMF):** nothing may be drawn *over* the player, and the player must not be modified. The dashboard goes **next to** it. Player size must be at least 200×200 (480×270 recommended).

### Models
- **Jev** (TypeSafe), on OpenRouter as `typesafe/jev-1.13`. About $0.042 per 1M input tokens, output free, about 0.5s median latency, text only. Context: 64k per request, and state plus the longest single question must fit in 32k.
- **A cheap LLM** via OpenRouter, *only* for one-line moment summaries (Jev can't write text).
- **cardiffnlp RoBERTa** (from the old project): a baseline to compare Jev against.

**Licensing:** MLB data and Reddit data are fine for an internal prototype or pitch demo. A client product needs licensing or legal sign-off. YouTube content is embedded, never downloaded.

---

## What Jev does here

Jev is a **decision model**, not a chatbot. You give it a **state** (text) and up to **64 typed questions**, and it returns **probabilities**, not prose:
- `noul`: yes/no, giving P(yes)
- `choice`: pick from a list, giving a probability per option
- `score`: rate against a rubric

It's fast and cheap and always returns structured output, so it can run every 5 seconds on every stream for every game.

### Update rate vs. window

These are two separate settings (all in `config/game.yaml`):

| Setting | Default | What it controls |
|---|---|---|
| `update_every_s` | 5 | How often Jev runs and the dashboard refreshes, i.e. how *live* it feels |
| `window_s` | 20 | How far back each call looks, i.e. how *reliable* each reading is |
| `min_comments` | 8 | If a stream has fewer comments than this in the window, widen its window... |
| `max_window_s` | 60 | ...up to this. If it's still below `min_comments`, skip the call and carry the last value forward (marked `stale`) |

Why: at 5 seconds, a stream gets only about 2–6 comments, which is too few for a stable reading. A 20-second sliding window gives each call about 10–25 comments while the dashboard still updates every 5 seconds. Comment volume (for spike detection) is still counted on raw 5-second buckets, so spikes stay sharp.

**Per update (every 5s) × per stream, one Jev call:**

State:
```
Game: WS G1, LAD @ TOR, bottom 6th, TOR leads 5-2, bases loaded, 1 out
Last 3 plays: [...from MLB feed, with seconds-ago]
Community: r/Dodgers (Dodgers fans)
Comments in the last 20s (n=23), oldest → newest, new since last update marked *:
 [1]  "roberts leaving him in is malpractice"
 [2]  "here we go again"
 [19]* "BARGER WHAT"
 ...
```

Questions (window level, answered for the whole window):
| id | type | question |
|---|---|---|
| mood | score | Overall fan mood toward their own team (-1 despair … +1 elation) |
| target | choice | Main subject of reaction: [players in this game…, manager, umpire, own team, opponent, broadcast, other] |
| emotion | choice | Dominant emotion: [joy, anger, anxiety, disbelief, sarcasm/gallows humor, resignation, boredom] |
| moment | noul | Is the community reacting to a specific notable event right now? |
| blame | noul | Is criticism aimed at a management decision? |

Questions (comment level, packed into the same call): for each comment that is **new since the last update** (marked `*`), ask `subj_k` (choice, who it's about) and `sent_k` (score). Older comments in the window are context only, so each comment is tagged exactly once despite windows overlapping. With the 64-question limit, that's up to about 29 new comments per call plus the 5 window questions; during bursts, split the new comments across extra calls.

### What goes into each Jev call (the state)

Built by `jev/state.py`, in this order:

1. **Community:** stream name and which team these fans root for (e.g. `r/Dodgers — Dodgers fans`, `r/baseball — neutral/unflaired`).
2. **Game state at time t** (MLB live feed): inning and half, score, outs, runners, count, current batter and pitcher, and win probability **from this fanbase's perspective**.
3. **Recent plays:** every play that **ended in the last `play_lookback_s` (default 90s)**, newest first, each with `Xs ago`, the play description, and the win-probability change. This is how reaction lag is handled inside the call (see below).
4. **Light player context:** for the current batter and pitcher and any player in the recent plays, **today's line only** (e.g. `Ohtani: 1-for-3, HR, 2 RBI`; `Snell: 5.0 IP, 5 ER`), rebuilt from play-by-play up to time t. No season stats (see "Player stats" below).
5. **Comments in the window:** for each comment, `[k]`, `Xs ago`, `*` if new, the cleaned text (truncated to about 300 characters), and, if it's a reply, `↳ replying to: "<first ~80 chars of parent>"`.
6. **Questions:** the window-level and comment-level questions above. `choice` options come from the game's rosters (both teams, with nicknames from `nicknames.yaml`) plus `manager`, `umpire`, `own team`, `opponent`, `broadcast`, `other`.

**From Arctic Shift, what each comment contains and what we use:**

| Field | Used? | Why |
|---|---|---|
| `body` | ✅ | The comment text. Cleaned: URLs → `[link]`, gif/image markup → `[gif]`, markdown stripped. |
| `created_utc` | ✅ | Placement in time (seconds-level). |
| `author_flair_text` | ✅ (routing only) | Splits r/baseball into LAD / TOR / neutral streams. Not sent to Jev. |
| `parent_id` | ✅ | Reply context (`↳ replying to`). Many comments ("exactly", "this 😭") mean nothing without it. |
| `id`, `link_id`, `subreddit` | ✅ (bookkeeping) | Joins and caching. Not sent to Jev. |
| `author` | ❌ to Jev | No value to the model, and keeps usernames out of model calls. A hashed author ID is kept locally only for counting unique commenters. |
| `score` (upvotes) | ❌ to Jev | Not available live (Arctic Shift stores it as 0/1 until about 36h later), so using it in replay would make replay and live behave differently. Fine to use in offline analysis (Phase 5). |
| `retrieved_on`, `distinguished`, `author_fullname` | ❌ | Not useful here. |

**Token budget per call:** about 300 tokens for game state + plays + player lines, about 25 tokens per comment, and about 400 for questions. That's about 2.5–3k tokens at a typical window size, well under Jev's 32k state limit.

### How reaction lag is handled

Fans react **after** a play: the broadcast or stream delay plus typing time is usually 5–60s. It's handled in three places:

1. **Measured, not assumed (Phase 1).** Cross-correlate each stream's 5-second comment volume with play end times (weighted by |Δ win probability|) to get the typical lag per stream. Set `play_lookback_s` from that (default 90s, so the slow tail is covered).
2. **Inside each Jev call.** The state lists *all* plays that ended in the last `play_lookback_s`, with `Xs ago`. Jev can therefore connect "WHAT A SWING" posted 40s after a homer to that homer, even though a newer pitch has happened since.
3. **When linking moments to plays (Phase 4).** A moment is linked to the play that ended 0–`play_lookback_s` before it, preferring the play with the largest |Δ win probability| in that range, not simply the most recent one.

In the **replay dashboard**, comments appear at their real posting time, so they show up a little after the play on screen, just as they did for fans that night. That delay is realistic and shouldn't be "corrected."

### Player stats: in the dashboard, mostly not in Jev

- **Today's line for players involved (in Jev's state):** yes. It's cheap and helps Jev resolve "this guy" and "he" to the right player.
- **Season / postseason / career stats:** **not in Jev's state.** Jev's job is to report *what fans feel*. Showing it stats would push its readings toward what the numbers say, which would contaminate exactly the perception-vs-performance comparison in Phase 5.
- **Where season stats do belong:**
  - The **dashboard**: when a player tops "who's being talked about," show a small card with his postseason line next to fan sentiment ("fans: −0.7 · postseason: .310/.400/.590").
  - **Phase 5 analysis**: WPA plus season context against fan sentiment.
- Source: MLB Stats API `/api/v1/people/{id}?hydrate=stats(group=[hitting,pitching],type=[season],season=2025)` and the game boxscore. Cache per player.

**Why Jev over the alternatives:**
- **vs. RoBERTa:** Jev sees the *game context*, can say *who* a comment is about, and handles sarcasm.
- **vs. a regular LLM:** It's cheaper, faster, structured by design, and its probabilities can be used directly as thresholds for automatic actions (e.g. `P(moment) > 0.85` → flag a clip).

**Cost:** About 3h of game × 720 updates per hour × 5 streams ≈ 11k calls at ~2.5–3k tokens (the window makes each call bigger) ≈ 30M tokens ≈ **$1–3 per game**, including burst splits.

---

## Architecture

```
            ┌───────────── Source interface ─────────────┐
            │  ReplaySource (cached data, driven by clock) │
            │  LiveSource   (polls MLB + Arctic Shift, 5s) │
            └──────────────┬───────────────────────────────┘
                           │  TickBatch(t, game_state, window_comments_by_stream, new_comment_ids)
                           ▼
                    Tick engine ── Jev client ── cache (by tick hash)
                           │
                           ▼
                 TickResult store (parquet / JSON)
                           │
                           ▼
       Dashboard (YouTube embed + panels), driven by a clock
       • replay: clock = player.getCurrentTime() → wall time via anchors
       • live:   clock = now
```

**Key design rule:** the tick engine and dashboard never know whether they're running on a replay or live. Replay and live mode differ only in the `Source` and the clock. That's what makes this "real-time," and Phase 8 proves it.

**Streams (fanbases):** `r/Dodgers`, `r/Torontobluejays`, `r/baseball:LAD-flair`, `r/baseball:TOR-flair`, `r/baseball:neutral`.

---

## Repo structure

```
mlb-fan-live/
├── README.md
├── .env.example                 # OPENROUTER_API_KEY, JEV_MODEL, SUMMARY_MODEL
├── pyproject.toml
├── config/
│   ├── game.yaml                # gamePk, youtube_video_id, thread IDs, bots, streams, window settings, play_lookback_s
│   ├── anchors.csv              # filled in by hand (see Phase 6)
│   └── nicknames.yaml           # "Sho", "Vladdy", "Doc" → player IDs
├── data/{raw,processed,ticks}/
├── src/fanpulse_live/
│   ├── ingest/{mlb.py, reddit.py, clean.py}   # copied/adapted from fan-pulse
│   ├── sources/{base.py, replay.py, live.py}
│   ├── jev/{client.py, questions.py, state.py}
│   ├── engine/{ticks.py, moments.py, summarize.py}
│   ├── analysis/fanbases.py
│   ├── sync/video_clock.py
│   └── cli.py
├── web/                         # static dashboard (HTML + vanilla JS + Plotly.js)
│   ├── index.html
│   └── app.js
├── server/app.py                # tiny FastAPI: serves tick JSON; SSE in live mode
└── tests/
```

Keep it pragmatic: no frontend framework, no database server. Use Parquet/JSON files, plus a small FastAPI app only where live mode needs it.

---

## Build phases

> **For Claude Code:** Build **one phase at a time**. At the end of each phase, stop, show the checkpoint output, and wait for review. Cache all external calls. Never guess an API's response shape; fetch one response and inspect it first.

### Phase 0: Setup + port ingest
- Scaffold the repo. Copy `ingest/` and the cleaning code from `fan-pulse`, and adapt it for multiple threads and subreddits.
- Write `config/game.yaml` with everything in the data-sources tables above.

✅ **Checkpoint:** `pip install -e .`, `fanpulse-live --help`, and `pytest` all work.

### Phase 1: Pull all Game 1 data
- MLB: gamePk, plays (with per-pitch times), and win probability.
- Reddit: all 6 threads. Clean them (deleted/removed/AutoModerator/bots). Assign each comment its `stream`, splitting r/baseball by flair (Dodgers flair → LAD, Blue Jays flair → TOR, otherwise neutral). Tag `phase` = pregame / in-game / postgame using first-pitch and final-out times.

✅ **Checkpoint:**
- Comment counts per thread, compared against the table above (some loss from deletions is fine).
- Comments per minute per stream on one chart, with win probability overlaid.
- The 6th inning (the Blue Jays' big inning, including Barger's pinch-hit grand slam) should be a visible spike in every stream.
- **Reaction lag per stream:** cross-correlation of 5-second comment volume vs. play end times (weighted by |Δ win probability|). Report the peak lag and the 90th percentile, and set `play_lookback_s` from it.

### Phase 2: Jev client + question design + evaluation
- `jev/client.py`: **First, read Jev's documentation (OpenRouter model page / TypeSafe docs) and make one test call.** Confirm the request and response format, including how the question types are expressed. Don't assume a schema. Then write a thin client with retries, response caching keyed by a hash of (state, questions), and logging of token usage and cost.
- `jev/questions.py`: the question set from "What Jev does here." Build the player list for `choice` questions from the game's rosters.
- `jev/state.py`: builds the state exactly as in "What goes into each Jev call" (game state, recent plays within `play_lookback_s`, today's lines, cleaned comments with reply context). Print 3 example states in full for review.
- **Eval:** Hand-label about 150 (update, stream) windows (mood, target, moment) and about 200 comments (subject, sentiment). Compare Jev against RoBERTa (comment sentiment only).

✅ **Checkpoint:** A table comparing accuracy and agreement for Jev and RoBERTa, plus latency per call and cost per call. Show 5 example windows with Jev's full output. **Tune the wording of the questions here.** It's the cheapest point to do it.

### Phase 3: Precompute every tick
- `engine/ticks.py`: step through the game (first pitch − 5 min → final out + 5 min) every `update_every_s` (5s). At each update *t*, for each stream:
  - Take comments in `(t − window_s, t]`. If there are fewer than `min_comments`, widen the window up to `max_window_s`. If it's still too few, skip and carry the last value forward (`stale=true`).
  - One Jev call: game state at *t* + window comments + window-level questions + comment-level questions for comments new since *t − 5s*.
  - Store: `t`, stream, `window_used_s`, `n_comments`, all answers with probabilities.
- Separately, store raw comment counts per 5-second bucket per stream (for spike detection).
- Run calls in parallel (about 8 at a time), resumable, with everything cached.
- No extra smoothing is needed. The window already smooths. Add a light EMA only if the chart still looks jittery.

✅ **Checkpoint:**
- Mood by stream over the whole game against win probability. LAD and TOR streams should move in opposite directions on big plays.
- Total cost and time for the run, and the share of updates per stream that needed a wider window or went `stale`.
- Spot-check 10 random updates by reading the comments next to Jev's answers.

### Phase 4: Moments + summaries
- `engine/moments.py`: a moment is an update where `P(moment)` is high **and** comment volume spikes (rolling z-score on the **raw 5-second counts**, not the window), across ≥ 2 streams. Merge moments that fall within 60s of each other. Link each one to the MLB play that ended within the previous 0–90s.
- `engine/summarize.py`: for each moment, have the cheap LLM write **one line per fanbase** ("Jays fans: pure delirium at Barger's slam. Dodgers fans: already blaming the bullpen."). Moments only, so cost is tiny.

✅ **Checkpoint:** The top 10 moments, each with its play, P(moment), volume, and per-fanbase summary. Read them: is it the game you'd recognize?

### Phase 5: Fanbase analysis (offline report)
Compare how the different communities react:
- **Home sub vs. same fans in r/baseball:** Do Dodgers fans in r/Dodgers react differently from Dodgers-flaired users in r/baseball? (An echo-chamber effect.)
- **Blame and credit:** Who each fanbase talks about most, and with what sentiment. Compare that against each player's **WPA** (win probability added) to find perception vs. performance gaps.
- **Emotional profile:** The mix of sarcasm, anger, and resignation per fanbase, and how it shifts as the game gets out of hand.
- **Recovery:** After a big negative swing, how long until each fanbase's mood returns to its pre-swing level?
- **Postgame narrative:** What each fanbase decided the story was, from the postgame threads plus the top side posts.

✅ **Checkpoint:** A single HTML report with 4–6 charts and a short written list of findings.

### Phase 6: Video clock sync
- `config/anchors.csv` (filled in by hand while watching the embedded video):
  ```
  half_inning,video_seconds,note
  T1,754,first pitch
  B1,1580,
  ...
  ```
  `half_inning` = T/B + inning number. `video_seconds` = player time at the **first pitch** of that half-inning.
- `sync/video_clock.py`: match each anchor to the first pitch's `startTime` from the MLB feed, then build a **piecewise-linear** video_seconds ↔ wall-clock map (breaks between half-innings are likely cut, so a single linear fit won't work). Expose both directions.

✅ **Checkpoint:** For 5 big plays (from the feed), predicted video time vs. where the play actually appears in the video, with error under about 5s. Print a warning for any half-inning with an inconsistent anchor.

### Phase 7: Replay dashboard 🎯
- `web/index.html`: **left**, the YouTube IFrame player (Game 1 video). **Right** (never over the player):
  1. **Scoreboard strip:** inning, score, count (from the MLB feed at the current clock)
  2. **Pulse chart:** win probability plus mood per fanbase, with a moving "now" cursor
  3. **Comment stream:** the latest comments flowing in, tagged with Jev's subject and sentiment as colored chips, switchable by subreddit
  4. **Moments feed:** cards that appear when a moment fires, each with per-fanbase one-liners
  5. **Who's being talked about:** a live bar of the top subjects over the last 2 minutes, colored by sentiment.
   - **Perception vs. stats card:** whenever a player is the #1 subject (and also when any player is clicked), show a small card with fan sentiment next to his numbers, e.g. **"Fans: −0.7 · Today: 0-for-3, 2 K · Postseason: .310/.400/.590"**. The gap between how fans feel and what the stats say is the fun part, so make both visible at a glance. Stats come from the MLB Stats API (season/postseason) and the play-by-play (today's line up to the current clock), cached per player. These stats are for display only; they are never sent to Jev.
- Clock: poll `player.getCurrentTime()` about every 500ms → wall time via `video_clock` → render the precomputed tick. Pausing and scrubbing must just work, since everything is precomputed.
- Serve the precomputed tick JSON statically (or via `server/app.py`).

✅ **Checkpoint:** Play the video from the 6th inning. The comments, mood, and moments line up with what's on screen. Jump to the 9th: the dashboard snaps to match.

### Phase 8: Live mode (the real-time proof)
- `sources/live.py`: every 5s, poll the MLB live feed (`/feed/live`) and Arctic Shift for new comments in the configured live game threads (`after=<last seen>`), keeping a rolling buffer of the last `max_window_s` per stream. Feed `TickBatch`es into the **same** tick engine. Jev runs live (about 0.5s per call, parallel across streams).
- `server/app.py`: push TickResults to the dashboard over Server-Sent Events. The dashboard runs in live mode (clock = now, no video panel, or a placeholder slot).
- Run it on a **2026 postseason game** while it's being played (or the morning after, as a replay, if the timing doesn't work).

✅ **Checkpoint:** The end-to-end delay from a play happening to the dashboard updating. Expect about 35–60s, mostly from Arctic Shift's lag (about 30s behind live Reddit) plus the 5s update. (Spikes use raw 5-second counts, so the window doesn't delay them, but mood readings lag by roughly half the window.) Write down where the remaining delay comes from and what would remove it (a licensed real-time Reddit feed, a direct broadcaster data feed).

---

## Later (not now)
- **Mood for the neutral stream:** `r/baseball:neutral` gets every question except `mood`, because mood is a fanbase's feeling about its own team and these commenters have no team in the game. A possible later reading: how much the neutral crowd is enjoying the game.
- **Clef** (Cloudflare, accepts images) on video frames, for an auto-generated visual ticker. Blocked on footage licensing.
- **More sources:** X, TikTok, Instagram, and YouTube comments. Add each as a new `Source`, using the same streams and tick engine.
- **More games:** WS G3 (68k comments in r/baseball, 18 innings) and G7 (57k).
- **More sports:** NFL (r/nfl + nflverse), IPL (r/Cricket + Cricsheet).

## Known gotchas
- **Reaction lag:** Fans react 5–60s after a play because of stream delays and typing time. Handled three ways (see "How reaction lag is handled"): lag measured per stream in Phase 1, all plays from the last `play_lookback_s` included in every Jev call, and moments linked to the biggest play in that lookback, not just the latest.
- **Mixed fanbases in neutral subs:** Always split by flair. A grand slam is joy for one side and despair for the other.
- **Pre-game comments:** The r/Torontobluejays thread opens about 3h early. Exclude pre-game comments from in-game ticks.
- **Overlapping windows:** Each comment falls in about 4 consecutive windows. Tag comments (subject/sentiment) only when they're new; never sum per-comment results across windows.
- **Sarcasm:** "love this for us" is not positive. This is half the reason Jev is here.
- **Arctic Shift** is a single person's free project: be polite, cache everything, and expect occasional downtime.